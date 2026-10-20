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

1. **FIGO, Ddx and comment (the FIGO card).** There is one annotate stage:
   draw every box and type its FIGO in any order. After **Save ROI** a small
   **FIGO card** opens beside the new box in the slice view. Click a box, or
   pick a row in the myoma list, to reopen its card. Click a FIGO type to save
   it: types 0–7 save with one click; **8** then asks for its location;
   **Hybrid** asks for the endometrial and serosal types; **Uncertain** asks
   for a reason. The card also has **Ddx** and **Comment** fields for that
   myoma; they are saved when the card closes. Press `Esc` or click empty
   image to close it. The card follows its box, and hides while the slice is
   outside the box. Ddx, comment and FIGO stay available after geometry edits
   and AI reveal. FIGO can be revised after reveal while the case is still
   unfinished, using the same card. A myoma with no FIGO yet reads e.g.
   **M01 · FIGO ?** in amber on the image and in the list; a typed one reads
   **M01 · FIGO 3**. **Reveal AI** stays disabled until every myoma has a
   FIGO; its tooltip names what is missing, e.g. "FIGO missing: M02, M04".
2. **Case comment.** The folded **Case comment** bar above the pages opens a
   text box for a note on the whole case; click the bar again to fold it away.
   It reads "(written)" while folded if there is text. The comment is saved
   with the draft and the final result, including early stops.
3. **Undo.** `Ctrl+Z` undoes a Segment Editor change on the correction page,
   or the latest myoma-box/FIGO/Ddx annotation change on the annotation page.
   `Ctrl+Y` redoes it; there are no Undo/Redo buttons, only the shortcuts.
   While the module is open it takes `Ctrl+Z` / `Ctrl+Y` from Slicer's own
   Edit menu (otherwise neither would fire) and gives them back on leaving.
   The Segment Editor also has its native undo history.
   Every default key above can be changed: the **Keyboard shortcuts…** button
   in the module header opens a panel listing all rebindable actions (Undo,
   Redo, Add myoma box, Delete box, Save draft, Show segmented myomas). Click a field, press the new key combination, and **Save** —
   bindings are remembered per Slicer user and the toolbar buttons update to
   show whatever is currently bound.
4. **True axial and coronal views.** **Reset true planes and fit** restores
   patient-space sagittal, axial, and coronal orientations and puts the
   corresponding T2 image in each view. If the source NIfTI images were
   already resampled, the module cannot reconstruct their original acquisition
   planes.
5. **Contrast.** In **Images and contrast**, choose one plane or **All three
   images**. Use **Automatic contrast** or enter Window and Level. This changes
   display settings only; the image voxels are unchanged.
6. **Edit the myoma mask.** On the correction page select the myoma segment
   in the Segment Editor and use its Paint, Draw or Erase effects (Draw: outline
   a closed region, then press Enter to fill it). `Ctrl+Z` is available after
   each operation.
7. **Add a new myoma.** Choose **Add myoma box**, drag a rectangle in any
   T2 view, adjust the 3-D handles if needed, and press **Save ROI**; the FIGO
   card opens beside the box. You can also paint the myoma segment in the
   Segment Editor after AI reveal for a mask-only correction.
8. **Edit an existing myoma.** While annotating, or after AI reveal, click the
   box in any T2 view to reveal its handles and open its FIGO card, then drag
   the handles to move or resize it; the new box is saved to the draft as soon
   as you click elsewhere, change page, save, or reveal AI, and `Ctrl+Z`
   undoes it. You can also use Paint, Draw, and Erase on the myoma segment to
   edit its mask. Save the draft before changing cases.
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
    circles are the drag control itself. Each box carries its label on the
    image (**M01 · FIGO 3**, or amber **M01 · FIGO ?** while FIGO is missing).
    In a completed read-only case, clicking a box or picking a row opens the
    FIGO card read-only.
11. **See segmented myoma.** Press `V` (rebindable) once AI is revealed. The myoma overlay is shown and all three views jump to an occupied
    myoma voxel. Scroll through the views to inspect separate lesions.

Reveal captures the original blinded boxes, FIGO values, differential
and diagnoses. Later corrections are saved separately, so the pre-AI record
remains auditable. A completed case is read-only (its saved corrected labels can
be inspected with `V`) until it is reopened with **Reopen case for editing**.

For the underlying Slicer behavior, see the official [Segment Editor
documentation](https://slicer.readthedocs.io/en/5.8/user_guide/modules/segmenteditor.html)
and the [qMRMLSegmentEditorWidget API](https://apidocs.slicer.org/v5.10/classqMRMLSegmentEditorWidget.html).
