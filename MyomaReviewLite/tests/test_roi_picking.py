"""Coverage for the pure click-to-select geometry behind ROI handle reveal."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from MyomaReviewLiteLib.domain import ValidationError
from MyomaReviewLiteLib.roi import BoxGeometry, box_contains_point, pick_box_at_point


class BoxContainsPointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = BoxGeometry((10.0, -4.0, 2.0), (20.0, 8.0, 6.0))

    def test_interior_and_exact_faces_are_inside(self):
        self.assertTrue(box_contains_point(self.box, (10.0, -4.0, 2.0)))
        self.assertTrue(box_contains_point(self.box, (0.0, -8.0, -1.0)))
        self.assertTrue(box_contains_point(self.box, (20.0, 0.0, 5.0)))

    def test_points_beyond_any_axis_are_outside(self):
        self.assertFalse(box_contains_point(self.box, (20.001, -4.0, 2.0)))
        self.assertFalse(box_contains_point(self.box, (10.0, -8.001, 2.0)))
        self.assertFalse(box_contains_point(self.box, (10.0, -4.0, 5.001)))

    def test_tolerance_widens_the_box_symmetrically(self):
        self.assertFalse(box_contains_point(self.box, (21.0, -4.0, 2.0)))
        self.assertTrue(box_contains_point(self.box, (21.0, -4.0, 2.0), tolerance_mm=1.0))
        self.assertTrue(box_contains_point(self.box, (-1.0, -4.0, 2.0), tolerance_mm=1.0))

    def test_invalid_points_and_tolerances_are_rejected(self):
        with self.assertRaises(ValidationError):
            box_contains_point(self.box, (0.0, 0.0))
        with self.assertRaises(ValidationError):
            box_contains_point(self.box, (0.0, 0.0, float("nan")))
        with self.assertRaises(ValidationError):
            box_contains_point(self.box, (0.0, 0.0, 0.0), tolerance_mm=-1.0)


class PickBoxAtPointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.large = BoxGeometry((0.0, 0.0, 0.0), (40.0, 40.0, 40.0))
        self.small = BoxGeometry((5.0, 5.0, 5.0), (10.0, 10.0, 10.0))
        self.far = BoxGeometry((100.0, 0.0, 0.0), (4.0, 4.0, 4.0))
        self.candidates = [("M01", self.large), ("M02", self.small), ("M03", self.far)]

    def test_click_outside_every_box_selects_nothing(self):
        self.assertIsNone(pick_box_at_point((0.0, 0.0, 500.0), self.candidates))
        self.assertIsNone(pick_box_at_point((0.0, 0.0, 0.0), []))

    def test_click_inside_one_box_selects_it(self):
        self.assertEqual(pick_box_at_point((100.0, 0.0, 0.0), self.candidates), "M03")
        self.assertEqual(pick_box_at_point((-15.0, -15.0, -15.0), self.candidates), "M01")

    def test_overlapping_boxes_resolve_to_the_tightest_one(self):
        self.assertEqual(pick_box_at_point((5.0, 5.0, 5.0), self.candidates), "M02")

    def test_equal_sized_overlap_breaks_ties_on_identifier(self):
        twin_a = BoxGeometry((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
        twin_b = BoxGeometry((1.0, 0.0, 0.0), (10.0, 10.0, 10.0))
        pair = [("M07", twin_b), ("M04", twin_a)]
        self.assertEqual(pick_box_at_point((0.5, 0.0, 0.0), pair), "M04")
        self.assertEqual(pick_box_at_point((0.5, 0.0, 0.0), list(reversed(pair))), "M04")

    def test_a_click_on_a_slice_that_misses_the_box_depth_selects_nothing(self):
        # A slice offset outside the box depth must not reveal that box.
        self.assertIsNone(pick_box_at_point((5.0, 5.0, 21.0), [("M02", self.small)]))
        self.assertEqual(pick_box_at_point((5.0, 5.0, 9.0), [("M02", self.small)]), "M02")

    def test_tolerance_allows_clicking_the_drawn_edge(self):
        self.assertIsNone(pick_box_at_point((10.5, 5.0, 5.0), [("M02", self.small)]))
        self.assertEqual(
            pick_box_at_point((10.5, 5.0, 5.0), [("M02", self.small)], tolerance_mm=1.0), "M02"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
