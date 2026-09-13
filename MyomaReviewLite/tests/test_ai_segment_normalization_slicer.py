"""Slicer-runtime regression tests for generic AI segment normalization."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import slicer


MODULE_ROOT = Path(__file__).resolve().parents[1]
for module_name in tuple(sys.modules):
    if module_name == "MyomaReviewLite" or module_name.startswith("MyomaReviewLiteLib"):
        del sys.modules[module_name]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != MODULE_ROOT]
sys.path.insert(0, str(MODULE_ROOT))

import MyomaReviewLite as bundled_module  # noqa: E402
from MyomaReviewLite import FIXED_SEGMENT_IDS, MyomaReviewLiteLogic  # noqa: E402
from MyomaReviewLiteLib import DatasetManifest  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT


def _test_manifest() -> DatasetManifest:
    manifest_value = (
        os.environ.get("MYOMA_REVIEW_LITE_TEST_QUEUE", "").strip()
        or os.environ.get("MYOMA_REVIEW_LITE_CASE", "").strip()
    )
    if not manifest_value:
        raise RuntimeError(
            "set MYOMA_REVIEW_LITE_TEST_QUEUE to a dataset queue before running this test"
        )
    data_root = os.environ.get("MYOMA_REVIEW_LITE_DATA_ROOT", "").strip()
    manifest = DatasetManifest.from_file(
        manifest_value, source_root=data_root or None
    )
    if len(manifest.cases) < 2:
        raise RuntimeError("the Slicer normalization test queue requires at least two cases")
    return manifest


def _load_sources(logic: MyomaReviewLiteLogic) -> None:
    manifest = logic.manifest
    assert manifest is not None
    logic.volume_nodes["sagittal"] = slicer.util.loadVolume(
        str(manifest.volumes["sagittal"]), {"singleFile": True}
    )
    logic.ai_mask_node = slicer.util.loadLabelVolume(
        str(manifest.ai_mask), {"singleFile": True}
    )
    assert logic.volume_nodes["sagittal"] is not None
    assert logic.ai_mask_node is not None


def test_real_ai_masks_normalize_to_fixed_segments() -> None:
    expected = {
        "uterine_wall": "uterine wall",
        "uterine_cavity": "uterine cavity",
        "myoma": "myoma",
        "nabothian_cyst": "Nabothian cyst",
        "cervix": "cervix",
        "cesarean_scar_niche": "cesarean scar niche",
    }
    manifest = _test_manifest()
    for case in manifest.cases[:2]:
        slicer.mrmlScene.Clear(0)
        logic = MyomaReviewLiteLogic()
        logic.open_manifest(manifest.manifest_path, case_id=case.case_id)
        _load_sources(logic)
        source_values = set(
            int(value) for value in slicer.util.arrayFromVolume(logic.ai_mask_node).flat
        )

        try:
            logic._create_native_segmentation()
        except Exception as exc:
            raise AssertionError(f"{case.case_id} normalization failed: {exc}") from exc

        segmentation = logic.segmentation_node.GetSegmentation()
        actual = {
            segmentation.GetNthSegmentID(index): segmentation.GetSegment(
                segmentation.GetNthSegmentID(index)
            ).GetName()
            for index in range(segmentation.GetNumberOfSegments())
        }
        assert actual == expected, f"{case.case_id}: {actual!r}"
        for label_value, segment_id in FIXED_SEGMENT_IDS.items():
            voxels = slicer.util.arrayFromSegmentBinaryLabelmap(
                logic.segmentation_node,
                segment_id,
                logic.volume_nodes["sagittal"],
            )
            assert bool(voxels.any()) == (label_value in source_values), (
                f"{case.case_id} label {label_value} voxel presence changed"
            )


def test_failed_reveal_draft_is_repaired() -> None:
    slicer.mrmlScene.Clear(0)
    manifest = _test_manifest()
    logic = MyomaReviewLiteLogic()
    logic.open_manifest(manifest.manifest_path, case_id=manifest.cases[0].case_id)
    _load_sources(logic)
    logic.segmentation_node = slicer.mrmlScene.AddNewNodeByClass(
        "vtkMRMLSegmentationNode", "Failed reveal fixture"
    )
    logic.segmentation_node.CreateDefaultDisplayNodes()
    logic.segmentation_node.SetReferenceImageGeometryParameterFromVolumeNode(
        logic.volume_nodes["sagittal"]
    )
    assert slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
        logic.ai_mask_node, logic.segmentation_node
    )
    segmentation = logic.segmentation_node.GetSegmentation()
    for wrong_id, wrong_name in (
        ("uterine wall", "uterine_wall"),
        ("uterine cavity", "uterine_cavity"),
        ("myoma", "myoma"),
        ("Nabothian cyst", "nabothian_cyst"),
    ):
        segmentation.AddEmptySegment(wrong_id, wrong_name)

    try:
        logic._normalize_segments()
    except Exception as exc:
        raise AssertionError(f"failed-reveal draft repair failed: {exc}") from exc

    actual = {
        segmentation.GetNthSegmentID(index): segmentation.GetSegment(
            segmentation.GetNthSegmentID(index)
        ).GetName()
        for index in range(segmentation.GetNumberOfSegments())
    }
    assert actual == {
        "uterine_wall": "uterine wall",
        "uterine_cavity": "uterine cavity",
        "myoma": "myoma",
        "nabothian_cyst": "Nabothian cyst",
        "cervix": "cervix",
        "cesarean_scar_niche": "cesarean scar niche",
    }


try:
    test_failed_reveal_draft_is_repaired()
    test_real_ai_masks_normalize_to_fixed_segments()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite AI segment normalization: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
finally:
    slicer.mrmlScene.Clear(0)
