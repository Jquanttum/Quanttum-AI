"""Opt-in smoke test that drives the real window.

It opens an actual (briefly visible) window, so it only runs when asked:

    PowerShell:  $env:QUANTTUM_GUI_TESTS = "1"
                 py -3.13 -m unittest tests.test_gui_smoke -v

On a machine without a display (or without customtkinter) it is skipped.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core.features import FeatureRegistry
from core.memory import Memory
from core.voice import VoiceService
from tests.fake_ollama import FakeOllama
from tests.fake_voice import FakeMic, FakeRecognizer, FakeSpeaker

FAKE_RAM = {"percent": 40, "total_gb": 3.83, "available_gb": 2.3}


@unittest.skipUnless(os.environ.get("QUANTTUM_GUI_TESTS") == "1",
                     "set QUANTTUM_GUI_TESTS=1 to run GUI tests")
class WindowSmokeTest(unittest.TestCase):
    def setUp(self):
        try:
            import customtkinter  # noqa: F401
            from interface import window
        except ImportError as error:
            self.skipTest(f"GUI libraries unavailable: {error}")

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(os.path.realpath(self.tmp.name))
        self.docs = base / "Documents"
        self.docs.mkdir()
        (self.docs / "Exam Notes.docx").write_text("x")
        self.settings_path = base / "settings.json"
        # standard profile = the local model is on by default
        self.settings_path.write_text('{"profile": "standard"}', encoding="utf-8")
        self.server = FakeOllama(reply="I am the local model.").start()
        db_path = str(base / "gui.db")

        # voice: a fake microphone, recogniser and speaker, and a fake model
        self.models = base / "vosk-models"
        (self.models / "m" / "am").mkdir(parents=True)
        (self.models / "m" / "conf").mkdir()
        self.speaker = FakeSpeaker()
        self.mic = FakeMic(real_delay=0.02)
        self.recognizer = FakeRecognizer({})
        test = self

        class QuickVoice(VoiceService):
            def listen(self, *args, **kwargs):
                kwargs.setdefault("silence_timeout", 0.4)
                return super().listen(*args, **kwargs)

        def make_voice(features, folders):
            return QuickVoice(features, folders, mic_factory=lambda: test.mic,
                              recognizer_factory=lambda path: test.recognizer,
                              speaker=test.speaker)

        self.patches = [
            mock.patch.object(window, "Memory", lambda: Memory(db_path)),
            mock.patch.object(window, "SETTINGS_PATH", str(self.settings_path)),
            mock.patch.object(window, "OLLAMA_HOST", self.server.url),
            mock.patch.object(window, "VoiceService", make_voice),
            mock.patch.object(window, "VOSK_MODELS_DIR", str(self.models)),
            mock.patch.object(window, "FeatureRegistry",
                              lambda path: FeatureRegistry(
                                  path, find_spec=lambda name: object())),
            mock.patch.object(window, "PERMISSIONS_PATH",
                              str(base / "permissions.json")),
            mock.patch.object(window, "FILE_INDEX_PATH",
                              str(base / "fileindex.db")),
            mock.patch.object(window, "default_user_folders",
                              lambda: [self.docs]),
            mock.patch.object(window, "get_memory_status",
                              lambda: dict(FAKE_RAM)),
        ]
        for patch in self.patches:
            patch.start()

        try:
            self.app = window.QuanttumApp()
        except Exception as error:               # e.g. no display
            self.tearDown()
            self.skipTest(f"cannot open a window here: {error}")

        self.app.update()

    def tearDown(self):
        for patch in getattr(self, "patches", []):
            patch.stop()
        if getattr(self, "app", None):
            self.app.on_close()
        if getattr(self, "server", None):
            self.server.stop()
        self.tmp.cleanup()

    def say(self, text, wait=True):
        self.app.entry.insert(0, text)
        self.app.send_message()
        self.app.update()

        if wait:
            self.wait_until_idle()

    def wait_until_idle(self, timeout=15):
        deadline = time.time() + timeout

        while self.app.busy and time.time() < deadline:
            self.app.update()
            time.sleep(0.01)

        self.app.update()
        self.assertFalse(self.app.busy, "the window never finished replying")

    def chat(self):
        return self.app.chat.get("1.0", "end")

    def test_conversation_and_memory_flow(self):
        self.say("Remember that I prefer Python for development")
        self.say("what do you remember?")
        self.assertIn("I prefer Python for development", self.chat())

    def test_ram_monitor_toggle_takes_effect_live(self):
        self.app.update_resource_status()
        self.assertIn("RAM: 40% used", self.app.resource_label.cget("text"))

        self.say("disable ram monitor")
        self.app.update_resource_status()
        self.assertIn("RAM monitor is off",
                      self.app.resource_label.cget("text"))

        self.say("enable ram monitor")
        self.app.update_resource_status()
        self.assertIn("RAM: 40% used", self.app.resource_label.cget("text"))

    def test_settings_are_written_next_to_the_data(self):
        self.say("disable ram monitor")
        self.assertTrue(self.settings_path.exists())

    def test_find_files_and_create_folder_through_the_window(self):
        self.app.index.wait_until_idle(10)
        self.say("find exam")
        self.assertIn("Exam Notes.docx", self.chat())

        self.say(f"create folder Reports in {self.docs}")
        self.assertIn("about to create", self.chat())
        self.assertFalse((self.docs / "Reports").exists())
        self.say("yes")
        self.assertTrue((self.docs / "Reports").is_dir())

        self.say("undo")
        self.say("yes")
        self.assertFalse((self.docs / "Reports").exists())

    def test_closing_the_window_stops_the_indexer(self):
        self.app.assistant.index.refresh_in_background([self.docs],
                                                       throttle=0.05)
        self.app.on_close()
        self.assertFalse(self.app.index.indexing)
        self.app = None

    def test_the_local_model_answers_and_streams_into_the_chat(self):
        self.server.reply = "one two three four"
        self.say("why is the sky blue")
        self.assertIn("one two three four", self.chat())
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.status_label.cget("text"), "CORE ONLINE")
        self.assertFalse(self.app.stop_button.winfo_ismapped())

    def test_the_window_stays_responsive_while_the_model_is_thinking(self):
        self.server.reply = " ".join(f"w{i}" for i in range(40))
        self.server.token_delay = 0.03
        self.say("write something long", wait=False)
        self.assertTrue(self.app.busy)
        self.assertEqual(self.app.status_label.cget("text"), "THINKING...")
        self.app.update()                          # the UI keeps processing
        self.say("a second message while busy", wait=False)   # ignored
        self.wait_until_idle(20)
        self.assertNotIn("a second message while busy", self.chat())

    def test_stop_ends_a_reply(self):
        self.server.reply = " ".join(f"w{i}" for i in range(300))
        self.server.token_delay = 0.03
        self.say("write an essay", wait=False)
        deadline = time.time() + 5
        while "w2 " not in self.chat() and time.time() < deadline:
            self.app.update()
            time.sleep(0.01)
        self.app.assistant.cancel()
        self.wait_until_idle()
        self.assertIn("[stopped]", self.chat())
        self.assertNotIn("w250", self.chat())

    def test_an_unreachable_model_is_explained_in_the_chat(self):
        self.server.stop()
        self.app.assistant.llm.snapshot(force=True)
        self.say("what is a quasar")
        self.assertIn("local model isn't available", self.chat())

    # ---- voice -------------------------------------------------------

    def wait_until(self, condition, timeout=10, message="condition not met"):
        deadline = time.time() + timeout

        while not condition() and time.time() < deadline:
            self.app.update()
            time.sleep(0.01)

        self.app.update()
        self.assertTrue(condition(), message)

    def enable(self, *keys):
        for key in keys:
            self.app.features.set_enabled(key, True)

    def listen(self):
        self.app.toggle_listening()
        self.assertTrue(self.app.listening)
        self.wait_until(lambda: not self.app.listening, 10,
                        "listening never finished")

    def test_the_mic_fills_the_box_live_and_waits_for_you_to_press_enter(self):
        self.enable("voice_input")
        self.recognizer.script = {3: "open", 5: "open note",
                                  8: ("final", "open notepad")}
        self.app.toggle_listening()
        self.assertEqual(self.app.status_label.cget("text"), "LISTENING...")
        self.wait_until(lambda: self.app.entry.get() in ("open", "open note"),
                        message="no live transcript appeared")
        self.wait_until(lambda: not self.app.listening)
        self.assertEqual(self.app.entry.get(), "open notepad")
        self.assertNotIn("You\nopen notepad", self.chat())      # not sent
        self.assertEqual(self.app.status_label.cget("text"), "CORE ONLINE")
        self.assertEqual(self.app.mic_button.cget("text"), "Mic")
        self.assertTrue(self.mic.closed)                          # mic released

    def test_pressing_the_mic_again_cancels_and_clears_the_box(self):
        self.enable("voice_input")
        self.recognizer.script = {2: "hello"}                     # never ends
        self.app.toggle_listening()
        self.wait_until(lambda: self.app.entry.get() == "hello")
        self.app.toggle_listening()                               # "Done"
        self.wait_until(lambda: not self.app.listening)
        self.assertEqual(self.app.entry.get(), "")
        self.assertTrue(self.mic.closed)

    def test_silence_is_reported_helpfully(self):
        self.enable("voice_input")
        self.listen()
        self.assertIn("I didn't hear anything", self.chat())
        self.assertIn("test microphone", self.chat())

    def test_unavailable_voice_is_explained_when_you_press_the_mic(self):
        self.app.toggle_listening()
        self.assertFalse(self.app.listening)
        self.assertIn("Voice input is disabled", self.chat())
        self.assertFalse(self.mic.opened)                        # never opened

    def test_autosend_sends_ordinary_sentences(self):
        self.enable("voice_input", "voice_autosend")
        self.recognizer.script = {2: ("final", "why is the sky blue")}
        self.listen()
        self.wait_until(lambda: not self.app.busy and
                        "I am the local model." in self.chat())
        self.assertIn("why is the sky blue", self.chat())

    def test_autosend_never_sends_an_answer_to_a_question(self):
        self.enable("voice_input", "voice_autosend")
        for spoken in ("yes", "no", "keep both", "confirm delete reports"):
            with self.subTest(spoken=spoken):
                self.app.entry.delete(0, "end")
                self.recognizer.script = {2: ("final", spoken)}
                self.mic.chunks, self.recognizer.calls = [], 0
                self.listen()
                self.assertEqual(self.app.entry.get(), spoken)   # waits for you
                self.assertFalse(self.app.busy)
                self.assertNotIn(f"You\n{spoken}", self.chat())

    def test_a_spoken_yes_cannot_approve_an_action_without_enter(self):
        self.enable("voice_input", "voice_autosend")
        docs = self.docs
        self.say(f"create folder Reports in {docs}")             # asks first
        self.recognizer.script = {2: ("final", "yes")}
        self.listen()
        self.assertFalse((docs / "Reports").exists())            # not approved
        self.app.send_message()                                  # you press Enter
        self.wait_until(lambda: not self.app.busy)
        self.assertTrue((docs / "Reports").is_dir())

    def test_replies_are_read_aloud_when_voice_output_is_on(self):
        self.enable("voice_output")
        self.say("why is the sky blue")
        self.assertEqual(self.speaker.spoken, ["I am the local model."])

    def test_nothing_is_spoken_when_voice_output_is_off(self):
        self.say("why is the sky blue")
        self.assertEqual(self.speaker.spoken, [])

    def test_sending_a_message_or_pressing_stop_silences_speech(self):
        self.enable("voice_output")
        self.say("why is the sky blue")
        before = self.speaker.stops
        self.say("another question")
        self.assertGreater(self.speaker.stops, before)
        before = self.speaker.stops
        self.app.stop_everything()
        self.assertGreater(self.speaker.stops, before)

    def test_the_mic_is_ignored_while_the_model_is_replying(self):
        self.enable("voice_input")
        self.server.reply = " ".join(f"w{i}" for i in range(30))
        self.server.token_delay = 0.03
        self.say("write something", wait=False)
        self.app.toggle_listening()
        self.assertFalse(self.app.listening)
        self.wait_until(lambda: not self.app.busy, 20)

    def test_closing_the_window_while_listening_releases_the_mic(self):
        self.enable("voice_input")
        self.recognizer.script = {2: "hello"}
        self.app.toggle_listening()
        self.wait_until(lambda: self.mic.opened)
        self.app.on_close()
        self.wait_until(lambda: self.mic.closed)
        self.app = None

    def test_show_features_renders(self):
        self.say("show features")
        self.assertIn("RAM monitor (ram_monitor)", self.chat())


if __name__ == "__main__":
    unittest.main()
