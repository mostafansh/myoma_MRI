"""Tests for the deployment config loader."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio_lib.deployment import (check_address, load_deployment, missing_workspace_files,  # noqa: E402
                                   project_name)
from studio_lib.manifest import ConfigError  # noqa: E402


class DeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.top = Path(self.tmp.name)
        self.mod = self.top / "mod"
        (self.mod / "HelloStudio").mkdir(parents=True)
        self.root = self.top / "deploy"
        self.root.mkdir()
        (self.top / "data").mkdir()

    def manifest(self, uses_dataset: bool, requires=()) -> None:
        (self.mod / "studio-module.json").write_text(json.dumps({
            "schema": 1, "name": "hello", "title": "Hello", "module_paths": ["HelloStudio"],
            "start_module": "HelloStudio", "uses_dataset": uses_dataset,
            "workspace_requires": list(requires)}), encoding="utf-8")

    def config(self, **data) -> None:
        cfg = {"schema": 1, "module": str(self.mod / "studio-module.json"), "server_address": "192.168.0.201"}
        cfg.update(data)
        (self.root / "studio.json").write_text(json.dumps(cfg), encoding="utf-8")

    def test_deployment_loads_relative_and_absolute_paths(self):
        self.manifest(True)
        self.config(module="../mod/studio-module.json", dataset=str(self.top / "data"))
        d = load_deployment(self.root)
        self.assertEqual(d.manifest.name, "hello")
        self.assertEqual(d.dataset, self.top / "data")
        self.assertEqual(d.server_address, "192.168.0.201")
        self.config(dataset="../data")
        self.assertEqual(load_deployment(self.root).dataset.resolve(), (self.top / "data").resolve())
        self.assertEqual(d.users_dir, self.root / "users")
        self.assertEqual(d.portal_dir, self.root / "portal")
        self.assertEqual(d.generated_dir, self.root / "generated")
        self.assertEqual(d.config_path, self.root / "studio.json")

    def test_dataset_required_exactly_when_used(self):
        self.manifest(True)
        self.config()
        with self.assertRaisesRegex(ConfigError, "dataset"):
            load_deployment(self.root)
        self.manifest(False)
        self.config(dataset=str(self.top / "data"))
        with self.assertRaisesRegex(ConfigError, "dataset"):
            load_deployment(self.root)
        self.config()
        self.assertIsNone(load_deployment(self.root).dataset)

    def test_unknown_and_missing_keys(self):
        self.manifest(False)
        self.config(extra=1)
        with self.assertRaisesRegex(ConfigError, "extra"):
            load_deployment(self.root)
        with self.assertRaisesRegex(ConfigError, "studio.json"):
            load_deployment(self.top / "nowhere")

    def test_bad_server_address(self):
        for good in ("192.168.0.201", "studio.example.org", "[::1]", "localhost:8443"):
            check_address(good)
        for bad in ("", "1.2.3.4\n  x: y", "1.2.3.4\n", "a b", "a;b", None):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                check_address(bad)
        self.manifest(False)
        self.config(server_address="1.2.3.4\n  x: y")
        with self.assertRaisesRegex(ConfigError, "server_address"):
            load_deployment(self.root)

    def test_project_name_is_sanitised(self):
        self.assertEqual(project_name(Path("x") / "My Pilot (2026)"), "studio-my-pilot-2026")
        self.assertEqual(project_name(Path("x") / ("a" * 100)), ("studio-" + "a" * 100)[:63])
        self.assertEqual(project_name(Path("x") / "---"), "studio")

    def test_missing_workspace_files(self):
        self.manifest(False, ["queue.json", "b.json"])
        self.config()
        d = load_deployment(self.root)
        (d.users_dir / "ali").mkdir(parents=True)
        (d.users_dir / "ali" / "queue.json").write_text("{}", encoding="utf-8")
        self.assertEqual(missing_workspace_files(d, "ali"), ["b.json"])
        self.assertEqual(missing_workspace_files(d, "sara"), ["queue.json", "b.json"])


if __name__ == "__main__":
    unittest.main()
