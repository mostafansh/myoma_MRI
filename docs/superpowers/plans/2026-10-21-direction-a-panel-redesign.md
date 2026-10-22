# Direction A Panel Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the MyomaReviewLite panel as "Direction A" (header with previous/next case, queue drawer, step bar, bottom action bar, restyled FIGO card) in Hanken Grotesk and the Warm monochrome palette.

**Architecture:**
- **Look.** Colours, QSS and the bundled font loader live in `MyomaReviewLiteLib/theme.py` and a new `MyomaReviewLiteLib/fonts.py`.
- **Decisions in pure Python.** New `MyomaReviewLiteLib/panel_model.py` decides the step states, hint text, queue grouping and strip text, so it is unit-tested outside Slicer. The queue gains one `QueueSummary.neighbour` method.
- **Widgets.** `MyomaReviewLite.py` `_build_ui`/`refresh_ui` only build widgets and copy those decisions onto them. `figo_card.py` swaps its dropdowns for framed button groups.

**Tech Stack:** Python 3.12, 3D Slicer 5.10 (PythonQt `qt`, Qt 5.15), `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-21-direction-a-panel-redesign-design.md` (visual reference: `design/redesign-preview.html`, top row).

## Global Constraints

- **Paths:** all paths are relative to `MyomaReviewLite/` unless they start with `docs/`, `design/` or `PILOT_FEATURE_GUIDE.md`.
- **Data untouched:** no edits to `MyomaReviewLiteLib/domain.py` or `storage.py`. Drafts, `annotations.json` and blinded snapshots are unchanged.
- **Colour tokens:** exactly the spec's table.
  - `BG #1b1917`, `SURFACE #24211f`, `SURFACE_HOVER #2a2724`, `BUTTON #2f2b28`, `FIELD #141210`, `BORDER #3a3532`, `GHOST_BORDER #5a534c`.
  - `TEXT #efebe6`, `MUTED #a9a199`, `DISABLED_TEXT #8a8178`.
  - `ACCENT #e8e2d9`, `ON_ACCENT #1b1917`, `ACCENT_SOFT #363230`.
  - Status and FIGO tag colours keep today's values.
- **Contrast:** every text/background pair listed in `theme.CHECKED_PAIRS` must be ≥ 4.5.
- **Font:** `"Hanken Grotesk"`, with `"Segoe UI"` as the QSS fallback. Static TTFs go in `Resources/Fonts/` (Regular, Medium, SemiBold, Bold) together with `OFL.txt`.
- **Exact copy kept:**
  - `"Reveal AI  →"`, `"Export corrected labels and complete"`, `"Save draft"`, `"Reopen case for editing"`, `"Case comment"`, `"Case comment (written)"`, `"Open case manifest"`, `"Change manifest"`.
  - The early-stop texts `["No uterus", "Wrong image", "Cannot assess"]`.
  - The reveal tooltip `FIGO missing: M01, M02`.
- **New exact copy:**
  - Step titles `("Box", "FIGO", "Reveal AI", "Correct")`.
  - Filters `(("To do", "todo"), ("Finished", "finished"), ("Attention", "attention"), ("All", "all"))`, default `"todo"`.
  - Group headers `"NEEDS ATTENTION"`, `"IN PROGRESS"`, `"NEW"`, `"FINISHED"`.
  - `"Next case  →"`, `"Nothing to box? No uterus, wrong image, cannot assess"`.
  - Banner `"Finished"` / `"Read-only. Nothing here can be changed."`.
  - Card group titles `"FIGO type"`, `"Endometrial side"`, `"Serosal side"`, `"Reason"`, `"Location"`, and collapsed bar `"FIGO type 0–8 · hidden while Hybrid is picked"`.
- **Saves:** every save still goes `_remember_annotation()` → `ReviewState` setter → `_write_current_draft()` → `refresh_ui()`. Case changes still go through `save_coordinator.transition` (via `open_case`).
- **Pure tests:** run from `MyomaReviewLite/` as `python -m unittest discover -s tests -p "<file>" -v`.
- **Slicer tests** (PowerShell, from `MyomaReviewLite/`), called **SLICER_TEST `<file>`** below:

  ```powershell
  $env:MYOMA_TEST_SCRIPT="tests/<file>"; $env:MYOMA_TEST_LOG="$env:TEMP\myoma_<file>.log"; & "$env:LOCALAPPDATA\slicer.org\3D Slicer 5.10.0\Slicer.exe" --no-splash --python-script tests/run_slicer_test.py; Get-Content "$env:TEMP\myoma_<file>.log" -Tail 15
  ```

  Pass = the log ends with that test's `PASS` line and no traceback.
- **No commits:** the reader asked for no commits unless they say so. Each task ends with a checkpoint (`git status` plus `git diff --stat`), not a commit.

## Review Focus

