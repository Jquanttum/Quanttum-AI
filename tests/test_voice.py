import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from core.assistant import Assistant
from core.features import FeatureRegistry, FeatureUnavailable
from core.memory import Memory
from core.voice import VoiceError, VoiceService
from core.voice.audio import MicStream, default_microphone_name, rms
from core.voice.listen import run_listen
from core.voice.models import find_models, looks_like_vosk_model
from core.voice.tts import Speaker, speakable
from tests.fake_voice import (
    FakeClock, FakeMic, FakeProcess, FakePyAudio, FakeRecognizer, FakeSpeaker,
    FakeVosk, pcm,
)


def make_model(folder):
    (folder / "am").mkdir(parents=True)
    (folder / "conf").mkdir()
    return folder


class LevelTests(unittest.TestCase):
    def test_rms(self):
        self.assertEqual(rms(pcm(0.0)), 0.0)
        self.assertAlmostEqual(rms(pcm(1.0)), 1.0, places=2)
        self.assertAlmostEqual(rms(pcm(0.5)), 0.5, places=2)
        self.assertAlmostEqual(rms(pcm(-0.5)), 0.5, places=2)

    def test_empty_and_odd_length_audio(self):
        self.assertEqual(rms(b""), 0.0)
        self.assertGreater(rms(pcm(0.3) + b"\x01"), 0.0)       # stray byte ok


class ModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_recognises_a_real_model_layout(self):
        model = make_model(self.base / "vosk-model-small-en-us-0.15")
        self.assertTrue(looks_like_vosk_model(model))
        self.assertFalse(looks_like_vosk_model(self.base))
        self.assertFalse(looks_like_vosk_model(self.base / "missing"))

    def test_finds_models_directly_nested_and_ignores_the_rest(self):
        direct = make_model(self.base / "a-model")
        nested = make_model(self.base / "zipname" / "inner-model")
        (self.base / "photos").mkdir()
        (self.base / "notes.txt").write_text("x")
        deep = make_model(self.base / "x" / "y" / "z" / "too-deep")
        found = find_models([self.base])
        self.assertIn(direct, found)
        self.assertIn(nested, found)
        self.assertNotIn(deep, found)
        self.assertEqual(len(found), 2)

    def test_missing_folder_and_a_model_given_directly(self):
        self.assertEqual(find_models([self.base / "nope"]), [])
        model = make_model(self.base / "m")
        self.assertEqual(find_models([model]), [model])


