"""Slicer-runtime smoke test for thin ROI outlines and click-revealed handles.

Run with: Slicer --no-splash --python-script tests/test_roi_handles_slicer.py
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import traceback
import types
from pathlib import Path
from typing import Any

import qt
import slicer
import vtk


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
    # Fills lesion_list; on_delete_box reads the list.
    widget.refresh_ui()
    assert widget.lesion_list.count == 2, "the fixture did not populate the lesion list"
    return widget, first, second


def _row(widget: MyomaReviewLiteWidget, lesion_id: str) -> int:
    for row in range(widget.lesion_list.count):
        if widget.lesion_list.item(row).data(qt.Qt.UserRole) == lesion_id:
            return row
    raise AssertionError(f"{lesion_id} has no row in the myoma list")


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
        assert not quiet_outline.GetDisplayVisibility(), "a selected myoma must show alone"
        assert widget._selected_lesion_id() == second, "the click did not select the row"

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
        widget.lesion_list.setCurrentRow(_row(widget, second))
        assert widget._selected_lesion_id() == second, "the fixture did not select a row"
        assert widget.active_roi_id == second, "the selected box did not follow the list"
        assert widget.roi_nodes[second].GetDisplayVisibility()
        assert widget.roi_nodes[second].GetDisplayNode().GetHandlesInteractive()
        assert not widget.roi_nodes[first].GetDisplayVisibility()
        assert not widget.roi_nodes[first].GetDisplayNode().GetHandlesInteractive()
        assert _views_pass_through(SMALL_BOX_INSIDE), "the views did not follow the list"

        widget.lesion_list.setCurrentRow(_row(widget, first))
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
        widget.lesion_list.setCurrentRow(_row(widget, first))
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


def test_after_reveal_the_annotation_page_keeps_click_to_edit() -> None:
    # Once AI is revealed the annotation page is still where a box is
    # adjusted, by clicking it.
    widget, first, second = _revealed_case("sub-00025")
    try:
        assert widget.logic.state.phase == ReviewPhase.AI_REVEALED.value
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.active_roi_id == second, "a box can no longer be picked after reveal"
        assert widget.roi_nodes[second].GetDisplayNode().GetHandlesInteractive()
        assert not widget.roi_outline_nodes[first].GetDisplayVisibility()
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


def test_a_box_nested_in_the_revealed_one_is_reached_by_deselecting_first() -> None:
    widget, first, second = _build_case("sub-00016")
    try:
        widget.on_view_clicked("MyomaLiteAxial", BIG_BOX_INSIDE)
        assert widget.active_roi_id == first
        # M02 lies wholly inside M01 and is hidden while M01 is selected, so a
        # click on where it would be stays on M01 ...
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.active_roi_id == first, "a hidden box was picked"
        # ... until empty image clears the selection and every box shows again.
        widget.on_view_clicked("MyomaLiteAxial", FAR_OUTSIDE)
        assert widget.active_roi_id is None
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.active_roi_id == second, "a nested box could never be selected"

        # But releasing at the end of a handle drag, just outside the box being
        # dragged, must not hand the selection to the box behind it.
        widget.roi_nodes[second].SetCenterWorld(6.0, 5.0, 5.0)
        just_outside = (13.0, 5.0, 5.0)  # 2 mm beyond M02, still well inside M01
        widget.on_view_clicked("MyomaLiteAxial", just_outside)
        assert widget.active_roi_id == second, "the drag released onto the outer box"

        # A later, ordinary click must not inherit that earlier, still-unsaved drag.
        widget.begin_view_click()
        assert widget.roi_click_baseline is not None, "the press did not re-baseline"
        widget.on_view_clicked("MyomaLiteAxial", FAR_OUTSIDE)
        assert widget.active_roi_id is None, "an unsaved drag froze the selection"
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
        assert widget.active_roi_id is None, "the FIGO page should open with no myoma selected"
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
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


def _revealed_case(case_id: str) -> tuple[MyomaReviewLiteWidget, str, str]:
    widget, first, second = _build_case(case_id)
    for lesion_id in (first, second):
        widget._on_card_figo(lesion_id, {"kind": "standard", "type": 0})
    widget.logic.state.reveal()
    widget.refresh_ui()
    return widget, first, second


def test_after_reveal_the_reader_can_still_add_a_box() -> None:
    widget, first, second = _revealed_case("sub-00040")
    try:
        assert not hasattr(widget, "blind_stack"), "the annotation page is still split in two"
        assert widget.add_button.enabled and widget.delete_button.enabled, (
            "boxes cannot be added or deleted after reveal"
        )
        blinded = widget.logic.state.blinded_snapshot
        widget.accept_rectangle(BoxGeometry((30.0, 30.0, 30.0), (12.0, 12.0, 12.0)), None)
        widget.save_roi_edit()
        added = [item for item in widget.logic.state.lesions if item not in (first, second)]
        assert added == ["M03"], f"the new box was not added: {sorted(widget.logic.state.lesions)}"
        assert widget.logic.state.lesions["M03"].figo is None
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == "M03"
        assert widget.logic.state.blinded_snapshot == blinded, "the blinded record changed"
        assert len(widget.logic.state.blinded_snapshot["lesions"]) == 2
    finally:
        widget.cleanup()


def test_only_a_completed_read_only_case_offers_reopen() -> None:
    widget, _first, _second = _revealed_case("sub-00042")
    try:
        assert widget.reopen_button.isHidden()
        widget.logic.state = None
        widget.logic.read_only = True
        widget.logic.final_annotation = {"status": "wrong_image", "lesions": []}
        widget.refresh_ui()
        assert widget.reopen_button.isHidden(), "an early stop must not be reopened"
        widget.logic.final_annotation = {"status": "complete", "lesions": []}
        widget.refresh_ui()
        assert not widget.reopen_button.isHidden()
    finally:
        widget.cleanup()


def _slice_styles() -> list:
    styles = []
    for view_name in VIEW_NAMES.values():
        view = slicer.app.layoutManager().sliceWidget(view_name).sliceView()
        styles.append(
            view.interactorObserver() if hasattr(view, "interactorObserver")
            else view.interactor().GetInteractorStyle()
        )
    return styles


def test_navigation_stays_available_while_placing_a_box() -> None:
    widget, _first, _second = _build_case("sub-00043")
    try:
        widget.logic.activate_layout()
        styles = _slice_styles()
        before = [
            {bit: bool(style.GetActionEnabled(1 << bit)) for bit in range(int(style.AllActionsMask).bit_length())}
            for style in styles
        ]
        widget.on_add_box()
        assert widget.draw_tool.active
        for style in styles:
            for name in ("Zoom", "Translate", "BrowseSlice"):
                assert style.GetActionEnabled(getattr(style, name)), f"{name} is locked while drawing"
            for name in ("AdjustWindowLevelBackground", "AdjustWindowLevelForeground"):
                assert not style.GetActionEnabled(getattr(style, name)), f"{name} would fight the box drag"
        widget.draw_tool.cancel()
        after = [
            {bit: bool(style.GetActionEnabled(1 << bit)) for bit in range(int(style.AllActionsMask).bit_length())}
            for style in styles
        ]
        assert after == before, "drawing did not restore the view actions"
    finally:
        widget.draw_tool.cancel()
        widget.cleanup()


def test_early_stops_sit_with_zero_myomas_on_the_annotation_page() -> None:
    widget, first, second = _build_case("sub-00044")
    try:
        page = widget.page_stack.widget(0)
        texts = [button.text for button in widget.early_stop_buttons]
        assert texts == ["No uterus", "Wrong image", "Cannot assess"], texts
        for button in widget.early_stop_buttons:
            assert page.isAncestorOf(button), f"{button.text} is not on the annotation page"
            assert button.enabled, f"{button.text} is disabled before reveal"
        for control in (widget.zero_check, widget.save_draft_button, widget.reveal_button,
                        widget.lesion_list):
            assert page.isAncestorOf(control), f"{control} is not on the annotation page"
        for lesion_id in (first, second):
            widget._on_card_figo(lesion_id, {"kind": "standard", "type": 0})
        widget.logic.state.reveal()
        widget.refresh_ui()
        assert not any(button.enabled for button in widget.early_stop_buttons), (
            "an early stop is still offered after reveal"
        )
    finally:
        widget.cleanup()


def test_removed_buttons_are_gone_and_v_shows_the_myomas() -> None:
    widget, _first, _second = _build_case("sub-00045")
    try:
        texts = {button.text for button in widget.parent.findChildren(qt.QPushButton)}
        for removed in ("Undo", "Redo", "Show segmented myomas", "Paint / add myoma",
                        "Fill chosen area", "Erase myoma"):
            assert not any(text.startswith(removed) for text in texts), f"{removed} button is still there"
        actions = {entry[0]: entry for entry in bundled_module.SHORTCUT_ACTIONS}
        assert actions["show_myomas"][2] == "V"
        assert actions["show_myomas"][3] == "on_show_myomas"
        assert actions["undo"][2] == "Ctrl+Z" and actions["redo"][2] == "Ctrl+Y"
        calls = []
        widget.correction_tools.show_myomas = lambda: calls.append(True)
        widget.on_show_myomas()
        assert not calls, "V must do nothing before a segmentation exists"
        widget.logic.segmentation_node = object()
        widget.on_show_myomas()
        assert calls == [True], "V did not show the segmented myomas"
        widget.logic.segmentation_node = None
    finally:
        widget.cleanup()


def test_the_redraw_button_is_gone() -> None:
    widget, _first, _second = _build_case("sub-00046")
    try:
        texts = {button.text for button in widget.parent.findChildren(qt.QPushButton)}
        assert not any(text.startswith("Redraw") for text in texts), "Redraw is still offered"
        assert "redraw_box" not in {entry[0] for entry in bundled_module.SHORTCUT_ACTIONS}
        assert not hasattr(widget, "redraw_button")
    finally:
        widget.cleanup()


def _menu_actions_with(sequence: str) -> list:
    target = qt.QKeySequence(sequence)
    return [
        action
        for action in slicer.util.mainWindow().findChildren(qt.QAction)
        if any(item.matches(target) == qt.QKeySequence.ExactMatch for item in action.shortcuts())
    ]


def test_undo_and_redo_keys_reach_the_module_while_it_is_open() -> None:
    widget, first, _second = _build_case("sub-00047")
    before_undo = _menu_actions_with("Ctrl+Z")
    before_redo = _menu_actions_with("Ctrl+Y")
    assert before_undo and before_redo, "Slicer no longer has Edit > Undo / Redo on Ctrl+Z / Ctrl+Y"
    try:
        widget._install_shortcuts()
        widget.enter()
        assert not _menu_actions_with("Ctrl+Z"), "Slicer's Edit > Undo still competes for Ctrl+Z"
        assert not _menu_actions_with("Ctrl+Y"), "Slicer's Edit > Redo still competes for Ctrl+Y"
        widget._remember_annotation()
        widget.logic.state.delete_lesion(first)
        undo = widget.shortcut_bindings["undo"]
        assert undo.enabled
        undo.activated()
        assert first in widget.logic.state.lesions, "Ctrl+Z did not undo the delete"
        widget.exit()
        assert _menu_actions_with("Ctrl+Z") == before_undo, "Edit > Undo did not get Ctrl+Z back"
        assert _menu_actions_with("Ctrl+Y") == before_redo, "Edit > Redo did not get Ctrl+Y back"
    finally:
        widget.exit()
        widget.cleanup()


def test_the_case_comment_folds_away_and_is_saved() -> None:
    widget, _first, _second = _build_case("sub-00048")
    try:
        assert widget.case_comment_edit.isHidden(), "the comment box should start folded"
        assert widget.comment_toggle.text == "Case comment"
        widget.comment_toggle.checked = True
        assert not widget.case_comment_edit.isHidden()
        widget.case_comment_edit.plainText = "  Motion artefact on the axial series  "
        widget.comment_toggle.checked = False
        assert widget.case_comment_edit.isHidden()
        assert widget.logic.state.comment == "Motion artefact on the axial series"
        assert widget.comment_toggle.text == "Case comment (written)"
        assert widget.logic.storage.load_draft().comment == "Motion artefact on the axial series"
    finally:
        widget.cleanup()


def test_reload_also_drops_the_helper_package() -> None:
    # Slicer's Reload re-reads only MyomaReviewLite.py; a stale
    # MyomaReviewLiteLib once left ReviewState without its new fields.
    widget, _first, _second = _build_case("sub-00050")
    saved_modules = dict(sys.modules)
    original_reload = slicer.util.reloadScriptedModule
    reloaded = []
    slicer.util.reloadScriptedModule = lambda name: reloaded.append(name)
    try:
        widget.moduleName = "MyomaReviewLite"
        assert "MyomaReviewLiteLib.domain" in sys.modules
        widget.onReload()
        assert reloaded == ["MyomaReviewLite"], f"Slicer's reload did not run: {reloaded}"
        stale = [name for name in sys.modules if name.startswith("MyomaReviewLiteLib")]
        assert not stale, f"stale helper modules survived the reload: {stale}"
    finally:
        slicer.util.reloadScriptedModule = original_reload
        sys.modules.update(saved_modules)
        widget.cleanup()


def _card_child(widget: MyomaReviewLiteWidget, name: str) -> Any:
    child = slicer.util.findChild(widget.figo_card.widget, name)
    assert child is not None, f"the FIGO card has no {name}"
    return child


def _center_views_on(point: tuple[float, float, float]) -> None:
    manager = slicer.app.layoutManager()
    for view_name in VIEW_NAMES.values():
        manager.sliceWidget(view_name).mrmlSliceNode().JumpSliceByCentering(*point)
    slicer.app.processEvents()


def _box_qt_rect(widget: MyomaReviewLiteWidget, lesion_id: str, view_name: str) -> Any:
    """The box's on-screen rectangle in the view's logical Qt pixels (top-left origin)."""

    slice_widget = slicer.app.layoutManager().sliceWidget(view_name)
    view = slice_widget.sliceView()
    inverse = vtk.vtkMatrix4x4()
    vtk.vtkMatrix4x4.Invert(slice_widget.mrmlSliceNode().GetXYToRAS(), inverse)
    bounds = [0.0] * 6
    widget.roi_nodes[lesion_id].GetRASBounds(bounds)
    xs, ys = [], []
    for r in bounds[0:2]:
        for a in bounds[2:4]:
            for s in bounds[4:6]:
                x, y, _z, _w = inverse.MultiplyPoint([r, a, s, 1.0])
                xs.append(x)
                ys.append(y)
    ratio = float(view.devicePixelRatioF())
    height = int(view.height)
    left, right = min(xs) / ratio, max(xs) / ratio
    top, bottom = height - max(ys) / ratio, height - min(ys) / ratio
    return qt.QRect(int(left), int(top), max(1, int(right - left)), max(1, int(bottom - top)))


def test_saving_a_new_box_opens_its_card() -> None:
    widget, _first, _second = _build_case("sub-00060")
    try:
        widget.logic.activate_layout()
        widget.accept_rectangle(BoxGeometry((30.0, 30.0, 30.0), (12.0, 12.0, 12.0)), None)
        widget.save_roi_edit()
        assert widget.figo_card.is_open, "saving a new box did not open its FIGO card"
        assert widget.figo_card.lesion_id == "M03"
        assert widget.active_roi_id == "M03", widget.active_roi_id
        assert widget.roi_nodes["M03"].GetDisplayNode().GetHandlesInteractive(), "the saved box has no handles"
    finally:
        widget.cleanup()


def test_clicking_a_box_opens_its_card_and_empty_image_closes_it() -> None:
    widget, _first, second = _build_case("sub-00061")
    try:
        widget.logic.activate_layout()
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == second
        widget.on_view_clicked("MyomaLiteAxial", FAR_OUTSIDE)
        assert not widget.figo_card.is_open, "clicking empty image left the card open"
    finally:
        widget.cleanup()


def test_picking_a_row_opens_that_card() -> None:
    widget, first, second = _build_case("sub-00062")
    try:
        widget.logic.activate_layout()
        # The fixture already has row 0 current, so pick the other row first.
        widget.lesion_list.setCurrentRow(1)
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == second
        widget.lesion_list.setCurrentRow(0)
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == first
    finally:
        widget.cleanup()


def test_a_card_choice_is_saved_and_undoable() -> None:
    widget, first, _second = _build_case("sub-00063")
    try:
        widget.logic.activate_layout()
        widget.open_figo_card(first)
        _card_child(widget, "figo_type_4").click()
        figo = widget.logic.state.lesions[first].figo
        assert figo is not None and figo.type == 4, f"the card choice was not saved: {figo}"
        draft = widget.logic.storage.load_draft()
        assert draft is not None and draft.lesions[first].figo.type == 4, "the draft lacks the choice"
        widget.on_undo()
        assert widget.logic.state.lesions[first].figo is None, "Ctrl+Z did not undo the card choice"
    finally:
        widget.cleanup()


def test_card_sits_beside_the_box_not_on_it() -> None:
    widget, _first, second = _build_case("sub-00064")
    try:
        widget.logic.activate_layout()
        _center_views_on(SMALL_BOX_INSIDE)
        widget.open_figo_card(second, "MyomaLiteAxial")
        slicer.app.processEvents()
        assert widget.figo_card.is_open and not widget.figo_card.widget.isHidden()
        box = _box_qt_rect(widget, second, "MyomaLiteAxial")
        card = widget.figo_card.widget.geometry
        assert not box.intersects(card), (
            f"the card {card.x()},{card.y()} {card.width()}x{card.height()} covers the box "
            f"{box.x()},{box.y()} {box.width()}x{box.height()}"
        )
    finally:
        widget.cleanup()


def test_card_hides_when_the_slice_leaves_the_box() -> None:
    widget, _first, second = _build_case("sub-00065")
    try:
        widget.logic.activate_layout()
        _center_views_on(SMALL_BOX_INSIDE)
        widget.open_figo_card(second, "MyomaLiteAxial")
        assert not widget.figo_card.widget.isHidden()
        slice_node = slicer.app.layoutManager().sliceWidget("MyomaLiteAxial").mrmlSliceNode()
        offset = slice_node.GetSliceOffset()
        slice_node.SetSliceOffset(offset + 100.0)
        assert widget.figo_card.widget.isHidden(), "the card stayed up off the box's slices"
        assert widget.figo_card.is_open, "leaving the box closed the card instead of hiding it"
        slice_node.SetSliceOffset(offset)
        assert not widget.figo_card.widget.isHidden(), "the card did not come back"
    finally:
        widget.cleanup()


def test_undo_refreshes_or_closes_the_open_card() -> None:
    widget, first, _second = _build_case("sub-00066")
    try:
        widget.logic.activate_layout()
        widget.open_figo_card(first)
        _card_child(widget, "figo_type_4").click()
        widget.on_undo()
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == first
        names = [f"figo_type_{n}" for n in range(9)] + ["figo_hybrid", "figo_uncertain"]
        checked = [name for name in names if _card_child(widget, name).checked]
        assert not checked, f"the card still shows the undone choice: {checked}"

        widget.accept_rectangle(BoxGeometry((30.0, 30.0, 30.0), (12.0, 12.0, 12.0)), None)
        widget.save_roi_edit()
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == "M03"
        widget.on_undo()
        assert "M03" not in widget.logic.state.lesions
        assert not widget.figo_card.is_open, "the card outlived its undone box"
    finally:
        widget.cleanup()


def test_reveal_saves_text_typed_on_the_card_into_the_blinded_snapshot() -> None:
    widget, first, second = _build_case("sub-00067")
    try:
        widget.logic.activate_layout()
        # reveal_ai only needs the manifest for the AI mask path it returns.
        widget.logic.manifest = types.SimpleNamespace(ai_mask=OUTPUT_ROOT / "ai.nii.gz")
        widget.logic.load_ai_mask = lambda: None
        widget._open_correction_page = lambda: None
        for lesion_id in (first, second):
            widget.open_figo_card(lesion_id)
            _card_child(widget, "figo_type_0").click()
        assert widget.figo_card.lesion_id == second
        _card_child(widget, "figo_ddx").text = "Leiomyoma"
        widget.on_reveal_ai()
        assert widget.logic.state.phase == ReviewPhase.AI_REVEALED.value, "the reveal did not happen"
        lesions = {item["lesion_id"]: item for item in widget.logic.state.blinded_snapshot["lesions"]}
        assert lesions[second]["differential_diagnosis"] == "Leiomyoma", (
            "the Ddx typed on the open card missed the blinded snapshot"
        )
        assert not widget.figo_card.is_open
    finally:
        widget.cleanup()


def test_the_card_never_shows_on_the_correction_page() -> None:
    widget, first, _second = _build_case("sub-00068")
    try:
        widget.logic.activate_layout()
        widget.open_figo_card(first)
        assert widget.figo_card.is_open
        widget._show_page(1)
        assert not widget.figo_card.is_open, "the card followed the reader to the correction page"
        widget.open_figo_card(first)
        assert not widget.figo_card.is_open, "the card opened on the correction page"
    finally:
        widget.cleanup()


def test_escape_in_a_view_closes_the_card() -> None:
    widget, first, _second = _build_case("sub-00069")
    try:
        widget.logic.activate_layout()
        widget.open_figo_card(first, "MyomaLiteAxial")
        assert widget.figo_card.is_open
        widget._card_escape_shortcuts["MyomaLiteAxial"].activated()
        assert not widget.figo_card.is_open, "Esc in the view left the card open"
    finally:
        widget.cleanup()


AMBER = (1.0, 0.6, 0.1)
LABEL_YELLOW = (1.0, 0.93, 0.2)


def _same_colour(actual: Any, expected: tuple[float, float, float]) -> bool:
    return all(abs(float(a) - b) < 1e-6 for a, b in zip(actual, expected))


def test_labels_show_the_figo_status() -> None:
    widget, first, _second = _build_case("sub-00070")
    try:
        widget.logic.activate_layout()
        widget.roi_overlay.bind_views()
        text, colour = widget.roi_label(first)
        assert text == "M01 · FIGO ?" and _same_colour(colour, AMBER), (text, colour)
        entry = next(e for e in widget.roi_overlay.entries.values() if e["public_id"] == first)
        actor = next(iter(entry["actors"].values()))
        assert actor.GetInput() == "M01 · FIGO ?", actor.GetInput()
        assert _same_colour(actor.GetTextProperty().GetColor(), AMBER)

        widget._on_card_figo(first, {"kind": "standard", "type": 3})
        text, colour = widget.roi_label(first)
        assert text == "M01 · FIGO 3" and _same_colour(colour, LABEL_YELLOW), (text, colour)
        assert actor.GetInput() == "M01 · FIGO 3", "the on-image label did not refresh"
        assert _same_colour(actor.GetTextProperty().GetColor(), LABEL_YELLOW)
        row_text = widget.lesion_list.item(_row(widget, first)).text()
        assert row_text == "M01 · FIGO 3", row_text
    finally:
        widget.cleanup()


def _tooltip(control: Any) -> str:
    """A tooltip's text; Slicer's style wraps plain tooltips in <p> for line wrapping."""

    return re.sub(r"<[^>]+>", "", str(control.toolTip))


def test_reveal_is_gated_with_a_tooltip_naming_missing_myomas() -> None:
    widget, first, second = _build_case("sub-00071")
    try:
        assert widget.reveal_button.enabled is False
        assert _tooltip(widget.reveal_button) == "FIGO missing: M01, M02", widget.reveal_button.toolTip
        widget._on_card_figo(first, {"kind": "standard", "type": 3})
        assert widget.reveal_button.enabled is False
        assert _tooltip(widget.reveal_button) == "FIGO missing: M02", widget.reveal_button.toolTip
        widget._on_card_figo(second, {"kind": "uncertain", "reason": "otherwise_not_classifiable"})
        assert widget.reveal_button.enabled is True, "every myoma has FIGO but reveal is off"
        assert _tooltip(widget.reveal_button) == "", widget.reveal_button.toolTip
    finally:
        widget.cleanup()


def test_figo_any_order_before_reveal() -> None:
    widget, first, second = _build_case("sub-00072")
    try:
        widget.logic.activate_layout()
        assert not hasattr(widget, "continue_to_figo_button"), "the boxing stage is still there"
        widget._on_card_figo(second, {"kind": "standard", "type": 2})
        widget.accept_rectangle(BoxGeometry((30.0, 30.0, 30.0), (12.0, 12.0, 12.0)), None)
        widget.save_roi_edit()
        third = widget.figo_card.lesion_id
        assert third == "M03", third
        assert not widget.logic.state.can_reveal
        widget._on_card_figo(first, {"kind": "hybrid", "endometrial": 2, "serosal": 5})
        assert not widget.logic.state.can_reveal
        _card_child(widget, "figo_type_6").click()
        assert widget.logic.state.lesions[third].figo is not None, "the card did not save M03"
        assert widget.logic.state.can_reveal
        assert widget.reveal_button.enabled
    finally:
        widget.cleanup()


def test_the_card_works_after_reveal() -> None:
    widget, _first, second = _revealed_case("sub-00073")
    try:
        widget.logic.activate_layout()
        blinded = json.dumps(widget.logic.state.blinded_snapshot, sort_keys=True)
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == second
        _card_child(widget, "figo_type_5").click()
        assert widget.logic.state.lesions[second].figo.type == 5, "the card choice was not saved"
        assert json.dumps(widget.logic.state.blinded_snapshot, sort_keys=True) == blinded, (
            "a choice after reveal changed the blinded record"
        )
        text, _colour = widget.roi_label(second)
        assert text == "M02 · FIGO 5", text
    finally:
        widget.cleanup()


def test_a_refresh_keeps_a_half_made_card_choice() -> None:
    widget, first, _second = _build_case("sub-00074")
    try:
        widget.logic.activate_layout()
        widget.open_figo_card(first)
        _card_child(widget, "figo_type_8").click()
        assert widget.logic.state.lesions[first].figo is None, "8 saved without a location"
        widget.refresh_ui()
        assert _card_child(widget, "figo_type_8").checked, "a refresh reset the half-made 8"
        assert not _card_child(widget, "figo_location").isHidden(), "the location row went away"

        # A FIGO saved some other way still reaches the open card.
        widget._remember_annotation()
        widget.logic.state.set_figo(first, {"kind": "standard", "type": 1})
        widget.refresh_ui()
        assert _card_child(widget, "figo_type_1").checked, "the card missed the saved FIGO"
        assert not _card_child(widget, "figo_type_8").checked
    finally:
        widget.cleanup()


def test_a_no_op_undo_leaves_the_card_alone() -> None:
    widget, first, _second = _build_case("sub-00075")
    try:
        widget.logic.activate_layout()
        assert not widget.annotation_undo and not widget.annotation_redo
        widget.open_figo_card(first)
        _card_child(widget, "figo_hybrid").click()
        widget.on_undo()
        widget.on_redo()
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == first
        assert _card_child(widget, "figo_hybrid").checked, "an empty undo reopened the card"
    finally:
        widget.cleanup()

    # The undo stack still holds blind-phase steps after reveal: also a no-op.
    widget, first, _second = _revealed_case("sub-00076")
    try:
        widget.logic.activate_layout()
        assert widget.annotation_undo, "the fixture left nothing to undo"
        widget.open_figo_card(first)
        _card_child(widget, "figo_type_8").click()
        widget.on_undo()
        assert widget.logic.state.phase == ReviewPhase.AI_REVEALED.value
        assert widget.figo_card.is_open
        assert _card_child(widget, "figo_type_8").checked, "a blocked undo reopened the card"
    finally:
        widget.cleanup()


def test_the_card_hides_while_its_box_is_not_drawn() -> None:
    widget, _first, second = _build_case("sub-00077")
    try:
        widget.logic.activate_layout()
        _center_views_on(SMALL_BOX_INSIDE)
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert widget.figo_card.is_open and not widget.figo_card.widget.isHidden()
        widget._set_roi_visibility(False)
        assert widget.figo_card.is_open, "hiding the boxes closed the card"
        assert widget.figo_card.widget.isHidden(), "the card stayed up without its box"
        widget._set_roi_visibility(True)
        assert not widget.figo_card.widget.isHidden(), "the card did not come back with its box"
    finally:
        widget.cleanup()


def test_a_read_only_list_shows_figo_and_opens_the_card_read_only() -> None:
    widget, _first, _second = _build_case("sub-00078")
    try:
        widget.logic.activate_layout()
        widget.logic.state = None
        widget.logic.read_only = True
        widget.logic.final_annotation = {
            "status": "complete",
            "lesions": [
                {"lesion_id": "M01", "center_ras": [0.0, 0.0, 0.0], "size_mm": [40.0, 40.0, 40.0],
                 "figo": {"kind": "standard", "type": 3}},
                {"lesion_id": "M02", "center_ras": [5.0, 5.0, 5.0], "size_mm": [10.0, 10.0, 10.0],
                 "figo": {"kind": "hybrid", "endometrial": 2, "serosal": 5}},
            ],
        }
        widget.rebuild_roi_nodes()
        widget.refresh_ui()
        rows = [widget.lesion_list.item(row).text() for row in range(widget.lesion_list.count)]
        assert rows == ["M01 · FIGO 3", "M02 · Hybrid 2-5"], rows
        assert not widget.figo_card.is_open, "filling the read-only list opened a card"
        assert not any(c.enabled for c in (widget.add_button, widget.delete_button, widget.zero_check,
                                           widget.reveal_button, *widget.early_stop_buttons))
        widget.lesion_list.setCurrentRow(1)
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == "M02"
        assert _card_child(widget, "figo_hybrid").checked
        assert not _card_child(widget, "figo_type_3").enabled, "a read-only card can be edited"
        assert widget.roi_label("M02")[0] == "M02 · Hybrid 2-5"
    finally:
        widget.cleanup()


def test_card_and_label_show_in_a_sagittal_view_whose_normal_is_minus_r() -> None:
    """The sagittal slice normal is -R, so GetSliceOffset() is -R: compare positions, not offsets."""

    widget, _first, _second = _build_case("sub-00079")
    try:
        off_midline = widget.logic.state.add_lesion((-41.4, 0.0, 0.0), (40.0, 40.0, 40.0))
        widget.rebuild_roi_nodes()
        widget.refresh_ui()
        widget.logic.activate_layout()
        widget.roi_overlay.bind_views()
        view_name = VIEW_NAMES["sagittal"]
        slice_node = slicer.app.layoutManager().sliceWidget(view_name).mrmlSliceNode()
        slice_to_ras = vtk.vtkMatrix4x4()
        # Screen x = -A, screen y = +S, so the normal is x cross y = -R.
        for row, (x, y, z) in enumerate(((0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0))):
            slice_to_ras.SetElement(row, 0, x)
            slice_to_ras.SetElement(row, 1, y)
            slice_to_ras.SetElement(row, 2, z)
        slice_to_ras.SetElement(0, 3, -41.44)
        slice_node.GetSliceToRAS().DeepCopy(slice_to_ras)
        slice_node.UpdateMatrices()
        slicer.app.processEvents()
        assert abs(slice_node.GetSliceOffset() - 41.44) < 1e-3, slice_node.GetSliceOffset()

        widget.open_figo_card(off_midline, view_name)
        slicer.app.processEvents()
        assert widget.figo_card.is_open and widget.figo_card.lesion_id == off_midline
        assert not widget.figo_card.widget.isHidden(), "the card hid on a sagittal slice through its box"
        entry = next(e for e in widget.roi_overlay.entries.values() if e["public_id"] == off_midline)
        assert entry["actors"][view_name].GetVisibility(), "the box's label is not drawn in sagittal"

        # The mirrored slice (R = +41.44, offset -41.44) misses the box.
        slice_node.GetSliceToRAS().SetElement(0, 3, 41.44)
        slice_node.UpdateMatrices()
        slicer.app.processEvents()
        assert widget.figo_card.widget.isHidden(), "the card showed on the mirrored sagittal slice"
        assert not entry["actors"][view_name].GetVisibility(), "the label showed on the mirrored slice"
    finally:
        widget.cleanup()


def _outlines_shown(widget: MyomaReviewLiteWidget) -> set[str]:
    return {lid for lid, node in widget.roi_outline_nodes.items() if node.GetDisplayVisibility()}


def _click_list(widget: MyomaReviewLiteWidget, row: int | None) -> None:
    """A real mouse click on a list row, or on the empty list below the rows."""

    view = widget.lesion_list
    if row is None:
        last = view.visualItemRect(view.item(view.count - 1))
        pos = qt.QPoint(last.center().x(), min(last.bottom() + 20, view.viewport().height - 2))
        assert view.itemAt(pos) is None, "the empty-list point hit a row"
    else:
        pos = view.visualItemRect(view.item(row)).center()
    for kind in (qt.QEvent.MouseButtonPress, qt.QEvent.MouseButtonRelease):
        event = qt.QMouseEvent(kind, qt.QPointF(pos), qt.Qt.LeftButton, qt.Qt.LeftButton, qt.Qt.NoModifier)
        qt.QApplication.sendEvent(view.viewport(), event)
    slicer.app.processEvents()


def test_a_selected_myoma_shows_alone_and_no_selection_shows_all() -> None:
    widget, first, second = _build_case("sub-00090")
    try:
        widget.logic.activate_layout()
        widget.roi_overlay.bind_views()
        _center_views_on(SMALL_BOX_INSIDE)
        assert widget._selected_lesion_id() is None, "a row was picked for the reader"
        assert _outlines_shown(widget) == {first, second}

        def labels_shown() -> set[str]:
            return {
                e["public_id"] for e in widget.roi_overlay.entries.values()
                if e["actors"]["MyomaLiteAxial"].GetVisibility()
            }

        assert labels_shown() == {first, second}
        widget.lesion_list.setCurrentRow(_row(widget, second))
        assert _outlines_shown(widget) == {second}, _outlines_shown(widget)
        assert labels_shown() == {second}, "the hidden box kept its label"
        widget.clear_lesion_selection()
        assert widget.active_roi_id is None and widget._selected_lesion_id() is None
        assert not widget.figo_card.is_open
        assert _outlines_shown(widget) == {first, second}
        assert labels_shown() == {first, second}
        # Esc in the list does the same.
        widget.lesion_list.setCurrentRow(_row(widget, first))
        assert _outlines_shown(widget) == {first}
        widget.lesion_list.findChild(qt.QShortcut).activated()
        assert _outlines_shown(widget) == {first, second}, "Esc in the list did not deselect"
    finally:
        widget.cleanup()


def test_clicking_the_selected_row_or_the_empty_list_deselects() -> None:
    widget, first, second = _build_case("sub-00091")
    try:
        widget.logic.activate_layout()
        widget.parent.show()
        slicer.app.processEvents()
        _click_list(widget, _row(widget, second))
        assert widget.active_roi_id == second and widget.figo_card.is_open, "the first click did not select"
        _click_list(widget, _row(widget, second))
        assert widget.active_roi_id is None, "clicking the selected row did not deselect"
        assert widget._selected_lesion_id() is None and not widget.figo_card.is_open
        assert _outlines_shown(widget) == {first, second}

        _click_list(widget, _row(widget, first))
        assert widget.active_roi_id == first
        widget.close_figo_card()
        _click_list(widget, _row(widget, first))
        assert widget.active_roi_id == first and widget.figo_card.is_open, (
            "a row whose card was closed with Esc should bring the card back"
        )
        _click_list(widget, None)
        assert widget.active_roi_id is None, "clicking the empty list did not deselect"
    finally:
        widget.parent.hide()
        widget.cleanup()


def _slice_offsets() -> list[float]:
    manager = slicer.app.layoutManager()
    return [manager.sliceWidget(name).mrmlSliceNode().GetSliceOffset() for name in VIEW_NAMES.values()]


def test_tabs_appear_after_reveal_and_switch_pages_without_moving_the_views() -> None:
    widget, first, second = _revealed_case("sub-00092")
    try:
        widget.logic.activate_layout()
        assert widget.page_tabs.isHidden(), "the tabs show before AI is revealed"
        assert not widget.nothing_to_box_section.isHidden()
        widget.logic.segmentation_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode")
        widget.page_stack.setCurrentIndex(1)
        widget.refresh_ui()
        assert not widget.page_tabs.isHidden(), "the tabs are missing after reveal"
        assert widget.nothing_to_box_section.isHidden()
        assert [widget.page_tabs.tabText(i) for i in range(widget.page_tabs.count)] == ["Segmentation", "FIGO"]
        assert widget.page_tabs.currentIndex == 0

        _center_views_on(SMALL_BOX_INSIDE)
        before = _slice_offsets()
        widget.page_tabs.setCurrentIndex(1)
        assert widget.page_stack.currentIndex == 0, "the FIGO tab did not show the myoma page"
        assert widget.active_roi_id is None and _outlines_shown(widget) == {first, second}
        widget.on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)
        assert _outlines_shown(widget) == {second}
        widget.page_tabs.setCurrentIndex(0)
        assert widget.page_stack.currentIndex == 1
        assert not widget.figo_card.is_open
        widget.page_tabs.setCurrentIndex(1)
        assert widget.active_roi_id is None, "the FIGO tab kept an old selection"
        assert _slice_offsets() == before, "switching tabs moved the slice views"
        assert widget.logic.segmentation_node.GetDisplayNode().GetVisibility(), "switching tabs hid the mask"

        # A box still being placed keeps the reader on the FIGO tab.
        widget.accept_rectangle(BoxGeometry((30.0, 30.0, 30.0), (12.0, 12.0, 12.0)), None)
        widget.page_tabs.setCurrentIndex(0)
        assert widget.page_stack.currentIndex == 0 and widget.page_tabs.currentIndex == 1
        widget.cancel_roi_edit()
    finally:
        widget.logic.segmentation_node = None
        widget.cleanup()


TESTS = (
    test_a_selected_myoma_shows_alone_and_no_selection_shows_all,
    test_clicking_the_selected_row_or_the_empty_list_deselects,
    test_tabs_appear_after_reveal_and_switch_pages_without_moving_the_views,
    test_saved_boxes_are_thin_and_have_no_handle_circles,
    test_clicking_a_box_reveals_only_that_box,
    test_selecting_a_row_selects_that_box,
    test_selecting_a_row_while_drawing_leaves_the_drawing_alone,
    test_after_reveal_the_annotation_page_keeps_click_to_edit,
    test_dragging_a_revealed_box_is_saved_and_undoable,
    test_a_second_drag_of_the_same_box_is_not_lost,
    test_undo_straight_after_a_drag_restores_that_drag,
    test_saving_while_a_box_is_in_hand_keeps_it_bold,
    test_a_box_nested_in_the_revealed_one_is_reached_by_deselecting_first,
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
    test_after_reveal_the_reader_can_still_add_a_box,
    test_only_a_completed_read_only_case_offers_reopen,
    test_navigation_stays_available_while_placing_a_box,
    test_early_stops_sit_with_zero_myomas_on_the_annotation_page,
    test_removed_buttons_are_gone_and_v_shows_the_myomas,
    test_the_redraw_button_is_gone,
    test_undo_and_redo_keys_reach_the_module_while_it_is_open,
    test_the_case_comment_folds_away_and_is_saved,
    test_reload_also_drops_the_helper_package,
    test_saving_a_new_box_opens_its_card,
    test_clicking_a_box_opens_its_card_and_empty_image_closes_it,
    test_picking_a_row_opens_that_card,
    test_a_card_choice_is_saved_and_undoable,
    test_card_sits_beside_the_box_not_on_it,
    test_card_hides_when_the_slice_leaves_the_box,
    test_undo_refreshes_or_closes_the_open_card,
    test_reveal_saves_text_typed_on_the_card_into_the_blinded_snapshot,
    test_the_card_never_shows_on_the_correction_page,
    test_escape_in_a_view_closes_the_card,
    test_labels_show_the_figo_status,
    test_reveal_is_gated_with_a_tooltip_naming_missing_myomas,
    test_figo_any_order_before_reveal,
    test_the_card_works_after_reveal,
    test_a_refresh_keeps_a_half_made_card_choice,
    test_a_no_op_undo_leaves_the_card_alone,
    test_the_card_hides_while_its_box_is_not_drawn,
    test_a_read_only_list_shows_figo_and_opens_the_card_read_only,
    test_card_and_label_show_in_a_sagittal_view_whose_normal_is_minus_r,
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
