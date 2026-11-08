import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.memory import Memory, SCHEMA_VERSION
from core.textutils import categorise, fts_query, significant_terms


def make_v1_database(path):
    """The exact schema shipped in 0.1.1 / 0.2.0-dev (user_version 1)."""

    raw = sqlite3.connect(path)
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
        INSERT INTO memories (fact, active) VALUES ('I prefer Python', 1);
        INSERT INTO memories (fact, active) VALUES ('Quanttum AI is my project', 1);
        INSERT INTO memories (fact, active) VALUES ('An old forgotten fact', 0);
        PRAGMA user_version = 1;
    """)
    raw.commit()
    raw.close()


class TempDbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = str(Path(self.tmp.name) / "test.db")
        self.opened = []

    def open(self, path=None):
        memory = Memory(path or self.path)
        self.opened.append(memory)
        return memory

    def tearDown(self):
        for memory in self.opened:
            try:
                memory.close()
            except sqlite3.ProgrammingError:
                pass
        self.tmp.cleanup()


class MigrationV2Tests(TempDbCase):
    def test_v1_data_is_converted_not_lost(self):
        make_v1_database(self.path)
        memory = self.open()

        self.assertEqual(memory.search(limit=10).__len__(), 2)
        self.assertEqual(memory.recent()[0][1], "old chat")

        forgotten = memory.lookup("An old forgotten fact")
        self.assertEqual(forgotten.status, "forgotten")

        columns = [r["name"] for r in memory.connection.execute(
            "PRAGMA table_info(memories)")]
        self.assertNotIn("active", columns)
        for name in ("status", "category", "source_message_id",
                     "last_used_at", "supersedes_id"):
            self.assertIn(name, columns)

        version = memory.connection.execute(
            "PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, SCHEMA_VERSION)

    def test_existing_facts_get_categories_and_are_searchable(self):
        make_v1_database(self.path)
        memory = self.open()
        self.assertEqual(memory.lookup("I prefer Python").category,
                         "preference")
        self.assertEqual(memory.lookup("Quanttum AI is my project").category,
                         "general")
        self.assertEqual([r.fact for r in memory.find("project")],
                         ["Quanttum AI is my project"])

    def test_backup_is_written_before_upgrading(self):
        make_v1_database(self.path)
        self.open()
        backup = Path(self.path + ".bak-v1")
        self.assertTrue(backup.exists())

        old = sqlite3.connect(str(backup))
        try:
            columns = [r[1] for r in old.execute("PRAGMA table_info(memories)")]
            self.assertIn("active", columns)     # untouched v1 copy
            self.assertEqual(
                old.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            old.close()

    def test_brand_new_database_needs_no_backup(self):
        self.open()
        self.assertFalse(Path(self.path + ".bak-v0").exists())

    def test_failed_migration_leaves_database_unchanged(self):
        make_v1_database(self.path)

        def broken(self):
            self.connection.execute(
                "ALTER TABLE memories ADD COLUMN category TEXT")
            raise ValueError("boom")

        with mock.patch.object(Memory, "_migrate_to_2", broken):
            with self.assertRaises(ValueError):
                Memory(self.path)

        raw = sqlite3.connect(self.path)
        try:
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 1)
            columns = [r[1] for r in raw.execute("PRAGMA table_info(memories)")]
            self.assertNotIn("category", columns)
            self.assertIn("active", columns)
        finally:
            raw.close()

        memory = self.open()                      # and it still upgrades
        self.assertEqual(len(memory.find(limit=10)), 2)


class SearchTests(TempDbCase):
    def setUp(self):
        super().setUp()
        self.memory = self.open()

    def facts(self, query, **kwargs):
        return [r.fact for r in self.memory.find(query, **kwargs)]

    def test_stemming_finds_plural_and_other_forms(self):
        self.memory.remember("Quanttum AI is my assistant project")
        self.assertEqual(self.facts("projects"),
                         ["Quanttum AI is my assistant project"])
        self.assertEqual(self.facts("my projects"),
                         ["Quanttum AI is my assistant project"])

    def test_fillers_do_not_match(self):
        self.memory.remember("I like tea")
        self.assertEqual(self.facts("What is the weather like?"), [])

    def test_questions_find_facts_by_the_informative_word(self):
        self.memory.remember("I prefer Python for development.")
        self.assertEqual(len(self.facts("Which language do I prefer?")), 1)

    def test_more_matching_terms_rank_first(self):
        for fact in ("Python is great", "Tea is nice",
                     "Rain falls in April", "Exams start in May",
                     "I prefer Python for development",
                     "The lab opens at nine"):
            self.memory.remember(fact)
        results = self.facts("python development")
        self.assertEqual(results[0], "I prefer Python for development")
        self.assertIn("Python is great", results)

    def test_forgotten_and_superseded_are_not_found(self):
        self.memory.remember("Alpha fact about cats")
        self.memory.remember("Beta fact about cats")
        alpha = self.memory.lookup("Alpha fact about cats").id
        self.memory.forget(alpha)
        self.assertEqual(self.facts("cats"), ["Beta fact about cats"])

    def test_index_stays_in_sync_with_inserts_and_forget(self):
        self.memory.remember("Zebra stripes")
        self.assertEqual(len(self.facts("zebra")), 1)
        self.memory.forget(self.memory.lookup("Zebra stripes").id)
        self.assertEqual(self.facts("zebra"), [])
        self.memory.remember("zebra STRIPES")          # reactivates same row
        self.assertEqual(len(self.facts("zebra")), 1)

    def test_empty_query_returns_most_recent_first(self):
        self.memory.remember("first fact here")
        self.memory.remember("second fact here")
        self.assertEqual(self.facts("")[0], "second fact here")

    def test_category_filter(self):
        self.memory.remember("call the supervisor", category="task")
        self.memory.remember("I prefer tea")
        self.assertEqual(self.facts("", category="task"),
                         ["call the supervisor"])
        self.assertEqual(self.facts("", category="preference"),
                         ["I prefer tea"])

    def test_query_text_cannot_break_the_search_syntax(self):
        self.memory.remember("Plain fact about tea")
        for nasty in ('tea"', "tea OR", "NEAR(tea", "tea*", "col:tea", '"'):
            with self.subTest(query=nasty):
                self.memory.find(nasty)      # must not raise


class SimilarityAndReplaceTests(TempDbCase):
    def setUp(self):
        super().setUp()
        self.memory = self.open()

    def test_same_sentence_frame_is_similar(self):
        self.memory.remember("My name is Jerry")
        found = self.memory.find_similar("My name is Quanttum")
        self.assertEqual(found.fact, "My name is Jerry")

    def test_unrelated_and_identical_are_not_similar(self):
        self.memory.remember("My name is Jerry")
        self.assertIsNone(self.memory.find_similar("I study forensic science"))
        self.assertIsNone(self.memory.find_similar("my name is jerry"))

    def test_tasks_are_never_similar_candidates(self):
        self.memory.remember("call my supervisor on monday", category="task")
        self.assertIsNone(
            self.memory.find_similar("call my supervisor on friday"))

    def test_replace_supersedes_and_keeps_history(self):
        old = self.memory.remember("I prefer Python")
        new = self.memory.replace(old, "I prefer Rust", source_message_id=7)

        self.assertNotEqual(new, old)
        self.assertEqual(self.memory.lookup("I prefer Python").status,
                         "superseded")
        self.assertEqual(self.memory.lookup("I prefer Rust").status, "active")
        row = self.memory.connection.execute(
            "SELECT supersedes_id, source_message_id FROM memories WHERE id=?",
            (new,)).fetchone()
        self.assertEqual((row["supersedes_id"], row["source_message_id"]),
                         (old, 7))
        self.assertEqual(self.memory.search("python"), [])

    def test_replace_requires_an_active_memory(self):
        old = self.memory.remember("I prefer Python")
        self.memory.forget(old)
        self.assertIsNone(self.memory.replace(old, "I prefer Rust"))
        self.assertIsNone(self.memory.lookup("I prefer Rust"))

    def test_replace_with_identical_text_changes_nothing(self):
        old = self.memory.remember("I prefer Python")
        self.assertEqual(self.memory.replace(old, "i PREFER python"), old)
        self.assertEqual(self.memory.lookup("I prefer Python").status, "active")

    def test_replacing_back_reactivates_the_old_row(self):
        python = self.memory.remember("I prefer Python")
        rust = self.memory.replace(python, "I prefer Rust")
        again = self.memory.replace(rust, "I prefer Python")
        self.assertEqual(again, python)
        self.assertEqual(self.memory.lookup("I prefer Python").status, "active")
        self.assertEqual(self.memory.lookup("I prefer Rust").status,
                         "superseded")

    def test_related_excludes_itself(self):
        a = self.memory.remember("Python is my favourite language")
        self.memory.remember("I write Python every day")
        self.memory.remember("The lab opens at nine")
        related = self.memory.related(a)
        self.assertEqual([r.fact for r in related],
                         ["I write Python every day"])

    def test_touch_records_last_used(self):
        memory_id = self.memory.remember("Something to touch")
        self.memory.touch([memory_id])
        row = self.memory.connection.execute(
            "SELECT last_used_at FROM memories WHERE id = ?",
            (memory_id,)).fetchone()
        self.assertIsNotNone(row["last_used_at"])

    def test_source_message_is_recorded(self):
        message_id = self.memory.save("user", "Remember that x y z")
        memory_id = self.memory.remember("x y z", source_message_id=message_id)
        row = self.memory.connection.execute(
            "SELECT source_message_id FROM memories WHERE id = ?",
            (memory_id,)).fetchone()
        self.assertEqual(row["source_message_id"], message_id)


class TextUtilTests(unittest.TestCase):
    def test_significant_terms(self):
        self.assertEqual(
            significant_terms("What do you remember about my Python projects?"),
            ["python", "projects"])
        self.assertEqual(significant_terms("the and of"), [])

    def test_fts_query_quotes_every_term(self):
        self.assertEqual(fts_query(["a", 'b"c']), '"a" OR "b""c"')

    def test_categorise(self):
        self.assertEqual(categorise("I prefer Python"), "preference")
        self.assertEqual(categorise("My favourite colour is blue"),
                         "preference")
        self.assertEqual(categorise("I really love jollof rice"), "preference")
        self.assertEqual(categorise("This looks like rain"), "general")
        self.assertEqual(categorise("Quanttum AI is my project"), "general")


if __name__ == "__main__":
    unittest.main()
