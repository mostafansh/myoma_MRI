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
    """Embed ``widget``; call ``refresh`` after loading and ``cleanup`` on exit.

    ``logic.volume_nodes`` must map plane names to scalar volume nodes. Contrast
    changes affect display nodes only; neither image pixels nor labels change.
    """

    def __init__(self, logic: Any, parent: Any = None) -> None:
        import qt
        import slicer

        self.logic = logic
        self._slicer = slicer
        self._observations: list[tuple[Any, int]] = []
        self._volume_ids: tuple[Any, ...] = ()
        self._syncing = False

        self.alt_drag = AltDragWindowLevel(logic, VIEW_NAMES, on_changed=self._on_drag_changed)

        self.widget = qt.QGroupBox("Images and contrast", parent)
        layout = qt.QVBoxLayout(self.widget)
        self.reset_button = qt.QPushButton("Reset true planes and fit")
        self.reset_button.toolTip = (
            "Restore sagittal, axial and coronal patient-space planes, with each "
            "view showing its corresponding T2 image."
        )
        self.reset_button.connect("clicked()", self.restore_views)
        layout.addWidget(self.reset_button)
        explanation = qt.QLabel(
            "True planes are orthogonal to the patient axes. Resampled inputs "
            "cannot recreate the original acquisitions."
        )
        explanation.wordWrap = True
        layout.addWidget(explanation)

        form = qt.QFormLayout()
        self.target_combo = qt.QComboBox()
        for plane in VIEW_NAMES:
            self.target_combo.addItem(f"{plane.title()} T2", plane)
        self.target_combo.addItem("All three images", "all")
        self.target_combo.connect("currentIndexChanged(int)", self._sync_controls)
        form.addRow("Contrast for", self.target_combo)
        self.auto_check = qt.QCheckBox("Automatic contrast")
        self.auto_check.connect("toggled(bool)", self._set_auto)
        form.addRow("", self.auto_check)
        self.window_spin = qt.QDoubleSpinBox()
        self.window_spin.decimals = 3
        self.window_spin.minimum = 0.001
        self.window_spin.maximum = 1.0e12
        self.window_spin.singleStep = 10.0
        self.window_spin.keyboardTracking = False
        self.window_spin.toolTip = "Window width: smaller values increase contrast."
        self.level_spin = qt.QDoubleSpinBox()
        self.level_spin.decimals = 3
        self.level_spin.minimum = -1.0e12
        self.level_spin.maximum = 1.0e12
        self.level_spin.singleStep = 10.0
        self.level_spin.keyboardTracking = False
        self.level_spin.toolTip = "Window centre: controls image brightness."
        form.addRow("Window", self.window_spin)
        form.addRow("Level", self.level_spin)
        self.window_spin.connect("valueChanged(double)", self._set_manual)
        self.level_spin.connect("valueChanged(double)", self._set_manual)
        layout.addLayout(form)

        drag_help = qt.QLabel(
            "Alt + left-drag in a view: up/down changes contrast (window), "
            "left/right changes brightness (level)."
        )
        drag_help.wordWrap = True
        layout.addWidget(drag_help)

        self.invert_check = qt.QCheckBox("Invert Alt-drag directions")
        self.invert_check.toolTip = "Reverse both drag axes if the motion feels backwards."
        self.invert_check.checked = False
        self.invert_check.connect("toggled(bool)", self._set_drag_inverted)
        layout.addWidget(self.invert_check)

        self.sensitivity_slider = qt.QSlider()
        self.sensitivity_slider.orientation = qt.Qt.Horizontal
        self.sensitivity_slider.minimum = 1
        self.sensitivity_slider.maximum = 10
        self.sensitivity_slider.singleStep = 1
        self.sensitivity_slider.pageStep = 1
        self.sensitivity_slider.tickInterval = 1
        self.sensitivity_slider.value = 5
        self.sensitivity_slider.toolTip = "How far the image changes per pixel of Alt-drag."
        self.sensitivity_slider.connect("valueChanged(int)", self._set_drag_sensitivity)
        sensitivity_form = qt.QFormLayout()
        sensitivity_form.addRow("Drag sensitivity", self.sensitivity_slider)
        layout.addLayout(sensitivity_form)

        self.status_label = qt.QLabel()
        self.status_label.wordWrap = True
        layout.addWidget(self.status_label)

        self.alt_drag.inverted = False
        self.alt_drag.sensitivity = float(self.sensitivity_slider.value)
        self.refresh()

    def _display_nodes(self, target: str | None = None) -> list[Any]:
        if target is None:
            target = str(self.target_combo.currentData)
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
            self._remove_observers()
            self._volume_ids = volume_ids
            for display in self._display_nodes("all"):
                tag = display.AddObserver("ModifiedEvent", self._sync_controls)
                self._observations.append((display, tag))
        has_volumes = bool(self._display_nodes("all"))
        self.widget.enabled = has_volumes
        self.restore_views(reset_orientation=changed, fit=changed)
        if has_volumes:
            self.alt_drag.bind()
        else:
            self.alt_drag.unbind()
        self._sync_controls()

    def set_contrast(self, target: str, *, automatic: bool,
                     window: float | None = None, level: float | None = None) -> None:
        """Set one plane or ``all``; omitted manual values keep existing values."""
        if target not in (*VIEW_NAMES, "all"):
            raise ValueError(f"Unknown image plane: {target}")
        if window is not None and (not math.isfinite(window) or window <= 0):
            raise ValueError("Window must be a positive finite number")
        if level is not None and not math.isfinite(level):
            raise ValueError("Level must be finite")
        self._syncing = True
        try:
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
        finally:
            self._syncing = False
        self._sync_controls()

    def _set_auto(self, checked: bool) -> None:
        if not self._syncing:
            self.set_contrast(str(self.target_combo.currentData), automatic=bool(checked))

    def _set_manual(self, *_args: Any) -> None:
        if not self._syncing:
            self.set_contrast(
                str(self.target_combo.currentData), automatic=False,
                window=float(self.window_spin.value), level=float(self.level_spin.value),
            )

    def _set_drag_inverted(self, checked: bool) -> None:
        self.alt_drag.inverted = bool(checked)

    def _set_drag_sensitivity(self, value: int) -> None:
        self.alt_drag.sensitivity = float(value)

    def _on_drag_changed(self, plane: Any = None) -> None:
        if self._syncing:
            return
        self._sync_controls()

    def _sync_controls(self, *_args: Any) -> None:
        if self._syncing:
            return
        self._syncing = True
        controls = (self.auto_check, self.window_spin, self.level_spin)
        blocked = [control.blockSignals(True) for control in controls]
        try:
            displays = self._display_nodes()
            self.auto_check.enabled = bool(displays)
            if not displays:
                self.window_spin.enabled = False
                self.level_spin.enabled = False
                self.status_label.text = "Open a case to adjust image display."
                return
            values = [(bool(node.GetAutoWindowLevel()), node.GetWindow(), node.GetLevel()) for node in displays]
            automatic, window, level = values[0]
            all_auto = all(value[0] for value in values)
            self.auto_check.checked = all_auto
            self.window_spin.value = max(0.001, float(window))
            self.level_spin.value = float(level)
            self.window_spin.enabled = not all_auto
            self.level_spin.enabled = not all_auto
            mixed = any(
                mode != automatic or not math.isclose(width, window) or not math.isclose(centre, level)
                for mode, width, centre in values[1:]
            )
            self.status_label.text = (
                "Images have different contrast settings; values shown are from sagittal T2. "
                "Manual changes apply to all three images."
                if mixed else "Changes affect display only."
            )
        finally:
            for control, previous in zip(controls, blocked):
                control.blockSignals(previous)
            self._syncing = False

    def _remove_observers(self) -> None:
        for node, tag in self._observations:
            node.RemoveObserver(tag)
        self._observations = []

    def cleanup(self) -> None:
        self.alt_drag.unbind()
        self._remove_observers()
        self._volume_ids = ()