1. **Previous/next pressed while a box is being placed, or with unsaved text on the FIGO card.** It must go through the same guarded transition as picking from the list (the save prompt, or a refusal), never silently drop work. Pinned in Task 3 (`test_next_case_uses_the_guarded_open_path`).
2. **Font files missing or failing to load** (old install, antivirus, bad path). The panel must still build, falling back to Segoe UI. Pinned in Task 1 (`test_load_bundled_fonts_returns_empty_for_a_missing_folder`).
3. **A search or filter that matches no case.** The drawer must show "No cases match" instead of a blank list. Pinned in Task 2 (`test_case_groups_is_empty_when_nothing_matches`) and Task 3 (`test_drawer_shows_empty_state`).
4. **A Hybrid case opened read-only.** The collapsed "FIGO type" bar may still be expanded so the reader can look, but every choice stays disabled. Pinned in Task 5 (`test_read_only_card_can_expand_but_not_change`).
5. **The comment pop-over after the panel is resized, or after switching pages.** It must stay attached above the bottom bar and must not cover the main button. Pinned in Task 4 (`test_comment_popover_sits_above_the_action_bar`).

---

### Task 1: Warm monochrome theme and bundled Hanken Grotesk

**Files:**
- Modify: `MyomaReviewLiteLib/theme.py:13-108` (tokens, `PANEL_QSS`, `CARD_QSS`), plus new helpers.
- Create: `MyomaReviewLiteLib/fonts.py`.
- Create: `Resources/Fonts/HankenGrotesk-Regular.ttf`, `-Medium.ttf`, `-SemiBold.ttf`, `-Bold.ttf`, `Resources/Fonts/OFL.txt`.
- Modify: `CMakeLists.txt` (`MODULE_PYTHON_RESOURCES` gains the five files).
- Modify: `MyomaReviewLite.py` `setup` (1321-1342), so that it calls the loader before `_build_ui`.
- Test: `tests/test_theme.py` (new, pure) and `tests/test_fonts_slicer.py` (new).

**Interfaces:**
- Produces:
  - `theme.contrast_ratio(fg: str, bg: str) -> float` (WCAG 2.x relative luminance).
  - `theme.CHECKED_PAIRS: tuple[tuple[str, str, str], ...]`, as (name, fg, bg). It must include text/BG, text/SURFACE, muted/BG, muted/SURFACE, muted/ACCENT_SOFT, ON_ACCENT/ACCENT, text/BUTTON, text/FIELD, and every status tag (fg, bg) pair.
  - `theme.FONT_FAMILY = "Hanken Grotesk"`.
  - New tokens `BUTTON`, `GHOST_BORDER`, `DISABLED_TEXT`, `ON_ACCENT`.
  - `fonts.load_bundled_fonts(font_dir: pathlib.Path) -> list[str]` returns the family names it loaded. It returns `[]` for a missing or empty folder, and it is idempotent: a second call returns the cached list and adds nothing.

- [ ] **Step 1: Write the failing pure tests** in `tests/test_theme.py`:

```python
def test_tokens_are_warm_monochrome(self):
    self.assertEqual((theme.BG, theme.SURFACE, theme.ACCENT, theme.ON_ACCENT, theme.ACCENT_SOFT),
                     ("#1b1917", "#24211f", "#e8e2d9", "#1b1917", "#363230"))

def test_contrast_ratio_matches_known_values(self):
    self.assertAlmostEqual(theme.contrast_ratio("#ffffff", "#000000"), 21.0, places=1)
    self.assertAlmostEqual(theme.contrast_ratio("#777777", "#ffffff"), 4.48, places=2)

def test_every_checked_pair_is_readable(self):
    for name, fg, bg in theme.CHECKED_PAIRS:
        with self.subTest(name):
            self.assertGreaterEqual(theme.contrast_ratio(fg, bg), 4.5)

def test_stylesheets_use_the_bundled_font_first(self):
    self.assertIn('font-family: "Hanken Grotesk", "Segoe UI"', theme.PANEL_QSS)
    self.assertIn('font-family: "Hanken Grotesk", "Segoe UI"', theme.CARD_QSS)
    self.assertNotIn("#4f86d1", theme.PANEL_QSS + theme.CARD_QSS)
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_theme.py" -v`. Expected: FAIL (old tokens; `contrast_ratio` missing).
- [ ] **Step 3: Update `theme.py`.**
  - Set the tokens and add `contrast_ratio`, `CHECKED_PAIRS` and `FONT_FAMILY`.
  - Rewrite both stylesheets from the tokens:
    - Primary buttons: `ACCENT` background, `ON_ACCENT` text.
    - Checked pills and card buttons: the same.
    - Disabled text: `DISABLED_TEXT`.
    - Ghost buttons: `GHOST_BORDER` border.
    - Links: `TEXT` with an underline.
  - The row delegate's selected fill stays `ACCENT_SOFT` with an `ACCENT` border.
