import unittest

from core.intent import Intent, INTENT_NAMES, parse_intent


class ParseIntentTests(unittest.TestCase):
    def test_returns_intent_object(self):
        intent = parse_intent("hello")
        self.assertIsInstance(intent, Intent)
        self.assertEqual(intent.name, "greeting")
        self.assertEqual(intent.raw, "hello")
        self.assertEqual(intent.confidence, 1.0)

    def test_unknown_has_zero_confidence(self):
        intent = parse_intent("what is the weather like")
        self.assertEqual(intent.name, "unknown")
        self.assertEqual(intent.confidence, 0.0)

    def test_remember_extracts_fact_and_keeps_punctuation(self):
        intent = parse_intent("Remember that I prefer Python.")
        self.assertEqual(intent.name, "remember")
        self.assertEqual(intent.entities, {"fact": "I prefer Python."})

    def test_dont_forget_is_remember_not_forget(self):
        intent = parse_intent("Don't forget that I like tea")
        self.assertEqual(intent.name, "remember")
        self.assertEqual(intent.entities["fact"], "I like tea")

    def test_forget_by_id(self):
        for text in ("forget #3", "Forget memory 3", "forget 3."):
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, "forget")
                self.assertEqual(intent.entities, {"id": 3})

    def test_forget_by_reference(self):
        for text in ("forget that", "Forget it", "forget that one!",
                     "forget the last one"):
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, "forget")
                self.assertEqual(intent.entities, {"reference": "last"})

    def test_recall_topic_and_all(self):
        intent = parse_intent("What do you remember about Python?")
        self.assertEqual(intent.name, "recall")
        self.assertEqual(intent.entities, {"query": "Python"})

        for text in ("What do you remember?", "what do you remember about me",
                     "show my memories"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "recall_all")

    def test_recalling_is_not_recall(self):
        self.assertEqual(parse_intent("Recalling last week's meeting").name,
                         "unknown")

    def test_names_are_all_declared(self):
        samples = ["hello", "what time is it", "what day is it", "help",
                   "remember that x", "what do you remember", "recall x",
                   "forget #1", "zzz"]
        for text in samples:
            self.assertIn(parse_intent(text).name, INTENT_NAMES)


if __name__ == "__main__":
    unittest.main()
