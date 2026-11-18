"""Stand-ins for the microphone, Vosk and the Windows voice, for tests."""

import json
import struct
import time


def pcm(level, frames=4000):
    """16-bit mono audio at a constant level (0..1)."""

    value = int(max(-1.0, min(1.0, level)) * 32767)
    return struct.pack(f"<{frames}h", *([value] * frames))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeMic:
    """Context-manager microphone. Each read() advances a fake clock and
    returns the next scripted chunk (or silence once the script runs out)."""

    def __init__(self, chunks=(), clock=None, step=0.25, real_delay=0.0):
        self.chunks = list(chunks)
        self.clock = clock
        self.step = step
        self.real_delay = real_delay
        self.reads = 0
        self.opened = False
        self.closed = False

    def __enter__(self):
        self.opened = True
        return self

    def __exit__(self, *exc):
        self.closed = True

    def read(self):
        self.reads += 1

        if self.clock is not None:
            self.clock.now += self.step

        if self.real_delay:
            time.sleep(self.real_delay)

        return self.chunks.pop(0) if self.chunks else pcm(0.0)


class FakeRecognizer:
    """Vosk look-alike driven by a script keyed on the chunk number:
    {3: "open", 5: ("final", "open notepad")}."""

    def __init__(self, script=None, final_text=""):
        self.script = script or {}
        self.final_text = final_text
        self.calls = 0
        self._partial = ""
        self._result = ""

    def AcceptWaveform(self, data):
        self.calls += 1
        event = self.script.get(self.calls)

        if isinstance(event, tuple) and event[0] == "final":
            self._result = event[1]
            return True

        if isinstance(event, str):
            self._partial = event

        return False

    def PartialResult(self):
        return json.dumps({"partial": self._partial})

    def Result(self):
        return json.dumps({"text": self._result})

    def FinalResult(self):
        return json.dumps({"text": self.final_text or self._partial})


class FakeSpeaker:
    def __init__(self, available=""):
        self.available = available
        self.spoken = []
        self.stops = 0
        self.speaking = False

    def available_reason(self):
        return self.available

    def speak(self, text):
        self.spoken.append(text)
        self.speaking = True
        return True

    def stop(self):
        self.stops += 1
        self.speaking = False


class FakeProcess:
    """subprocess.Popen look-alike for the Windows speech test."""

    def __init__(self):
        self.written = b""
        self.terminated = False
        self.stdin = self
        self._closed = False

    def write(self, data):
        self.written += data

    def close(self):
        self._closed = True

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


class FakePyAudio:
    """The pyaudio module, for MicStream tests."""

    paInt16 = 8

    def __init__(self, fail_open=False, fail_read=False,
                 device_name="Fake Microphone"):
        self.fail_open, self.fail_read = fail_open, fail_read
        self.device_name = device_name
        self.opened_with = None
        self.events = []
        outer = self

        class Stream:
            def read(self, frames, exception_on_overflow=False):
                if outer.fail_read:
                    raise OSError("device unplugged")
                return pcm(0.2, frames)

            def stop_stream(self):
                outer.events.append("stop_stream")

            def close(self):
                outer.events.append("close")

        class Audio:
            def open(self, **kwargs):
                if outer.fail_open:
                    raise OSError("No Default Input Device Available")
                outer.opened_with = kwargs
                return Stream()

            def terminate(self):
                outer.events.append("terminate")

            def get_default_input_device_info(self):
                return {"name": outer.device_name}

        self._audio_class = Audio

    def PyAudio(self):
        return self._audio_class()


class FakeVosk:
    """The vosk module."""

    def __init__(self, script=None):
        self.script = script
        self.models_loaded = []
        self.log_level = None

    def SetLogLevel(self, level):
        self.log_level = level

    def Model(self, path):
        self.models_loaded.append(path)
        return ("model", path)

    def KaldiRecognizer(self, model, rate):
        self.rate = rate
        return FakeRecognizer(self.script or {2: ("final", "hello there")})
