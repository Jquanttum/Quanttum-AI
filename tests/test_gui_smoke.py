"""Opt-in smoke test that drives the real window.

It opens an actual (briefly visible) window, so it only runs when asked:

    PowerShell:  $env:QUANTTUM_GUI_TESTS = "1"
                 py -3.13 -m unittest tests.test_gui_smoke -v

On a machine without a display (or without customtkinter) it is skipped.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.memory import Memory

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
        base = Path(self.tmp.name)
        self.settings_path = base / "settings.json"
        db_path = str(base / "gui.db")

        self.patches = [
            mock.patch.object(window, "Memory", lambda: Memory(db_path)),
            mock.patch.object(window, "SETTINGS_PATH", str(self.settings_path)),
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
        self.tmp.cleanup()

    def say(self, text):
        self.app.entry.insert(0, text)
        self.app.send_message()
        self.app.update()

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

    def test_show_features_renders(self):
        self.say("show features")
        self.assertIn("RAM monitor (ram_monitor)", self.chat())


if __name__ == "__main__":
    unittest.main()
