# MyomaReviewLite pilot guide

The bundled module is for 3D Slicer 5.10. Open the supplied reader launcher,
load a case, and use the three patient-space T2 views. The panel has two
tabs: **Cases** (progress, All/Unfinished/Finished/Attention filters, a
search box, and the case list — click a case to open it) and **Annotation**
(the viewer and every review tool below). Opening a case always switches to
Annotation. The package keeps the AI input labels at values 0–4. The
correction editor adds two empty fixed labels, so the exported correction
labels are:

| Value | Label | Use |
|---:|---|---|
| 0 | background | Empty voxels |
| 1 | uterine wall | Uterine wall |
| 2 | uterine cavity | Endometrium/cavity |
| 3 | myoma | All segmented myoma voxels |
| 4 | Nabothian cyst | Nabothian cyst |
| 5 | cervix | Cervical tissue |
| 6 | cesarean scar niche | C-section scar niche |

The following controls address the pilot list:

1. **Myoma Ddx.** Select a myoma, enter the reader's differential diagnosis
   in **Myoma Ddx**, and choose **Save FIGO and Ddx**. It is saved per lesion
   and remains available after geometry edits and AI reveal. FIGO typing
   opens on the first myoma (M01), not the last one drawn. Choosing a myoma in
   **Selected lesion** shows its box and centers the three T2 views on it.
   Saving jumps to the next myoma in the list automatically, so the reader can
   page straight through all of them; saving the last myoma instead asks
   whether to reveal AI now.
2. **Undo.** `Ctrl+Z` undoes a Segment Editor change on the correction page,
   or the latest myoma-box/FIGO/Ddx annotation change on the annotation page.
   `Ctrl+Y` redoes it. The Segment Editor also has its native undo history.
   Every default key above can be changed: the **Keyboard shortcuts…** button
   in the module header opens a panel listing all rebindable actions (Undo,
   Redo, Add myoma box, Redraw box, Delete box, Continue to FIGO typing, Save
   draft). Click a field, press the new key combination, and **Save** —
   bindings are remembered per Slicer user and the toolbar buttons update to
   show whatever is currently bound.
3. **Cervix label.** On the correction page, select the fixed **cervix**
   segment in the Segment Editor table and paint or erase it.
4. **Cesarean scar niche label.** Use the fixed **cesarean scar niche**
   segment in the same table. It is kept separate from cervix and myoma.
5. **True axial and coronal views.** **Reset true planes and fit** restores
   patient-space sagittal, axial, and coronal orientations and puts the
   corresponding T2 image in each view. If the source NIfTI images were
   already resampled, the module cannot reconstruct their original acquisition
   planes.
6. **Contrast.** In **Images and contrast**, choose one plane or **All three
   images**. Use **Automatic contrast** or enter Window and Level. This changes
   display settings only; the image voxels are unchanged.
7. **Fill a chosen area.** On the correction page select the myoma segment,
   press **Fill chosen area**, outline a closed region in a slice, and press
   Enter to fill it. **Paint / add myoma** adds to the same myoma label;
   **Erase myoma** removes it. `Ctrl+Z` is available after each operation.
8. **Cervix/endometrium boundary.** Under **Cervix / endometrium boundary**,
   choose **Place / edit boundary**, then click three points along the
   cervix/corpus boundary (for example, at the internal os in the sagittal
   view); nothing is shown until all three land. Drag the handles to adjust
   it, then press **Save boundary**. The inferior side of the plane is always
   the cervix — there is no side to choose. This only records the boundary
   in RAS millimetres; it never changes segmentation labels. To actually
   convert voxels to the cervix label, use the fixed **cervix** segment in
   the Segment Editor table as in item 3.
9. **Correct FIGO.** On the annotation page select the lesion in the list,
   choose `standard`, `hybrid`, or `uncertain`, fill the visible fields, and
   save. Type 8 also requires its location. FIGO can be revised after AI
   reveal while the case is still unfinished.
10. **Add a new myoma.** Choose **Add myoma box**, drag a rectangle in any
    T2 view, adjust the 3-D handles if needed, and press **Save ROI**. You can
    also use **Paint / add myoma** after AI reveal for a mask-only correction.
11. **Edit an existing myoma.** On the annotation page, click the box in any
    T2 view to reveal its handles, then drag them to move or resize it; the new
    box is saved to the draft as soon as you click elsewhere, change page, save,
    or reveal AI, and `Ctrl+Z` undoes it. You can also
    select its row and choose **Redraw** to draw the box again from scratch, or
    use Paint, Draw, and Erase on the myoma segment to edit its mask. Save the
    draft before changing cases.
12. **More than six myomas.** There is no six-lesion limit. Each annotation
    receives the next ID (`M01`, `M02`, …), and all lesions share the myoma
    segmentation label. Deleted IDs are not reused.
13. **Myoma box appearance.** Every box is a one-pixel outline with no handle
    circles, so the T2 underneath stays readable. The weight does not change as
    you work: the rectangle you drag out, the box you adjust before **Save
    ROI**, and every saved box are all drawn the same way. The box currently in
    hand is simply **bolder** — two pixels instead of one. The circles belong to
    one box at a time and appear only when you click that box on the annotation
    page; clicking away from every box hides them again. Where a box must not
    move — a completed read-only case, or the segmentation page — the click
    still highlights the box but no circles appear, because in Slicer the
    circles are the drag control itself.
14. **See segmented myoma.** Press **Show segmented myomas** on the correction
    page. The myoma overlay is shown and all three views jump to an occupied
    myoma voxel. Scroll through the views to inspect separate lesions.

Reveal captures the original blinded boxes, FIGO values, differential
diagnoses, and split-plane state. Later corrections are saved separately, so
the pre-AI record remains auditable. A completed case is read-only, but its
saved corrected labels can be reopened and inspected with **Show segmented
myomas**.

For the underlying Slicer behavior, see the official [Segment Editor
documentation](https://slicer.readthedocs.io/en/5.8/user_guide/modules/segmenteditor.html)
and the [qMRMLSegmentEditorWidget API](https://apidocs.slicer.org/v5.10/classqMRMLSegmentEditorWidget.html).
