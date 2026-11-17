import os
import tempfile
import unittest
from pathlib import Path

from core.actions import (
    Action, ActionEngine, ActionError, ActionResult, Level,
)
from core.assistant import Assistant
from core.fileindex import FileIndex
from core.memory import Memory
from core.policy import FolderPolicy


class Probe(Action):
    """A test action whose side effects we can observe."""

    def __init__(self, name, level, fail=None, crash=False):
        self.name, self.level = name, level
        self.fail, self.crash = fail, crash
        self.ran = []
        self.valid = True

    def validate(self, params, env):
        if not self.valid:
            raise ActionError("no longer valid")
        if self.fail == "validate":
            raise ActionError("bad parameters")
        return params

    def describe(self, params):
        return f"About to run {self.name}"

    def authorisation_phrase(self, params):
        return f"confirm {self.name} {params.get('target', '')}".strip()

    def run(self, params, env):
        if self.crash:
            raise RuntimeError("kaboom")
        if self.fail == "run":
            raise ActionError("could not do it")
        self.ran.append(params)
        return ActionResult(f"{self.name} done",
                            undo={"action": "noop", "params": {}})


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.memory = Memory(str(Path(self.tmp.name) / "a.db"))
        self.assistant = Assistant(self.memory)
        self.engine = self.assistant.actions
        self.env = self.assistant.env
        self.l1 = Probe("probe_read", Level.READ_ONLY)
        self.l2 = Probe("probe_write", Level.REVERSIBLE)
        self.l3 = Probe("probe_wipe", Level.DESTRUCTIVE)
        for action in (self.l1, self.l2, self.l3):
            self.engine.register(action)

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def events(self, outcome_request=None):
        return [e.event for e in reversed(self.assistant.audit.recent(50))]

    def ask(self, text):
        return self.assistant.respond(text)


class LevelTests(EngineCase):
    def test_level_1_runs_immediately_and_is_logged(self):
        outcome = self.engine.request("probe_read", {"a": 1}, self.env)
        self.assertEqual((outcome.status, outcome.message),
                         ("done", "probe_read done"))
        self.assertEqual(self.l1.ran, [{"a": 1}])
        self.assertEqual(self.events(), ["requested", "succeeded"])

    def test_level_2_does_nothing_until_confirmed(self):
        outcome = self.engine.request("probe_write", {}, self.env)
        self.assertEqual(outcome.status, "needs_confirmation")
        self.assertIsNotNone(outcome.pending)
        self.assertEqual(self.l2.ran, [])
        self.assertEqual(self.events(),
                         ["requested", "confirmation_asked"])

    def test_level_2_yes_runs_it_and_records_undo(self):
        outcome = self.engine.request("probe_write", {}, self.env)
        message = outcome.pending.on_yes(self.env)
        self.assertEqual(message, "probe_write done")
        self.assertEqual(self.events(),
                         ["requested", "confirmation_asked", "confirmed",
                          "succeeded"])
        self.assertIsNotNone(self.assistant.audit.last_undoable())

    def test_level_2_no_cancels(self):
        outcome = self.engine.request("probe_write", {}, self.env)
        outcome.pending.on_no(self.env)
        self.assertEqual(self.l2.ran, [])
        self.assertEqual(self.events()[-1], "declined")

    def test_level_3_needs_the_exact_phrase_and_plain_yes_is_not_enough(self):
        self.ask("zzz")                      # any turn to start from
        outcome = self.engine.request("probe_wipe", {"target": "reports"},
                                      self.env)
        self.env.state.pending = outcome.pending
        self.assertEqual(outcome.pending.phrase, "confirm probe_wipe reports")

        # next turn: a plain yes re-arms instead of running
        answer = self.ask("yes")
        self.assertIn("isn't enough", answer)
        self.assertEqual(self.l3.ran, [])

        # next turn: a wrong phrase cancels
        answer = self.ask("confirm probe_wipe other")
        self.assertIn("cancelled", answer)
        self.assertEqual(self.l3.ran, [])
        self.assertIn("declined", self.events())

    def test_level_3_runs_with_the_exact_phrase(self):
        outcome = self.engine.request("probe_wipe", {"target": "reports"},
                                      self.env)
        self.env.state.pending = outcome.pending
        answer = self.ask("Confirm PROBE_WIPE   Reports.")
        self.assertEqual(answer, "probe_wipe done")
        self.assertEqual(len(self.l3.ran), 1)
        self.assertIn("confirmed", self.events())

    def test_authorise_with_nothing_pending_does_nothing(self):
        self.assertIn("nothing waiting", self.ask("confirm probe_wipe x"))
        self.assertEqual(self.l3.ran, [])


