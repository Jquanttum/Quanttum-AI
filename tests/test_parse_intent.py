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

    def test_remember_to_is_a_task(self):
        intent = parse_intent("Remember to call my supervisor on Monday.")
        self.assertEqual(intent.name, "remember")
        self.assertEqual(intent.entities, {
            "fact": "call my supervisor on Monday.", "category": "task"})

    def test_remember_that_to_is_still_a_fact(self):
        intent = parse_intent("Remember that to win you must train")
        self.assertEqual(intent.name, "remember")
        self.assertNotIn("category", intent.entities)

    def test_curly_apostrophe_in_dont_forget(self):
        intent = parse_intent("Don\u2019t forget that I like tea")
        self.assertEqual(intent.name, "remember")

    def test_category_listing(self):
        cases = {"show my tasks": "task",
                 "What do you remember about my preferences?": "preference",
                 "list my to-dos": "task",
                 "show my facts": "general"}
        for text, category in cases.items():
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, "recall_category")
                self.assertEqual(intent.entities, {"category": category})

    def test_show_my_memories_is_still_recall_all(self):
        self.assertEqual(parse_intent("show my memories").name, "recall_all")

    def test_related(self):
        for text in ("related to #3", "What's related to 3?"):
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, "related")
                self.assertEqual(intent.entities, {"id": 3})

    def test_confirmation_answers(self):
        for text in ("yes", "Yes!", "replace it"):
            self.assertEqual(parse_intent(text).name, "confirm_yes")
        for text in ("no", "Nope.", "cancel"):
            self.assertEqual(parse_intent(text).name, "confirm_no")
        for text in ("keep both", "Both!"):
            self.assertEqual(parse_intent(text).name, "confirm_alt")

    def test_yes_inside_a_sentence_is_not_a_confirmation(self):
        self.assertEqual(parse_intent("yes I would like some tea").name,
                         "unknown")

    def test_show_features_and_profile(self):
        for text in ("show features", "Show my settings", "features", "list all toggles"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "show_features")
        for text in ("show profile", "what's my profile", "profile"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "show_profile")

    def test_set_profile(self):
        for text in ("use profile standard", "Use the full profile",
                     "switch to lite profile", "set profile to standard"):
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, "set_profile")
                self.assertIn(intent.entities["profile"],
                              ("lite", "standard", "full"))
        self.assertEqual(parse_intent("use profile ultra").name, "unknown")

    def test_enable_disable_reset(self):
        cases = {
            "enable ram monitor": ("ram monitor", True),
            "Disable the local model": ("the local model", False),
            "turn on voice input": ("voice input", True),
            "turn off ram": ("ram", False),
            "enable feature local_llm": ("local_llm", True),
            "reset feature ram": ("ram", None),
        }
        for text, (feature, enabled) in cases.items():
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, "set_feature")
                self.assertEqual(intent.entities,
                                 {"feature": feature, "enabled": enabled})

    def test_memory_commands_win_over_settings_commands(self):
        self.assertEqual(parse_intent("remember that I use profile lite").name,
                         "remember")

    def test_action_commands(self):
        simple = {"system info": "system_info", "Show system status": "system_info",
                  "show allowed folders": "show_allowed_folders",
                  "undo": "undo", "undo that": "undo", "undo the last action": "undo",
                  "index status": "index_status", "rebuild index": "rebuild_index",
                  "update the file index": "rebuild_index",
                  "show action log": "show_action_log"}
        for text, name in simple.items():
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, name)

    def test_find(self):
        cases = {
            "find exam notes": ("exam notes", "any"),
            "Find my thesis draft": ("thesis draft", "any"),
            "locate the file budget": ("budget", "file"),
            "find folder projects": ("projects", "folder"),
            "where is the folder reports?": ("reports", "folder"),
            "search for files invoice": ("invoice", "file"),
        }
        for text, (query, kind) in cases.items():
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, "find_files")
                self.assertEqual(intent.entities,
                                 {"query": query, "kind": kind})

    def test_memory_search_phrases_are_not_file_searches(self):
        self.assertEqual(parse_intent("find in my memories python").name, "recall")
        self.assertEqual(
            parse_intent("search my memories for python").name, "recall")

    def test_create_folder_splits_name_and_location(self):
        cases = {
            "create folder Reports in Documents": ("Reports", "Documents"),
            "make a new folder called Lab Notes inside Desktop": ("Lab Notes", "Desktop"),
            "create folder Reports": ("Reports", ""),
            'create folder "Work in progress" in Documents': ("Work in progress", "Documents"),
            "create folder D:\\Work in Progress\\New": ("D:\\Work in Progress\\New", ""),
            "create directory ~/Notes": ("~/Notes", ""),
        }
        for text, (name, location) in cases.items():
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, "create_folder")
                self.assertEqual(intent.entities,
                                 {"name": name, "location": location})

    def test_allow_and_disallow(self):
        intent = parse_intent("allow folder D:\\Projects\\Thesis")
        self.assertEqual((intent.name, intent.entities["path"]),
                         ("allow_folder", "D:\\Projects\\Thesis"))
        for text in ("remove allowed folder D:\\X", "disallow D:\\X",
                     "stop allowing folder D:\\X"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "disallow_folder")
        self.assertNotEqual(parse_intent("remove folder D:\\X").name,
                            "disallow_folder")

    def test_app_commands(self):
        cases = {
            "open notepad": ("open_app", "notepad"),
            "Launch Google Chrome": ("open_app", "Google Chrome"),
            "open the app calculator": ("open_app", "calculator"),
            "start the program paint": ("open_app", "paint"),
            "allow app Notepad": ("allow_app", "Notepad"),
            "allow application google chrome": ("allow_app", "google chrome"),
            "remove allowed app notepad": ("disallow_app", "notepad"),
            "disallow app notepad": ("disallow_app", "notepad"),
            "stop allowing program paint": ("disallow_app", "paint"),
        }
        for text, (name, app) in cases.items():
            with self.subTest(text=text):
                intent = parse_intent(text)
                self.assertEqual(intent.name, name)
                self.assertEqual(intent.entities, {"name": app})
        self.assertEqual(parse_intent("show allowed apps").name,
                         "show_allowed_apps")

    def test_open_file_and_folder_are_not_app_launches(self):
        for text in ("open file budget.xlsx", "open folder Reports",
                     "open the folder reports", "open directory x"):
            with self.subTest(text=text):
                self.assertNotEqual(parse_intent(text).name, "open_app")

    def test_app_and_folder_permission_commands_do_not_collide(self):
        self.assertEqual(parse_intent("disallow folder D:\\X").name,
                         "disallow_folder")
        self.assertEqual(parse_intent("disallow app notepad").name,
                         "disallow_app")
        self.assertEqual(parse_intent("allow folder D:\\X").name,
                         "allow_folder")

    def test_model_commands(self):
        for text in ("show model", "models", "llm status", "Ollama",
                     "list models", "ai model", "model status"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "llm_status")
        for text in ("benchmark", "benchmark model", "Benchmark the LLM"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "benchmark_model")
        intent = parse_intent("use model qwen2.5:3b")
        self.assertEqual((intent.name, intent.entities),
                         ("use_model", {"model": "qwen2.5:3b"}))
        intent = parse_intent("switch to model llama3.2")
        self.assertEqual(intent.entities, {"model": "llama3.2"})
        self.assertEqual(parse_intent("use default model").entities,
                         {"model": ""})
        self.assertEqual(parse_intent("use profile standard").name,
                         "set_profile")

    def test_voice_commands(self):
        for text in ("voice", "voice status", "show voice", "Voice settings"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "voice_status")
        for text in ("test microphone", "test mic", "check my microphone",
                     "mic test", "microphone test"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "voice_test")
        for text in ("stop speaking", "stop talking", "be quiet", "Quiet!"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text).name, "stop_speaking")
        intent = parse_intent('use voice model "D:\\My Models\\small"')
        self.assertEqual((intent.name, intent.entities["path"]),
                         ("use_voice_model", '"D:\\My Models\\small"'))
        self.assertEqual(parse_intent("use default voice model").entities,
                         {"path": ""})

    def test_voice_and_llm_model_commands_do_not_collide(self):
        self.assertEqual(parse_intent("use model qwen2.5:3b").name, "use_model")
        self.assertEqual(parse_intent("use voice model D:\\m").name,
                         "use_voice_model")

    def test_authorise_needs_more_than_the_word_confirm(self):
        self.assertEqual(parse_intent("confirm").name, "confirm_yes")
        intent = parse_intent("Confirm delete reports.")
        self.assertEqual(intent.name, "authorise")
        self.assertEqual(intent.entities["phrase"], "Confirm delete reports")

    def test_names_are_all_declared(self):
        samples = ["hello", "what time is it", "what day is it", "help",
                   "remember that x", "what do you remember", "recall x",
                   "forget #1", "show my tasks", "related to #1", "yes", "no",
                   "keep both", "show features", "show profile",
                   "enable ram", "use profile lite", "system info", "find x",
                   "create folder a in b", "undo", "allow folder x",
                   "show action log", "confirm foo bar", "open notepad",
                   "allow app x", "disallow app x", "show allowed apps", "show model",
                   "use model x", "benchmark", "voice status", "test mic",
                   "stop speaking", "use voice model x", "zzz"]
        for text in samples:
            self.assertIn(parse_intent(text).name, INTENT_NAMES)


if __name__ == "__main__":
    unittest.main()
