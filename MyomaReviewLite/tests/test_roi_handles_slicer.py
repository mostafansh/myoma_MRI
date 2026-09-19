"""Slicer-runtime smoke test for thin ROI outlines and click-revealed handles.

Run with: Slicer --no-splash --python-script tests/test_roi_handles_slicer.py
"""

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
    ROI_ACTIVE_OUTLINE_THICKNESS_PX,
    ROI_HANDLE_SCALE_PERCENT,
    ROI_OUTLINE_THICKNESS_PX,
    VIEW_NAMES,
    LiteRectangleDrawTool,
    LiteRoiClickSelector,
    LiteRoiLabelOverlay,
    MyomaReviewLiteWidget,
)
from MyomaReviewLiteLib import BoxGeometry, CaseStorage, ReviewPhase, ReviewState  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT

OUTPUT_ROOT = Path(tempfile.mkdtemp(prefix="myoma_roi_handles_"))
BIG_BOX_CENTER = (0.0, 0.0, 0.0)
BIG_BOX_INSIDE = (-15.0, -15.0, -15.0)
SMALL_BOX_INSIDE = (5.0, 5.0, 5.0)
FAR_OUTSIDE = (500.0, 500.0, 500.0)


def _build_case(case_id: str) -> tuple[MyomaReviewLiteWidget, str, str]:
    """A widget holding two overlapping myoma boxes, the smaller inside the larger."""

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
    # Committing a dragged box writes a draft, so give it somewhere real to write.
    widget.logic.storage = CaseStorage(OUTPUT_ROOT, case_id)
    first = widget.logic.state.add_lesion(BIG_BOX_CENTER, (40.0, 40.0, 40.0))
    second = widget.logic.state.add_lesion(SMALL_BOX_INSIDE, (10.0, 10.0, 10.0))
    widget.rebuild_roi_nodes()
    # Fills lesion_list and figo_lesion_combo; on_delete_box reads the list.
    widget.refresh_ui()
    assert widget.lesion_list.count == 2, "the fixture did not populate the lesion list"
    return widget, first, second


def test_saved_boxes_are_thin_and_have_no_handle_circles() -> None:
    widget, first, second = _build_case("sub-00000")
    try:
        for lesion_id in (first, second):
            roi = widget.roi_nodes[lesion_id]
            display = roi.GetDisplayNode()
            assert display is not None, f"{lesion_id} has no display node"
            # No circles: neither the interaction handles nor the centre control point.
            assert not display.GetHandlesInteractive(), f"{lesion_id} still shows handles"
            if roi.GetNumberOfControlPoints() > 0:
                assert not roi.GetNthControlPointVisibility(0), f"{lesion_id} centre glyph shows"
            # The 3 px markups outline gives way to the 1 px model outline.
            assert not roi.GetDisplayVisibility(), f"{lesion_id} markups outline is still drawn"

            outline = widget.roi_outline_nodes[lesion_id]
            outline_display = outline.GetDisplayNode()
            assert outline_display.GetVisibility2D(), "the thin outline would not draw in 2-D"
            assert not outline_display.GetVisibility3D(), "the outline should not fill the 3-D view"
            assert outline_display.GetSliceIntersectionThickness() == ROI_OUTLINE_THICKNESS_PX
            assert outline.GetDisplayVisibility(), f"{lesion_id} thin outline is hidden"
    finally:
        widget.cleanup()


