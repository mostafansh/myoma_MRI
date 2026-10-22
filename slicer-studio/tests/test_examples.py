"""Tests for the bundled sample module."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

STUDIO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO_ROOT))

from studio_lib.manifest import expand_env, load_manifest  # noqa: E402

HELLO = STUDIO_ROOT / "examples" / "hello-module"


def load_hello():
    spec = importlib.util.spec_from_file_location(
        "hello_studio_under_test", HELLO / "HelloStudio" / "HelloStudio.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HelloModuleTests(unittest.TestCase):
    def test_manifest_loads(self) -> None:
        manifest = load_manifest(HELLO / "studio-module.json")
        self.assertEqual(manifest.name, "hello")
        self.assertEqual(manifest.title, "Hello Studio")
        self.assertEqual(manifest.start_module, "HelloStudio")
        self.assertFalse(manifest.uses_dataset)
        self.assertEqual(expand_env(manifest), {"STUDIO_WORKSPACE": "/workspace"})

    def test_progress_for_caps_at_total(self) -> None:
        hello = load_hello()
        self.assertEqual(hello.progress_for(12)["done"], 10)
        self.assertEqual(
            hello.progress_for(3),
            {"total": 10, "done": 3, "in_progress": 0, "needs_attention": 0})

    def test_count_once_persists_both_files(self) -> None:
        hello = load_hello()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            self.assertEqual(hello.HelloStudioLogic().count_once(workspace), 1)
            self.assertEqual(hello.HelloStudioLogic().count_once(workspace), 2)
            self.assertEqual(json.loads((workspace / "hello.json").read_text()), {"count": 2})
            self.assertEqual(json.loads((workspace / "progress.json").read_text())["done"], 2)
            self.assertEqual([p.name for p in workspace.iterdir() if p.name.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
