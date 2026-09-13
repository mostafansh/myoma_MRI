"""Tests for the pure Alt+drag window/level arithmetic."""

from __future__ import annotations

import math
import unittest

from MyomaReviewLiteLib.contrast_tools import (
    DEFAULT_SENSITIVITY,
    MAX_SENSITIVITY,
    MIN_SENSITIVITY,
    compute_window_level,
)


RANGE = (0.0, 1000.0)
WINDOW0 = 100.0
LEVEL0 = 50.0


def _drag(dx: float, dy: float, **options: object) -> tuple[float, float]:
    return compute_window_level(WINDOW0, LEVEL0, RANGE, dx, dy, **options)


class PurityTests(unittest.TestCase):
    def test_zero_delta_returns_the_inputs_unchanged(self) -> None:
        self.assertEqual(_drag(0, 0), (WINDOW0, LEVEL0))
        self.assertEqual(
            compute_window_level(37.5, -12.25, (-100.0, 900.0), 0, 0),
            (37.5, -12.25),
        )

    def test_the_function_has_no_hidden_state(self) -> None:
        first = _drag(120, -80)
        for _ in range(5):
            _drag(-500, 400)
        self.assertEqual(_drag(120, -80), first)

    def test_halves_of_a_drag_compose_into_the_whole_drag(self) -> None:
        # The window is a pure exponential in dy and the level is affine in dx,
        # so a drag evaluated from its start matches the same drag applied in
        # two steps; nothing drifts as the pointer moves.
        direct = _drag(100, -100)
        window, level = _drag(50, -50)
        stepped = compute_window_level(window, level, RANGE, 50, -50)

        self.assertAlmostEqual(direct[0], stepped[0], places=9)
        self.assertAlmostEqual(direct[1], stepped[1], places=9)


class DirectionTests(unittest.TestCase):
    def test_dragging_up_narrows_the_window_and_down_widens_it(self) -> None:
        up_window, up_level = _drag(0, 150)
        down_window, down_level = _drag(0, -150)

        self.assertLess(up_window, WINDOW0)
        self.assertGreater(down_window, WINDOW0)
        self.assertEqual(up_level, LEVEL0)
        self.assertEqual(down_level, LEVEL0)

    def test_dragging_right_lowers_the_level_and_left_raises_it(self) -> None:
        right_window, right_level = _drag(150, 0)
        left_window, left_level = _drag(-150, 0)

        self.assertLess(right_level, LEVEL0)
        self.assertGreater(left_level, LEVEL0)
        self.assertEqual(right_window, WINDOW0)
        self.assertEqual(left_window, WINDOW0)

    def test_inverted_flips_both_axes(self) -> None:
        plain = _drag(120, 90)
        inverted = _drag(120, 90, inverted=True)
        mirrored = _drag(-120, -90)

        self.assertAlmostEqual(inverted[0], mirrored[0], places=9)
        self.assertAlmostEqual(inverted[1], mirrored[1], places=9)
        self.assertGreater(inverted[0], plain[0])
        self.assertGreater(inverted[1], plain[1])

    def test_the_axes_are_independent(self) -> None:
        window, level = _drag(200, -140)

        self.assertAlmostEqual(window, _drag(0, -140)[0], places=9)
        self.assertAlmostEqual(level, _drag(200, 0)[1], places=9)


