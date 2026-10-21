"""Report how the module panel and the main window size up on the VoxLab screen.

Run with Slicer --no-splash --python-script <this file> <json> [<queue.json>] [<png-prefix>].

It loads the real module in Slicer's own module panel, opens the first case of
<queue.json> when one is given, and for each panel page (the annotation page,
the correction page and the queue drawer) prints the module widget's and the
main window's minimumSizeHint.  It then asks the window for 1920x1080 (the
VoxLab screen), 1440x900 and 1280x720 and reports the size it actually takes.
A window that cannot shrink to the screen is the layout bug readers see: VoxLab
runs Slicer with no window manager, so nothing clamps the window and the bottom
of the four panes hangs off the screen.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import qt
import slicer


MODULE_NAME = "MyomaReviewLite"
SIZES = ((1920, 1080), (1440, 900), (1280, 720))


def _size_hint(widget):
    hint = widget.minimumSizeHint
    return [int(hint.width()), int(hint.height())]


def _rect(widget):
    geometry = widget.geometry
    return [int(geometry.x()), int(geometry.y()), int(geometry.width()), int(geometry.height())]


def _settle(rounds: int = 5) -> None:
    for _ in range(rounds):
        slicer.app.processEvents()


def _views_inside_series(module) -> dict:
    """Each view's slice offset against its own series' range along the view normal.

    A view opened outside its series shows nothing but black.
    """

    from MyomaReviewLite import VIEW_NAMES

    layout_manager = slicer.app.layoutManager()
    report = {}
    for plane, view_name in VIEW_NAMES.items():
        widget = layout_manager.sliceWidget(view_name)
        if widget is None or module.logic.volume_nodes.get(plane) is None:
            report[plane] = None
            continue
        logic = widget.sliceLogic()
        bounds = [0.0] * 6
        logic.GetLowestVolumeSliceBounds(bounds)
        offset = float(logic.GetSliceOffset())
        report[plane] = {
            "offset": round(offset, 2),
            "range": [round(bounds[4], 2), round(bounds[5], 2)],
            "inside": bounds[4] - 0.01 <= offset <= bounds[5] + 0.01,
        }
    return report


def _measure(window, widget, label: str, screenshot_prefix: Path | None) -> dict:
    _settle()
    report: dict[str, object] = {
        "module_widget_minimum": _size_hint(widget),
        "window_minimum": _size_hint(window),
    }
    for width, height in SIZES:
        window.setGeometry(0, 0, width, height)
        _settle()
        geometry = _rect(window)
        report[f"requested_{width}x{height}"] = geometry
        report[f"fits_{width}x{height}"] = geometry[2] <= width and geometry[3] <= height
    window.setGeometry(0, 0, 1920, 1080)
    _settle()
    if screenshot_prefix is not None:
        path = Path(f"{screenshot_prefix}_{label}.png")
        window.grab().save(str(path))
        report["screenshot"] = str(path)
    return report


def main(argv: list[str]) -> int:
    output = Path(argv[1]) if len(argv) > 1 else Path("layout.json")
    queue = argv[2] if len(argv) > 2 and argv[2] else None
    screenshot_prefix = Path(argv[3]) if len(argv) > 3 else None
    payload: dict[str, object] = {}
    try:
        slicer.util.selectModule(MODULE_NAME)
        _settle()
        window = slicer.util.mainWindow()
        widget = slicer.modules.myomareviewlite.widgetRepresentation()
        module = widget.self()
        payload["screens"] = [
            [int(s.geometry.width()), int(s.geometry.height())] for s in qt.QGuiApplication.screens()
        ]
        if queue:
            module.open_case(queue)
            _settle(20)
            payload["case_id"] = module.logic.case_id
            payload["views_inside_series"] = _views_inside_series(module)
        payload["annotation_page"] = _measure(window, widget, "annotation", screenshot_prefix)
        module.page_stack.setCurrentIndex(1)
        payload["correction_page"] = _measure(window, widget, "correction", screenshot_prefix)
        module.page_stack.setCurrentIndex(0)
        module.queue_toggle.checked = True
        payload["queue_drawer"] = _measure(window, widget, "queue", screenshot_prefix)
        module.queue_toggle.checked = False
        _settle()
    except Exception:
        payload["error"] = traceback.format_exc()
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("layout report:", output)
    return 0


if __name__ == "__main__":
    code = main(sys.argv)
    slicer.app.processEvents()
    slicer.util.exit(code)
