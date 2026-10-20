# MyomaReviewLite pilot guide

The bundled module is for 3D Slicer 5.10. Open the supplied reader launcher,
load a case, and use the three patient-space T2 views. The panel has two
tabs: **Cases** (progress, All/Unfinished/Finished/Attention filters, a
search box, and the case list — click a case to open it) and **Annotation**
(the viewer and every review tool below). Opening a case always switches to
Annotation. The AI input and the exported correction use the same labels,
values 0–4:

| Value | Label | Use |
|---:|---|---|
| 0 | background | Empty voxels |
| 1 | uterine wall | Uterine wall |
| 2 | uterine cavity | Endometrium/cavity |
| 3 | myoma | All segmented myoma voxels |
| 4 | Nabothian cyst | Nabothian cyst |

The following controls address the pilot list:

1. **Myoma Ddx.** Select a myoma, enter the reader's differential diagnosis
   in **Myoma Ddx**, and choose **Save FIGO and Ddx**. It is saved per lesion
   and remains available after geometry edits and AI reveal. FIGO typing
   opens on the first myoma (M01), not the last one drawn. Before AI reveal,
   FIGO typing shows only the myoma chosen in **Selected lesion**, as a thin
   outline with no handles and no label on any other myoma, and centers the
   three T2 views on it. Clicking in a view does not select a box there, and
   **← Back to myoma boxes** brings every box back. In the boxing-stage list,
   picking a myoma selects its box (with handles) and centers the views too.
   Saving jumps to the next myoma in the list automatically, so the reader can
   page straight through all of them; saving the last myoma instead asks
   whether to reveal AI now.
2. **Undo.** `Ctrl+Z` undoes a Segment Editor change on the correction page,
   or the latest myoma-box/FIGO/Ddx annotation change on the annotation page.
   `Ctrl+Y` redoes it; there are no Undo/Redo buttons, only the shortcuts.
   The Segment Editor also has its native undo history.
   Every default key above can be changed: the **Keyboard shortcuts…** button
   in the module header opens a panel listing all rebindable actions (Undo,
   Redo, Add myoma box, Redraw box, Delete box, Continue to FIGO typing, Save
   draft, Show segmented myomas). Click a field, press the new key combination, and **Save** —
   bindings are remembered per Slicer user and the toolbar buttons update to
   show whatever is currently bound.
3. **True axial and coronal views.** **Reset true planes and fit** restores
   patient-space sagittal, axial, and coronal orientations and puts the
   corresponding T2 image in each view. If the source NIfTI images were
   already resampled, the module cannot reconstruct their original acquisition
   planes.
4. **Contrast.** In **Images and contrast**, choose one plane or **All three
   images**. Use **Automatic contrast** or enter Window and Level. This changes
   display settings only; the image voxels are unchanged.
5. **Edit the myoma mask.** On the correction page select the myoma segment
   in the Segment Editor and use its Paint, Draw or Erase effects (Draw: outline
   a closed region, then press Enter to fill it). `Ctrl+Z` is available after
   each operation.
6. **Correct FIGO.** On the annotation page select the lesion in the list,
   choose `standard`, `hybrid`, or `uncertain`, fill the visible fields, and
   save. Type 8 also requires its location. FIGO can be revised after AI
   reveal while the case is still unfinished.
7. **Add a new myoma.** Choose **Add myoma box**, drag a rectangle in any
    T2 view, adjust the 3-D handles if needed, and press **Save ROI**. You can
    also paint the myoma segment in the Segment Editor after AI reveal for a
    mask-only correction.
8. **Edit an existing myoma.** While boxing, or on the annotation page after
    AI reveal, click the box in any T2 view to reveal its handles, then drag them to move or resize it; the new
    box is saved to the draft as soon as you click elsewhere, change page, save,
    or reveal AI, and `Ctrl+Z` undoes it. You can also
    select its row and choose **Redraw** to draw the box again from scratch, or
    use Paint, Draw, and Erase on the myoma segment to edit its mask. Save the
    draft before changing cases.
9. **More than six myomas.** There is no six-lesion limit. Each annotation
    receives the next ID (`M01`, `M02`, …), and all lesions share the myoma
    segmentation label. Deleted IDs are not reused.
10. **Myoma box appearance.** Every box is a one-pixel outline with no handle
    circles, so the T2 underneath stays readable. The weight does not change as
    you work: the rectangle you drag out, the box you adjust before **Save
    ROI**, and every saved box are all drawn the same way. The box currently in
    hand is simply **bolder** — two pixels instead of one. The circles belong to
    one box at a time and appear only when you click that box on the annotation
    page; clicking away from every box hides them again. Where a box must not
    move — a completed read-only case, or the segmentation page — the click
    still highlights the box but no circles appear, because in Slicer the
    circles are the drag control itself.
11. **See segmented myoma.** Press `V` (rebindable) once AI is revealed. The myoma overlay is shown and all three views jump to an occupied
    myoma voxel. Scroll through the views to inspect separate lesions.

Reveal captures the original blinded boxes, FIGO values, differential
and diagnoses. Later corrections are saved separately, so the pre-AI record
remains auditable. A completed case is read-only (its saved corrected labels can
be inspected with `V`) until it is reopened with **Reopen case for editing**.

For the underlying Slicer behavior, see the official [Segment Editor
documentation](https://slicer.readthedocs.io/en/5.8/user_guide/modules/segmenteditor.html)
and the [qMRMLSegmentEditorWidget API](https://apidocs.slicer.org/v5.10/classqMRMLSegmentEditorWidget.html).