- [ ] **Step 4: Run** the same command. Expected: PASS, 4 tests.
- [ ] **Step 5: Get the font files.**
  - Download the static TTFs and `OFL.txt` from the Hanken Grotesk upstream release (`github.com/marcologous/hanken-grotesk`; check the static TTF folder path when you get there).
  - If only the variable `HankenGrotesk[wght].ttf` is available (`github.com/google/fonts/tree/main/ofl/hankengrotesk`), make the four statics with `fontTools.varLib.instancer` at wght 400/500/600/700.
  - Copy them to `Resources/Fonts/` with the exact names above, and add them to `CMakeLists.txt`.
- [ ] **Step 6: Write the failing Slicer test** `tests/test_fonts_slicer.py`. It must assert:
  - `load_bundled_fonts(MODULE_ROOT / "Resources" / "Fonts")` contains `"Hanken Grotesk"`, and `"Hanken Grotesk" in qt.QFontDatabase().families()`.
  - A second call returns the same list and does not grow `QFontDatabase().families()`.
  - `test_load_bundled_fonts_returns_empty_for_a_missing_folder`: `load_bundled_fonts(Path("does/not/exist")) == []`.
  - It prints `MyomaReviewLite fonts: PASS`.
- [ ] **Step 7: Run** SLICER_TEST `test_fonts_slicer.py`. Expected: FAIL (`fonts` module missing).
- [ ] **Step 8: Implement `fonts.load_bundled_fonts`.** Load each `*.ttf` through `qt.QFontDatabase.addApplicationFont(str(path))` (`-1` means skip that file), and collect `applicationFontFamilies(id)`. Cache in a module-level dict keyed by the resolved folder. In `setup`, call it with `Path(__file__).parent / "Resources" / "Fonts"` before `_build_ui`.
- [ ] **Step 9: Run** SLICER_TEST `test_fonts_slicer.py` and SLICER_TEST `test_widget_layout_slicer.py`. Expected: both PASS.
- [ ] **Step 10: Checkpoint:** `git status; git diff --stat`.

### Task 2: Pure panel decisions and queue neighbours

**Files:**
- Create: `MyomaReviewLiteLib/panel_model.py`.
- Modify: `MyomaReviewLiteLib/queue.py` (`QueueSummary`, after `next_unfinished`).
- Modify: `MyomaReviewLite.py:2214` (`_matches_case_search` becomes a call to `panel_model.matches_case_search`).
- Test: `tests/test_panel_model.py` (new) and `tests/test_queue.py` (add tests).

**Interfaces:**
- Produces:
  - `QueueSummary.neighbour(case_id: str, step: int) -> QueueEntry | None`. `step` is `+1` (next) or `-1` (previous), in list order. It skips `QueueStatus.ATTENTION`, never wraps, and returns `None` for an unknown `case_id`.
  - `panel_model.STEP_TITLES = ("Box", "FIGO", "Reveal AI", "Correct")`.
  - `panel_model.step_states(phase: str | None, lesion_count: int, zero_confirmed: bool, missing_figo: int) -> tuple[str, str, str, str]`. Each value is `"done"`, `"now"` or `"todo"`; the rules come from the tests below.
  - `panel_model.bottom_hint(phase: str | None, missing_ids: Sequence[str], lesion_count: int, zero_confirmed: bool) -> tuple[str, str]` returns `(text, tone)`, where tone is `"warning"`, `"ok"` or `"muted"`.
  - `panel_model.CASE_FILTERS` / `DEFAULT_CASE_FILTER = "todo"` (moved from `MyomaReviewLite.py:143-149`, new values per Global Constraints).
  - `panel_model.matches_case_search(case_id: str, needle: str) -> bool` (moved unchanged).
  - `panel_model.case_groups(entries: Sequence[QueueEntry], filter_key: str, needle: str) -> list[tuple[str, list[QueueEntry]]]`.
    - Groups come in the order NEEDS ATTENTION, IN PROGRESS, NEW, FINISHED, and empty groups are left out.
    - `todo` keeps everything except COMPLETE; `finished` keeps COMPLETE; `attention` keeps ATTENTION; `all` keeps everything.
  - `panel_model.queue_strip_text(counts: Mapping[QueueStatus, int]) -> str`.

- [ ] **Step 1: Write the failing tests** in `tests/test_panel_model.py` and `tests/test_queue.py`:

