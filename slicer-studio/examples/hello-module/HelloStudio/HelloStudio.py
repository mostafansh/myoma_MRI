"""HelloStudio: a tiny scripted module, the template for new studio modules."""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import qt  # type: ignore
    from slicer.ScriptedLoadableModule import (  # type: ignore
        ScriptedLoadableModule,
        ScriptedLoadableModuleLogic,
        ScriptedLoadableModuleWidget,
    )
except (ImportError, ModuleNotFoundError):  # pragma: no cover - outside Slicer
    class ScriptedLoadableModule:  # type: ignore[no-redef]
        def __init__(self, parent=None):
            self.parent = parent

    class ScriptedLoadableModuleLogic:  # type: ignore[no-redef]
        pass

    class ScriptedLoadableModuleWidget:  # type: ignore[no-redef]
        def __init__(self, parent=None):
            self.parent = parent

TOTAL = 10


def progress_for(count: int) -> dict:
    """The portal's progress.json shape: four non-negative ints."""
    return {"total": TOTAL, "done": max(0, min(count, TOTAL)),
            "in_progress": 0, "needs_attention": 0}


def _atomic_write_json(path: Path, data: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temp, path)


def workspace_path() -> Path:
    return Path(os.environ.get("STUDIO_WORKSPACE", "/workspace"))


def user_label(workspace: Path) -> str:
    return os.environ.get("STUDIO_USER", "") or workspace.name


class HelloStudio(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        parent.title = "Hello Studio"
        parent.categories = ["Examples"]
        parent.contributors = ["Slicer Studio"]
        parent.helpText = "Sample module: counts clicks in the user's workspace."


class HelloStudioLogic(ScriptedLoadableModuleLogic):
    def count_once(self, workspace: Path) -> int:
        """Increment hello.json and refresh progress.json; return the new count."""
        counter = workspace / "hello.json"
        try:
            count = int(json.loads(counter.read_text(encoding="utf-8")).get("count", 0))
        except (OSError, ValueError, AttributeError, TypeError):
            count = 0
        count += 1
        _atomic_write_json(counter, {"count": count})
        _atomic_write_json(workspace / "progress.json", progress_for(count))
        return count


class HelloStudioWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = HelloStudioLogic()
        workspace = workspace_path()
        self.layout.addWidget(qt.QLabel(f"User: {user_label(workspace)}"))
        self.layout.addWidget(qt.QLabel(f"User workspace: {workspace}"))
        self.countLabel = qt.QLabel("")
        self.layout.addWidget(self.countLabel)
        button = qt.QPushButton("Count")
        button.connect("clicked()", self.onCount)
        self.layout.addWidget(button)
        self.layout.addStretch(1)

    def onCount(self):
        try:
            self.countLabel.setText(f"Count: {self.logic.count_once(workspace_path())}")
        except OSError as exc:
            self.countLabel.setText(f"Could not save: {exc}")