def test_clicking_a_box_reveals_only_that_box() -> None:
    widget, first, second = _build_case("sub-00001")
    try:
        # A click inside both boxes resolves to the tighter one.
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.active_roi_id == second

        revealed = widget.roi_nodes[second]
        assert revealed.GetDisplayNode().GetHandlesInteractive()
        assert revealed.GetDisplayVisibility()
        assert not revealed.GetLocked(), "a revealed box must be draggable"
        assert not revealed.GetDisplayNode().GetOutlineVisibility(), (
            "the hardcoded 3 px markups outline is competing with the thin one"
        )
        revealed_outline = widget.roi_outline_nodes[second]
        assert revealed_outline.GetDisplayVisibility(), "the revealed box lost its outline"
        assert (
            revealed_outline.GetDisplayNode().GetSliceIntersectionThickness()
            == ROI_ACTIVE_OUTLINE_THICKNESS_PX
        ), "the revealed box is not bolder than the rest"

        quiet = widget.roi_nodes[first]
        assert not quiet.GetDisplayNode().GetHandlesInteractive()
        assert quiet.GetLocked()
        quiet_outline = widget.roi_outline_nodes[first]
        assert quiet_outline.GetDisplayVisibility()
        assert (
            quiet_outline.GetDisplayNode().GetSliceIntersectionThickness()
            == ROI_OUTLINE_THICKNESS_PX
        )
        assert widget.figo_lesion_combo.currentData == second, "the click did not select the row"

        # A click outside every box clears the selection again.
        widget.on_view_clicked("MyomaLiteAxial", FAR_OUTSIDE)
        assert widget.active_roi_id is None
        for lesion_id in (first, second):
            assert not widget.roi_nodes[lesion_id].GetDisplayNode().GetHandlesInteractive()
            assert widget.roi_nodes[lesion_id].GetLocked()
            outline = widget.roi_outline_nodes[lesion_id]
            assert outline.GetDisplayVisibility()
            assert (
                outline.GetDisplayNode().GetSliceIntersectionThickness()
                == ROI_OUTLINE_THICKNESS_PX
            ), "the deselected box stayed bold"
    finally:
        widget.cleanup()


def test_selecting_a_row_selects_that_box() -> None:
    widget, first, second = _build_case("sub-00002")
    try:
        widget.logic.activate_layout()
        widget.lesion_list.setCurrentRow(widget.figo_lesion_combo.findData(second))
        assert widget._selected_lesion_id() == second, "the fixture did not select a row"
        assert widget.active_roi_id == second, "the selected box did not follow the list"
        assert widget.roi_nodes[second].GetDisplayVisibility()
        assert widget.roi_nodes[second].GetDisplayNode().GetHandlesInteractive()
        assert not widget.roi_nodes[first].GetDisplayVisibility()
        assert not widget.roi_nodes[first].GetDisplayNode().GetHandlesInteractive()
        assert _views_pass_through(SMALL_BOX_INSIDE), "the views did not follow the list"

        widget.lesion_list.setCurrentRow(widget.figo_lesion_combo.findData(first))
        assert widget.active_roi_id == first
        assert not widget.roi_nodes[second].GetDisplayVisibility()
        assert _views_pass_through(BIG_BOX_CENTER)
    finally:
        widget.cleanup()


def test_selecting_a_row_while_drawing_leaves_the_drawing_alone() -> None:
    widget, first, _second = _build_case("sub-00022")
    try:
        widget.accept_rectangle(BoxGeometry((30.0, 30.0, 30.0), (12.0, 12.0, 12.0)), None)
        assert widget._has_unresolved_roi()
        widget.lesion_list.setCurrentRow(widget.figo_lesion_combo.findData(first))
        assert widget.active_roi_id is None, "a box was selected while another was being placed"
        assert widget._has_unresolved_roi(), "picking a row cancelled the box being placed"
        widget.cancel_roi_edit()
    finally:
        widget.cleanup()


def _views_pass_through(point: tuple[float, float, float]) -> bool:
    """Whether all three T2 slice planes currently cut through ``point``."""

    manager = slicer.app.layoutManager()
    for view_name in VIEW_NAMES.values():
        matrix = manager.sliceWidget(view_name).mrmlSliceNode().GetSliceToRAS()
        normal = [matrix.GetElement(row, 2) for row in range(3)]
        origin = [matrix.GetElement(row, 3) for row in range(3)]
        offset = sum(n * (p - o) for n, p, o in zip(normal, point, origin))
        if abs(offset) > 1e-3:
            return False
    return True


def _assert_only_shown(widget: MyomaReviewLiteWidget, lesion_id: str) -> None:
    """``lesion_id`` is the one box drawn: a thin outline, no handles, no label for the rest."""

    for identifier, node in widget.roi_nodes.items():
        assert not node.GetDisplayNode().GetHandlesInteractive(), f"{identifier} has handles"
        assert node.GetLocked(), f"{identifier} could be dragged"
        assert not node.GetDisplayVisibility(), f"{identifier} markups box is drawn"
        outline = widget.roi_outline_nodes[identifier]
        assert bool(outline.GetDisplayVisibility()) == (identifier == lesion_id), (
            f"{identifier} outline visibility is wrong while only {lesion_id} should show"
        )
        assert (
            outline.GetDisplayNode().GetSliceIntersectionThickness() == ROI_OUTLINE_THICKNESS_PX
        ), f"{identifier} is not a thin line"
    assert widget.active_roi_id is None, "a box was left in hand during FIGO typing"
    assert widget.roi_overlay.only_id == lesion_id, "the other myomas are still labelled"


