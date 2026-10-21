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
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

READER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESERVED_IDS = frozenset({"admin", "login", "logout", "auth", "unavailable", "static"})
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
class Reader:
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
    reader: Reader | None = None
    message: str | None = None


# --- reader accounts -------------------------------------------------------

def load_readers(path: Path) -> dict[str, Reader]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PortalError(f"Cannot read readers file: {exc}") from exc
    except ValueError as exc:
        raise PortalError(f"Readers file is not valid JSON: {exc}") from exc
    entries = data.get("readers") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise PortalError('Readers file must contain a "readers" list')
    readers: dict[str, Reader] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PortalError("Reader entry must be an object")
        rid = entry.get("id")
        if not isinstance(rid, str) or not READER_ID_RE.match(rid):
            raise PortalError(f"Invalid reader id: {rid!r}")
        if rid in RESERVED_IDS:
            raise PortalError(f"Reader id is reserved: {rid}")
        if rid in readers:
            raise PortalError(f"Duplicate reader id: {rid}")
        name = entry.get("display_name")
        phash = entry.get("password_hash")
        admin = entry.get("admin", False)
        if not isinstance(name, str) or not isinstance(phash, str) or not isinstance(admin, bool):
            raise PortalError(f"Invalid fields for reader {rid}")
        readers[rid] = Reader(rid, name, phash, admin)
    return readers


def save_readers(path: Path, readers: dict[str, Reader]) -> None:
    path = Path(path)
    payload = {
        "readers": [
            {"id": r.id, "display_name": r.display_name, "password_hash": r.password_hash, "admin": r.admin}
            for r in readers.values()
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


def sign_session(reader_id: str, secret: bytes, now: float) -> str:
    payload = _b64e(json.dumps({"r": reader_id, "exp": now + SESSION_LIFETIME}).encode("utf-8"))
    return payload + "." + _mac(secret, payload)


def read_session(token: str | None, secret: bytes, now: float) -> str | None:
    if not token or not isinstance(token, str) or token.count(".") != 1:
        return None
    payload, mac = token.split(".")
    try:
        if not hmac.compare_digest(_mac(secret, payload), mac):
            return None
        data = json.loads(_b64d(payload))
        reader_id, exp = data["r"], data["exp"]
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeError):
        return None
    if not isinstance(reader_id, str) or isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    if now > exp:
        return None
    return reader_id


# --- throttling ------------------------------------------------------------

class LoginThrottle:
    """5 failures within 600 s lock a username for 600 s."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, user: str, now: float) -> bool:
        return now < self._locked_until.get(user, 0.0)

    def record_failure(self, user: str, now: float) -> None:
        recent = [t for t in self._failures.get(user, []) if now - t < LOCK_SECONDS]
        recent.append(now)
        self._failures[user] = recent
        if len(recent) >= LOCK_FAILURES:
            self._locked_until[user] = now + LOCK_SECONDS

    def reset(self, user: str) -> None:
        self._failures.pop(user, None)
        self._locked_until.pop(user, None)


# --- authorization ---------------------------------------------------------

def authorize(reader: Reader, forwarded_uri: str) -> bool:
    if reader.admin:
        return True
    path = posixpath.normpath(urllib.parse.unquote(urllib.parse.urlsplit(forwarded_uri).path))
    segments = [s for s in path.split("/") if s]
    return bool(segments) and segments[0] == reader.id and not path.startswith("//")


def dataset_available(data_root: Path) -> bool:
    try:
        root = Path(data_root)
        return root.is_dir() and any(root.glob("sub-*"))
    except OSError:
        return False


def landing_path(reader: Reader) -> str:
    if reader.admin:
        return "/admin"
    return (
        f"/{reader.id}/vnc.html?autoconnect=1&reconnect=1&resize=scale"
        f"&quality=6&compression=2&path={reader.id}/websockify"
    )


# --- application -----------------------------------------------------------

class PortalApp:
    _dummy: str | None = None

    def __init__(self, readers: dict[str, Reader], secret: bytes, data_root: Path,
                 readers_root: Path, clock: Callable[[], float] = time.time) -> None:
        self.readers = readers
        self.secret = secret
        self.data_root = Path(data_root)
        self.readers_root = Path(readers_root)
        self.clock = clock
        self.throttle = LoginThrottle()

    def login(self, username: str, password: str) -> LoginResult:
        user = (username or "").strip().lower()
        now = self.clock()
        if self.throttle.is_locked(user, now):
            return LoginResult(False, None, MSG_LOCKED)
        reader = self.readers.get(user)
        stored = reader.password_hash if reader else self._dummy_hash()
        valid = verify_password(password or "", stored) and reader is not None
        if not valid:
            self.throttle.record_failure(user, now)
            if self.throttle.is_locked(user, now):
                return LoginResult(False, None, MSG_LOCKED)
            return LoginResult(False, None, MSG_BAD_LOGIN)
        self.throttle.reset(user)
        return LoginResult(True, reader, None)

    @classmethod
    def _dummy_hash(cls) -> str:
        # Burns the same scrypt time for unknown usernames.
        if cls._dummy is None:
            cls._dummy = hash_password(secrets.token_hex(8))
        return cls._dummy

    def reader_for(self, token: str | None) -> Reader | None:
        reader_id = read_session(token, self.secret, self.clock())
        return self.readers.get(reader_id) if reader_id else None

    def check_auth(self, token: str | None, forwarded_uri: str) -> AuthResult:
        reader = self.reader_for(token)
        if reader is None:
            return AuthResult(302, "/login")
        if not dataset_available(self.data_root):
            return AuthResult(302, "/unavailable")
        if not authorize(reader, forwarded_uri):
            return AuthResult(403, None)
        return AuthResult(200, None)

    def progress(self) -> list[dict]:
        from MyomaReviewLiteLib import reader_queue

        rows = []
        for reader in self.readers.values():
            if reader.admin:
                continue
            row = {"id": reader.id, "display_name": reader.display_name,
                   "total": None, "counts": None, "error": None}
            try:
                report = reader_queue.queue_report(self.readers_root / reader.id, data_root=self.data_root)
                row["total"] = report.get("total")
                row["counts"] = report.get("counts")
            except Exception as exc:  # any failure is that reader's error
                row["error"] = str(exc) or type(exc).__name__
            rows.append(row)
        return rows
