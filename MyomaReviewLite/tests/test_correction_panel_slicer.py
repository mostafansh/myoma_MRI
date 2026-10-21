"""Slicer-runtime regression test for the correction page's own controls.

The segment rows, tool grid and brush panel stand in for the Segment Editor's
buttons and table, which stay hidden, and follow the editor when a tool or
segment is changed elsewhere.

Run with run_slicer_test.py and MYOMA_REVIEW_LITE_TEST_QUEUE set to a dataset
queue. Every file is written to a temporary reader folder; the source queue is
only read. Set MYOMA_TEST_SCREENSHOT to a .png path to keep a picture of the
panel.
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
from MyomaReviewLiteLib import DatasetManifest  # noqa: E402
from MyomaReviewLiteLib.correction_panel import HIDDEN_EDITOR_PARTS  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT


def _write_queue(root: Path) -> tuple[Path, str]:
    source = DatasetManifest.from_file(
        os.environ["MYOMA_REVIEW_LITE_TEST_QUEUE"],
        source_root=os.environ.get("MYOMA_REVIEW_LITE_DATA_ROOT") or None,
    )
    case = source.cases[0]
    reader = root / "reader"
    reader.mkdir()
    queue_path = reader / "queue.json"
    queue_path.write_text(json.dumps({
        "schema_version": "1.0",
        "manifest_type": "dataset",
        "study_id": "CORRECTION_PANEL_SMOKE",
        "reader_id": "correction_panel_smoke",
        "output_root": ".",
        "cases": [{
            "case_id": case.case_id,
            "volumes": {name: str(path) for name, path in case.volumes.items()},
            "ai_mask": str(case.ai_mask),
        }],
    }), encoding="utf-8")
    return queue_path, case.case_id


def test_correction_panel_drives_the_segment_editor() -> None:
    slicer.mrmlScene.Clear(0)
    with tempfile.TemporaryDirectory() as temporary:
        queue_path, case_id = _write_queue(Path(temporary))
        parent = slicer.qMRMLWidget()
        parent.setLayout(qt.QVBoxLayout())
        parent.setMRMLScene(slicer.mrmlScene)
        widget = MyomaReviewLiteWidget(parent)
        widget._build_ui()
        widget.roi_overlay = LiteRoiLabelOverlay(widget)
        widget.draw_tool = LiteRectangleDrawTool(widget)
        try:
            widget.open_case(queue_path, case_id)
            widget.logic.state.confirm_zero_myomas(True)
            widget.on_reveal_ai()
            panel = widget.correction_panel
            editor = widget.segment_editor_widget
            assert editor is not None and panel.editor is editor
            assert widget.page_stack.currentIndex == 1

            # Slicer's own buttons and segment table stay out of sight.
            for name in HIDDEN_EDITOR_PARTS:
                part = editor.findChild(qt.QWidget, name)
                assert part is None or part.isHidden(), name
            assert list(panel.segment_buttons) == ["myoma", "uterine_wall", "uterine_cavity", "nabothian_cyst"]
            assert [b.accessibleName for b in panel.segment_buttons.values()] == [
                "Myoma", "Uterine wall", "Uterine cavity", "Nabothian cyst",
            ]
            assert panel.segment_buttons["myoma"].checked

            # Paint switches the editor on, the brush panel sets its size in mm.
            panel.tool_buttons["Paint"].click()
            assert panel.active_effect_name() == "Paint"
            assert panel.tool_buttons["Paint"].checked
            assert panel.brush_panel.visible or not parent.visible
            assert not panel.brush_panel.isHidden()
            panel.brush_slider.value = 20
            effect = editor.activeEffect()
            assert effect.integerParameter("BrushDiameterIsRelative") == 0
            assert abs(effect.doubleParameter("BrushAbsoluteDiameter") - 20.0) < 1e-6
            assert panel.brush_size_label.text == "20 mm"
            panel.sphere_check.checked = True
            assert effect.integerParameter("BrushSphere") == 1
            assert panel.brush_slider.value == 20

            # Erase keeps the same brush.
            panel.tool_buttons["Erase"].click()
            assert panel.active_effect_name() == "Erase"
            assert abs(editor.activeEffect().doubleParameter("BrushAbsoluteDiameter") - 20.0) < 1e-6

            # A second click puts the tool down.
            panel.tool_buttons["Erase"].click()
            assert panel.active_effect_name() == ""
            assert not any(button.checked for button in panel.tool_buttons.values())
            assert panel.brush_panel.isHidden()

            # Picking a row picks the editor's segment.
            panel.segment_buttons["uterine_wall"].click()
            assert editor.currentSegmentID() == "uterine_wall"
            assert panel.segment_buttons["uterine_wall"].checked
            assert not panel.segment_buttons["myoma"].checked

            # A tool switched outside the panel shows on it, with its own options.
            editor.setActiveEffectByName("Islands")
            slicer.app.processEvents()
            assert panel.tool_buttons["Islands"].checked
            assert panel.brush_panel.isHidden()
            options = editor.findChild(qt.QWidget, "OptionsGroupBox")
            assert options.maximumHeight > 0
            editor.setCurrentSegmentID("myoma")
            assert panel.segment_buttons["myoma"].checked

            assert panel.key_hint.text == "" or "show boxes" in panel.key_hint.text

            shot = os.environ.get("MYOMA_TEST_SCREENSHOT", "").strip()
            if shot:
                editor.setActiveEffectByName("Paint")
                panel.sync()
                widget.parent.resize(380, 880)
                widget.parent.show()
                slicer.app.processEvents()
                widget.parent.grab().save(shot)

            # Read-only cases lock the panel.
            panel.set_read_only(True)
            assert not panel.tool_buttons["Paint"].enabled
            assert not panel.segment_buttons["myoma"].enabled
            panel.set_read_only(False)

            # Leaving the case lets go of the editor.
            widget._teardown_current_case()
            assert panel.editor is None
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


try:
    test_correction_panel_drives_the_segment_editor()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite correction panel: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
