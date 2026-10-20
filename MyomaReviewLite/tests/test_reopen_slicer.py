"""Slicer-runtime smoke test for reopening a completed case and editing it.

Run with: Slicer --no-splash --python-script tests/test_reopen_slicer.py
(set MYOMA_REVIEW_LITE_TEST_QUEUE to a dataset queue first, as for
test_queue_switching_slicer.py).
"""

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
from MyomaReviewLiteLib import BoxGeometry, DatasetManifest, QueueStatus  # noqa: E402

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
    return DatasetManifest.from_file(value, source_root=data_root or None)


def _segment_voxel(widget: MyomaReviewLiteWidget) -> int:
    values = slicer.util.arrayFromSegmentBinaryLabelmap(
        widget.logic.segmentation_node, "myoma", widget.logic.volume_nodes["sagittal"]
    )
    return int(values[0, 0, 0])


def test_reopen_completed_case_and_complete_again() -> None:
    slicer.mrmlScene.Clear(0)
    case = _source_manifest().cases[0]
    # Confirmation dialogs would block an unattended run; accept them.
    original_question = qt.QMessageBox.question
    qt.QMessageBox.question = staticmethod(lambda *_args, **_kwargs: qt.QMessageBox.Yes)
    with tempfile.TemporaryDirectory() as temporary:
        reader = Path(temporary) / "reader"
        reader.mkdir()
        queue_path = reader / "queue.json"
        queue_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "manifest_type": "dataset",
                    "study_id": "REOPEN_SMOKE",
                    "reader_id": "reopen_smoke",
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
        parent = slicer.qMRMLWidget()
        parent.setLayout(qt.QVBoxLayout())
        parent.setMRMLScene(slicer.mrmlScene)
        widget = MyomaReviewLiteWidget(parent)
        widget._build_ui()
        widget.roi_overlay = LiteRoiLabelOverlay(widget)
        widget.draw_tool = LiteRectangleDrawTool(widget)
        case_dir = reader / case.case_id
        try:
            widget.open_case(queue_path)
            state = widget.logic.state
            first = state.add_lesion((0.0, 0.0, 0.0), (20.0, 20.0, 20.0))
            state.set_figo(first, {"kind": "standard", "type": 3})
            widget.rebuild_roi_nodes()
            widget.on_reveal_ai()
            assert widget.logic.state.phase == "ai_revealed"

            # Mark the correction so it can be told apart from the raw AI mask.
            values = slicer.util.arrayFromSegmentBinaryLabelmap(
                widget.logic.segmentation_node, "myoma", widget.logic.volume_nodes["sagittal"]
            )
            values[0, 0, 0] = 0 if values[0, 0, 0] else 1
            corrected = int(values[0, 0, 0])
            slicer.util.updateSegmentBinaryLabelmapFromArray(
                values, widget.logic.segmentation_node, "myoma", widget.logic.volume_nodes["sagittal"]
            )
            widget.on_complete()
            assert (case_dir / "annotations.json").is_file(), "first completion failed"

            widget.open_case(queue_path, case.case_id)
            assert widget.logic.read_only
            assert not widget.reopen_button.isHidden()
            widget.on_reopen_case()
            assert not widget.logic.read_only, "the case did not reopen"
            assert widget.logic.state.phase == "ai_revealed"
            assert not (case_dir / "annotations.json").exists()
            assert (case_dir / "draft.seg.nrrd").is_file()
            assert len(list((case_dir / "reopened").iterdir())) == 1
            assert widget.logic.queue_summary.entry(case.case_id).status is QueueStatus.IN_PROGRESS
            assert _segment_voxel(widget) == corrected, "the earlier correction was lost"

            # Add a second myoma after reveal and type its FIGO.
            widget.on_back_to_boxing()
            assert widget.add_button.enabled
            widget.accept_rectangle(BoxGeometry((30.0, 30.0, 30.0), (12.0, 12.0, 12.0)), None)
            widget.save_roi_edit()
            second = "M02"
            assert second in widget.logic.state.lesions
            widget.on_continue_to_figo()
            widget.logic.state.set_figo(second, {"kind": "standard", "type": 6})
            widget.logic.state.set_figo(first, {"kind": "standard", "type": 4})
            widget.on_complete()

            saved = json.loads((case_dir / "annotations.json").read_text(encoding="utf-8"))
            assert [item["lesion_id"] for item in saved["lesions"]] == [first, second]
            assert saved["lesions"][0]["figo"]["type"] == 4
            assert saved["blinded_snapshot"]["lesions"][0]["figo"]["type"] == 3
            assert len(saved["blinded_snapshot"]["lesions"]) == 1
        finally:
            qt.QMessageBox.question = original_question
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


try:
    test_reopen_completed_case_and_complete_again()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite reopen: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