def test_figo_typing_opens_on_the_first_myoma_and_shows_only_its_box() -> None:
    widget, first, second = _build_case("sub-00019")
    try:
        widget.logic.activate_layout()
        # The reader drew M02 last, so it is the selected row when boxing ends.
        widget.lesion_list.setCurrentRow(widget.figo_lesion_combo.findData(second))
        assert widget._selected_lesion_id() == second

        widget.on_continue_to_figo()
        assert widget.blind_stack.currentIndex == 1, "FIGO typing did not open"
        assert widget.figo_lesion_combo.currentData == first, "FIGO typing did not start at M01"
        assert widget._selected_lesion_id() == first
        _assert_only_shown(widget, first)
        assert _views_pass_through(BIG_BOX_CENTER), "the views were not brought to the first box"
    finally:
        widget.cleanup()


def test_choosing_a_myoma_in_figo_typing_shows_only_that_box() -> None:
    widget, first, second = _build_case("sub-00020")
    try:
        widget.logic.activate_layout()
        widget.on_continue_to_figo()
        _assert_only_shown(widget, first)

        widget.figo_lesion_combo.setCurrentIndex(widget.figo_lesion_combo.findData(second))
        _assert_only_shown(widget, second)
        assert _views_pass_through(SMALL_BOX_INSIDE), "the views were not brought to the chosen box"

        # A click in a view must not pick a box the reader cannot see.
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        assert widget.figo_lesion_combo.currentData == second
        _assert_only_shown(widget, second)
        assert _views_pass_through(SMALL_BOX_INSIDE), "clicking moved the views"
    finally:
        widget.cleanup()


def test_saving_figo_advances_to_and_shows_only_the_next_myoma() -> None:
    widget, first, second = _build_case("sub-00021")
    try:
        widget.logic.activate_layout()
        widget.on_continue_to_figo()
        assert widget.figo_lesion_combo.currentData == first

        widget.on_apply_figo()
        assert widget.logic.state.lesions[first].figo is not None
        assert widget.figo_lesion_combo.currentData == second, "saving did not advance"
        _assert_only_shown(widget, second)
        assert _views_pass_through(SMALL_BOX_INSIDE)
    finally:
        widget.cleanup()


def test_continuing_to_figo_puts_a_box_in_hand_down_and_keeps_its_drag() -> None:
    widget, _first, second = _build_case("sub-00023")
    try:
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.active_roi_id == second
        widget.roi_nodes[second].SetCenterWorld(6.0, 5.0, 5.0)

        widget.on_continue_to_figo()
        assert widget.active_roi_id is None, "the box stayed in hand with its handles"
        assert not widget.roi_nodes[second].GetDisplayNode().GetHandlesInteractive()
        assert abs(widget.logic.state.lesions[second].center_ras[0] - 6.0) < 1e-3, (
            "the drag made before continuing was lost"
        )
    finally:
        widget.cleanup()


def test_going_back_to_boxing_shows_every_box_again() -> None:
    widget, first, second = _build_case("sub-00024")
    try:
        widget.on_continue_to_figo()
        _assert_only_shown(widget, first)

        widget.on_back_to_boxing()
        assert widget.blind_stack.currentIndex == 0
        assert widget.roi_overlay.only_id is None, "the labels stayed limited to one myoma"
        for lesion_id in (first, second):
            assert widget.roi_outline_nodes[lesion_id].GetDisplayVisibility(), (
                f"{lesion_id} stayed hidden after leaving FIGO typing"
            )
    finally:
        widget.cleanup()


