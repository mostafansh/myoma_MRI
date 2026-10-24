"""Tests for the studio's generated compose file and Caddyfile."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio_lib.deployment import load_deployment  # noqa: E402
from studio_lib.manifest import ConfigError  # noqa: E402
from studio_lib.render import _q, render, render_caddyfile, render_compose  # noqa: E402

import portal_core  # noqa: E402  (studio_lib puts slicer-studio/portal on sys.path)

ADDR = "192.168.0.201"


def _write_fixture(top: Path, uses_dataset: bool = True, requires=(), dataset_name: str = "data",
                   env=None, users=("ali",)):
    """Build a module, a dataset and a deployment folder; return (deployment, module_dir)."""
    mod = top / "mod"
    (mod / "HelloStudio").mkdir(parents=True)
    (mod / "studio-module.json").write_text(json.dumps({
        "schema": 1, "name": "hello", "title": "Hello Studio", "module_paths": ["HelloStudio"],
        "start_module": "HelloStudio", "uses_dataset": uses_dataset,
        "env": {"A": "{workspace}/q.json"} if env is None else env,
        "workspace_requires": list(requires)}), encoding="utf-8")
    root = top / "deploy"
    (root / "portal").mkdir(parents=True)
    cfg = {"schema": 1, "module": str(mod / "studio-module.json"), "server_address": ADDR}
    if uses_dataset:
        (top / dataset_name).mkdir()
        cfg["dataset"] = str(top / dataset_name)
    (root / "studio.json").write_text(json.dumps(cfg), encoding="utf-8")
    table = {u: portal_core.User(u, u.title(), portal_core.hash_password("pw-" + u + "-12345"), False)
             for u in users}
    portal_core.save_users(root / "portal" / "users.json", table)
    return load_deployment(root), (mod / "HelloStudio").resolve()


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.top = Path(self.temporary.name)
        self.dep, self.mod_dir = _write_fixture(self.top)

    def test_compose_has_one_slicer_per_user_and_readonly_dataset(self) -> None:
        text = render_compose(self.dep, ["ali", "mostafa"])
        self.assertIn("slicer-ali:", text)
        self.assertIn("slicer-mostafa:", text)
        self.assertEqual(text.count(":/data:ro"), 3)
        self.assertEqual(len(re.findall(r'"[^"]*:443:443"', text)), 1)
        self.assertIn(f'      - "{ADDR}:443:443"', text)
        self.assertNotIn('- "443:443"', text)
        self.assertNotIn("6080:", text)
        self.assertIn("caddy:2.8", text)
        self.assertIn("image: slicer-studio-slicer", text)
        self.assertIn("image: slicer-studio-portal", text)
        self.assertIn("settings-ali:/home/user/.config", text)

    def test_compose_has_project_name_and_init_for_every_slicer(self) -> None:
        text = render_compose(self.dep, ["ali", "sara"])
        self.assertRegex(text, r"(?m)^name: studio-deploy$")
        for rid in ("ali", "sara"):
            block = re.search(rf"^  slicer-{rid}:\n((?:    .*\n)+)", text, re.M).group(1)
            self.assertIn("    init: true\n", block)

    def test_compose_rejects_bad_server_address(self) -> None:
        bad = dataclasses.replace(self.dep, server_address="1.2.3.4\n  x: y")
        with self.assertRaises(ConfigError):
            render_compose(bad, ["ali"])

    def test_caddyfile_guards_every_user_route(self) -> None:
        text = render_caddyfile(["ali", "mostafa"], ADDR)
        self.assertIn(chr(10) + ADDR + " {", text)
        self.assertIn("tls internal", text)
        self.assertNotIn("trusted_proxies", text)
        for rid in ("ali", "mostafa"):
            start = text.index(f"handle /{rid}/*")
            guard = text.index("forward_auth portal:9000", start)
            proxy = text.index(f"reverse_proxy slicer-{rid}:6080", start)
            self.assertLess(guard, proxy)

    def test_render_skips_admins_and_rejects_bad_ids(self) -> None:
        users = portal_core.load_users(self.dep.portal_dir / "users.json")
        users["boss"] = portal_core.User("boss", "Boss", portal_core.hash_password("pw-boss-12345"), True)
        portal_core.save_users(self.dep.portal_dir / "users.json", users)
        result = render(self.dep)
        self.assertEqual(result.user_ids, ["ali"])
        compose = (self.dep.generated_dir / "docker-compose.yml").read_text(encoding="utf-8")
        caddy = (self.dep.generated_dir / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("slicer-ali:", compose)
        self.assertNotIn("slicer-boss", compose)
        self.assertNotIn("slicer-boss", caddy)
        with self.assertRaises(ValueError):
            render_compose(self.dep, ["Bad Id"])
        with self.assertRaises(ValueError):
            render_compose(self.dep, ["admin"])
        with self.assertRaises(ValueError):
            render_caddyfile(["Bad Id"], "x")

    def test_module_mounts_and_env(self) -> None:
        text = render_compose(self.dep, ["ali"])
        self.assertIn(_q(str(self.mod_dir) + ":/modules/0:ro"), text)
        for line in ('SLICER_STUDIO: "1"', 'STUDIO_START_MODULE: "HelloStudio"',
                     'STUDIO_MODULE_PATHS: "/modules/0"', 'A: "/workspace/q.json"'):
            self.assertIn(line, text)
        self.assertIn('STUDIO_TITLE: "Hello Studio"', text)
        self.assertIn(_q(str(self.dep.users_dir.resolve() / "ali") + ":/workspace"), text)
        self.assertIn(_q(str(self.dep.users_dir.resolve()) + ":/workspaces:ro"), text)
        self.assertIn("--users-root /workspaces", text)
        self.assertIn("command: \"python /app/studio_portal.py serve --data-dir /portal ", text)

    def test_each_service_gets_only_its_own_studio_user(self) -> None:
        text = render_compose(self.dep, ["ali", "bob"])
        ali = text[text.index("  slicer-ali:"):text.index("  slicer-bob:")]
        bob = text[text.index("  slicer-bob:"):text.index("\nnetworks:\n")]
        self.assertIn('STUDIO_USER: "ali"', ali)
        self.assertNotIn('STUDIO_USER: "bob"', ali)
        self.assertIn('STUDIO_USER: "bob"', bob)
        self.assertNotIn('STUDIO_USER: "ali"', bob)

    def test_two_module_paths_are_colon_separated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dep, _ = _write_fixture(Path(tmp))
            mod = dep.manifest.path
            (mod.parent / "Second").mkdir()
            data = json.loads(mod.read_text(encoding="utf-8"))
            data["module_paths"] = ["HelloStudio", "Second"]
            mod.write_text(json.dumps(data), encoding="utf-8")
            text = render_compose(load_deployment(dep.root), ["ali"])
        self.assertIn('STUDIO_MODULE_PATHS: "/modules/0:/modules/1"', text)
        self.assertIn(":/modules/0:ro", text)
        self.assertIn(":/modules/1:ro", text)

    def test_id_with_trailing_newline_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            render_compose(self.dep, ["ali\n"])
        with self.assertRaises(ValueError):
            render_caddyfile(["ali\n"], ADDR)

    def test_non_ascii_env_values_are_escaped(self) -> None:
        bs = chr(92)
        raw = "a" + chr(0x85) + "b" + chr(0x7f) + "c"
        with tempfile.TemporaryDirectory() as tmp:
            dep, _ = _write_fixture(Path(tmp), env={"N": raw})
            text = render_compose(dep, ["ali"])
        self.assertIn('N: "a' + bs + 'u0085b' + bs + 'u007fc"', text)
        self.assertNotIn(chr(0x85), text)
        self.assertNotIn(chr(0x7f), text)

    def test_dataset_only_when_used(self) -> None:
        text = render_compose(self.dep, ["ali"])
        self.assertIn(":/data:ro", text)
        self.assertIn("--data-root /data", text)
        with tempfile.TemporaryDirectory() as tmp:
            dep, _ = _write_fixture(Path(tmp), uses_dataset=False)
            text = render_compose(dep, ["ali"])
        self.assertNotIn(":/data:ro", text)
        self.assertNotIn("--data-root", text)
        self.assertIn("--users-root /workspaces", text)

    def test_paths_with_spaces_and_dollar_are_quoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dep, _ = _write_fixture(Path(tmp), dataset_name="data $HOME set", env={"P": "{dataset}/$X"})
            text = render_compose(dep, ["ali"])
        self.assertRegex(text, r'"[^"\n]*/data \$\$HOME set:/data:ro"')
        self.assertNotRegex(text, r"[^$]\$HOME")
        self.assertIn('P: "/data/$$X"', text)

    def test_render_warns_for_unprepared_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dep, _ = _write_fixture(Path(tmp), requires=["queue.json"], users=("ali", "sara", "tom"))
            (dep.users_dir / "ali").mkdir(parents=True)
            (dep.users_dir / "ali" / "queue.json").write_text("[]", encoding="utf-8")
            (dep.users_dir / "sara").mkdir()
            result = render(dep)
            self.assertEqual(result.user_ids, ["ali", "sara", "tom"])
            self.assertEqual(len(result.warnings), 2)
            self.assertTrue((dep.generated_dir / "docker-compose.yml").is_file())
            self.assertTrue((dep.generated_dir / "Caddyfile").is_file())

    def test_render_writes_nothing_on_invalid_users_file(self) -> None:
        (self.dep.portal_dir / "users.json").write_text("{", encoding="utf-8")
        with self.assertRaises((ConfigError, portal_core.PortalError)):
            render(self.dep)
        self.assertFalse(self.dep.generated_dir.exists())

    @staticmethod
    def _service_networks(compose: str, service: str) -> set[str]:
        block = re.search(rf"^  {re.escape(service)}:\n((?:    .*\n)+)", compose, re.M).group(1)
        part = block.split("    networks:\n", 1)[1]
        return set(re.findall(r"^      - (\S+)$", part, re.M))

    def test_users_are_isolated_on_internal_networks(self) -> None:
        text = render_compose(self.dep, ["a", "b"])
        a = self._service_networks(text, "slicer-a")
        b = self._service_networks(text, "slicer-b")
        self.assertEqual(a, {"net-a"})
        self.assertEqual(b, {"net-b"})
        self.assertFalse(a & b)
        self.assertEqual(self._service_networks(text, "portal"), {"edge"})
        self.assertEqual(self._service_networks(text, "caddy"), {"edge", "net-a", "net-b"})
        self.assertNotIn("  studio:", text)
        for net in ("net-a", "net-b"):
            self.assertRegex(text, rf"(?m)^  {net}:\n    internal: true$")
        self.assertNotRegex(text, r"(?m)^  edge:\n    internal")

    def test_caddyfile_sets_default_sni_for_ip_addresses(self) -> None:
        text = render_caddyfile(["ali"], ADDR)
        self.assertTrue(text.startswith("{" + chr(10) + f"    default_sni {ADDR}" + chr(10) + "}" + chr(10)))

    def test_caddyfile_orders_auth_before_strip_inside_route(self) -> None:
        text = render_caddyfile(["ali"], ADDR)
        block = text[text.index("handle /ali/*"):]
        self.assertLess(block.index("route {"), block.index("forward_auth"))
        self.assertLess(block.index("forward_auth"), block.index("uri strip_prefix /ali"))


OLD_CADDY_TEMPLATE = """192.168.0.201 {{
    tls internal

    handle /{rid}/* {{
        forward_auth portal:9000 {{
            uri /auth
        }}
        uri strip_prefix /{rid}
        reverse_proxy slicer-{rid}:6080
    }}
}}
"""


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _walk_handlers(node):
    """Yield handler dicts depth-first in execution order."""
    if isinstance(node, dict):
        if "handler" in node:
            yield node
        for key in ("handle", "routes"):
            for child in node.get(key, []) or []:
                yield from _walk_handlers(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk_handlers(child)


def _auth_before_strip(adapted: dict, rid: str) -> bool:
    mine = []

    def find(node) -> None:
        if isinstance(node, dict):
            if any(f"/{rid}/*" in m.get("path", []) for m in node.get("match", []) if isinstance(m, dict)):
                mine.append(node)
                return
            for value in node.values():
                find(value)
        elif isinstance(node, list):
            for value in node:
                find(value)

    find(adapted["apps"]["http"]["servers"])
    if len(mine) != 1:
        return False
    handlers = list(_walk_handlers(mine[0]))
    auth = strip = None
    for index, h in enumerate(handlers):
        if h["handler"] == "reverse_proxy" and h.get("rewrite", {}).get("uri") == "/auth" and auth is None:
            if any(u.get("dial") == "portal:9000" for u in h.get("upstreams", [])):
                auth = index
        if h["handler"] == "rewrite" and h.get("strip_path_prefix") == f"/{rid}" and strip is None:
            strip = index
    return auth is not None and strip is not None and auth < strip


@unittest.skipUnless(_docker_available(), "docker is not available")
class DockerBackedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name)
        (self.out / "fx").mkdir()

    def _adapt(self) -> dict:
        result = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{self.out}:/etc/caddy", "caddy:2.8",
             "caddy", "adapt", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
            capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_old_template_is_detected_as_wrong(self) -> None:
        (self.out / "Caddyfile").write_text(OLD_CADDY_TEMPLATE.format(rid="ali"), encoding="utf-8")
        self.assertFalse(_auth_before_strip(self._adapt(), "ali"))

    def test_real_caddy_authenticates_before_stripping_prefix(self) -> None:
        ids = ["ali", "sara"]
        (self.out / "Caddyfile").write_text(render_caddyfile(ids, ADDR), encoding="utf-8")
        adapted = self._adapt()
        for rid in ids:
            self.assertTrue(_auth_before_strip(adapted, rid), rid)

    def test_compose_config_is_valid(self) -> None:
        dep, _ = _write_fixture(self.out / "fx", users=("ali", "sara"))
        compose = render_compose(dep, ["ali", "sara"])
        (self.out / "docker-compose.yml").write_text(compose, encoding="utf-8")
        result = subprocess.run(
            ["docker", "compose", "-f", str(self.out / "docker-compose.yml"), "config"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        resolved = subprocess.run(
            ["docker", "compose", "-f", str(self.out / "docker-compose.yml"), "config", "--format", "json"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        services = json.loads(resolved.stdout)["services"]
        for rid in ("ali", "sara"):
            service = services[f"slicer-{rid}"]
            for key in ("SLICER_STUDIO", "STUDIO_USER", "STUDIO_START_MODULE", "STUDIO_MODULE_PATHS"):
                self.assertIn(key, service["environment"], f"{rid}: {key}")
            self.assertEqual(service["environment"]["STUDIO_USER"], rid)
            mounts = {v["target"]: v for v in service["volumes"]}
            self.assertIn("/modules/0", mounts)
            self.assertTrue(mounts["/modules/0"].get("read_only"), "module mount must be read-only")
            self.assertIn("/workspace", mounts)
            self.assertIn("/home/user/.config", mounts)
            self.assertEqual(mounts["/home/user/.config"]["type"], "volume")
        command = services["portal"]["command"]
        command = command if isinstance(command, str) else " ".join(command)
        self.assertTrue(command.startswith("python /app/studio_portal.py serve"), command)


if __name__ == "__main__":
    unittest.main()
