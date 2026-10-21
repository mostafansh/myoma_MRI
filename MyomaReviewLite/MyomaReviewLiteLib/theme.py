"""The module's dark look: colours, Qt stylesheets and row painters.

Everything visual lives here so ``MyomaReviewLite.py`` only tags widgets with a
``role`` property (``primary``, ``secondary``, ``pill`` ...) and the
stylesheets below decide how each role looks. Qt is imported lazily so the
pure helpers stay importable outside Slicer.
"""

from __future__ import annotations

from typing import Any

# Colour tokens: "Warm monochrome". Neutral warm greys with a cream
# highlight; the status colours below are the only real hues on the panel.
BG = "#1b1917"
SURFACE = "#24211f"
SURFACE_HOVER = "#2a2724"
BUTTON = "#2f2b28"
FIELD = "#141210"
BORDER = "#3a3532"
GHOST_BORDER = "#5a534c"
TEXT = "#efebe6"
MUTED = "#a9a199"
DISABLED_TEXT = "#8a8178"
ACCENT = "#e8e2d9"
ON_ACCENT = "#1b1917"
ACCENT_SOFT = "#363230"
AMBER = "#f0b85a"

# The module panel's own object name, so only it (not Slicer around it) is painted.
PANEL_OBJECT_NAME = "MyomaReviewLitePanel"

FONT_FAMILY = "Hanken Grotesk"
FONT_STACK = f'"{FONT_FAMILY}", "Segoe UI"'

# Per status: (bar / dot colour, tag background, tag text).
STATUS_COLOURS = {
    "complete": ("#4fae72", "#2b4a36", "#9fe0b3"),
    "in_progress": ("#e3a04a", "#4f3c1f", "#f6cd88"),
    "attention": ("#e0605a", "#55302d", "#f6aaa4"),
    "new": ("#59606a", "#3b4048", "#c0c6ce"),
}
FIGO_SET_COLOURS = ("#4fae72", "#2b4a36", "#9fe0b3")
FIGO_MISSING_COLOURS = (AMBER, "#52401f", AMBER)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two ``#rrggbb`` colours."""

    def luminance(colour: str) -> float:
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Every text-on-background pair the panel draws; each must stay readable.
CHECKED_PAIRS = (
    ("text on background", TEXT, BG),
    ("text on surface", TEXT, SURFACE),
    ("muted on background", MUTED, BG),
    ("muted on surface", MUTED, SURFACE),
    ("muted on selected row", MUTED, ACCENT_SOFT),
    ("primary button", ON_ACCENT, ACCENT),
    ("text on button", TEXT, BUTTON),
    ("text in field", TEXT, FIELD),
    ("warning hint on surface", AMBER, SURFACE),
    ("ok hint on surface", FIGO_SET_COLOURS[2], SURFACE),
    ("banner text", "#c9ebd3", FIGO_SET_COLOURS[1]),
    ("done step on background", FIGO_SET_COLOURS[2], BG),
    ("FIGO set tag", FIGO_SET_COLOURS[2], FIGO_SET_COLOURS[1]),
    ("FIGO missing tag", FIGO_MISSING_COLOURS[2], FIGO_MISSING_COLOURS[1]),
    *((f"{key} tag", tag_fg, tag_bg) for key, (_dot, tag_bg, tag_fg) in STATUS_COLOURS.items()),
)

