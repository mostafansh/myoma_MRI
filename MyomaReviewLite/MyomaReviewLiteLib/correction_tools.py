"""Reviewer controls for segmentation corrections and the cervix boundary marker.

``canonical_cervix_normal`` has no Slicer dependency, so its RAS convention can
be tested with plain vectors.
"""
from __future__ import annotations

import math
from typing import Any


def canonical_cervix_normal(normal_ras: Any) -> tuple[float, float, float]:
    """Orient a plane normal so the inferior side is always cervix.

    The reviewer clicks three points in any order, so the plane's initial
    normal direction (right-hand rule across the three points) is arbitrary.
    This flips it, if needed, so it always points toward the superior side of
    the patient (RAS's third component); the opposite, inferior side is then
    always the cervix, with no reviewer choice involved. A normal with no
    superior/inferior component (its S component is exactly zero) cannot
    distinguish the two sides and is returned unchanged.
    """
    if not isinstance(normal_ras, (list, tuple)) or len(normal_ras) != 3:
        raise ValueError("normal_ras must contain exactly three values")
    try:
        x, y, z = (float(component) for component in normal_ras)
    except (TypeError, ValueError) as exc:
        raise ValueError("normal_ras must contain numeric values") from exc
    if not all(math.isfinite(component) for component in (x, y, z)):
        raise ValueError("normal_ras must contain finite values")
    if math.hypot(x, y, z) < 1e-12:
        raise ValueError("normal_ras must not be zero")
    return (-x, -y, -z) if z < 0 else (x, y, z)


