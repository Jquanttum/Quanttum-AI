"""The voice system as the rest of the app sees it."""

import time
from pathlib import Path

from core.features import FeatureUnavailable
from core.voice.audio import (
    SAMPLE_RATE, MicStream, default_microphone_name,
)
from core.voice.base import VoiceError
from core.voice.listen import run_listen
from core.voice.models import find_models, looks_like_vosk_model
from core.voice.tts import Speaker


class VoiceService:
    """Listening (Vosk, offline) and speaking (Windows voice).

    mic_factory and recognizer_factory exist so tests can run without a real
    microphone or speech model. By default the real libraries are imported
    only when first needed, and only if the feature is enabled.
    """

    def __init__(self, features, model_dirs=(), mic_factory=None,
                 recognizer_factory=None, speaker=None, clock=time.monotonic):
        self.features = features
        self.model_dirs = [Path(d) for d in model_dirs]
        self._mic_factory = mic_factory
        self._recognizer_factory = recognizer_factory
        self._clock = clock
        self.speaker = speaker if speaker is not None else Speaker()
        self._vosk_model = None
        self._vosk_model_path = None

    # ---- speech models ---------------------------------------------------

    def models(self):
        return find_models(self.model_dirs)

    def model_path(self):
        """The model to use: the one you chose, else the first one found."""

        chosen = self.features.option("voice_model")

        if isinstance(chosen, str) and looks_like_vosk_model(chosen):
            return Path(chosen)

        found = self.models()
        return found[0] if found else None

    def input_unavailable_reason(self):
        if self.model_path() is None:
            folders = ", ".join(str(d) for d in self.model_dirs) or "(none)"
            return ("no speech model found. Download a Vosk model and unzip "
                    f"it into {folders} (say: voice status)")

        return ""

    def output_unavailable_reason(self):
        return self.speaker.available_reason()

    # ---- listening ------------------------------------------------------

    def _open_mic(self):
        if self._mic_factory is not None:
            return self._mic_factory()

        pyaudio = self.features.import_module("voice_input", "pyaudio")
        return MicStream(pyaudio)

    def _new_recognizer(self, path):
        if self._recognizer_factory is not None:
            return self._recognizer_factory(path)

        vosk = self.features.import_module("voice_input", "vosk")

        if self._vosk_model is None or self._vosk_model_path != path:
            vosk.SetLogLevel(-1)
            self._vosk_model = vosk.Model(str(path))      # a second or two
            self._vosk_model_path = path

        return vosk.KaldiRecognizer(self._vosk_model, SAMPLE_RATE)

    def listen(self, on_partial=None, cancel=None, max_seconds=15.0,
               silence_timeout=8.0):
        """Open the microphone, wait for one spoken sentence, close it again.

        Blocks until done, so call it from a worker thread.
        """

        reason = self.input_unavailable_reason()

        if reason:
            raise VoiceError(reason)

        try:
            try:
                recognizer = self._new_recognizer(self.model_path())
            except (VoiceError, FeatureUnavailable):
                raise
            except Exception as error:
                raise VoiceError(
                    f"I couldn't load the speech model ({error}).")

            with self._open_mic() as mic:
                return run_listen(mic, recognizer, on_partial, cancel,
                                  max_seconds, silence_timeout,
                                  clock=self._clock)
        except FeatureUnavailable as error:
            raise VoiceError(f"{error}. Say: enable voice input")

    def microphone_name(self):
        try:
            pyaudio = self.features.import_module("voice_input", "pyaudio")
        except Exception:
            return None

        return default_microphone_name(pyaudio)

    # ---- speaking ------------------------------------------------------

    def speak(self, text):
        return self.speaker.speak(text)

    def stop_speaking(self):
        self.speaker.stop()

    @property
    def speaking(self):
        return self.speaker.speaking
