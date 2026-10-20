# On-image FIGO card — design

Date: 2026-10-20
Module: `MyomaReviewLite` (3D Slicer 5.10 scripted module)

## Goal

Readers find the current "box everything, then type FIGO" flow rigid, and they
dislike choosing FIGO from the left panel. They should be able to classify a
myoma right after drawing its box, or at any later time before AI reveal, and
do it **on the image**, next to the box, so the work feels direct.

### What the reader asked for

- FIGO can be typed after each box, or whenever the reader likes, any time
  before AI reveal.
- FIGO is chosen on the images, not from the left panel.

### Decisions made in discussion

- The control is a **card next to the box** (not a right-click menu).
- The **same card** is used after AI reveal; the left FIGO form is removed.
- The card **saves instantly**; there is no Save button.

### Success criteria

- A reader can draw a box and set its FIGO without moving the mouse to the
  left panel.
- A reader can draw several boxes first and classify them later, in any order.
- At a glance on the image, the reader can see which boxes still lack FIGO.
- AI reveal still requires FIGO on every box (or a zero-myoma confirmation).
- The saved data (drafts, `annotations.json`, the blinded snapshot) is
  unchanged in format.

## 1. Workflow before AI reveal

The two blind sub-pages (boxing, then FIGO typing) merge into **one annotate
stage**. The left panel keeps:

- the myoma list, each row showing `FIGO set` or `FIGO missing`;
- **Add myoma box**, **Delete**, **Save ROI** / **Cancel ROI**;
- **Confirm zero myomas** and the early stops (No uterus, Wrong image,
  Cannot assess);
- **Save Draft** and **Reveal AI**.

The following are removed:

- "Done boxing → continue to FIGO typing" and "← Back to myoma boxes";
- the left FIGO form: Selected lesion, FIGO kind/type/location/hybrid/reason
  combos, Myoma Ddx, Myoma comment and "Save FIGO and Ddx";
- the blind FIGO focus mode (only the selected box shown, thin and
  handle-free, clicks ignored) and its `_boxing_stage_done` /
  `_figo_focus_active` state;
- the "Reveal AI now?" prompt after the last FIGO;
- the `continue_to_figo` shortcut.

Picking a row in the myoma list selects that box (handles shown), centres the
three views on it and opens its card.

## 2. The FIGO card

A small frameless Qt panel (new helper `MyomaReviewLiteLib/figo_card.py`,
class `FigoCard`) parented to the slice view where the box was drawn,
clicked, or, when opened from the list, the sagittal view.

### Placement

- Positioned beside the box's projection in that view: to the right of the
  box when there is room, otherwise to the left; vertically aligned with the
  box top and clamped inside the view.
- It never covers the box itself.
- It follows the box when the view pans, zooms or scrolls slices, and when
  the box is dragged; it hides when the view's slice no longer intersects the
  box and reappears when it does.

### Opening and closing

Opens when:

- a new box is saved with **Save ROI**;
- a box is clicked in a slice view;
- a row is picked in the myoma list.

Closes when:

- the reader clicks empty image (which also deselects the box);
- the reader presses **Esc** while the card or view has focus;
- another box is selected (its card replaces this one);
- the box is deleted, the page changes, AI is revealed, the case is
  completed or switched.

Only one card exists at a time.

### Contents

```
M01  FIGO                 [x]
[0] [1] [2] [3]
[4] [5] [6] [7]
[8]  [Hybrid]  [Uncertain]
  (inline row for 8 / Hybrid / Uncertain, see below)
Ddx      [__________________]
Comment  [__________________]
```

- The current classification is highlighted.
- **0–7**: one click sets and saves `{"kind": "standard", "type": n}`.
- **8**: shows an inline row of the location choices; picking one saves
  `{"kind": "standard", "type": 8, "location": ...}`. Until a location is
  picked nothing is saved.
- **Hybrid**: shows endometrial and serosal pickers (each 0–8, as the domain
  allows today); the value is saved once both are chosen, and again whenever
  either changes.
- **Uncertain**: shows the reason choices; picking one saves
  `{"kind": "uncertain", "reason": ...}`.
- The option lists come from the existing `FIGOClassification` domain values,
  so validation stays in one place.
- **Ddx** and **Comment** are single-line fields saved (with
  `set_differential_diagnosis` / `set_lesion_comment`) when the card closes
  or another box is selected, only if they changed.

### Saving and undo

- Every save goes through the existing path: `_remember_annotation()`, the
  `ReviewState` setter, `_write_current_draft()`, `refresh_ui()`.
- So each FIGO choice and each Ddx/comment change is one Ctrl+Z step.
- After undo/redo, an open card refreshes its highlight and fields from the
  state.

### While the card is open

- The selected box keeps its handles; dragging or resizing still works and the
  card moves with it.
- Add myoma box closes the card first (drawing needs the views).
- Read-only (completed) cases: clicking a box opens the card with every
  control disabled, so the saved values can be read.

## 3. Labels on the image

The existing `LiteRoiLabelOverlay` text gains the FIGO status:

- `M01 · FIGO 3`, `M01 · FIGO 8 (cervical)`, `M01 · Hybrid 2-5`,
  `M01 · Uncertain`, in the current label colour;
- `M01 · FIGO ?` in amber while FIGO is missing.

Labels refresh whenever FIGO changes, including after undo/redo. The same
format is used after reveal and on read-only cases.

## 4. Reveal gate

- **Reveal AI** stays disabled until `state.can_reveal` (every box has FIGO,
  or zero myomas is confirmed) — unchanged domain rule.
- Its tooltip names the myomas still missing FIGO, for example
  "FIGO missing: M02, M04".
- The list rows and amber labels show the same thing; picking a missing row
  opens its card.

## 5. After AI reveal

The annotation page works the same way: the same list, the same card, the
same labels, Add/Delete and Confirm zero myomas. The blinded snapshot stays
frozen, as today. The correction page is unchanged; the card is never shown
there.

## 6. Out of scope / unchanged

- Data format: drafts, `annotations.json`, `blinded_snapshot`, comments,
  lesion IDs, reopening completed cases.
- Domain rules in `MyomaReviewLiteLib/domain.py` (no change expected).
- Keyboard and mouse shortcuts other than the removed `continue_to_figo`;
  the segmentation page; the case comment bar.

## 7. Testing

Slicer runtime tests (`tests/test_roi_handles_slicer.py`) are reworked:

- the card opens after Save ROI, on clicking a box, and on picking a row;
- clicking empty image or Esc closes it; selecting another box replaces it;
- a one-click type (0–7) saves and is undoable with Ctrl+Z;
- type 8 saves only after a location; Hybrid only after both parts;
  Uncertain after a reason;
- Ddx and Comment save on close;
- labels read `FIGO ?` in amber until set, then the classification;
- Reveal AI is disabled with a tooltip naming missing myomas, and enabled
  once all are set;
- the card stays beside the box (not overlapping it) and follows a drag;
- after reveal the card works the same; on a read-only case it is disabled.

Tests for the removed two-stage flow (FIGO focus, continue/back to boxing,
the reveal prompt after the last FIGO) are deleted. `test_reopen_slicer.py`
and the layout test are updated to the new controls. Pure-Python tests should
need no change.

`PILOT_FEATURE_GUIDE.md` is updated to describe the new flow.