class ReviewCorrectionTools:
    """Small correction panel owned by one MyomaReviewLite widget."""

    def __init__(self, owner: Any) -> None:
        import qt
        self.owner = owner
        self.plane_node = None
        self.plane_observer = None
        self._canonicalizing = False
        self._case_key = None
        self.widget = qt.QWidget()
        layout = qt.QVBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        overlays = qt.QHBoxLayout()
        self.show_check = qt.QCheckBox("Show segmentation overlays")
        self.show_check.checked = True
        self.show_check.connect("toggled(bool)", self.update_overlays)
        overlays.addWidget(self.show_check)
        overlays.addWidget(qt.QLabel("Fill opacity"))
        self.opacity = qt.QSlider(qt.Qt.Horizontal)
        self.opacity.minimum = 0
        self.opacity.maximum = 100
        self.opacity.value = 35
        self.opacity.connect("valueChanged(int)", self.update_overlays)
        overlays.addWidget(self.opacity)
        layout.addLayout(overlays)
        self.show_myomas_button = qt.QPushButton("Show segmented myomas")
        self.show_myomas_button.connect("clicked()", self.show_myomas)
        layout.addWidget(self.show_myomas_button)
        actions = qt.QHBoxLayout()
        self.edit_buttons = []
        for title, effect in (("Paint / add myoma", "Paint"), ("Fill chosen area", "Draw"), ("Erase myoma", "Erase")):
            button = qt.QPushButton(title)
            button.connect("clicked()", lambda _checked=False, name=effect: self.activate_myoma_effect(name))
            actions.addWidget(button)
            self.edit_buttons.append(button)
        layout.addLayout(actions)
        help_label = qt.QLabel("Fill chosen area: outline the myoma with Draw, then press Enter to fill the closed region. Paint another lesion into the same myoma label; there is no six-myoma limit.")
        help_label.wordWrap = True
        layout.addWidget(help_label)

        self.plane_group = qt.QGroupBox("Cervix / endometrium boundary")
        plane_layout = qt.QVBoxLayout(self.plane_group)
        plane_help = qt.QLabel("Click three points along the boundary between the cervix and the uterine corpus (for example, at the internal os in the sagittal view). Nothing is shown until all three are placed. The inferior side of the resulting plane is always the cervix; there is no side to choose. This only records the boundary — it never changes segmentation labels.")
        plane_help.wordWrap = True
        plane_layout.addWidget(plane_help)
        plane_actions = qt.QHBoxLayout()
        self.edit_plane_button = qt.QPushButton("Place / edit boundary")
        self.edit_plane_button.connect("clicked()", self.edit_plane)
        self.save_plane_button = qt.QPushButton("Save boundary")
        self.save_plane_button.connect("clicked()", self.save_plane)
        self.clear_plane_button = qt.QPushButton("Clear boundary")
        self.clear_plane_button.connect("clicked()", self.clear_plane)
        for button in (self.edit_plane_button, self.save_plane_button, self.clear_plane_button):
            plane_actions.addWidget(button)
        plane_layout.addLayout(plane_actions)
        self.plane_status = qt.QLabel("No boundary saved.")
        self.plane_status.wordWrap = True
        plane_layout.addWidget(self.plane_status)
        layout.addWidget(self.plane_group)
        self.refresh()

    @property
    def logic(self) -> Any:
        return self.owner.logic

    def _editable(self) -> bool:
        return bool(self.logic.state is not None and self.logic.phase == "ai_revealed" and not self.logic.read_only)

    def _require_editable(self) -> None:
        if not self._editable() or self.logic.segmentation_node is None:
            raise RuntimeError("Open an unfinished case and reveal AI to edit segmentation")

    def _plane_values(self) -> tuple[list[float], list[float]]:
        if self.plane_node is None or not self.plane_node.GetIsPlaneValid():
            raise RuntimeError("Place the cervix boundary first")
        origin, normal = [0.0] * 3, [0.0] * 3
        self.plane_node.GetOriginWorld(origin)
        self.plane_node.GetNormalWorld(normal)
        return origin, normal

    def _remove_plane(self) -> None:
        import slicer
        if self.plane_node is not None:
            if self.plane_observer is not None:
                self.plane_node.RemoveObserver(self.plane_observer)
            if self.plane_node.GetScene() is slicer.mrmlScene:
                slicer.mrmlScene.RemoveNode(self.plane_node)
        self.plane_node = None
        self.plane_observer = None

    def cleanup(self) -> None:
        self._remove_plane()
        self._case_key = None

    def refresh(self) -> None:
        case_key = (self.logic.case_id, id(self.logic.state), id(self.logic.segmentation_node))
        if case_key != self._case_key:
            self._remove_plane()
            self._case_key = case_key
            self.plane_status.text = "Boundary saved; choose Place / edit boundary to view it." if getattr(self.logic.state, "split_plane", None) else "No boundary saved."
        has_segmentation = self.logic.segmentation_node is not None
        self.show_check.enabled = has_segmentation
        self.opacity.enabled = has_segmentation
        self.show_myomas_button.enabled = has_segmentation
        editable = self._editable() and has_segmentation
        for button in self.edit_buttons:
            button.enabled = editable
        self.plane_group.enabled = editable
        self.save_plane_button.enabled = editable and self.plane_node is not None and self.plane_node.GetIsPlaneValid()
        self.clear_plane_button.enabled = editable and (self.plane_node is not None or getattr(self.logic.state, "split_plane", None) is not None)
        self.update_overlays()

    def update_overlays(self, *_args: Any) -> None:
        node = self.logic.segmentation_node
        if node is None:
            return
        node.CreateDefaultDisplayNodes()
        display = node.GetDisplayNode()
        display.SetVisibility(bool(self.show_check.checked))
        display.SetOpacity2DFill(float(self.opacity.value) / 100.0)
        display.SetOpacity2DOutline(1.0)

    def activate_myoma_effect(self, effect_name: str) -> None:
        try:
            self._require_editable()
            editor = self.owner.segment_editor_widget
            if editor is None or editor.effectByName(effect_name) is None:
                raise RuntimeError(f"The {effect_name} effect is unavailable in this Slicer installation")
            self.show_check.checked = True
            self.logic.segmentation_node.GetDisplayNode().SetSegmentVisibility(self.logic.segment_ids[3], True)
            editor.setCurrentSegmentID(self.logic.segment_ids[3])
            editor.setActiveEffectByName(effect_name)
            instruction = "Outline the area and press Enter to fill it." if effect_name == "Draw" else "Edit the selected myoma label in a slice view."
            self.owner._set_status(f"{effect_name}: {instruction} Ctrl+Z undoes segmentation changes.")
        except Exception as exc:
            self.owner._show_error(str(exc))

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
            self.show_check.checked = True
            self.update_overlays()
            node.GetDisplayNode().SetSegmentVisibility(self.logic.segment_ids[3], True)
            if not len(positions):
                self.owner._set_status("The myoma label is empty. Use Paint / add myoma or Fill chosen area to segment a lesion.")
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

    def _start_point_placement(self) -> None:
        """Arm placement mode so the reader's next three clicks define the plane.

        Nothing is shown before that: the node starts with zero control points,
        so it renders nothing and ``GetIsPlaneValid()`` is false until all three
        land. Persistence is off, so Slicer returns to normal navigation right
        after the third point instead of starting a second plane.
        """
        import slicer

        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self.plane_node.GetID())
        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        interaction_node.SetPlaceModePersistence(0)
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)

    def edit_plane(self) -> None:
        try:
            import slicer
            import vtk
            self._require_editable()
            if self.plane_node is None:
                self.plane_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsPlaneNode", "Cervix / endometrium boundary")
                self.plane_node.CreateDefaultDisplayNodes()
                self.plane_node.GetDisplayNode().SetHandlesInteractive(True)
                self.plane_node.GetDisplayNode().SetFillOpacity(0.12)
                self.plane_observer = self.plane_node.AddObserver(vtk.vtkCommand.ModifiedEvent, self._on_plane_modified)
                saved = getattr(self.logic.state, "split_plane", None)
                if saved is not None:
                    # A boundary already exists for this case: show it directly
                    # for dragging, rather than asking for three fresh clicks.
                    reference = self.logic.volume_nodes["sagittal"]
                    bounds = [0.0] * 6
                    reference.GetRASBounds(bounds)
                    self.plane_node.SetPlaneType(slicer.vtkMRMLMarkupsPlaneNode.PlaneTypePointNormal)
                    self.plane_node.SetOriginWorld(*saved.origin_ras)
                    self.plane_node.SetNormalWorld(*saved.normal_ras)
                    self.plane_node.SetSizeMode(slicer.vtkMRMLMarkupsPlaneNode.SizeModeAbsolute)
                    self.plane_node.SetSizeWorld(max(bounds[1] - bounds[0], 30.0), max(bounds[3] - bounds[2], 30.0))
                else:
                    self.plane_node.SetPlaneType(slicer.vtkMRMLMarkupsPlaneNode.PlaneType3Points)
            if not self.plane_node.GetIsPlaneValid():
                # Either a brand-new node, or a previous placement was
                # interrupted (Esc, switching modules, ...) before all three
                # points landed. Re-arm placement either way so clicking this
                # button again always resumes taking clicks, rather than
                # showing an unresponsive "click three points" message.
                self._start_point_placement()
            self.plane_node.SetLocked(False)
            self.plane_node.SetDisplayVisibility(True)
            self.refresh()
            if self.plane_node.GetIsPlaneValid():
                self.owner._set_status("Drag the plane handles to adjust the boundary, then Save boundary.")
            else:
                self.owner._set_status("Click three points along the cervix/corpus boundary. Nothing is shown until all three are placed.")
        except Exception as exc:
            self.owner._show_error(str(exc))

    def _on_plane_modified(self, *_args: Any) -> None:
        if self._canonicalizing or self.plane_node is None:
            return
        node = self.plane_node
        if node.GetIsPlaneValid():
            normal = [0.0, 0.0, 0.0]
            node.GetNormalWorld(normal)
            try:
                canonical = canonical_cervix_normal(normal)
            except ValueError:
                canonical = None
            if canonical is not None and any(abs(a - b) > 1e-9 for a, b in zip(normal, canonical)):
                self._canonicalizing = True
                try:
                    node.SetNormalWorld(*canonical)
                finally:
                    self._canonicalizing = False
        self.refresh()
        if node.GetIsPlaneValid():
            self.owner._set_status("Boundary placed. Save boundary to keep it, or drag the handles to adjust it first.")

    def save_plane(self) -> None:
        try:
            self._require_editable()
            origin, normal = self._plane_values()
            self.logic.state.set_split_plane(origin, canonical_cervix_normal(normal))
            self.owner._write_current_draft()
            self.plane_status.text = "Boundary saved. The inferior side is always cervix."
        except Exception as exc:
            self.owner._show_error(str(exc))

    def clear_plane(self) -> None:
        try:
            self._require_editable()
            self.logic.state.clear_split_plane()
            self._remove_plane()
            self.owner._write_current_draft()
            self.plane_status.text = "Boundary cleared."
            self.refresh()
        except Exception as exc:
            self.owner._show_error(str(exc))
