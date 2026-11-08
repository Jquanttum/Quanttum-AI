import unittest

from core.intent import detect_intent


class IntentTests(unittest.TestCase):
    def check(self, expected, *messages):
        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(detect_intent(message), expected)

    def test_greetings(self):
        self.check("greeting", "hello", "Hi", "hey there", "Good morning!",
                   "Hello Quanttum AI")

    def test_substring_false_positives_are_not_greetings(self):
        # "hi" used to match inside these words.
        self.check("unknown", "This is a test", "machine learning",
                   "Tell me something", "Which language do I prefer?")

    def test_time(self):
        self.check("time", "What time is it?", "what's the time",
                   "What\u2019s the time?", "time", "Tell me the current time")

    def test_date(self):
        self.check("date", "What's the date?", "what day is it today",
                   "Today's date", "date")

    def test_date_substring_false_positives(self):
        # "date" used to match inside "update"; "today" anywhere matched.
        self.check("unknown", "update my notes",
                   "I want to learn Python today", "sometimes I code")

    def test_long_sentences_are_not_clock_requests(self):
        self.check("unknown",
                   "by the time I get home I will call my supervisor")

    def test_time_beats_greeting(self):
        self.check("time", "Hi, what time is it?")

    def test_help(self):
        self.check("help", "help", "What can you do?", "commands")


if __name__ == "__main__":
    unittest.main()
