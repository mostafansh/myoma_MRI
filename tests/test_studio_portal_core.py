"""Tests for the studio portal's pure login, session and authorization logic."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "studio" / "portal"))
sys.path.insert(0, str(PROJECT_ROOT / "MyomaReviewLite"))

import portal_core  # noqa: E402
from portal_core import Reader  # noqa: E402


SECRET = b"k" * 32


class PortalCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "data"
        (self.data_root / "sub-001").mkdir(parents=True)
        self.readers_root = self.root / "readers"
        self.readers_root.mkdir()
        self.path = self.root / "readers.json"
        pw = portal_core.hash_password("pw")
        self.readers = {
            "mostafa": Reader("mostafa", "Mostafa", pw, False),
            "boss": Reader("boss", "Boss", pw, True),
        }
        self.app = portal_core.PortalApp(
            self.readers, SECRET, self.data_root, self.readers_root, clock=lambda: 1000.0
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_password_round_trip_and_format(self):
        stored = portal_core.hash_password("s3cret")
        self.assertTrue(stored.startswith("scrypt$16384$8$1$"))
        self.assertTrue(portal_core.verify_password("s3cret", stored))
        self.assertFalse(portal_core.verify_password("S3cret", stored))
        self.assertFalse(portal_core.verify_password("s3cret", "-"))

    def test_session_rejects_tamper_and_expiry(self):
        token = portal_core.sign_session("mostafa", SECRET, now=1000.0)
        self.assertEqual(portal_core.read_session(token, SECRET, now=1000.0 + 43199), "mostafa")
        self.assertIsNone(portal_core.read_session(token, SECRET, now=1000.0 + 43201))
        self.assertIsNone(portal_core.read_session(token, b"x" * 32, now=1001.0))
        self.assertIsNone(portal_core.read_session(token[:-1] + ("0" if token[-1] != "0" else "1"), SECRET, now=1001.0))
        self.assertIsNone(portal_core.read_session(None, SECRET, now=1001.0))
        self.assertIsNone(portal_core.read_session("garbage", SECRET, now=1001.0))

    def test_throttle_locks_after_five_failures_for_ten_minutes(self):
        t = portal_core.LoginThrottle()
        for i in range(5):
            t.record_failure("ali", 100.0 + i)
        self.assertTrue(t.is_locked("ali", 105.0))
        self.assertFalse(t.is_locked("ali", 104.0 + 601))
        t2 = portal_core.LoginThrottle()
        for i in range(5):
            t2.record_failure("ali", 100.0 + i * 200)  # spread beyond the 600 s window
        self.assertFalse(t2.is_locked("ali", 900.0))

    def test_authorize_whole_segment_and_normalized(self):
        ali = Reader("ali", "Ali", "-", False)
        self.assertTrue(portal_core.authorize(ali, "/ali/vnc.html?x=1"))
        self.assertTrue(portal_core.authorize(ali, "/ali/websockify"))
        for uri in ("/ali2/vnc.html", "/reader2/", "/ali/../reader2/vnc.html", "/ali/%2e%2e/reader2/", "/admin", "/", "//reader2/"):
            with self.subTest(uri=uri):
                self.assertFalse(portal_core.authorize(ali, uri))
        self.assertTrue(portal_core.authorize(Reader("boss", "B", "-", True), "/ali/vnc.html"))

    def test_check_auth_outcomes(self):
        token = portal_core.sign_session("mostafa", SECRET, now=1000.0)
        self.assertEqual(self.app.check_auth(None, "/mostafa/").location, "/login")
        self.assertEqual(self.app.check_auth(token, "/reader2/").status, 403)
        self.assertEqual(self.app.check_auth(token, "/mostafa/vnc.html").status, 200)
        shutil.rmtree(self.data_root / "sub-001")
        self.assertEqual(self.app.check_auth(token, "/mostafa/").location, "/unavailable")

    def test_login_is_case_insensitive_and_messages_are_exact(self):
        self.assertTrue(self.app.login("Mostafa", "pw").ok)
        self.assertEqual(self.app.login("mostafa", "nope").message, "Wrong username or password")
        self.assertEqual(self.app.login("nobody", "pw").message, "Wrong username or password")
        for _ in range(5):
            self.app.login("mostafa", "nope")
        self.assertEqual(self.app.login("mostafa", "pw").message, "Too many attempts. Try again in 10 minutes.")

    def test_load_readers_rejects_bad_files(self):
        path = self.path
        ok = '{"id": "ali", "display_name": "Ali", "password_hash": "-", "admin": false}'
        for text in ("", "{", '{"readers": [{"id": "Bad Id", "display_name": "x", "password_hash": "-", "admin": false}]}',
                     '{"readers": [' + ok + "," + ok + "]}"):
            with self.subTest(text=text[:20]), self.assertRaises(portal_core.PortalError):
                path.write_text(text, encoding="utf-8")
                portal_core.load_readers(path)
        with self.assertRaises(portal_core.PortalError):
            portal_core.load_readers(path.with_name("missing.json"))

    def test_load_readers_rejects_reserved_ids(self):
        for rid in sorted(portal_core.RESERVED_IDS):
            entry = '{"id": "%s", "display_name": "x", "password_hash": "-", "admin": false}' % rid
            self.path.write_text('{"readers": [' + entry + "]}", encoding="utf-8")
            with self.subTest(rid=rid), self.assertRaises(portal_core.PortalError):
                portal_core.load_readers(self.path)

    def test_save_and_load_round_trip(self):
        portal_core.save_readers(self.path, self.readers)
        self.assertEqual(portal_core.load_readers(self.path), self.readers)

    def test_landing_path_and_dataset(self):
        self.assertEqual(
            portal_core.landing_path(self.readers["mostafa"]),
            "/mostafa/vnc.html?autoconnect=1&reconnect=1&resize=scale&quality=6&compression=2&path=mostafa/websockify",
        )
        self.assertEqual(portal_core.landing_path(self.readers["boss"]), "/admin")
        self.assertTrue(portal_core.dataset_available(self.data_root))
        self.assertFalse(portal_core.dataset_available(self.root / "nope"))

    def test_progress_reports_queue_errors_per_reader(self):
        rows = self.app.progress()
        self.assertEqual([r["id"] for r in rows], ["mostafa"])
        self.assertIsNotNone(rows[0]["error"])

    def test_progress_import_failure_fills_every_row_error(self):
        import builtins

        real_import = builtins.__import__

        def broken(name, *args, **kwargs):
            if name == "MyomaReviewLiteLib":
                raise ImportError("No module named 'nibabel'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = broken
        try:
            rows = self.app.progress()
        finally:
            builtins.__import__ = real_import
        self.assertEqual(len(rows), 1)
        self.assertIn("nibabel", rows[0]["error"])


if __name__ == "__main__":
    unittest.main()
