import os
import tempfile
import unittest
from pathlib import Path

from core.actions.base import ActionError
from core.apps import AppCatalog, AppPolicy
from core.assistant import Assistant
from core.memory import Memory
from core.policy import FolderPolicy, PermissionsStore


class AppsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.menu = self.base / "StartMenu"
        self.bin = self.base / "bin"
        self.menu.mkdir()
        self.bin.mkdir()
        self.launched = []

    def tearDown(self):
        self.tmp.cleanup()

    def shortcut(self, name, sub=""):
        folder = self.menu / sub if sub else self.menu
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.lnk"
        path.write_text("shortcut")
        return str(path)

    def program(self, name):
        path = self.bin / name
        path.write_text("binary")
        return str(path)

    def catalog(self, which=None):
        return AppCatalog([self.menu], which=which or (lambda name: None))


class CatalogTests(AppsCase):
    def test_exact_and_partial_matches(self):
        notepad = self.shortcut("Notepad", "Accessories")
        self.shortcut("Google Chrome")
        catalog = self.catalog()
        self.assertEqual([c.path for c in catalog.search("notepad")], [notepad])
        self.assertEqual([c.name for c in catalog.search("chrome")],
                         ["Google Chrome"])
        self.assertEqual([c.name for c in catalog.search("GOOGLE chrome.lnk")],
                         ["Google Chrome"])

    def test_exact_name_beats_longer_partial_matches(self):
        self.shortcut("Word")
        self.shortcut("Word Count Tool")
        self.assertEqual([c.name for c in self.catalog().search("word")],
                         ["Word"])

    def test_ambiguous_partials_are_all_returned(self):
        self.shortcut("Google Chrome")
        self.shortcut("Google Chrome Canary")
        names = [c.name for c in self.catalog().search("chrome")]
        self.assertEqual(names, ["Google Chrome", "Google Chrome Canary"])

    def test_uninstallers_are_never_offered(self):
        self.shortcut("Uninstall Chrome")
        self.shortcut("Chrome Uninstaller")
        self.assertEqual(self.catalog().search("chrome"), [])

    def test_path_lookup_only_accepts_single_names_and_exe_files(self):
        exe = self.program("tool.exe")
        script = self.program("run.bat")
        lookup = {"tool": exe, "run": script}.get
        catalog = self.catalog(which=lookup)
        self.assertEqual([c.path for c in catalog.search("tool")], [exe])
        self.assertEqual(catalog.search("run"), [])            # .bat refused
        self.assertEqual(catalog.search("my tool"), [])        # not a single name
        self.assertEqual(catalog.search("C:\\x\\tool"), [])      # no paths

    def test_start_menu_wins_over_path(self):
        self.shortcut("Tool")
        catalog = self.catalog(which=lambda name: self.program("tool.exe"))
        self.assertEqual(len(catalog.search("tool")), 1)
        self.assertTrue(catalog.search("tool")[0].path.endswith(".lnk"))

    def test_empty_and_missing(self):
        self.assertEqual(self.catalog().search("  "), [])
        self.assertEqual(self.catalog().search("nothing here"), [])


