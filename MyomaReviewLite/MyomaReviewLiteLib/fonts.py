"""Register the fonts bundled under Resources/Fonts with Qt; Qt is imported lazily."""

from __future__ import annotations

from pathlib import Path

# Resolved folder -> families loaded from it, so a module reload never
# registers the same files twice.
_LOADED: dict[Path, list[str]] = {}


def load_bundled_fonts(font_dir: Path) -> list[str]:
    """Load every ``*.ttf`` in ``font_dir``; return the family names Qt now knows.

    A missing folder or a file Qt refuses is skipped, so the panel falls back to
    the stylesheet's next family instead of failing to build.
    """

    key = Path(font_dir).resolve()
    if key in _LOADED:
        return _LOADED[key]
    families: list[str] = []
    if key.is_dir():
        import qt

        for path in sorted(key.glob("*.ttf")):
            font_id = qt.QFontDatabase.addApplicationFont(str(path))
            if font_id == -1:
                continue
            for family in qt.QFontDatabase.applicationFontFamilies(font_id):
                if family not in families:
                    families.append(family)
    _LOADED[key] = families
    return families
