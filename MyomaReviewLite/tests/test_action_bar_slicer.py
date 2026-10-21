"""Slicer-runtime tests for the step bar, the bottom action bar, the case-comment
pop-over, the folding "Nothing to box?" section and the read-only banner."""

from __future__ import annotations

import shutil
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
    LiteRoiClickSelector,
    LiteRoiLabelOverlay,
    MyomaReviewLiteWidget,
)
from MyomaReviewLiteLib import CaseStorage, ReviewState  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT

OUTPUT_ROOT = Path(tempfile.mkdtemp(prefix="myoma_action_bar_"))


def _build_case(case_id: str, boxes: int) -> MyomaReviewLiteWidget:
    slicer.mrmlScene.Clear(0)
    parent = slicer.qMRMLWidget()
    parent.setLayout(qt.QVBoxLayout())
    parent.setMRMLScene(slicer.mrmlScene)
    widget = MyomaReviewLiteWidget(parent)
    widget._build_ui()
    widget.roi_overlay = LiteRoiLabelOverlay(widget)
    widget.draw_tool = LiteRectangleDrawTool(widget)
    widget.click_selector = LiteRoiClickSelector(widget)
    widget.logic.state = ReviewState.new("STUDY", "reader", case_id)
    widget.logic.storage = CaseStorage(OUTPUT_ROOT, case_id)
    for index in range(boxes):
        widget.logic.state.add_lesion((index * 50.0, 0.0, 0.0), (20.0, 20.0, 20.0))
    widget.rebuild_roi_nodes()
    widget.refresh_ui()
    return widget


def _rect_in(widget: MyomaReviewLiteWidget, control) -> qt.QRect:
    corner = control.mapTo(widget.annotation_tab, qt.QPoint(0, 0))
    return qt.QRect(corner.x(), corner.y(), control.width, control.height)


def test_step_bar_and_hint_follow_the_blind_read() -> None:
    widget = _build_case("sub-00101", 2)
    try:
        first, second = sorted(widget.logic.state.lesions)
        widget._on_card_figo(first, {"kind": "standard", "type": 0})
        assert [button.text for button in widget.step_buttons] == ["1 Box", "2 FIGO", "3 Reveal AI", "4 Correct"]
        assert [button.property("state") for button in widget.step_buttons] == ["done", "now", "todo", "todo"]
        assert widget.status_label.text == f"{second} needs a FIGO type before you can reveal"
        assert widget.status_label.property("role") == "hint-warning"
        assert not any(button.enabled for button in widget.step_buttons), "a step is clickable before reveal"
        assert widget.action_bar.isAncestorOf(widget.reveal_button)
        assert widget.action_bar.isAncestorOf(widget.save_draft_button)
        assert not widget.page_stack.isAncestorOf(widget.reveal_button)
        assert widget.annotation_tab.isAncestorOf(widget.action_bar)
    finally:
        widget.cleanup()


def test_comment_popover_sits_above_the_action_bar() -> None:
    widget = _build_case("sub-00102", 1)
    parent = widget.parent
    try:
        parent.resize(380, 820)
        parent.show()
        slicer.app.processEvents()

        def check() -> None:
            slicer.app.processEvents()
            popover = _rect_in(widget, widget.case_comment_edit)
            bar = _rect_in(widget, widget.action_bar)
            assert popover.bottom() <= bar.top() - 6, (popover, bar)
            for button in (widget.reveal_button, widget.complete_button):
                if not button.isHidden():
                    assert not popover.intersects(_rect_in(widget, button)), button.text

        widget.comment_toggle.checked = True
        check()
        parent.resize(360, 700)
        check()
        widget._show_page(1)
        check()
    finally:
        widget.comment_toggle.checked = False
        parent.hide()
        widget.cleanup()


def test_nothing_to_box_opens_for_an_empty_case_and_stays_as_the_reader_leaves_it() -> None:
    widget = _build_case("sub-00103", 0)
    try:
        assert "Nothing to box?" in widget.nothing_to_box_toggle.text
        assert not widget.nothing_to_box_body.isHidden(), "an empty case should open the section"
        widget.nothing_to_box_toggle.click()
        assert widget.nothing_to_box_body.isHidden()
        widget.logic.state.add_lesion((0.0, 0.0, 0.0), (20.0, 20.0, 20.0))
        widget.refresh_ui()
        assert widget.nothing_to_box_body.isHidden(), "a refresh reopened what the reader closed"
        assert widget.status_label.property("role") == "hint-warning"
    finally:
        widget.cleanup()
    boxed = _build_case("sub-00104", 1)
    try:
        assert boxed.nothing_to_box_body.isHidden(), "a boxed case should start folded"
    finally:
        boxed.cleanup()


def test_a_completed_read_only_case_shows_the_banner_and_next_case() -> None:
    widget = _build_case("sub-00105", 1)
    try:
        assert widget.readonly_banner.isHidden()
        assert widget.footer_next_button.isHidden()
        widget.logic.state = None
        widget.logic.read_only = True
        widget.logic.final_annotation = {"status": "complete", "lesions": []}
        widget.refresh_ui()
        assert not widget.readonly_banner.isHidden()
        assert [button.property("state") for button in widget.step_buttons] == ["done"] * 4
        assert not widget.reopen_button.isHidden()
        assert not widget.footer_next_button.isHidden()
        assert widget.footer_next_button.text == "Next case  →"
        assert widget.reveal_button.isHidden() and widget.complete_button.isHidden()
        assert widget.action_bar.isAncestorOf(widget.reopen_button)
    finally:
        widget.cleanup()


def test_the_hint_never_keeps_the_blind_message_after_reveal() -> None:
    widget = _build_case("sub-00106", 1)
    try:
        (only,) = widget.logic.state.lesions
        widget._on_card_figo(only, {"kind": "standard", "type": 0})
        assert widget.status_label.text.startswith("All myomas have a FIGO type")
        widget.logic.state.reveal()
        widget.refresh_ui()
        assert widget.status_label.text == "Correct the AI labels, then export and complete.", widget.status_label.text
    finally:
        widget.cleanup()


def test_the_panel_is_painted_in_the_warm_background() -> None:
    widget = _build_case("sub-00107", 0)
    parent = widget.parent
    try:
        parent.resize(380, 820)
        parent.show()
        slicer.app.processEvents()
        image = parent.grab().toImage()
        # Just inside the left edge, beside the header: no control there.
        colour = qt.QColor(image.pixel(2, 2)).name()
        assert colour == "#1b1917", colour
    finally:
        parent.hide()
        widget.cleanup()


TESTS = (
    test_step_bar_and_hint_follow_the_blind_read,
    test_comment_popover_sits_above_the_action_bar,
    test_nothing_to_box_opens_for_an_empty_case_and_stays_as_the_reader_leaves_it,
    test_a_completed_read_only_case_shows_the_banner_and_next_case,
    test_the_hint_never_keeps_the_blind_message_after_reveal,
    test_the_panel_is_painted_in_the_warm_background,
)

failures = 0
for test in TESTS:
    try:
        test()
    except Exception:
        failures += 1
        sys.stderr.write(f"MyomaReviewLite action bar: FAIL {test.__name__}\n")
        traceback.print_exc()
shutil.rmtree(OUTPUT_ROOT, ignore_errors=True)
if failures:
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite action bar: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