```python
def test_step_states_follow_the_blind_read(self):
    self.assertEqual(step_states(None, 0, False, 0), ("todo", "todo", "todo", "todo"))
    self.assertEqual(step_states("blind", 0, False, 0), ("now", "todo", "todo", "todo"))
    self.assertEqual(step_states("blind", 3, False, 1), ("done", "now", "todo", "todo"))
    self.assertEqual(step_states("blind", 3, False, 0), ("done", "done", "now", "todo"))
    self.assertEqual(step_states("blind", 0, True, 0), ("done", "done", "now", "todo"))
    self.assertEqual(step_states("ai_revealed", 3, False, 0), ("done", "done", "done", "now"))
    self.assertEqual(step_states("complete", 3, False, 0), ("done",) * 4)
    self.assertEqual(step_states("terminal", 0, False, 0), ("done",) * 4)

def test_bottom_hint_says_what_blocks_reveal(self):
    self.assertEqual(bottom_hint("blind", [], 0, False), ("Box at least one myoma, or confirm there are none", "muted"))
    self.assertEqual(bottom_hint("blind", ["M02"], 3, False), ("M02 needs a FIGO type before you can reveal", "warning"))
    self.assertEqual(bottom_hint("blind", ["M02", "M03"], 3, False), ("M02, M03 need a FIGO type before you can reveal", "warning"))
    self.assertEqual(bottom_hint("blind", [], 3, False), ("All myomas have a FIGO type. Revealing locks your blind read.", "ok"))
    self.assertEqual(bottom_hint("blind", [], 0, True), ("No myomas confirmed. Revealing locks your blind read.", "ok"))
    self.assertEqual(bottom_hint("ai_revealed", [], 3, False), ("", "muted"))

def test_case_groups_order_and_filters(self):
    entries = [QueueEntry("sub-1", QueueStatus.COMPLETE), QueueEntry("sub-2", QueueStatus.NEW),
               QueueEntry("sub-3", QueueStatus.ATTENTION, "bad"), QueueEntry("sub-4", QueueStatus.IN_PROGRESS)]
    groups = case_groups(entries, "todo", "")
    self.assertEqual([title for title, _ in groups], ["NEEDS ATTENTION", "IN PROGRESS", "NEW"])
    self.assertEqual([e.case_id for e in case_groups(entries, "finished", "")[0][1]], ["sub-1"])
    self.assertEqual([t for t, _ in case_groups(entries, "all", "")][-1], "FINISHED")
    self.assertEqual([e.case_id for _, g in case_groups(entries, "all", "4") for e in g], ["sub-4"])

def test_case_groups_is_empty_when_nothing_matches(self):
    self.assertEqual(case_groups([QueueEntry("sub-1", QueueStatus.NEW)], "todo", "zzz"), [])

def test_queue_strip_text(self):
    self.assertEqual(queue_strip_text({QueueStatus.COMPLETE: 12, QueueStatus.NEW: 25, QueueStatus.ATTENTION: 3, QueueStatus.IN_PROGRESS: 0}),
                     "12 of 40 finished · 3 need attention")
    self.assertEqual(queue_strip_text({QueueStatus.COMPLETE: 1, QueueStatus.NEW: 1, QueueStatus.ATTENTION: 1, QueueStatus.IN_PROGRESS: 0}),
                     "1 of 3 finished · 1 needs attention")
    self.assertEqual(queue_strip_text({QueueStatus.COMPLETE: 0, QueueStatus.NEW: 2, QueueStatus.ATTENTION: 0, QueueStatus.IN_PROGRESS: 0}),
                     "0 of 2 finished")

# tests/test_queue.py
def test_neighbour_skips_attention_and_never_wraps(self):
    summary = QueueSummary((QueueEntry("sub-1", QueueStatus.COMPLETE), QueueEntry("sub-2", QueueStatus.ATTENTION, "x"),
                            QueueEntry("sub-3", QueueStatus.NEW)))
    self.assertEqual(summary.neighbour("sub-1", +1).case_id, "sub-3")
    self.assertEqual(summary.neighbour("sub-3", -1).case_id, "sub-1")
    self.assertIsNone(summary.neighbour("sub-3", +1))
    self.assertIsNone(summary.neighbour("sub-1", -1))
    self.assertIsNone(summary.neighbour("nope", +1))
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_panel_model.py" -v` and `... -p "test_queue.py" -v`. Expected: FAIL (module and method missing).
- [ ] **Step 3: Implement** `panel_model.py` and `QueueSummary.neighbour`. The module imports nothing from Qt. Point `MyomaReviewLite.py` at the moved `CASE_FILTERS` / `DEFAULT_CASE_FILTER` / `matches_case_search`.
- [ ] **Step 4: Run** both commands, then `... -p "test_queue_workflow.py" -v`. Expected: all PASS.
- [ ] **Step 5: Checkpoint:** `git status; git diff --stat`.

### Task 3: Header with previous/next and the queue drawer

**Files:**
- Modify: `MyomaReviewLite.py`:
  - `_build_ui` header and Cases tab (1607-1704).
  - `_load_case` labels (2321-2330).
  - `_refresh_case_list` (2173), `_refresh_progress` (2239), `on_case_chosen` (2278), `_focus_annotation_tab` (1978).
