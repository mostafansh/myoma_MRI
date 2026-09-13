"""Pure-Python tests for queue-aware case lifecycle and save transitions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from MyomaReviewLite import MyomaReviewLiteLogic
from MyomaReviewLiteLib import (
    QueueStatus,
    ReviewSaveCoordinator,
    ReviewState,
    TransitionBlocked,
)


STAMP = "2026-08-11T08:00:00.000Z"


class QueueAwareLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "reader"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self) -> Path:
        cases = []
        for case_id in ("sub-00010", "sub-00002", "sub-00001"):
            source = self.root / "source" / case_id
            source.mkdir(parents=True)
            volumes = {}
            for plane in ("sagittal", "axial", "coronal"):
                path = source / f"{case_id}_{plane}_t2.nii.gz"
                path.write_bytes(b"image")
                volumes[plane] = str(path)
            mask = source / f"{case_id}_mask_0.nii.gz"
            mask.write_bytes(b"mask")
            cases.append({"case_id": case_id, "volumes": volumes, "ai_mask": str(mask)})
        path = self.root / "queue.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "manifest_type": "dataset",
                    "study_id": "LUIS_FIGO",
                    "reader_id": "reader_a",
                    "output_root": str(self.output),
                    "cases": cases,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_draft(self, case_id: str) -> None:
        state = ReviewState.new("LUIS_FIGO", "reader_a", case_id, started_at=STAMP)
        directory = self.output / case_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "draft.json").write_text(json.dumps(state.to_draft()), encoding="utf-8")

    def _write_early_stop(self, case_id: str) -> None:
        state = ReviewState.new("LUIS_FIGO", "reader_a", case_id, started_at=STAMP)
        annotation = state.early_stop("wrong_image", completed_at=STAMP)
        directory = self.output / case_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "annotations.json").write_text(json.dumps(annotation), encoding="utf-8")

    def test_opening_dataset_selects_earliest_numeric_unfinished_case(self) -> None:
        path = self._manifest()
        self._write_early_stop("sub-00001")
        self._write_draft("sub-00002")
        logic = MyomaReviewLiteLogic()

        logic.open_manifest(path)

        self.assertEqual(logic.case_id, "sub-00002")
        self.assertEqual(logic.queue_summary.counts[QueueStatus.COMPLETE], 1)
        self.assertEqual(logic.queue_summary.counts[QueueStatus.IN_PROGRESS], 1)
        self.assertEqual(logic.queue_summary.counts[QueueStatus.NEW], 1)

    def test_final_outcome_refreshes_status_and_finds_next_unfinished(self) -> None:
        path = self._manifest()
        self._write_early_stop("sub-00001")
        self._write_draft("sub-00002")
        logic = MyomaReviewLiteLogic()
        logic.open_manifest(path)

        logic.publish_early_stop("cannot_assess")
        summary = logic.refresh_queue()

        self.assertEqual(summary.entry("sub-00002").status, QueueStatus.COMPLETE)
        self.assertEqual(logic.next_unfinished_case_id(), "sub-00010")

    def test_attention_case_is_not_opened_as_a_reviewable_case(self) -> None:
        path = self._manifest()
        directory = self.output / "sub-00001"
        directory.mkdir(parents=True)
        (directory / "annotations.json").write_text("{broken", encoding="utf-8")
        logic = MyomaReviewLiteLogic()

        with self.assertRaisesRegex(Exception, "Attention"):
            logic.open_manifest(path, case_id="sub-00001")


class ReviewSaveCoordinatorTests(unittest.TestCase):
    def test_autosave_writes_only_when_dirty_and_retries_after_failure(self) -> None:
        coordinator = ReviewSaveCoordinator()
        calls = []

        self.assertFalse(coordinator.autosave(lambda: calls.append("unexpected")))
        coordinator.mark_dirty()

        def fail() -> None:
            calls.append("fail")
            raise OSError("disk full")

        with self.assertRaisesRegex(OSError, "disk full"):
            coordinator.autosave(fail)
        self.assertTrue(coordinator.dirty)
        self.assertTrue(coordinator.autosave(lambda: calls.append("saved")))
        self.assertFalse(coordinator.dirty)
        self.assertEqual(calls, ["fail", "saved"])

    def test_unresolved_roi_and_failed_save_block_navigation(self) -> None:
        coordinator = ReviewSaveCoordinator()
        events = []

        with self.assertRaisesRegex(TransitionBlocked, "ROI"):
            coordinator.transition(
                unresolved_roi=True,
                save_current=lambda: events.append("save"),
                teardown_current=lambda: events.append("teardown"),
                open_target=lambda: events.append("open"),
            )
        self.assertEqual(events, [])

        def fail() -> None:
            events.append("save")
            raise OSError("write failed")

        with self.assertRaisesRegex(OSError, "write failed"):
            coordinator.transition(
                unresolved_roi=False,
                save_current=fail,
                teardown_current=lambda: events.append("teardown"),
                open_target=lambda: events.append("open"),
            )
        self.assertEqual(events, ["save"])

    def test_successful_navigation_saves_then_tears_down_then_opens(self) -> None:
        coordinator = ReviewSaveCoordinator()
        coordinator.mark_dirty()
        events = []

        coordinator.transition(
            unresolved_roi=False,
            save_current=lambda: events.append("save"),
            teardown_current=lambda: events.append("teardown"),
            open_target=lambda: events.append("open"),
        )

        self.assertEqual(events, ["save", "teardown", "open"])
        self.assertFalse(coordinator.dirty)


if __name__ == "__main__":
    unittest.main()
