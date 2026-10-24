"""Pure tests for the studio lockdown (no Slicer needed)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "slicer" / "lockdown" / "studio_lockdown.py"
spec = importlib.util.spec_from_file_location("studio_lockdown", PATH)
studio_lockdown = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio_lockdown)


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
        return call


class LockdownTests(unittest.TestCase):
    def test_only_exact_one_turns_studio_mode_on(self):
        for value, expected in (("1", True), ("0", False), ("", False), ("true", False), ("yes", False)):
            with self.subTest(value=value):
                self.assertIs(studio_lockdown.is_studio_mode({"SLICER_STUDIO": value}), expected)
        self.assertFalse(studio_lockdown.is_studio_mode({}))

    def test_locked_actions_cover_file_window_and_settings(self):
        names = ["FileAddDataAction", "WindowPythonInteractorAction", "EditApplicationSettingsAction",
                 "ViewExtensionsManagerAction", "HelpAboutAction", "ModuleUndoAction", ""]
        self.assertEqual(studio_lockdown.locked_action_names(names), names[:5])

    def test_install_is_a_no_op_outside_studio(self):
        window = _Recorder()
        studio_lockdown.install({}, main_window=window)
        studio_lockdown.install({"SLICER_STUDIO": "0", "STUDIO_START_MODULE": "Data"}, main_window=window)
        self.assertEqual(window.calls, [])


if __name__ == "__main__":
    unittest.main()
