import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from core.assistant import Assistant
from core.features import FeatureRegistry
from core.llm import ModelInfo, OllamaClient
from core.llm_brain import (
    LLM_COMMANDS, LLMBrain, build_chat_messages, choose_model, parse_command,
    validate_command,
)
from core.memory import Memory
from core.policy import FolderPolicy
from tests.fake_ollama import FakeOllama

GB = 1_000_000_000


def models(*pairs):
    return [ModelInfo(name, int(size * GB)) for name, size in pairs]


class ChooseModelTests(unittest.TestCase):
    INSTALLED = models(("qwen2.5:7b", 4.7), ("llama3.2:3b", 2.0),
                       ("phi3.5:latest", 2.2), ("nomic-embed-text", 0.3))

    def test_preferences_follow_the_profile(self):
        self.assertEqual(choose_model(self.INSTALLED, "standard", 15.4),
                         "llama3.2:3b")
        self.assertEqual(choose_model(self.INSTALLED, "full", 15.4),
                         "qwen2.5:7b")

    def test_your_choice_wins_if_installed(self):
        self.assertEqual(
            choose_model(self.INSTALLED, "standard", 15.4, "qwen2.5:7b"),
            "qwen2.5:7b")
        self.assertEqual(
            choose_model(self.INSTALLED, "standard", 15.4, "QWEN2.5:7B"),
            "qwen2.5:7b")

    def test_a_chosen_model_that_is_gone_falls_back_to_automatic(self):
        self.assertEqual(
            choose_model(self.INSTALLED, "standard", 15.4, "deleted:1b"),
            "llama3.2:3b")

    def test_models_too_big_for_the_ram_are_skipped(self):
        # 7B weights (4.7 GB) don't fit in 45% of 8 GB
        self.assertEqual(choose_model(self.INSTALLED, "full", 8.0),
                         "llama3.2:3b")

    def test_embedding_models_are_never_chosen(self):
        only = models(("nomic-embed-text", 0.3))
        self.assertIsNone(choose_model(only, "standard", 15.4))

    def test_falls_back_to_the_smallest_usable_model(self):
        odd = models(("mystery:8b", 4.9), ("tiny:1b", 0.7))
        self.assertEqual(choose_model(odd, "standard", 15.4), "tiny:1b")

    def test_nothing_installed_or_nothing_fits(self):
        self.assertIsNone(choose_model([], "standard", 15.4))
        self.assertIsNone(choose_model(models(("big:70b", 40)), "full", 15.4))

    def test_unknown_ram_does_not_block(self):
        self.assertEqual(choose_model(self.INSTALLED, "full", None),
                         "qwen2.5:7b")


