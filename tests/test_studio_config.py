"""Tests for the studio's generated compose file and Caddyfile."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "studio"))
sys.path.insert(0, str(PROJECT_ROOT / "studio" / "portal"))

import portal_core  # noqa: E402
import studio_config  # noqa: E402


class StudioConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_compose_has_one_slicer_per_reader_and_readonly_dataset(self) -> None:
        text = studio_config.render_compose(["ali", "mostafa"], "/srv/data", "/srv/readers", "/srv/portal")
        self.assertIn("slicer-ali:", text)
        self.assertIn("slicer-mostafa:", text)
        self.assertEqual(text.count(":/data:ro"), 3)
        self.assertEqual(text.count("443:443"), 1)
        self.assertNotIn("6080:", text)
        self.assertIn("caddy:2.8", text)

    def test_caddyfile_guards_every_reader_route(self) -> None:
        text = studio_config.render_caddyfile(["ali", "mostafa"], "192.168.0.201")
        self.assertTrue(text.lstrip().startswith("192.168.0.201 {"))
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
            "ali": portal_core.Reader("ali", "Ali", portal_core.hash_password("pw-ali-12345"), False),
            "boss": portal_core.Reader("boss", "Boss", portal_core.hash_password("pw-boss-12345"), True),
        }
        portal_core.save_readers(portal_data / "readers.json", readers)
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
            studio_config.render_compose(["Bad Id"], "/d", "/r", "/p")
        with self.assertRaises(ValueError):
            studio_config.render_compose(["admin"], "/d", "/r", "/p")
        with self.assertRaises(ValueError):
            studio_config.render_caddyfile(["Bad Id"], "x")

    def test_render_reads_dataset_root_from_review_config(self) -> None:
        config = self.root / "review-config.json"
        config.write_text(json.dumps({"dataset_root": "/srv/data"}), encoding="utf-8")
        root = studio_config.read_dataset_root(config)
        text = studio_config.render_compose(["ali"], root, "/r", "/p")
        self.assertIn('"/srv/data:/data:ro"', text)

    def test_relative_and_drive_dataset_roots(self) -> None:
        drive = "M"
        config = self.root / "review-config.json"
        config.write_text(json.dumps({"dataset_root": "sub\\data"}), encoding="utf-8")
        self.assertEqual(studio_config.read_dataset_root(config), (self.root / "sub" / "data").as_posix())
        config.write_text(json.dumps({"dataset_root": drive + ":\\projects\\x y"}), encoding="utf-8")
        self.assertEqual(studio_config.read_dataset_root(config), drive + ":/projects/x y")


if __name__ == "__main__":
    unittest.main()
