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
    min_clearance: int = 48,
) -> tuple[int, int]:
    """Top-left of the card in Qt widget pixels, clear of the box and inside the view.

    The card keeps the tissue around the box visible for FIGO reading: it
    sits half the box's size away (at least ``min_clearance``) on the first
    side with that much room (right, left, below, above); failing that, as
    far away as the roomiest side allows, but never closer than ``gap``.

    box_rect is (x_min, y_min, x_max, y_max) in VTK slice-view XY (origin bottom-left,
    physical pixels); card_size and view_size are logical Qt pixels.
    """
    x_min, y_min, x_max, y_max = (v / device_pixel_ratio for v in box_rect)
    card_w, card_h = card_size
    view_w, view_h = view_size
    top, bottom = view_h - y_max, view_h - y_min
    clearance = max(min_clearance, 0.5 * max(x_max - x_min, bottom - top))
    # Free space between the box and the view edge once the card is placed.
    room = {
        "right": view_w - x_max - card_w,
        "left": x_min - card_w,
        "below": view_h - bottom - card_h,
        "above": top - card_h,
    }
    side = next((name for name, free in room.items() if free >= clearance), None)
    if side is None:
        side = max(room, key=lambda name: room[name])
    distance = min(clearance, room[side])
    if distance < gap:
        x, y = x_max + gap, top
    elif side == "right":
        x, y = x_max + distance, top
    elif side == "left":
        x, y = x_min - distance - card_w, top
    elif side == "below":
        x, y = x_min, bottom + distance
    else:
        x, y = x_min, top - distance - card_h
    x = min(max(x, 0), max(view_w - card_w, 0))
    y = min(max(y, 0), max(view_h - card_h, 0))
    return int(round(x)), int(round(y))