def test_after_reveal_the_annotation_page_keeps_click_to_edit() -> None:
    # Only the blinded FIGO stage shows a single box. Once AI is revealed the
    # annotation page is where a box is still adjusted, by clicking it.
    widget, first, second = _build_case("sub-00025")
    try:
        widget.on_continue_to_figo()
        for lesion_id in (first, second):
            widget.logic.state.set_figo(lesion_id, {"kind": "standard", "type": 0})
        widget.logic.state.reveal()
        assert widget.logic.state.phase == ReviewPhase.AI_REVEALED.value
        widget.refresh_ui()
        assert not widget._figo_focus_active()
        assert widget.roi_overlay.only_id is None

        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.active_roi_id == second, "a box can no longer be picked after reveal"
        assert widget.roi_nodes[second].GetDisplayNode().GetHandlesInteractive()
        assert widget.roi_outline_nodes[first].GetDisplayVisibility()
    finally:
        widget.cleanup()


def test_dragging_a_revealed_box_is_saved_and_undoable() -> None:
    widget, first, _second = _build_case("sub-00003")
    try:
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        assert widget.active_roi_id == first

        # Simulate the reader dragging the revealed handles.
        moved_center = (12.0, -3.0, 4.0)
        widget.roi_nodes[first].SetCenterWorld(*moved_center)
        widget.set_active_roi(None)

        lesion = widget.logic.state.lesions[first]
        assert all(
            abs(actual - expected) < 1e-3
            for actual, expected in zip(lesion.center_ras, moved_center)
        ), f"the drag was not saved: {lesion.center_ras}"
        assert widget.logic.storage.draft_path.is_file(), "the drag never reached draft.json"
        assert widget.annotation_undo, "the drag left nothing to undo"

        widget.on_undo()
        restored = widget.logic.state.lesions[first]
        assert all(
            abs(value - expected) < 1e-3
            for value, expected in zip(restored.center_ras, BIG_BOX_CENTER)
        ), "Ctrl+Z did not restore the box"
    finally:
        widget.cleanup()


def test_a_second_drag_of_the_same_box_is_not_lost() -> None:
    widget, first, _second = _build_case("sub-00004")
    try:
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        widget.roi_nodes[first].SetCenterWorld(3.0, 0.0, 0.0)
        widget._write_current_draft()
        widget.roi_nodes[first].SetCenterWorld(9.0, 0.0, 0.0)
        widget.set_active_roi(None)
        assert abs(widget.logic.state.lesions[first].center_ras[0] - 9.0) < 1e-3, (
            "the second drag was dropped because the baseline was never re-armed"
        )
    finally:
        widget.cleanup()


def test_saving_while_a_box_is_in_hand_keeps_it_bold() -> None:
    widget, _first, second = _build_case("sub-00015")
    try:
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        widget.roi_nodes[second].SetCenterWorld(6.0, 5.0, 5.0)
        widget.on_save_draft()
        assert widget.active_roi_id == second, "saving should not drop the selection"
        assert (
            widget.roi_outline_nodes[second].GetDisplayNode().GetSliceIntersectionThickness()
            == ROI_ACTIVE_OUTLINE_THICKNESS_PX
        ), "the box in hand was thinned by the save and re-clicking cannot restore it"
    finally:
        widget.cleanup()


def test_a_click_still_switches_to_a_box_nested_in_the_revealed_one() -> None:
    widget, first, second = _build_case("sub-00016")
    try:
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        assert widget.active_roi_id == first
        # M02 lies wholly inside M01, so the keep-the-selection guard must not
        # swallow an ordinary click on it.
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.active_roi_id == second, "a nested box could never be selected"

        # But releasing at the end of a handle drag, just outside the box being
        # dragged, must not hand the selection to the box behind it.
        widget.roi_nodes[second].SetCenterWorld(6.0, 5.0, 5.0)
        just_outside = (13.0, 5.0, 5.0)  # 2 mm beyond M02, still well inside M01
        widget.on_view_clicked("MyomaLiteAxial", just_outside)
        assert widget.active_roi_id == second, "the drag released onto the outer box"

        # A later, ordinary click must not inherit that earlier, still-unsaved
        # drag. This point sits inside the 6 mm halo that the guard uses, so it
        # only reaches M01 when the press has re-baselined.
        widget.begin_view_click()
        assert widget.roi_click_baseline is not None, "the press did not re-baseline"
        widget.on_view_clicked("MyomaLiteAxial", just_outside)
        assert widget.active_roi_id == first, "an unsaved drag froze the selection"
        assert widget.roi_click_baseline is None, "the baseline outlived the click"
    finally:
        widget.cleanup()


