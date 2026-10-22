"""Slicer-runtime test for the studio lockdown, against Slicer's bare main window."""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from pathlib import Path

import qt
import slicer

_HERE = Path(__file__).resolve()
for candidate in (_HERE.parents[2] / "slicer" / "lockdown", Path("/opt/studio/lockdown")):
    if (candidate / "studio_lockdown.py").is_file():
        sys.path.insert(0, str(candidate))
        break

import studio_lockdown  # noqa: E402

ENV = studio_lockdown.STUDIO_ENV


def _main_window_actions():
    return [(str(a.objectName), a) for a in slicer.util.mainWindow().findChildren(qt.QAction)]


def test_lock_down() -> None:
    main_window = slicer.util.mainWindow()
    studio_lockdown.lock_down(main_window)
    studio_lockdown.lock_down(main_window)  # repeat-safe
    assert not main_window.menuBar().visible, "menu bar still visible"
    assert not main_window.statusBar().visible, "status bar still visible"
    console = main_window.findChild(qt.QDockWidget, "PythonConsoleDockWidget")
    assert console is not None and not console.visible, "python console visible"

    names = [name for name, _ in _main_window_actions()]
    try:
        locked = studio_lockdown.locked_action_names(names)
        assert locked, "no locked actions found"
        for name, action in _main_window_actions():
            if name in locked:
                assert not action.enabled, f"{name} still enabled"
                assert len(action.shortcuts()) == 0, f"{name} still has shortcuts"
        assert not console.toggleViewAction().enabled, "console toggle enabled"
        console_actions = [
            (name, action) for name, action in _main_window_actions()
            if "Python" in str(action.text).replace("&", "")
            or any(sc.toString() == "Ctrl+3" for sc in action.shortcuts())
        ]
        assert console_actions, "no Python console action found"
        for name, action in console_actions:
            assert not action.enabled, f"console action {name!r} {action.text!r} enabled"
            for attempt in range(2):
                action.trigger()
                slicer.app.processEvents()
                assert not console.visible, f"{name!r} showed the console (trigger {attempt + 1})"
        for attempt in range(2):
            console.show()
            slicer.app.processEvents()
            assert not console.visible, f"console.show() stuck (call {attempt + 1})"
    except AssertionError:
        print("ACTION NAMES:", sorted(set(names)))
        raise
    assert not main_window.acceptDrops


def test_keep_on_module() -> None:
    studio_lockdown.keep_on_module("Data")
    studio_lockdown.keep_on_module("Data")  # idempotent
    slicer.util.selectModule("Data")
    slicer.app.processEvents()
    calls = []
    real = slicer.util.selectModule
    slicer.util.selectModule = lambda name: (calls.append(name), real(name))[1]
    try:
        real("Welcome")
        slicer.app.processEvents()
        slicer.app.processEvents()
    finally:
        slicer.util.selectModule = real
    assert slicer.util.selectedModule() == "Data", slicer.util.selectedModule()
    assert calls == ["Data"], f"re-selected {calls}, expected exactly one (connected twice?)"


def test_install() -> None:
    # Runs first: nothing else may have wired the keep-on-module hook yet, so
    # this proves install() itself does it.
    assert not studio_lockdown.is_installed()
    assert not studio_lockdown._keep["connected"]
    env = {ENV: "1", studio_lockdown.START_ENV: "Data"}
    studio_lockdown.install(env)
    assert studio_lockdown.is_installed()
    assert studio_lockdown._keep["connected"], "install() did not wire keep_on_module"
    assert not slicer.util.mainWindow().menuBar().visible
    # show_when_locked.py relies on isHidden(): isVisible() is false before the window is shown.
    assert slicer.util.mainWindow().menuBar().isHidden(), "install() did not explicitly hide the menu bar"
    assert slicer.util.selectedModule() == "Data"
    slicer.util.selectModule("Welcome")
    slicer.app.processEvents()
    slicer.app.processEvents()
    assert slicer.util.selectedModule() == "Data"


def test_install_unknown_module_raises() -> None:
    env = {ENV: "1", studio_lockdown.START_ENV: "NoSuchModule"}
    try:
        studio_lockdown.install(env)
    except RuntimeError as error:
        assert "NoSuchModule" in str(error), str(error)
    else:
        raise AssertionError("install() accepted an unknown start module")
    assert not studio_lockdown.is_installed()
    assert not studio_lockdown._keep["connected"]


try:
    test_install_unknown_module_raises()
    test_install()
    test_lock_down()
    test_keep_on_module()
except Exception:
    traceback.print_exc()
    sys.stderr.flush()
    slicer.app.exit(1)
else:
    sys.stderr.write("studio lockdown: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
