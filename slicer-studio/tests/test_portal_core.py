"""Tests for the studio portal's pure login, session and authorization logic."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "slicer-studio" / "portal"))

import portal_core  # noqa: E402
from portal_core import User  # noqa: E402


SECRET = b"k" * 32


class PortalCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "data"
        (self.data_root / "sub-001").mkdir(parents=True)
        self.workspaces_root = self.root / "workspaces"
        self.workspaces_root.mkdir()
        self.path = self.root / "users.json"
        pw = portal_core.hash_password("pw")
        self.users = {
            "mostafa": User("mostafa", "Mostafa", pw, False),
            "boss": User("boss", "Boss", pw, True),
        }
        self.app = portal_core.PortalApp(
            self.users, SECRET, self.workspaces_root, self.data_root, clock=lambda: 1000.0
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def token(self, user_id):
        return portal_core.sign_session(user_id, SECRET, now=1000.0, password_hash=self.users[user_id].password_hash)

    def test_session_is_bound_to_the_current_password_hash(self):
        token = self.token("mostafa")
        self.assertEqual(self.app.user_for(token).id, "mostafa")
        self.users["mostafa"] = User("mostafa", "Mostafa", portal_core.hash_password("pw"), False)
        self.assertIsNone(self.app.user_for(token), "same password, new salt: the old session must end")
        self.assertEqual(self.app.check_auth(token, "/mostafa/").location, "/login")
        self.assertEqual(self.app.user_for(self.token("mostafa")).id, "mostafa")

    def test_session_without_binding_is_rejected(self):
        import json
        payload = portal_core._b64e(json.dumps({"r": "mostafa", "exp": 5000.0}).encode("utf-8"))
        token = payload + "." + portal_core._mac(SECRET, payload)
        self.assertIsNone(portal_core.read_session_claims(token, SECRET, now=1000.0))
        self.assertIsNone(self.app.user_for(token))

    def test_password_round_trip_and_format(self):
        stored = portal_core.hash_password("s3cret")
        self.assertTrue(stored.startswith("scrypt$16384$8$1$"))
        self.assertTrue(portal_core.verify_password("s3cret", stored))
        self.assertFalse(portal_core.verify_password("S3cret", stored))
        self.assertFalse(portal_core.verify_password("s3cret", "-"))

    def test_session_rejects_tamper_and_expiry(self):
        token = portal_core.sign_session("mostafa", SECRET, now=1000.0, password_hash="h")
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
        ali = User("ali", "Ali", "-", False)
        self.assertTrue(portal_core.authorize(ali, "/ali/vnc.html?x=1"))
        self.assertTrue(portal_core.authorize(ali, "/ali/websockify"))
        for uri in ("/ali2/vnc.html", "/user2/", "/ali/../user2/vnc.html", "/ali/%2e%2e/user2/", "/admin", "/", "//user2/"):
            with self.subTest(uri=uri):
                self.assertFalse(portal_core.authorize(ali, uri))
        self.assertTrue(portal_core.authorize(User("boss", "B", "-", True), "/ali/vnc.html"))

    def test_check_auth_outcomes(self):
        token = self.token("mostafa")
        self.assertEqual(self.app.check_auth(None, "/mostafa/").location, "/login")
        self.assertEqual(self.app.check_auth(token, "/user2/").status, 403)
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

    def test_load_users_rejects_bad_files(self):
        path = self.path
        ok = '{"id": "ali", "display_name": "Ali", "password_hash": "-", "admin": false}'
        for text in ("", "{", '{"users": [{"id": "Bad Id", "display_name": "x", "password_hash": "-", "admin": false}]}',
                     '{"users": [' + ok + "," + ok + "]}",
                     '{"users": [{"id": "ali\\n", "display_name": "x", "password_hash": "-", "admin": false}]}'):
            with self.subTest(text=text[:20]), self.assertRaises(portal_core.PortalError):
                path.write_text(text, encoding="utf-8")
                portal_core.load_users(path)
        with self.assertRaises(portal_core.PortalError):
            portal_core.load_users(path.with_name("missing.json"))

    def test_load_users_rejects_reserved_ids(self):
        for rid in sorted(portal_core.RESERVED_IDS):
            entry = '{"id": "%s", "display_name": "x", "password_hash": "-", "admin": false}' % rid
            self.path.write_text('{"users": [' + entry + "]}", encoding="utf-8")
            with self.subTest(rid=rid), self.assertRaises(portal_core.PortalError):
                portal_core.load_users(self.path)

    def test_save_and_load_round_trip(self):
        portal_core.save_users(self.path, self.users)
        self.assertEqual(portal_core.load_users(self.path), self.users)

    def test_landing_path_and_dataset(self):
        self.assertEqual(
            portal_core.landing_path(self.users["mostafa"]),
            "/mostafa/vnc.html?autoconnect=1&reconnect=1&resize=scale&quality=6&compression=2&path=mostafa/websockify",
        )
        self.assertEqual(portal_core.landing_path(self.users["boss"]), "/admin")
        self.assertTrue(portal_core.dataset_available(self.data_root))
        self.assertFalse(portal_core.dataset_available(self.root / "nope"))

    def test_dataset_available_needs_only_one_entry_of_any_name(self):
        other = self.root / "other"
        self.assertFalse(portal_core.dataset_available(other))  # missing
        other.mkdir()
        self.assertFalse(portal_core.dataset_available(other))  # empty
        (other / "case_a").mkdir()
        self.assertTrue(portal_core.dataset_available(other))  # no sub-* folder needed
        flat = self.root / "flat"
        flat.mkdir()
        (flat / "scan.nrrd").write_bytes(b"x")
        self.assertTrue(portal_core.dataset_available(flat))
        (self.root / "afile").write_bytes(b"x")
        self.assertFalse(portal_core.dataset_available(self.root / "afile"))  # a file, not a directory

    def test_read_progress_accepts_exact_shape(self):
        p = self.root / "progress.json"; p.write_text('{"total": 3, "done": 1, "in_progress": 1, "needs_attention": 0}', encoding="utf-8")
        self.assertEqual(portal_core.read_progress(p), {"total": 3, "done": 1, "in_progress": 1, "needs_attention": 0})

    def test_read_progress_rejects_bad_files(self):
        p = self.root / "progress.json"
        for text in ("", "{", "[]", '{"total": 3}', '{"total": -1, "done": 0, "in_progress": 0, "needs_attention": 0}',
                     '{"total": true, "done": 0, "in_progress": 0, "needs_attention": 0}',
                     '{"total": 1, "done": 0, "in_progress": 0, "needs_attention": 0, "x": 1}',
                     '{"total": "3", "done": 0, "in_progress": 0, "needs_attention": 0}'):
            with self.subTest(text=text):
                p.write_text(text, encoding="utf-8"); self.assertIsNone(portal_core.read_progress(p))
        p.write_bytes(b"{" + b" " * (2 * 1024 * 1024) + b"}")          # > 1 MiB is refused unread
        self.assertIsNone(portal_core.read_progress(p))
        self.assertIsNone(portal_core.read_progress(self.root / "missing.json"))

    def test_read_progress_bounded_read_just_over_cap(self):
        p = self.root / "progress.json"
        p.write_text('{"total": 1, "done": 0, "in_progress": 0, "needs_attention": 0}', encoding="utf-8")
        real_fstat = os.fstat
        grown = []

        def fstat_then_grow(fd):
            result = real_fstat(fd)
            if not grown:
                grown.append(1)
                with open(p, "ab") as handle:
                    handle.write(b" " * (portal_core.PROGRESS_MAX_BYTES + 1))
            return result

        with mock.patch.object(os, "fstat", fstat_then_grow):
            self.assertIsNone(portal_core.read_progress(p))

    def test_read_progress_refuses_symlink(self):
        target = self.root / "real.json"
        target.write_text('{"total": 1, "done": 0, "in_progress": 0, "needs_attention": 0}', encoding="utf-8")
        link = self.root / "progress.json"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted")
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("no O_NOFOLLOW")
        self.assertIsNone(portal_core.read_progress(link))

    def test_read_progress_fifo_returns_promptly(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("no mkfifo")
        fifo = self.root / "progress.json"
        os.mkfifo(fifo)
        result = []
        t = threading.Thread(target=lambda: result.append(portal_core.read_progress(fifo)), daemon=True)
        t.start(); t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(result, [None])

    def test_read_progress_directory_returns_none(self):
        d = self.root / "progress.json"
        d.mkdir()
        self.assertIsNone(portal_core.read_progress(d))

    def test_progress_rows_use_progress_json_only(self):
        pw = portal_core.hash_password("pw")
        users = {"ali": User("ali", "Ali", pw, False), "sara": User("sara", "Sara", pw, False),
                 "boss": User("boss", "Boss", pw, True)}
        (self.workspaces_root / "ali").mkdir()
        (self.workspaces_root / "ali" / "progress.json").write_text(
            '{"total": 3, "done": 1, "in_progress": 1, "needs_attention": 0}', encoding="utf-8")
        app = portal_core.PortalApp(users, SECRET, self.workspaces_root, self.data_root)
        rows = {r["id"]: r for r in app.progress()}
        self.assertEqual(set(rows), {"ali", "sara"})
        self.assertEqual(rows["ali"]["progress"]["done"], 1)
        self.assertEqual(rows["ali"]["display_name"], "Ali")
        self.assertIsNone(rows["sara"]["progress"])

    def test_check_auth_without_dataset_skips_unavailable(self):
        app = portal_core.PortalApp(self.users, SECRET, workspaces_root=self.workspaces_root, data_root=None, clock=lambda: 1000.0)
        token = self.token("mostafa")
        self.assertEqual(app.check_auth(token, "/mostafa/vnc.html").status, 200)

    def test_concurrent_wrong_logins_cannot_exceed_the_attempt_budget(self):
        import threading
        from unittest import mock

        calls = []
        gate = threading.Event()

        def slow_verify(password, stored):
            calls.append(1)
            gate.wait(2)
            return False

        results = []
        barrier = threading.Barrier(20)

        def attempt():
            barrier.wait()
            results.append(self.app.login("mostafa", "wrong"))

        with mock.patch.object(portal_core, "verify_password", slow_verify):
            threads = [threading.Thread(target=attempt) for _ in range(20)]
            for t in threads:
                t.start()
            threading.Event().wait(0.5)
            gate.set()
            for t in threads:
                t.join(10)
        self.assertLessEqual(len(calls), 5)
        self.assertEqual(len(results), 20)
        self.assertTrue(self.app.throttle.is_locked("mostafa", 1000.0))
        self.assertEqual(self.app.login("mostafa", "pw").message, portal_core.MSG_LOCKED)


if __name__ == "__main__":
    unittest.main()
