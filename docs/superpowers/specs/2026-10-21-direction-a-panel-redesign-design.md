# Direction A panel redesign: design

Date: 2026-10-21. Status: agreed with the reader (all recommended defaults accepted).

Visual reference: `design/redesign-preview.html`, top row "Direction A · Hanken Grotesk + Warm monochrome" (boards A1–A6). The rows below it are history.

## What changes

The MyomaReviewLite module panel is restyled and restructured. Data formats, the review workflow and the slice views do not change.

### Look

- Font: **Hanken Grotesk**, bundled with the module (SIL OFL 1.1, licence file shipped next to the fonts), loaded at module start. Falls back to Segoe UI if loading fails.
- Colours: **Warm monochrome**. The highlight colour is cream with dark text; status colours keep their meaning.

| Token | Value | Token | Value |
|---|---|---|---|
| BG | `#1b1917` | TEXT | `#efebe6` |
| SURFACE | `#24211f` | MUTED | `#a9a199` |
| SURFACE_HOVER | `#2a2724` | ACCENT (cream) | `#e8e2d9` |
| BUTTON | `#2f2b28` | ON_ACCENT | `#1b1917` |
| FIELD | `#141210` | ACCENT_SOFT (selected row) | `#363230` |
| BORDER | `#3a3532` | DISABLED_TEXT | `#8a8178` |
| GHOST_BORDER | `#5a534c` | | |

  Status colours (`STATUS_COLOURS`, `FIGO_SET_COLOURS`, `FIGO_MISSING_COLOURS`, `AMBER`) keep today's values.
- Scope: the module panel and the FIGO card only. The rest of Slicer stays in "Dark Slicer".
- Every text/background pair used must reach WCAG contrast 4.5:1.

### Layout (top to bottom)

1. **Header**: ‹ previous case · case ID (large) over "study · Reader name" · next case ›.
2. **Queue strip**: one button showing "Queue", "N of M finished · K need attention" and a segmented progress bar. Clicking it opens the **queue drawer** in place of the case content:
   - filter pills "To do", "Finished", "Attention", "All" (default "To do");
   - the search field;
   - the case list grouped under "NEEDS ATTENTION", "IN PROGRESS", "NEW" and "FINISHED";
   - "Manifest: name (N cases)" with a "Change manifest" link at the bottom.

   Picking a case closes the drawer. The Cases/Annotation tabs go away.
3. **Box / Segment switch** (replaces the earlier four-step bar): two buttons. "Box" shows the box/FIGO page; "Segment" shows the correction page and stays disabled until AI is revealed. The blind read's boxes are saved silently at reveal (`blinded_snapshot` in the draft and the final annotation) so the AI mask's effect on the reader can be measured; the panel does not show them separately.
4. **Page content**:
   - Myoma page: "MYOMAS · N", the myoma rows with their FIGO tags, Add box and Delete.
   - "Nothing to box?" folds into one line. It opens by itself when the case has no boxes.
   - Correction page (board A5): "SEGMENTS" rows (Myoma, Uterine wall, Uterine cavity, Nabothian cyst) with the V / Q key hint, a 3 × 2 "TOOLS" grid (Paint, Erase, Draw, Scissors, Islands, Smooth) and, for Paint and Erase, a brush panel (size in mm, sphere brush). Slicer's Segment Editor still does the editing behind them; its own buttons and segment table are hidden, and only the picked effect's options show. "Show ROI boxes" sits at the bottom.
5. **Bottom bar**: one hint line above three controls: a comment icon button, "Save draft", and the page's main button ("Reveal AI  →" or "Export corrected labels and complete").
   - The hint tells the reader what blocks them or what happens next.
   - The case comment opens as a pop-over above the bar; the icon shows a dot when a comment is written.
6. **Read-only completed case**: a green "Finished / Read-only. Nothing here can be changed." banner and a bottom bar with "Reopen case for editing" and "Next case  →".

### Previous / next case

They step through the queue in list order and skip Attention cases. Finished cases open read-only. Each button is disabled when nothing lies that way. Opening goes through the same guarded path as picking from the list.

### FIGO card (stays on the slice view)

- "FIGO type" is a framed, labelled group with 0–8 in one row of 9 buttons. Below it, "Hybrid" and "Uncertain" sit side by side.
- Hybrid adds two framed rows, "Endometrial side" 0–8 and "Serosal side" 0–8. While Hybrid is chosen, the FIGO type group collapses to a one-line bar ("FIGO type 0–8 · hidden while Hybrid is picked"), and clicking the bar expands it again.
- Uncertain reason (4 choices) and type-8 location (4 choices) become framed button groups in a 2×2 grid instead of dropdowns.
- The Differential diagnosis and Comment fields stay.

### Shortcuts

New action "Show ROI boxes", default key **Q**, in `SHORTCUT_ACTIONS`, so readers can rebind it or put it on a mouse button. Before shipping, check that Q is not already taken in Slicer. The checkbox shows its key the way the other bound buttons do.

## Not changing

- `domain.py` / `storage.py` / draft and annotation JSON
- FIGO validation
- Undo/redo behaviour
- Slice view layout, ROI boxes and in-view labels
- The early-stop meanings
- The reveal tooltip text `FIGO missing: M01, M02`
- The Segment Editor's effects
