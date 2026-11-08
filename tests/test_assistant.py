import tempfile
import unittest
from pathlib import Path

from core.assistant import Assistant
from core.memory import Memory


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.memory = Memory(str(Path(self.tmp.name) / "test.db"))
        self.assistant = Assistant(self.memory)

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def ask(self, text):
        return self.assistant.respond(text)

    def test_remember_and_recall_topic(self):
        self.assertIn("saved", self.ask("Remember that I prefer Python."))
        answer = self.ask("What do you remember about Python?")
        self.assertIn("I prefer Python.", answer)

    def test_recall_all_with_and_without_question_mark(self):
        self.ask("Remember that I prefer Python.")
        for phrase in ("What do you remember about me?",
                       "What do you remember about me",
                       "What do you remember?",
                       "show my memories"):
            with self.subTest(phrase=phrase):
                answer = self.ask(phrase)
                self.assertIn("Here is what I remember", answer)
                self.assertIn("I prefer Python.", answer)

    def test_recalling_is_not_a_recall_command(self):
        answer = self.ask("Recalling last week's meeting")
        self.assertNotIn("related to", answer)

    def test_listing_is_not_capped_at_five(self):
        for number in range(1, 9):
            self.ask(f"Remember that fact number {number}")
        answer = self.ask("What do you remember?")
        listed = [line for line in answer.splitlines() if line.startswith("#")]
        self.assertEqual(len(listed), 8)

    def test_forget_by_id(self):
        self.ask("Remember that I like tea")
        listing = self.ask("What do you remember?")
        memory_id = int(listing.split("#")[1].split()[0])

        self.assertIn("forgotten", self.ask(f"forget #{memory_id}"))
        self.assertIn("don't have any saved",
                      self.ask("What do you remember?"))

    def test_forget_unknown_id(self):
        self.assertIn("don't have an active memory",
                      self.ask("forget #42"))

    def test_memory_questions_are_not_hijacked_by_greeting(self):
        self.ask("Remember that I prefer Python for development.")
        answer = self.ask("Which language do I prefer?")
        self.assertFalse(answer.startswith("Hello"))

    def test_conversation_is_saved(self):
        self.ask("hello")
        roles = [role for role, _, _ in self.memory.recent()]
        self.assertEqual(roles, ["user", "assistant"])

    def test_empty_message(self):
        self.assertEqual(self.ask("   "), "Please enter a message.")


if __name__ == "__main__":
    unittest.main()