class ValidateCommandTests(unittest.TestCase):
    def check(self, name, entities, text):
        return validate_command(name, entities, text)

    def test_accepts_grounded_commands(self):
        intent = self.check("find_files", {"query": "exam notes", "kind": "any"},
                            "can you dig up my exam notes please")
        self.assertEqual((intent.name, intent.entities),
                         ("find_files", {"query": "exam notes", "kind": "any"}))
        self.assertEqual(intent.confidence, 0.6)
        self.assertEqual(intent.raw, "can you dig up my exam notes please")

    def test_enum_default_and_optional_text(self):
        intent = self.check("find_files", {"query": "budget"}, "find budget")
        self.assertEqual(intent.entities["kind"], "any")
        intent = self.check("create_folder", {"name": "Reports"},
                            "make a Reports folder")
        self.assertEqual(intent.entities, {"name": "Reports", "location": ""})

    def test_commands_without_arguments(self):
        for name in ("time", "date", "system_info", "recall_all", "help"):
            self.assertEqual(self.check(name, {}, "x").name, name)
        self.assertIsNotNone(self.check("time", None, "x")
                             if False else self.check("time", {}, "x"))

    def test_invented_text_is_rejected(self):
        self.assertIsNone(self.check("find_files", {"query": "passwords"},
                                     "find my exam notes"))
        self.assertIsNone(self.check("open_app", {"name": "powershell"},
                                     "open my text editor"))
        self.assertIsNone(self.check("remember", {"fact": "I love Rust"},
                                     "I like Python"))

    def test_partial_overlap_is_still_rejected(self):
        self.assertIsNone(self.check("find_files", {"query": "exam secrets"},
                                     "find exam notes"))

    def test_missing_required_or_unexpected_fields_are_rejected(self):
        self.assertIsNone(self.check("find_files", {}, "find things"))
        self.assertIsNone(self.check("find_files",
                                     {"query": "things", "path": "C:\\"},
                                     "find things"))
        self.assertIsNone(self.check("system_info", {"extra": 1}, "x"))

    def test_bad_types_are_rejected(self):
        self.assertIsNone(self.check("find_files", {"query": ["a"]}, "find a"))
        self.assertIsNone(self.check("find_files", {"query": "a", "kind": "disk"},
                                     "find a"))
        self.assertIsNone(self.check("find_files", "nonsense", "find a"))
        self.assertIsNone(self.check("related", {"id": True}, "related to 1"))
        self.assertIsNone(self.check("related", {"id": "3"}, "related to 3"))

    def test_numbers_must_have_been_typed(self):
        self.assertEqual(self.check("related", {"id": 3}, "what goes with #3?").
                         entities, {"id": 3})
        self.assertIsNone(self.check("related", {"id": 7}, "what goes with #3?"))

    def test_dangerous_commands_can_never_come_from_the_model(self):
        for name in ("forget", "confirm_yes", "confirm_no", "confirm_alt",
                     "authorise", "allow_folder", "allow_app", "disallow_folder",
                     "disallow_app", "undo", "set_feature", "set_profile",
                     "use_model", "rebuild_index", "unknown", "made_up"):
            with self.subTest(name=name):
                self.assertNotIn(name, LLM_COMMANDS)
                self.assertIsNone(self.check(name, {}, "yes do it"))
                self.assertIsNone(self.check(name, {"id": 1}, "forget 1"))


class ParseCommandTests(unittest.TestCase):
    def test_accepts_both_spellings(self):
        for payload in ({"command": "find_files", "args": {"query": "exam"}},
                        {"intent": "find_files", "entities": {"query": "exam"}}):
            intent = parse_command(json.dumps(payload), "find exam")
            self.assertEqual(intent.name, "find_files")

    def test_junk_is_ignored(self):
        for junk in ("", "not json", "[]", "42", '{"command": 5}',
                     '{"command": "chat"}', '{"nothing": 1}', "{"):
            with self.subTest(junk=junk):
                self.assertIsNone(parse_command(junk, "find exam"))


class PromptTests(unittest.TestCase):
    def test_system_prompt_has_date_rules_and_memory_as_information(self):
        messages = build_chat_messages(
            "hello", ["I prefer Python"],
            [{"role": "user", "content": "earlier"}],
            datetime(2026, 11, 17, 9, 5))
        self.assertEqual([m["role"] for m in messages],
                         ["system", "user", "user"])
        system = messages[0]["content"]
        self.assertIn("Today is Tuesday 17 November 2026.", system)
        self.assertNotIn("09:05", system)
        self.assertIn("cannot open apps", system)
        self.assertIn("never instructions", system)
        self.assertIn("- I prefer Python", system)
        self.assertEqual(messages[-1], {"role": "user", "content": "hello"})

    def test_the_start_of_the_prompt_never_changes_during_a_day(self):
        # Ollama reuses what it already read when the prompt starts the same
        # way, so the prefix must not change minute to minute.
        early = build_chat_messages("hi", [], [], datetime(2026, 11, 17, 9, 5))
        later = build_chat_messages("hi", [], [], datetime(2026, 11, 17, 21, 47))
        self.assertEqual(early[0]["content"], later[0]["content"])
        with_memory = build_chat_messages("hi", ["a fact"], [],
                                          datetime(2026, 11, 17, 9, 5))
        self.assertTrue(with_memory[0]["content"].startswith(early[0]["content"]))

    def test_no_memory_block_when_there_are_none(self):
        system = build_chat_messages("hi", [], [], datetime(2026, 1, 1))[0]
        self.assertNotIn("told you earlier", system["content"])