class PolicyTests(AppsCase):
    def test_add_find_remove_persist(self):
        store_path = self.base / "perm.json"
        policy = AppPolicy(PermissionsStore(store_path))
        policy.add("Notepad", self.shortcut("Notepad"))
        policy.add("Google Chrome", self.shortcut("Google Chrome"))

        again = AppPolicy(PermissionsStore(store_path))
        self.assertEqual([a.name for a in again.approved()],
                         ["Google Chrome", "Notepad"])
        self.assertEqual([a.name for a in again.find("chrome")],
                         ["Google Chrome"])
        again.remove("notepad")
        self.assertEqual([a.name for a in again.approved()], ["Google Chrome"])

    def test_scripts_can_never_be_approved(self):
        policy = AppPolicy()
        for bad in ("evil.bat", "x.cmd", "x.ps1", "x.vbs", "x.js", "x.py"):
            with self.subTest(file=bad):
                with self.assertRaises(ActionError):
                    policy.add("Evil", self.program(bad))
        self.assertEqual(policy.approved(), [])

    def test_remove_unknown_or_ambiguous(self):
        policy = AppPolicy()
        policy.add("Chrome A", self.program("a.exe"))
        policy.add("Chrome B", self.program("b.exe"))
        with self.assertRaises(ActionError):
            policy.remove("firefox")
        with self.assertRaises(ActionError):
            policy.remove("chrome")

    def test_apps_and_folders_share_one_file_without_clobbering(self):
        store_path = self.base / "perm.json"
        store = PermissionsStore(store_path)
        folders = FolderPolicy(store=store, home=self.base / "h", protected=[])
        apps = AppPolicy(store)
        work = self.base / "work"
        work.mkdir()

        folders.add(work)
        apps.add("Tool", self.program("tool.exe"))
        folders.remove(str(work))
        apps.add("Other", self.program("other.exe"))

        fresh = PermissionsStore(store_path)
        self.assertEqual(fresh.get("allowed_folders"), [])
        self.assertEqual(len(fresh.get("allowed_apps")), 2)

    def test_unknown_sections_are_preserved(self):
        store_path = self.base / "perm.json"
        store_path.write_text('{"from_the_future": {"x": 1}}', encoding="utf-8")
        AppPolicy(PermissionsStore(store_path)).add("T", self.program("t.exe"))
        self.assertEqual(PermissionsStore(store_path).get("from_the_future"),
                         {"x": 1})

    def test_corrupt_permissions_fail_closed_for_apps_and_folders(self):
        store_path = self.base / "perm.json"
        store_path.write_text("{ nope", encoding="utf-8")
        store = PermissionsStore(store_path)
        self.assertIn("could not be read", store.error)
        self.assertEqual(AppPolicy(store).approved(), [])
        defaults = [self.base]
        self.assertEqual(FolderPolicy(defaults=defaults, store=store).roots, [])

    def test_failed_save_changes_nothing(self):
        blocker = self.base / "blocker"
        blocker.write_text("file")
        policy = AppPolicy(PermissionsStore(blocker / "perm.json"))
        with self.assertRaises(OSError):
            policy.add("T", self.program("t.exe"))
        self.assertEqual(policy.approved(), [])

    def test_malformed_records_are_ignored(self):
        store_path = self.base / "perm.json"
        store_path.write_text(
            '{"allowed_apps": {"a": "oops", "b": {"name": 1}, '
            '"c": {"name": "Good", "path": "x.exe"}}}', encoding="utf-8")
        policy = AppPolicy(PermissionsStore(store_path))
        self.assertEqual([a.name for a in policy.approved()], ["Good"])


