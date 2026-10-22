"""Pure logic for the studio portal: accounts, sessions, throttling, authorization.

Standard library only, and no HTTP code, so it can be tested on its own.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import posixpath
import re
import secrets
import stat
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

USER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESERVED_IDS = frozenset({"admin", "login", "logout", "auth", "unavailable", "static"})
PROGRESS_KEYS = ("total", "done", "in_progress", "needs_attention")
PROGRESS_FILE = "progress.json"
PROGRESS_MAX_BYTES = 1024 * 1024
SESSION_LIFETIME = 43200
LOCK_FAILURES = 5
LOCK_SECONDS = 600
SCRYPT_N, SCRYPT_R, SCRYPT_P = 16384, 8, 1
SCRYPT_SALT_BYTES, SCRYPT_KEY_BYTES = 16, 64
SCRYPT_MAXMEM = 128 * 1024 * 1024

MSG_BAD_LOGIN = "Wrong username or password"
MSG_LOCKED = "Too many attempts. Try again in 10 minutes."


class PortalError(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: str
    display_name: str
    password_hash: str
    admin: bool


@dataclass
class AuthResult:
    status: int
    location: str | None = None


@dataclass
class LoginResult:
    ok: bool
    user: User | None = None
    message: str | None = None


# --- user accounts -------------------------------------------------------

def load_users(path: Path) -> dict[str, User]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PortalError(f"Cannot read users file: {exc}") from exc
    except ValueError as exc:
        raise PortalError(f"Users file is not valid JSON: {exc}") from exc
    entries = data.get("users") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise PortalError('Users file must contain a "users" list')
    users: dict[str, User] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PortalError("User entry must be an object")
        rid = entry.get("id")
        if not isinstance(rid, str) or not USER_ID_RE.match(rid):
            raise PortalError(f"Invalid user id: {rid!r}")
        if rid in RESERVED_IDS:
            raise PortalError(f"User id is reserved: {rid}")
        if rid in users:
            raise PortalError(f"Duplicate user id: {rid}")
        name = entry.get("display_name")
        phash = entry.get("password_hash")
        admin = entry.get("admin", False)
        if not isinstance(name, str) or not isinstance(phash, str) or not isinstance(admin, bool):
            raise PortalError(f"Invalid fields for user {rid}")
        users[rid] = User(rid, name, phash, admin)
    return users


def save_users(path: Path, users: dict[str, User]) -> None:
    path = Path(path)
    payload = {
        "users": [
            {"id": r.id, "display_name": r.display_name, "password_hash": r.password_hash, "admin": r.admin}
            for r in users.values()
        ]
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --- passwords -------------------------------------------------------------

def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=SCRYPT_KEY_BYTES
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    key = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(key_hex)
        actual = _scrypt(password, bytes.fromhex(salt_hex), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --- sessions --------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode("ascii"), "sha256").hexdigest()


def sign_session(user_id: str, secret: bytes, now: float) -> str:
    payload = _b64e(json.dumps({"r": user_id, "exp": now + SESSION_LIFETIME}).encode("utf-8"))
    return payload + "." + _mac(secret, payload)


def read_session(token: str | None, secret: bytes, now: float) -> str | None:
    if not token or not isinstance(token, str) or token.count(".") != 1:
        return None
    payload, mac = token.split(".")
    try:
        if not hmac.compare_digest(_mac(secret, payload), mac):
            return None
        data = json.loads(_b64d(payload))
        user_id, exp = data["r"], data["exp"]
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeError):
        return None
    if not isinstance(user_id, str) or isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    if now > exp:
        return None
    return user_id


# --- throttling ------------------------------------------------------------

class LoginThrottle:
    """5 failures within 600 s lock a username for 600 s. Thread safe.

    Attempts are reserved before the slow password check so that concurrent
    requests cannot all pass the lock check: failures plus attempts in flight
    never exceed LOCK_FAILURES.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._in_flight: dict[str, int] = {}

    def _recent(self, user: str, now: float) -> list[float]:
        return [t for t in self._failures.get(user, []) if now - t < LOCK_SECONDS]

    def is_locked(self, user: str, now: float) -> bool:
        with self._lock:
            return now < self._locked_until.get(user, 0.0)

    def try_begin(self, user: str, now: float) -> bool:
        """Reserve one attempt; False when locked or the budget is used up."""
        with self._lock:
            if now < self._locked_until.get(user, 0.0):
                return False
            if len(self._recent(user, now)) + self._in_flight.get(user, 0) >= LOCK_FAILURES:
                return False
            self._in_flight[user] = self._in_flight.get(user, 0) + 1
            return True

    def end(self, user: str) -> None:
        with self._lock:
            left = self._in_flight.get(user, 0) - 1
            if left > 0:
                self._in_flight[user] = left
            else:
                self._in_flight.pop(user, None)

    def record_failure(self, user: str, now: float) -> None:
        with self._lock:
            recent = self._recent(user, now)
            recent.append(now)
            self._failures[user] = recent
            if len(recent) >= LOCK_FAILURES:
                self._locked_until[user] = now + LOCK_SECONDS

    def reset(self, user: str) -> None:
        with self._lock:
            self._failures.pop(user, None)
            self._locked_until.pop(user, None)


