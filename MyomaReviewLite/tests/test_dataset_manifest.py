"""Tests for single-file dataset manifests and case selection."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from MyomaReviewLite import MyomaReviewLiteLogic
from MyomaReviewLiteLib import CaseManifest, DatasetManifest, ManifestError, load_manifest


class DatasetManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _case_payload(self, case_id: str) -> dict[str, object]:
        case_root = self.root / "images" / case_id
        case_root.mkdir(parents=True, exist_ok=True)
        volumes = {}
        for view in ("sagittal", "axial", "coronal"):
            path = case_root / f"{case_id}_{view}_t2.nii.gz"
            path.touch()
            volumes[view] = str(path)
        mask = case_root / f"{case_id}_mask_0.nii.gz"
        mask.touch()
        return {
            "case_id": case_id,
            "volumes": volumes,
            "ai_mask": str(mask),
        }

    def _dataset_payload(self, case_ids: tuple[str, ...] = ("sub-00007", "sub-00008", "sub-00009")) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "manifest_type": "dataset",
            "study_id": "LUIS_FIGO",
            "reader_id": "test_reader",
            "output_root": str(self.root / "results"),
            "cases": [self._case_payload(case_id) for case_id in case_ids],
        }

    def _write_json(self, payload: dict[str, object], filename: str = "dataset.json") -> Path:
        path = self.root / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_loads_all_cases_and_resolves_shared_metadata(self) -> None:
        manifest = DatasetManifest.from_file(self._write_json(self._dataset_payload()))

        self.assertEqual(manifest.case_ids, ("sub-00007", "sub-00008", "sub-00009"))
        self.assertEqual(manifest.study_id, "LUIS_FIGO")
        self.assertEqual(manifest.reader_id, "test_reader")
        self.assertEqual(manifest.case_by_id("sub-00008").case_id, "sub-00008")
        self.assertEqual(manifest.case_by_id("sub-00008").output_root, (self.root / "results").resolve())

    def test_rejects_duplicate_case_ids(self) -> None:
        payload = self._dataset_payload(("sub-00007", "sub-00007"))

        with self.assertRaisesRegex(ManifestError, "unique"):
            DatasetManifest.from_file(self._write_json(payload))

    def test_rejects_empty_cases(self) -> None:
        payload = self._dataset_payload()
        payload["cases"] = []

        with self.assertRaisesRegex(ManifestError, "at least one"):
            DatasetManifest.from_file(self._write_json(payload))

    def test_rejects_case_with_missing_volume(self) -> None:
        payload = self._dataset_payload(("sub-00007",))
        case = payload["cases"][0]
        del case["volumes"]["coronal"]

        with self.assertRaisesRegex(ManifestError, "coronal"):
            DatasetManifest.from_file(self._write_json(payload))

    def test_legacy_single_case_manifest_still_loads(self) -> None:
        case = self._case_payload("sub-00007")
        payload = {
            "schema_version": "1.0",
            "study_id": "LUIS_FIGO",
            "reader_id": "test_reader",
            "case_id": case["case_id"],
            "volumes": case["volumes"],
            "ai_mask": case["ai_mask"],
            "output_root": str(self.root / "results"),
        }

        manifest = CaseManifest.from_file(self._write_json(payload, "legacy.json"))

        self.assertEqual(manifest.case_id, "sub-00007")

    def test_source_root_resolves_portable_sources_without_moving_results(self) -> None:
        source_root = self.root / "portable source"
        reader_root = self.root / "reader"
        case_id = "sub-00007"
        case_root = source_root / case_id
        case_root.mkdir(parents=True)
        relative_volumes = {}
        for view, short_name in (
            ("sagittal", "sag"),
            ("axial", "ax"),
            ("coronal", "cor"),
        ):
            relative = f"{case_id}/{case_id}_{short_name}_t2.nii.gz"
            (source_root / relative).touch()
            relative_volumes[view] = relative
        relative_mask = f"{case_id}/{case_id}_mask_0.nii.gz"
        (source_root / relative_mask).touch()
        payload = {
            "schema_version": "1.0",
            "manifest_type": "dataset",
            "study_id": "LUIS_FIGO",
            "reader_id": "test_reader",
            "output_root": ".",
            "cases": [
                {
                    "case_id": case_id,
                    "volumes": relative_volumes,
                    "ai_mask": relative_mask,
                }
            ],
        }
        reader_root.mkdir()
        manifest_path = reader_root / "queue.json"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        from_file = DatasetManifest.from_file(manifest_path, source_root=source_root)
        from_dict = DatasetManifest.from_dict(
            payload, manifest_path, source_root=source_root
        )

        for manifest in (from_file, from_dict):
            self.assertEqual(manifest.output_root, reader_root.resolve())
            self.assertEqual(
                manifest.cases[0].volumes["sagittal"],
                (source_root / relative_volumes["sagittal"]).resolve(),
            )
            self.assertEqual(
                manifest.cases[0].ai_mask, (source_root / relative_mask).resolve()
            )

    def test_load_manifest_source_root_keeps_legacy_single_case_compatible(self) -> None:
        source_root = self.root / "source"
        reader_root = self.root / "reader"
        source_root.mkdir()
        reader_root.mkdir()
        case_id = "sub-00007"
        source_case = source_root / case_id
        source_case.mkdir()
        volumes = {}
        for view, short_name in (
            ("sagittal", "sag"),
            ("axial", "ax"),
            ("coronal", "cor"),
        ):
            relative = f"{case_id}/{case_id}_{short_name}_t2.nii.gz"
            (source_root / relative).touch()
            volumes[view] = relative
        mask = f"{case_id}/{case_id}_mask_0.nii.gz"
        (source_root / mask).touch()
        path = reader_root / "legacy.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "study_id": "LUIS_FIGO",
                    "reader_id": "test_reader",
                    "case_id": case_id,
                    "volumes": volumes,
                    "ai_mask": mask,
                    "output_root": ".",
                }
            ),
            encoding="utf-8",
        )

        manifest = load_manifest(path, source_root=source_root)

        self.assertIsInstance(manifest, CaseManifest)
        self.assertEqual(manifest.output_root, reader_root.resolve())
        self.assertEqual(manifest.ai_mask, (source_root / mask).resolve())

    def test_logic_uses_launcher_data_root_for_portable_queue(self) -> None:
        source_root = self.root / "configured source"
        reader_root = self.root / "reader"
        case_id = "sub-00007"
        source_case = source_root / case_id
        source_case.mkdir(parents=True)
        volumes = {}
        for view, short_name in (
            ("sagittal", "sag"),
            ("axial", "ax"),
            ("coronal", "cor"),
        ):
            relative = f"{case_id}/{case_id}_{short_name}_t2.nii.gz"
            (source_root / relative).touch()
            volumes[view] = relative
        mask = f"{case_id}/{case_id}_mask_0.nii.gz"
        (source_root / mask).touch()
        reader_root.mkdir()
        path = reader_root / "queue.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "manifest_type": "dataset",
                    "study_id": "LUIS_FIGO",
                    "reader_id": "test_reader",
                    "output_root": ".",
                    "cases": [
                        {
                            "case_id": case_id,
                            "volumes": volumes,
                            "ai_mask": mask,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        logic = MyomaReviewLiteLogic()

        with patch.dict(
            os.environ,
            {"MYOMA_REVIEW_LITE_DATA_ROOT": str(source_root)},
            clear=False,
        ):
            logic.open_manifest(path)

        self.assertEqual(logic.manifest.volumes["axial"], (source_root / volumes["axial"]).resolve())
        self.assertEqual(logic.storage.output_root, reader_root.resolve())

    def test_logic_opens_selected_dataset_case(self) -> None:
        path = self._write_json(self._dataset_payload())
        logic = MyomaReviewLiteLogic()

        logic.open_manifest(path, case_id="sub-00008")

        self.assertEqual(logic.case_id, "sub-00008")
        self.assertEqual(logic.available_case_ids, ("sub-00007", "sub-00008", "sub-00009"))
        self.assertEqual(logic.manifest_source, path.resolve())
        self.assertEqual(logic.storage.case_id, "sub-00008")


if __name__ == "__main__":
    unittest.main()
