"""Regression coverage for reviewer annotations and blinded-record retention."""

from __future__ import annotations

import json
import tempfile
import unittest

from MyomaReviewLiteLib import (
    AI_LABEL_VALUES,
    CORRECTION_LABEL_NAMES,
    CaseStorage,
    ImmutableCaseError,
    ReviewState,
    SplitPlane,
    StorageError,
    ValidationError,
    validate_ai_mask_values,
    validate_correction_mask_values,
)


STAMP = "2026-09-06T08:00:00.000Z"
LATER = "2026-09-06T09:00:00.000Z"


def annotated_state() -> ReviewState:
    state = ReviewState.new("study", "reader", "case", started_at=STAMP)
    lesion_id = state.add_lesion((1, 2, 3), (4, 5, 6))
    state.set_figo(lesion_id, {"kind": "standard", "type": 3})
    return state


class ReviewerAnnotationTests(unittest.TestCase):
    def test_correction_labels_do_not_expand_ai_input_contract(self) -> None:
        self.assertEqual(AI_LABEL_VALUES, frozenset(range(5)))
        self.assertEqual(CORRECTION_LABEL_NAMES[5], "cervix")
        self.assertEqual(CORRECTION_LABEL_NAMES[6], "cesarean scar niche")
        validate_correction_mask_values(range(7))
        for invalid in (5, 6):
            with self.subTest(ai_label=invalid), self.assertRaises(ValidationError):
                validate_ai_mask_values([invalid])
        for invalid in (-1, 7, 3.5, True):
            with self.subTest(correction_label=invalid), self.assertRaises(ValidationError):
                validate_correction_mask_values([invalid])

    def test_differential_diagnosis_survives_geometry_and_figo_changes(self) -> None:
        state = annotated_state()
        state.set_differential_diagnosis("M01", "  Reader's differential\nSecond possibility  ")
        state.update_lesion("M01", (9, 8, 7), (6, 5, 4))
        state.set_figo("M01", {"kind": "standard", "type": 8, "location": "cervical"})
        restored = ReviewState.from_draft(json.loads(json.dumps(state.to_draft())))
        lesion = restored.lesions["M01"]
        self.assertEqual(lesion.differential_diagnosis, "Reader's differential\nSecond possibility")
        self.assertEqual(lesion.center_ras, (9, 8, 7))
        self.assertEqual(lesion.figo.location, "cervical")

    def test_revealed_edits_preserve_blinded_snapshot_and_timestamp(self) -> None:
        state = annotated_state()
        state.set_differential_diagnosis("M01", "Original differential")
        state.set_split_plane((1, 2, 3), (0, 0, 2))
        state.reveal(revealed_at=STAMP)
        original = json.loads(json.dumps(state.blinded_snapshot))
        state.set_figo("M01", {"kind": "standard", "type": 4})
        state.set_differential_diagnosis("M01", "Revised differential")
        state.set_split_plane((4, 5, 6), (0, 3, 0))
        added = state.add_lesion((2, 3, 4), (1, 1, 1))
        state.set_figo(added, {"kind": "uncertain", "reason": "otherwise_not_classifiable"})
        state.delete_lesion("M01")
        self.assertEqual(state.blinded_snapshot, original)
        self.assertEqual(state.ai_revealed_at, STAMP)
        self.assertFalse(state.can_reveal)
        final = state.complete(completed_at=LATER)
        self.assertEqual(final["blinded_snapshot"], original)
        self.assertEqual(final["lesions"][0]["lesion_id"], "M02")
        self.assertEqual(final["split_plane"]["origin_ras"], [4, 5, 6])

    def test_snapshot_serialization_is_detached(self) -> None:
        state = annotated_state()
        state.reveal(revealed_at=STAMP)
        payload = state.to_draft()
        payload["blinded_snapshot"]["lesions"][0]["figo"]["type"] = 7
        self.assertEqual(state.blinded_snapshot["lesions"][0]["figo"]["type"], 3)

    def test_legacy_revealed_draft_captures_original_before_first_correction(self) -> None:
        state = annotated_state()
        state.reveal(revealed_at=STAMP)
        legacy = state.to_draft()
        for key in ("blinded_snapshot", "split_plane", "next_lesion_number"):
            legacy.pop(key)
        legacy["lesions"][0].pop("differential_diagnosis")
        restored = ReviewState.from_draft(legacy)
        restored.set_figo("M01", {"kind": "standard", "type": 5})
        self.assertEqual(restored.blinded_snapshot["lesions"][0]["figo"]["type"], 3)
        self.assertEqual(restored.lesions["M01"].differential_diagnosis, "")
        self.assertEqual(restored.ai_revealed_at, STAMP)

    def test_legacy_blind_draft_remains_blind(self) -> None:
        legacy = annotated_state().to_draft()
        for key in ("blinded_snapshot", "split_plane", "next_lesion_number"):
            legacy.pop(key)
        restored = ReviewState.from_draft(legacy)
        self.assertIsNone(restored.blinded_snapshot)
        self.assertTrue(restored.can_reveal)

    def test_more_than_six_lesions_and_deleted_ids_are_not_reused(self) -> None:
        state = annotated_state()
        for number in range(2, 102):
            self.assertEqual(state.add_lesion((0, 0, 0), (1, 1, 1)), f"M{number:02d}")
        self.assertEqual(state.lesion_count, 101)
        state.delete_lesion("M101")
        restored = ReviewState.from_draft(state.to_draft())
        self.assertEqual(restored.add_lesion((0, 0, 0), (1, 1, 1)), "M102")

    def test_pending_correction_figo_does_not_lock_failed_completion(self) -> None:
        state = annotated_state()
        state.reveal(revealed_at=STAMP)
        added = state.add_lesion((0, 0, 0), (1, 1, 1))
        with self.assertRaises(ValidationError):
            state.complete(completed_at=LATER)
        self.assertEqual(state.phase, "ai_revealed")
        self.assertIsNone(state.completed_at)
        state.set_figo(added, {"kind": "standard", "type": 2})
        self.assertEqual(state.complete(completed_at=LATER)["status"], "complete")

    def test_removing_all_revealed_lesions_requires_zero_confirmation(self) -> None:
        state = annotated_state()
        state.reveal(revealed_at=STAMP)
        state.delete_lesion("M01")
        with self.assertRaises(ValidationError):
            state.complete(completed_at=LATER)
        state.confirm_zero_myomas(True)
        final = state.complete(completed_at=LATER)
        self.assertTrue(final["zero_myomas"])
        self.assertEqual(len(final["blinded_snapshot"]["lesions"]), 1)

    def test_split_plane_normalizes_and_rejects_invalid_geometry(self) -> None:
        plane = SplitPlane((1, 2, 3), (0, 3, 4))
        self.assertEqual(plane.normal_ras, (0.0, 0.6, 0.8))
        for origin, normal in (((0, 0, 0), (0, 0, 0)), ((float("nan"), 0, 0), (0, 0, 1)), ((0, 0, 0), (0, float("inf"), 0))):
            with self.subTest(origin=origin, normal=normal), self.assertRaises(ValidationError):
                SplitPlane(origin, normal)
        state = annotated_state()
        state.set_split_plane(plane.origin_ras, plane.normal_ras)
        restored = ReviewState.from_draft(state.to_draft())
        self.assertEqual(restored.split_plane, plane)
        restored.clear_split_plane()
        self.assertIsNone(restored.split_plane)

    def test_completed_records_block_all_annotation_mutations(self) -> None:
        state = annotated_state()
        state.reveal(revealed_at=STAMP)
        state.complete(completed_at=LATER)
        actions = (
            lambda: state.add_lesion((0, 0, 0), (1, 1, 1)),
            lambda: state.update_lesion("M01", (0, 0, 0), (1, 1, 1)),
            lambda: state.delete_lesion("M01"),
            lambda: state.set_figo("M01", {"kind": "standard", "type": 1}),
            lambda: state.set_differential_diagnosis("M01", "Revised"),
            lambda: state.set_split_plane((0, 0, 0), (0, 0, 1)),
            state.clear_split_plane,
        )
        for action in actions:
            with self.assertRaises(ValidationError):
                action()

    def test_draft_cannot_revert_a_revealed_record_to_blind(self) -> None:
        state = annotated_state()
        state.reveal(revealed_at=STAMP)
        payload = state.to_draft()
        payload["phase"] = "blind"
        with self.assertRaises(ValidationError):
            ReviewState.from_draft(payload)


