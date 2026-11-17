import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.actions.base import ActionError
from core.audit import AuditLog
from core.memory import Memory
from core.policy import FolderPolicy


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(os.path.realpath(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def folder(self, *parts):
        path = self.base.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path


class AuditTests(TempCase):
    def setUp(self):
        super().setUp()
        self.memory = Memory(str(self.base / "a.db"))
        self.audit = AuditLog(self.memory.connection)

    def tearDown(self):
        self.memory.close()
        super().tearDown()

    def test_events_are_recorded_newest_first(self):
        self.audit.record("r1", "find_files", 1, "requested", {"q": "x"})
        self.audit.record("r1", "find_files", 1, "succeeded", detail="ok")
        events = self.audit.recent()
        self.assertEqual([e.event for e in events], ["succeeded", "requested"])
        self.assertEqual(events[1].params, '{"q": "x"}')
        self.assertEqual([e.event for e in self.audit.history("r1")],
                         ["requested", "succeeded"])

    def test_the_log_cannot_be_edited_or_deleted(self):
        self.audit.record("r1", "a", 1, "requested")
        with self.assertRaises(sqlite3.DatabaseError):
            self.memory.connection.execute("UPDATE audit_log SET event='x'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.memory.connection.execute("DELETE FROM audit_log")
        self.assertEqual(len(self.audit.recent()), 1)

    def test_last_undoable_skips_undone_and_actions_without_undo(self):
        undo = {"action": "remove_empty_folder", "params": {"paths": ["p"]}}
        self.audit.record("r1", "create_folder", 2, "succeeded", undo=undo)
        self.audit.record("r2", "find_files", 1, "succeeded")
        self.assertEqual(self.audit.last_undoable().request_id, "r1")
        self.audit.record("r1", "remove_empty_folder", 2, "undone")
        self.assertIsNone(self.audit.last_undoable())


class PolicyTests(TempCase):
    def policy(self, *defaults, **kwargs):
        kwargs.setdefault("home", self.base / "home")
        kwargs.setdefault("protected", [])
        return FolderPolicy(kwargs.pop("path", None), defaults=defaults,
                            **kwargs)

    def test_only_paths_inside_allowed_folders_are_allowed(self):
        docs = self.folder("home", "Documents")
        policy = self.policy(docs)
        self.assertTrue(policy.is_allowed(docs))
        self.assertTrue(policy.is_allowed(docs / "a" / "b.txt"))
        self.assertFalse(policy.is_allowed(self.base / "home"))
        self.assertFalse(policy.is_allowed(self.base))

    def test_dotdot_cannot_escape(self):
        docs = self.folder("home", "Documents")
        self.folder("home", "Secret")
        policy = self.policy(docs)
        self.assertFalse(policy.is_allowed(docs / ".." / "Secret"))

    def test_sibling_with_the_same_prefix_is_not_inside(self):
        docs = self.folder("home", "Documents")
        self.folder("home", "Documents2")
        self.assertFalse(self.policy(docs).is_allowed(
            self.base / "home" / "Documents2" / "x"))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_symlink_out_of_an_allowed_folder_is_refused(self):
        docs = self.folder("home", "Documents")
        outside = self.folder("outside")
        try:
            os.symlink(outside, docs / "link")
        except OSError:
            self.skipTest("cannot create symlinks here")
        self.assertFalse(self.policy(docs).is_allowed(docs / "link" / "f.txt"))

    def test_no_allowed_folders_means_nothing_is_allowed(self):
        self.assertEqual(self.policy().roots, [])
        self.assertFalse(self.policy().is_allowed(self.base))

    def test_validate_root_rules(self):
        policy = self.policy()
        project = self.folder("home", "Documents", "Project")
        self.assertEqual(policy.validate_root(f'"{project}"'), project)
        for bad, fragment in (
            ("relative/dir", "full path"),
            (str(self.base / "missing"), "isn't an existing folder"),
            (str(self.base / "home"), "too broad"),           # home itself
            (str(self.base), "too broad"),                    # above home
            (os.path.abspath(os.sep), "too broad"),           # drive root
            ("", "Which folder"),
        ):
            with self.subTest(path=bad):
                with self.assertRaises(ActionError) as caught:
                    policy.validate_root(bad)
                self.assertIn(fragment, str(caught.exception))

    def test_protected_system_folders_are_refused(self):
        windows = self.folder("fake", "Windows")
        policy = self.policy(protected=[windows])
        with self.assertRaises(ActionError):
            policy.validate_root(str(self.folder("fake", "Windows", "System32")))

    def test_add_and_remove_persist_and_start_from_defaults(self):
        docs = self.folder("home", "Documents")
        extra = self.folder("work", "Reports")
        path = self.base / "permissions.json"

        policy = self.policy(docs, path=path)
        self.assertTrue(policy.uses_defaults)
        policy.add(extra)
        self.assertFalse(policy.uses_defaults)

        again = self.policy(docs, path=path)
        self.assertEqual(set(again.roots), {docs, extra})

        again.remove(str(docs))
        self.assertEqual(self.policy(docs, path=path).roots, [extra])

    def test_adding_twice_or_removing_unknown_is_an_error(self):
        extra = self.folder("work")
        policy = self.policy()
        policy.add(extra)
        with self.assertRaises(ActionError):
            policy.add(extra)
        with self.assertRaises(ActionError):
            policy.remove(str(self.folder("elsewhere")))

    def test_corrupt_permissions_fail_closed_not_to_defaults(self):
        docs = self.folder("home", "Documents")
        path = self.base / "permissions.json"
        path.write_text("{ broken", encoding="utf-8")
        policy = self.policy(docs, path=path)
        self.assertEqual(policy.roots, [])
        self.assertIn("could not be read", policy.error)
        self.assertTrue(Path(str(path) + ".corrupt").exists())

    def test_failed_save_changes_nothing(self):
        blocker = self.base / "blocker"
        blocker.write_text("file")
        policy = self.policy(path=blocker / "permissions.json")
        extra = self.folder("work")
        with self.assertRaises(OSError):
            policy.add(extra)
        self.assertEqual(policy.roots, [])

    def test_known_folders_follow_windows_when_it_knows_better(self):
        from unittest import mock
        from core import policy as policy_module
        onedrive = self.folder("OneDrive", "Documents")
        home = self.base / "home"

        with mock.patch.object(policy_module.sys, "platform", "win32"), \
             mock.patch.object(policy_module, "_windows_known_folder",
                               return_value=str(onedrive)):
            self.assertEqual(policy_module.known_folder("documents"), onedrive)
            # an explicit home (as in tests) never consults Windows
            self.assertEqual(policy_module.known_folder("documents", home),
                             home / "Documents")

        with mock.patch.object(policy_module.sys, "platform", "win32"), \
             mock.patch.object(policy_module, "_windows_known_folder",
                               side_effect=OSError("no shell")):
            fallback = policy_module.known_folder("documents")
            self.assertEqual(fallback, Path.home() / "Documents")

        with mock.patch.object(policy_module.sys, "platform", "win32"), \
             mock.patch.object(policy_module, "_windows_known_folder",
                               return_value=None):
            self.assertEqual(policy_module.known_folder("desktop"),
                             Path.home() / "Desktop")

    def test_default_user_folders_lists_only_existing_ones(self):
        from core.policy import default_user_folders
        home = self.base / "home"
        docs = self.folder("home", "Documents")
        self.assertEqual(default_user_folders(home), [docs])

    def test_location_aliases(self):
        policy = self.policy()
        self.assertEqual(policy.location("documents"),
                         self.base / "home" / "Documents")
        with self.assertRaises(ActionError):
            policy.location("somewhere")


if __name__ == "__main__":
    unittest.main()
