"""The correction page's own controls in front of Slicer's Segment Editor.

The reader picks a segment from four rows and a tool from a 3 x 2 grid; Paint
and Erase share a brush panel. Slicer's ``qMRMLSegmentEditorWidget`` still does
the work (effects, undo, the effects' option panels) but its own buttons,
segment table and masking section are hidden. The panel follows the editor
node, so a tool switched elsewhere (Alt-drag contrast, undo) shows here too.
Qt is imported lazily so the pure helpers stay importable outside Slicer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import theme

# (Segment Editor effect name, button label, icon file).
TOOLS = (
    ("Paint", "Paint", "tool-paint.svg"),
    ("Erase", "Erase", "tool-erase.svg"),
    ("Draw", "Draw", "tool-draw.svg"),
    ("Scissors", "Scissors", "tool-scissors.svg"),
    ("Islands", "Islands", "tool-islands.svg"),
    ("Smoothing", "Smooth", "tool-smooth.svg"),
)
# These use the panel's brush controls; the others show the effect's own options.
BRUSH_TOOLS = frozenset({"Paint", "Erase"})
# The editor's parts the panel replaces or the reader never needs.
HIDDEN_EDITOR_PARTS = (
    "EffectsGroupBox",
    "UndoRedoGroupBox",
    "SegmentsTableResizableFrame",
    "Show3DButton",
    "SwitchToSegmentationsButton",
    "SpecifyGeometryButton",
)
# The editor shows these again whenever an effect starts, so they are
# squeezed to no height instead of hidden.
COLLAPSED_EDITOR_PARTS = ("MaskingGroupBox", "EffectHelpBrowser")
BRUSH_MIN_MM = 1
BRUSH_MAX_MM = 50
BRUSH_DEFAULT_MM = 12
QWIDGETSIZE_MAX = 16777215


def segment_title(name: str) -> str:
    """``uterine wall`` -> ``Uterine wall``; keeps ``Nabothian cyst`` as is."""

    return name[:1].upper() + name[1:]


def tinted_icon(path: Path, normal: str, checked: str) -> Any:
    """An SVG drawn in ``normal`` colour, and in ``checked`` colour when on."""

    import qt

    source = path.read_text(encoding="utf-8")
    icon = qt.QIcon()
    for colour, state in ((normal, qt.QIcon.Off), (checked, qt.QIcon.On)):
        pixmap = qt.QPixmap()
        pixmap.loadFromData(qt.QByteArray(source.replace(theme.TEXT, colour).encode("utf-8")), "SVG")
        icon.addPixmap(pixmap, qt.QIcon.Normal, state)
    disabled = qt.QPixmap()
    disabled.loadFromData(qt.QByteArray(source.replace(theme.TEXT, theme.DISABLED_TEXT).encode("utf-8")), "SVG")
    icon.addPixmap(disabled, qt.QIcon.Disabled, qt.QIcon.Off)
    return icon


class CorrectionPanel:
    """Segment rows, tool grid and brush panel driving one Segment Editor."""

    def __init__(self, icon_dir: Path, segments: dict[str, str]) -> None:
        """``segments`` maps segment ID to its name, in display order."""

        import qt

        self.editor: Any = None
        self.editor_node: Any = None
        self.segmentation_node: Any = None
        self._observer: int | None = None
        self._read_only = False
        self._syncing = False

        self.widget = qt.QWidget()
        layout = qt.QVBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = qt.QHBoxLayout()
        header.addWidget(theme.set_role(qt.QLabel("SEGMENTS"), "section"))
        header.addStretch(1)
        self.key_hint = theme.set_role(qt.QLabel(""), "hint-muted")
        header.addWidget(self.key_hint)
        layout.addLayout(header)

        self.segment_buttons: dict[str, Any] = {}
        self._segment_swatches: dict[str, Any] = {}
        self._segment_names: dict[str, Any] = {}
        self._segment_editing: dict[str, Any] = {}
        for segment_id, name in segments.items():
            button = theme.set_role(qt.QPushButton(), "segment")
            button.checkable = True
            button.setAccessibleName(segment_title(name))
            button.objectName = f"segment_{segment_id}"
            row = qt.QHBoxLayout(button)
            row.setContentsMargins(12, 8, 12, 8)
            row.setSpacing(10)
            swatch = qt.QLabel()
            swatch.setFixedSize(12, 12)
            title = qt.QLabel(segment_title(name))
            editing = theme.set_role(qt.QLabel("editing"), "hint-muted")
            editing.visible = False
            for label in (swatch, title, editing):
                label.setAttribute(qt.Qt.WA_TransparentForMouseEvents, True)
            row.addWidget(swatch)
            row.addWidget(title, 1)
            row.addWidget(editing)
            button.setMinimumHeight(36)
            button.connect("clicked()", lambda segment_id=segment_id: self.select_segment(segment_id))
            layout.addWidget(button)
            self.segment_buttons[segment_id] = button
            self._segment_swatches[segment_id] = swatch
            self._segment_names[segment_id] = title
            self._segment_editing[segment_id] = editing

        layout.addWidget(theme.set_role(qt.QLabel("TOOLS"), "section"))
        grid = qt.QGridLayout()
        grid.setSpacing(6)
        self.tool_buttons: dict[str, Any] = {}
        for index, (effect_name, label, icon_name) in enumerate(TOOLS):
            button = theme.set_role(qt.QToolButton(), "tool")
            button.text = label
            button.checkable = True
            button.setIcon(tinted_icon(icon_dir / icon_name, theme.TEXT, theme.ON_ACCENT))
            button.setIconSize(qt.QSize(18, 18))
            button.setToolButtonStyle(qt.Qt.ToolButtonTextUnderIcon)
            button.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
            button.setAccessibleName(label)
            button.connect("clicked()", lambda effect_name=effect_name: self.toggle_tool(effect_name))
            grid.addWidget(button, index // 3, index % 3)
            self.tool_buttons[effect_name] = button
        layout.addLayout(grid)

        self.brush_panel = theme.set_role(qt.QFrame(), "card")
        brush_layout = qt.QVBoxLayout(self.brush_panel)
        brush_layout.setContentsMargins(12, 10, 12, 10)
        brush_layout.setSpacing(8)
        size_row = qt.QHBoxLayout()
        size_row.addWidget(qt.QLabel("Brush size"))
        size_row.addStretch(1)
        self.brush_size_label = theme.set_role(qt.QLabel(""), "muted")
        size_row.addWidget(self.brush_size_label)
        brush_layout.addLayout(size_row)
        self.brush_slider = qt.QSlider(qt.Qt.Horizontal)
        self.brush_slider.setRange(BRUSH_MIN_MM, BRUSH_MAX_MM)
        self.brush_slider.value = BRUSH_DEFAULT_MM
        self.brush_slider.setAccessibleName("Brush size")
        self.brush_slider.connect("valueChanged(int)", self._on_brush_size)
        brush_layout.addWidget(self.brush_slider)
        self.sphere_check = qt.QCheckBox("Sphere brush (paint in 3-D)")
        self.sphere_check.connect("toggled(bool)", self._on_sphere)
        brush_layout.addWidget(self.sphere_check)
        self.brush_panel.visible = False
        layout.addWidget(self.brush_panel)
        self._show_brush_size(BRUSH_DEFAULT_MM)

        # The editor itself sits here; only an effect's own options show.
        self.editor_host = qt.QWidget()
        self.editor_host_layout = qt.QVBoxLayout(self.editor_host)
        self.editor_host_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor_host)
        self.set_read_only(False)

    # --- binding -----------------------------------------------------------

    def bind(self, editor: Any, editor_node: Any, segmentation_node: Any) -> None:
        import qt
        import vtk

        self.unbind()
        self.editor = editor
        self.editor_node = editor_node
        self.segmentation_node = segmentation_node
        self.editor_host_layout.addWidget(editor)
        for name in HIDDEN_EDITOR_PARTS:
            part = editor.findChild(qt.QWidget, name)
            if part is not None:
                part.hide()
        for name in COLLAPSED_EDITOR_PARTS:
            part = editor.findChild(qt.QWidget, name)
            if part is not None:
                part.setMaximumHeight(0)
        if editor_node is not None:
            self._observer = editor_node.AddObserver(vtk.vtkCommand.ModifiedEvent, self._on_editor_node_modified)
        self.refresh_segment_colours()
        self.sync()

    def unbind(self) -> None:
        if self.editor_node is not None and self._observer is not None:
            try:
                self.editor_node.RemoveObserver(self._observer)
            except Exception:
                pass
        if self.editor is not None:
            try:
                self.editor_host_layout.removeWidget(self.editor)
            except Exception:
                pass
        self._observer = None
        self.editor = None
        self.editor_node = None
        self.segmentation_node = None
        self.sync()

    def refresh_segment_colours(self) -> None:
        import qt

        segmentation = self.segmentation_node.GetSegmentation() if self.segmentation_node is not None else None
        for segment_id, swatch in self._segment_swatches.items():
            segment = segmentation.GetSegment(segment_id) if segmentation is not None else None
            colour = qt.QColor(theme.MUTED)
            if segment is not None:
                red, green, blue = segment.GetColor()
                colour = qt.QColor.fromRgbF(red, green, blue)
            swatch.setStyleSheet(f"background: {colour.name()}; border-radius: 3px;")

    # --- reader actions ----------------------------------------------------

    def select_segment(self, segment_id: str) -> None:
        if self.editor is not None and not self._read_only:
            self.editor.setCurrentSegmentID(segment_id)
        self.sync()

    def toggle_tool(self, effect_name: str) -> None:
        if self.editor is None or self._read_only:
            self.sync()
            return
        active = self.active_effect_name()
        self.editor.setActiveEffectByName("" if active == effect_name else effect_name)
        if effect_name in BRUSH_TOOLS and active != effect_name:
            self._push_brush()
        self.sync()

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = read_only
        for button in (*self.segment_buttons.values(), *self.tool_buttons.values()):
            button.enabled = not read_only
        self.brush_panel.enabled = not read_only
        self.sync()

    def set_key_hint(self, text: str) -> None:
        self.key_hint.text = text

    # --- state from the editor ---------------------------------------------

    def active_effect_name(self) -> str:
        if self.editor is None:
            return ""
        effect = self.editor.activeEffect()
        return str(effect.name) if effect is not None else ""

    def _on_editor_node_modified(self, *_args: Any) -> None:
        self.sync()

    def sync(self) -> None:
        """Show the editor's current segment, tool and brush on the panel."""

        if self._syncing:
            return
        self._syncing = True
        try:
            import qt

            active = self.active_effect_name()
            selected = str(self.editor.currentSegmentID() or "") if self.editor is not None else ""
            for segment_id, button in self.segment_buttons.items():
                is_selected = segment_id == selected
                button.checked = is_selected
                self._segment_editing[segment_id].visible = is_selected and bool(active)
                font = qt.QFont(self._segment_names[segment_id].font)
                font.setWeight(qt.QFont.DemiBold if is_selected else qt.QFont.Normal)
                self._segment_names[segment_id].setFont(font)
            for effect_name, button in self.tool_buttons.items():
                button.checked = effect_name == active
            brush = active in BRUSH_TOOLS
            self.brush_panel.visible = brush
            if self.editor is not None:
                options = self.editor.findChild(qt.QWidget, "OptionsGroupBox")
                if options is not None:
                    options.setMaximumHeight(0 if brush or not active else QWIDGETSIZE_MAX)
            if brush:
                self._pull_brush()
        finally:
            self._syncing = False

    # --- brush -------------------------------------------------------------

    def _brush_effect(self) -> Any:
        if self.editor is None or self.active_effect_name() not in BRUSH_TOOLS:
            return None
        return self.editor.activeEffect()

    def _push_brush(self) -> None:
        """Give the brush the panel's size (in mm) and shape."""

        effect = self._brush_effect()
        if effect is None:
            return
        # Read both first: each write below syncs the panel from the editor.
        diameter = float(self.brush_slider.value)
        sphere = 1 if self.sphere_check.checked else 0
        effect.setCommonParameter("BrushAbsoluteDiameter", diameter)
        effect.setCommonParameter("BrushSphere", sphere)
        effect.setCommonParameter("BrushDiameterIsRelative", 0)

    def _pull_brush(self) -> None:
        """Follow a brush resized in the views (Shift + wheel)."""

        effect = self._brush_effect()
        if effect is None:
            return
        try:
            relative = effect.integerParameter("BrushDiameterIsRelative")
            diameter = effect.doubleParameter("BrushAbsoluteDiameter")
            sphere = bool(effect.integerParameter("BrushSphere"))
        except Exception:
            return
        if relative:
            return
        self.brush_slider.blockSignals(True)
        self.sphere_check.blockSignals(True)
        try:
            self.brush_slider.value = int(round(min(max(diameter, BRUSH_MIN_MM), BRUSH_MAX_MM)))
            self.sphere_check.checked = sphere
        finally:
            self.brush_slider.blockSignals(False)
            self.sphere_check.blockSignals(False)
        self._show_brush_size(diameter)

    def _show_brush_size(self, diameter: float) -> None:
        self.brush_size_label.text = f"{diameter:.0f} mm" if diameter >= 1 else f"{diameter:.1f} mm"

    def _on_brush_size(self, value: int) -> None:
        self._show_brush_size(value)
        if not self._syncing:
            self._push_brush()

    def _on_sphere(self, _checked: bool) -> None:
        if not self._syncing:
            self._push_brush()