class TotalityTests(unittest.TestCase):
    def test_the_window_stays_strictly_positive_for_extreme_drags(self) -> None:
        for dx, dy in (
            (0, 1e6),
            (0, -1e6),
            (1e9, 1e9),
            (-1e9, -1e9),
            (0, 1e300),
            (0, -1e300),
        ):
            with self.subTest(dx=dx, dy=dy):
                window, level = _drag(dx, dy)
                self.assertGreater(window, 0.0)
                self.assertTrue(math.isfinite(window))
                self.assertTrue(math.isfinite(level))

    def test_degenerate_scalar_ranges_still_produce_sane_values(self) -> None:
        for scalar_range in (
            (0.0, 0.0),
            (7.0, 7.0),
            None,
            (),
            ("low", "high"),
            (float("nan"), 1.0),
            (float("-inf"), float("inf")),
        ):
            with self.subTest(scalar_range=scalar_range):
                window, level = compute_window_level(WINDOW0, LEVEL0, scalar_range, 10, 10)
                self.assertGreater(window, 0.0)
                self.assertTrue(math.isfinite(window))
                self.assertTrue(math.isfinite(level))

    def test_degenerate_window_and_level_fall_back_instead_of_raising(self) -> None:
        for window0, level0 in (
            (0.0, 0.0),
            (-25.0, 10.0),
            (float("nan"), float("nan")),
            (float("inf"), float("-inf")),
            (None, None),
            ("window", "level"),
        ):
            with self.subTest(window0=window0, level0=level0):
                window, level = compute_window_level(window0, level0, RANGE, 30, -30)
                self.assertGreater(window, 0.0)
                self.assertTrue(math.isfinite(window))
                self.assertTrue(math.isfinite(level))

    def test_non_finite_deltas_are_treated_as_no_movement(self) -> None:
        for dx, dy in ((float("nan"), 0), (0, float("nan")), (None, None), ("x", "y")):
            with self.subTest(dx=dx, dy=dy):
                self.assertEqual(_drag(dx, dy), (WINDOW0, LEVEL0))


class SensitivityTests(unittest.TestCase):
    def test_magnitude_grows_monotonically_with_sensitivity(self) -> None:
        window_shift = []
        level_shift = []
        for sensitivity in range(MIN_SENSITIVITY, MAX_SENSITIVITY + 1):
            window, level = _drag(100, 100, sensitivity=sensitivity)
            window_shift.append(WINDOW0 - window)
            level_shift.append(LEVEL0 - level)

        self.assertEqual(window_shift, sorted(window_shift))
        self.assertEqual(level_shift, sorted(level_shift))
        self.assertLess(window_shift[0], window_shift[-1])
        self.assertLess(level_shift[0], level_shift[-1])

    def test_the_default_sensitivity_is_the_neutral_middle(self) -> None:
        self.assertEqual(DEFAULT_SENSITIVITY, 5)
        self.assertEqual(_drag(80, -60), _drag(80, -60, sensitivity=DEFAULT_SENSITIVITY))

    def test_out_of_range_and_unusable_sensitivity_is_clamped(self) -> None:
        low = _drag(100, 100, sensitivity=MIN_SENSITIVITY)
        high = _drag(100, 100, sensitivity=MAX_SENSITIVITY)
        default = _drag(100, 100)

        self.assertEqual(_drag(100, 100, sensitivity=0), low)
        self.assertEqual(_drag(100, 100, sensitivity=-40), low)
        self.assertEqual(_drag(100, 100, sensitivity=999), high)
        for unusable in (float("nan"), float("inf"), None, "five"):
            with self.subTest(unusable=unusable):
                self.assertEqual(_drag(100, 100, sensitivity=unusable), default)


class ScaleInvarianceTests(unittest.TestCase):
    def test_the_window_ratio_is_independent_of_intensity_units(self) -> None:
        wide = compute_window_level(1000.0, 500.0, (0.0, 4000.0), 0, 100)
        narrow = compute_window_level(0.01, 0.005, (0.0, 0.04), 0, 100)

        self.assertAlmostEqual(wide[0] / 1000.0, narrow[0] / 0.01, places=9)

    def test_the_level_shift_is_a_constant_fraction_of_the_range(self) -> None:
        wide = compute_window_level(1000.0, 500.0, (0.0, 4000.0), 100, 0)
        narrow = compute_window_level(0.01, 0.005, (0.0, 0.04), 100, 0)

        self.assertAlmostEqual((500.0 - wide[1]) / 4000.0, (0.005 - narrow[1]) / 0.04, places=9)


if __name__ == "__main__":
    unittest.main()
