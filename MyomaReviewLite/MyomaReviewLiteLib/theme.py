"""The module's dark look: colours, Qt stylesheets and row painters.

Everything visual lives here so ``MyomaReviewLite.py`` only tags widgets with a
``role`` property (``primary``, ``secondary``, ``pill`` ...) and the
stylesheets below decide how each role looks. Qt is imported lazily so the
pure helpers stay importable outside Slicer.
"""

from __future__ import annotations

from typing import Any

# Colour tokens. Neutral surfaces are a touch cooler than Slicer's own dark
# grey so the panel reads as one piece; status colours match the case tags.
BG = "#24272c"
SURFACE = "#2c3036"
SURFACE_HOVER = "#33383f"
FIELD = "#1b1d21"
BORDER = "#3b4048"
TEXT = "#e6e8eb"
MUTED = "#9aa1aa"
ACCENT = "#4f86d1"
ACCENT_HOVER = "#6197e0"
ACCENT_SOFT = "#2c3a50"
AMBER = "#f0b85a"

# Per status: (bar / dot colour, tag background, tag text).
STATUS_COLOURS = {
    "complete": ("#4fae72", "#2b4a36", "#9fe0b3"),
    "in_progress": ("#e3a04a", "#4f3c1f", "#f6cd88"),
    "attention": ("#e0605a", "#55302d", "#f6aaa4"),
    "new": ("#59606a", "#3b4048", "#c0c6ce"),
}
FIGO_SET_COLOURS = ("#4fae72", "#2b4a36", "#9fe0b3")
FIGO_MISSING_COLOURS = (AMBER, "#52401f", AMBER)

PANEL_QSS = f"""
QWidget {{ font-family: "Segoe UI"; }}
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

QPushButton {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 7px 12px; }}
QPushButton:hover {{ background: {SURFACE_HOVER}; }}
QPushButton:disabled {{ color: #6b717a; background: #282b30; border-color: #30343a; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border: 1px solid {ACCENT};
    color: white; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[role="primary"]:disabled {{ background: #303a48; border-color: #303a48; color: #7d8794; }}
QPushButton[role="ghost"] {{ background: transparent; border: 1px dashed #4a505a; color: {TEXT}; }}
QPushButton[role="ghost"]:hover {{ background: {SURFACE}; }}
QPushButton[role="ghost"]:disabled {{ color: #6b717a; border-color: #3a3e45; background: transparent; }}
QPushButton[role="link"] {{ background: transparent; border: none; color: {ACCENT_HOVER};
    padding: 0px; text-align: left; }}
QPushButton[role="link"]:hover {{ color: #8ab4ee; }}

QToolButton[role="pill"] {{ background: {SURFACE}; color: {MUTED}; border: none;
    border-radius: 12px; padding: 4px 11px; }}
QToolButton[role="pill"]:hover {{ color: {TEXT}; }}
QToolButton[role="pill"]:checked {{ background: {ACCENT}; color: white; }}
QToolButton[role="icon"] {{ background: {SURFACE}; color: {MUTED}; border: none;
    border-radius: 6px; padding: 4px 9px; }}
QToolButton[role="icon"]:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}
QToolButton[role="bar"] {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 8px; text-align: left; }}
QToolButton[role="bar"]:checked {{ border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; }}

QLineEdit, QPlainTextEdit {{ background: {FIELD}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QPlainTextEdit[role="comment"] {{ border-top-left-radius: 0px; border-top-right-radius: 0px;
    border: 1px solid {ACCENT}; border-top: none; background: {FIELD}; }}
QPlainTextEdit[role="comment"][opens="up"] {{ border-radius: 6px; border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px; border: 1px solid {ACCENT}; border-bottom: none; }}
QToolButton[role="bar"]:checked {{ border-color: {ACCENT}; }}

QListWidget[role="rows"] {{ background: transparent; border: none; outline: none; }}

QCheckBox {{ color: {TEXT}; spacing: 8px; }}
"""

