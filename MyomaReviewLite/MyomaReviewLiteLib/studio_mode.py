"""Studio mode: lock Slicer's own chrome when the module is served to a browser.

A reader in the review studio must not reach the Python console, file dialogs,
Slicer's menus or other modules, because the AI mask could be opened before the
reveal. The mode is switched on by ``MYOMA_REVIEW_LITE_STUDIO=1`` and changes
nothing when it is off.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Mapping, Optional

STUDIO_ENV = "MYOMA_REVIEW_LITE_STUDIO"
LOCKED_ACTION_PATTERN = re.compile(r"^(File|Edit|View|Window|Help)\w*Action$")
KEPT_DOCK_NAME = "PanelDockWidget"
_GUARD_PROPERTY = "myomaStudioLocked"


def is_studio_mode(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when the variable is exactly ``"1"``."""
    source = os.environ if environ is None else environ
    return source.get(STUDIO_ENV) == "1"


def locked_action_names(names: Iterable[str]) -> list:
    """The action names Studio mode disables, in their original order."""
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
