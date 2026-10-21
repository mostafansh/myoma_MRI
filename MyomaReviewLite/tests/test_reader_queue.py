"""Tests for reader queue creation and the command-line status report."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import nibabel as nib
import numpy as np

from MyomaReviewLiteLib import CaseStorage, DatasetManifest, QueueStatus, QueueSummary, ReviewState
from MyomaReviewLiteLib.reader_queue import (
    QueueCreationError,
    create_queue,
    load_review_config,
    main,
    migrate_paths,
)


class ReaderQueueCreationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "source"
        self.reader_dir = self.root / "reader_a"
        self.launcher = self.root / "Start-MyomaReviewLite.ps1"
        self.launcher.write_text("# launcher fixture\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_case(
        self,
        case_id: str,
        *,
        mask_values: tuple[int, ...] = (0, 1, 2, 3, 4),
        axial_shape: tuple[int, int, int] = (3, 4, 5),
        mask_shape: tuple[int, int, int] = (3, 4, 5),
    ) -> None:
        directory = self.data_root / case_id
        directory.mkdir(parents=True)
        affine = np.eye(4)
        shapes = {
            "sag": (3, 4, 5),
            "ax": axial_shape,
            "cor": (3, 4, 5),
        }
        for short_name, shape in shapes.items():
            data = np.zeros(shape, dtype=np.int16)
            nib.save(
                nib.Nifti1Image(data, affine),
                directory / f"{case_id}_{short_name}_t2.nii.gz",
            )
        mask = np.resize(np.asarray(mask_values, dtype=np.int16), mask_shape)
        nib.save(
            nib.Nifti1Image(mask, affine),
            directory / f"{case_id}_mask_0.nii.gz",
        )

    def _create(self, **overrides: object):
        arguments = {
            "reader_id": "reader_a",
            "study_id": "LUIS_FIGO",
            "data_root": self.data_root,
            "reader_dir": self.reader_dir,
            "launcher_path": self.launcher,
        }
        arguments.update(overrides)
        return create_queue(**arguments)

    def test_creates_full_queue_with_dataset_relative_forward_slash_sources(self) -> None:
        self._make_case("sub-00010")
        self._make_case("sub-00002")

        result = self._create()

        payload = json.loads((self.reader_dir / "queue.json").read_text(encoding="utf-8"))
        self.assertEqual([case["case_id"] for case in payload["cases"]], ["sub-00002", "sub-00010"])
        self.assertEqual(payload["output_root"], ".")
        self.assertEqual(result.case_ids, ("sub-00002", "sub-00010"))
        for case in payload["cases"]:
            expected_prefix = case["case_id"] + "/"
            for source_path in (*case["volumes"].values(), case["ai_mask"]):
                self.assertTrue(source_path.startswith(expected_prefix), source_path)
                self.assertNotIn("\\", source_path)
                self.assertFalse(Path(source_path).is_absolute())
        command = (self.reader_dir / "Start Review.cmd").read_text(encoding="utf-8")
        self.assertIn(r'-File "%~dp0..\Start-MyomaReviewLite.ps1"', command)
        self.assertNotIn(str(self.root), command)

    def test_subset_file_is_numeric_and_matching_rerun_is_idempotent(self) -> None:
        for case_id in ("sub-00001", "sub-00002", "sub-00010"):
            self._make_case(case_id)
        case_list = self.root / "subset.txt"
        case_list.write_text("sub-00010\nsub-00002\n", encoding="utf-8")

        first = self._create(case_list=case_list)
        queue_bytes = (self.reader_dir / "queue.json").read_bytes()
        second = self._create(case_list=case_list)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.case_ids, ("sub-00002", "sub-00010"))
        self.assertEqual((self.reader_dir / "queue.json").read_bytes(), queue_bytes)

    def test_rejects_duplicate_and_unknown_subset_cases(self) -> None:
        self._make_case("sub-00001")

        with self.assertRaisesRegex(QueueCreationError, "duplicate"):
            self._create(case_list="sub-00001,sub-00001")
        with self.assertRaisesRegex(QueueCreationError, "unknown"):
            self._create(case_list="sub-99999")

    def test_accepts_axial_and_coronal_on_their_own_grids(self) -> None:
        self._make_case("sub-00001", axial_shape=(6, 7, 2))

        result = self._create()

        self.assertEqual(result.case_ids, ("sub-00001",))

    def test_rejects_wrong_filename_geometry_and_mask_labels(self) -> None:
        self._make_case("sub-00001")
        expected = self.data_root / "sub-00001" / "sub-00001_cor_t2.nii.gz"
        expected.rename(expected.with_name("unexpected_coronal.nii.gz"))
        with self.assertRaisesRegex(QueueCreationError, "expected source file"):
            self._create()

        self.temporary.cleanup()
        self.setUp()
        self._make_case("sub-00001", mask_shape=(2, 4, 5))
        with self.assertRaisesRegex(QueueCreationError, "mask geometry"):
            self._create()

        self.temporary.cleanup()
        self.setUp()
        self._make_case("sub-00001", mask_values=(0, 5))
        with self.assertRaisesRegex(QueueCreationError, "labels 0 through 4"):
            self._create()

    def test_rejects_output_inside_source_and_conflicting_queue(self) -> None:
        self._make_case("sub-00001")
        with self.assertRaisesRegex(QueueCreationError, "inside the source"):
            self._create(reader_dir=self.data_root / "reader")

        self._create()
        queue_path = self.reader_dir / "queue.json"
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        payload["reader_id"] = "another_reader"
        queue_path.write_text(json.dumps(payload), encoding="utf-8")
        (self.reader_dir / "sub-00001").mkdir()
        (self.reader_dir / "sub-00001" / "draft.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(QueueCreationError, "conflicting"):
            self._create()

    def test_missing_launcher_does_not_leave_a_partial_queue(self) -> None:
        self._make_case("sub-00001")

        with self.assertRaisesRegex(QueueCreationError, "launcher"):
            self._create(launcher_path=self.root / "missing-launcher.ps1")

        self.assertFalse((self.reader_dir / "queue.json").exists())

    def test_status_json_reports_artifact_derived_counts(self) -> None:
        self._make_case("sub-00001")
        self._make_case("sub-00002")
        self._create()
        case_directory = self.reader_dir / "sub-00001"
        case_directory.mkdir()
        draft = {
            "schema_version": "1.0",
            "study_id": "LUIS_FIGO",
            "reader_id": "reader_a",
            "case_id": "sub-00001",
            "phase": "blind",
            "started_at": "2026-08-11T08:00:00.000Z",
            "ai_revealed_at": None,
            "completed_at": None,
            "zero_myomas": False,
            "lesions": [],
        }
        (case_directory / "draft.json").write_text(json.dumps(draft), encoding="utf-8")

        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "status",
                    "--reader-dir",
                    str(self.reader_dir),
                    "--data-root",
                    str(self.data_root),
                    "--json",
                ]
            )
        report = json.loads(output.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["counts"], {"attention": 0, "complete": 0, "in_progress": 1, "new": 1})
        self.assertEqual(report["first_unfinished"], "sub-00001")

    def test_two_readers_resume_and_complete_independently(self) -> None:
        self._make_case("sub-00001")
        self._make_case("sub-00002")
        reader_b = self.root / "reader_b"
        self._create()
        create_queue(
            reader_id="reader_b",
            study_id="LUIS_FIGO",
            data_root=self.data_root,
            reader_dir=reader_b,
            launcher_path=self.launcher,
        )
        manifest_a = DatasetManifest.from_file(
            self.reader_dir / "queue.json", source_root=self.data_root
        )
        manifest_b = DatasetManifest.from_file(
            reader_b / "queue.json", source_root=self.data_root
        )
        state_a = ReviewState.new("LUIS_FIGO", "reader_a", "sub-00001", started_at="2026-08-11T08:00:00.000Z")
        state_b = ReviewState.new("LUIS_FIGO", "reader_b", "sub-00001", started_at="2026-08-11T09:00:00.000Z")
        state_b.add_lesion((1, 2, 3), (4, 5, 6))
        state_b.set_figo("M01", {"kind": "standard", "type": 3})
        storage_a = CaseStorage(self.reader_dir, "sub-00001")
        storage_b = CaseStorage(reader_b, "sub-00001")
        storage_a.save_draft(state_a)
        storage_b.save_draft(state_b)

        annotation_a = state_a.early_stop("cannot_assess", completed_at="2026-08-11T10:00:00.000Z")
        storage_a.publish_early_stop(annotation_a)

        summary_a = QueueSummary.scan(manifest_a)
        summary_b = QueueSummary.scan(manifest_b)
        self.assertEqual(summary_a.entry("sub-00001").status, QueueStatus.COMPLETE)
        self.assertEqual(summary_b.entry("sub-00001").status, QueueStatus.IN_PROGRESS)
        resumed_b = storage_b.load_draft()
        self.assertIsNotNone(resumed_b)
        self.assertEqual(resumed_b.started_at, "2026-08-11T09:00:00.000Z")
        self.assertEqual(resumed_b.active_lesions[0].lesion_id, "M01")
        self.assertFalse((reader_b / "sub-00001" / "annotations.json").exists())

    def test_config_resolves_relative_and_absolute_dataset_roots(self) -> None:
        package_root = self.root / "package with spaces"
        relative_data = package_root / "data"
        absolute_data = self.root / "external data"
        package_root.mkdir()
        relative_data.mkdir()
        absolute_data.mkdir()
        config_path = package_root / "review-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "dataset_root": "data",
                    "slicer_path": "",
                }
            ),
            encoding="utf-8",
        )

        relative_config = load_review_config(config_path)
        self.assertEqual(relative_config.dataset_root, relative_data.resolve())

        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "dataset_root": str(absolute_data),
                    "slicer_path": "",
                }
            ),
            encoding="utf-8",
        )
        absolute_config = load_review_config(config_path)
        self.assertEqual(absolute_config.dataset_root, absolute_data.resolve())

    def test_config_rejects_malformed_values_and_missing_dataset(self) -> None:
        config_path = self.root / "review-config.json"
        invalid_payloads = (
            "not json",
            json.dumps({"schema_version": "1.0", "slicer_path": ""}),
            json.dumps(
                {
                    "schema_version": "2.0",
                    "dataset_root": str(self.data_root),
                    "slicer_path": "",
                }
            ),
            json.dumps(
                {
                    "schema_version": "1.0",
                    "dataset_root": 42,
                    "slicer_path": "",
                }
            ),
            json.dumps(
                {
                    "schema_version": "1.0",
                    "dataset_root": str(self.root / "missing"),
                    "slicer_path": "",
                }
            ),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                config_path.write_text(payload, encoding="utf-8")
                with self.assertRaises(QueueCreationError):
                    load_review_config(config_path)

    def test_cli_data_root_overrides_configured_dataset_root(self) -> None:
        self._make_case("sub-00001")
        self._create()
        config_path = self.root / "review-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "dataset_root": str(self.root / "configured-but-missing"),
                    "slicer_path": "",
                }
            ),
            encoding="utf-8",
        )

        code = main(
            [
                "status",
                "--reader-dir",
                str(self.reader_dir),
                "--config",
                str(config_path),
                "--data-root",
                str(self.data_root),
            ]
        )

        self.assertEqual(code, 0)

    def _make_legacy_absolute_queue(self) -> bytes:
        self._make_case("sub-00001")
        self._make_case("sub-00002")
        self._create()
        queue_path = self.reader_dir / "queue.json"
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        for case in payload["cases"]:
            case_id = case["case_id"]
            for view, short_name in (
                ("sagittal", "sag"),
                ("axial", "ax"),
                ("coronal", "cor"),
            ):
                case["volumes"][view] = str(
                    (self.data_root / case_id / f"{case_id}_{short_name}_t2.nii.gz").resolve()
                )
            case["ai_mask"] = str(
                (self.data_root / case_id / f"{case_id}_mask_0.nii.gz").resolve()
            )
        queue_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        result_dir = self.reader_dir / "sub-00001"
        result_dir.mkdir()
        artifact = result_dir / "draft.json"
        artifact.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "study_id": "LUIS_FIGO",
                    "reader_id": "reader_a",
                    "case_id": "sub-00001",
                    "phase": "blind",
                    "started_at": "2026-08-11T08:00:00.000Z",
                    "ai_revealed_at": None,
                    "completed_at": None,
                    "zero_myomas": False,
                    "lesions": [],
                }
            ),
            encoding="utf-8",
        )
        return artifact.read_bytes()

    def test_migration_is_atomic_idempotent_and_preserves_result_artifacts(self) -> None:
        artifact_bytes = self._make_legacy_absolute_queue()

        first = migrate_paths(reader_dir=self.reader_dir, data_root=self.data_root)
        queue_after_first = (self.reader_dir / "queue.json").read_bytes()
        second = migrate_paths(reader_dir=self.reader_dir, data_root=self.data_root)

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual((self.reader_dir / "queue.json").read_bytes(), queue_after_first)
        self.assertEqual(
            (self.reader_dir / "sub-00001" / "draft.json").read_bytes(),
            artifact_bytes,
        )
        payload = json.loads(queue_after_first)
        self.assertEqual(
            payload["cases"][0]["volumes"]["sagittal"],
            "sub-00001/sub-00001_sag_t2.nii.gz",
        )

    def test_migration_refuses_identity_order_source_and_malformed_conflicts(self) -> None:
        variants = ("reader", "order", "source", "malformed")
        for variant in variants:
            with self.subTest(variant=variant):
                self.temporary.cleanup()
                self.setUp()
                self._make_legacy_absolute_queue()
                queue_path = self.reader_dir / "queue.json"
                original = queue_path.read_bytes()
                if variant == "malformed":
                    queue_path.write_text("{", encoding="utf-8")
                    original = queue_path.read_bytes()
                else:
                    payload = json.loads(original)
                    if variant == "reader":
                        payload["reader_id"] = "another_reader"
                    elif variant == "order":
                        payload["cases"].reverse()
                    else:
                        payload["cases"][0]["volumes"]["sagittal"] = str(
                            self.data_root
                            / "sub-00002"
                            / "sub-00002_sag_t2.nii.gz"
                        )
                    queue_path.write_text(json.dumps(payload), encoding="utf-8")
                    original = queue_path.read_bytes()

                with self.assertRaises(QueueCreationError):
                    migrate_paths(reader_dir=self.reader_dir, data_root=self.data_root)

                self.assertEqual(queue_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
