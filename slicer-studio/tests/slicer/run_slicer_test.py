"""Run a *_slicer.py test unattended inside Slicer, logging to a file.

Usage:
    STUDIO_TEST_SCRIPT=tests/slicer/test_x_slicer.py STUDIO_TEST_LOG=out.log Slicer.exe --no-splash --python-script tests/slicer/run_slicer_test.py

stdout/stderr go to the log, and modal QMessageBox prompts are logged as DIALOG
lines and answered No so the run never blocks.
"""

import os
import runpy
import sys

log = open(os.environ["STUDIO_TEST_LOG"], "w", encoding="utf-8", buffering=1)
sys.stdout = log
sys.stderr = log

import qt  # noqa: E402


def _dialog_stub(*args, **kwargs):
    log.write(f"DIALOG {args[1:3]}\n")
    return qt.QMessageBox.No


for _name in ("question", "warning", "critical", "information"):
    setattr(qt.QMessageBox, _name, staticmethod(_dialog_stub))

if sys.platform.startswith("linux"):
    # Headless Xvfb has no window manager, so Slicer stays at its 1200x570
    # default and the slice views come out narrower than the FIGO card.
    # Windows runs the same tests in a maximised window.
    import slicer

    slicer.util.mainWindow().setGeometry(0, 0, 1920, 1080)
    slicer.app.processEvents()

try:
    runpy.run_path(os.environ["STUDIO_TEST_SCRIPT"], run_name="__main__")
except Exception:
    # A script that dies before its own exit call (say, on a failing import)
    # would otherwise leave Slicer open and the unattended run hanging.
    import traceback

    import slicer

    log.write(traceback.format_exc())
    log.flush()
    slicer.app.exit(1)
