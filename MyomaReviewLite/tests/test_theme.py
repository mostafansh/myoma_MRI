"""Pure-Python checks on the panel's Warm monochrome theme."""

from __future__ import annotations

import unittest

from MyomaReviewLiteLib import theme


class ThemeTokenTests(unittest.TestCase):
    def test_tokens_are_warm_monochrome(self):
        self.assertEqual(
            (theme.BG, theme.SURFACE, theme.ACCENT, theme.ON_ACCENT, theme.ACCENT_SOFT),
            ("#1b1917", "#24211f", "#e8e2d9", "#1b1917", "#363230"),
        )

    def test_contrast_ratio_matches_known_values(self):
        self.assertAlmostEqual(theme.contrast_ratio("#ffffff", "#000000"), 21.0, places=1)
        self.assertAlmostEqual(theme.contrast_ratio("#777777", "#ffffff"), 4.48, places=2)

    def test_every_checked_pair_is_readable(self):
        for name, fg, bg in theme.CHECKED_PAIRS:
            with self.subTest(name):
                self.assertGreaterEqual(theme.contrast_ratio(fg, bg), 4.5)

    def test_stylesheets_use_the_bundled_font_first(self):
        self.assertIn('font-family: "Hanken Grotesk", "Segoe UI"', theme.PANEL_QSS)
        self.assertIn('font-family: "Hanken Grotesk", "Segoe UI"', theme.CARD_QSS)
        self.assertNotIn("#4f86d1", theme.PANEL_QSS + theme.CARD_QSS)


if __name__ == "__main__":
    unittest.main()
