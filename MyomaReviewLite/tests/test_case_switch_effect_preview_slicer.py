"""Slicer-runtime regression test: no segmentation survives a case switch.

Fill between slices and Grow from seeds keep their result in a separate
"<segmentation> preview" node until it is applied. Switching cases while one
of them is active used to strip the editor's segmentation first, so the effect
could not remove that preview and the previous case's labels stayed drawn on
the next case, even while it was still blind.

Run with run_slicer_test.py and MYOMA_REVIEW_LITE_TEST_QUEUE set to a dataset
queue with at least two cases. Every file is written to a temporary reader
folder; the source queue is only read.
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
    MyomaReviewLiteWidget,
)
from MyomaReviewLiteLib import DatasetManifest  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT


def _source_manifest() -> DatasetManifest:
    value = os.environ.get("MYOMA_REVIEW_LITE_TEST_QUEUE", "").strip()
    if not value:
        raise RuntimeError("set MYOMA_REVIEW_LITE_TEST_QUEUE to a dataset queue")
    data_root = os.environ.get("MYOMA_REVIEW_LITE_DATA_ROOT", "").strip()
    manifest = DatasetManifest.from_file(value, source_root=data_root or None)
    if len(manifest.cases) < 2:
        raise RuntimeError("this test needs a queue with at least two cases")
    return manifest


def _segmentation_names() -> list[str]:
    return [node.GetName() for node in slicer.util.getNodesByClass("vtkMRMLSegmentationNode")]


def test_effect_preview_does_not_survive_case_switch() -> None:
    slicer.mrmlScene.Clear(0)
    source_cases = _source_manifest().cases[:2]
    first_case_id, second_case_id = (case.case_id for case in source_cases)
    with tempfile.TemporaryDirectory() as temporary:
        reader = Path(temporary) / "reader"
        reader.mkdir()
        queue_path = reader / "queue.json"
        queue_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "manifest_type": "dataset",
                    "study_id": "PREVIEW_SMOKE",
                    "reader_id": "preview_smoke",
                    "output_root": ".",
                    "cases": [
                        {
                            "case_id": case.case_id,
                            "volumes": {name: str(path) for name, path in case.volumes.items()},
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
        widget.draw_tool = LiteRectangleDrawTool(widget)
        try:
            widget.open_case(queue_path, first_case_id)
            widget.logic.state.confirm_zero_myomas(True)
            widget.on_reveal_ai()
            editor = widget.segment_editor_widget
            assert editor is not None
            editor.setCurrentSegmentID("myoma")
            editor.setActiveEffectByName("Fill between slices")
            editor.activeEffect().self().onPreview()
            slicer.app.processEvents()
            assert len(_segmentation_names()) == 2, _segmentation_names()

            widget.on_save_draft()
            widget.open_case(queue_path, second_case_id)
            slicer.app.processEvents()

            assert widget.logic.case_id == second_case_id
            assert widget.logic.segmentation_node is None
            assert _segmentation_names() == [], _segmentation_names()
            assert slicer.util.getNodesByClass("vtkMRMLLabelMapVolumeNode") == []
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


try:
    test_effect_preview_does_not_survive_case_switch()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite effect preview case switch: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
