"""Tests for the studio.py command line. Docker and password prompts are injected fakes."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

import studio  # noqa: E402
from studio_lib.deployment import load_deployment  # noqa: E402

import portal_core  # noqa: E402  (studio_lib puts slicer-studio/portal on sys.path)


class FakeRun:
    def __init__(self, code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.code = code

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.code)


def prompts(*answers: str):
    queue = list(answers)
    return lambda prompt="": queue.pop(0)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.top = Path(self.temporary.name)
        self.run_fake = FakeRun()

    def module(self, requires=()) -> Path:
        mod = self.top / "mod"
        (mod / "HelloStudio").mkdir(parents=True, exist_ok=True)
        path = mod / "studio-module.json"
        path.write_text(json.dumps({
            "schema": 1, "name": "hello", "title": "Hello", "module_paths": ["HelloStudio"],
            "start_module": "HelloStudio", "uses_dataset": False,
            "workspace_requires": list(requires)}), encoding="utf-8")
        return path

    def cli(self, *argv, answers=()):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = studio.main([str(a) for a in argv], getpass=prompts(*answers), run=self.run_fake)
        return code, out.getvalue(), err.getvalue()

    def init(self, requires=()) -> Path:
        deploy = self.top / "deploy"
        code, _, err = self.cli("init", deploy, "--module", self.module(requires),
                                "--server-address", "192.168.0.201")
        self.assertEqual(code, 0, err)
        return deploy

    def users(self, deploy: Path):
        return portal_core.load_users(deploy / "portal" / "users.json")

    def test_init_creates_layout_and_refuses_non_empty(self) -> None:
        deploy = self.init()
        self.assertTrue((deploy / "studio.json").is_file())
        self.assertTrue((deploy / "users").is_dir())
        self.assertTrue((deploy / "generated").is_dir())
        self.assertEqual(len((deploy / "portal" / "secret.key").read_bytes()), 32)
        self.assertEqual(self.users(deploy), {})
        self.assertEqual(load_deployment(deploy).server_address, "192.168.0.201")
        code, _, err = self.cli("init", deploy, "--module", self.module(), "--server-address", "x")
        self.assertEqual(code, 2)
        self.assertIn("not empty", err)

    def test_init_validates_config_before_writing(self) -> None:
        deploy = self.top / "deploy"
        code, _, _ = self.cli("init", deploy, "--module", self.top / "nope.json",
                              "--server-address", "192.168.0.201")
        self.assertEqual(code, 2)
        self.assertFalse((deploy / "studio.json").exists())

    def test_init_refuses_inside_repo(self) -> None:
        target = STUDIO / "tests" / "_tmp_deploy"
        self.addCleanup(lambda: target.exists() and shutil.rmtree(target))
        code, _, err = self.cli("init", target, "--module", self.module(), "--server-address", "x.y")
        self.assertEqual(code, 2)
        self.assertIn("repository", err)
        self.assertFalse(target.exists())

    def test_add_user_requires_workspace_files(self) -> None:
        deploy = self.init(requires=["queue.json"])
        code, _, err = self.cli("add-user", deploy, "ali", answers=("password1", "password1"))
        self.assertEqual(code, 2)
        self.assertIn("queue.json", err)
        self.assertEqual(self.users(deploy), {})
        (deploy / "users" / "ali" / "queue.json").write_text("{}", encoding="utf-8")
        code, _, err = self.cli("add-user", deploy, "ali", answers=("password1", "password1"))
        self.assertEqual(code, 0, err)
        self.assertIn("ali", self.users(deploy))

    def test_add_user_password_rules(self) -> None:
        deploy = self.init()
        before = (deploy / "portal" / "users.json").read_bytes()
        code, _, _ = self.cli("add-user", deploy, "ali", answers=("password1", "password2"))
        self.assertEqual(code, 2)
        code, _, err = self.cli("add-user", deploy, "ali", answers=("short", "short"))
        self.assertEqual(code, 2)
        self.assertIn("at least 8", err)
        self.assertEqual((deploy / "portal" / "users.json").read_bytes(), before)

    def test_add_user_resets_existing_password(self) -> None:
        deploy = self.init()
        self.cli("add-user", deploy, "ali", answers=("password1", "password1"))
        first = self.users(deploy)["ali"].password_hash
        code, _, err = self.cli("add-user", deploy, "ali", answers=("password2", "password2"))
        self.assertEqual(code, 0, err)
        users = self.users(deploy)
        self.assertEqual(len(users), 1)
        self.assertNotEqual(users["ali"].password_hash, first)

    def test_add_user_rejects_reserved_and_bad_ids(self) -> None:
        deploy = self.init()
        for bad in ("admin", "Ali", "1x", "a b", "x" * 40, "ali\n"):
            code, _, _ = self.cli("add-user", deploy, bad, answers=("password1", "password1"))
            self.assertEqual(code, 2, bad)
        self.assertEqual(self.users(deploy), {})
        self.assertEqual(list((deploy / "users").iterdir()), [])

    def test_admin_needs_no_workspace_files(self) -> None:
        deploy = self.init(requires=["queue.json"])
        code, _, err = self.cli("add-user", deploy, "boss", "--admin", answers=("password1", "password1"))
        self.assertEqual(code, 0, err)
        self.assertTrue(self.users(deploy)["boss"].admin)
        self.assertFalse((deploy / "users" / "boss").exists())

    def test_remove_user_keeps_workspace(self) -> None:
        deploy = self.init()
        self.cli("add-user", deploy, "ali", answers=("password1", "password1"))
        code, out, _ = self.cli("remove-user", deploy, "ali")
        self.assertEqual(code, 0)
        self.assertIn(f'Run "studio.py up {deploy}" to stop their Slicer.', out)
        self.assertEqual(self.users(deploy), {})
        self.assertTrue((deploy / "users" / "ali").is_dir())
        code, _, _ = self.cli("remove-user", deploy, "ali")
        self.assertEqual(code, 2)

    def test_render_prints_paths(self) -> None:
        deploy = self.init()
        code, out, _ = self.cli("render", deploy)
        self.assertEqual(code, 0)
        self.assertIn("docker-compose.yml", out)
        self.assertIn("Caddyfile", out)

    def test_up_renders_then_calls_compose(self) -> None:
        deploy = self.init()
        (deploy / "generated").rmdir()
        code, _, err = self.cli("up", deploy)
        self.assertEqual(code, 0, err)
        compose = deploy / "generated" / "docker-compose.yml"
        self.assertTrue(compose.is_file())
        self.assertEqual(self.run_fake.calls,
                         [["docker", "compose", "-f", str(compose), "up", "-d", "--remove-orphans"]])

    def test_down_never_removes_volumes(self) -> None:
        deploy = self.init()
        self.cli("render", deploy)
        code, _, _ = self.cli("down", deploy)
        self.assertEqual(code, 0)
        argv = self.run_fake.calls[0]
        self.assertEqual(argv[-1], "down")
        self.assertNotIn("-v", argv)
        self.assertNotIn("--volumes", argv)

    def test_restart_and_docker_failure(self) -> None:
        deploy = self.init()
        self.cli("render", deploy)
        self.assertEqual(self.cli("restart", deploy)[0], 0)
        self.assertEqual(self.run_fake.calls[0][-1], "restart")
        self.run_fake.code = 3
        code, _, err = self.cli("restart", deploy)
        self.assertEqual(code, 1)
        self.assertIn("docker compose", err)

    def test_status_prints_progress_lines(self) -> None:
        deploy = self.init()
        self.cli("add-user", deploy, "ali", answers=("password1", "password1"))
        self.cli("add-user", deploy, "sara", answers=("password1", "password1"))
        self.cli("add-user", deploy, "boss", "--admin", answers=("password1", "password1"))
        (deploy / "users" / "ali" / "progress.json").write_text(json.dumps(
            {"total": 10, "done": 4, "in_progress": 2, "needs_attention": 1}), encoding="utf-8")
        self.cli("render", deploy)
        code, out, err = self.cli("status", deploy)
        self.assertEqual(code, 0, err)
        self.assertIn("ali  4/10 done, 2 in progress, 1 need attention", out)
        self.assertIn("sara  —", out)
        self.assertNotIn("boss", out)
        self.assertEqual(self.run_fake.calls[0][-1], "ps")

    def test_config_error_exits_2(self) -> None:
        code, _, err = self.cli("render", self.top / "missing")
        self.assertEqual(code, 2)
        self.assertIn("studio.json", err)


if __name__ == "__main__":
    unittest.main()
