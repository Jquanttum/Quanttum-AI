"""Spoken replies using the voice built into Windows.

No extra packages: PowerShell's System.Speech does the talking. The text goes
in through standard input, never on the command line, so nothing in a reply
can be mistaken for a command.
"""

import re
import shutil
import subprocess
import sys

_SCRIPT = (
    "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName System.Speech; "
    "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$voice.Speak([Console]::In.ReadToEnd())"
)


def speakable(text, max_chars=500):
    """Make a reply pleasant to hear: no markdown marks, no wall of text."""

    text = re.sub(r"[*_`#>]+", " ", text)
    text = " ".join(text.split())

    if len(text) <= max_chars:
        return text

    cut = text[:max_chars]
    sentence_end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))

    return cut[: sentence_end + 1] if sentence_end > max_chars // 2 else cut


class Speaker:
    def __init__(self, platform=sys.platform, which=shutil.which,
                 popen=subprocess.Popen, max_chars=500):
        self._platform = platform
        self._which = which
        self._popen = popen
        self.max_chars = max_chars
        self._process = None

    def available_reason(self):
        """"" if speaking works here, otherwise why not."""

        if self._platform != "win32":
            return "spoken replies need Windows"

        if not self._which("powershell"):
            return "PowerShell wasn't found"

        return ""

    @property
    def speaking(self):
        return self._process is not None and self._process.poll() is None

    def speak(self, text):
        """Start speaking (does not wait). Replaces anything already spoken."""

        self.stop()

        if self.available_reason():
            return False

        text = speakable(text, self.max_chars)

        if not text:
            return False

        command = [self._which("powershell"), "-NoLogo", "-NoProfile",
                   "-NonInteractive", "-Command", _SCRIPT]

        try:
            self._process = self._popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._process.stdin.write(text.encode("utf-8"))
            self._process.stdin.close()
        except OSError:
            self._process = None
            return False

        return True

    def stop(self):
        process, self._process = self._process, None

        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