class _FakeInteractor:
    """Stands in for the VTK interactor so the press handler can be driven."""

    def __init__(self, position: tuple[int, int] = (10, 10)) -> None:
        self.position = position

    def GetEventPosition(self) -> tuple[int, int]:
        return self.position


def test_pressing_in_a_view_rebaselines_the_revealed_box() -> None:
    widget, _first, second = _build_case("sub-00017")
    try:
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        widget.roi_nodes[second].SetCenterWorld(6.0, 5.0, 5.0)
        assert widget._active_roi_was_dragged(), "the unsaved drag was not noticed"

        widget.click_selector._press(_FakeInteractor(), "MyomaLiteAxial")
        assert widget.roi_click_baseline is not None, "the press did not snapshot the box"
        assert abs(widget.roi_click_baseline.center_ras[0] - 6.0) < 1e-3
        assert not widget._active_roi_was_dragged(), (
            "the press baseline is stale, so ordinary clicks read as drags"
        )
    finally:
        widget.cleanup()


def test_finalizing_a_case_takes_the_box_out_of_hand() -> None:
    widget, _first, second = _build_case("sub-00018")
    try:
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert not widget.roi_nodes[second].GetLocked()

        # Stand in for publish_early_stop / publish_complete, which is what
        # _advance_after_finalization runs after.
        widget.logic.read_only = True
        widget._advance_after_finalization("Final outcome saved.")

        assert widget.active_roi_id is None, "a published case left a box in hand"
        node = widget.roi_nodes[second]
        assert node.GetLocked(), "a published box can still be dragged"
        assert not node.GetDisplayNode().GetHandlesInteractive()
        assert (
            widget.roi_outline_nodes[second].GetDisplayNode().GetSliceIntersectionThickness()
            == ROI_OUTLINE_THICKNESS_PX
        ), "the published box still looks like the one in hand"
    finally:
        widget.cleanup()


def test_a_read_only_case_gets_no_handles_at_all() -> None:
    # Slicer has no visible-but-immovable handle, and SetLocked does not disarm
    # one, so a box that must not move must not be given handles either.
    widget, first, _second = _build_case("sub-00005")
    try:
        widget.logic.read_only = True
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        assert widget.active_roi_id == first, "a completed box should still highlight"
        assert widget.roi_nodes[first].GetLocked(), "a completed case must not become draggable"
        assert not widget.roi_nodes[first].GetDisplayNode().GetHandlesInteractive(), (
            "interactive handles on a read-only case can be dragged despite SetLocked"
        )
    finally:
        widget.cleanup()


def test_switching_to_the_correction_page_disarms_the_revealed_box() -> None:
    widget, _first, second = _build_case("sub-00009")
    try:
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert not widget.roi_nodes[second].GetLocked()

        widget.show_roi_check.checked = True
        widget._show_page(1)
        assert widget.roi_nodes[second].GetLocked(), (
            "a Segment Editor stroke could drag this box"
        )
        assert not widget.roi_nodes[second].GetDisplayNode().GetHandlesInteractive()

        widget._show_page(0)
        assert not widget.roi_nodes[second].GetLocked(), "the box never became editable again"
    finally:
        widget.cleanup()


def test_undo_straight_after_a_drag_restores_that_drag() -> None:
    widget, first, _second = _build_case("sub-00010")
    try:
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        widget.roi_nodes[first].SetCenterWorld(21.0, 0.0, 0.0)
        # No deselect: Ctrl+Z must still undo the drag, not some earlier edit.
        widget.on_undo()
        restored = widget.logic.state.lesions[first]
        assert all(
            abs(value - expected) < 1e-3
            for value, expected in zip(restored.center_ras, BIG_BOX_CENTER)
        ), f"Ctrl+Z did not undo the drag: {restored.center_ras}"
        assert first in widget.roi_nodes, "the lesion was removed instead of moved back"
    finally:
        widget.cleanup()


