"""Reviewer controls for the segmentation overlays on the correction page."""
from __future__ import annotations

from typing import Any


class ReviewCorrectionTools:
    """Segmentation overlay handling owned by one MyomaReviewLite widget.

    The mask is drawn at a fixed fill opacity; there are no panel controls
    for it (V centres the views on the myomas, A shows or hides the mask).
    """

    FILL_OPACITY = 0.35

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.mask_visible = True
        self.refresh()

    @property
    def logic(self) -> Any:
        return self.owner.logic

    def cleanup(self) -> None:
        """Each case opens with its mask shown."""
        self.mask_visible = True

    def toggle_mask(self) -> bool:
        """Show or hide the whole AI mask; returns whether it is now shown."""
        self.mask_visible = not self.mask_visible
        self.update_overlays()
        return self.mask_visible

    def refresh(self) -> None:
        self.update_overlays()

    def update_overlays(self, *_args: Any) -> None:
        node = self.logic.segmentation_node
        if node is None:
            return
        node.CreateDefaultDisplayNodes()
        display = node.GetDisplayNode()
        display.SetVisibility(self.mask_visible)
        display.SetOpacity2DFill(self.FILL_OPACITY)
        display.SetOpacity2DOutline(1.0)

    def _reference_matrix(self) -> Any:
        import numpy as np
        import vtk
        reference = self.logic.volume_nodes["sagittal"]
        if reference.GetParentTransformNode() is not None or self.logic.segmentation_node.GetParentTransformNode() is not None:
            raise RuntimeError("This operation requires the case's original registered geometry without extra transforms")
        matrix = vtk.vtkMatrix4x4()
        reference.GetIJKToRASMatrix(matrix)
        return np.array([[matrix.GetElement(row, column) for column in range(4)] for row in range(4)])

    def show_myomas(self) -> None:
        try:
            import numpy as np
            import slicer
            node = self.logic.segmentation_node
            if node is None:
                raise RuntimeError("No segmentation is loaded")
            reference = self.logic.volume_nodes["sagittal"]
            mask = slicer.util.arrayFromSegmentBinaryLabelmap(node, self.logic.segment_ids[3], reference)
            positions = np.argwhere(mask > 0)
            # Jumping to the myomas is pointless with the mask hidden.
            self.mask_visible = True
            self.update_overlays()
            node.GetDisplayNode().SetSegmentVisibility(self.logic.segment_ids[3], True)
            if not len(positions):
                self.owner._set_status("The myoma label is empty. Use the Segment Editor to segment a lesion.")
                return
            # Choose an occupied voxel near the bounds center, so separated
            # lesions cannot leave all three views centered on empty space.
            bounds_center = (positions.min(axis=0) + positions.max(axis=0)) / 2
            point = positions[np.argmin(np.sum((positions - bounds_center) ** 2, axis=1))]
            ras = self._reference_matrix() @ np.array([point[2], point[1], point[0], 1.0])
            manager = slicer.app.layoutManager()
            for view_name in ("MyomaLiteSagittal", "MyomaLiteAxial", "MyomaLiteCoronal"):
                view = manager.sliceWidget(view_name)
                if view is not None:
                    view.mrmlSliceNode().JumpSliceByCentering(*ras[:3])
            if self.owner.segment_editor_widget is not None:
                self.owner.segment_editor_widget.setCurrentSegmentID(self.logic.segment_ids[3])
            self.owner._set_status("Myoma overlays shown. Views are centered on a segmented voxel; scroll to inspect the other lesions.")
        except Exception as exc:
            self.owner._show_error(str(exc))
