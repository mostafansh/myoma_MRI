"""Tests for artifact-derived reader queue status and navigation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from MyomaReviewLiteLib import (
    DatasetManifest,
    QueueStatus,
    QueueSummary,
    ReviewState,
)


STAMP = "2026-08-11T08:00:00.000Z"


class QueueSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.output = self.root / "reader"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self, case_ids: tuple[str, ...]) -> DatasetManifest:
        cases = []
        for case_id in case_ids:
            case_root = self.source / case_id
            case_root.mkdir(parents=True)
            volumes = {}
            for plane in ("sagittal", "axial", "coronal"):
                path = case_root / f"{case_id}_{plane}_t2.nii.gz"
                path.write_bytes(b"image")
                volumes[plane] = str(path)
            mask = case_root / f"{case_id}_mask_0.nii.gz"
            mask.write_bytes(b"mask")
            cases.append({"case_id": case_id, "volumes": volumes, "ai_mask": str(mask)})
        payload = {
            "schema_version": "1.0",
            "manifest_type": "dataset",
            "study_id": "LUIS_FIGO",
            "reader_id": "reader_a",
            "output_root": str(self.output),
            "cases": cases,
        }
        path = self.root / "queue.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return DatasetManifest.from_file(path)

    def _write_draft(self, case_id: str) -> None:
        state = ReviewState.new("LUIS_FIGO", "reader_a", case_id, started_at=STAMP)
        directory = self.output / case_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "draft.json").write_text(json.dumps(state.to_draft()), encoding="utf-8")

    def _write_early_stop(self, case_id: str) -> None:
        state = ReviewState.new("LUIS_FIGO", "reader_a", case_id, started_at=STAMP)
        annotation = state.early_stop("cannot_assess", completed_at=STAMP)
        directory = self.output / case_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "annotations.json").write_text(json.dumps(annotation), encoding="utf-8")

    def _write_complete(self, case_id: str, *, corrected: bool = True) -> None:
        state = ReviewState.new("LUIS_FIGO", "reader_a", case_id, started_at=STAMP)
        state.confirm_zero_myomas(True)
        state.reveal(revealed_at=STAMP)
        annotation = state.complete(completed_at=STAMP)
        directory = self.output / case_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "annotations.json").write_text(json.dumps(annotation), encoding="utf-8")
        if corrected:
            (directory / "corrected_labels.nii.gz").write_bytes(b"labels")

    def test_scans_artifacts_and_sorts_cases_by_numeric_subject_id(self) -> None:
        manifest = self._manifest(
            ("sub-00010", "sub-00002", "sub-00003", "sub-00001", "sub-00004")
        )
        self._write_draft("sub-00002")
        self._write_complete("sub-00003")
        self._write_early_stop("sub-00004")

        summary = QueueSummary.scan(manifest)

        self.assertEqual(
            [(entry.case_id, entry.status) for entry in summary.entries],
            [
                ("sub-00001", QueueStatus.NEW),
                ("sub-00002", QueueStatus.IN_PROGRESS),
                ("sub-00003", QueueStatus.COMPLETE),
                ("sub-00004", QueueStatus.COMPLETE),
                ("sub-00010", QueueStatus.NEW),
            ],
        )
        self.assertEqual(summary.counts[QueueStatus.NEW], 2)
        self.assertEqual(summary.counts[QueueStatus.IN_PROGRESS], 1)
        self.assertEqual(summary.counts[QueueStatus.COMPLETE], 2)
        self.assertEqual(summary.counts[QueueStatus.ATTENTION], 0)

    def test_navigation_wraps_and_excludes_complete_and_attention_cases(self) -> None:
        manifest = self._manifest(
            ("sub-00001", "sub-00002", "sub-00003", "sub-00004", "sub-00005")
        )
        self._write_complete("sub-00001")
        self._write_draft("sub-00002")
        self._write_complete("sub-00003", corrected=False)
        self._write_early_stop("sub-00004")

        summary = QueueSummary.scan(manifest)

        self.assertEqual(summary.first_unfinished().case_id, "sub-00002")
        self.assertEqual(summary.next_unfinished("sub-00002").case_id, "sub-00005")
        self.assertEqual(summary.next_unfinished("sub-00005").case_id, "sub-00002")
        self.assertEqual(summary.entry("sub-00003").status, QueueStatus.ATTENTION)
        self.assertIn("corrected_labels", summary.entry("sub-00003").diagnostic)

    def test_malformed_conflicting_and_orphan_artifacts_require_attention(self) -> None:
        manifest = self._manifest(
            ("sub-00001", "sub-00002", "sub-00003", "sub-00004", "sub-00005")
        )
        malformed = self.output / "sub-00001"
        malformed.mkdir(parents=True)
        (malformed / "annotations.json").write_text("{broken", encoding="utf-8")
        self._write_draft("sub-00002")
        self._write_early_stop("sub-00002")
        orphan_draft = self.output / "sub-00003"
        orphan_draft.mkdir(parents=True)
        (orphan_draft / "draft.seg.nrrd").write_bytes(b"segmentation")
        orphan_final = self.output / "sub-00004"
        orphan_final.mkdir(parents=True)
        (orphan_final / "corrected_labels.nii.gz").write_bytes(b"labels")
        self._write_early_stop("sub-00005")
        (self.output / "sub-00005" / "corrected_labels.nii.gz").write_bytes(b"labels")

        summary = QueueSummary.scan(manifest)

        self.assertTrue(all(entry.status is QueueStatus.ATTENTION for entry in summary.entries))
        self.assertEqual(summary.counts[QueueStatus.ATTENTION], 5)
        self.assertIsNone(summary.first_unfinished())
        self.assertIsNone(summary.next_unfinished("sub-00003"))

    def test_all_complete_has_no_unfinished_case(self) -> None:
        manifest = self._manifest(("sub-00001", "sub-00002"))
        self._write_complete("sub-00001")
        self._write_early_stop("sub-00002")

        summary = QueueSummary.scan(manifest)

        self.assertIsNone(summary.first_unfinished())
        self.assertIsNone(summary.next_unfinished("sub-00001"))
        self.assertEqual(summary.counts[QueueStatus.COMPLETE], 2)


if __name__ == "__main__":
    unittest.main()