class ListenLoopTests(unittest.TestCase):
    def listen(self, recognizer, mic=None, **kwargs):
        clock = FakeClock()
        mic = mic or FakeMic(clock=clock)
        mic.clock = clock
        return mic, run_listen(mic, recognizer, clock=clock, **kwargs)

    def test_returns_the_sentence_when_you_stop_speaking(self):
        partials = []
        recognizer = FakeRecognizer({2: "open", 3: "open note",
                                     4: ("final", "open notepad")})
        mic, result = self.listen(recognizer, on_partial=partials.append)
        self.assertEqual((result.text, result.reason), ("open notepad", "final"))
        self.assertEqual(partials, ["open", "open note"])
        self.assertEqual(mic.reads, 4)

    def test_partial_callback_only_fires_on_change(self):
        recognizer = FakeRecognizer({1: "hello", 2: "hello", 3: "hello",
                                     4: ("final", "hello")})
        partials = []
        self.listen(recognizer, on_partial=partials.append)
        self.assertEqual(partials, ["hello"])

    def test_nothing_said_ends_with_a_silence_result(self):
        mic, result = self.listen(FakeRecognizer(), silence_timeout=2.0,
                                  max_seconds=30)
        self.assertEqual((result.text, result.reason), ("", "silence"))
        self.assertLessEqual(result.seconds, 2.5)

    def test_speech_that_never_finishes_is_cut_off_at_the_maximum(self):
        recognizer = FakeRecognizer({1: "talking and talking"})
        mic, result = self.listen(recognizer, max_seconds=3.0,
                                  silence_timeout=1.0)
        self.assertEqual(result.reason, "timeout")
        self.assertEqual(result.text, "talking and talking")

    def test_empty_results_from_noise_are_skipped(self):
        recognizer = FakeRecognizer({1: ("final", ""), 3: ("final", "yes")})
        mic, result = self.listen(recognizer)
        self.assertEqual(result.text, "yes")

    def test_cancel_stops_listening(self):
        cancel = threading.Event()
        cancel.set()
        mic, result = self.listen(FakeRecognizer(), cancel=cancel)
        self.assertEqual((result.text, result.reason), ("", "cancelled"))
        self.assertEqual(mic.reads, 0)

    def test_cancel_in_the_middle(self):
        cancel = threading.Event()
        recognizer = FakeRecognizer()
        original = recognizer.AcceptWaveform

        def accept(data):
            if recognizer.calls == 2:
                cancel.set()
            return original(data)

        recognizer.AcceptWaveform = accept
        mic, result = self.listen(recognizer, cancel=cancel)
        self.assertEqual(result.reason, "cancelled")
        self.assertEqual(result.text, "")

    def test_peak_level_separates_a_dead_mic_from_unclear_speech(self):
        _, quiet = self.listen(FakeRecognizer(), silence_timeout=1.0)
        self.assertLess(quiet.peak, 0.01)
        loud = FakeMic(chunks=[pcm(0.0), pcm(0.4), pcm(0.1)])
        _, noisy = self.listen(FakeRecognizer(), mic=loud, silence_timeout=1.0)
        self.assertAlmostEqual(noisy.peak, 0.4, places=2)

    def test_garbage_from_the_recogniser_is_ignored(self):
        class Broken(FakeRecognizer):
            def PartialResult(self):
                return "not json"

            def Result(self):
                return "{"

            def FinalResult(self):
                return "[]"

        _, result = self.listen(Broken(), silence_timeout=1.0)
        self.assertEqual(result.text, "")


class MicStreamTests(unittest.TestCase):
    def test_opens_reads_and_always_closes(self):
        module = FakePyAudio()
        with MicStream(module) as mic:
            data = mic.read()
        self.assertEqual(len(data), 8000)
        self.assertEqual(module.opened_with["rate"], 16000)
        self.assertEqual(module.opened_with["channels"], 1)
        self.assertTrue(module.opened_with["input"])
        self.assertEqual(module.events, ["stop_stream", "close", "terminate"])

    def test_closes_even_if_the_caller_fails(self):
        module = FakePyAudio()
        with self.assertRaises(RuntimeError):
            with MicStream(module):
                raise RuntimeError("boom")
        self.assertIn("close", module.events)
        self.assertIn("terminate", module.events)

    def test_no_microphone_gives_a_helpful_error(self):
        with self.assertRaises(VoiceError) as caught:
            MicStream(FakePyAudio(fail_open=True)).__enter__()
        self.assertIn("Privacy > Microphone", str(caught.exception))

    def test_unplugged_mid_read(self):
        with self.assertRaises(VoiceError):
            with MicStream(FakePyAudio(fail_read=True)) as mic:
                mic.read()

    def test_default_microphone_name(self):
        self.assertEqual(default_microphone_name(FakePyAudio()),
                         "Fake Microphone")

        class Broken:
            def PyAudio(self):
                raise OSError("no audio system")

        self.assertIsNone(default_microphone_name(Broken()))


