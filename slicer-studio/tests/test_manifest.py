"""Tests for the module manifest loader."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio_lib.manifest import ConfigError, expand_env, load_manifest  # noqa: E402


def base() -> dict:
    return {"schema": 1, "name": "hello", "title": "Hello Studio",
            "module_paths": ["HelloStudio"], "start_module": "HelloStudio"}


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "mod"
        (self.root / "HelloStudio").mkdir(parents=True)
        self.path = self.root / "studio-module.json"

    def write(self, data: dict) -> Path:
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return self.path

    def load(self, **changes):
        data = base()
        data.update(changes)
        return load_manifest(self.write(data))

    def test_valid_manifest_loads(self):
        (self.root / "Other").mkdir()
        m = self.load(module_paths=["HelloStudio", "Other"], uses_dataset=True,
                      env={"A": "x"}, workspace_requires=["queue.json"])
        self.assertEqual(m.module_dirs, ((self.root / "HelloStudio").resolve(), (self.root / "Other").resolve()))
        self.assertTrue(m.uses_dataset)
        self.assertEqual(m.env, {"A": "x"})
        self.assertEqual(m.workspace_requires, ("queue.json",))
        self.assertEqual((m.name, m.title, m.start_module), ("hello", "Hello Studio", "HelloStudio"))
        d = self.load()
        self.assertFalse(d.uses_dataset)
        self.assertEqual(d.env, {})
        self.assertEqual(d.workspace_requires, ())

    def test_unknown_key_is_an_error(self):
        data = base()
        data["modul_paths"] = ["x"]
        with self.assertRaisesRegex(ConfigError, "modul_paths"):
            load_manifest(self.write(data))

    def test_required_keys(self):
        for key in ("schema", "name", "title", "module_paths", "start_module"):
            data = base()
            del data[key]
            with self.assertRaises(ConfigError) as cm:
                load_manifest(self.write(data))
            self.assertIn(key, str(cm.exception))
            self.assertIn("studio-module.json", str(cm.exception))

    def test_name_and_types(self):
        for changes, key in [({"name": "Bad Name"}, "name"), ({"module_paths": "x"}, "module_paths"),
                             ({"module_paths": []}, "module_paths"), ({"uses_dataset": "yes"}, "uses_dataset"),
                             ({"env": {"A": 3}}, "env"), ({"schema": 2}, "schema"),
                             ({"schema": True}, "schema"), ({"env": []}, "env")]:
            with self.assertRaisesRegex(ConfigError, key, msg=str(changes)):
                self.load(**changes)

    def _link(self, link: Path, target: Path) -> bool:
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            pass
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            return r.returncode == 0 and link.exists()
        return False

    def test_module_path_escape_is_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        for bad in ("../outside", str(outside)):
            with self.assertRaisesRegex(ConfigError, "module_paths"):
                self.load(module_paths=[bad])
        link = self.root / "link"
        if not self._link(link, outside):
            self.skipTest("cannot create a symlink or junction")
        with self.assertRaisesRegex(ConfigError, "module_paths"):
            self.load(module_paths=["link"])

    def test_module_path_must_be_a_folder(self):
        (self.root / "afile").write_text("x", encoding="utf-8")
        for bad in ("missing", "afile"):
            with self.assertRaisesRegex(ConfigError, "module_paths"):
                self.load(module_paths=[bad])

    def test_env_placeholders(self):
        m = self.load(uses_dataset=True, env={"A": "{workspace}/q.json", "B": "{dataset}"})
        self.assertEqual(expand_env(m), {"A": "/workspace/q.json", "B": "/data"})
        with self.assertRaisesRegex(ConfigError, "env.B"):
            self.load(uses_dataset=False, env={"B": "{dataset}"})
        with self.assertRaisesRegex(ConfigError, "env.C"):
            self.load(env={"C": "{other}"})
        with self.assertRaisesRegex(ConfigError, "env.D"):
            self.load(env={"D": "{workspace"})
        self.assertEqual(expand_env(self.load(env={"E": "{{x}}"})), {"E": "{x}"})

    def test_workspace_requires_are_relative_names(self):
        for bad in ("../x", "/abs", "", "a/../b", "C:" + "/x", "a\\b"):
            with self.assertRaisesRegex(ConfigError, "workspace_requires", msg=bad):
                self.load(workspace_requires=[bad])


if __name__ == "__main__":
    unittest.main()