PANEL_QSS = f"""
QWidget {{ font-family: {FONT_STACK}; }}
QWidget#{PANEL_OBJECT_NAME} {{ background: {BG}; }}
QStackedWidget, QStackedWidget > QWidget {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 15px; font-weight: bold; color: {TEXT}; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: 11px; font-weight: 600;
    letter-spacing: 1px; padding-top: 6px; }}
QLabel[role="warning"] {{ color: {AMBER}; }}
QFrame[role="divider"] {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 4px;
    margin-right: 16px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QPushButton {{ background: {BUTTON}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 7px 12px; }}
QPushButton:hover {{ background: {SURFACE_HOVER}; }}
QPushButton:disabled {{ color: {DISABLED_TEXT}; background: {SURFACE}; border-color: {BORDER}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border: 1px solid {ACCENT};
    color: {ON_ACCENT}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: #f4efe7; }}
QPushButton[role="primary"]:disabled {{ background: {BUTTON}; border-color: {BUTTON}; color: {DISABLED_TEXT}; }}
QPushButton[role="ghost"] {{ background: transparent; border: 1px dashed {GHOST_BORDER}; color: {TEXT}; }}
QPushButton[role="ghost"]:hover {{ background: {SURFACE}; }}
QPushButton[role="ghost"]:disabled {{ color: {DISABLED_TEXT}; border-color: {BORDER}; background: transparent; }}
QPushButton[role="link"] {{ background: transparent; border: none; color: {TEXT};
    padding: 0px; text-align: left; text-decoration: underline; }}
QPushButton[role="link"]:hover {{ color: #ffffff; }}

QToolButton[role="pill"] {{ background: {SURFACE}; color: {MUTED}; border: none;
    border-radius: 12px; padding: 4px 11px; }}
QToolButton[role="pill"]:hover {{ color: {TEXT}; }}
QToolButton[role="pill"]:checked {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QToolButton[role="icon"] {{ background: {SURFACE}; color: {MUTED}; border: none;
    border-radius: 6px; padding: 4px 9px; }}
QToolButton[role="icon"]:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}
QToolButton[role="strip"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QToolButton[role="strip"]:hover {{ background: {SURFACE_HOVER}; }}
QToolButton[role="strip"]:checked {{ border-color: {ACCENT}; }}
QToolButton[role="icon"]:disabled {{ background: transparent; }}
QToolButton[role="bar"] {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 8px; text-align: left; }}
QToolButton[role="bar"]:checked {{ border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; }}

QLineEdit, QPlainTextEdit {{ background: {FIELD}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 8px; selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QPlainTextEdit[role="comment"] {{ border: 1px solid {ACCENT}; border-radius: 8px; background: {FIELD}; }}
QToolButton[role="bar"]:checked {{ border-color: {ACCENT}; }}

QToolButton[role="step"] {{ background: transparent; color: {MUTED}; border: none;
    border-top: 3px solid {BORDER}; border-radius: 0px; padding: 6px 0px 2px 0px;
    text-align: left; font-size: 12px; }}
QToolButton[role="step"][state="done"] {{ border-top-color: {STATUS_COLOURS["complete"][0]};
    color: {FIGO_SET_COLOURS[2]}; }}
QToolButton[role="step"][state="now"] {{ border-top-color: {ACCENT}; color: {TEXT}; font-weight: 600; }}
QToolButton[role="step"]:enabled:hover {{ color: {TEXT}; }}
QToolButton[role="fold"] {{ background: transparent; color: {MUTED}; border: none; padding: 4px 0px;
    text-align: left; }}
QToolButton[role="fold"]:hover, QToolButton[role="fold"]:checked {{ color: {TEXT}; }}
QToolButton[role="icon"]:checked {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QFrame[role="footer"] {{ background: {SURFACE}; border: none; border-top: 1px solid {BORDER}; }}
QFrame[role="banner"] {{ background: {FIGO_SET_COLOURS[1]}; border: none; border-radius: 8px; }}
QLabel[role="banner-title"] {{ color: {FIGO_SET_COLOURS[2]}; font-weight: 600; }}
QLabel[role="banner-text"] {{ color: #c9ebd3; font-size: 12px; }}
QLabel[role="hint-muted"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[role="hint-warning"] {{ color: {AMBER}; font-size: 12px; }}
QLabel[role="hint-ok"] {{ color: {FIGO_SET_COLOURS[2]}; font-size: 12px; }}

QListWidget[role="rows"] {{ background: transparent; border: none; outline: none; }}

QCheckBox {{ color: {TEXT}; spacing: 8px; }}
"""

