"""Source-level checks that every read of the AI mask path goes through one method."""

from __future__ import annotations

import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "MyomaReviewLite.py").read_text(encoding="utf-8")


class AiMaskSeamTests(unittest.TestCase):
    def test_ai_mask_path_is_read_in_one_place(self):
        self.assertEqual(SOURCE.count("manifest.ai_mask"), 1)
        seam = SOURCE.index("def ai_mask_path(")
        next_def = SOURCE.index("\n    def ", seam + 1)
        self.assertTrue(seam < SOURCE.index("manifest.ai_mask") < next_def)

    def test_reveal_and_load_use_the_seam(self):
        for method in ("def reveal_ai(", "def load_ai_mask("):
            start = SOURCE.index(method)
            body = SOURCE[start : SOURCE.index("\n    def ", start + 1)]
            with self.subTest(method):
                self.assertIn("self.ai_mask_path()", body)


if __name__ == "__main__":
    unittest.main()
