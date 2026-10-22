"""Slicer-runtime regression test for the MyomaReviewLite module panel layout:
header with previous/next case, queue drawer, and the guarded case switch."""

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


def _new_widget() -> MyomaReviewLiteWidget:
    parent = slicer.qMRMLWidget()
    parent.setLayout(qt.QVBoxLayout())
    parent.setMRMLScene(slicer.mrmlScene)
    widget = MyomaReviewLiteWidget(parent)
    widget._build_ui()
    return widget


def _write_queue(root: Path, case_count: int) -> tuple[Path, list[str]]:
    """A reader queue over the first ``case_count`` cases of the test dataset."""
    source = DatasetManifest.from_file(
        os.environ["MYOMA_REVIEW_LITE_TEST_QUEUE"],
        source_root=os.environ.get("MYOMA_REVIEW_LITE_DATA_ROOT") or None,
    )
    cases = source.cases[:case_count]
    assert len(cases) == case_count, "the test dataset needs more cases"
    reader = root / "reader"
    reader.mkdir()
    queue_path = reader / "queue.json"
    queue_path.write_text(json.dumps({
        "schema_version": "1.0",
        "manifest_type": "dataset",
        "study_id": "LAYOUT_SMOKE",
        "reader_id": "layout_smoke",
        "output_root": ".",
        "cases": [
            {
                "case_id": case.case_id,
                "volumes": {name: str(path) for name, path in case.volumes.items()},
                "ai_mask": str(case.ai_mask),
            }
            for case in cases
        ],
    }), encoding="utf-8")
    return queue_path, [case.case_id for case in cases]


def _case_rows(widget) -> list[str]:
    return [
        str(widget.case_list.item(i).data(qt.Qt.UserRole))
        for i in range(widget.case_list.count)
        if widget.case_list.item(i).data(qt.Qt.UserRole)
    ]


def test_panel_has_header_drawer_and_annotation() -> None:
    widget = _new_widget()
    layout = widget.parent.layout()
    assert layout.indexOf(widget.content_stack) >= 0
    assert widget.content_stack.indexOf(widget.annotation_tab) == 0
    assert widget.content_stack.indexOf(widget.queue_drawer) == 1
    assert widget.annotation_tab.layout().indexOf(widget.page_stack) >= 0
    assert widget.manifest_button.text == "Open case manifest"
    assert list(widget.filter_buttons) == ["todo", "finished", "attention", "questions", "all"]
    assert widget.filter_buttons["todo"].checked
    assert widget.case_list.count == 0
    assert not widget.prev_case_button.enabled and not widget.next_case_button.enabled
    assert "Previous case" in widget.prev_case_button.toolTip  # Slicer wraps tooltips in <p>
    assert "Next case" in widget.next_case_button.toolTip

    widget.queue_toggle.click()
    assert widget.content_stack.currentIndex == 1
    widget._focus_annotation_tab()
    assert widget.content_stack.currentIndex == 0
    assert not widget.queue_toggle.checked


def test_drawer_shows_empty_state(queue_path: Path, case_ids: list[str]) -> None:
    widget = _new_widget()
    widget.draw_tool = LiteRectangleDrawTool(widget)
    try:
        widget.open_case(queue_path)
        widget.on_case_filter("all")
        assert set(_case_rows(widget)) == set(case_ids)
        assert not widget.case_empty_label.visible
        widget.case_search.text = "zzz"
        assert _case_rows(widget) == []
        assert not widget.case_empty_label.isHidden()
        widget.case_search.text = ""
        assert widget.case_empty_label.isHidden()
    finally:
        widget.cleanup()
        slicer.mrmlScene.Clear(0)


def test_next_case_uses_the_guarded_open_path(queue_path: Path, case_ids: list[str]) -> None:
    first, attention, third = case_ids
    # A segmentation draft with no draft.json puts the middle case in Attention.
    (queue_path.parent / attention).mkdir(exist_ok=True)
    (queue_path.parent / attention / "draft.seg.nrrd").write_bytes(b"")
    widget = _new_widget()
    widget.draw_tool = LiteRectangleDrawTool(widget)
    try:
        widget.open_case(queue_path, first)
        assert widget.logic.case_id == first
        assert not widget.prev_case_button.enabled
        assert widget.next_case_button.enabled

        transitions = []
        real_transition = widget.save_coordinator.transition

        def recording_transition(**kwargs):
            transitions.append(kwargs)
            return real_transition(**kwargs)

        widget.save_coordinator.transition = recording_transition
        # An unfinished box edit must block the switch, like picking from the list.
        widget._has_unresolved_roi = lambda: True
        widget.on_next_case()
        assert len(transitions) == 1
        assert widget.logic.case_id == first

        widget._has_unresolved_roi = lambda: False
        widget.on_next_case()
        assert widget.logic.case_id == third, widget.logic.case_id
        assert widget.prev_case_button.enabled
        assert not widget.next_case_button.enabled
        widget.on_previous_case()
        assert widget.logic.case_id == first
    finally:
        widget.cleanup()
        slicer.mrmlScene.Clear(0)


try:
    test_panel_has_header_drawer_and_annotation()
    for test, count in ((test_drawer_shows_empty_state, 2), (test_next_case_uses_the_guarded_open_path, 3)):
        slicer.mrmlScene.Clear(0)
        with tempfile.TemporaryDirectory() as temporary:
            test(*_write_queue(Path(temporary), count))
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite widget layout: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