class FigoCard:
    """Compact FIGO chooser shown beside a box; Qt is imported lazily.

    Every choice is a button: the FIGO type row (0-8), Hybrid / Uncertain, and
    framed rows for the parts those need (type-8 location, Hybrid endometrial
    and serosal sides, Uncertain reason). While Hybrid is picked the type row
    folds into a one-line bar, so the two side rows have the room.
    """

    COLLAPSED_TEXT = "FIGO type 0–8 · hidden while Hybrid is picked"

    def __init__(
        self,
        on_figo: Callable[[str, dict], None],
        on_text: Callable[[str, str, str], None],
        on_close: Callable[[], None],
        on_resize: Callable[[], None] | None = None,
        on_question: Callable[[str, str | None], None] | None = None,
    ) -> None:
        import qt

        self._qt = qt
        self._on_figo = on_figo
        self._on_text = on_text
        self._on_close = on_close
        self._on_resize = on_resize
        self._on_question = on_question
        self._orig_question: str | None = None
        self.lesion_id: str | None = None
        self.is_open = False
        self._loading = False
        self._kind_key = None
        self._type_expanded = False
        self._orig_ddx = ""
        self._orig_comment = ""
        # Where the owner last asked the card to sit, and how far the reader
        # dragged each box's card from there; the drag follows the box.
        self._auto: tuple[int, int] | None = None
        self._offsets: dict[str, tuple[int, int]] = {}
        self._drag: tuple[Any, Any] | None = None
        self._placed = False

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
        layout.setSpacing(6)

        title_row = qt.QHBoxLayout()
        self._title = qt.QLabel("")
        self._title.toolTip = "Drag to move the card"
        self._title.setCursor(qt.QCursor(qt.Qt.OpenHandCursor))
        title_row.addWidget(self._title, 1)
        close_button = qt.QToolButton()
        close_button.objectName = "figo_close"
        close_button.text = "×"
        close_button.connect("clicked()", self.close)
        title_row.addWidget(close_button)
        layout.addLayout(title_row)

        # The kind: one exclusive group over the nine types, Hybrid and Uncertain.
        self._group = qt.QButtonGroup(widget)
        self._group.exclusive = True
        self._buttons: dict[object, Any] = {}
        self._type_group, type_grid = self._framed("figo_type_group", "FIGO type")
        for number in range(9):
            self._add_kind_button(type_grid, number, f"figo_type_{number}", str(number), 0, number)
        layout.addWidget(self._type_group)
        self._type_collapsed = qt.QToolButton()
        self._type_collapsed.objectName = "figo_type_collapsed"
        self._type_collapsed.text = self.COLLAPSED_TEXT
        self._type_collapsed.toolButtonStyle = qt.Qt.ToolButtonTextBesideIcon
        self._type_collapsed.arrowType = qt.Qt.RightArrow
        self._type_collapsed.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
        self._type_collapsed.connect("clicked()", self._expand_type_row)
        layout.addWidget(self._type_collapsed)
        kinds = qt.QGridLayout()
        kinds.setSpacing(2)
        self._add_kind_button(kinds, "hybrid", "figo_hybrid", "Hybrid", 0, 0)
        self._add_kind_button(kinds, "uncertain", "figo_uncertain", "Uncertain", 0, 1)
        layout.addLayout(kinds)

        # The parts some kinds need, each its own exclusive row.
        self._location = self._choice_row(
            layout, "figo_location", "Location",
            [(m.value, m.value.replace("_", " ")) for m in Type8Location], columns=1,
        )
        self._endo = self._choice_row(
            layout, "figo_endo", "Endometrial side", [(n, str(n)) for n in range(9)], columns=9,
        )
        self._sero = self._choice_row(
            layout, "figo_sero", "Serosal side", [(n, str(n)) for n in range(9)], columns=9,
        )
        self._reason = self._choice_row(
            layout, "figo_reason", "Reason",
            [(m.value, m.value.replace("_", " ")) for m in UncertainReason], columns=1,
        )
        self._choices = (self._location, self._endo, self._sero, self._reason)

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

        # A question for the senior reviewer: a tag on this myoma, with optional text.
        self._question_toggle = qt.QToolButton()
        self._question_toggle.objectName = "figo_question"
        self._question_toggle.text = "?  Question for senior reviewer"
        self._question_toggle.checkable = True
        self._question_toggle.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
        self._question_toggle.connect("toggled(bool)", self._question_toggled)
        layout.addWidget(self._question_toggle)
        self._question = qt.QLineEdit()
        self._question.objectName = "figo_question_text"
        self._question.placeholderText = "What to ask (optional)"
        self._question.maxLength = 4000
        self._question.visible = False
        layout.addWidget(self._question)

        shortcut = qt.QShortcut(qt.QKeySequence("Esc"), widget)
        shortcut.context = qt.Qt.WidgetWithChildrenShortcut
        shortcut.connect("activated()", self.close)
        self._shortcut = shortcut

        card = self

        class _DragFilter(qt.QObject):
            def eventFilter(self, obj, event):
                return card._drag_event(event)

        self._drag_filter = _DragFilter(widget)
        widget.installEventFilter(self._drag_filter)
        self._title.installEventFilter(self._drag_filter)
        self._update_rows(None)

    def dispose(self) -> None:
        """Kept for the owner's teardown; the card holds nothing to release."""

    def _framed(self, name: str, title: str):
        box = self._qt.QGroupBox(title)
        box.objectName = name
        grid = self._qt.QGridLayout(box)
        grid.setContentsMargins(4, 3, 4, 4)
        grid.setSpacing(2)
        return box, grid

    def _make_button(self, name: str, text: str):
        button = self._qt.QToolButton()
        button.objectName = name
        button.text = text
        button.checkable = True
        button.setSizePolicy(self._qt.QSizePolicy.Expanding, self._qt.QSizePolicy.Fixed)
        return button

    def _add_kind_button(self, grid, key, name, text, row, col) -> None:
        button = self._make_button(name, text)
        self._group.addButton(button)
        grid.addWidget(button, row, col)
        button.connect("clicked()", lambda v=key: self._on_type_clicked(v))
        self._buttons[key] = button

    def _choice_row(self, layout, prefix, title, items, columns):
        """A framed, exclusive row of choice buttons; returns (box, group, {value: button})."""
        box, grid = self._framed(f"{prefix}_group", title)
        group = self._qt.QButtonGroup(box)
        group.exclusive = True
        buttons = {}
        for index, (value, text) in enumerate(items):
            button = self._make_button(f"{prefix}_{value}", text)
            group.addButton(button)
            grid.addWidget(button, index // columns, index % columns)
            button.connect("clicked()", self._on_choice_changed)
            buttons[value] = button
        layout.addWidget(box)
        return box, group, buttons

    # -- selection state -------------------------------------------------
    def _selected_key(self):
        for key, button in self._buttons.items():
            if button.checked:
                return key
        return None

    @staticmethod
    def _value(choice):
        _box, _group, buttons = choice
        for value, button in buttons.items():
            if button.checked:
                return value
        return None

    def _expand_type_row(self) -> None:
        self._type_expanded = True
        self._update_rows(self._kind_key)

    def _update_rows(self, key) -> None:
        folded = key == "hybrid" and not self._type_expanded
        self._type_group.visible = not folded
        self._type_collapsed.visible = folded
        self._location[0].visible = key == 8
        self._endo[0].visible = key == "hybrid"
        self._sero[0].visible = key == "hybrid"
        self._reason[0].visible = key == "uncertain"
        if self.is_open:
            # The card sits on a slice view, not in a layout, so nothing grows
            # it for the extra rows; without this they squash the type buttons.
            self.widget.adjustSize()
            if self._on_resize is not None:
                self._on_resize()

    def _reset_choices(self) -> None:
        for _box, group, buttons in self._choices:
            group.exclusive = False
            for button in buttons.values():
                button.checked = False
            group.exclusive = True

    def _on_type_clicked(self, value) -> None:
        if self._loading:
            return
        # Re-clicking the kind already chosen must keep its saved choices.
        if value == self._kind_key:
            return
        self._kind_key = value
        self._type_expanded = False
        self._reset_choices()
        self._update_rows(value)
        self._emit()

    def _on_choice_changed(self, _checked=None) -> None:
        if not self._loading:
            self._emit()

    def _build_figo(self) -> dict | None:
        key = self._selected_key()
        if key is None:
            return None
        if key == "hybrid":
            endo, sero = self._value(self._endo), self._value(self._sero)
            if endo is None or sero is None:
                return None
            return {"kind": "hybrid", "endometrial": endo, "serosal": sero}
        if key == "uncertain":
            reason = self._value(self._reason)
            return None if reason is None else {"kind": "uncertain", "reason": reason}
        if key == 8:
            location = self._value(self._location)
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
            self._reset_choices()
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
            self._type_expanded = False
            self._update_rows(key)
        finally:
            self._loading = was_loading

    @staticmethod
    def _select(choice, value) -> None:
        button = choice[2].get(value)
        if button is not None:
            button.checked = True

    # -- lifecycle ---------------------------------------------------------
    def open(self, parent, lesion_id, figo, ddx, comment, read_only, question=None) -> None:
        if self.is_open:
            self.close()
        qt = self._qt
        self.lesion_id = lesion_id
        self._auto = None
        self._placed = False
        self._drag = None
        self._title.text = f"{lesion_id}  FIGO"
        self.widget.setParent(parent)
        self.set_figo(figo)
        self._ddx.text = ddx or ""
        self._comment.text = comment or ""
        self._orig_ddx = (ddx or "").strip()
        self._orig_comment = (comment or "").strip()
        self._loading = True
        try:
            self._question_toggle.checked = question is not None
            self._question.text = question or ""
            self._question.visible = question is not None
        finally:
            self._loading = False
        self._orig_question = None if question is None else question.strip()
        for cls in (qt.QAbstractButton, qt.QLineEdit):
            for control in self.widget.findChildren(cls):
                # Closing, and unfolding the type row to look, stay possible.
                if control.objectName not in ("figo_close", "figo_type_collapsed"):
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
                or self._question_changed()
            )
        )

    def close(self) -> None:
        if not self.is_open:
            return
        texts_changed = (
            self._ddx.text.strip() != self._orig_ddx or self._comment.text.strip() != self._orig_comment
        )
        question_changed = self._question_changed()
        self.is_open = False
        try:
            if texts_changed:
                self._on_text(self.lesion_id, self._ddx.text.strip(), self._comment.text.strip())
            if question_changed and self._on_question is not None:
                self._on_question(self.lesion_id, self._current_question())
        finally:
            self.widget.hide()
            self._on_close()

    def _current_question(self) -> str | None:
        return self._question.text.strip() if self._question_toggle.checked else None

    def _question_changed(self) -> bool:
        return self.is_open and self.lesion_id is not None and self._current_question() != self._orig_question

    def _question_toggled(self, checked: bool) -> None:
        """Tagging or untagging saves at once; typed text is saved on close."""
        self._question.visible = bool(checked)
        self.widget.adjustSize()
        if self._on_resize is not None:
            self._on_resize()
        if self._loading or not self.is_open or self.lesion_id is None:
            return
        if checked:
            self._question.setFocus()
        question = self._current_question()
        if self._on_question is not None and question != self._orig_question:
            self._orig_question = question
            self._on_question(self.lesion_id, question)

    def move_to(self, x: int, y: int) -> None:
        """Sit at (x, y) beside the box, shifted by wherever the reader dragged it."""
        self._auto = (int(x), int(y))
        dx, dy = self._offsets.get(self.lesion_id or "", (0, 0))
        self._place(x + dx, y + dy)

    def place_at(self, x: int, y: int) -> None:
        """Sit at (x, y) without a box to follow."""
        self._place(x, y)

    @property
    def placed(self) -> bool:
        """Whether the card has been put anywhere since it opened."""
        return self._placed

    def _place(self, x: float, y: float) -> None:
        parent = self.widget.parentWidget()
        if parent is not None:
            x = min(max(x, 0), max(parent.width - self.widget.width, 0))
            y = min(max(y, 0), max(parent.height - self.widget.height, 0))
        self.widget.move(int(x), int(y))
        self._placed = True

    def _drag_event(self, event: Any) -> bool:
        qt = self._qt
        kind = event.type()
        if kind == qt.QEvent.MouseButtonPress and event.button() == qt.Qt.LeftButton:
            self._drag = (event.globalPos(), self.widget.pos)
            self._title.setCursor(qt.QCursor(qt.Qt.ClosedHandCursor))
            return True
        if kind == qt.QEvent.MouseMove and self._drag is not None:
            start, origin = self._drag
            delta = event.globalPos() - start
            self.drag_to(origin.x() + delta.x(), origin.y() + delta.y())
            return True
        if kind == qt.QEvent.MouseButtonRelease and self._drag is not None:
            self._drag = None
            self._title.setCursor(qt.QCursor(qt.Qt.OpenHandCursor))
            return True
        return False

    def drag_to(self, x: int, y: int) -> None:
        """Move the card where the reader put it and remember that for its box."""
        self._place(x, y)
        if self._auto is not None and self.lesion_id:
            self._offsets[self.lesion_id] = (
                int(self.widget.x) - self._auto[0],
                int(self.widget.y) - self._auto[1],
            )

    def size(self) -> tuple[int, int]:
        hint = self.widget.sizeHint
        return int(hint.width()), int(hint.height())
