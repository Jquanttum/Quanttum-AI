
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from config import DATABASE_PATH
from core.textutils import categorise, fts_query, significant_terms, tokens

# Bump this and add a _migrate_to_<n> method whenever the schema changes.
# The version is stored inside the database file (PRAGMA user_version), so
# existing databases are upgraded in place and never rebuilt. Each step runs
# in one transaction: it either completes or leaves the database untouched.
SCHEMA_VERSION = 2

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"   # replaced by a newer fact; kept as history
STATUS_FORGOTTEN = "forgotten"     # removed by the user; kept, never deleted


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    fact: str
    category: str
    status: str
    created_at: str
    updated_at: str


_RECORD_COLUMNS = "id, fact, category, status, created_at, updated_at"


def _record(row):
    return MemoryRecord(
        row["id"], row["fact"], row["category"], row["status"],
        row["created_at"], row["updated_at"],
    )


class Memory:
    """Stores conversation history and explicit long-term memories."""

    def __init__(self, database_path=DATABASE_PATH):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row

        try:
            self._migrate()
        except BaseException:
            self.connection.close()
            raise

    # -----------------------------------------
    # SCHEMA MIGRATIONS
    # -----------------------------------------

    def _migrate(self):
        """Bring the database up to SCHEMA_VERSION, one step at a time."""

        version = self.connection.execute("PRAGMA user_version").fetchone()[0]

        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than this "
                f"version of Quanttum AI understands ({SCHEMA_VERSION}). "
                "Update the application instead of opening this database."
            )

        if version < SCHEMA_VERSION:
            self._backup_before_migration(version)

        for target in range(version + 1, SCHEMA_VERSION + 1):
            self.connection.execute("BEGIN")

            try:
                getattr(self, f"_migrate_to_{target}")()
                self.connection.execute(f"PRAGMA user_version = {target}")
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def _backup_before_migration(self, version):
        """Copy an existing database next to itself before upgrading it.

        The copy is named <database>.bak-v<old version> and is never
        overwritten, so the first backup of each version is preserved.
        """

        if self.database_path == ":memory:":
            return

        has_data = self.connection.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type = 'table' AND name IN ('messages', 'memories')"
        ).fetchone()[0]

        backup_path = Path(f"{self.database_path}.bak-v{version}")

        if not has_data or backup_path.exists():
            return

        destination = sqlite3.connect(str(backup_path))

        try:
            self.connection.backup(destination)
        finally:
            destination.close()

    def _migrate_to_1(self):
        """Baseline schema: conversation history and long-term memories.

        Uses IF NOT EXISTS so databases created before versioning existed
        (user_version 0, tables already present) are adopted without changes.
        """

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL COLLATE NOCASE UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)

    def _migrate_to_2(self):
        """Memory Engine v2.

        - `active` (0/1) becomes `status` (active / superseded / forgotten).
        - New columns: category, source_message_id, last_used_at,
          supersedes_id.
        - A full-text index (FTS5, Porter stemming) over the fact text, kept
          in sync by triggers.
        """

        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise RuntimeError(
                "This version of SQLite is too old (3.35 or newer is "
                f"required, found {sqlite3.sqlite_version})."
            )

        connection = self.connection

        for column in (
            "category TEXT NOT NULL DEFAULT 'general'",
            "status TEXT NOT NULL DEFAULT 'active'",
            "source_message_id INTEGER",
            "last_used_at TEXT",
            "supersedes_id INTEGER",
        ):
            connection.execute(f"ALTER TABLE memories ADD COLUMN {column}")

        connection.execute("""
            UPDATE memories
            SET status = CASE WHEN active = 1 THEN 'active'
                              ELSE 'forgotten' END
        """)
        connection.execute("ALTER TABLE memories DROP COLUMN active")

        # Frozen copy of the preference rule as it was when v2 shipped, so
        # this migration never changes behaviour if categorise() evolves.
        import re
        preference = re.compile(
            r"\b(?:prefer|prefers|favou?rite)\b|"
            r"\bi\s+(?:really\s+|also\s+)?"
            r"(?:like|love|hate|dislike|enjoy|prefer)\b",
            re.IGNORECASE,
        )

        for row in connection.execute("SELECT id, fact FROM memories").fetchall():
            if preference.search(row["fact"]):
                connection.execute(
                    "UPDATE memories SET category = 'preference' WHERE id = ?",
                    (row["id"],)
                )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_status "
            "ON memories(status, category)"
        )

        try:
            connection.execute("""
                CREATE VIRTUAL TABLE memories_fts USING fts5(
                    fact,
                    content='memories',
                    content_rowid='id',
                    tokenize='porter unicode61'
                )
            """)
        except sqlite3.OperationalError as error:
            raise RuntimeError(
                "This Python's SQLite was built without FTS5 full-text "
                "search, which Quanttum AI 0.2 needs."
            ) from error

        connection.execute("""
            CREATE TRIGGER memories_fts_insert AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, fact) VALUES (new.id, new.fact);
            END
        """)
        connection.execute("""
            CREATE TRIGGER memories_fts_delete AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, fact)
                VALUES ('delete', old.id, old.fact);
            END
        """)
        connection.execute("""
            CREATE TRIGGER memories_fts_update AFTER UPDATE OF fact ON memories
            BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, fact)
                VALUES ('delete', old.id, old.fact);
                INSERT INTO memories_fts(rowid, fact) VALUES (new.id, new.fact);
            END
        """)

        connection.execute(
            "INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')"
        )

    # -----------------------------------------
    # CONVERSATION HISTORY
    # -----------------------------------------

    def save(self, role, content):
        """Store a chat message and return its ID (None if blank)."""

        if not content or not content.strip():
            return None

        cursor = self.connection.execute(
            "INSERT INTO messages (role, content) VALUES (?, ?)",
            (role, content)
        )
        self.connection.commit()
        return cursor.lastrowid

    def recent(self, limit=30):
        limit = max(1, min(int(limit), 500))

        cursor = self.connection.execute(
            """
            SELECT role, content, created_at
            FROM messages
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        )

        return [
            (row["role"], row["content"], row["created_at"])
            for row in reversed(cursor.fetchall())
        ]

    # -----------------------------------------
    # LONG-TERM MEMORY
    # -----------------------------------------

    def _upsert(self, fact, category, source_message_id, supersedes_id):
        """Insert a fact, or reactivate an identical (case-insensitive) one.

        Does not commit, so callers can combine it with other changes in one
        transaction. Returns the row's ID.
        """

        self.connection.execute(
            """
            INSERT INTO memories (fact, category, source_message_id,
                                  supersedes_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(fact)
            DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP,
                status = 'active',
                category = excluded.category,
                source_message_id = excluded.source_message_id,
                supersedes_id = excluded.supersedes_id
            """,
            (fact, category, source_message_id, supersedes_id)
        )

        # lastrowid is unreliable after an upsert that updated an existing
        # row, so look the ID up (the column is case-insensitive).
        return self.connection.execute(
            "SELECT id FROM memories WHERE fact = ?", (fact,)
        ).fetchone()["id"]

    def remember(self, fact, category=None, source_message_id=None):
        """Save or reactivate an explicitly provided fact.

        category defaults to a rule-based guess (see textutils.categorise).
        source_message_id links the memory to the chat message that created
        it. Returns the memory's ID, or None if the fact was empty.
        """

        fact = " ".join(fact.split())

        if not fact:
            return None

        memory_id = self._upsert(
            fact, category or categorise(fact), source_message_id, None
        )
        self.connection.commit()
        return memory_id

    def replace(self, old_id, fact, category=None, source_message_id=None):
        """Replace an active memory with a new fact, keeping the old one.

        The old row becomes 'superseded' and the new row records which row it
        supersedes, all in one transaction. Returns the new ID, or None if
        old_id is not an active memory.
        """

        fact = " ".join(fact.split())

        if not fact or self.get(old_id) is None:
            return None

        try:
            new_id = self._upsert(
                fact, category or categorise(fact), source_message_id, old_id
            )

            if new_id != old_id:
                self.connection.execute(
                    """
                    UPDATE memories
                    SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (old_id,)
                )

            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

        return new_id

    def lookup(self, fact):
        """Return the record whose text equals fact (any status), or None."""

        fact = " ".join(fact.split())

        row = self.connection.execute(
            f"SELECT {_RECORD_COLUMNS} FROM memories WHERE fact = ?", (fact,)
        ).fetchone()

        return _record(row) if row else None

    def find(self, query="", limit=5, category=None):
        """Find active memories, best match first.

        A query is reduced to its informative words (no fillers like "what"
        or "the"), then matched against the full-text index with Porter
        stemming ("projects" finds "project") and ranked by BM25. An empty
        or all-filler query returns the most recently updated memories.
        """

        limit = max(1, min(int(limit), 100))
        terms = significant_terms(query)
        category_sql = " AND m.category = ?" if category else ""
        category_args = (category,) if category else ()

        if not terms:
            cursor = self.connection.execute(
                f"""
                SELECT {_RECORD_COLUMNS}
                FROM memories AS m
                WHERE m.status = 'active'{category_sql}
                ORDER BY m.updated_at DESC, m.id DESC
                LIMIT ?
                """,
                (*category_args, limit)
            )
        else:
            cursor = self.connection.execute(
                f"""
                SELECT m.id, m.fact, m.category, m.status,
                       m.created_at, m.updated_at
                FROM memories_fts
                JOIN memories AS m ON m.id = memories_fts.rowid
                WHERE memories_fts MATCH ?
                  AND m.status = 'active'{category_sql}
                ORDER BY bm25(memories_fts), m.updated_at DESC, m.id DESC
                LIMIT ?
                """,
                (fts_query(terms), *category_args, limit)
            )

        return [_record(row) for row in cursor.fetchall()]

    def search_records(self, query="", limit=5):
        """Like find(), but returns (id, fact) tuples."""

        return [(r.id, r.fact) for r in self.find(query, limit)]

    def search(self, query="", limit=5):
        """Like find(), but returns only the fact text."""

        return [r.fact for r in self.find(query, limit)]

    def find_similar(self, fact, threshold=0.5, min_shared=2):
        """Return the active non-task memory most similar to fact, or None.

        Similarity is the Jaccard overlap of the two texts' word sets (shared
        words / all words). Fillers are kept on purpose: "my name is Jerry"
        and "my name is Quanttum" share the sentence frame, which is exactly
        what signals an update. Identical text is not "similar"; use
        lookup() for exact matches.
        """

        new_words = set(tokens(fact))
        best = None
        best_score = 0.0

        rows = self.connection.execute(
            f"""
            SELECT {_RECORD_COLUMNS} FROM memories
            WHERE status = 'active' AND category != 'task'
            """
        ).fetchall()

        for row in rows:
            old_words = set(tokens(row["fact"]))
            shared = len(new_words & old_words)
            union = len(new_words | old_words)

            if not union or shared < min_shared or new_words == old_words:
                continue

            score = shared / union

            if score >= threshold and score > best_score:
                best, best_score = row, score

        return _record(best) if best else None

    def related(self, memory_id, limit=5):
        """Active memories that share informative words with this one."""

        fact = self.get(memory_id)

        if fact is None:
            return []

        found = self.find(fact, limit + 1)
        return [r for r in found if r.id != memory_id][:limit]

    def get(self, memory_id):
        """Return the text of an active memory, or None."""

        row = self.connection.execute(
            "SELECT fact FROM memories WHERE id = ? AND status = 'active'",
            (memory_id,)
        ).fetchone()

        return row["fact"] if row else None

    def touch(self, memory_ids):
        """Record that these memories were just shown to the user."""

        ids = [int(memory_id) for memory_id in memory_ids]

        if not ids:
            return

        placeholders = ",".join("?" for _ in ids)
        self.connection.execute(
            f"UPDATE memories SET last_used_at = CURRENT_TIMESTAMP "
            f"WHERE id IN ({placeholders})",
            ids
        )
        self.connection.commit()

    def forget(self, memory_id):
        """Mark a memory as forgotten without destroying its record."""

        cursor = self.connection.execute(
            """
            UPDATE memories
            SET status = 'forgotten', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'active'
            """,
            (memory_id,)
        )

        self.connection.commit()
        return cursor.rowcount > 0

    def close(self):
        self.connection.close()
