# On-image FIGO Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-stage "box, then FIGO" blind flow with one annotate stage where FIGO, Ddx and a comment are set on a card that floats beside the box in the slice view, before and after AI reveal.

**Architecture:** Pure helpers (label text, card placement, missing-FIGO list) and the Qt `FigoCard` panel live in a new `MyomaReviewLiteLib/figo_card.py`. `MyomaReviewLiteWidget` owns one card, opens it on Save ROI / box click / list pick, positions it from the box's projection in the slice view, and routes its saves through the existing undoable state path. The left FIGO form, the FIGO sub-page and the FIGO focus mode are then deleted. The domain and storage layers do not change.

**Tech Stack:** Python 3.12, 3D Slicer 5.10 (PythonQt `qt`, `vtk`, `slicer`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-20-on-image-figo-card-design.md`

## Global Constraints

- All paths below are relative to `MyomaReviewLite/` unless they start with `docs/` or `PILOT_FEATURE_GUIDE.md`.
- Data format unchanged: drafts, `annotations.json`, `blinded_snapshot`, lesion IDs, comments. No edits to `MyomaReviewLiteLib/domain.py` or `storage.py`.
- FIGO values come from the domain: `FIGOKind`, `Type8Location`, `UncertainReason`, standard types 0–8, hybrid endometrial and serosal each 0–8; validate with `FIGOClassification.from_dict`.
- Every save goes through `_remember_annotation()` → `ReviewState` setter → `_write_current_draft()` → `refresh_ui()`, so it is one Ctrl+Z step.
- Exactly one card exists at a time; it is never shown on the correction page (`page_stack` index 1).
- Label text format (exact): `M01 · FIGO 3`, `M01 · FIGO 8 (cervical)`, `M01 · Hybrid 2-5`, `M01 · Uncertain`, `M01 · FIGO ?`. Missing colour amber `(1.0, 0.6, 0.1)`; set colour stays `(1.0, 0.93, 0.2)`.
- Reveal tooltip (exact): `FIGO missing: M02, M04` (IDs in list order, comma-space separated); empty tooltip when nothing is missing.
- Slicer tests run unattended through `tests/run_slicer_test.py` (Task 1) which logs to a file and auto-answers modal dialogs.

## Review Focus

1. **A pending Ddx/comment typed on the card when the reader presses Reveal AI, completes, switches case or closes Slicer** — it must be saved first, and before reveal it must land in the blinded snapshot. Pinned in Task 3 (`test_reveal_saves_text_typed_on_the_card_into_the_blinded_snapshot`).
2. **Undo while a card is open** — undoing a FIGO must update the card's highlight; undoing the box's creation must close the card. Pinned in Task 3 (`test_undo_refreshes_or_closes_the_open_card`).
3. **A box near the view edge, or a view smaller than the card** — the card flips left, clamps inside the view, and only overlaps the box when there is no other room. Pinned in Task 1 (`test_card_position_*`).
4. **Windows display scaling (device pixel ratio 1.25–2)** — the card lands beside the box, not offset by the scale factor. Pinned in Task 1 (`test_card_position_divides_by_device_pixel_ratio`).
5. **Scrolling the slice off the box while its card is open** — the card hides and comes back when the slice crosses the box again. Pinned in Task 3 (`test_card_hides_when_the_slice_leaves_the_box`).

---

### Task 1: Pure helpers and the Slicer test runner

**Files:**
- Create: `MyomaReviewLiteLib/figo_card.py` (pure part only: no `qt`/`slicer` import at module top)
- Create: `tests/test_figo_card.py`
- Create: `tests/run_slicer_test.py`

**Interfaces:**
- Produces:
  - `figo_label(figo: Mapping[str, Any] | None) -> str` — `None` → `"FIGO ?"`; standard n → `"FIGO n"`; standard 8 → `"FIGO 8 (cervical)"` (location value with `_` → space); hybrid → `"Hybrid {endometrial}-{serosal}"`; uncertain → `"Uncertain"`.
  - `missing_figo_ids(state: ReviewState) -> list[str]` — active lesion IDs (list order) whose `figo is None`.
  - `card_position(box_rect: tuple[float, float, float, float], card_size: tuple[int, int], view_size: tuple[int, int], device_pixel_ratio: float = 1.0, gap: int = 8) -> tuple[int, int]` — `box_rect` is `(x_min, y_min, x_max, y_max)` in VTK slice-view XY (origin bottom-left, physical pixels); `card_size` and `view_size` are in logical Qt pixels (`view_size` = the slice view's `width`, `height`); returns the card's top-left in Qt widget coordinates (origin top-left, logical pixels).
  - `tests/run_slicer_test.py`: run as `Slicer.exe --no-splash --python-script tests/run_slicer_test.py`, reading env `MYOMA_TEST_SCRIPT` (test file) and `MYOMA_TEST_LOG` (log path); redirects stdout/stderr to the log, replaces `qt.QMessageBox.question/warning/critical/information` with a stub that logs `DIALOG …` and returns `No`, then `runpy.run_path(script, run_name="__main__")`.

- [ ] **Step 1: Write the failing tests** in `tests/test_figo_card.py`:

```python
def test_figo_label_formats_every_kind(self):
    self.assertEqual(figo_label(None), "FIGO ?")
    self.assertEqual(figo_label({"kind": "standard", "type": 3}), "FIGO 3")
    self.assertEqual(figo_label({"kind": "standard", "type": 8, "location": "round_ligament"}), "FIGO 8 (round ligament)")
    self.assertEqual(figo_label({"kind": "hybrid", "endometrial": 2, "serosal": 5}), "Hybrid 2-5")
    self.assertEqual(figo_label({"kind": "uncertain", "reason": "otherwise_not_classifiable"}), "Uncertain")

def test_missing_figo_ids_lists_unclassified_in_order(self):
    state = ReviewState.new("s", "r", "c")
    a, b, c = (state.add_lesion((i, 0, 0), (1, 1, 1)) for i in range(3))
    state.set_figo(b, {"kind": "standard", "type": 1})
    self.assertEqual(missing_figo_ids(state), [a, c])

def test_card_position_prefers_the_right_of_the_box(self):
    # view 400x300, box x 100..150, y(bottom-up) 200..250 -> top in Qt = 300-250 = 50
    self.assertEqual(card_position((100, 200, 150, 250), (120, 90), (400, 300)), (158, 50))

def test_card_position_flips_left_near_the_right_edge(self):
    self.assertEqual(card_position((300, 200, 380, 250), (120, 90), (400, 300)), (172, 50))

def test_card_position_clamps_inside_the_view(self):
    x, y = card_position((10, 0, 390, 20), (120, 90), (400, 300))
    self.assertTrue(0 <= x <= 400 - 120 and 0 <= y <= 300 - 90)

def test_card_position_divides_by_device_pixel_ratio(self):
    # same box in physical pixels at dpr 2 -> same logical answer as dpr 1
    self.assertEqual(card_position((200, 400, 300, 500), (120, 90), (400, 300), device_pixel_ratio=2.0),
                     card_position((100, 200, 150, 250), (120, 90), (400, 300)))
```

- [ ] **Step 2: Run** `python -m unittest tests.test_figo_card -v` from `MyomaReviewLite/`. Expected: FAIL (`ModuleNotFoundError: MyomaReviewLiteLib.figo_card`).

- [ ] **Step 3: Implement** the three functions in `MyomaReviewLiteLib/figo_card.py`. `card_position`: convert `box_rect` to logical pixels (divide by `device_pixel_ratio`), flip y (`top = view_h - y_max`), try `x = x_max + gap`, else `x = x_min - gap - card_w`, else clamp; `y = top` clamped to `[0, view_h - card_h]`; `x` clamped to `[0, view_w - card_w]`; return ints (round). Also write `tests/run_slicer_test.py` per the Interfaces block.

- [ ] **Step 4: Run** `python -m unittest tests.test_figo_card -v`. Expected: 6 tests OK. Then `python -m unittest discover -s tests -p "test_*.py"`: only the `*_slicer` import errors remain.

- [ ] **Step 5: Commit** `git add MyomaReviewLiteLib/figo_card.py tests/test_figo_card.py tests/run_slicer_test.py && git commit -m "Add FIGO card helpers and an unattended Slicer test runner"`

---

### Task 2: The `FigoCard` Qt panel

**Files:**
- Modify: `MyomaReviewLiteLib/figo_card.py` (add the class; import `qt` inside `__init__`, as `correction_tools.py` does)
- Create: `tests/test_figo_card_slicer.py` (same header/`TESTS`/exit pattern as `tests/test_roi_handles_slicer.py`)

**Interfaces:**
- Consumes: `figo_label` (Task 1); domain enums `FIGOKind`, `Type8Location`, `UncertainReason`.
- Produces: `class FigoCard`:
  - `__init__(self, on_figo: Callable[[str, dict], None], on_text: Callable[[str, str, str], None], on_close: Callable[[], None])` — `on_figo(lesion_id, figo_dict)` fires on every complete choice; `on_text(lesion_id, ddx, comment)` fires from `close()` only when the text changed since `open()`; `on_close()` fires after.
  - `widget` (a `qt.QFrame`, frameless, `autoFillBackground`, initially hidden, `objectName "MyomaFigoCard"`).
  - `open(self, parent: Any, lesion_id: str, figo: dict | None, ddx: str, comment: str, read_only: bool) -> None` — reparents `widget` to `parent`, fills it, shows and raises it.
  - `close(self) -> None` — idempotent; flushes text via `on_text`, hides, then `on_close()`.
  - `set_figo(self, figo: dict | None) -> None` — refresh highlight and inline rows without firing `on_figo`.
  - `move_to(self, x: int, y: int) -> None`; `size() -> tuple[int, int]`; `lesion_id: str | None`; `is_open: bool`.
  - Child object names used by tests: buttons `figo_type_0` … `figo_type_8`, `figo_hybrid`, `figo_uncertain`; inline combos `figo_location`, `figo_endometrial`, `figo_serosal`, `figo_reason` (each with a leading empty "choose…" item, data `None`); line edits `figo_ddx`, `figo_comment`; close button `figo_close`.
  - An `Esc` `qt.QShortcut` on `widget` with `qt.Qt.WidgetWithChildrenShortcut` calls `close()`.

- [ ] **Step 1: Write the failing tests** in `tests/test_figo_card_slicer.py` (helper `_card()` builds a `FigoCard` with recording callbacks and opens it on a plain `qt.QWidget` parent of size 400×300):
  - `test_a_type_button_saves_at_once`: click `figo_type_3` → `on_figo` calls `== [("M01", {"kind": "standard", "type": 3})]`; button `figo_type_3` is checked.
  - `test_type_8_waits_for_a_location`: click `figo_type_8` → no call, `figo_location` visible; choose `"cervical"` → one call `{"kind": "standard", "type": 8, "location": "cervical"}`.
  - `test_hybrid_waits_for_both_parts`: click `figo_hybrid`, set endometrial 2 → no call; set serosal 5 → `{"kind": "hybrid", "endometrial": 2, "serosal": 5}`; change serosal to 6 → second call.
  - `test_uncertain_saves_after_a_reason`: click `figo_uncertain`, choose `"both_interfaces_unclear"` → one call.
  - `test_text_is_flushed_on_close_only_when_changed`: open with ddx `"a"`, close → no `on_text`; reopen, type ddx `"b"`, close → `[("M01", "b", "")]`; `on_close` called each time.
  - `test_set_figo_updates_without_firing`: `set_figo({"kind": "standard", "type": 5})` → `figo_type_5` checked, no `on_figo`.
  - `test_read_only_disables_every_control`: open with `read_only=True` → every button/combo/line edit except `figo_close` disabled.

- [ ] **Step 2: Run** `MYOMA_TEST_SCRIPT=tests/test_figo_card_slicer.py MYOMA_TEST_LOG=<scratch>/card.log "<Slicer.exe>" --no-splash --python-script tests/run_slicer_test.py`, then `grep -E "PASS|FAIL" <scratch>/card.log`. Expected: FAIL (`FigoCard` missing).

- [ ] **Step 3: Implement `FigoCard`.** Type buttons are checkable `QToolButton`s in one `QButtonGroup` (exclusive); the inline row for 8 / Hybrid / Uncertain is shown only for the selected kind; build `figo` dicts and validate with `FIGOClassification.from_dict` before calling `on_figo`. Title label shows `"{lesion_id}  FIGO"`. Keep it compact: ≤ 240 px wide.

- [ ] **Step 4: Run** the command from Step 2. Expected: 7 PASS, 0 FAIL.

- [ ] **Step 5: Commit** `git commit -m "Add the FigoCard panel"` (both files).

---

### Task 3: Open, place and save the card from the widget

**Files:**
- Modify: `MyomaReviewLite.py` — `MyomaReviewLiteWidget.__init__`, `save_roi_edit` (~L3471), `on_view_clicked` (~L3370), `_on_lesion_row_changed` (~L2351), `on_delete_box`, `_show_page`, `on_reveal_ai`, `on_complete`, `on_early_stop`, `_teardown_current_case`, `_restore_annotation`, `_flush_pending_roi_drag`, `_write_current_draft`, `on_add_box`, `enter`/`exit`
- Test: `tests/test_roi_handles_slicer.py`

**Interfaces:**
- Consumes: `FigoCard` (Task 2), `card_position` (Task 1).
- Produces (on `MyomaReviewLiteWidget`):
  - `self.figo_card: FigoCard` (created in `_build_ui`).
  - `open_figo_card(self, lesion_id: str, view_name: str | None = None) -> None` — `view_name` defaults to the last view the reader clicked or drew in, else `VIEW_NAMES["sagittal"]`; parent is `slicer.app.layoutManager().sliceWidget(view_name).sliceView()`; no-op on page 1.
  - `close_figo_card(self) -> None` — `self.figo_card.close()`.
  - `_position_figo_card(self) -> None` — projects the box's 8 RAS corners through the inverse of `sliceNode.GetXYToRAS()` to XY, takes min/max, calls `card_position(..., device_pixel_ratio=view.devicePixelRatioF())`; hides the card (without closing) when the view's slice offset is outside the box's RAS bounds on that view's axis, shows it again when inside.
  - `_on_card_figo(self, lesion_id: str, figo: dict) -> None` and `_on_card_text(self, lesion_id: str, ddx: str, comment: str) -> None` — the undoable save path (Global Constraints).
  - `self._last_view_name: str | None` — set in `on_view_clicked` and when a drawn rectangle is accepted.
  - `self._card_escape_shortcuts: dict[str, Any]` — per-view Esc shortcuts (see Step 3).

- [ ] **Step 1: Write the failing tests** in `tests/test_roi_handles_slicer.py` (use the existing `_build_case`; call `widget.logic.activate_layout()` first):
  - `test_saving_a_new_box_opens_its_card` — `accept_rectangle(...)`, `save_roi_edit()` → `figo_card.is_open` and `figo_card.lesion_id == "M03"`.
  - `test_clicking_a_box_opens_its_card_and_empty_image_closes_it` — `on_view_clicked("MyomaLiteAxial", SMALL_BOX_INSIDE)` → card for `second`; `on_view_clicked(..., FAR_OUTSIDE)` → closed.
  - `test_picking_a_row_opens_that_card` — `lesion_list.setCurrentRow(0)` → card for `first`.
  - `test_a_card_choice_is_saved_and_undoable` — open card for `first`, click `figo_type_4` → `state.lesions[first].figo.type == 4`, draft on disk has it; `on_undo()` → `figo is None`.
  - `test_card_sits_beside_the_box_not_on_it` — open for `second` in axial; the card's geometry rectangle does not intersect the box's projected Qt rectangle.
  - `test_card_hides_when_the_slice_leaves_the_box` — open for `second`, move the axial slice offset 100 mm away → card hidden but `is_open`; move back → visible.
  - `test_undo_refreshes_or_closes_the_open_card` — set FIGO via card, `on_undo()` → no type button checked; then add a box, `save_roi_edit()`, `on_undo()` → card closed.
  - `test_reveal_saves_text_typed_on_the_card_into_the_blinded_snapshot` — set FIGO on both boxes via cards, type `figo_ddx` `"Leiomyoma"` on `second`'s open card without closing, `on_reveal_ai()` (patch `logic.load_ai_mask` and `_open_correction_page` to no-ops) → `blinded_snapshot` lesion `second` has `differential_diagnosis == "Leiomyoma"`, card closed.
  - `test_the_card_never_shows_on_the_correction_page` — open card, `_show_page(1)` → closed; `open_figo_card(first)` while on page 1 → stays closed.
  - `test_escape_in_a_view_closes_the_card` — open card, emit `activated()` on the axial view's Esc shortcut (`widget._card_escape_shortcuts["MyomaLiteAxial"]`) → closed.

- [ ] **Step 2: Run** the ROI suite through `tests/run_slicer_test.py`. Expected: the 9 new tests FAIL, existing ones PASS.

- [ ] **Step 3: Wire it up.** Open the card at the end of `save_roi_edit` (new box or edit), in `on_view_clicked` after `set_active_roi` (close when `lesion_id is None`), and in `_on_lesion_row_changed` after `_reveal_lesion`. Close it in `on_add_box` (before drawing), `on_delete_box`, `_show_page`, `_teardown_current_case`, and inside `_flush_pending_roi_drag` (which `on_reveal_ai`, `on_complete`, `on_early_stop` already call before persisting, so the text flush lands before the snapshot). In `_restore_annotation`, after restoring: close the card if its lesion no longer exists, else `figo_card.set_figo(...)`. Reposition from observers on the three slice nodes and on the open box's ROI node (`vtkCommand.ModifiedEvent`); remove them on close/teardown. Read-only cases open the card with `read_only=True`. In `_build_ui`-time view binding (where `click_selector.bind_views()` runs), create one `qt.QShortcut(qt.QKeySequence("Esc"), slice_widget)` per view with `qt.Qt.WidgetWithChildrenShortcut`, connected to `close_figo_card`, kept in `self._card_escape_shortcuts: dict[str, Any]` keyed by view name.

- [ ] **Step 4: Run** the ROI suite. Expected: all PASS (old two-stage tests still pass at this point).

- [ ] **Step 5: Commit** `git commit -m "Open the FIGO card beside the box and save from it"`

---

### Task 4: Remove the two-stage flow and the left FIGO form

**Files:**
- Modify: `MyomaReviewLite.py` — `SHORTCUT_ACTIONS` (drop `continue_to_figo`), `_build_ui` (~L1794–1902: drop `blind_stack`'s FIGO page, `continue_to_figo_button`, `back_to_boxing_button`, `figo_lesion_combo`, every `figo_*_combo`, `ddx_edit`, `lesion_comment_edit`, `apply_figo_button`; keep the boxing page content as the single blind page and move Save Draft / Reveal AI / Return to segmentation onto it), `refresh_ui`, `LiteRoiLabelOverlay._create_actors`/`refresh_view`
- Delete methods: `on_continue_to_figo`, `on_back_to_boxing`, `_on_figo_lesion_changed`, `_load_selected_annotation`, `_refresh_figo_controls`, `on_apply_figo`, `_offer_ai_reveal_after_last_figo`, `_figo_focus_active` and its uses in `_apply_roi_selection`/`_reveal_lesion`/`on_view_clicked`; field `_boxing_stage_done`; `LiteRoiLabelOverlay.only_id`/`set_only`
- Test: `tests/test_roi_handles_slicer.py`, `tests/test_widget_layout_slicer.py`, `tests/test_reopen_slicer.py`

**Interfaces:**
- Consumes: `figo_label`, `missing_figo_ids` (Task 1); `open_figo_card`, `_on_card_figo` (Task 3).
- Produces: `MyomaReviewLiteWidget.roi_label(self, lesion_id: str) -> tuple[str, tuple[float, float, float]]` — `(f"{lesion_id} · {figo_label(...)}", colour)`, reading `logic.state` or, for read-only cases, `logic.final_annotation["lesions"]`. `LiteRoiLabelOverlay` calls it in `refresh_view` (`actor.SetInput`, `prop.SetColor`); `refresh_ui` calls `roi_overlay.refresh()`.

- [ ] **Step 1: Write the failing tests / update existing ones** in `tests/test_roi_handles_slicer.py`:
  - add `test_labels_show_the_figo_status` — new case: label text for `first` is `"M01 · FIGO ?"` with amber; after `_on_card_figo(first, {"kind": "standard", "type": 3})` it is `"M01 · FIGO 3"` with `(1.0, 0.93, 0.2)`.
  - add `test_reveal_is_gated_with_a_tooltip_naming_missing_myomas` — fresh case: `reveal_button.enabled is False`, tooltip `"FIGO missing: M01, M02"`; set `first` → `"FIGO missing: M02"`; set `second` → enabled, tooltip `""`.
  - add `test_figo_any_order_before_reveal` — set FIGO on `second`, draw and save a third box, set FIGO on `first` then the third → `can_reveal`; at no point is a boxing/FIGO page switch needed (`not hasattr(widget, "continue_to_figo_button")`).
  - add `test_the_card_works_after_reveal` — `_revealed_case` helper rewritten to set FIGO via `_on_card_figo` and call `state.reveal()`; clicking a box opens its card; a choice updates `lesions` but not `blinded_snapshot`.
  - delete the tests for the removed flow: `test_figo_typing_opens_on_the_first_myoma_and_shows_only_its_box`, `test_choosing_a_myoma_in_figo_typing_shows_only_that_box`, `test_saving_figo_advances_to_and_shows_only_the_next_myoma`, `test_continuing_to_figo_puts_a_box_in_hand_down_and_keeps_its_drag`, `test_going_back_to_boxing_shows_every_box_again`, `test_saving_the_last_figo_after_reveal_does_not_offer_reveal`, `test_each_myoma_has_its_own_comment`, and rework `test_after_reveal_the_reader_can_go_back_and_add_a_box`, `test_early_stops_sit_with_zero_myomas_on_the_boxing_page` (early stops on the single blind page, disabled after reveal) to the single page.
  - `tests/test_reopen_slicer.py`: replace `on_back_to_boxing` / `on_continue_to_figo` / `state.set_figo` UI steps with `_on_card_figo`.
  - `tests/test_widget_layout_slicer.py`: adjust any assertion on removed widgets.

- [ ] **Step 2: Run** the ROI, layout and reopen suites. Expected: the new tests FAIL.

- [ ] **Step 3: Remove** the listed controls, methods and state; make the blind page single; list rows read `f"{lesion_id} · {figo_label(...)}"`; set the reveal tooltip from `missing_figo_ids`; give `LiteRoiLabelOverlay` the `roi_label` lookup.

- [ ] **Step 4: Run** all Slicer suites (`test_roi_handles_slicer`, `test_figo_card_slicer`, `test_widget_layout_slicer`, `test_reopen_slicer` with `MYOMA_REVIEW_LITE_TEST_QUEUE` set, `test_view_tools_slicer`, `test_alt_drag_slicer`, `test_ai_segment_normalization_slicer`) and `python -m unittest discover -s tests -p "test_*.py"`. Expected: every Slicer suite 0 FAIL; Python suite only `*_slicer` import errors. `grep -n "figo_lesion_combo\|_boxing_stage_done\|_figo_focus_active\|on_apply_figo\|continue_to_figo" MyomaReviewLite.py` returns nothing.

- [ ] **Step 5: Commit** `git commit -m "Replace the box-then-FIGO stages with the on-image FIGO card"`

---

### Task 5: Guide and final check

**Files:**
- Modify: `PILOT_FEATURE_GUIDE.md` (items "Myoma Ddx", "Correct FIGO", "Add a new myoma", "Edit an existing myoma", "Myoma box appearance", shortcuts list)

- [ ] **Step 1: Rewrite** those items for the new flow: draw a box → card opens beside it; click a box or pick a row to reopen it; one-click types, 8/Hybrid/Uncertain inline choices, Ddx and comment saved on close; amber `FIGO ?` labels; Reveal disabled with the missing list; same card after reveal; Esc or empty-image click closes. Remove "Continue to FIGO typing" from the shortcut list and every mention of "Save FIGO and Ddx", "← Back to myoma boxes" and the one-box FIGO focus.
- [ ] **Step 2: Verify** `grep -n "Save FIGO\|Back to myoma\|Continue to FIGO\|FIGO typing" PILOT_FEATURE_GUIDE.md` returns nothing.
- [ ] **Step 3: Commit** `git commit -m "Describe the on-image FIGO card in the feature guide"`
