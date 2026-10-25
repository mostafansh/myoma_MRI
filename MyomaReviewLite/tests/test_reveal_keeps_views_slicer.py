"""Slicer-runtime regression test: each view opens inside its own series, and
revealing AI does not move the views.

On opening a case the slice bars used to write a stale slice position (0)
back after the views were fitted, so sub-00020's axial view opened outside
its series and showed nothing.

Loading the AI mask let Slicer show it in the views, and with native
(oblique) series Slicer then re-aimed every view along its own idea of the
volume axes: on sub-00020 all three views turned ~35 degrees and the sagittal
lost its slice spacing. The mask is now loaded hidden.

Run with run_slicer_test.py, MYOMA_REVIEW_LITE_TEST_QUEUE set to a dataset
queue and MYOMA_REVIEW_LITE_TEST_CASE to a case with an oblique series
(default: the queue's first case). Every file is written to a temporary
reader folder; the source queue is only read.
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
from MyomaReviewLite import LiteRectangleDrawTool, MyomaReviewLiteWidget, VIEW_NAMES  # noqa: E402
from MyomaReviewLiteLib import DatasetManifest  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT


def _view_state() -> dict[str, tuple]:
    manager = slicer.app.layoutManager()
    state = {}
    for name in VIEW_NAMES.values():
        node = manager.sliceWidget(name).mrmlSliceNode()
        matrix = node.GetSliceToRAS()
        rotation = tuple(round(matrix.GetElement(row, column), 4) for row in range(3) for column in range(3))
        state[name] = (node.GetOrientation(), rotation, round(node.GetFieldOfView()[2], 3))
    return state


def test_reveal_keeps_the_views() -> None:
    slicer.mrmlScene.Clear(0)
    source = DatasetManifest.from_file(
        os.environ["MYOMA_REVIEW_LITE_TEST_QUEUE"],
        source_root=os.environ.get("MYOMA_REVIEW_LITE_DATA_ROOT") or None,
    )
    wanted = os.environ.get("MYOMA_REVIEW_LITE_TEST_CASE", "").strip()
    case = next(c for c in source.cases if c.case_id == wanted) if wanted else source.cases[0]
    with tempfile.TemporaryDirectory() as temporary:
        reader = Path(temporary) / "reader"
        reader.mkdir()
        queue_path = reader / "queue.json"
        queue_path.write_text(json.dumps({
            "schema_version": "1.0",
            "manifest_type": "dataset",
            "study_id": "REVEAL_VIEWS",
            "reader_id": "reveal_views",
            "output_root": ".",
            "cases": [{
                "case_id": case.case_id,
                "volumes": {name: str(path) for name, path in case.volumes.items()},
                "ai_mask": str(case.ai_mask),
            }],
        }), encoding="utf-8")
        parent = slicer.qMRMLWidget()
        parent.setLayout(qt.QVBoxLayout())
        parent.setMRMLScene(slicer.mrmlScene)
        widget = MyomaReviewLiteWidget(parent)
        widget._build_ui()
        widget.draw_tool = LiteRectangleDrawTool(widget)
        try:
            widget.open_case(queue_path, case.case_id)
            slicer.app.processEvents()
            # Each view opens on a slice inside its own series. The slice bars
            # used to write a stale position of 0 back after the fit, which
            # lies outside e.g. sub-00020's axial series.
            axis = {"sagittal": 0, "axial": 2, "coronal": 1}
            for plane, name in VIEW_NAMES.items():
                bounds = [0.0] * 6
                widget.logic.volume_nodes[plane].GetRASBounds(bounds)
                low, high = bounds[2 * axis[plane]], bounds[2 * axis[plane] + 1]
                # The slice's position along its own axis, in RAS (the sagittal
                # offset is measured along -R, so the offset itself is not used).
                slice_to_ras = slicer.app.layoutManager().sliceWidget(name).mrmlSliceNode().GetSliceToRAS()
                position = slice_to_ras.GetElement(axis[plane], 3)
                assert low <= position <= high, (
                    f"{name} opened at {position:.1f} mm, outside its series ({low:.1f}, {high:.1f})"
                )
            widget.logic.state.confirm_zero_myomas(True)
            before = _view_state()
            widget.on_reveal_ai()
            slicer.app.processEvents()
            assert widget.logic.segmentation_node is not None, "the reveal failed"
            after = _view_state()
            for name in VIEW_NAMES.values():
                assert after[name] == before[name], f"revealing AI moved {name}: {before[name]} -> {after[name]}"
            # The source mask stays out of the views; the segmentation draws it.
            for name in VIEW_NAMES.values():
                composite = slicer.app.layoutManager().sliceWidget(name).sliceLogic().GetSliceCompositeNode()
                assert composite.GetLabelVolumeID() is None, f"the raw AI mask is shown in {name}"

            # Reopening the revealed case loads its saved correction instead.
            widget.on_save_draft()
            widget.open_case(queue_path, case.case_id)
            slicer.app.processEvents()
            assert widget.logic.segmentation_node is not None, "the resumed correction did not load"
            resumed = _view_state()
            for name in VIEW_NAMES.values():
                assert resumed[name] == before[name], f"reopening moved {name}: {before[name]} -> {resumed[name]}"
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


try:
    test_reveal_keeps_the_views()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite reveal keeps the views: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
