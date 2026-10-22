"""Slicer-runtime test for Myoma's own SLICER_STUDIO behaviour and progress.json."""

from __future__ import annotations

import json
import os
import sys
import tempfile
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
from MyomaReviewLite import LiteRectangleDrawTool, MyomaReviewLiteWidget  # noqa: E402
from MyomaReviewLiteLib import DatasetManifest  # noqa: E402
from MyomaReviewLiteLib.queue import progress_counts  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT

ENV = "SLICER_STUDIO"


def _new_widget() -> MyomaReviewLiteWidget:
    parent = slicer.qMRMLWidget()
    parent.setLayout(qt.QVBoxLayout())
    parent.setMRMLScene(slicer.mrmlScene)
    widget = MyomaReviewLiteWidget(parent)
    widget._build_ui()
    return widget


class _DialogStub:
    def __enter__(self):
        self.calls = []
        self._real = qt.QFileDialog.getOpenFileName
        qt.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: self.calls.append(a) or "")
        return self

    def __exit__(self, *exc):
        qt.QFileDialog.getOpenFileName = self._real


def test_studio_on() -> None:
    os.environ[ENV] = "1"
    window = slicer.util.mainWindow()
    window.menuBar().setVisible(True)
    with _DialogStub() as stub:
        widget = _new_widget()
        try:
            widget.enter()
            slicer.app.processEvents()
            widget.exit()
            assert widget.manifest_button.isHidden()
            widget.choose_manifest()
            assert stub.calls == [], stub.calls
            assert not window.menuBar().visible, "menu bar came back"
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)
            slicer.app.processEvents()
            window.menuBar().setVisible(True)


def test_studio_off() -> None:
    os.environ.pop(ENV, None)
    window = slicer.util.mainWindow()
    with _DialogStub() as stub:
        widget = _new_widget()
        try:
            widget.enter()
            slicer.app.processEvents()  # let the deferred chrome hide run
            assert not window.menuBar().visible, "enter() should hide the chrome"
            widget.exit()
            assert not widget.manifest_button.isHidden()
            widget.choose_manifest()
            assert len(stub.calls) == 1, stub.calls
            assert window.menuBar().visible, "chrome not restored after exit()"
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


def _source_manifest() -> DatasetManifest:
    value = (
        os.environ.get("MYOMA_REVIEW_LITE_TEST_QUEUE", "").strip()
        or os.environ.get("MYOMA_REVIEW_LITE_CASE", "").strip()
    )
    if not value:
        raise RuntimeError("set MYOMA_REVIEW_LITE_TEST_QUEUE to a dataset queue")
    data_root = os.environ.get("MYOMA_REVIEW_LITE_DATA_ROOT", "").strip()
    return DatasetManifest.from_file(value, source_root=data_root or None)


def test_progress_json_after_save() -> None:
    os.environ.pop(ENV, None)
    slicer.mrmlScene.Clear(0)
    cases = _source_manifest().cases[:2]
    with tempfile.TemporaryDirectory() as temporary:
        reader = Path(temporary) / "reader"
        reader.mkdir()
        queue_path = reader / "queue.json"
        queue_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "manifest_type": "dataset",
                    "study_id": "HOOKS",
                    "reader_id": "hooks",
                    "output_root": ".",
                    "cases": [
                        {
                            "case_id": case.case_id,
                            "volumes": {n: str(p) for n, p in case.volumes.items()},
                            "ai_mask": str(case.ai_mask),
                        }
                        for case in cases
                    ],
                }
            ),
            encoding="utf-8",
        )
        widget = _new_widget()
        widget.draw_tool = LiteRectangleDrawTool(widget)
        try:
            widget.open_case(queue_path)
            logic = widget.logic
            progress = logic.dataset_manifest.output_root / "progress.json"
            assert not progress.exists()
            logic.state.comment = "hooks"
            logic.save_draft()
            assert progress.is_file(), "progress.json not written after save_draft"
            written = json.loads(progress.read_text(encoding="utf-8"))
            assert written == progress_counts(logic.refresh_queue()), written
            assert written["total"] == len(cases) and written["in_progress"] == 1, written

            # A failing progress write must never fail the save.
            progress.unlink()
            real_replace = os.replace
            os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
            try:
                logic._write_progress()
            finally:
                os.replace = real_replace
            assert not progress.exists()
        finally:
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


def test_progress_json_after_reopen() -> None:
    """Completing a case counts it done; reopening it must stop counting it."""
    os.environ.pop(ENV, None)
    slicer.mrmlScene.Clear(0)
    case = _source_manifest().cases[0]
    original_question = qt.QMessageBox.question
    qt.QMessageBox.question = staticmethod(lambda *_a, **_k: qt.QMessageBox.Yes)
    with tempfile.TemporaryDirectory() as temporary:
        reader = Path(temporary) / "reader"
        reader.mkdir()
        queue_path = reader / "queue.json"
        queue_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "manifest_type": "dataset",
                    "study_id": "HOOKS",
                    "reader_id": "hooks",
                    "output_root": ".",
                    "cases": [
                        {
                            "case_id": case.case_id,
                            "volumes": {n: str(p) for n, p in case.volumes.items()},
                            "ai_mask": str(case.ai_mask),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        widget = _new_widget()
        widget.draw_tool = LiteRectangleDrawTool(widget)
        try:
            widget.open_case(queue_path)
            progress = widget.logic.dataset_manifest.output_root / "progress.json"
            first = widget.logic.state.add_lesion((0.0, 0.0, 0.0), (20.0, 20.0, 20.0))
            widget.rebuild_roi_nodes()
            widget._on_card_figo(first, {"kind": "standard", "type": 3})
            widget.on_reveal_ai()
            widget.on_complete()
            done = json.loads(progress.read_text(encoding="utf-8"))
            assert done["done"] == 1 and done["in_progress"] == 0, done

            widget.open_case(queue_path, case.case_id)
            assert widget.logic.read_only and not widget.reopen_button.isHidden(), "completed case did not open"
            widget.on_reopen_case()
            assert not widget.logic.read_only, "the case did not reopen"
            reopened = json.loads(progress.read_text(encoding="utf-8"))
            assert reopened["done"] == 0 and reopened["in_progress"] == 1, reopened
            assert reopened == progress_counts(widget.logic.refresh_queue()), reopened
        finally:
            qt.QMessageBox.question = original_question
            widget.cleanup()
            slicer.mrmlScene.Clear(0)


_saved_env = os.environ.get(ENV)
try:
    test_studio_on()
    test_studio_off()
    test_progress_json_after_save()
    test_progress_json_after_reopen()
except Exception:
    traceback.print_exc()
    sys.stderr.flush()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite studio hooks: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
finally:
    if _saved_env is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = _saved_env