- Modify: `MyomaReviewLiteLib/theme.py`: `ROW_HEADER = 0x0100 + 14`; the delegate draws header rows as 11px semibold `MUTED` text with 1px letter spacing, 26px high, not selectable.
- Create: `Resources/Icons/chevron-left.svg`, `chevron-right.svg`, `chevron-down.svg` (stroke icons in `MUTED`); add them to `CMakeLists.txt`.
- Test: `tests/test_widget_layout_slicer.py` (rewrite its assertions) and `tests/test_queue_switching_slicer.py` (adapt to the new filter keys and the drawer).

**Interfaces:**
- Consumes: `QueueSummary.neighbour`, `case_groups`, `queue_strip_text`, `CASE_FILTERS` (Task 2).
- Produces these widget attributes:
  - `prev_case_button`, `next_case_button` (QToolButton, icon-only, `toolTip` "Previous case" / "Next case").
  - `study_label` (the large case ID), `case_header` ("study · Reader name").
  - `queue_toggle` (checkable QToolButton holding the strip text and the bar).
  - `content_stack` (QStackedWidget): index 0 is `annotation_tab`, index 1 is `queue_drawer`.
  - `queue_drawer` (replaces `cases_tab`); `case_empty_label` ("No cases match", shown when `case_groups` is empty).
  - `manifest_label`, plus the existing `manifest_button`, `filter_buttons` (keys `todo`/`finished`/`attention`/`all`), `case_search`, `case_list`, `progress_segments`.
- Produces these methods:
  - `on_previous_case()` and `on_next_case()`. Both call `open_case(manifest_source, neighbour.case_id)` when a neighbour exists.
  - `_refresh_case_nav()`, which sets each button's `enabled` from whether `neighbour` returns anything.
  - `_focus_annotation_tab()` keeps its name: it unchecks `queue_toggle` and shows `content_stack` index 0.
- `tab_widget` and `cases_tab` are removed.

- [ ] **Step 1: Rewrite `tests/test_widget_layout_slicer.py`.**
  - Assert `slicer_layout.indexOf(widget.content_stack) >= 0` and `widget.content_stack.indexOf(widget.queue_drawer) == 1`.
  - Assert `widget.manifest_button.text == "Open case manifest"` and `list(widget.filter_buttons) == ["todo", "finished", "attention", "all"]`.
  - Assert `widget.filter_buttons["todo"].checked`.
  - Check the toggle: `queue_toggle.click()` → `content_stack.currentIndex == 1`; `_focus_annotation_tab()` → `0` and `not queue_toggle.checked`.
  - Add `test_drawer_shows_empty_state`: with a two-case manifest loaded, typing "zzz" into `case_search` makes `case_empty_label.visible` true and leaves `case_list` with no selectable rows.
  - Add `test_next_case_uses_the_guarded_open_path`: on a 3-case manifest with the 2nd case ATTENTION, with case 1 open:
    - Replace `open_case` with a recorder; `on_next_case()` records case 3.
    - Set the recorder to raise `TransitionBlocked`; the open case stays case 1.
    - `prev_case_button.enabled` is False on case 1.
  - Build the manifest fixture with the helpers `test_queue_switching_slicer.py` already uses.
- [ ] **Step 2: Run** SLICER_TEST `test_widget_layout_slicer.py`. Expected: FAIL (`content_stack` missing).
- [ ] **Step 3: Rebuild the header and the drawer.**
  - Header: `[prev] [study_label over case_header] [next]`, followed by `queue_toggle`.
  - Drawer contents, top to bottom: filter pills, then search, then `case_list` (with header items from `case_groups`; header items carry `ROW_HEADER` and no `Qt.UserRole`), then `case_empty_label`, then the `manifest_label` + `manifest_button` row.
  - `on_case_chosen` ignores items without `Qt.UserRole` and calls `_focus_annotation_tab()` after a successful open.
  - `_refresh_progress` fills the strip text from `queue_strip_text` and the segment widths (the legend label is removed).
  - Call `_refresh_case_nav()` at the end of `_load_case` and in `_refresh_progress`.
  - `status_label` stays where it is for now (Task 4 moves it).
- [ ] **Step 4: Update `tests/test_queue_switching_slicer.py`** for the filter keys (`unfinished` → `todo`) and for the case list now holding header rows (select by `Qt.UserRole`). Then run SLICER_TEST on `test_widget_layout_slicer.py`, `test_queue_switching_slicer.py` and `test_reopen_slicer.py`. Expected: all PASS.
- [ ] **Step 5: Checkpoint:** `git status; git diff --stat`.

### Task 4: Step bar, bottom action bar, comment pop-over, nothing-to-box fold, read-only banner

