"""Slicer-runtime test for studio mode (Level 1 lockdown)."""

from __future__ import annotations

import os
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

import MyomaReviewLite as bundled_module  # noqa: E402
from MyomaReviewLite import MyomaReviewLiteWidget  # noqa: E402
from MyomaReviewLiteLib import studio_mode  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT

ENV = studio_mode.STUDIO_ENV


def _new_widget() -> MyomaReviewLiteWidget:
    parent = slicer.qMRMLWidget()
    parent.setLayout(qt.QVBoxLayout())
    parent.setMRMLScene(slicer.mrmlScene)
    widget = MyomaReviewLiteWidget(parent)
    widget._build_ui()
    return widget


def _main_window_actions():
    return [(str(a.objectName), a) for a in slicer.util.mainWindow().findChildren(qt.QAction)]


class _Stubs:
    def __enter__(self):
        self.dialog_calls = []
        self.select_calls = []
        self._real_dialog = qt.QFileDialog.getOpenFileName
        self._real_select = slicer.util.selectModule
        qt.QFileDialog.getOpenFileName = staticmethod(
            lambda *a, **k: self.dialog_calls.append(a) or ""
        )
        slicer.util.selectModule = lambda name: self.select_calls.append(name)
        return self

    def __exit__(self, *exc):
        qt.QFileDialog.getOpenFileName = self._real_dialog
        slicer.util.selectModule = self._real_select


def test_studio_on() -> None:
    os.environ[ENV] = "1"
    with _Stubs() as stubs:
        widget = _new_widget()
        try:
            widget.enter()
            widget.exit()
            main_window = slicer.util.mainWindow()
            assert not main_window.menuBar().visible, "menu bar still visible"
            console = main_window.findChild(qt.QDockWidget, "PythonConsoleDockWidget")
            assert console is None or not console.visible, "python console visible"

            names = [name for name, _ in _main_window_actions()]
            try:
                locked = studio_mode.locked_action_names(names)
                for name, action in _main_window_actions():
                    if name in locked:
                        assert not action.enabled, f"{name} still enabled"
                        assert len(action.shortcuts()) == 0, f"{name} still has shortcuts"
                assert console is not None
                assert not console.toggleViewAction().enabled, "console toggle enabled"
                console_actions = [
                    (name, action) for name, action in _main_window_actions()
                    if "Python" in str(action.text).replace("&", "")
                    or any(sc.toString() == "Ctrl+3" for sc in action.shortcuts())
                ]
                assert console_actions, "no Python console action found"
                for name, action in console_actions:
                    assert not action.enabled, f"console action {name!r} {action.text!r} enabled"
                    action.trigger()
                    slicer.app.processEvents()
                    assert not console.visible, f"{name!r} showed the console"
            except AssertionError:
                print("ACTION NAMES:", sorted(set(names)))
                raise
            assert not main_window.acceptDrops

            assert not widget.manifest_button.visible
            widget.choose_manifest()
            assert stubs.dialog_calls == [], stubs.dialog_calls

            slicer.app.processEvents()
            assert "MyomaReviewLite" in stubs.select_calls, stubs.select_calls
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)
            slicer.app.processEvents()  # drain timers cleanup() may have queued
            # Undo the lock so the next section sees an unlocked window.
            window = slicer.util.mainWindow()
            window.menuBar().setVisible(True)
            window.setAcceptDrops(True)


def test_studio_off() -> None:
    os.environ[ENV] = "0"
    with _Stubs() as stubs:
        widget = _new_widget()
        try:
            widget.enter()
            widget.exit()
            assert slicer.util.mainWindow().menuBar().visible, "menu bar should be back"
            slicer.app.processEvents()
            widget.choose_manifest()
            assert len(stubs.dialog_calls) == 1, stubs.dialog_calls
            assert "MyomaReviewLite" not in stubs.select_calls, stubs.select_calls
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


_saved_env = os.environ.get(ENV)
try:
    test_studio_on()
    test_studio_off()
except Exception:
    traceback.print_exc()
    sys.stderr.flush()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite studio mode: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
finally:
    if _saved_env is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = _saved_env
