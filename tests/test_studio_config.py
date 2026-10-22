"""Tests for the studio's generated compose file and Caddyfile."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "studio"))
sys.path.insert(0, str(PROJECT_ROOT / "slicer-studio" / "portal"))

import portal_core  # noqa: E402
import studio_config  # noqa: E402


class StudioConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_compose_has_one_slicer_per_reader_and_readonly_dataset(self) -> None:
        text = studio_config.render_compose(["ali", "mostafa"], "/srv/data", "/srv/readers", "/srv/portal", "192.168.0.201")
        self.assertIn("slicer-ali:", text)
        self.assertIn("slicer-mostafa:", text)
        self.assertEqual(text.count(":/data:ro"), 3)
        self.assertEqual(len(re.findall(r'"[^"]*:443:443"', text)), 1)
        self.assertIn('      - "192.168.0.201:443:443"', text)
        self.assertNotIn('- "443:443"', text)
        self.assertNotIn("6080:", text)
        self.assertIn("caddy:2.8", text)

    def test_compose_has_project_name_and_init_for_every_slicer(self) -> None:
        text = studio_config.render_compose(["ali", "sara"], "/d", "/r", "/p", "192.168.0.201")
        self.assertRegex(text, r"(?m)^name: myoma-studio$")
        for rid in ("ali", "sara"):
            block = re.search(rf"^  slicer-{rid}:\n((?:    .*\n)+)", text, re.M).group(1)
            self.assertIn("    init: true\n", block)

    def test_compose_rejects_bad_server_address(self) -> None:
        with self.assertRaises(ValueError):
            studio_config.render_compose(["ali"], "/d", "/r", "/p", "1.2.3.4\n  x: y")

    def test_caddyfile_guards_every_reader_route(self) -> None:
        text = studio_config.render_caddyfile(["ali", "mostafa"], "192.168.0.201")
        self.assertIn(chr(10) + "192.168.0.201 {", text)
        self.assertIn("tls internal", text)
        self.assertNotIn("trusted_proxies", text)
        for rid in ("ali", "mostafa"):
            start = text.index(f"handle /{rid}/*")
            guard = text.index("forward_auth portal:9000", start)
            proxy = text.index(f"reverse_proxy slicer-{rid}:6080", start)
            self.assertLess(guard, proxy)

    def test_render_skips_admins_and_rejects_bad_ids(self) -> None:
        portal_data = self.root / "portal-data"
        portal_data.mkdir()
        readers = {
            "ali": portal_core.User("ali", "Ali", portal_core.hash_password("pw-ali-12345"), False),
            "boss": portal_core.User("boss", "Boss", portal_core.hash_password("pw-boss-12345"), True),
        }
        portal_core.save_users(portal_data / "readers.json", readers)
        config = self.root / "review-config.json"
        config.write_text(json.dumps({"dataset_root": "/srv/data"}), encoding="utf-8")
        out = self.root / "out"
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "studio" / "studio_config.py"), "render",
             "--server-address", "192.168.0.201", "--review-config", str(config),
             "--portal-data", str(portal_data), "--readers-root", str(self.root / "readers"),
             "--out", str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        compose = (out / "docker-compose.yml").read_text(encoding="utf-8")
        caddy = (out / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("slicer-ali:", compose)
        self.assertNotIn("slicer-boss", compose)
        self.assertNotIn("slicer-boss", caddy)
        with self.assertRaises(ValueError):
            studio_config.render_compose(["Bad Id"], "/d", "/r", "/p", "192.168.0.201")
        with self.assertRaises(ValueError):
            studio_config.render_compose(["admin"], "/d", "/r", "/p", "192.168.0.201")
        with self.assertRaises(ValueError):
            studio_config.render_caddyfile(["Bad Id"], "x")

    def test_render_reads_dataset_root_from_review_config(self) -> None:
        config = self.root / "review-config.json"
        config.write_text(json.dumps({"dataset_root": "/srv/data"}), encoding="utf-8")
        root = studio_config.read_dataset_root(config)
        text = studio_config.render_compose(["ali"], root, "/r", "/p", "192.168.0.201")
        self.assertIn('"/srv/data:/data:ro"', text)

    def test_relative_and_drive_dataset_roots(self) -> None:
        drive = "M"
        config = self.root / "review-config.json"
        config.write_text(json.dumps({"dataset_root": "sub\\data"}), encoding="utf-8")
        self.assertEqual(studio_config.read_dataset_root(config), (self.root / "sub" / "data").as_posix())
        config.write_text(json.dumps({"dataset_root": drive + ":\\projects\\x y"}), encoding="utf-8")
        self.assertEqual(studio_config.read_dataset_root(config), drive + ":/projects/x y")

    @staticmethod
    def _service_networks(compose: str, service: str) -> set[str]:
        block = re.search(rf"^  {re.escape(service)}:\n((?:    .*\n)+)", compose, re.M).group(1)
        part = block.split("    networks:\n", 1)[1]
        return set(re.findall(r"^      - (\S+)$", part, re.M))

    def test_readers_are_isolated_on_internal_networks(self) -> None:
        text = studio_config.render_compose(["a", "b"], "/d", "/r", "/p", "192.168.0.201")
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
        text = studio_config.render_caddyfile(["ali"], "192.168.0.201")
        self.assertTrue(text.startswith("{" + chr(10) + "    default_sni 192.168.0.201" + chr(10) + "}" + chr(10)))

    def test_caddyfile_orders_auth_before_strip_inside_route(self) -> None:
        text = studio_config.render_caddyfile(["ali"], "192.168.0.201")
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
        (self.out / "Caddyfile").write_text(studio_config.render_caddyfile(ids, "192.168.0.201"), encoding="utf-8")
        adapted = self._adapt()
        for rid in ids:
            self.assertTrue(_auth_before_strip(adapted, rid), rid)

    def test_compose_config_is_valid(self) -> None:
        compose = studio_config.render_compose(["ali", "sara"], "/srv/data", "/srv/readers", "/srv/portal", "192.168.0.201")
        (self.out / "docker-compose.yml").write_text(compose, encoding="utf-8")
        result = subprocess.run(
            ["docker", "compose", "-f", str(self.out / "docker-compose.yml"), "config"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
