"""Patient-space slice views and display-only contrast controls.

Imports of Slicer, Qt, and VTK are deferred so the package remains usable by
the ordinary Python queue and storage tools.
"""

from __future__ import annotations

import math
from typing import Any

from .contrast_tools import AltDragWindowLevel


VIEW_NAMES = {
    "sagittal": "MyomaLiteSagittal",
    "axial": "MyomaLiteAxial",
    "coronal": "MyomaLiteCoronal",
}


class ReviewViewTools:
    """Call ``refresh`` after loading a case and ``cleanup`` on exit.

    ``logic.volume_nodes`` must map plane names to scalar volume nodes. Contrast
    changes affect display nodes only; neither image pixels nor labels change.
    There is no on-screen contrast panel: planes are restored automatically on
    load, and Alt + left-drag in a slice view is the only way to adjust
    window/level (see :class:`AltDragWindowLevel`). ``set_contrast`` remains
    available for programmatic/test use.
    """

    def __init__(self, logic: Any, parent: Any = None) -> None:
        import slicer

        self.logic = logic
        self._slicer = slicer
        self._volume_ids: tuple[Any, ...] = ()

        self.alt_drag = AltDragWindowLevel(logic, VIEW_NAMES)
        self.alt_drag.inverted = False
        self.alt_drag.sensitivity = 5.0

        self.refresh()

    def _display_nodes(self, target: str = "all") -> list[Any]:
        planes = tuple(VIEW_NAMES) if target == "all" else (target,)
        result = []
        for plane in planes:
            volume = self.logic.volume_nodes.get(plane)
            if volume is not None:
                display = volume.GetDisplayNode()
                if display is not None and display not in result:
                    result.append(display)
        return result

    def restore_views(self, reset_orientation: bool = True, fit: bool = True) -> None:
        """Restore per-plane sources; optionally reset orientation and zoom.

        Use ``restore_views(False, False)`` after changing a Segment Editor
        source volume to restore backgrounds without moving the reader's view.
        """
        import vtk

        manager = self._slicer.app.layoutManager()
        if manager is None:
            return
        for plane, name in VIEW_NAMES.items():
            view = manager.sliceWidget(name)
            volume = self.logic.volume_nodes.get(plane)
            if view is None or volume is None:
                continue
            slice_logic = view.sliceLogic()
            composite = slice_logic.GetSliceCompositeNode()
            # Linking background selection would replace the other T2 sources.
            composite.SetLinkedControl(False)
            composite.SetHotLinkedControl(False)
            composite.SetBackgroundVolumeID(volume.GetID())
            composite.SetForegroundVolumeID(None)
            if reset_orientation:
                node = slice_logic.GetSliceNode()
                orientation = plane.title()
                matrix = vtk.vtkMatrix3x3()
                getattr(self._slicer.vtkMRMLSliceNode, f"Get{orientation}SliceToRASMatrix")(matrix)
                node.AddSliceOrientationPreset(orientation, matrix)
                node.SetOrientation(orientation)
            if fit:
                slice_logic.FitSliceToAll()

    def refresh(self) -> None:
        """Bind new display nodes and initialise a newly loaded case's views."""
        volume_ids = tuple(
            (volume.GetID(), volume.GetDisplayNode().GetID() if volume.GetDisplayNode() else None)
            if volume is not None else None
            for volume in (self.logic.volume_nodes.get(plane) for plane in VIEW_NAMES)
        )
        changed = volume_ids != self._volume_ids
        if changed:
            self._volume_ids = volume_ids
        has_volumes = bool(self._display_nodes("all"))
        self.restore_views(reset_orientation=changed, fit=changed)
        if has_volumes:
            self.alt_drag.bind()
        else:
            self.alt_drag.unbind()

    def set_contrast(self, target: str, *, automatic: bool,
                     window: float | None = None, level: float | None = None) -> None:
        """Set one plane or ``all``; omitted manual values keep existing values."""
        if target not in (*VIEW_NAMES, "all"):
            raise ValueError(f"Unknown image plane: {target}")
        if window is not None and (not math.isfinite(window) or window <= 0):
            raise ValueError("Window must be a positive finite number")
        if level is not None and not math.isfinite(level):
            raise ValueError("Level must be finite")
        for display in self._display_nodes(target):
            previous = display.StartModify()
            try:
                display.SetAutoWindowLevel(bool(automatic))
                if not automatic:
                    display.SetWindowLevel(
                        display.GetWindow() if window is None else float(window),
                        display.GetLevel() if level is None else float(level),
                    )
            finally:
                display.EndModify(previous)

    def cleanup(self) -> None:
        self.alt_drag.unbind()
        self._volume_ids = ()
