"""Slicer-runtime regression test: drafts saved with the retired segments open.

Drafts saved before the cervix and cesarean scar niche labels were removed
still carry those two segments. Loading such a draft must drop them (their
voxels become background) and leave the fixed four-segment set, while any
other unexpected segment is still rejected.

Run with run_slicer_test.py and MYOMA_REVIEW_LITE_TEST_QUEUE set to a dataset
queue (as for test_reopen_slicer.py). Every file is written to a temporary
reader folder; the source queue is only read.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
import slicer


MODULE_ROOT = Path(__file__).resolve().parents[1]
for module_name in tuple(sys.modules):
    if module_name == "MyomaReviewLite" or module_name.startswith("MyomaReviewLiteLib"):
        del sys.modules[module_name]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != MODULE_ROOT]
sys.path.insert(0, str(MODULE_ROOT))

import MyomaReviewLite as bundled_module  # noqa: E402
from MyomaReviewLite import FIXED_SEGMENT_IDS, MyomaReviewLiteLogic  # noqa: E402
from MyomaReviewLiteLib import DatasetManifest, ValidationError  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT

EXPECTED_SEGMENTS = {
    "uterine_wall": "uterine wall",
    "uterine_cavity": "uterine cavity",
    "myoma": "myoma",
    "nabothian_cyst": "Nabothian cyst",
}


def _source_manifest() -> DatasetManifest:
    value = (
        os.environ.get("MYOMA_REVIEW_LITE_TEST_QUEUE", "").strip()
        or os.environ.get("MYOMA_REVIEW_LITE_CASE", "").strip()
    )
    if not value:
        raise RuntimeError(
            "set MYOMA_REVIEW_LITE_TEST_QUEUE to a dataset queue before running this test"
        )
    data_root = os.environ.get("MYOMA_REVIEW_LITE_DATA_ROOT", "").strip()
    return DatasetManifest.from_file(value, source_root=data_root or None)


def _write_queue(reader: Path) -> Path:
    case = _source_manifest().cases[0]
    queue_path = reader / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "manifest_type": "dataset",
                "study_id": "RETIRED_SEGMENT_DRAFT",
                "reader_id": "retired_segment_draft",
                "output_root": ".",
                "cases": [
                    {
                        "case_id": case.case_id,
                        "volumes": {name: str(path) for name, path in case.volumes.items()},
                        "ai_mask": str(case.ai_mask),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return queue_path


def _open_logic(queue_path: Path) -> MyomaReviewLiteLogic:
    logic = MyomaReviewLiteLogic()
    logic.open_manifest(queue_path)
    manifest = logic.manifest
    assert manifest is not None and logic.storage is not None
    logic.volume_nodes["sagittal"] = slicer.util.loadVolume(
        str(manifest.volumes["sagittal"]), {"singleFile": True}
    )
    logic.ai_mask_node = slicer.util.loadLabelVolume(
        str(manifest.ai_mask), {"singleFile": True}
    )
    assert logic.volume_nodes["sagittal"] is not None
    assert logic.ai_mask_node is not None
    return logic


def _write_old_draft(logic: MyomaReviewLiteLogic, extra_segment: str | None = None) -> np.ndarray:
    """Save a draft shaped like the pre-removal ones; return the cervix voxels."""

    reference = logic.volume_nodes["sagittal"]
    logic._create_native_segmentation()
    node = logic.segmentation_node
    segmentation = node.GetSegmentation()
    wall = slicer.util.arrayFromSegmentBinaryLabelmap(node, "uterine_wall", reference)
    cervix = np.zeros_like(wall)
    indices = np.argwhere(wall)[:50]
    assert len(indices), "the test case needs uterine wall voxels"
    cervix[tuple(indices.T)] = 1
    # The old Segment Editor set wrote cervix voxels over the wall they replaced.
    wall[tuple(indices.T)] = 0
    slicer.util.updateSegmentBinaryLabelmapFromArray(wall, node, "uterine_wall", reference)
    segmentation.AddEmptySegment("cervix", "cervix")
    slicer.util.updateSegmentBinaryLabelmapFromArray(cervix, node, "cervix", reference)
    segmentation.AddEmptySegment("cesarean_scar_niche", "cesarean scar niche")
    if extra_segment is not None:
        segmentation.AddEmptySegment(extra_segment, extra_segment)
    assert slicer.util.saveNode(node, str(logic.storage.draft_segmentation_path))
    slicer.mrmlScene.RemoveNode(node)
    logic.segmentation_node = None
    return cervix


def _segments(logic: MyomaReviewLiteLogic) -> dict[str, str]:
    segmentation = logic.segmentation_node.GetSegmentation()
    return {
        segmentation.GetNthSegmentID(index): segmentation.GetSegment(
            segmentation.GetNthSegmentID(index)
        ).GetName()
        for index in range(segmentation.GetNumberOfSegments())
    }


def test_draft_with_retired_segments_opens() -> None:
    slicer.mrmlScene.Clear(0)
    with tempfile.TemporaryDirectory() as temporary:
        reader = Path(temporary) / "reader"
        reader.mkdir()
        logic = _open_logic(_write_queue(reader))
        cervix = _write_old_draft(logic)

        try:
            logic._load_draft_segmentation()
        except Exception as exc:
            raise AssertionError(f"old draft did not open: {exc}") from exc

        assert _segments(logic) == EXPECTED_SEGMENTS, _segments(logic)
        logic.validate_correction_segments()
        reference = logic.volume_nodes["sagittal"]
        for segment_id in FIXED_SEGMENT_IDS.values():
            voxels = slicer.util.arrayFromSegmentBinaryLabelmap(
                logic.segmentation_node, segment_id, reference
            )
            assert not voxels[cervix.astype(bool)].any(), (
                f"retired cervix voxels landed in {segment_id}"
            )
        exported = np.asarray(slicer.util.arrayFromVolume(_export(logic)))
        assert set(np.unique(exported).tolist()) <= {0, 1, 2, 3, 4}
        assert not exported[cervix.astype(bool)].any(), "cervix voxels were not cleared"
        slicer.mrmlScene.Clear(0)


def _export(logic: MyomaReviewLiteLogic):
    working = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "retired test export")
    assert slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
        logic.segmentation_node,
        list(FIXED_SEGMENT_IDS.values()),
        working,
        logic.volume_nodes["sagittal"],
        slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY,
    )
    return working


def test_draft_with_other_unexpected_segment_is_rejected() -> None:
    slicer.mrmlScene.Clear(0)
    with tempfile.TemporaryDirectory() as temporary:
        reader = Path(temporary) / "reader"
        reader.mkdir()
        logic = _open_logic(_write_queue(reader))
        _write_old_draft(logic, extra_segment="ovary")
        try:
            logic._load_draft_segmentation()
        except ValidationError as exc:
            message = str(exc)
            assert "unexpected=ovary" in message, message
            assert "cervix" not in message and "cesarean" not in message, message
        else:
            raise AssertionError("a draft with an unknown segment was accepted")
        slicer.mrmlScene.Clear(0)


try:
    test_draft_with_retired_segments_opens()
    test_draft_with_other_unexpected_segment_is_rejected()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite retired segment draft: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
finally:
    slicer.mrmlScene.Clear(0)
