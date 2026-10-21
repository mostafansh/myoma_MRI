"""Tests for the studio portal's HTTP server and CLI."""

from __future__ import annotations

import http.client
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "studio" / "portal"))
sys.path.insert(0, str(PROJECT_ROOT / "MyomaReviewLite"))

import portal_core  # noqa: E402
import studio_portal  # noqa: E402
from portal_core import Reader  # noqa: E402

SECRET = b"k" * 32
SCRIPT = str(PROJECT_ROOT / "studio" / "portal" / "studio_portal.py")


class PortalServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.data_root = root / "data"
        (self.data_root / "sub-001").mkdir(parents=True)
        self.readers_root = root / "readers"
        self.readers_root.mkdir()
        self.log_path = root / "access.log"
        pw = portal_core.hash_password("pw")
        readers = {
            "mostafa": Reader("mostafa", "Mostafa <b>", pw, False),
            "boss": Reader("boss", "Boss", pw, True),
        }
        self.app = portal_core.PortalApp(readers, SECRET, self.data_root, self.readers_root)
        self.server = studio_portal.make_server(self.app, "127.0.0.1", 0, self.log_path)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp, resp.read().decode("utf-8")
        finally:
            conn.close()

    def login(self, user, password):
        body = urllib.parse.urlencode({"username": user, "password": password})
        return self.request("POST", "/login", body,
                            {"Content-Type": "application/x-www-form-urlencoded"})

    def cookie_for(self, user):
        resp, _ = self.login(user, "pw")
        return resp.getheader("Set-Cookie").split(";")[0]

    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args],
                              capture_output=True, text=True, timeout=30)

    def test_login_sets_cookie_and_lands_on_viewer(self):
        resp, _ = self.login("mostafa", "pw")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), portal_core.landing_path(self.app.readers["mostafa"]))
        cookie = resp.getheader("Set-Cookie")
        for part in ("studio_session=", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=43200"):
            self.assertIn(part, cookie)
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")

    def test_auth_guards_other_readers_websocket(self):
        cookie = self.cookie_for("mostafa")
        resp, _ = self.request("GET", "/auth", headers={"Cookie": cookie, "X-Forwarded-Uri": "/reader2/websockify"})
        self.assertEqual(resp.status, 403)
        resp, _ = self.request("GET", "/auth", headers={"Cookie": cookie, "X-Forwarded-Uri": "/mostafa/websockify"})
        self.assertEqual(resp.status, 200)

    def test_auth_without_cookie_redirects_to_login(self):
        resp, _ = self.request("GET", "/auth", headers={"X-Forwarded-Uri": "/mostafa/"})
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "/login")

    def test_admin_page_lists_readers_only_for_admin(self):
        resp, body = self.request("GET", "/admin", headers={"Cookie": self.cookie_for("boss")})
        self.assertEqual(resp.status, 200)
        self.assertIn("mostafa", body)
        self.assertNotIn("<b>", body)
        resp, _ = self.request("GET", "/admin", headers={"Cookie": self.cookie_for("mostafa")})
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "/login")

    def test_unavailable_page_copy(self):
        resp, body = self.request("GET", "/unavailable")
        self.assertEqual(resp.status, 503)
        self.assertIn("Data drive unavailable, tell the study admin", body)
        self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", resp.getheader("Content-Security-Policy"))

    def test_access_log_records_failures(self):
        resp, body = self.login("mostafa", "wrong")
        self.assertEqual(resp.status, 401)
        self.assertIn("Wrong username or password", body)
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "login_failed")

    def test_failed_login_logs_sanitised_username(self):
        self.login("Ali\nFAKE\tlogin_ok\tboss\t1.1.1.1" + "x" * 100, "bad")
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        fields = lines[0].split("\t")
        self.assertEqual(len(fields), 4)
        self.assertEqual(fields[1], "login_failed")
        self.assertTrue(fields[2].startswith("ali?fake?login_ok?boss"))
        self.assertLessEqual(len(fields[2]), 64)

    def test_folded_forwarded_for_cannot_forge_log_lines(self):
        import socket

        body = urllib.parse.urlencode({"username": "mostafa", "password": "bad"})
        raw = (
            "POST /login HTTP/1.1\r\nHost: x\r\n"
            "X-Forwarded-For: 1.2.3.4\r\n x\tlogin_ok\tboss\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n{body}"
        )
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(raw.encode("ascii"))
            while sock.recv(4096):
                pass
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        fields = lines[0].split("\t")
        self.assertEqual(len(fields), 4)
        self.assertEqual(fields[1], "login_failed")
        self.assertEqual(fields[3], "127.0.0.1")

    def test_valid_forwarded_for_is_logged(self):
        self.request("POST", "/login", urllib.parse.urlencode({"username": "x", "password": "y"}),
                     {"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": "10.1.2.3, 10.0.0.1"})
        fields = self.log_path.read_text(encoding="utf-8").splitlines()[0].split("\t")
        self.assertEqual(fields[3], "10.1.2.3")

    def test_root_and_logout_redirect_to_login(self):
        resp, _ = self.request("GET", "/")
        self.assertEqual((resp.status, resp.getheader("Location")), (302, "/login"))
        resp, _ = self.request("GET", "/logout")
        self.assertEqual((resp.status, resp.getheader("Location")), (302, "/login"))
        self.assertIn("Max-Age=0", resp.getheader("Set-Cookie"))

    def test_serve_refuses_malformed_readers_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "readers.json").write_text("{", encoding="utf-8")
            (Path(d) / "secret.key").write_bytes(b"s" * 32)
            result = self.run_cli("serve", "--data-dir", d, "--data-root", d,
                                  "--readers-root", d, "--port", "0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("readers.json", result.stderr)

    def test_serve_refuses_missing_secret(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.run_cli("serve", "--data-dir", d, "--data-root", d,
                                  "--readers-root", d, "--port", "0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("secret.key", result.stderr)

    def test_set_password_refuses_reserved_id(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.run_cli("set-password", "admin", "--data-dir", d)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((Path(d) / "readers.json").exists())


if __name__ == "__main__":
    unittest.main()
