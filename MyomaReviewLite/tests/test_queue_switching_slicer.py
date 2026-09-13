"""Slicer-runtime smoke test for queue saving, restoration, and auto-next."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import qt
import slicer


MODULE_ROOT = Path(__file__).resolve().parents[1]
for module_name in tuple(sys.modules):
    if module_name == "MyomaReviewLite" or module_name.startswith("MyomaReviewLiteLib"):
        del sys.modules[module_name]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != MODULE_ROOT]
sys.path.insert(0, str(MODULE_ROOT))

import MyomaReviewLite as bundled_module  # noqa: E402
from MyomaReviewLite import (  # noqa: E402
    LiteRectangleDrawTool,
    LiteRoiLabelOverlay,
    MyomaReviewLiteWidget,
)
from MyomaReviewLiteLib import DatasetManifest  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT


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
    manifest = DatasetManifest.from_file(value, source_root=data_root or None)
    if len(manifest.cases) < 2:
        raise RuntimeError("the Slicer switching test queue requires at least two cases")
    return manifest


def test_queue_switching_and_corrected_restore() -> None:
    slicer.mrmlScene.Clear(0)
    source_manifest = _source_manifest()
    source_cases = source_manifest.cases[:2]
    first_case_id, second_case_id = (case.case_id for case in source_cases)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        reader = root / "reader"
        queue_path = reader / "queue.json"
        reader.mkdir()
        queue_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "manifest_type": "dataset",
                    "study_id": "QUEUE_SMOKE",
                    "reader_id": "queue_smoke",
                    "output_root": ".",
                    "cases": [
                        {
                            "case_id": case.case_id,
                            "volumes": {
                                name: str(path) for name, path in case.volumes.items()
                            },
                            "ai_mask": str(case.ai_mask),
                        }
                        for case in source_cases
                    ],
                }
            ),
            encoding="utf-8",
        )

        parent = slicer.qMRMLWidget()
        parent.setLayout(qt.QVBoxLayout())
        parent.setMRMLScene(slicer.mrmlScene)
        widget = MyomaReviewLiteWidget(parent)
        widget._build_ui()
        widget.roi_overlay = LiteRoiLabelOverlay(widget)
        widget.draw_tool = LiteRectangleDrawTool(widget)
        try:
            widget.open_case(queue_path)
            assert widget.logic.case_id == first_case_id

            widget.open_case(queue_path, second_case_id)
            assert (reader / first_case_id / "draft.json").is_file()
            widget.open_case(queue_path, first_case_id)
            assert (reader / second_case_id / "draft.json").is_file()

            assert widget.logic.state is not None
            widget.logic.state.confirm_zero_myomas(True)
            widget.logic.reveal_ai()
            widget.logic.load_ai_mask()
            widget._open_correction_page()
            segment_id = "myoma"
            values = slicer.util.arrayFromSegmentBinaryLabelmap(
                widget.logic.segmentation_node,
                segment_id,
                widget.logic.volume_nodes["sagittal"],
            )
            original = int(values[0, 0, 0])
            values[0, 0, 0] = 0 if original else 1
            slicer.util.updateSegmentBinaryLabelmapFromArray(
                values,
                widget.logic.segmentation_node,
                segment_id,
                widget.logic.volume_nodes["sagittal"],
            )
            slicer.app.processEvents()
            assert widget.save_coordinator.dirty
            widget.on_autosave_timeout()
            assert (reader / first_case_id / "draft.seg.nrrd").is_file()
            assert not widget.save_coordinator.dirty

            widget.open_case(queue_path, second_case_id)
            widget.open_case(queue_path, first_case_id)
            restored = slicer.util.arrayFromSegmentBinaryLabelmap(
                widget.logic.segmentation_node,
                segment_id,
                widget.logic.volume_nodes["sagittal"],
            )
            assert int(restored[0, 0, 0]) == int(values[0, 0, 0])

            widget.logic.validate_correction_segments()
            temporary_labels = widget._save_corrected_snapshot()
            try:
                widget.logic.publish_complete(temporary_labels)
            finally:
                temporary_labels.unlink(missing_ok=True)
            widget._advance_after_finalization("Smoke completion saved.")

            assert widget.logic.case_id == second_case_id
            assert (reader / first_case_id / "annotations.json").is_file()
            assert (reader / first_case_id / "corrected_labels.nii.gz").is_file()
            assert not (reader / second_case_id / "annotations.json").exists()
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


try:
    test_queue_switching_and_corrected_restore()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite queue switching: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