def test_starting_a_new_box_disarms_the_revealed_one() -> None:
    widget, _first, second = _build_case("sub-00011")
    try:
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert not widget.roi_nodes[second].GetLocked()
        widget.on_add_box()
        assert widget.active_roi_id is None, "the old box would follow the drawing drag"
        assert widget.roi_nodes[second].GetLocked()
        if widget.draw_tool is not None:
            widget.draw_tool.cancel()
    finally:
        widget.cleanup()


def test_hiding_the_boxes_clears_the_revealed_one() -> None:
    widget, first, second = _build_case("sub-00006")
    try:
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.active_roi_id == second
        widget._set_roi_visibility(False)
        assert widget.active_roi_id is None
        for lesion_id in (first, second):
            assert not widget.roi_nodes[lesion_id].GetDisplayVisibility()
            assert not widget.roi_outline_nodes[lesion_id].GetDisplayVisibility()
        widget._set_roi_visibility(True)
        for lesion_id in (first, second):
            assert widget.roi_outline_nodes[lesion_id].GetDisplayVisibility()
            assert not widget.roi_nodes[lesion_id].GetDisplayVisibility()
            assert (
                widget.roi_outline_nodes[lesion_id]
                .GetDisplayNode()
                .GetSliceIntersectionThickness()
                == ROI_OUTLINE_THICKNESS_PX
            )
    finally:
        widget.cleanup()


def test_deleting_a_revealed_box_removes_its_outline_and_label() -> None:
    widget, _first, second = _build_case("sub-00007")
    try:
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        deleted_node = widget.roi_nodes[second]
        outline_id = widget.roi_outline_nodes[second].GetID()
        assert widget._selected_lesion_id() == second, "the click did not select the row"
        widget.on_delete_box()
        assert second not in widget.roi_nodes, "on_delete_box deleted nothing"
        assert slicer.mrmlScene.GetNodeByID(outline_id) is None
        assert second not in widget.roi_outline_nodes, "a deleted box left its outline behind"
        assert widget.active_roi_id != second
        assert id(deleted_node) not in widget.roi_overlay.entries, (
            "the deleted box keeps its M## label drawn in every view"
        )
    finally:
        widget.cleanup()


def test_a_box_is_the_same_weight_while_it_is_being_drawn() -> None:
    widget, _first, _second = _build_case("sub-00012")
    try:
        geometry = BoxGeometry((30.0, 30.0, 30.0), (12.0, 12.0, 12.0))

        # The rubber band during the corner drag.
        preview = widget.update_roi_preview(None, geometry, "MyomaLiteAxial")
        assert preview.IsA("vtkMRMLModelNode"), "the preview is still a fat markups box"
        assert (
            preview.GetDisplayNode().GetSliceIntersectionThickness()
            == ROI_ACTIVE_OUTLINE_THICKNESS_PX
        )
        widget.remove_roi_preview(preview)

        # The box handed over for handle adjustment before Save ROI.
        widget.accept_rectangle(geometry, None)
        assert widget.roi_draft_outline is not None, "the draft box has no thin outline"
        assert (
            widget.roi_draft_outline.GetDisplayNode().GetSliceIntersectionThickness()
            == ROI_ACTIVE_OUTLINE_THICKNESS_PX
        )
        assert not widget.roi_draft_node.GetDisplayNode().GetOutlineVisibility(), (
            "the draft box still draws Slicer's 3 px outline"
        )
        assert widget.roi_draft_node.GetDisplayNode().GetHandlesInteractive()

        # The outline follows the box while its handles are moved.
        widget.roi_draft_node.SetCenterWorld(60.0, 30.0, 30.0)
        slicer.app.processEvents()
        bounds = [0.0] * 6
        widget.roi_draft_outline.GetRASBounds(bounds)
        assert abs((bounds[0] + bounds[1]) / 2.0 - 60.0) < 1e-3, (
            f"the draft outline did not follow the drag: {bounds[:2]}"
        )

        widget.cancel_roi_edit()
        assert widget.roi_draft_outline is None, "the cancelled draft left its outline behind"
    finally:
        widget.cleanup()