**Files:**
- Modify: `MyomaReviewLite.py`:
  - `_build_ui` annotation part (1715-1861).
  - `refresh_ui` (2356-2450), `_refresh_page_tabs` (1896), `_on_page_tab_changed` (1908), `_place_comment_bar` (1914), `_position_comment_overlay` (1925), `_refresh_comment_toggle` (2708), `_show_page` (2063).
  - Remove `PAGE_TAB_TITLES` / `PAGE_FOR_TAB` (138-139) and `correction_save_button`.
- Modify: `MyomaReviewLiteLib/theme.py`. Add QSS for:
  - role `step` (QToolButton; dynamic property `state` = done / now / todo; a 3px top border coloured `STATUS_COLOURS["complete"][0]` / `ACCENT` / `BORDER`);
  - role `footer` (QFrame; `SURFACE` background, `BORDER` top line);
  - role `banner` (QFrame; `FIGO_SET_COLOURS[1]` background);
  - QLabel roles `hint-warning` / `hint-ok` / `hint-muted` (`AMBER` / `FIGO_SET_COLOURS[2]` / `MUTED`).
- Create: `Resources/Icons/comment.svg` and `comment-written.svg` (the second one has a dot); add them to `CMakeLists.txt`.
- Test: `tests/test_roi_handles_slicer.py`, `tests/test_reopen_slicer.py` and `tests/test_case_switch_effect_preview_slicer.py` (update), plus `tests/test_action_bar_slicer.py` (new).

**Interfaces:**
- Consumes: `step_states`, `bottom_hint`, `STEP_TITLES` (Task 2); `_focus_annotation_tab`, `next_case_button`, `on_next_case` (Task 3).
- Produces these widget attributes:
  - `step_bar`; `step_buttons: list[qt.QToolButton]` (4 of them, text `"1 Box"` … `"4 Correct"`, property `state`).
  - `action_bar` (the footer QFrame, a child of `annotation_tab` and outside `page_stack`).
  - `status_label`, now the hint line inside `action_bar`.
  - `comment_toggle`: icon-only. Its `text` stays `"Case comment"` / `"Case comment (written)"`, and its icon switches to `comment-written.svg` when a comment is written.
  - Inside `action_bar`: `save_draft_button`, `reveal_button`, `complete_button`, `reopen_button`, `footer_next_button` ("Next case  →").
  - `nothing_to_box_toggle` (checkable QToolButton with the fold text) and `nothing_to_box_body` (the existing `zero_check` and `early_stop_buttons`). `nothing_to_box_section` still wraps both.
  - `readonly_banner`.
- Behaviour:
  - **Step bar clicks.** After reveal, clicking step 2 calls `_show_page(0)` and step 4 calls `_show_page(1)`. Steps 1 and 3 are not clickable, and before reveal no step is.
  - **Pages.** Page 0 shows `reveal_button` (while BLIND); page 1 shows `complete_button`. When the case is read-only and complete, both are hidden and `reopen_button` + `footer_next_button` are shown.
  - **Hint line.** `refresh_ui` sets `status_label` from `bottom_hint`. Its role is `hint-<tone>`; repolish after setting it. When the text is empty, the last `_set_status` message stays.
  - **Nothing-to-box fold.** It opens itself when `lesion_count == 0` on case load; after that, only the reader toggles it.
  - **Comment pop-over.** It is anchored with its bottom edge 6px above `action_bar`'s top and spans the panel width minus 24px. It is re-placed on resize and on every page change.

- [ ] **Step 1: Write the failing test `tests/test_action_bar_slicer.py`.** Use the blind-case fixture from `test_roi_handles_slicer.py`.
  - `[b.text for b in widget.step_buttons] == ["1 Box", "2 FIGO", "3 Reveal AI", "4 Correct"]`.
  - The `state` properties follow `step_states` for a case with 2 boxes and one FIGO missing: `["done", "now", "todo", "todo"]`.
  - `widget.status_label.text == "M02 needs a FIGO type before you can reveal"`.
  - `widget.reveal_button.parent()` is inside `widget.action_bar`, and `not widget.page_stack.isAncestorOf(widget.reveal_button)`.
  - `test_comment_popover_sits_above_the_action_bar`:
    - After `comment_toggle.checked = True`, the bottom of `case_comment_edit.geometry` (mapped to `annotation_tab`) is ≤ the top of `action_bar` minus 6.
    - After `annotation_tab.resize(360, 700)` and `_show_page(1)`, the same still holds.
    - The pop-over never intersects `reveal_button` or `complete_button`.
  - A case with 0 boxes opens with `nothing_to_box_body.visible`. After the reader collapses it and a box is added, it stays collapsed.
  - Read-only complete case: `readonly_banner.visible`, all `step_buttons` have `state == "done"`, `reopen_button.visible`, `footer_next_button.visible` and `not reveal_button.visible`.
  - Print `MyomaReviewLite action bar: PASS`.
