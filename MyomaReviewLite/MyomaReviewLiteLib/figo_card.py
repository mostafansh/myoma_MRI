"""Slicer-independent helpers for the on-image FIGO card."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .domain import FIGOClassification, FIGOKind, ReviewState, Type8Location, UncertainReason


def figo_label(figo: Mapping[str, Any] | None) -> str:
    """Short card/summary label for a saved FIGO classification dict."""
    if figo is None:
        return "FIGO ?"
    kind = figo.get("kind")
    if kind == "hybrid":
        return f"Hybrid {figo.get('endometrial')}-{figo.get('serosal')}"
    if kind == "uncertain":
        return "Uncertain"
    label = f"FIGO {figo.get('type')}"
    location = figo.get("location")
    if location:
        label += f" ({str(location).replace('_', ' ')})"
    return label


def missing_figo_ids(state: ReviewState) -> list[str]:
    """IDs of active lesions that still have no FIGO classification, in list order."""
    return [lesion.lesion_id for lesion in state.active_lesions if lesion.figo is None]


def card_position(
    box_rect: tuple[float, float, float, float],
    card_size: tuple[int, int],
    view_size: tuple[int, int],
    device_pixel_ratio: float = 1.0,
    gap: int = 8,
) -> tuple[int, int]:
    """Top-left of the card in Qt widget pixels, beside the box and inside the view.

    box_rect is (x_min, y_min, x_max, y_max) in VTK slice-view XY (origin bottom-left,
    physical pixels); card_size and view_size are logical Qt pixels.
    """
    x_min, _y_min, x_max, y_max = (v / device_pixel_ratio for v in box_rect)
    card_w, card_h = card_size
    view_w, view_h = view_size
    x = x_max + gap
    if x + card_w > view_w:
        x = x_min - gap - card_w
    top = view_h - y_max
    x = min(max(x, 0), max(view_w - card_w, 0))
    y = min(max(top, 0), max(view_h - card_h, 0))
    return int(round(x)), int(round(y))


class FigoCard:
    """Compact FIGO chooser shown beside a box; Qt is imported lazily."""

    def __init__(
        self,
        on_figo: Callable[[str, dict], None],
        on_text: Callable[[str, str, str], None],
        on_close: Callable[[], None],
    ) -> None:
        import qt

        self._qt = qt
        self._on_figo = on_figo
        self._on_text = on_text
        self._on_close = on_close
        self.lesion_id: str | None = None
        self.is_open = False
        self._loading = False
        self._kind_key = None
        self._orig_ddx = ""
        self._orig_comment = ""

        widget = qt.QFrame()
        widget.objectName = "MyomaFigoCard"
        widget.setWindowFlags(qt.Qt.FramelessWindowHint | qt.Qt.Widget)
        widget.autoFillBackground = True
        widget.setFrameShape(qt.QFrame.StyledPanel)
        widget.setMaximumWidth(240)
        widget.visible = False
        self.widget = widget

        layout = qt.QVBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)

        title_row = qt.QHBoxLayout()
        self._title = qt.QLabel("")
        title_row.addWidget(self._title, 1)
        close_button = qt.QToolButton()
        close_button.objectName = "figo_close"
        close_button.text = "×"
        close_button.connect("clicked()", self.close)
        title_row.addWidget(close_button)
        layout.addLayout(title_row)

        self._group = qt.QButtonGroup(widget)
        self._group.exclusive = True
        self._buttons: dict[object, Any] = {}
        grid = qt.QGridLayout()
        grid.setSpacing(2)
        for number in range(9):
            self._add_button(grid, number, f"figo_type_{number}", str(number), number, number // 5, number % 5)
        self._add_button(grid, "hybrid", "figo_hybrid", "Hybrid", "hybrid", 2, 0, 2)
        self._add_button(grid, "uncertain", "figo_uncertain", "Uncertain", "uncertain", 2, 2, 3)
        layout.addLayout(grid)

        self._location = self._combo(
            "figo_location", "Location…",
            [(m.value.replace("_", " "), m.value) for m in Type8Location],
        )
        self._endo = self._combo(
            "figo_endometrial", "Endometrial…", [(str(n), n) for n in range(9)]
        )
        self._sero = self._combo(
            "figo_serosal", "Serosal…", [(str(n), n) for n in range(9)]
        )
        self._reason = self._combo(
            "figo_reason", "Reason…",
            [(m.value.replace("_", " "), m.value) for m in UncertainReason],
        )
        self._combos = (self._location, self._endo, self._sero, self._reason)
        # A wheel turned over an unfocused list must scroll the image, not
        # silently change (and save) a FIGO choice.
        self._wheel_guard = self._make_wheel_guard()
        for combo in self._combos:
            combo.setFocusPolicy(qt.Qt.StrongFocus)
            combo.installEventFilter(self._wheel_guard)
            layout.addWidget(combo)
            combo.connect("currentIndexChanged(int)", self._on_choice_changed)

        self._ddx = qt.QLineEdit()
        self._ddx.objectName = "figo_ddx"
        self._ddx.placeholderText = "Differential diagnosis"
        self._ddx.maxLength = 4000
        self._comment = qt.QLineEdit()
        self._comment.objectName = "figo_comment"
        self._comment.placeholderText = "Comment"
        self._comment.maxLength = 4000
        layout.addWidget(self._ddx)
        layout.addWidget(self._comment)

        shortcut = qt.QShortcut(qt.QKeySequence("Esc"), widget)
        shortcut.context = qt.Qt.WidgetWithChildrenShortcut
        shortcut.connect("activated()", self.close)
        self._shortcut = shortcut
        self._update_rows(None)

    def _make_wheel_guard(self):
        qt = self._qt

        class _WheelGuard(qt.QObject):
            def eventFilter(self, obj, event):
                if event.type() == qt.QEvent.Wheel and not obj.hasFocus():
                    event.ignore()
                    return True
                return False

        return _WheelGuard()

    def _add_button(self, grid, key, name, text, value, row, col, span=1) -> None:
        button = self._qt.QToolButton()
        button.objectName = name
        button.text = text
        button.checkable = True
        button.setSizePolicy(self._qt.QSizePolicy.Expanding, self._qt.QSizePolicy.Fixed)
        self._group.addButton(button)
        grid.addWidget(button, row, col, 1, span)
        button.connect("clicked()", lambda v=value: self._on_type_clicked(v))
        self._buttons[key] = button

    def _combo(self, name, placeholder, items):
        combo = self._qt.QComboBox()
        combo.objectName = name
        combo.addItem(placeholder, None)
        for text, data in items:
            combo.addItem(text, data)
        return combo

    # -- selection state -------------------------------------------------
    def _selected_key(self):
        for key, button in self._buttons.items():
            if button.checked:
                return key
        return None

    def _update_rows(self, key) -> None:
        self._location.visible = key == 8
        self._endo.visible = key == "hybrid"
        self._sero.visible = key == "hybrid"
        self._reason.visible = key == "uncertain"

    def _reset_combos(self) -> None:
        was_loading, self._loading = self._loading, True
        try:
            for combo in self._combos:
                combo.setCurrentIndex(0)
        finally:
            self._loading = was_loading

    def _on_type_clicked(self, value) -> None:
        if self._loading:
            return
        # Re-clicking the kind already chosen must keep its saved choices.
        if value == self._kind_key:
            return
        self._kind_key = value
        self._update_rows(value)
        self._reset_combos()
        self._emit()

    def _on_choice_changed(self, _index=None) -> None:
        if not self._loading:
            self._emit()

    @staticmethod
    def _data(combo):
        return combo.itemData(combo.currentIndex)

    def _build_figo(self) -> dict | None:
        key = self._selected_key()
        if key is None:
            return None
        if key == "hybrid":
            endo, sero = self._data(self._endo), self._data(self._sero)
            if endo is None or sero is None:
                return None
            return {"kind": "hybrid", "endometrial": endo, "serosal": sero}
        if key == "uncertain":
            reason = self._data(self._reason)
            return None if reason is None else {"kind": "uncertain", "reason": reason}
        if key == 8:
            location = self._data(self._location)
            if location is None:
                return None
            return {"kind": "standard", "type": 8, "location": location}
        return {"kind": "standard", "type": key}

    def _emit(self) -> None:
        figo = self._build_figo()
        if figo is None or self.lesion_id is None:
            return
        try:
            FIGOClassification.from_dict(figo)
        except Exception:
            return
        self._on_figo(self.lesion_id, figo)

    def set_figo(self, figo: dict | None) -> None:
        was_loading, self._loading = self._loading, True
        try:
            self._group.exclusive = False
            for button in self._buttons.values():
                button.checked = False
            self._group.exclusive = True
            self._reset_combos()
            key = None
            if figo is not None:
                kind = figo.get("kind")
                if kind == FIGOKind.HYBRID.value:
                    key = "hybrid"
                    self._select(self._endo, figo.get("endometrial"))
                    self._select(self._sero, figo.get("serosal"))
                elif kind == FIGOKind.UNCERTAIN.value:
                    key = "uncertain"
                    self._select(self._reason, figo.get("reason"))
                else:
                    key = figo.get("type")
                    if key == 8:
                        self._select(self._location, figo.get("location"))
                if key in self._buttons:
                    self._buttons[key].checked = True
                else:
                    key = None
            self._kind_key = key
            self._update_rows(key)
        finally:
            self._loading = was_loading

    @staticmethod
    def _select(combo, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    # -- lifecycle ---------------------------------------------------------
    def open(self, parent, lesion_id, figo, ddx, comment, read_only) -> None:
        if self.is_open:
            self.close()
        qt = self._qt
        self.lesion_id = lesion_id
        self._title.text = f"{lesion_id}  FIGO"
        self.widget.setParent(parent)
        self.set_figo(figo)
        self._ddx.text = ddx or ""
        self._comment.text = comment or ""
        self._orig_ddx = (ddx or "").strip()
        self._orig_comment = (comment or "").strip()
        for cls in (qt.QToolButton, qt.QComboBox, qt.QLineEdit):
            for control in self.widget.findChildren(cls):
                if control.objectName != "figo_close":
                    control.enabled = not read_only
        self.is_open = True
        self.widget.adjustSize()
        self.widget.show()
        self.widget.raise_()

    def has_unsaved_text(self) -> bool:
        """Whether closing would save Ddx/comment text typed since the card opened."""
        return bool(
            self.is_open
            and self.lesion_id is not None
            and (
                self._ddx.text.strip() != self._orig_ddx
                or self._comment.text.strip() != self._orig_comment
            )
        )

    def close(self) -> None:
        if not self.is_open:
            return
        unsaved = self.has_unsaved_text()
        self.is_open = False
        try:
            if unsaved:
                self._on_text(self.lesion_id, self._ddx.text.strip(), self._comment.text.strip())
        finally:
            self.widget.hide()
            self._on_close()

    def move_to(self, x: int, y: int) -> None:
        self.widget.move(int(x), int(y))

    def size(self) -> tuple[int, int]:
        hint = self.widget.sizeHint
        return int(hint.width()), int(hint.height())
