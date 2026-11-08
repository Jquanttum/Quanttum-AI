import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.memory import Memory, SCHEMA_VERSION


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = str(Path(self.tmp.name) / "data" / "test.db")
        self.memory = Memory(self.path)

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def test_remember_and_search(self):
        self.memory.remember("I prefer Python for development.")
        self.assertEqual(self.memory.search("python"),
                         ["I prefer Python for development."])

    def test_duplicates_are_case_insensitive(self):
        self.memory.remember("I prefer Python")
        self.memory.remember("i PREFER python")
        self.assertEqual(len(self.memory.search()), 1)

    def test_search_records_returns_ids(self):
        self.memory.remember("Quanttum AI is my project")
        records = self.memory.search_records("project")
        self.assertEqual(len(records), 1)
        memory_id, fact = records[0]
        self.assertIsInstance(memory_id, int)
        self.assertEqual(self.memory.get(memory_id), fact)

    def test_forget_deactivates_but_keeps_the_row(self):
        self.memory.remember("Temporary fact")
        memory_id = self.memory.search_records()[0][0]

        self.assertTrue(self.memory.forget(memory_id))
        self.assertEqual(self.memory.search(), [])
        self.assertIsNone(self.memory.get(memory_id))

        row = self.memory.connection.execute(
            "SELECT active FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        self.assertEqual(row["active"], 0)

    def test_forget_unknown_or_repeated(self):
        self.assertFalse(self.memory.forget(999))
        self.memory.remember("x fact")
        memory_id = self.memory.search_records()[0][0]
        self.assertTrue(self.memory.forget(memory_id))
        self.assertFalse(self.memory.forget(memory_id))

    def test_remembering_again_reactivates(self):
        self.memory.remember("Reactivate me")
        memory_id = self.memory.search_records()[0][0]
        self.memory.forget(memory_id)
        self.memory.remember("reactivate me")
        self.assertEqual(len(self.memory.search()), 1)

    def test_blank_messages_are_not_saved(self):
        self.memory.save("user", "   ")
        self.assertEqual(self.memory.recent(), [])

    def test_recent_is_chronological(self):
        self.memory.save("user", "first")
        self.memory.save("assistant", "second")
        self.assertEqual([r[1] for r in self.memory.recent()],
                         ["first", "second"])

    def test_data_survives_restart(self):
        self.memory.remember("Persistent fact")
        self.memory.save("user", "hello")
        self.memory.close()

        reopened = Memory(self.path)
        try:
            self.assertEqual(reopened.search(), ["Persistent fact"])
            self.assertEqual(len(reopened.recent()), 1)
        finally:
            reopened.close()
        self.memory = Memory(self.path)  # so tearDown has something to close

    def test_new_database_gets_current_schema_version(self):
        version = self.memory.connection.execute(
            "PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, SCHEMA_VERSION)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = str(Path(self.tmp.name) / "legacy.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_pre_versioning_database_is_adopted_without_data_loss(self):
        # Exactly the schema shipped before versioning existed.
        raw = sqlite3.connect(self.path)
        raw.executescript("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL COLLATE NOCASE UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO messages (role, content) VALUES ('user', 'old chat');
            INSERT INTO memories (fact) VALUES ('old fact');
        """)
        raw.commit()
        raw.close()

        memory = Memory(self.path)
        try:
            self.assertEqual(memory.search(), ["old fact"])
            self.assertEqual(memory.recent()[0][1], "old chat")
            version = memory.connection.execute(
                "PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, SCHEMA_VERSION)
        finally:
            memory.close()

    def test_newer_database_is_refused(self):
        raw = sqlite3.connect(self.path)
        raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        raw.commit()
        raw.close()

        with self.assertRaises(RuntimeError):
            Memory(self.path)


class ConfigTests(unittest.TestCase):
    def test_database_path_is_absolute_and_inside_project(self):
        path = Path(config.DATABASE_PATH)
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.parent, config.BASE_DIR / "data")


if __name__ == "__main__":
    unittest.main()
