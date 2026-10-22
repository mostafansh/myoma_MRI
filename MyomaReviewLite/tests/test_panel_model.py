"""Pure-Python coverage for the panel's hint line and queue drawer decisions."""

from __future__ import annotations

import unittest

from MyomaReviewLiteLib.queue import QueueEntry, QueueStatus
from MyomaReviewLiteLib.panel_model import (
    CASE_FILTERS,
    DEFAULT_CASE_FILTER,
    bottom_hint,
    case_filter_counts,
    case_groups,
    matches_case_search,
    queue_strip_text,
)


class BottomHintTests(unittest.TestCase):
    def test_bottom_hint_says_what_blocks_reveal(self):
        self.assertEqual(bottom_hint("blind", [], 0, False), ("Box at least one myoma, or confirm there are none", "muted"))
        self.assertEqual(bottom_hint("blind", ["M02"], 3, False), ("M02 needs a FIGO type before you can reveal", "warning"))
        self.assertEqual(bottom_hint("blind", ["M02", "M03"], 3, False), ("M02, M03 need a FIGO type before you can reveal", "warning"))
        self.assertEqual(bottom_hint("blind", [], 3, False), ("All myomas have a FIGO type. Revealing locks your blind read.", "ok"))
        self.assertEqual(bottom_hint("blind", [], 0, True), ("No myomas confirmed. Revealing locks your blind read.", "ok"))
        self.assertEqual(bottom_hint("ai_revealed", [], 3, False), ("Correct the AI labels, then export and complete.", "muted"))


class QueueDrawerTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            QueueEntry("sub-1", QueueStatus.COMPLETE),
            QueueEntry("sub-2", QueueStatus.NEW),
            QueueEntry("sub-3", QueueStatus.ATTENTION, "bad"),
            QueueEntry("sub-4", QueueStatus.IN_PROGRESS, has_question=True),
        ]

    def test_filters(self):
        self.assertEqual(CASE_FILTERS, (
            ("To do", "todo"), ("Finished", "finished"), ("Attention", "attention"),
            ("Questions", "questions"), ("All", "all"),
        ))

    def test_questions_filter_lists_only_cases_with_a_question(self):
        self.assertEqual([e.case_id for _, g in case_groups(self.entries, "questions", "") for e in g], ["sub-4"])
        counts = case_filter_counts(self.entries)
        self.assertEqual(counts["questions"], 1)
        self.assertEqual(counts["todo"], 3)
        self.assertEqual(counts["all"], 4)
        self.assertEqual(DEFAULT_CASE_FILTER, "todo")

    def test_case_groups_order_and_filters(self):
        groups = case_groups(self.entries, "todo", "")
        self.assertEqual([title for title, _ in groups], ["NEEDS ATTENTION", "IN PROGRESS", "NEW"])
        self.assertEqual([e.case_id for e in case_groups(self.entries, "finished", "")[0][1]], ["sub-1"])
        self.assertEqual([e.case_id for _, g in case_groups(self.entries, "attention", "") for e in g], ["sub-3"])
        self.assertEqual([t for t, _ in case_groups(self.entries, "all", "")][-1], "FINISHED")
        self.assertEqual([e.case_id for _, g in case_groups(self.entries, "all", "4") for e in g], ["sub-4"])

    def test_case_groups_is_empty_when_nothing_matches(self):
        self.assertEqual(case_groups([QueueEntry("sub-1", QueueStatus.NEW)], "todo", "zzz"), [])

    def test_matches_case_search_by_id_or_number(self):
        self.assertTrue(matches_case_search("sub-00014", ""))
        self.assertTrue(matches_case_search("sub-00014", "14"))
        self.assertTrue(matches_case_search("sub-00014", "SUB-000"))
        self.assertFalse(matches_case_search("sub-00014", "15"))

    def test_queue_strip_text(self):
        self.assertEqual(
            queue_strip_text({QueueStatus.COMPLETE: 12, QueueStatus.NEW: 25, QueueStatus.ATTENTION: 3, QueueStatus.IN_PROGRESS: 0}),
            "12 of 40 finished · 3 need attention",
        )
        self.assertEqual(
            queue_strip_text({QueueStatus.COMPLETE: 1, QueueStatus.NEW: 1, QueueStatus.ATTENTION: 1, QueueStatus.IN_PROGRESS: 0}),
            "1 of 3 finished · 1 needs attention",
        )
        self.assertEqual(
            queue_strip_text({QueueStatus.COMPLETE: 0, QueueStatus.NEW: 2, QueueStatus.ATTENTION: 0, QueueStatus.IN_PROGRESS: 0}),
            "0 of 2 finished",
        )


if __name__ == "__main__":
    unittest.main()
