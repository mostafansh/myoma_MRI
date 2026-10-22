"""Studio lockdown: lock Slicer's own chrome when a module is served to a browser.

A user in the studio must not reach the Python console, file dialogs, Slicer's
menus or other modules. The mode is switched on by ``SLICER_STUDIO=1`` and
changes nothing when it is off. The module to keep the user on is named by
``STUDIO_START_MODULE``.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Mapping, Optional

STUDIO_ENV = "SLICER_STUDIO"
START_ENV = "STUDIO_START_MODULE"
LOCKED_ACTION_PATTERN = re.compile(r"^(File|Edit|View|Window|Help)\w*Action$")
KEPT_DOCK_NAME = "PanelDockWidget"
_GUARD_PROPERTY = "studioLocked"

# State of the keep-on-module hook; the signal is connected at most once.
_keep = {"module": None, "connected": False, "quitting": False}
# True only after install() has locked the window and pinned the start module.
_installed = False


def is_studio_mode(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when the variable is exactly ``"1"``."""
    source = os.environ if environ is None else environ
    return source.get(STUDIO_ENV) == "1"


def locked_action_names(names: Iterable[str]) -> list:
    """The action names the lockdown disables, in their original order."""
    return [name for name in names if LOCKED_ACTION_PATTERN.match(name)]


def lock_down(main_window: Any) -> None:
    """Hide Slicer's chrome and disable its menu actions. Safe to repeat."""
    import qt  # imported here so the pure tests run without Slicer

    if main_window is None:
        return
    main_window.menuBar().setVisible(False)
    main_window.statusBar().setVisible(False)
    for toolbar in main_window.findChildren(qt.QToolBar):
        toolbar.setVisible(False)
    for dock in main_window.findChildren(qt.QDockWidget):
        if dock.objectName != KEPT_DOCK_NAME:
            dock.setVisible(False)
            toggle = dock.toggleViewAction()
            toggle.setEnabled(False)
            toggle.setShortcuts([])
            if not dock.property(_GUARD_PROPERTY):
                # A disabled action still fires when triggered from code, so
                # keep the dock closed whatever asks to show it.
                dock.setProperty(_GUARD_PROPERTY, True)
                dock.connect("visibilityChanged(bool)", lambda visible, d=dock: d.hide() if visible else None)
    for action in main_window.findChildren(qt.QAction):
        if LOCKED_ACTION_PATTERN.match(str(action.objectName)):
            action.setEnabled(False)
            action.setShortcuts([])
    main_window.setAcceptDrops(False)


def _on_module_selected(selected: str) -> None:
    import qt
    import slicer

    target = _keep["module"]
    if _keep["quitting"] or not target or str(selected) == target:
        return
    # Re-select on the next event-loop turn, not from inside the signal.
    qt.QTimer.singleShot(0, lambda: None if _keep["quitting"] else slicer.util.selectModule(target))


def _on_about_to_quit() -> None:
    _keep["quitting"] = True


def keep_on_module(module_name: str) -> None:
    """Return to ``module_name`` whenever another module gets selected. Idempotent."""
    import slicer

    _keep["module"] = module_name
    if _keep["connected"]:
        return
    _keep["connected"] = True
    slicer.app.connect("aboutToQuit()", _on_about_to_quit)
    slicer.util.mainWindow().moduleSelector().connect("moduleSelected(QString)", _on_module_selected)


def is_installed() -> bool:
    """True once install() fully succeeded in this process."""
    return _installed


def install(environ: Optional[Mapping[str, str]] = None, main_window: Any = None) -> None:
    """In studio mode, lock the main window, select the start module and pin it.

    Raises when the start module cannot be selected: an unlocked fallback module
    (Welcome) must never be served.
    """
    global _installed
    source = os.environ if environ is None else environ
    if not is_studio_mode(source):
        return
    import slicer

    if main_window is None:
        main_window = slicer.util.mainWindow()
    lock_down(main_window)
    start = source.get(START_ENV, "")
    if start:
        # The selector reports whatever name it was given, even for a module
        # that does not exist, so ask the module manager as well.
        if slicer.app.moduleManager().module(start) is None:
            raise RuntimeError(f"start module {start!r} is not loaded in this Slicer")
        slicer.util.selectModule(start)
        if slicer.util.selectedModule() != start:
            raise RuntimeError(
                f"start module {start!r} could not be selected "
                f"(current module: {slicer.util.selectedModule()!r})"
            )
        keep_on_module(start)
    _installed = True
