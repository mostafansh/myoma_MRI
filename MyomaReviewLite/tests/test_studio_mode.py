import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from MyomaReviewLiteLib import studio_mode  # noqa: E402


class StudioModeTests(unittest.TestCase):
    def test_only_exact_one_turns_studio_mode_on(self):
        for value, expected in (("1", True), ("0", False), ("", False), ("true", False), ("yes", False)):
            with self.subTest(value=value):
                self.assertIs(studio_mode.is_studio_mode({"MYOMA_REVIEW_LITE_STUDIO": value}), expected)
        self.assertFalse(studio_mode.is_studio_mode({}))

    def test_locked_actions_cover_file_window_and_settings(self):
        names = ["FileAddDataAction", "WindowPythonInteractorAction", "EditApplicationSettingsAction",
                 "ViewExtensionsManagerAction", "HelpAboutAction", "MyomaUndoAction", ""]
        self.assertEqual(studio_mode.locked_action_names(names), names[:5])


if __name__ == "__main__":
    unittest.main()
