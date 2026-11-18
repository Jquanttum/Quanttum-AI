"""Microphone access (PyAudio) and a tiny level meter."""

import array
import math
import sys

from core.voice.base import VoiceError

SAMPLE_RATE = 16000          # what Vosk models expect
CHUNK_FRAMES = 4000          # 0.25 s of audio per read


def rms(data):
    """Loudness of 16-bit mono audio, from 0.0 (silence) to 1.0 (full scale).

    Pure Python on purpose: the audioop module was removed in Python 3.13.
    """

    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])

    if sys.byteorder == "big":
        samples.byteswap()

    if not samples:
        return 0.0

    return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0


_HELP = ("Check Windows Settings > Privacy > Microphone (allow desktop apps), "
         "and that a microphone is plugged in and selected.")


class MicStream:
    """Opens the default microphone. Use as a context manager so it is
    always closed again: the microphone is never left open."""

    def __init__(self, pyaudio_module, rate=SAMPLE_RATE, chunk=CHUNK_FRAMES,
                 device=None):
        self._module = pyaudio_module
        self.rate = rate
        self.chunk = chunk
        self.device = device
        self._audio = None
        self._stream = None

    def __enter__(self):
        try:
            self._audio = self._module.PyAudio()
            self._stream = self._audio.open(
                format=self._module.paInt16, channels=1, rate=self.rate,
                input=True, frames_per_buffer=self.chunk,
                input_device_index=self.device)
        except (OSError, ValueError) as error:
            self.close()
            raise VoiceError(
                f"I couldn't open the microphone ({error}). {_HELP}")

        return self

    def read(self):
        try:
            return self._stream.read(self.chunk, exception_on_overflow=False)
        except OSError as error:
            raise VoiceError(f"The microphone stopped working ({error}).")

    def close(self):
        for closer in (getattr(self._stream, "stop_stream", None),
                       getattr(self._stream, "close", None),
                       getattr(self._audio, "terminate", None)):
            try:
                if closer:
                    closer()
            except Exception:
                pass

        self._stream = self._audio = None

    def __exit__(self, *exc):
        self.close()


def default_microphone_name(pyaudio_module):
    """Name of the default input device, or None."""

    audio = None

    try:
        audio = pyaudio_module.PyAudio()
        return str(audio.get_default_input_device_info().get("name"))
    except Exception:
        return None
    finally:
        try:
            if audio is not None:
                audio.terminate()
        except Exception:
            pass