class ReviewerStorageTests(unittest.TestCase):
    def test_extended_fields_persist_through_draft_and_publication(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = CaseStorage(root, "case")
            state = annotated_state()
            state.reveal(revealed_at=STAMP)
            storage.save_draft(state, b"synthetic segmentation")
            state.set_differential_diagnosis("M01", "Reviewer note")
            state.set_split_plane((1, 2, 3), (0, 0, -1))
            storage.save_draft(state)
            restored = storage.load_draft()
            self.assertEqual(restored.lesions["M01"].differential_diagnosis, "Reviewer note")
            self.assertEqual(restored.split_plane.normal_ras, (0, 0, -1))
            storage.publish_complete(restored.complete(completed_at=LATER), b"synthetic corrected labels")
            saved = storage.load_annotation()
            self.assertEqual(saved["label_names"]["5"], "cervix")
            self.assertEqual(saved["blinded_snapshot"]["lesions"][0]["figo"]["type"], 3)
            self.assertFalse(storage.draft_path.exists())
            with self.assertRaises(ImmutableCaseError):
                storage.save_draft(restored)

    def test_reveal_reset_is_rejected_before_segmentation_write(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = CaseStorage(root, "case")
            state = annotated_state()
            original_blind = ReviewState.from_draft(state.to_draft())
            state.reveal(revealed_at=STAMP)
            storage.save_draft(state, b"original")
            with self.assertRaises(StorageError):
                storage.save_draft(original_blind, b"replacement")
            self.assertEqual(storage.draft_segmentation_path.read_bytes(), b"original")
            self.assertEqual(storage.load_draft().phase, "ai_revealed")

    def test_mutation_of_saved_blinded_snapshot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = CaseStorage(root, "case")
            state = annotated_state()
            state.reveal(revealed_at=STAMP)
            storage.save_draft(state)
            state.blinded_snapshot["lesions"][0]["figo"]["type"] = 8
            state.blinded_snapshot["lesions"][0]["figo"]["location"] = "cervical"
            with self.assertRaisesRegex(StorageError, "original blinded"):
                storage.save_draft(state)
            with self.assertRaisesRegex(StorageError, "original blinded"):
                storage.publish_complete(state.complete(completed_at=LATER), b"corrected")
            self.assertFalse(storage.annotation_path.exists())
            self.assertFalse(storage.corrected_labelmap_path.exists())


if __name__ == "__main__":
    unittest.main()
