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


class TaskTests(CoreTestCase):
    def test_remember_to_is_saved_as_a_task_without_pretending_to_remind(self):
        answer = self.ask("Remember to call my supervisor on Monday")
        self.assertIn("noted this as a task", answer)
        self.assertIn("can't set reminders", answer)
        record = self.memory.lookup("call my supervisor on Monday")
        self.assertEqual(record.category, "task")

    def test_tasks_are_listed_separately_from_facts(self):
        self.ask("Remember to call my supervisor on Monday")
        self.ask("Remember that I prefer Python")
        tasks = self.ask("show my tasks")
        self.assertIn("call my supervisor", tasks)
        self.assertNotIn("Python", tasks)
        prefs = self.ask("show my preferences")
        self.assertIn("Python", prefs)
        self.assertNotIn("supervisor", prefs)

    def test_empty_category_message(self):
        self.assertIn("don't have any saved tasks", self.ask("show my tasks"))

    def test_listing_tags_non_general_categories(self):
        self.ask("Remember to call my supervisor on Monday")
        self.assertIn("[task]", self.ask("what do you remember?"))

    def test_forget_that_works_on_a_task(self):
        self.ask("Remember to call my supervisor on Monday")
        self.assertIn("forgotten", self.ask("forget that"))
        self.assertEqual(self.active_facts(), [])


class ReplaceConfirmationTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.ask("Remember that I prefer Python for development")

    def test_similar_fact_asks_before_saving(self):
        answer = self.ask("Remember that I prefer Rust for development")
        self.assertIn("similar memory", answer)
        self.assertIn("I prefer Python for development", answer)
        # nothing has changed yet
        self.assertEqual(self.active_facts(),
                         ["I prefer Python for development"])

    def test_yes_replaces_and_keeps_history(self):
        self.ask("Remember that I prefer Rust for development")
        answer = self.ask("yes")
        self.assertIn("replaces", answer)
        self.assertEqual(self.active_facts(),
                         ["I prefer Rust for development"])
        self.assertEqual(
            self.memory.lookup("I prefer Python for development").status,
            "superseded")

    def test_keep_both_saves_both(self):
        self.ask("Remember that I prefer Rust for development")
        self.assertIn("alongside", self.ask("keep both"))
        self.assertEqual(len(self.active_facts()), 2)

    def test_no_discards_the_new_fact(self):
        self.ask("Remember that I prefer Rust for development")
        self.assertIn("haven't saved", self.ask("no"))
        self.assertEqual(self.active_facts(),
                         ["I prefer Python for development"])
        self.assertIsNone(self.memory.lookup("I prefer Rust for development"))

    def test_unanswered_question_expires(self):
        self.ask("Remember that I prefer Rust for development")
        self.ask("hello")                               # ignores the question
        self.assertIn("nothing waiting", self.ask("yes"))
        self.assertEqual(self.active_facts(),
                         ["I prefer Python for development"])

    def test_yes_with_nothing_pending(self):
        self.assertIn("nothing waiting", self.ask("yes"))
        self.assertIn("nothing waiting", self.ask("keep both"))

    def test_keep_both_after_the_question_was_already_answered(self):
        self.ask("Remember that I prefer Rust for development")
        self.ask("yes")
        self.assertIn("nothing waiting", self.ask("keep both"))

    def test_answer_is_consumed_once(self):
        self.ask("Remember that I prefer Rust for development")
        self.ask("yes")
        self.assertIn("nothing waiting", self.ask("yes"))

    def test_exact_duplicate_is_reported_not_resaved(self):
        answer = self.ask("remember that i PREFER python for development")
        self.assertIn("already remember", answer)
        self.assertEqual(len(self.active_facts()), 1)

    def test_unrelated_fact_saves_without_a_question(self):
        answer = self.ask("Remember that the lab opens at nine")
        self.assertIn("saved this to long-term memory", answer)
        self.assertEqual(len(self.active_facts()), 2)

    def test_source_message_links_to_the_chat(self):
        self.ask("Remember that I prefer Rust for development")
        self.ask("yes")
        row = self.memory.connection.execute(
            """SELECT m.source_message_id, g.content
               FROM memories m JOIN messages g ON g.id = m.source_message_id
               WHERE m.fact = 'I prefer Rust for development'""").fetchone()
        self.assertEqual(row["content"],
                         "Remember that I prefer Rust for development")


class RelatedAndFallbackTests(CoreTestCase):
    def test_related_command(self):
        self.ask("Remember that Python is my favourite language")
        self.ask("Remember that I write Python every day")
        self.ask("Remember that the lab opens at nine")
        first = self.memory.lookup("Python is my favourite language").id
        answer = self.ask(f"what's related to #{first}")
        self.assertIn("I write Python every day", answer)
        self.assertNotIn("lab opens", answer)

    def test_related_unknown_id(self):
        self.assertIn("don't have an active memory", self.ask("related to #9"))

    def test_fallback_is_quiet_when_only_filler_words_overlap(self):
        self.ask("Remember that I like tea")
        answer = self.ask("What is the weather like?")
        self.assertIn("don't have a response", answer)

    def test_fallback_surfaces_a_relevant_fact(self):
        self.ask("Remember that my supervisor is Dr Mensah")
        answer = self.ask("Who is my supervisor?")
        self.assertIn("Dr Mensah", answer)

    def test_showing_a_memory_records_last_used(self):
        self.ask("Remember that I like tea")
        self.ask("what do you remember?")
        row = self.memory.connection.execute(
            "SELECT last_used_at FROM memories").fetchone()
        self.assertIsNotNone(row["last_used_at"])