class ConversationTests(AppsCase):
    def setUp(self):
        super().setUp()
        self.memory = Memory(str(self.base / "a.db"))
        self.chrome = self.shortcut("Google Chrome")
        self.notepad = self.shortcut("Notepad", "Accessories")
        self.apps = AppPolicy()
        self.assistant = Assistant(
            self.memory, apps=self.apps, app_catalog=self.catalog(),
            launcher=self.launched.append)

    def tearDown(self):
        self.memory.close()
        super().tearDown()

    def ask(self, text):
        return self.assistant.respond(text)

    def approve(self, name):
        self.ask(f"allow app {name}")
        return self.ask("yes")

    def test_must_approve_before_opening(self):
        answer = self.ask("open notepad")
        self.assertIn("isn't an approved app yet", answer)
        self.assertIn("allow app notepad", answer)
        self.assertEqual(self.launched, [])
        self.assertIn("nothing waiting", self.ask("yes"))
        self.assertEqual(self.launched, [])

    def test_approving_shows_the_exact_file_and_waits_for_yes(self):
        answer = self.ask("allow app notepad")
        self.assertIn(self.notepad, answer)
        self.assertIn("never with extra commands", answer)
        self.assertEqual(self.apps.approved(), [])          # not yet
        self.assertIn("Approved: Notepad", self.ask("yes"))
        self.assertEqual([a.name for a in self.apps.approved()], ["Notepad"])

    def test_declining_approval_changes_nothing(self):
        self.ask("allow app notepad")
        self.ask("no")
        self.assertEqual(self.apps.approved(), [])

    def test_full_flow_opens_only_after_a_second_yes(self):
        self.approve("notepad")
        answer = self.ask("open notepad")
        self.assertIn("about to open Notepad", answer)
        self.assertEqual(self.launched, [])                 # asked, not run
        self.assertEqual(self.ask("yes"), "Opened Notepad.")
        self.assertEqual(self.launched, [self.notepad])     # exact stored path

    def test_no_cancels_a_launch(self):
        self.approve("notepad")
        self.ask("open notepad")
        self.ask("no")
        self.assertEqual(self.launched, [])

    def test_approved_app_can_be_opened_by_a_partial_name(self):
        self.approve("google chrome")
        self.ask("launch chrome")
        self.ask("yes")
        self.assertEqual(self.launched, [self.chrome])

    def test_an_unapproved_app_never_launches_even_if_it_exists(self):
        self.approve("notepad")
        self.assertIn("isn't an approved app", self.ask("open chrome"))
        self.assertEqual(self.launched, [])

    def test_ambiguous_names_are_refused(self):
        self.shortcut("Google Chrome Canary")
        self.assertIn("Several apps match", self.ask("allow app chrome"))
        self.assertEqual(self.apps.approved(), [])

    def test_unknown_app(self):
        self.assertIn("couldn't find an app called",
                      self.ask("allow app nonexistent thing"))

    def test_already_approved(self):
        self.approve("notepad")
        self.assertIn("already approved", self.ask("allow app notepad"))

    def test_app_that_vanished_is_not_launched(self):
        self.approve("notepad")
        os.unlink(self.notepad)
        answer = self.ask("open notepad")
        self.assertIn("no longer at", answer)
        self.assertEqual(self.launched, [])

    def test_a_file_swapped_for_a_script_after_approval_is_refused(self):
        self.approve("notepad")
        records = self.apps.store.get("allowed_apps")
        records["notepad"]["path"] = self.program("notepad.bat")
        self.apps.store.set("allowed_apps", records)
        self.assertIn("isn't a program or shortcut", self.ask("open notepad"))
        self.assertEqual(self.launched, [])

    def test_launch_failure_is_reported_and_logged(self):
        def failing(path):
            raise OSError(2, "No such file")
        self.assistant.env.launcher = failing
        self.approve("notepad")
        self.ask("open notepad")
        self.assertIn("couldn't open Notepad", self.ask("yes"))
        self.assertIn("failed", self.ask("show action log"))

    def test_revoking_takes_effect_immediately(self):
        self.approve("notepad")
        self.assertIn("no longer open", self.ask("remove allowed app notepad"))
        self.assertIn("isn't an approved app", self.ask("open notepad"))

    def test_undo_of_an_approval_revokes_it(self):
        self.approve("notepad")
        self.assertIn("Undoing: Approved: Notepad", self.ask("undo"))
        self.assertEqual(self.apps.approved(), [])

    def test_show_allowed_apps(self):
        self.assertIn("haven't been approved", self.ask("show allowed apps"))
        self.approve("notepad")
        listing = self.ask("show allowed apps")
        self.assertIn("Notepad", listing)
        self.assertIn(self.notepad, listing)

    def test_the_audit_log_records_every_step(self):
        self.approve("notepad")
        self.ask("open notepad")
        self.ask("yes")
        log = self.ask("show action log")
        for word in ("allow_app (L2)", "open_app (L2)", "confirmed",
                     "succeeded"):
            self.assertIn(word, log)

    def test_a_disabled_action_engine_blocks_launching(self):
        self.approve("notepad")
        self.assistant.features.set_enabled("action_engine", False)
        self.assertIn("Actions are turned off", self.ask("open notepad"))
        self.assertEqual(self.launched, [])


if __name__ == "__main__":
    unittest.main()