def test_the_revealed_box_outline_follows_a_handle_drag() -> None:
    widget, first, _second = _build_case("sub-00013")
    try:
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        widget.roi_nodes[first].SetCenterWorld(80.0, 0.0, 0.0)
        slicer.app.processEvents()
        bounds = [0.0] * 6
        widget.roi_outline_nodes[first].GetRASBounds(bounds)
        assert abs((bounds[0] + bounds[1]) / 2.0 - 80.0) < 1e-3, (
            f"the outline stayed behind while the box moved: {bounds[:2]}"
        )
    finally:
        widget.cleanup()


def test_outline_nodes_stay_out_of_the_data_tree_and_saved_scenes() -> None:
    widget, first, _second = _build_case("sub-00014")
    try:
        outline = widget.roi_outline_nodes[first]
        display = outline.GetDisplayNode()
        assert outline.GetHideFromEditors(), "the outline clutters the reader's Data module"
        assert not outline.GetSaveWithScene(), "the outline would be written into saved scenes"
        assert display.GetHideFromEditors()
        assert not display.GetSaveWithScene()
        if hasattr(display, "GetFolderDisplayOverrideAllowed"):
            assert not display.GetFolderDisplayOverrideAllowed(), (
                "a hierarchy folder could impose one thickness on every box"
            )

        # Removing a box must take its display node with it.
        display_id = display.GetID()
        widget._remove_roi_outline(first)
        assert slicer.mrmlScene.GetNodeByID(display_id) is None, "orphan display node left behind"
    finally:
        widget.cleanup()


def test_handles_are_drawn_smaller_than_the_slicer_default() -> None:
    widget, first, _second = _build_case("sub-00008")
    try:
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        display = widget.roi_nodes[first].GetDisplayNode()
        if hasattr(display, "GetInteractionHandleScale"):
            assert abs(display.GetInteractionHandleScale() - ROI_HANDLE_SCALE_PERCENT) < 1e-6
            assert ROI_HANDLE_SCALE_PERCENT < 3.0, "3.0 is the Slicer default; this must be smaller"
    finally:
        widget.cleanup()


TESTS = (
    test_saved_boxes_are_thin_and_have_no_handle_circles,
    test_clicking_a_box_reveals_only_that_box,
    test_selecting_a_row_selects_that_box,
    test_selecting_a_row_while_drawing_leaves_the_drawing_alone,
    test_figo_typing_opens_on_the_first_myoma_and_shows_only_its_box,
    test_choosing_a_myoma_in_figo_typing_shows_only_that_box,
    test_saving_figo_advances_to_and_shows_only_the_next_myoma,
    test_continuing_to_figo_puts_a_box_in_hand_down_and_keeps_its_drag,
    test_going_back_to_boxing_shows_every_box_again,
    test_after_reveal_the_annotation_page_keeps_click_to_edit,
    test_dragging_a_revealed_box_is_saved_and_undoable,
    test_a_second_drag_of_the_same_box_is_not_lost,
    test_undo_straight_after_a_drag_restores_that_drag,
    test_saving_while_a_box_is_in_hand_keeps_it_bold,
    test_a_click_still_switches_to_a_box_nested_in_the_revealed_one,
    test_pressing_in_a_view_rebaselines_the_revealed_box,
    test_finalizing_a_case_takes_the_box_out_of_hand,
    test_a_read_only_case_gets_no_handles_at_all,
    test_switching_to_the_correction_page_disarms_the_revealed_box,
    test_starting_a_new_box_disarms_the_revealed_one,
    test_hiding_the_boxes_clears_the_revealed_one,
    test_deleting_a_revealed_box_removes_its_outline_and_label,
    test_a_box_is_the_same_weight_while_it_is_being_drawn,
    test_the_revealed_box_outline_follows_a_handle_drag,
    test_outline_nodes_stay_out_of_the_data_tree_and_saved_scenes,
    test_handles_are_drawn_smaller_than_the_slicer_default,
)

failures = 0
for test in TESTS:
    try:
        test()
    except Exception:
        failures += 1
        sys.stderr.write(f"MyomaReviewLite ROI handles: FAIL {test.__name__}\n")
        traceback.print_exc()
    else:
        sys.stderr.write(f"MyomaReviewLite ROI handles: PASS {test.__name__}\n")
shutil.rmtree(OUTPUT_ROOT, ignore_errors=True)
sys.stderr.flush()
slicer.app.exit(1 if failures else 0)