class RefusalTests(EngineCase):
    def test_unknown_action_is_denied(self):
        outcome = self.engine.request("format_c_drive", {}, self.env)
        self.assertEqual(outcome.status, "denied")
        self.assertEqual(self.events(), ["requested", "denied"])

    def test_disabled_engine_refuses_everything(self):
        self.assistant.features.set_enabled("action_engine", False)
        outcome = self.engine.request("probe_read", {}, self.env)
        self.assertEqual(outcome.status, "denied")
        self.assertIn("enable action engine", outcome.message)
        self.assertEqual(self.l1.ran, [])
        self.assertIn("denied", self.events())

    def test_invalid_parameters_are_denied_before_anything_happens(self):
        self.l2.fail = "validate"
        outcome = self.engine.request("probe_write", {}, self.env)
        self.assertEqual((outcome.status, outcome.message),
                         ("denied", "bad parameters"))
        self.assertIsNone(outcome.pending)

    def test_confirmed_action_is_rechecked_before_running(self):
        outcome = self.engine.request("probe_write", {}, self.env)
        self.l2.valid = False                 # the world changed meanwhile
        message = outcome.pending.on_yes(self.env)
        self.assertEqual(message, "no longer valid")
        self.assertEqual(self.l2.ran, [])
        self.assertIn("denied", self.events())

    def test_expected_failure_is_reported_and_logged(self):
        self.l1.fail = "run"
        outcome = self.engine.request("probe_read", {}, self.env)
        self.assertEqual((outcome.status, outcome.message),
                         ("failed", "could not do it"))
        self.assertEqual(self.events()[-1], "failed")

    def test_crash_is_contained_and_logged(self):
        self.l1.crash = True
        with self.assertLogs("core.actions.engine", level="ERROR"):
            outcome = self.engine.request("probe_read", {}, self.env)
        self.assertEqual(outcome.status, "failed")
        self.assertNotIn("kaboom", outcome.message)
        self.assertIn("failed", self.events())

    def test_duplicate_or_nameless_registration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.register(Probe("probe_read", Level.READ_ONLY))
        with self.assertRaises(ValueError):
            self.engine.register(object())


class ExpiryTests(EngineCase):
    def test_unanswered_question_is_logged_as_expired(self):
        outcome = self.engine.request("probe_write", {}, self.env)
        self.env.state.pending = outcome.pending
        self.ask("hello")                    # answers something else
        self.assertEqual(self.events()[-1], "expired")
        self.assertIn("nothing waiting", self.ask("yes"))
        self.assertEqual(self.l2.ran, [])

    def test_answered_question_is_not_also_expired(self):
        outcome = self.engine.request("probe_write", {}, self.env)
        self.env.state.pending = outcome.pending
        self.ask("yes")
        self.assertNotIn("expired", self.events())


