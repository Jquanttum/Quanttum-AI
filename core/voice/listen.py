"""One round of listening: microphone audio in, recognised text out."""

import json
import time
from dataclasses import dataclass

from core.voice.audio import rms


@dataclass
class ListenResult:
    text: str
    seconds: float
    peak: float          # loudest moment, 0..1 (to tell "muted" from "unclear")
    reason: str          # final | timeout | silence | cancelled


def _text(raw, key):
    try:
        return json.loads(raw).get(key, "").strip()
    except (ValueError, AttributeError):
        return ""


def run_listen(mic, recognizer, on_partial=None, cancel=None, max_seconds=15.0,
               silence_timeout=8.0, clock=time.monotonic):
    """Read audio until a sentence is recognised.

    Stops when: the recogniser decides you finished speaking (final), nothing
    is heard for silence_timeout seconds (silence), max_seconds pass
    (timeout, returning whatever was heard so far), or cancel is set.
    """

    started = clock()
    peak = 0.0
    heard_speech = False
    last_partial = ""

    while True:
        elapsed = clock() - started

        if cancel is not None and cancel.is_set():
            return ListenResult("", elapsed, peak, "cancelled")

        if elapsed >= max_seconds:
            text = _text(recognizer.FinalResult(), "text")
            return ListenResult(text, elapsed, peak, "timeout")

        data = mic.read()
        peak = max(peak, rms(data))

        if recognizer.AcceptWaveform(data):
            text = _text(recognizer.Result(), "text")

            if text:
                return ListenResult(text, clock() - started, peak, "final")
        else:
            partial = _text(recognizer.PartialResult(), "partial")

            if partial:
                heard_speech = True

                if partial != last_partial and on_partial is not None:
                    on_partial(partial)
                    last_partial = partial

        if not heard_speech and elapsed >= silence_timeout:
            return ListenResult("", elapsed, peak, "silence")