CARD_QSS = f"""
QFrame#MyomaFigoCard, QFrame#MyomaFigoCard QWidget {{ font-family: "Segoe UI"; }}
QFrame#MyomaFigoCard {{ background: {SURFACE}; border: 1px solid #454b54; border-radius: 8px; }}
QFrame#MyomaFigoCard QLabel {{ color: {TEXT}; font-weight: 600; background: transparent; }}
QFrame#MyomaFigoCard QToolButton {{ background: #363b42; color: {TEXT}; border: none;
    border-radius: 5px; padding: 5px 4px; min-width: 22px; }}
QFrame#MyomaFigoCard QToolButton:hover {{ background: #40464e; }}
QFrame#MyomaFigoCard QToolButton:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}
QFrame#MyomaFigoCard QToolButton#figo_close {{ background: transparent; color: {MUTED};
    font-size: 14px; min-width: 0px; }}
QFrame#MyomaFigoCard QToolButton#figo_close:hover {{ color: {TEXT}; }}
QFrame#MyomaFigoCard QToolButton:disabled {{ color: #6b717a; }}
QFrame#MyomaFigoCard QLineEdit, QFrame#MyomaFigoCard QComboBox {{ background: {FIELD};
    color: {TEXT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px; }}
QFrame#MyomaFigoCard QLineEdit:focus, QFrame#MyomaFigoCard QComboBox:focus {{ border-color: {ACCENT}; }}
QFrame#MyomaFigoCard QComboBox QAbstractItemView {{ background: {FIELD}; color: {TEXT};
    border: 1px solid {BORDER}; selection-background-color: {ACCENT}; selection-color: white;
    outline: none; }}
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
            subtitle = index.data(ROW_SUBTITLE)
            return qt.QSize(120, 44 if subtitle else 34)

        def paint(self, painter, option, index):
            painter.save()
            try:
                painter.setRenderHint(qt.QPainter.Antialiasing, True)
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


# Shown in place of Slicer's own name and logo while the module is open.
BRAND_NAME = "Myoma Review"


class AppBranding:
    """Swap Slicer's logo, window title and icon for ours while the module is shown.

    ``enter`` remembers what Slicer showed and ``exit`` puts it back, so other
    modules still look like plain Slicer.
    """

    def __init__(self, logo_path: str) -> None:
        self._logo_path = logo_path
        self._saved: dict[str, Any] | None = None

    def enter(self) -> None:
        import qt
        import slicer

        window = slicer.util.mainWindow()
        if window is None or self._saved is not None:
            return
        logo = window.findChild(qt.QLabel, "LogoLabel")
        self._saved = {
            "title": window.windowTitle,
            "icon": window.windowIcon,
            "app_icon": slicer.app.windowIcon,
            "logo_pixmap": qt.QPixmap(logo.pixmap) if logo is not None and logo.pixmap is not None else None,
        }
        icon = qt.QIcon(self._logo_path)
        window.windowTitle = BRAND_NAME
        # Slicer rewrites the title when a scene loads; keep ours on top.
        window.connect("windowTitleChanged(QString)", self._keep_title)
        window.setWindowIcon(icon)
        slicer.app.setWindowIcon(icon)
        if logo is not None:
            logo.setPixmap(self._logo_pixmap(icon))

    def exit(self) -> None:
        import qt
        import slicer

        window = slicer.util.mainWindow()
        if window is None or self._saved is None:
            return
        saved, self._saved = self._saved, None
        window.disconnect("windowTitleChanged(QString)", self._keep_title)
        window.windowTitle = saved["title"]
        window.setWindowIcon(saved["icon"])
        slicer.app.setWindowIcon(saved["app_icon"])
        logo = window.findChild(qt.QLabel, "LogoLabel")
        if logo is not None and saved["logo_pixmap"] is not None:
            logo.setPixmap(saved["logo_pixmap"])

    def _keep_title(self, title: str) -> None:
        import slicer

        if self._saved is None or title == BRAND_NAME:
            return
        # What Slicer would show now is what exit() should put back.
        self._saved["title"] = title
        slicer.util.mainWindow().windowTitle = BRAND_NAME

    @staticmethod
    def _logo_pixmap(icon: Any) -> Any:
        """Our icon and name drawn side by side, about the size of Slicer's logo."""

        import qt

        height, icon_size = 40, 32
        font = qt.QFont("Segoe UI")
        font.setPixelSize(22)
        metrics = qt.QFontMetrics(font)
        text_width = metrics.horizontalAdvance(BRAND_NAME) if hasattr(metrics, "horizontalAdvance") else metrics.width(BRAND_NAME)
        pixmap = qt.QPixmap(icon_size + 12 + text_width + 4, height)
        pixmap.fill(qt.QColor(0, 0, 0, 0))
        painter = qt.QPainter(pixmap)
        try:
            painter.setRenderHint(qt.QPainter.Antialiasing, True)
            painter.drawPixmap((height - icon_size) // 2, (height - icon_size) // 2, icon.pixmap(icon_size, icon_size))
            painter.setFont(font)
            painter.setPen(qt.QColor(TEXT))
            painter.drawText(
                qt.QRect(icon_size + 12, 0, text_width + 4, height),
                int(qt.Qt.AlignLeft | qt.Qt.AlignVCenter),
                BRAND_NAME,
            )
        finally:
            painter.end()
        return pixmap
