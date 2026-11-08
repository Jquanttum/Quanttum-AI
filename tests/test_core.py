import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core.assistant import Assistant
from core.handlers import build_registry
from core.intent import INTENT_NAMES
from core.memory import Memory


class CoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.memory = Memory(str(Path(self.tmp.name) / "test.db"))
        self.clock = lambda: datetime(2026, 3, 14, 15, 9)
        self.assistant = Assistant(self.memory, clock=self.clock)

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def ask(self, text):
        return self.assistant.respond(text)

    def active_facts(self):
        return self.memory.search(limit=100)


class RegistryTests(CoreTestCase):
    def test_every_intent_has_a_handler_and_no_extras(self):
        self.assertEqual(set(build_registry()), set(INTENT_NAMES))


class ClockTests(CoreTestCase):
    def test_time_and_date_use_the_injected_clock(self):
        self.assertIn("03:09 PM", self.ask("what time is it"))
        self.assertIn("Saturday, 14 March 2026", self.ask("what's the date"))


class FollowUpTests(CoreTestCase):
    def test_forget_that_after_remember(self):
        self.ask("Remember that I like tea")
        self.ask("Remember that I prefer Python")
        answer = self.ask("forget that")
        self.assertIn("forgotten", answer)
        self.assertEqual(self.active_facts(), ["I like tea"])

    def test_forget_that_after_a_single_result_listing(self):
        self.ask("Remember that I like tea")
        self.ask("Remember that I prefer Python")
        self.ask("What do you remember about tea?")
        self.assertIn("forgotten", self.ask("forget that"))
        self.assertEqual(self.active_facts(), ["I prefer Python"])

    def test_forget_that_after_a_long_listing_asks_instead_of_guessing(self):
        self.ask("Remember that I like tea")
        self.ask("Remember that I prefer Python")
        self.ask("What do you remember?")
        answer = self.ask("forget that")
        self.assertIn("several memories", answer)
        self.assertEqual(len(self.active_facts()), 2)

    def test_forget_that_with_no_context(self):
        self.ask("Remember that I like tea")
        self.ask("hello")                      # unrelated turn in between
        answer = self.ask("forget that")
        self.assertIn("not sure which memory", answer)
        self.assertEqual(self.active_facts(), ["I like tea"])

    def test_reference_does_not_survive_a_forget(self):
        self.ask("Remember that I like tea")
        self.ask("forget that")
        answer = self.ask("forget that")
        self.assertIn("not sure which memory", answer)

    def test_explicit_id_still_works_after_unrelated_turns(self):
        self.ask("Remember that I like tea")
        memory_id = self.memory.search_records()[0][0]
        self.ask("hello")
        self.assertIn("forgotten", self.ask(f"forget #{memory_id}"))


class FailureTests(CoreTestCase):
    def test_a_crashing_handler_does_not_crash_the_core(self):
        def broken(intent, env):
            raise ValueError("boom")

        self.assistant.handlers["greeting"] = broken

        with self.assertLogs("core.assistant", level="ERROR"):
            answer = self.ask("hello")

        self.assertIn("Something went wrong", answer)
        roles = [role for role, _, _ in self.memory.recent()]
        self.assertEqual(roles, ["user", "assistant"])

    def test_unregistered_intent_falls_back_to_unknown(self):
        del self.assistant.handlers["help"]
        self.assertIn("don't have a response", self.ask("help"))


class MemoryReturnTests(CoreTestCase):
    def test_remember_returns_id_and_is_stable_for_duplicates(self):
        first = self.memory.remember("I like tea")
        again = self.memory.remember("i LIKE tea")
        self.assertIsInstance(first, int)
        self.assertEqual(first, again)

    def test_remember_empty_returns_none(self):
        self.assertIsNone(self.memory.remember("   "))


if __name__ == "__main__":
    unittest.main()