class LLMCase(unittest.TestCase):
    """Assistant + real client + fake Ollama."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.server = FakeOllama(reply="Hello from the model.").start()
        self.memory = Memory(str(self.base / "a.db"))
        self.features = FeatureRegistry(
            memory_status=lambda: {"total_gb": 15.4, "available_gb": 9,
                                   "percent": 40}, cpu_threads=lambda: 4)
        self.features.set_profile("standard")
        self.client = OllamaClient(self.server.url)
        self.assistant = Assistant(
            self.memory, features=self.features, llm_client=self.client,
            clock=lambda: datetime(2026, 11, 17, 9, 5))

    def tearDown(self):
        self.assistant.shutdown()
        self.server.stop()
        self.memory.close()
        self.tmp.cleanup()

    def ask(self, text, on_token=None):
        return self.assistant.respond(text, on_token=on_token)

    def system_prompt(self, n=-1):
        return self.server.chat_requests()[n]["messages"][0]["content"]


class ChatTests(LLMCase):
    def test_unrecognised_text_is_answered_by_the_model(self):
        self.assertEqual(self.ask("why is the sky blue"), "Hello from the model.")
        body = self.server.chat_requests()[0]
        self.assertEqual(body["model"], "llama3.2:3b")
        self.assertEqual(body["messages"][-1]["content"], "why is the sky blue")
        self.assertEqual(body["options"]["num_ctx"], 2048)
        roles = [r for r, _, _ in self.memory.recent()]
        self.assertEqual(roles, ["user", "assistant"])

    def test_commands_never_reach_the_model(self):
        self.ask("what time is it")
        self.ask("remember that I prefer Python")
        self.ask("show allowed folders")
        self.assertEqual(self.server.chat_requests(), [])

    def test_relevant_memories_become_context_but_unrelated_ones_do_not(self):
        self.ask("remember that my supervisor is Dr Mensah")
        self.ask("remember that I like tea")
        self.ask("who is my supervisor")
        system = self.system_prompt()
        self.assertIn("Dr Mensah", system)
        self.assertNotIn("tea", system)

    def test_recent_conversation_is_included_without_duplicating_this_turn(self):
        self.ask("first question here")
        self.ask("second question here")
        messages = self.server.chat_requests()[-1]["messages"]
        contents = [m["content"] for m in messages[1:]]
        self.assertEqual(contents.count("second question here"), 1)
        self.assertIn("first question here", contents)
        self.assertEqual(messages[-1]["content"], "second question here")

    def test_replies_stream_to_the_caller(self):
        self.server.reply = "one two three"
        seen = []
        answer = self.ask("tell me something", on_token=seen.append)
        self.assertEqual("".join(seen), "one two three")
        self.assertEqual(answer, "one two three")

    def test_streaming_callback_is_cleared_between_turns(self):
        seen = []
        self.ask("question one", on_token=seen.append)
        count = len(seen)
        self.ask("question two")                 # no callback this time
        self.assertEqual(len(seen), count)

    def test_model_down_falls_back_with_an_honest_note(self):
        self.server.stop()
        self.assistant.llm.snapshot(force=True)
        self.ask("remember that my supervisor is Dr Mensah")
        answer = self.ask("who is my supervisor")
        self.assertIn("local model isn't available", answer)
        self.assertIn("Dr Mensah", answer)       # old memory fallback

    def test_model_failure_mid_request_falls_back(self):
        self.server.fail = "404"
        answer = self.ask("what is a quasar")
        self.assertIn("local model isn't available", answer)
        self.assertIn("not found", answer)

    def test_feature_off_means_the_model_is_never_called(self):
        self.features.set_enabled("local_llm", False)
        answer = self.ask("what is a quasar")
        self.assertIn("don't have a response", answer)
        self.assertEqual(self.server.chat_requests(), [])

    def test_lite_profile_does_not_use_the_model_by_default(self):
        self.features.set_profile("lite")
        self.assertIn("don't have a response", self.ask("what is a quasar"))
        self.assertEqual(self.server.chat_requests(), [])

    def test_empty_model_reply_is_reported(self):
        self.server.reply = ""
        self.assertIn("empty reply", self.ask("say nothing"))

    def test_cancel_stops_a_long_reply(self):
        self.server.reply = " ".join(f"w{i}" for i in range(200))
        self.server.token_delay = 0.01
        seen = []

        def on_token(piece):
            seen.append(piece)
            if len(seen) == 3:
                self.assistant.cancel()

        answer = self.ask("write an essay", on_token=on_token)
        self.assertTrue(answer.endswith("[stopped]"))
        self.assertLess(len(seen), 40)
        self.server.token_delay = 0
        self.server.reply = "fine"
        self.assertEqual(self.ask("and now?"), "fine")      # next turn is ok

    def test_two_messages_at_once_are_handled_one_after_another(self):
        self.server.token_delay = 0.01
        results = []

        def worker(text):
            results.append(self.ask(text))

        threads = [threading.Thread(target=worker, args=(f"question {i}",))
                   for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(len(results), 3)
        roles = [r for r, _, _ in self.memory.recent(20)]
        self.assertEqual(roles, ["user", "assistant"] * 3)

    def test_warm_up_loads_the_model_in_the_background(self):
        self.assistant.start_background_services()
        deadline = time.time() + 5
        while time.time() < deadline and not self.server.chat_requests():
            time.sleep(0.02)
        body = self.server.chat_requests()[0]
        self.assertEqual(body["messages"], [])
        self.assertEqual(body["keep_alive"], "30m")


class CommandUnderstandingTests(LLMCase):
    def setUp(self):
        super().setUp()
        self.docs = self.base / "Documents"
        self.docs.mkdir()
        (self.docs / "Exam Notes.docx").write_text("x")
        self.assistant.policy = FolderPolicy(defaults=[self.docs],
                                             home=self.base, protected=[])
        self.assistant.env.policy = self.assistant.policy
        self.features.set_enabled("llm_commands", True)

    def route(self, command_json, chat="A chat reply."):
        def reply(body):
            return command_json if body.get("format") == "json" else chat
        self.server.reply = reply

    def test_off_by_default(self):
        self.features.set_enabled("llm_commands", False)
        self.route('{"command": "find_files", "args": {"query": "exam"}}')
        answer = self.ask("dig up my exam notes please")
        self.assertEqual(answer, "A chat reply.")
        self.assertFalse(any(b.get("format") == "json"
                             for b in self.server.chat_requests()))

    def test_a_sentence_becomes_a_find_command(self):
        self.route('{"command": "find_files", "args": {"query": "exam notes", '
                   '"kind": "any"}}')
        answer = self.ask("could you dig up my exam notes please")
        self.assertIn("Exam Notes.docx", answer)

    def test_commands_typed_normally_skip_the_model_entirely(self):
        self.route('{"command": "system_info", "args": {}}')
        self.ask("find exam")
        self.assertEqual(self.server.chat_requests(), [])

    def test_invented_arguments_fall_back_to_chat(self):
        self.route('{"command": "find_files", "args": {"query": "passwords"}}')
        self.assertEqual(self.ask("could you dig up my exam notes"),
                         "A chat reply.")

    def test_forbidden_commands_fall_back_to_chat(self):
        for command in ('{"command": "forget", "args": {"id": 1}}',
                        '{"command": "allow_folder", "args": {"path": "C:\\\\"}}',
                        '{"command": "undo", "args": {}}'):
            with self.subTest(command=command):
                self.route(command)
                self.assertEqual(self.ask("please sort that out for me 1"),
                                 "A chat reply.")

    def test_the_model_can_never_answer_a_confirmation_for_you(self):
        self.ask("create folder Reports in Documents")   # now waiting for yes
        self.route('{"command": "confirm_yes", "args": {}}')
        answer = self.ask("sure go right ahead")
        self.assertEqual(answer, "A chat reply.")
        self.assertFalse((self.docs / "Reports").exists())

    def test_gated_commands_still_ask_first(self):
        self.route('{"command": "create_folder", "args": {"name": "Reports", '
                   '"location": "Documents"}}')
        answer = self.ask("could you make me a Reports folder in Documents")
        self.assertIn("about to create", answer)
        self.assertFalse((self.docs / "Reports").exists())

    def test_garbage_and_model_errors_fall_back_to_chat(self):
        self.route("this is not json")
        self.assertEqual(self.ask("tell me a story"), "A chat reply.")

    def test_needs_the_local_model_feature_too(self):
        self.features.set_enabled("local_llm", False)
        self.route('{"command": "system_info", "args": {}}')
        self.assertIn("don't have a response", self.ask("how is my machine"))


class ModelCommandTests(LLMCase):
    def setUp(self):
        super().setUp()
        self.server.models = [
            {"name": "llama3.2:3b", "size": 2 * GB},
            {"name": "qwen2.5:7b", "size": int(4.7 * GB)},
            {"name": "qwen2.5:3b", "size": 2 * GB},
        ]

    def test_show_model_lists_installed_and_marks_the_one_in_use(self):
        answer = self.ask("show model")
        self.assertIn("Ollama: running (version 0.5.0)", answer)
        self.assertIn("- llama3.2:3b (2.0 GB)  <- in use", answer)
        self.assertIn("- qwen2.5:7b (4.7 GB)", answer)
        self.assertIn("chose this automatically", answer)

    def test_status_when_ollama_is_down_or_empty(self):
        self.server.models = []
        self.assertIn("No models are installed", self.ask("llm status"))
        self.assertIn("ollama pull llama3.2:3b", self.ask("llm status"))
        self.server.stop()
        self.assertIn("not running", self.ask("llm status"))

    def test_use_model_by_full_or_partial_name_and_persists(self):
        self.assertIn("Now using qwen2.5:7b", self.ask("use model qwen2.5:7b"))
        self.assertEqual(self.features.option("llm_model"), "qwen2.5:7b")
        self.ask("tell me about the moon")
        self.assertEqual(self.server.chat_requests()[-1]["model"], "qwen2.5:7b")
        self.assertIn("You chose this model", self.ask("show model"))
        self.assertIn("Now using llama3.2:3b", self.ask("use model llama3.2"))

    def test_ambiguous_or_unknown_model_is_refused_without_changes(self):
        answer = self.ask("use model qwen")
        self.assertIn("exactly one installed model", answer)
        self.assertIsNone(self.features.option("llm_model"))
        self.assertIn("ollama pull", self.ask("use model mistral"))

    def test_use_default_model_clears_the_choice(self):
        self.ask("use model qwen2.5:7b")
        self.assertIn("automatically", self.ask("use default model"))
        self.assertIsNone(self.features.option("llm_model"))

    def test_big_model_on_small_ram_is_warned_about(self):
        self.features._memory_status = lambda: {"total_gb": 8.0,
                                                "available_gb": 4,
                                                "percent": 50}
        self.assertIn("large for this PC", self.ask("use model qwen2.5:7b"))

    def test_benchmark_reports_speed_and_a_verdict(self):
        answer = self.ask("benchmark")
        self.assertIn("Model: llama3.2:3b", answer)
        self.assertIn("Writing speed: 10.0 tokens/second", answer)
        self.assertIn("Reading speed: 50.0 tokens/second", answer)
        self.assertIn("about 6 s before the first word", answer)
        self.assertIn("Comfortable", answer)

    def test_models_stay_loaded_longer_on_bigger_profiles(self):
        self.ask("tell me about the moon")
        self.assertEqual(self.server.chat_requests()[-1]["keep_alive"], "30m")
        self.features.set_profile("full")
        self.ask("tell me about mars")
        self.assertEqual(self.server.chat_requests()[-1]["keep_alive"], "60m")

    def test_benchmark_caps_replies_to_what_this_pc_can_write_in_time(self):
        self.server.reply = " ".join(["w"] * 24)       # 24 tokens at 10 tok/s
        self.assertEqual(self.assistant.llm.chat_options()["num_predict"], 400)
        # pretend this PC only manages 5 tokens/second
        self.features.set_option("llm_speed_tps", 5.0)
        self.features.set_option("llm_speed_model", "llama3.2:3b")
        self.assertEqual(self.assistant.llm.chat_options()["num_predict"], 225)
        self.ask("tell me about the moon")
        self.assertEqual(self.server.chat_requests()[-1]["options"]["num_predict"],
                         225)

    def test_a_fast_pc_keeps_the_full_profile_cap(self):
        self.features.set_option("llm_speed_tps", 40.0)
        self.features.set_option("llm_speed_model", "llama3.2:3b")
        self.assertEqual(self.assistant.llm.chat_options()["num_predict"], 400)

    def test_a_very_slow_pc_still_gets_usable_replies(self):
        self.features.set_option("llm_speed_tps", 0.5)
        self.features.set_option("llm_speed_model", "llama3.2:3b")
        self.assertEqual(self.assistant.llm.chat_options()["num_predict"], 80)

    def test_the_measurement_only_applies_to_the_model_it_was_taken_with(self):
        self.features.set_option("llm_speed_tps", 5.0)
        self.features.set_option("llm_speed_model", "some-other-model:8b")
        self.assertEqual(self.assistant.llm.chat_options()["num_predict"], 400)

    def test_benchmark_saves_its_measurement_and_says_what_it_changed(self):
        self.server.reply = " ".join(["w"] * 24)
        self.server.token_seconds = 0.2                  # 5 tokens/second
        answer = self.ask("benchmark")
        self.assertEqual(self.features.option("llm_speed_model"), "llama3.2:3b")
        self.assertAlmostEqual(self.features.option("llm_speed_tps"), 5.0)
        self.assertIn("I'll now cap replies at about 225 tokens", answer)
        self.assertEqual(self.assistant.llm.chat_options()["num_predict"], 225)

    def test_a_fast_benchmark_does_not_shorten_replies(self):
        self.server.reply = " ".join(["w"] * 24)
        answer = self.ask("benchmark")                   # 10 tok/s
        self.assertNotIn("I'll now cap", answer)
        self.assertEqual(self.assistant.llm.chat_options()["num_predict"], 400)

    def test_benchmark_prompt_is_unique_each_run_so_the_cache_cannot_hide_cost(self):
        self.ask("benchmark")
        time.sleep(0.01)
        self.ask("benchmark")
        first, second = [r["messages"][0]["content"]
                         for r in self.server.chat_requests()[:2]]
        self.assertTrue(first.startswith("[benchmark "))
        self.assertNotEqual(first, second)

    def test_a_long_wait_before_the_first_word_is_flagged(self):
        self.server.prompt_tokens = 150
        self.server.prompt_seconds = 30.0           # 5 tokens/second reading
        answer = self.ask("benchmark")
        self.assertIn("about 60 s before the first word", answer)
        self.assertIn("Slow on this PC", answer)

    def test_benchmark_without_ollama(self):
        self.server.stop()
        self.assistant.llm.snapshot(force=True)
        self.assertIn("couldn't run the benchmark", self.ask("benchmark model"))

    def test_without_a_client_the_commands_say_so(self):
        plain = Assistant(self.memory, features=self.features)
        self.assertIn("isn't set up", plain.respond("show model"))
        self.assertIn("isn't set up", plain.respond("benchmark"))


class FeatureStatusTests(LLMCase):
    def test_the_feature_reports_why_it_cannot_run(self):
        self.assertTrue(self.features.is_enabled("local_llm"))
        self.server.models = []
        self.assistant.llm.snapshot(force=True)
        status = self.features.status("local_llm")
        self.assertEqual(status.state, "unavailable")
        self.assertIn("ollama pull llama3.2:3b", status.reason)
        self.server.stop()
        self.assistant.llm.snapshot(force=True)
        self.assertIn("Ollama isn't running",
                      self.features.status("local_llm").reason)

    def test_options_persist_and_clear(self):
        path = self.base / "settings.json"
        registry = FeatureRegistry(path)
        registry.set_option("llm_model", "qwen2.5:3b")
        self.assertEqual(FeatureRegistry(path).option("llm_model"), "qwen2.5:3b")
        registry.set_option("llm_model", None)
        self.assertIsNone(FeatureRegistry(path).option("llm_model"))

    def test_malformed_options_are_ignored(self):
        path = self.base / "settings.json"
        path.write_text(json.dumps({"options": {"a": ["x"], "b": "ok", 3: 1}}),
                        encoding="utf-8")
        registry = FeatureRegistry(path)
        self.assertEqual(registry.option("b"), "ok")
        self.assertIsNone(registry.option("a"))


if __name__ == "__main__":
    unittest.main()