CARD_QSS = f"""
QFrame#MyomaFigoCard, QFrame#MyomaFigoCard QWidget {{ font-family: {FONT_STACK}; }}
QFrame#MyomaFigoCard {{ background: {SURFACE}; border: 1px solid {GHOST_BORDER}; border-radius: 8px; }}
QFrame#MyomaFigoCard QLabel {{ color: {TEXT}; font-weight: 600; background: transparent; }}
QFrame#MyomaFigoCard QToolButton {{ background: {BUTTON}; color: {TEXT}; border: none;
    border-radius: 5px; padding: 5px 2px; min-width: 18px; }}
QFrame#MyomaFigoCard QToolButton:hover {{ background: {SURFACE_HOVER}; }}
QFrame#MyomaFigoCard QToolButton:checked {{ background: {ACCENT}; color: {ON_ACCENT}; font-weight: 600; }}
QFrame#MyomaFigoCard QToolButton#figo_close {{ background: transparent; color: {MUTED};
    font-size: 14px; min-width: 0px; }}
QFrame#MyomaFigoCard QToolButton#figo_close:hover {{ color: {TEXT}; }}
QFrame#MyomaFigoCard QToolButton:disabled {{ color: {DISABLED_TEXT}; }}
QFrame#MyomaFigoCard QLineEdit {{ background: {FIELD}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px; }}
QFrame#MyomaFigoCard QLineEdit:focus {{ border-color: {ACCENT}; }}
QFrame#MyomaFigoCard QGroupBox {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 6px;
    margin-top: 9px; padding-top: 4px; }}
QFrame#MyomaFigoCard QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0px 4px;
    color: {MUTED}; font-size: 12px; }}
QFrame#MyomaFigoCard QToolButton#figo_type_collapsed {{ background: {BG}; color: {MUTED};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 8px; text-align: left; font-size: 12px; }}
QFrame#MyomaFigoCard QToolButton#figo_type_collapsed:hover {{ color: {TEXT}; }}
"""


def status_key(status: Any) -> str:
    """The colour key of a queue status (enum member, value or name)."""

    raw = getattr(status, "value", status)
    key = str(raw or "new").lower().replace(" ", "_").replace("-", "_")
    return {"finished": "complete", "completed": "complete"}.get(key, key)


def set_role(widget: Any, role: str) -> Any:
    """Tag ``widget`` for the stylesheet and return it."""

    widget.setProperty("role", role)
    return widget


class DarkAppStyle:
    """Switch Slicer to its "Dark Slicer" style while the module is shown.

    ``enter`` remembers the style the reader had and ``exit`` puts it back,
    so other modules look the way the reader configured them.
    """

    DARK = "Dark Slicer"

    def __init__(self) -> None:
        self._previous: str | None = None
        self._style: Any = None

    def enter(self) -> None:
        import qt

        current = str(qt.QSettings().value("Styles/Style") or "Slicer")
        if current.lower() == self.DARK.lower() or self._previous is not None:
            return
        self._previous = current
        self._apply(self.DARK)

    def exit(self) -> None:
        if self._previous is None:
            return
        previous, self._previous = self._previous, None
        self._apply(previous)

    def _apply(self, name: str) -> None:
        import qt
        import slicer

        style = qt.QStyleFactory.create(name)
        if style is None:
            return
        slicer.app.setStyle(style)
        slicer.app.setPalette(style.standardPalette())
        # QApplication does not own a style made by the factory for us in
        # PythonQt; keep it alive as long as it is in use.
        self._style = style


def _pill_metrics(painter: Any, text: str) -> tuple[int, int]:
    metrics = painter.fontMetrics()
    width = metrics.horizontalAdvance(text) if hasattr(metrics, "horizontalAdvance") else metrics.width(text)
    return width + 16, metrics.height() + 4