class SandboxCase(unittest.TestCase):
    """A fake home with an allowed Documents folder, wired into Assistant."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.home = self.base / "home"
        self.docs = self.home / "Documents"
        self.docs.mkdir(parents=True)
        self.memory = Memory(str(self.base / "a.db"))
        self.policy = FolderPolicy(self.base / "perm.json",
                                   defaults=[self.docs], home=self.home,
                                   protected=[])
        self.index = FileIndex(self.base / "idx.db")
        self.assistant = Assistant(
            self.memory, policy=self.policy, index=self.index,
            memory_status=lambda: {"percent": 62, "total_gb": 3.83,
                                   "available_gb": 1.45})

    def tearDown(self):
        self.assistant.shutdown()
        self.memory.close()
        self.tmp.cleanup()

    def ask(self, text):
        return self.assistant.respond(text)

    def settle(self):
        self.index.wait_until_idle(10)


class CreateFolderTests(SandboxCase):
    def test_asks_first_then_creates_then_undo_removes(self):
        answer = self.ask("create folder Reports in Documents")
        self.assertIn("about to create", answer)
        self.assertFalse((self.docs / "Reports").exists())      # not yet

        self.assertIn("Created:", self.ask("yes"))
        self.assertTrue((self.docs / "Reports").is_dir())

        self.assertIn("Undoing: Created:", self.ask("undo"))
        self.assertTrue((self.docs / "Reports").exists())       # still asking
        self.assertIn("Removed:", self.ask("yes"))
        self.assertFalse((self.docs / "Reports").exists())
        self.assertIn("nothing to undo", self.ask("undo"))

    def test_no_cancels(self):
        self.ask("create folder Reports in Documents")
        self.assertIn("haven't done anything", self.ask("no"))
        self.assertFalse((self.docs / "Reports").exists())

    def test_full_path_and_missing_parents_are_created_and_undone(self):
        target = self.docs / "a" / "b" / "c"
        self.assertIn("plus 2 missing parent", self.ask(f"create folder {target}"))
        self.ask("yes")
        self.assertTrue(target.is_dir())
        self.ask("undo")
        self.ask("yes")
        self.assertFalse((self.docs / "a").exists())
        self.assertTrue(self.docs.is_dir())                     # root stays

    def test_outside_allowed_folders_is_refused_and_nothing_is_created(self):
        outside = self.base / "elsewhere" / "x"
        answer = self.ask(f"create folder {outside}")
        self.assertIn("isn't inside a folder I'm allowed", answer)
        self.assertFalse(outside.parent.exists())
        self.assertIn("nothing waiting", self.ask("yes"))

    def test_dotdot_escape_is_refused(self):
        answer = self.ask("create folder ../Sneaky in Documents")
        self.assertNotIn("about to create", answer)
        self.assertFalse((self.home / "Sneaky").exists())

    def test_bad_names_are_refused(self):
        for name in ("bad:name", "CON", "trailing.", 'quote"mark', "a|b"):
            with self.subTest(name=name):
                answer = self.ask(f"create folder {name} in Documents")
                self.assertNotIn("about to create", answer)
        self.assertEqual(list(self.docs.iterdir()), [])

    def test_existing_folder_is_refused(self):
        (self.docs / "Reports").mkdir()
        self.assertIn("already exists",
                      self.ask("create folder Reports in Documents"))

    def test_relative_name_without_a_location_asks_where(self):
        self.assertIn("Where should I create it",
                      self.ask("create folder Reports"))

    def test_undo_refuses_to_remove_a_folder_that_gained_files(self):
        self.ask("create folder Reports in Documents")
        self.ask("yes")
        (self.docs / "Reports" / "important.docx").write_text("precious")
        answer = self.ask("undo")
        self.assertIn("isn't empty", answer)
        self.assertTrue((self.docs / "Reports" / "important.docx").exists())

    def test_undo_state_changes_between_asking_and_yes(self):
        self.ask("create folder Reports in Documents")
        self.ask("yes")
        self.ask("undo")                       # asks
        (self.docs / "Reports" / "late.txt").write_text("x")
        answer = self.ask("yes")               # re-checked before removing
        self.assertIn("isn't empty", answer)
        self.assertTrue((self.docs / "Reports" / "late.txt").exists())

    def test_created_folder_is_found_straight_away(self):
        self.ask("create folder Quarterly Reports in Documents")
        self.ask("yes")
        self.settle()
        answer = self.ask("find folder quarterly")
        self.assertIn("Quarterly Reports", answer)
        self.assertNotIn("Live search", answer)

    def test_every_step_is_in_the_audit_log(self):
        self.ask("create folder Reports in Documents")
        self.ask("yes")
        log = self.ask("show action log")
        for word in ("create_folder (L2)", "confirmation_asked", "confirmed",
                     "succeeded"):
            self.assertIn(word, log)


class AllowFolderTests(SandboxCase):
    def test_allowing_needs_confirmation_and_can_be_undone(self):
        project = self.base / "work" / "Project"
        project.mkdir(parents=True)
        self.assertIn("give myself permission", self.ask(f"allow folder {project}"))
        self.assertNotIn(project, self.policy.roots)
        self.ask("yes")
        self.assertIn(project, self.policy.roots)
        self.ask("undo")                        # disallow is Level 1: runs
        self.assertNotIn(project, self.policy.roots)

    def test_new_allowed_folder_enables_creation_there(self):
        project = self.base / "work" / "Project"
        project.mkdir(parents=True)
        self.ask(f"allow folder {project}")
        self.ask("yes")
        self.ask(f"create folder Notes in {project}")
        self.assertIn("Created:", self.ask("yes"))

    def test_too_broad_and_missing_are_refused_without_asking(self):
        for text in (str(self.home), str(self.base),
                     str(self.base / "nope"), "relative"):
            with self.subTest(text=text):
                answer = self.ask(f"allow folder {text}")
                self.assertNotIn("give myself permission", answer)

    def test_disallow_takes_effect_immediately(self):
        self.assertIn("no longer have access",
                      self.ask(f"remove allowed folder {self.docs}"))
        self.assertEqual(self.policy.roots, [])
        self.assertIn("not allowed to use any folder",
                      self.ask("show allowed folders"))

    def test_show_allowed_folders_marks_defaults(self):
        answer = self.ask("show allowed folders")
        self.assertIn(str(self.docs), answer)
        self.assertIn("defaults", answer)


class FindFilesTests(SandboxCase):
    def setUp(self):
        super().setUp()
        (self.docs / "Exam Notes.docx").write_text("x" * 2048)
        (self.docs / "Thesis").mkdir()
        (self.docs / "Thesis" / "draft_v2.txt").write_text("hello")

    def test_live_search_works_before_any_index_exists(self):
        answer = self.ask("find exam")
        self.assertIn("Exam Notes.docx", answer)
        self.assertIn("2.0 KB", answer)
        self.assertIn("Live search", answer)

    def test_indexed_search_after_the_index_is_built(self):
        self.assistant.start_background_services()
        self.settle()
        answer = self.ask("locate exam notes")
        self.assertIn("Exam Notes.docx", answer)
        self.assertNotIn("Live search", answer)

    def test_files_and_folders(self):
        self.assertIn("Thesis", self.ask("find folder thesis"))
        self.assertIn("couldn't find", self.ask("find file thesis"))

    def test_deleted_files_are_dropped_even_if_the_index_is_stale(self):
        self.assistant.start_background_services()
        self.settle()
        (self.docs / "Exam Notes.docx").unlink()
        self.assertIn("couldn't find", self.ask("find exam"))

    def test_stale_index_entries_outside_the_allowed_folders_never_show(self):
        self.assistant.start_background_services()
        self.settle()
        self.policy.remove(str(self.docs))              # revoke, no reindex
        project = self.base / "other"
        project.mkdir()
        self.policy._write([str(project)])
        self.assertIn("couldn't find", self.ask("find exam"))

    def test_a_misbehaving_index_cannot_leak_files_outside_allowed_folders(self):
        from core.fileindex import FileHit
        secret = self.base / "outside-secret.txt"
        secret.write_text("private")

        class LyingIndex:
            indexing = False

            def status(self):
                return {"entries": 1, "indexing": False, "last_refresh": "",
                        "error": ""}

            def search(self, *args, **kwargs):
                return [FileHit(str(secret), secret.name, False, 7, 0.0)]

            def stop(self, *args):
                pass

        assistant = Assistant(self.memory, policy=self.policy,
                              index=LyingIndex())
        answer = assistant.respond("find secret")
        self.assertNotIn("outside-secret", answer)
        self.assertIn("couldn't find", answer)

    def test_no_allowed_folders(self):
        self.policy.remove(str(self.docs))
        self.assertIn("not allowed to look in any folder", self.ask("find exam"))

    def test_disabled_index_falls_back_to_live_search(self):
        self.assistant.start_background_services()
        self.settle()
        self.assistant.features.set_enabled("file_index", False)
        self.assertIn("Live search", self.ask("find exam"))
        self.assertIn("turned off", self.ask("rebuild index"))
        self.assertFalse(self.assistant.start_background_services())

    def test_index_status_and_rebuild(self):
        self.assertIn("hasn't completed", self.ask("index status"))
        self.assertIn("Updating the index", self.ask("rebuild index"))
        self.settle()
        status = self.ask("index status")
        self.assertIn("Entries: 3", status)
        self.assertIn("Last complete update", status)

    def test_long_queries_and_junk_are_handled(self):
        self.ask("find " + "x" * 300)
        self.ask("find %%%")
        self.ask('find "; DROP TABLE entries; --')


class SystemInfoTests(SandboxCase):
    def test_system_info(self):
        answer = self.ask("system info")
        self.assertIn("RAM: 62% used", answer)
        self.assertIn("1.45 GB free of 3.83 GB", answer)
        self.assertIn("Profile: lite", answer)

    def test_ram_unavailable_is_reported_not_faked(self):
        assistant = Assistant(self.memory, memory_status=lambda: None)
        self.assertIn("RAM: not available", assistant.respond("system info"))


if __name__ == "__main__":
    unittest.main()
