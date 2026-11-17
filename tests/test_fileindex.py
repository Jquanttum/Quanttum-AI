import os
import tempfile
import time
import unittest
from pathlib import Path

from core.fileindex import FileIndex, live_search


class IndexCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.root = self.base / "docs"
        self.root.mkdir()
        self.index = FileIndex(self.base / "idx.db")

    def tearDown(self):
        self.index.stop()
        self.tmp.cleanup()

    def make(self, *relative):
        path = self.root.joinpath(*relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return path

    def names(self, query, kind="any", roots=None):
        hits = self.index.search(query, roots or [self.root], kind)
        return [h.name for h in hits]


class SearchTests(IndexCase):
    def setUp(self):
        super().setUp()
        self.make("Exam Notes.docx")
        self.make("sub", "exam_results.pdf")
        self.make("sub", "deep", "Thesis Draft.txt")
        self.make("readme.md")
        self.index.refresh([self.root])

    def test_substring_and_case_insensitive(self):
        self.assertCountEqual(self.names("EXAM"),
                              ["Exam Notes.docx", "exam_results.pdf"])
        self.assertEqual(self.names("sis dra"), ["Thesis Draft.txt"])

    def test_all_words_must_match(self):
        self.assertEqual(self.names("exam notes"), ["Exam Notes.docx"])

    def test_short_terms_work_too(self):
        self.assertEqual(self.names("md"), ["readme.md"])

    def test_kind_filter(self):
        self.assertEqual(self.names("deep", kind="folder"), ["deep"])
        self.assertEqual(self.names("deep", kind="file"), [])

    def test_exact_and_prefix_matches_rank_first(self):
        self.make("my readme.txt")
        self.index.refresh([self.root])
        self.assertEqual(self.names("readme")[0], "readme.md")

    def test_special_characters_are_literal(self):
        self.make("100%_done (final).txt")
        self.index.refresh([self.root])
        self.assertEqual(self.names("100%_done"), ["100%_done (final).txt"])
        self.assertEqual(self.names("%"), ["100%_done (final).txt"])
        for nasty in ('"', "a OR b", "name:x", "NEAR(a b)", "*", "'; DROP"):
            self.names(nasty)                       # must not raise

    def test_results_never_leave_the_given_roots(self):
        other = self.base / "docs-private"
        other.mkdir()
        (other / "exam secret.txt").write_text("x")
        self.index.refresh([self.root, other])
        self.assertNotIn("exam secret.txt", self.names("exam"))
        self.assertIn("exam secret.txt", self.names("exam", roots=[other]))

    def test_empty_query_or_roots(self):
        self.assertEqual(self.index.search("  ", [self.root]), [])
        self.assertEqual(self.index.search("exam", []), [])


class RefreshTests(IndexCase):
    def test_first_scan_counts(self):
        self.make("a.txt")
        self.make("d", "b.txt")
        stats = self.index.refresh([self.root])
        self.assertEqual((stats.entries_added, stats.folders_scanned), (3, 2))
        self.assertTrue(stats.complete)
        self.assertEqual(self.index.status()["entries"], 3)
        self.assertTrue(self.index.status()["last_refresh"])

    def test_unchanged_folders_are_not_rescanned(self):
        self.make("d", "b.txt")
        self.index.refresh([self.root])
        stats = self.index.refresh([self.root])
        self.assertEqual(stats.folders_scanned, 0)
        self.assertEqual(stats.folders_unchanged, 2)
        self.assertEqual(stats.entries_added, 0)

    def test_new_deleted_and_renamed_entries(self):
        self.make("keep.txt")
        gone = self.make("gone.txt")
        self.make("olddir", "inner.txt")
        self.index.refresh([self.root])

        self.make("brand new.txt")
        gone.unlink()
        (self.root / "olddir").rename(self.root / "newdir")
        os.utime(self.root, None)
        self.index.refresh([self.root])

        self.assertEqual(self.names("brand"), ["brand new.txt"])
        self.assertEqual(self.names("gone"), [])
        self.assertEqual(self.names("olddir"), [])
        self.assertEqual(self.names("inner"), ["inner.txt"])
        self.assertEqual(self.names("newdir", kind="folder"), ["newdir"])

    def test_change_deep_in_the_tree_is_found_without_touching_parents(self):
        self.make("a", "b", "c", "start.txt")
        self.index.refresh([self.root])
        self.make("a", "b", "c", "late addition.txt")
        stats = self.index.refresh([self.root])
        self.assertEqual(self.names("late"), ["late addition.txt"])
        self.assertEqual(stats.folders_scanned, 1)

    def test_deleted_folder_removes_its_whole_subtree(self):
        self.make("tree", "x", "deep.txt")
        self.index.refresh([self.root])
        import shutil
        shutil.rmtree(self.root / "tree")
        os.utime(self.root, None)
        self.index.refresh([self.root])
        self.assertEqual(self.names("deep"), [])
        self.assertEqual(self.index.status()["entries"], 0)

    def test_shrinking_the_roots_shrinks_the_index(self):
        other = self.base / "other"
        other.mkdir()
        (other / "o.txt").write_text("x")
        self.make("m.txt")
        self.index.refresh([self.root, other])
        self.index.refresh([self.root])
        self.assertEqual(self.names("o.txt", roots=[other]), [])
        self.assertEqual(self.names("m.txt"), ["m.txt"])

    def test_excluded_folders_are_skipped(self):
        self.make(".git", "config")
        self.make("node_modules", "pkg", "index.js")
        self.make("real.txt")
        self.index.refresh([self.root])
        self.assertEqual(self.names("config"), [])
        self.assertEqual(self.names("index.js"), [])
        self.assertEqual(self.names("real"), ["real.txt"])

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_symlinks_are_never_followed_or_indexed(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("x")
        try:
            os.symlink(outside, self.root / "link")
        except OSError:
            self.skipTest("cannot create symlinks here")
        self.index.refresh([self.root])
        self.assertEqual(self.names("secret"), [])
        self.assertEqual(self.names("link"), [])

    def test_stop_request_ends_a_scan_cleanly(self):
        for i in range(30):
            self.make(f"d{i}", "f.txt")
        stats = self.index.refresh([self.root], should_stop=lambda: True)
        self.assertFalse(stats.complete)
        self.assertEqual(self.index.status()["last_refresh"], "")

    def test_vanished_root_is_handled(self):
        self.make("a.txt")
        self.index.refresh([self.root])
        import shutil
        shutil.rmtree(self.root)
        stats = self.index.refresh([self.root])
        self.assertEqual(self.index.status()["entries"], 0)
        self.assertTrue(stats.complete)


class BackgroundTests(IndexCase):
    def test_background_refresh_completes(self):
        for i in range(20):
            self.make(f"d{i}", "f.txt")
        self.assertTrue(self.index.refresh_in_background([self.root]))
        self.assertTrue(self.index.wait_until_idle(10))
        self.assertEqual(self.index.status()["entries"], 40)
        self.assertEqual(self.index.last_error, "")

    def test_request_during_a_scan_is_not_lost(self):
        for i in range(40):
            self.make(f"d{i}", "f.txt")
        self.index.refresh_in_background([self.root], throttle=0.01)
        self.make("added during scan.txt")
        os.utime(self.root, None)
        self.assertFalse(self.index.refresh_in_background([self.root]))
        self.assertTrue(self.index.wait_until_idle(20))
        self.assertEqual(self.names("added during"), ["added during scan.txt"])

    def test_stop_ends_the_thread(self):
        for i in range(60):
            self.make(f"d{i}", "f.txt")
        self.index.refresh_in_background([self.root], throttle=0.02)
        self.index.stop(5)
        self.assertFalse(self.index.indexing)

    def test_errors_are_captured_not_raised(self):
        bad = FileIndex(self.base / "missing-parent" / "x" / "idx.db")
        bad.path = self.base / "blocker.txt"
        bad.path.write_text("i am a file")
        bad.refresh_in_background([self.root])
        bad.wait_until_idle(5)
        self.assertTrue(bad.last_error)


class LiveSearchTests(IndexCase):
    def test_finds_without_an_index_and_ranks(self):
        self.make("Exam Notes.docx")
        self.make("sub", "old exam.txt")
        hits, complete = live_search([self.root], "exam")
        self.assertTrue(complete)
        self.assertEqual(hits[0].name, "Exam Notes.docx")
        self.assertEqual(len(hits), 2)

    def test_time_limit_stops_the_walk(self):
        for i in range(20):
            self.make(f"d{i}", "f.txt")
        hits, complete = live_search([self.root], "f.txt", time_limit=-1)
        self.assertFalse(complete)

    def test_kind_filter(self):
        self.make("projects", "a.txt")
        hits, _ = live_search([self.root], "projects", kind="file")
        self.assertEqual(hits, [])
        hits, _ = live_search([self.root], "projects", kind="folder")
        self.assertEqual([h.name for h in hits], ["projects"])


class SpeedTests(IndexCase):
    def test_search_stays_fast_with_many_files(self):
        import random
        random.seed(3)
        words = "alpha beta gamma delta exam notes thesis lab data".split()
        for d in range(100):
            folder = self.root / f"dir{d}"
            folder.mkdir()
            for f in range(200):
                (folder / f"{random.choice(words)} {random.choice(words)} {f}.txt"
                 ).write_text("")
        stats = self.index.refresh([self.root])
        self.assertEqual(stats.entries_added, 20100)

        started = time.perf_counter()
        hits = self.index.search("thesis lab", [self.root], limit=15)
        elapsed = time.perf_counter() - started
        self.assertTrue(hits)
        self.assertLess(elapsed, 0.5)             # typically a few ms

        started = time.perf_counter()
        again = self.index.refresh([self.root])
        self.assertEqual(again.entries_added, 0)
        self.assertLess(time.perf_counter() - started, 5)


if __name__ == "__main__":
    unittest.main()