- [ ] **Step 2: Run** SLICER_TEST `test_action_bar_slicer.py`. Expected: FAIL (`step_buttons` missing).
- [ ] **Step 3: Implement the layout and behaviour** described above. Delete `page_tabs` and its handlers, and route their callers to `_show_page`.
- [ ] **Step 4: Update the existing tests.**
  - `test_roi_handles_slicer.py`:
    - The `page_tabs` checks at 641-760 become `step_buttons` checks. Before reveal, no step is clickable. After reveal, `step_buttons[3].click()` → `page_stack.currentIndex == 1` and `step_buttons[1].click()` → `0`.
    - Its "descendant of `page_stack.widget(0)`" check now covers only `zero_check` and `lesion_list`. `save_draft_button` and `reveal_button` are instead checked as descendants of `action_bar`.
    - The `comment_toggle.text` assertions stay.
  - `test_reopen_slicer.py` and `test_case_switch_effect_preview_slicer.py`: replace `correction_save_button` with `save_draft_button`.
- [ ] **Step 5: Run** SLICER_TEST on `test_action_bar_slicer.py`, `test_roi_handles_slicer.py`, `test_reopen_slicer.py`, `test_case_switch_effect_preview_slicer.py` and `test_widget_layout_slicer.py`. Expected: all PASS.
- [ ] **Step 6: Checkpoint:** `git status; git diff --stat`.

### Task 5: FIGO card with framed number rows and the Hybrid collapse

**Files:**
- Modify: `MyomaReviewLiteLib/figo_card.py:55-362` (`FigoCard`).
- Modify: `MyomaReviewLiteLib/theme.py` `CARD_QSS`:
  - `QGroupBox` frame (1px `BORDER`, radius 6, `BG` fill, title in `MUTED` 12px);
  - the collapsed bar (`BG` fill, `BORDER` border, `MUTED` text);
  - checked buttons `ACCENT` / `ON_ACCENT`.
- Modify: `MyomaReviewLite.py` `_build_ui` (1862-1866): no change to the callbacks.
- Test: `tests/test_figo_card_slicer.py` (update and add tests).

**Interfaces:**
- Consumes: none new.
- Produces, keeping the public API: `FigoCard(on_figo, on_text, on_close, on_resize)`, `open`, `close`, `set_figo`, `has_unsaved_text`, `move_to`, `size`, `dispose`, `is_open`, `lesion_id`.
- objectNames:
  - Kept: `figo_type_0`…`figo_type_8`, `figo_hybrid`, `figo_uncertain`, `figo_close`, `figo_ddx`, `figo_comment`.
  - New:
    - `figo_type_group` (QGroupBox "FIGO type", 9 buttons in one row);
    - `figo_type_collapsed` (QToolButton bar);
    - `figo_endo_group` with `figo_endo_0`…`figo_endo_8`, and `figo_sero_group` with `figo_sero_0`…`figo_sero_8`;
    - `figo_reason_group` with `figo_reason_<UncertainReason.value>`, and `figo_location_group` with `figo_location_<Type8Location.value>` (each a 2×2 grid; labels are the value with `_` → space).
  - Removed: the four combos and the wheel guard. `dispose()` stays as a no-op.
- Behaviour:
  - Each side group is its own exclusive `QButtonGroup`. `_build_figo` reads the checked buttons. Emit only when the choice is complete, exactly as now.
  - Choosing Hybrid hides `figo_type_group` and shows `figo_type_collapsed`.
  - Clicking the bar shows the group again (and hides the bar) until the kind changes.
  - Choosing a type number while Hybrid is chosen switches to that standard type, as today.
  - Re-clicking the chosen kind keeps its side choices, as today.
  - The card's maximum width goes from 240 to 320.

- [ ] **Step 1: Add the failing tests** to `tests/test_figo_card_slicer.py`. Find children by objectName with `card.widget.findChild(qt.QAbstractButton, name)`.
  - Hybrid flow: clicking `figo_hybrid` → `figo_type_group.visible` is False and `figo_type_collapsed.visible` is True. Clicking `figo_endo_1` and then `figo_sero_6` → `on_figo` receives `("M01", {"kind": "hybrid", "endometrial": 1, "serosal": 6})` exactly once (after the second click).
  - Expanding: `figo_type_collapsed.click()` → `figo_type_group.visible`.
  - Uncertain: clicking `figo_uncertain`, then `figo_reason_both_interfaces_unclear` → `{"kind": "uncertain", "reason": "both_interfaces_unclear"}`.
  - Type 8: clicking `figo_type_8`, then `figo_location_cervical` → `{"kind": "standard", "type": 8, "location": "cervical"}`.
  - `set_figo({"kind": "hybrid", "endometrial": 2, "serosal": 5})` checks `figo_hybrid`, `figo_endo_2` and `figo_sero_5`, and collapses the type group.
  - `test_read_only_card_can_expand_but_not_change`: open read-only on a hybrid value. `figo_type_collapsed.enabled` is True, and clicking it shows the group. Every `figo_type_*`, `figo_endo_*`, `figo_sero_*`, `figo_reason_*` and `figo_location_*` button is disabled, and `on_figo` is never called.
  - Update the existing assertions that used `figo_endometrial` / `figo_serosal` / `figo_reason` / `figo_location` combos.
