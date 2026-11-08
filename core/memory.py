
import sqlite3
from pathlib import Path

from config import DATABASE_PATH

# Bump this and add a _migrate_to_<n> method whenever the schema changes.
# The version is stored inside the database file (PRAGMA user_version), so
# existing databases are upgraded in place and never rebuilt.
SCHEMA_VERSION = 1


class Memory:
    """Stores conversation history and explicit long-term memories."""

    def __init__(self, database_path=DATABASE_PATH):
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row

        self._migrate()

    def _migrate(self):
        """Bring the database up to SCHEMA_VERSION, one step at a time."""

        version = self.connection.execute("PRAGMA user_version").fetchone()[0]

        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than this "
                f"version of Quanttum AI understands ({SCHEMA_VERSION}). "
                "Update the application instead of opening this database."
            )

        for target in range(version + 1, SCHEMA_VERSION + 1):
            getattr(self, f"_migrate_to_{target}")()
            self.connection.execute(f"PRAGMA user_version = {target}")
            self.connection.commit()

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

        self.connection.commit()

    # -----------------------------------------
    # CONVERSATION HISTORY
    # -----------------------------------------

    def save(self, role, content):
        if not content or not content.strip():
            return

        self.connection.execute(
            "INSERT INTO messages (role, content) VALUES (?, ?)",
            (role, content)
        )
        self.connection.commit()

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

    def remember(self, fact):
        """Save or update an explicitly provided fact."""

        fact = " ".join(fact.split())

        if not fact:
            return False

        self.connection.execute(
            """
            INSERT INTO memories (fact)
            VALUES (?)
            ON CONFLICT(fact)
            DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP,
                active = 1
            """,
            (fact,)
        )

        self.connection.commit()
        return True

    def search_records(self, query="", limit=5):
        """Find stored facts using lightweight keyword matching.

        Returns a list of (id, fact) tuples, best match first. An empty query
        returns the most recently updated facts.
        """

        limit = max(1, min(int(limit), 100))

        cursor = self.connection.execute(
            """
            SELECT id, fact, updated_at
            FROM memories
            WHERE active = 1
            ORDER BY updated_at DESC, id DESC
            """
        )

        memories = cursor.fetchall()

        words = [
            word.casefold().strip(".,!?;:()[]{}\"'")
            for word in query.split()
            if len(word.strip(".,!?;:()[]{}\"'")) > 2
        ]

        if not words:
            return [(row["id"], row["fact"]) for row in memories[:limit]]

        matches = []

        for memory in memories:
            fact = memory["fact"].casefold()
            score = sum(word in fact for word in words)

            if score > 0:
                matches.append(
                    (score, memory["updated_at"], memory["id"], memory["fact"])
                )

        matches.sort(key=lambda item: item[:3], reverse=True)

        return [(item[2], item[3]) for item in matches[:limit]]

    def search(self, query="", limit=5):
        """Like search_records, but returns only the fact text."""

        return [fact for _, fact in self.search_records(query, limit)]

    def get(self, memory_id):
        """Return the text of an active memory, or None."""

        row = self.connection.execute(
            "SELECT fact FROM memories WHERE id = ? AND active = 1",
            (memory_id,)
        ).fetchone()

        return row["fact"] if row else None

    def forget(self, memory_id):
        """Deactivate a stored memory without destroying its record."""

        cursor = self.connection.execute(
            """
            UPDATE memories
            SET active = 0
            WHERE id = ? AND active = 1
            """,
            (memory_id,)
        )

        self.connection.commit()
        return cursor.rowcount > 0

    def close(self):
        self.connection.close()
