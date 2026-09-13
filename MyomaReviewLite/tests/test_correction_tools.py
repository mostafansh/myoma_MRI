"""Tests for the pure cervix-boundary normal convention."""
from pathlib import Path
import math
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from MyomaReviewLiteLib.correction_tools import canonical_cervix_normal


class CanonicalCervixNormalTests(unittest.TestCase):
    def test_a_superior_pointing_normal_is_kept(self):
        self.assertEqual(canonical_cervix_normal((0.0, 0.0, 1.0)), (0.0, 0.0, 1.0))
        self.assertEqual(canonical_cervix_normal((1.0, 2.0, 0.5)), (1.0, 2.0, 0.5))

    def test_an_inferior_pointing_normal_is_flipped(self):
        self.assertEqual(canonical_cervix_normal((0.0, 0.0, -1.0)), (0.0, 0.0, 1.0))
        self.assertEqual(canonical_cervix_normal((1.0, 2.0, -0.5)), (-1.0, -2.0, 0.5))

    def test_flipping_is_idempotent(self):
        once = canonical_cervix_normal((3.0, -4.0, -5.0))
        self.assertEqual(canonical_cervix_normal(once), once)

    def test_a_normal_with_no_superior_inferior_component_is_unchanged(self):
        self.assertEqual(canonical_cervix_normal((1.0, 0.0, 0.0)), (1.0, 0.0, 0.0))
        self.assertEqual(canonical_cervix_normal((0.0, -1.0, 0.0)), (0.0, -1.0, 0.0))

    def test_magnitude_is_preserved_not_renormalized(self):
        result = canonical_cervix_normal((0.0, 0.0, -2.0))
        self.assertEqual(result, (0.0, 0.0, 2.0))

    def test_rejects_degenerate_or_invalid_input(self):
        for bad in ((0.0, 0.0, 0.0), (float("nan"), 0.0, 1.0), (float("inf"), 0.0, 1.0), (1.0, 2.0), None, ("x", "y", "z")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    canonical_cervix_normal(bad)


if __name__ == "__main__":
    unittest.main()