def make_row_delegate(parent: Any) -> Any:
    """A list painter for card-like rows: title (+ subtitle) left, a tag right.

    Each item carries its look in data roles: ``ROW_TITLE``, ``ROW_SUBTITLE``,
    ``ROW_TAG`` and ``ROW_COLOURS`` (dot, tag background, tag text). The item's
    plain text is left as is (tests and accessibility read it) but not drawn.
    """

    import qt

    class _RowDelegate(qt.QStyledItemDelegate):
        def sizeHint(self, option, index):
            if index.data(ROW_HEADER):
                return qt.QSize(120, 26)
            subtitle = index.data(ROW_SUBTITLE)
            return qt.QSize(120, 44 if subtitle else 34)

        def paint(self, painter, option, index):
            painter.save()
            try:
                painter.setRenderHint(qt.QPainter.Antialiasing, True)
                if index.data(ROW_HEADER):
                    # A group title: small muted caps, never hovered or picked.
                    font = qt.QFont(painter.font())
                    font.setPointSizeF(max(font.pointSizeF() - 1.5, 6.0))
                    font.setWeight(qt.QFont.DemiBold)
                    font.setLetterSpacing(qt.QFont.AbsoluteSpacing, 1.0)
                    painter.setFont(font)
                    painter.setPen(qt.QColor(MUTED))
                    painter.drawText(
                        option.rect.adjusted(6, 0, 0, -2),
                        int(qt.Qt.AlignLeft | qt.Qt.AlignBottom),
                        str(index.data(ROW_TITLE) or ""),
                    )
                    return
                rect = option.rect.adjusted(0, 2, -2, -2)
                selected = bool(int(option.state) & int(qt.QStyle.State_Selected))
                hovered = bool(int(option.state) & int(qt.QStyle.State_MouseOver))
                if selected or hovered:
                    painter.setPen(qt.QPen(qt.QColor(ACCENT), 1) if selected else qt.Qt.NoPen)
                    painter.setBrush(qt.QColor(ACCENT_SOFT if selected else SURFACE))
                    painter.drawRoundedRect(qt.QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
                colours = index.data(ROW_COLOURS) or ("", "", "")
                dot, tag_bg, tag_fg = (list(colours) + ["", "", ""])[:3]
                x = rect.left() + 10
                if dot:
                    painter.setPen(qt.Qt.NoPen)
                    painter.setBrush(qt.QColor(dot))
                    painter.drawEllipse(qt.QRectF(x, rect.center().y() - 4, 8, 8))
                    x += 16
                title = str(index.data(ROW_TITLE) or index.data(qt.Qt.DisplayRole) or "")
                subtitle = str(index.data(ROW_SUBTITLE) or "")
                tag = str(index.data(ROW_TAG) or "")
                tag_width = 0
                if tag:
                    tag_width, tag_height = _pill_metrics(painter, tag)
                    tag_rect = qt.QRectF(
                        rect.right() - 8 - tag_width,
                        rect.center().y() - tag_height / 2,
                        tag_width,
                        tag_height,
                    )
                    painter.setPen(qt.Qt.NoPen)
                    painter.setBrush(qt.QColor(tag_bg or SURFACE))
                    painter.drawRoundedRect(tag_rect, tag_height / 2, tag_height / 2)
                    painter.setPen(qt.QColor(tag_fg or TEXT))
                    painter.drawText(tag_rect, qt.Qt.AlignCenter, tag)
                text_rect = qt.QRect(x, rect.top(), rect.right() - x - tag_width - 16, rect.height())
                painter.setPen(qt.QColor(TEXT))
                if subtitle:
                    top = qt.QRect(text_rect.left(), rect.top() + 4, text_rect.width(), rect.height() // 2 - 2)
                    bottom = qt.QRect(text_rect.left(), rect.center().y(), text_rect.width(), rect.height() // 2 - 4)
                    painter.drawText(top, int(qt.Qt.AlignLeft | qt.Qt.AlignVCenter), title)
                    painter.setPen(qt.QColor(tag_fg or MUTED))
                    painter.drawText(bottom, int(qt.Qt.AlignLeft | qt.Qt.AlignVCenter), subtitle)
                else:
                    painter.drawText(text_rect, int(qt.Qt.AlignLeft | qt.Qt.AlignVCenter), title)
            finally:
                painter.restore()

    return _RowDelegate(parent)


# Item data roles read by the row delegate (Qt.UserRole + n; UserRole itself
# keeps holding the case or lesion id).
ROW_TITLE = 0x0100 + 10
ROW_SUBTITLE = 0x0100 + 11
ROW_TAG = 0x0100 + 12
ROW_COLOURS = 0x0100 + 13
ROW_HEADER = 0x0100 + 14
