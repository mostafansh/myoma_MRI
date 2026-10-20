"""Pure-Python coverage for the on-image FIGO card helpers."""

from __future__ import annotations

import unittest

from MyomaReviewLiteLib import ReviewState
from MyomaReviewLiteLib.figo_card import card_position, figo_label, missing_figo_ids


class FigoLabelTests(unittest.TestCase):
    def test_figo_label_formats_every_kind(self):
        self.assertEqual(figo_label(None), "FIGO ?")
        self.assertEqual(figo_label({"kind": "standard", "type": 3}), "FIGO 3")
        self.assertEqual(figo_label({"kind": "standard", "type": 8, "location": "round_ligament"}), "FIGO 8 (round ligament)")
        self.assertEqual(figo_label({"kind": "hybrid", "endometrial": 2, "serosal": 5}), "Hybrid 2-5")
        self.assertEqual(figo_label({"kind": "uncertain", "reason": "otherwise_not_classifiable"}), "Uncertain")

    def test_figo_label_handles_full_to_dict_shape(self):
        full = {"kind": "standard", "type": 4, "location": None, "endometrial": None, "serosal": None, "reason": None}
        self.assertEqual(figo_label(full), "FIGO 4")


class MissingFigoTests(unittest.TestCase):
    def test_missing_figo_ids_lists_unclassified_in_order(self):
        state = ReviewState.new("s", "r", "c")
        a, b, c = (state.add_lesion((i, 0, 0), (1, 1, 1)) for i in range(3))
        state.set_figo(b, {"kind": "standard", "type": 1})
        self.assertEqual(missing_figo_ids(state), [a, c])


class CardPositionTests(unittest.TestCase):
    def test_card_position_prefers_the_right_of_the_box(self):
        # view 400x300, box x 100..150, y(bottom-up) 200..250 -> top in Qt = 300-250 = 50
        self.assertEqual(card_position((100, 200, 150, 250), (120, 90), (400, 300)), (158, 50))

    def test_card_position_flips_left_near_the_right_edge(self):
        self.assertEqual(card_position((300, 200, 380, 250), (120, 90), (400, 300)), (172, 50))

    def test_card_position_clamps_inside_the_view(self):
        x, y = card_position((10, 0, 390, 20), (120, 90), (400, 300))
        self.assertTrue(0 <= x <= 400 - 120 and 0 <= y <= 300 - 90)

    def test_card_position_divides_by_device_pixel_ratio(self):
        # same box in physical pixels at dpr 2 -> same logical answer as dpr 1
        self.assertEqual(card_position((200, 400, 300, 500), (120, 90), (400, 300), device_pixel_ratio=2.0),
                         card_position((100, 200, 150, 250), (120, 90), (400, 300)))


if __name__ == "__main__":
    unittest.main()
