"""Fast file-name search for the allowed folders.

How it is fast: the folder tree is scanned once into a small SQLite database
(data/fileindex.db) with an FTS5 *trigram* index over file names, so finding
"exam notes" takes milliseconds instead of walking the disk. Re-scans are
incremental: a folder is only re-listed if its modified time changed.

Only names, sizes and dates are stored, never file contents. The database is
disposable: delete it and it is rebuilt. Symlinks and junctions are never
followed, so the index cannot leak outside the allowed folders.

Every operation opens its own short-lived connection, so the background
indexer thread and the chat thread never share one.
"""

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", "$recycle.bin",
    "system volume information", ".venv", "venv",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    parent TEXT NOT NULL,
    size INTEGER,
    mtime REAL,
    is_dir INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(parent);
CREATE TABLE IF NOT EXISTS dirs (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS names_fts USING fts5(
    name, content='entries', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO names_fts(rowid, name) VALUES (new.id, new.name);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO names_fts(names_fts, rowid, name)
    VALUES ('delete', old.id, old.name);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE OF name ON entries BEGIN
    INSERT INTO names_fts(names_fts, rowid, name)
    VALUES ('delete', old.id, old.name);
    INSERT INTO names_fts(rowid, name) VALUES (new.id, new.name);
END;
"""


@dataclass(frozen=True)
class FileHit:
    path: str
    name: str
    is_dir: bool
    size: int
    mtime: float


@dataclass
class RefreshStats:
    folders_scanned: int = 0
    folders_unchanged: int = 0
    entries_added: int = 0
    entries_removed: int = 0
    errors: int = 0
    seconds: float = 0.0
    complete: bool = True


def _like_escape(text):
    return (text.replace("\\", "\\\\").replace("%", "\\%")
            .replace("_", "\\_"))


def _terms(query):
    return [t for t in query.casefold().split() if t][:6]


def _rank(hit, query):
    name = hit.name.casefold()
    wanted = query.casefold().strip()

    if name == wanted or os.path.splitext(name)[0] == wanted:
        tier = 0
    elif name.startswith(wanted):
        tier = 1
    else:
        tier = 2

    return (tier, -hit.mtime, len(hit.path))


def _skip_entry(entry):
    """Never index or follow symlinks/junctions."""

    try:
        if entry.is_symlink():
            return True
        junction = getattr(entry, "is_junction", None)
        return bool(junction and junction())
    except OSError:
        return True


def live_search(roots, query, kind="any", limit=15, time_limit=3.0):
    """Walk the allowed folders directly. Slower, but needs no index.

    Returns (hits, complete). complete is False if the time limit stopped it.
    """

    terms = _terms(query)
    hits = []
    deadline = time.monotonic() + time_limit
    stack = [str(r) for r in roots]

    while stack:
        if time.monotonic() > deadline:
            return _finish(hits, query, limit), False

        directory = stack.pop()

        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if _skip_entry(entry):
                        continue

                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue

                    if is_dir and entry.name.casefold() in EXCLUDED_DIRS:
                        continue

                    if is_dir:
                        stack.append(entry.path)

                    wanted_kind = (kind == "any" or (kind == "folder") == is_dir)
                    lowered = entry.name.casefold()

                    if wanted_kind and all(t in lowered for t in terms):
                        try:
                            st = entry.stat(follow_symlinks=False)
                            hits.append(FileHit(entry.path, entry.name, is_dir,
                                                st.st_size, st.st_mtime))
                        except OSError:
                            continue
        except OSError:
            continue

    return _finish(hits, query, limit), True


def _finish(hits, query, limit):
    return sorted(hits, key=lambda h: _rank(h, query))[:limit]


class FileIndex:
    def __init__(self, path, excluded=EXCLUDED_DIRS):
        self.path = Path(path)
        self.excluded = excluded
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.last_stats = None
        self.last_error = ""
        self._ready = False
        self._rerun = None          # (roots, throttle) asked for mid-refresh

    # ---- plumbing ---------------------------------------------------

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row

        if not self._ready:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            self._ready = True

        return connection

    # ---- refresh ----------------------------------------------------

    def refresh(self, roots, should_stop=lambda: False, throttle=0.0):
        """Bring the index up to date for these roots (incremental).

        Entries outside the given roots are removed, so shrinking the
        allowed folders also shrinks the index.
        """

        started = time.monotonic()
        stats = RefreshStats()
        roots = [str(Path(os.path.realpath(r))) for r in roots]
        connection = self._connect()

        try:
            self._drop_outside(connection, roots, stats)

            for root in roots:
                if not self._scan_root(connection, root, should_stop,
                                       throttle, stats):
                    stats.complete = False
                    break

            connection.commit()

            if stats.complete:
                connection.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('last_refresh', ?)",
                    (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),))
                connection.commit()
        finally:
            connection.close()

        stats.seconds = time.monotonic() - started
        self.last_stats = stats
        return stats

    def _drop_outside(self, connection, roots, stats):
        rows = connection.execute("SELECT DISTINCT parent FROM entries").fetchall()
        known = [row["parent"] for row in rows]

        for parent in known:
            if not any(parent == r or parent.startswith(r + os.sep)
                       for r in roots):
                cursor = connection.execute(
                    "DELETE FROM entries WHERE parent = ?", (parent,))
                stats.entries_removed += cursor.rowcount
                connection.execute("DELETE FROM dirs WHERE path = ?", (parent,))

        connection.commit()

    def _delete_subtree(self, connection, directory):
        pattern = _like_escape(directory + os.sep) + "%"
        cursor = connection.execute(
            "DELETE FROM entries WHERE path LIKE ? ESCAPE '\\'", (pattern,))
        connection.execute(
            "DELETE FROM dirs WHERE path = ? OR path LIKE ? ESCAPE '\\'",
            (directory, pattern))
        return cursor.rowcount

    def _scan_root(self, connection, root, should_stop, throttle, stats):
        stack = [root]
        since_commit = 0

        while stack:
            if should_stop():
                return False

            directory = stack.pop()

            try:
                mtime = os.stat(directory).st_mtime
            except OSError:
                stats.entries_removed += self._delete_subtree(
                    connection, directory)
                continue

            known = connection.execute(
                "SELECT mtime FROM dirs WHERE path = ?", (directory,)
            ).fetchone()

            if known and known["mtime"] == mtime:
                stats.folders_unchanged += 1
                children = connection.execute(
                    "SELECT path FROM entries WHERE parent = ? AND is_dir = 1",
                    (directory,)).fetchall()
                stack.extend(row["path"] for row in children)
                continue

            current = {}

            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if _skip_entry(entry):
                            continue
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if (is_dir and entry.name.casefold()
                                    in self.excluded):
                                continue
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        current[entry.path] = (
                            entry.name, st.st_size, st.st_mtime, int(is_dir))
            except OSError:
                stats.errors += 1
                continue

            stats.folders_scanned += 1
            old = {
                row["path"]: row for row in connection.execute(
                    "SELECT path, size, mtime, is_dir FROM entries "
                    "WHERE parent = ?", (directory,))
            }

            for path in old.keys() - current.keys():
                if old[path]["is_dir"]:
                    stats.entries_removed += self._delete_subtree(
                        connection, path)
                connection.execute("DELETE FROM entries WHERE path = ?", (path,))
                stats.entries_removed += 1

            for path, (name, size, entry_mtime, is_dir) in current.items():
                if path not in old:
                    connection.execute(
                        "INSERT INTO entries (path, name, parent, size, mtime, "
                        "is_dir) VALUES (?, ?, ?, ?, ?, ?)",
                        (path, name, directory, size, entry_mtime, is_dir))
                    stats.entries_added += 1
                elif (old[path]["size"], old[path]["mtime"]) != (size, entry_mtime):
                    connection.execute(
                        "UPDATE entries SET size = ?, mtime = ? WHERE path = ?",
                        (size, entry_mtime, path))

                if is_dir:
                    stack.append(path)

            connection.execute(
                "INSERT OR REPLACE INTO dirs (path, mtime) VALUES (?, ?)",
                (directory, mtime))

            since_commit += 1
            if since_commit >= 100:
                connection.commit()
                since_commit = 0

            if throttle:
                time.sleep(throttle)

        return True

    # ---- background ---------------------------------------------------

    @property
    def indexing(self):
        return self._thread is not None and self._thread.is_alive()

    def refresh_in_background(self, roots, throttle=0.0):
        """Start a refresh on a low-priority daemon thread.

        Returns False if one is already running; in that case the request is
        remembered and a fresh refresh runs as soon as the current one ends,
        so a change made mid-scan is never missed.
        """

        with self._lock:
            if self.indexing:
                self._rerun = (list(roots), throttle)
                return False

            self._stop.clear()
            self.last_error = ""
            self._thread = threading.Thread(
                target=self._run, args=(list(roots), throttle),
                name="quanttum-file-index", daemon=True)
            self._thread.start()
            return True

    def _run(self, roots, throttle):
        while True:
            try:
                self.refresh(roots, self._stop.is_set, throttle)
            except Exception as error:           # never kill the app
                self.last_error = str(error)

            with self._lock:
                again, self._rerun = self._rerun, None

            if again is None or self._stop.is_set():
                return

            roots, throttle = again

    def stop(self, wait=3.0):
        self._stop.set()

        if self._thread is not None:
            self._thread.join(wait)

    def wait_until_idle(self, timeout=30.0):
        if self._thread is not None:
            self._thread.join(timeout)

        return not self.indexing

    # ---- queries ----------------------------------------------------

    def status(self):
        if not self.path.exists():
            return {"entries": 0, "last_refresh": "", "indexing": self.indexing,
                    "error": self.last_error}

        connection = self._connect()

        try:
            entries = connection.execute(
                "SELECT count(*) FROM entries").fetchone()[0]
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'last_refresh'").fetchone()
        finally:
            connection.close()

        return {"entries": entries, "last_refresh": row["value"] if row else "",
                "indexing": self.indexing, "error": self.last_error}

    def search(self, query, roots, kind="any", limit=15):
        """Find names containing every word of the query, inside roots."""

        terms = _terms(query)

        if not terms or not roots:
            return []

        real_roots = [str(Path(os.path.realpath(r))) for r in roots]
        where = []
        args = []

        long_terms = [t for t in terms if len(t) >= 3]
        if long_terms:
            match = " AND ".join('"' + t.replace('"', '""') + '"'
                                 for t in long_terms)
            where.append("e.id IN (SELECT rowid FROM names_fts "
                         "WHERE names_fts MATCH ?)")
            args.append(match)

        for term in terms:
            if len(term) < 3:
                where.append("lower(e.name) LIKE ? ESCAPE '\\'")
                args.append("%" + _like_escape(term) + "%")

        root_sql = []
        for root in real_roots:
            root_sql.append("(e.path LIKE ? ESCAPE '\\')")
            args.append(_like_escape(root + os.sep) + "%")
        where.append("(" + " OR ".join(root_sql) + ")")

        if kind == "folder":
            where.append("e.is_dir = 1")
        elif kind == "file":
            where.append("e.is_dir = 0")

        sql = ("SELECT e.path, e.name, e.is_dir, e.size, e.mtime FROM entries "
               "AS e WHERE " + " AND ".join(where) +
               " ORDER BY e.mtime DESC LIMIT 300")

        connection = self._connect()

        try:
            rows = connection.execute(sql, args).fetchall()
        finally:
            connection.close()

        hits = [FileHit(r["path"], r["name"], bool(r["is_dir"]),
                        r["size"] or 0, r["mtime"] or 0.0) for r in rows]

        return _finish(hits, query, limit)
