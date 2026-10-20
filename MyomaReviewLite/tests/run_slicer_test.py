"""Run a *_slicer.py test unattended inside Slicer, logging to a file.

Usage:
    MYOMA_TEST_SCRIPT=tests/test_x_slicer.py MYOMA_TEST_LOG=out.log Slicer.exe --no-splash --python-script tests/run_slicer_test.py

stdout/stderr go to the log, and modal QMessageBox prompts are logged as DIALOG
lines and answered No so the run never blocks.
"""

import os
import runpy
import sys

log = open(os.environ["MYOMA_TEST_LOG"], "w", encoding="utf-8", buffering=1)
sys.stdout = log
sys.stderr = log

import qt  # noqa: E402


def _dialog_stub(*args, **kwargs):
    log.write(f"DIALOG {args[1:3]}\n")
    return qt.QMessageBox.No


for _name in ("question", "warning", "critical", "information"):
    setattr(qt.QMessageBox, _name, staticmethod(_dialog_stub))

runpy.run_path(os.environ["MYOMA_TEST_SCRIPT"], run_name="__main__")
