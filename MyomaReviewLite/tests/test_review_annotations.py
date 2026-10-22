"""Regression coverage for reviewer annotations and blinded-record retention."""

from __future__ import annotations

import json
import tempfile
import unittest

from MyomaReviewLiteLib import (
    AI_LABEL_VALUES,
    LABEL_NAMES,
    CaseStorage,
    ImmutableCaseError,
    ReviewState,
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
    def test_corrections_use_the_same_five_labels_as_the_ai_mask(self) -> None:
        self.assertEqual(AI_LABEL_VALUES, frozenset(range(5)))
        self.assertEqual(set(LABEL_NAMES), set(range(5)))
        validate_correction_mask_values(range(5))
        for invalid in (5, 6):
            with self.subTest(ai_label=invalid), self.assertRaises(ValidationError):
                validate_ai_mask_values([invalid])
        for invalid in (-1, 5, 6, 3.5, True):
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
        state.reveal(revealed_at=STAMP)
        original = json.loads(json.dumps(state.blinded_snapshot))
        state.set_figo("M01", {"kind": "standard", "type": 4})
        state.set_differential_diagnosis("M01", "Revised differential")
        added = state.add_lesion((2, 3, 4), (1, 1, 1))
        state.set_figo(added, {"kind": "uncertain", "reason": "otherwise_not_classifiable"})
        state.delete_lesion("M01")
        self.assertEqual(state.blinded_snapshot, original)
        self.assertEqual(state.ai_revealed_at, STAMP)
        self.assertFalse(state.can_reveal)
        final = state.complete(completed_at=LATER)
        self.assertEqual(final["blinded_snapshot"], original)
        self.assertEqual(final["lesions"][0]["lesion_id"], "M02")
        self.assertNotIn("split_plane", final)

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
        for key in ("blinded_snapshot", "next_lesion_number"):
            legacy.pop(key)
        legacy["lesions"][0].pop("differential_diagnosis")
        restored = ReviewState.from_draft(legacy)
        restored.set_figo("M01", {"kind": "standard", "type": 5})
        self.assertEqual(restored.blinded_snapshot["lesions"][0]["figo"]["type"], 3)
        self.assertEqual(restored.lesions["M01"].differential_diagnosis, "")
        self.assertEqual(restored.ai_revealed_at, STAMP)

    def test_legacy_blind_draft_remains_blind(self) -> None:
        legacy = annotated_state().to_draft()
        for key in ("blinded_snapshot", "next_lesion_number"):
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

    def test_a_saved_cervix_plane_is_rejected_but_an_empty_one_is_accepted(self) -> None:
        payload = annotated_state().to_draft()
        self.assertNotIn("split_plane", payload)
        payload["split_plane"] = None
        ReviewState.from_draft(payload)
        payload["split_plane"] = {"origin_ras": [0, 0, 0], "normal_ras": [0, 0, 1]}
        with self.assertRaises(ValidationError):
            ReviewState.from_draft(payload)

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
            storage.save_draft(state)
            restored = storage.load_draft()
            self.assertEqual(restored.lesions["M01"].differential_diagnosis, "Reviewer note")
            storage.publish_complete(restored.complete(completed_at=LATER), b"synthetic corrected labels")
            saved = storage.load_annotation()
            self.assertEqual(set(saved["label_names"]), {"0", "1", "2", "3", "4"})
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


def completed_storage(root: str) -> CaseStorage:
    storage = CaseStorage(root, "case")
    state = annotated_state()
    state.reveal(revealed_at=STAMP)
    state.add_lesion((7, 8, 9), (1, 1, 1))
    state.set_figo("M02", {"kind": "standard", "type": 5})
    storage.save_draft(state, b"draft segmentation")
    storage.publish_complete(state.complete(completed_at=LATER), b"corrected labels")
    return storage


class ReopenCompletedCaseTests(unittest.TestCase):
    def test_reopen_backs_up_results_and_resumes_after_reveal(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = completed_storage(root)
            original = storage.annotation_path.read_bytes()
            state = storage.reopen_complete(b"reopened segmentation")
            self.assertEqual(state.phase, "ai_revealed")
            self.assertIsNone(state.completed_at)
            self.assertEqual(state.ai_revealed_at, STAMP)
            self.assertEqual(sorted(state.lesions), ["M01", "M02"])
            self.assertEqual(len(state.blinded_snapshot["lesions"]), 1)
            self.assertFalse(storage.is_complete)
            self.assertFalse(storage.corrected_labelmap_path.exists())
            self.assertEqual(storage.draft_segmentation_path.read_bytes(), b"reopened segmentation")
            self.assertEqual(storage.load_draft().to_draft(), state.to_draft())
            backups = list((storage.case_directory / "reopened").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "annotations.reopened.json").read_bytes(), original)
            self.assertEqual(
                (backups[0] / "corrected_labels.reopened.nii.gz").read_bytes(), b"corrected labels"
            )

    def test_reopened_case_accepts_edits_and_completes_again(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = completed_storage(root)
            state = storage.reopen_complete()
            new_id = state.add_lesion((3, 3, 3), (2, 2, 2))
            self.assertEqual(new_id, "M03")
            state.set_figo(new_id, {"kind": "standard", "type": 2})
            state.set_figo("M01", {"kind": "standard", "type": 4})
            storage.save_draft(state)
            storage.publish_complete(state.complete(), b"second labels")
            saved = storage.load_annotation()
            self.assertEqual(saved["ai_revealed_at"], STAMP)
            self.assertEqual(saved["blinded_snapshot"]["lesions"][0]["figo"]["type"], 3)
            self.assertEqual([item["lesion_id"] for item in saved["lesions"]], ["M01", "M02", "M03"])
            self.assertEqual(saved["lesions"][0]["figo"]["type"], 4)

    def test_reopen_refuses_early_stop_and_incomplete_cases(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = CaseStorage(root, "case")
            with self.assertRaises(StorageError):
                storage.reopen_complete()
            state = ReviewState.new("study", "reader", "case", started_at=STAMP)
            storage.publish_early_stop(state.early_stop("wrong_image", completed_at=LATER))
            with self.assertRaises(ImmutableCaseError):
                storage.reopen_complete()
            self.assertTrue(storage.is_complete)


class CommentTests(unittest.TestCase):
    def test_case_and_myoma_comments_persist_and_survive_edits(self) -> None:
        state = annotated_state()
        state.set_comment("  Poor image quality in the fundus  ")
        state.set_lesion_comment("M01", " Possibly degenerating ")
        state.update_lesion("M01", (9, 8, 7), (6, 5, 4))
        state.set_figo("M01", {"kind": "standard", "type": 5})
        state.set_differential_diagnosis("M01", "Leiomyoma")
        restored = ReviewState.from_draft(json.loads(json.dumps(state.to_draft())))
        self.assertEqual(restored.comment, "Poor image quality in the fundus")
        self.assertEqual(restored.lesions["M01"].comment, "Possibly degenerating")
        restored.reveal(revealed_at=STAMP)
        self.assertEqual(restored.blinded_snapshot["lesions"][0]["comment"], "Possibly degenerating")
        restored.set_comment("Revised after AI")
        final = restored.complete(completed_at=LATER)
        self.assertEqual(final["comment"], "Revised after AI")
        self.assertEqual(final["lesions"][0]["comment"], "Possibly degenerating")
        self.assertEqual(final["lesions"][0]["differential_diagnosis"], "Leiomyoma")

    def test_drafts_without_comments_still_load(self) -> None:
        payload = annotated_state().to_draft()
        payload.pop("comment")
        payload["lesions"][0].pop("comment")
        restored = ReviewState.from_draft(payload)
        self.assertEqual(restored.comment, "")
        self.assertEqual(restored.lesions["M01"].comment, "")

    def test_completed_case_comments_are_read_only(self) -> None:
        state = annotated_state()
        state.reveal(revealed_at=STAMP)
        state.complete(completed_at=LATER)
        with self.assertRaises(ValidationError):
            state.set_comment("late")
        with self.assertRaises(ValidationError):
            state.set_lesion_comment("M01", "late")

    def test_early_stop_keeps_the_case_comment(self) -> None:
        state = ReviewState.new("study", "reader", "case", started_at=STAMP)
        state.set_comment("Only a pelvic scout was sent")
        self.assertEqual(state.early_stop("wrong_image", completed_at=LATER)["comment"], "Only a pelvic scout was sent")


class QuestionTests(unittest.TestCase):
    def test_case_and_myoma_questions_persist_into_the_final_record(self) -> None:
        state = annotated_state()
        self.assertFalse(state.has_questions)
        state.set_question("  Is the left lesion subserosal or pedunculated?  ")
        state.set_lesion_question("M01", " Type 2 or 3? ")
        self.assertTrue(state.has_questions)
        restored = ReviewState.from_draft(json.loads(json.dumps(state.to_draft())))
        self.assertEqual(restored.question, "Is the left lesion subserosal or pedunculated?")
        self.assertEqual(restored.lesions["M01"].question, "Type 2 or 3?")
        restored.reveal(revealed_at=STAMP)
        final = restored.complete(completed_at=LATER)
        self.assertEqual(final["question"], "Is the left lesion subserosal or pedunculated?")
        self.assertEqual(final["lesions"][0]["question"], "Type 2 or 3?")

    def test_a_tag_without_text_still_counts_and_can_be_removed(self) -> None:
        state = annotated_state()
        state.set_lesion_question("M01", "")
        self.assertTrue(state.has_questions)
        self.assertEqual(state.to_draft()["lesions"][0]["question"], "")
        state.set_lesion_question("M01", None)
        self.assertFalse(state.has_questions)
        self.assertIsNone(state.to_draft()["lesions"][0]["question"])

    def test_old_files_without_questions_still_load(self) -> None:
        payload = annotated_state().to_draft()
        payload.pop("question")
        payload["lesions"][0].pop("question")
        restored = ReviewState.from_draft(payload)
        self.assertEqual(restored.question, "")
        self.assertIsNone(restored.lesions["M01"].question)
        self.assertFalse(restored.has_questions)

    def test_completed_case_questions_are_read_only(self) -> None:
        state = annotated_state()
        state.reveal(revealed_at=STAMP)
        state.complete(completed_at=LATER)
        with self.assertRaises(ValidationError):
            state.set_question("late")
        with self.assertRaises(ValidationError):
            state.set_lesion_question("M01", "late")

    def test_early_stop_keeps_the_case_question(self) -> None:
        state = ReviewState.new("study", "reader", "case", started_at=STAMP)
        state.set_question("Is this the right series?")
        self.assertEqual(state.early_stop("wrong_image", completed_at=LATER)["question"], "Is this the right series?")


if __name__ == "__main__":
    unittest.main()
