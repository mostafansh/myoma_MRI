"""Slicer-runtime check that the bundled Hanken Grotesk loads (or fails soft)."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import qt
import slicer


MODULE_ROOT = Path(__file__).resolve().parents[1]
for module_name in tuple(sys.modules):
    if module_name == "MyomaReviewLite" or module_name.startswith("MyomaReviewLiteLib"):
        del sys.modules[module_name]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != MODULE_ROOT]
sys.path.insert(0, str(MODULE_ROOT))

from MyomaReviewLiteLib.fonts import load_bundled_fonts  # noqa: E402

FONT_DIR = MODULE_ROOT / "Resources" / "Fonts"


def test_bundled_font_is_registered_once() -> None:
    families = load_bundled_fonts(FONT_DIR)
    assert "Hanken Grotesk" in families, families
    assert "Hanken Grotesk" in qt.QFontDatabase().families()
    before = len(qt.QFontDatabase().families())
    assert load_bundled_fonts(FONT_DIR) == families
    assert len(qt.QFontDatabase().families()) == before


def test_load_bundled_fonts_returns_empty_for_a_missing_folder() -> None:
    assert load_bundled_fonts(Path("does/not/exist")) == []


try:
    test_bundled_font_is_registered_once()
    test_load_bundled_fonts_returns_empty_for_a_missing_folder()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite fonts: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