- [ ] **Step 2: Run** SLICER_TEST `test_figo_card_slicer.py`. Expected: FAIL (`figo_type_group` missing).
- [ ] **Step 3: Implement** the card as described. Read-only stays in `open()`: it disables every `QAbstractButton` and `QLineEdit` except `figo_close` and `figo_type_collapsed`.
- [ ] **Step 4: Run** SLICER_TEST on `test_figo_card_slicer.py` and `test_roi_handles_slicer.py`, and `python -m unittest discover -s tests -p "test_figo_card.py" -v`. Expected: all PASS.
- [ ] **Step 5: Checkpoint:** `git status; git diff --stat`.

### Task 6: "Show ROI boxes" shortcut (Q) and the feature guide

**Files:**
- Modify: `MyomaReviewLite.py`:
  - `SHORTCUT_ACTIONS` (158-166): add `("toggle_roi_boxes", "Show ROI boxes", "Q", "on_toggle_roi_boxes", "show_roi_check", "Show ROI boxes")`.
  - Add the method `on_toggle_roi_boxes()`.
  - `_refresh_shortcut_button_labels` (1389) must also accept a QCheckBox.
- Modify: `PILOT_FEATURE_GUIDE.md` (the layout and shortcut sections).
- Test: `tests/test_roi_handles_slicer.py` (add tests).

**Interfaces:**
- Consumes: `show_roi_check`, `on_show_roi_toggled` (existing).
- Produces: `on_toggle_roi_boxes() -> None`. It flips `show_roi_check.checked` only while page 1 is showing and the check is enabled; otherwise it does nothing.

- [ ] **Step 1: Add the failing tests.**
  - The `SHORTCUT_ACTIONS` entry for `"toggle_roi_boxes"` has default `"Q"`.
  - On the correction page, `widget.on_toggle_roi_boxes()` flips `show_roi_check.checked`. On page 0 it changes nothing.
  - `show_roi_check.text == "Show ROI boxes (Q)"`, in the same format `_refresh_shortcut_button_labels` gives buttons.
  - `test_q_is_not_taken_elsewhere`: no `QAction` under `slicer.util.mainWindow()` has `shortcut().toString() == "Q"`, and the only `QShortcut` with key `"Q"` is ours.
- [ ] **Step 2: Run** SLICER_TEST `test_roi_handles_slicer.py`. Expected: FAIL (the action is missing).
- [ ] **Step 3: Implement** the action and the label refresh.
  - Also check the slice-view key bindings that Slicer handles below Qt: press `Q` in a slice view with the module hidden, and confirm nothing changes.
  - If Q is taken at any level, stop and ask the reader for another key. Do not pick one yourself.
- [ ] **Step 4: Update `PILOT_FEATURE_GUIDE.md`** to describe:
  - the header and the previous/next buttons;
  - the queue drawer;
  - the step bar;
  - the bottom bar;
  - the comment pop-over;
  - the FIGO card rows;
  - the Q shortcut.

  Remove the "Keyboard shortcuts…" panel text at lines 46-50; that panel does not exist in the code.
- [ ] **Step 5: Run** SLICER_TEST `test_roi_handles_slicer.py`. Expected: PASS.
- [ ] **Step 6: Checkpoint:** `git status; git diff --stat`.

### Task 7: Full regression and a look in Slicer

**Files:** none new.

- [ ] **Step 1: Pure suite.** Run every non-Slicer test file from `tests/`: `test_contrast_effect_guard`, `test_contrast_math`, `test_dataset_manifest`, `test_figo_card`, `test_panel_model`, `test_queue`, `test_queue_workflow`, `test_reader_queue`, `test_review_annotations`, `test_roi_picking`, `test_theme`. Also run `python -m unittest discover -s ../tests -v` (repo-level `test_package_portability`, which must accept the new `Resources/Fonts` files). Expected: all PASS.
- [ ] **Step 2: Slicer suite.** Run SLICER_TEST on each `*_slicer.py` in `tests/`. Expected: every log ends in PASS.
- [ ] **Step 3: Look at it.**
  - Launch with `..\Start-MyomaReviewLite.ps1` against a test manifest.
  - Take screenshots of the 6 states (A1–A6): queue open, boxing with FIGO missing, Hybrid card, no boxes with the comment open, correction, and a completed read-only case.
  - Compare them side by side with `design/redesign-preview.html`'s top row and list every visible difference for the reader. Do not fix anything without asking.
- [ ] **Step 4: Checkpoint:** `git status; git diff --stat`, then hand over to the reader for review and any commit.