class SpeakerTests(unittest.TestCase):
    def speaker(self, **kwargs):
        self.processes = []

        def popen(command, **options):
            self.command, self.options = command, options
            process = FakeProcess()
            self.processes.append(process)
            return process

        defaults = dict(platform="win32", which=lambda name: "C:\\ps.exe",
                        popen=popen)
        defaults.update(kwargs)
        return Speaker(**defaults)

    def test_only_available_on_windows_with_powershell(self):
        self.assertIn("Windows", self.speaker(platform="linux").available_reason())
        self.assertIn("PowerShell",
                      self.speaker(which=lambda n: None).available_reason())
        self.assertEqual(self.speaker().available_reason(), "")
        self.assertFalse(self.speaker(platform="linux").speak("hi"))

    def test_the_text_goes_through_stdin_never_the_command_line(self):
        speaker = self.speaker()
        nasty = 'hello"; Remove-Item C:\\ -Recurse; "'
        self.assertTrue(speaker.speak(nasty))
        self.assertNotIn("Remove-Item", " ".join(self.command))
        self.assertEqual(self.processes[0].written, nasty.encode("utf-8"))
        self.assertTrue(self.processes[0]._closed)
        self.assertEqual(self.command[0], "C:\\ps.exe")
        self.assertIn("-NoProfile", self.command)
        self.assertNotIn("shell", self.options)

    def test_non_ascii_text_is_sent_as_utf8(self):
        speaker = self.speaker()
        speaker.speak("Akwaaba, café")
        self.assertEqual(self.processes[0].written.decode("utf-8"),
                         "Akwaaba, café")
        self.assertIn("UTF8", self.command[-1])

    def test_new_speech_replaces_old_and_stop_works(self):
        speaker = self.speaker()
        speaker.speak("first")
        speaker.speak("second")
        self.assertTrue(self.processes[0].terminated)
        self.assertFalse(self.processes[1].terminated)
        self.assertTrue(speaker.speaking)
        speaker.stop()
        self.assertTrue(self.processes[1].terminated)
        self.assertFalse(speaker.speaking)

    def test_empty_text_and_launch_failures(self):
        speaker = self.speaker()
        self.assertFalse(speaker.speak("  ** ## "))
        self.assertEqual(self.processes, [])

        def failing(command, **options):
            raise OSError("cannot start")

        self.assertFalse(self.speaker(popen=failing).speak("hi"))

    def test_speakable_cleans_markdown_and_shortens_long_replies(self):
        self.assertEqual(speakable("**Bold** and `code` # heading"),
                         "Bold and code heading")
        long = ("This is a sentence. " * 40)
        short = speakable(long, max_chars=100)
        self.assertLessEqual(len(short), 100)
        self.assertTrue(short.endswith("."))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.models = self.base / "models"
        self.models.mkdir()
        self.features = FeatureRegistry(find_spec=lambda name: object(),
                                        memory_status=lambda: None,
                                        cpu_threads=lambda: 4)

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, **kwargs):
        kwargs.setdefault("speaker", FakeSpeaker())
        return VoiceService(self.features, [self.models], **kwargs)

    def test_no_model_means_input_is_unavailable_with_instructions(self):
        reason = self.service().input_unavailable_reason()
        self.assertIn("no speech model found", reason)
        self.assertIn(str(self.models), reason)

    def test_first_model_found_is_used_and_your_choice_wins(self):
        first = make_model(self.models / "a-small")
        second = make_model(self.models / "b-big")
        service = self.service()
        self.assertEqual(service.model_path(), first)
        self.features.set_option("voice_model", str(second))
        self.assertEqual(service.model_path(), second)
        self.features.set_option("voice_model", str(self.base / "gone"))
        self.assertEqual(service.model_path(), first)       # falls back
        self.assertEqual(service.input_unavailable_reason(), "")

    def test_listen_without_a_model_is_a_clear_error(self):
        with self.assertRaises(VoiceError):
            self.service(mic_factory=FakeMic).listen()

    def test_listen_runs_and_always_closes_the_microphone(self):
        make_model(self.models / "m")
        clock = FakeClock()
        mic = FakeMic(clock=clock)
        service = self.service(
            mic_factory=lambda: mic, clock=clock,
            recognizer_factory=lambda path: FakeRecognizer(
                {2: ("final", "hello world")}))
        result = service.listen()
        self.assertEqual(result.text, "hello world")
        self.assertTrue(mic.opened)
        self.assertTrue(mic.closed)

    def test_the_microphone_closes_even_when_listening_fails(self):
        make_model(self.models / "m")
        mic = FakeMic()

        class Exploding(FakeRecognizer):
            def AcceptWaveform(self, data):
                raise RuntimeError("recogniser crashed")

        service = self.service(mic_factory=lambda: mic,
                               recognizer_factory=lambda path: Exploding())
        with self.assertRaises(RuntimeError):
            service.listen()
        self.assertTrue(mic.closed)

    def test_a_model_that_will_not_load_is_a_voice_error(self):
        make_model(self.models / "m")

        def broken(path):
            raise ValueError("corrupt model")

        service = self.service(mic_factory=FakeMic, recognizer_factory=broken)
        with self.assertRaises(VoiceError) as caught:
            service.listen()
        self.assertIn("couldn't load the speech model", str(caught.exception))

    def test_real_libraries_load_lazily_only_when_enabled(self):
        make_model(self.models / "m")
        service = self.service()
        with self.assertRaises(VoiceError) as caught:     # feature is off
            service.listen()
        self.assertIn("Voice input is disabled", str(caught.exception))
        self.assertIn("enable voice input", str(caught.exception))
        self.assertNotIn("couldn't load", str(caught.exception))

        self.features.set_enabled("voice_input", True)
        vosk = FakeVosk({2: ("final", "hello there")})
        pyaudio = FakePyAudio()

        def importer(name):
            return {"vosk": vosk, "pyaudio": pyaudio}[name]

        with mock.patch("importlib.import_module", side_effect=importer):
            first = service.listen()
            second = service.listen()

        self.assertEqual(first.text, "hello there")
        self.assertEqual(second.text, "hello there")
        self.assertEqual(vosk.log_level, -1)
        self.assertEqual(vosk.rate, 16000)
        self.assertEqual(len(vosk.models_loaded), 1)      # model cached
        self.assertEqual(pyaudio.events.count("terminate"), 2)

    def test_speaking_is_delegated(self):
        speaker = FakeSpeaker()
        service = self.service(speaker=speaker)
        service.speak("hello")
        self.assertEqual(speaker.spoken, ["hello"])
        self.assertTrue(service.speaking)
        service.stop_speaking()
        self.assertFalse(service.speaking)

    def test_microphone_name_needs_the_package(self):
        service = self.service()
        self.assertIsNone(service.microphone_name())       # feature off


class AssistantVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.models = self.base / "models"
        self.models.mkdir()
        self.memory = Memory(str(self.base / "a.db"))
        self.features = FeatureRegistry(find_spec=lambda name: object(),
                                        memory_status=lambda: None,
                                        cpu_threads=lambda: 4)
        self.speaker = FakeSpeaker()
        self.clock = FakeClock()
        self.mic = FakeMic(clock=self.clock)
        self.recognizer = FakeRecognizer({2: ("final", "open notepad")})
        self.voice = VoiceService(
            self.features, [self.models], speaker=self.speaker,
            mic_factory=lambda: self.mic,
            recognizer_factory=lambda path: self.recognizer,
            clock=self.clock)
        self.assistant = Assistant(self.memory, features=self.features,
                                   voice=self.voice)

    def tearDown(self):
        self.assistant.shutdown()
        self.memory.close()
        self.tmp.cleanup()

    def ask(self, text):
        return self.assistant.respond(text)

    def test_voice_is_off_by_default_and_says_why_it_cannot_run(self):
        self.assertEqual(self.features.status("voice_input").state, "disabled")
        self.features.set_enabled("voice_input", True)
        status = self.features.status("voice_input")
        self.assertEqual(status.state, "unavailable")
        self.assertIn("no speech model found", status.reason)
        make_model(self.models / "m")
        self.assertTrue(self.features.is_enabled("voice_input"))

    def test_speech_output_needs_windows(self):
        self.features.set_enabled("voice_output", True)
        self.speaker.available = "spoken replies need Windows"
        self.assertEqual(self.features.status("voice_output").state,
                         "unavailable")
        self.speaker.available = ""
        self.assertTrue(self.features.is_enabled("voice_output"))

    def test_autosend_needs_voice_input(self):
        self.features.set_enabled("voice_autosend", True)
        self.assertEqual(self.features.status("voice_autosend").state,
                         "unavailable")
        make_model(self.models / "m")
        self.features.set_enabled("voice_input", True)
        self.assertTrue(self.features.is_enabled("voice_autosend"))

    def test_voice_status_before_setup_explains_how_to_get_a_model(self):
        answer = self.ask("voice status")
        self.assertIn("Speech model: none found", answer)
        self.assertIn("alphacephei.com/vosk/models", answer)
        self.assertIn("vosk-model-small-en-us-0.15", answer)
        self.assertIn(str(self.models), answer)
        self.assertIn("only listens while you press the Mic button", answer)

    def test_voice_status_after_setup(self):
        make_model(self.models / "vosk-model-small-en-us-0.15")
        self.features.set_enabled("voice_input", True)
        answer = self.ask("voice")
        self.assertIn("Voice input: enabled", answer)
        self.assertIn("Speech model: vosk-model-small-en-us-0.15", answer)

    def test_test_microphone_reports_what_it_heard(self):
        make_model(self.models / "m")
        self.features.set_enabled("voice_input", True)
        self.mic.chunks = [pcm(0.2)] * 4
        answer = self.ask("test microphone")
        self.assertIn('I heard: "open notepad"', answer)
        self.assertIn("The microphone works", answer)
        self.assertTrue(self.mic.closed)

    def test_test_microphone_diagnoses_a_dead_mic(self):
        make_model(self.models / "m")
        self.features.set_enabled("voice_input", True)
        self.recognizer.script = {}
        answer = self.ask("mic test")
        self.assertIn("heard nothing at all", answer)
        self.assertIn("Privacy > Microphone", answer)

    def test_test_microphone_diagnoses_unclear_speech(self):
        make_model(self.models / "m")
        self.features.set_enabled("voice_input", True)
        self.recognizer.script = {}
        self.mic.chunks = [pcm(0.3)] * 40
        answer = self.ask("check the microphone")
        self.assertIn("picked up sound", answer)

    def test_test_microphone_when_voice_is_off_or_broken(self):
        self.assertIn("Voice input is disabled", self.ask("test mic"))
        make_model(self.models / "m")
        self.features.set_enabled("voice_input", True)

        def failing():
            raise VoiceError("I couldn't open the microphone (busy).")

        self.voice._mic_factory = failing
        self.assertIn("couldn't open the microphone", self.ask("test mic"))

    def test_stop_speaking(self):
        self.voice.speak("a long answer")
        self.assertEqual(self.ask("stop speaking"), "Okay.")
        self.assertFalse(self.speaker.speaking)
        self.assertEqual(self.ask("be quiet"), "Okay.")

    def test_choosing_a_speech_model(self):
        make_model(self.base / "elsewhere" / "big-model")
        wrong = self.base / "not-a-model"
        wrong.mkdir()
        self.assertIn("doesn't look like a Vosk speech model",
                      self.ask(f"use voice model {wrong}"))
        self.assertIsNone(self.features.option("voice_model"))
        good = self.base / "elsewhere" / "big-model"
        self.assertIn("Now using the speech model",
                      self.ask(f'use voice model "{good}"'))
        self.assertEqual(self.features.option("voice_model"), str(good))
        self.assertIn("automatically", self.ask("use default voice model"))
        self.assertIsNone(self.features.option("voice_model"))

    def test_shutdown_silences_speech(self):
        self.voice.speak("hello")
        self.assistant.shutdown()
        self.assertFalse(self.speaker.speaking)

    def test_without_a_voice_service_the_commands_say_so(self):
        plain = Assistant(self.memory, features=self.features)
        for command in ("voice status", "test microphone", "stop speaking",
                        "use voice model x"):
            with self.subTest(command=command):
                self.assertIn("isn't set up", plain.respond(command))


if __name__ == "__main__":
    unittest.main()