# --- authorization ---------------------------------------------------------

def authorize(user: User, forwarded_uri: str) -> bool:
    if user.admin:
        return True
    path = posixpath.normpath(urllib.parse.unquote(urllib.parse.urlsplit(forwarded_uri).path))
    segments = [s for s in path.split("/") if s]
    return bool(segments) and segments[0] == user.id and not path.startswith("//")


def read_progress(path: Path) -> dict[str, int] | None:
    """Parse a module's progress.json; None unless it is exactly the agreed shape."""
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = b""
        while len(raw) <= PROGRESS_MAX_BYTES:
            chunk = os.read(fd, PROGRESS_MAX_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
        if len(raw) > PROGRESS_MAX_BYTES:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    finally:
        os.close(fd)
    if not isinstance(data, dict) or set(data) != set(PROGRESS_KEYS):
        return None
    for value in data.values():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
    return {key: data[key] for key in PROGRESS_KEYS}


def dataset_available(data_root: Path) -> bool:
    try:
        root = Path(data_root)
        return root.is_dir() and any(root.glob("sub-*"))
    except OSError:
        return False


def landing_path(user: User) -> str:
    if user.admin:
        return "/admin"
    return (
        f"/{user.id}/vnc.html?autoconnect=1&reconnect=1&resize=scale"
        f"&quality=6&compression=2&path={user.id}/websockify"
    )


# --- application -----------------------------------------------------------

class PortalApp:
    _dummy: str | None = None

    def __init__(self, users: dict[str, User], secret: bytes, workspaces_root: Path,
                 data_root: Path | None = None, clock: Callable[[], float] = time.time,
                 users_path: Path | None = None) -> None:
        self.users = users
        self.users_path = Path(users_path) if users_path is not None else None
        self._users_lock = threading.Lock()
        self._users_stamp = self._stat_stamp()
        self.secret = secret
        self.data_root = Path(data_root) if data_root is not None else None
        self.workspaces_root = Path(workspaces_root)
        self.clock = clock
        self.throttle = LoginThrottle()

    def _stat_stamp(self) -> tuple[int, int] | None:
        if self.users_path is None:
            return None
        try:
            st = os.stat(self.users_path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _refresh_users(self) -> dict[str, User]:
        """Reload users.json when its mtime or size changed; keep the last good set on failure."""
        if self.users_path is None:
            return self.users
        with self._users_lock:
            stamp = self._stat_stamp()
            if stamp != self._users_stamp:
                self._users_stamp = stamp
                try:
                    self.users = load_users(self.users_path)
                except PortalError as exc:
                    print(f"portal: keeping previous users, reload failed: {exc}", file=sys.stderr)
            return self.users

    def login(self, username: str, password: str) -> LoginResult:
        users = self._refresh_users()
        name = (username or "").strip().lower()
        now = self.clock()
        if not self.throttle.try_begin(name, now):
            return LoginResult(False, None, MSG_LOCKED)
        try:
            user = users.get(name)
            stored = user.password_hash if user else self._dummy_hash()
            valid = verify_password(password or "", stored) and user is not None
            if not valid:
                self.throttle.record_failure(name, now)
                if self.throttle.is_locked(name, now):
                    return LoginResult(False, None, MSG_LOCKED)
                return LoginResult(False, None, MSG_BAD_LOGIN)
            self.throttle.reset(name)
            return LoginResult(True, user, None)
        finally:
            self.throttle.end(name)

    @classmethod
    def _dummy_hash(cls) -> str:
        # Burns the same scrypt time for unknown usernames.
        if cls._dummy is None:
            cls._dummy = hash_password(secrets.token_hex(8))
        return cls._dummy

    def user_for(self, token: str | None) -> User | None:
        users = self._refresh_users()
        user_id = read_session(token, self.secret, self.clock())
        return users.get(user_id) if user_id else None

    def check_auth(self, token: str | None, forwarded_uri: str) -> AuthResult:
        user = self.user_for(token)
        if user is None:
            return AuthResult(302, "/login")
        if self.data_root is not None and not dataset_available(self.data_root):
            return AuthResult(302, "/unavailable")
        if not authorize(user, forwarded_uri):
            return AuthResult(403, None)
        return AuthResult(200, None)

    def progress(self) -> list[dict]:
        rows = []
        for user in self._refresh_users().values():
            if user.admin:
                continue
            progress = read_progress(self.workspaces_root / user.id / PROGRESS_FILE)
            rows.append({"id": user.id, "display_name": user.display_name, "progress": progress})
        return rows
