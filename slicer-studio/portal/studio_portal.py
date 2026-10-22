"""HTTP server and command line for the studio portal (standard library only).

Caddy sends /login*, /logout, /admin* and /unavailable here, and calls /auth
before proxying any /<user>/* request to that user's Slicer container.
"""

from __future__ import annotations

import argparse
import html
import ipaddress
import http.cookies
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import portal_core
from portal_core import PortalApp, PortalError

COOKIE_NAME = "studio_session"
MAX_BODY = 8192
UNAVAILABLE_COPY = "Data drive unavailable, tell the study admin"
HTML_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'",
}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; background: #f2f4f7; color: #1c2430; margin: 0;
  min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
.card {{ background: #fff; border-radius: 10px; box-shadow: 0 2px 12px rgba(0,0,0,.12);
  padding: 28px 32px; min-width: 300px; max-width: 90vw; }}
h1 {{ font-size: 20px; margin: 0 0 16px; }}
label {{ display: block; margin: 12px 0 4px; font-size: 14px; }}
input {{ width: 100%; box-sizing: border-box; padding: 8px; font-size: 16px; }}
button {{ margin-top: 18px; padding: 9px 18px; font-size: 16px; cursor: pointer; }}
.msg {{ color: #b00020; margin: 0 0 8px; }}
table {{ border-collapse: collapse; }}
th, td {{ border-bottom: 1px solid #d5d9e0; padding: 6px 12px; text-align: left; font-size: 14px; }}
</style></head><body><div class="card">{body}</div></body></html>
"""


def clean_field(text: str) -> str:
    """Make a value safe for one tab-separated access-log field."""
    return "".join(
        "?" if (ord(c) < 32 or 127 <= ord(c) < 160 or c in "\u2028\u2029") else c
        for c in str(text)
    )


def clean_username(username: str) -> str:
    """Lowercase, truncate and neutralise control characters for the access log."""
    return clean_field((username or "").strip().lower()[:64]) or "-"


def studio_title() -> str:
    return os.environ.get("STUDIO_TITLE", "Slicer Studio")


def render_page(body: str) -> bytes:
    return PAGE.format(title=html.escape(studio_title()), body=body).encode("utf-8")


def login_page(message: str | None = None) -> bytes:
    msg = f'<p class="msg">{html.escape(message)}</p>' if message else ""
    body = (
        "<h1>" + html.escape(studio_title()) + " sign in</h1>" + msg +
        '<form method="post" action="/login">'
        '<label for="username">Username</label>'
        '<input id="username" name="username" autocomplete="username" autofocus required>'
        '<label for="password">Password</label>'
        '<input id="password" name="password" type="password" autocomplete="current-password" required>'
        '<button type="submit">Sign in</button></form>'
    )
    return render_page(body)


def admin_page(rows: list[dict]) -> bytes:
    lines = []
    for row in rows:
        progress = row["progress"]
        cells = [html.escape(row["id"]), html.escape(row["display_name"])]
        if progress is None:
            cells += ["—"] * len(portal_core.PROGRESS_KEYS)
        else:
            cells += [html.escape(str(progress[key])) for key in portal_core.PROGRESS_KEYS]
        lines.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    body = (
        "<h1>User progress</h1><table><tr><th>User</th><th>Name</th><th>Total</th><th>Done</th>"
        "<th>In progress</th><th>Needs attention</th></tr>" + "".join(lines)
        + '</table><p><a href="/logout">Sign out</a></p>'
    )
    return render_page(body)


class PortalHandler(BaseHTTPRequestHandler):
    server_version = "StudioPortal"
    app: PortalApp
    log_path: Path
    log_lock: threading.Lock

    def log_message(self, format, *args):  # noqa: A002 - silence default stderr logging
        pass

    # -- helpers --
    def client_ip(self) -> str:
        first = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(first))
        except ValueError:
            return clean_field(self.client_address[0])

    def log_event(self, event: str, user_id: str | None) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fields = [stamp, event, clean_field(user_id or "-"), clean_field(self.client_ip())]
        try:
            with self.log_lock, open(self.log_path, "a", encoding="utf-8", newline="") as handle:
                handle.write(chr(9).join(fields) + chr(10))
        except OSError:
            pass

    def session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = http.cookies.SimpleCookie()
            jar.load(raw)
        except http.cookies.CookieError:
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def send(self, status: int, body: bytes = b"", *, is_html: bool = False,
             location: str | None = None, cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        if is_html:
            for name, value in HTML_HEADERS.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        if location is not None:
            self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, location: str, cookie: str | None = None) -> None:
        self.send(302, location=location, cookie=cookie)

    # -- routes --
    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            self.redirect("/login")
        elif path == "/login":
            self.send(200, login_page(), is_html=True)
        elif path == "/logout":
            user = self.app.user_for(self.session_token())
            self.log_event("logout", user.id if user else None)
            self.redirect("/login", cookie=f"{COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0")
        elif path == "/auth":
            result = self.app.check_auth(self.session_token(), self.headers.get("X-Forwarded-Uri", "/"))
            if result.status == 302:
                self.redirect(result.location or "/login")
            else:
                self.send(result.status, b"Forbidden" if result.status == 403 else b"")
        elif path == "/admin":
            user = self.app.user_for(self.session_token())
            if user is None or not user.admin:
                self.redirect("/login")
            else:
                self.send(200, admin_page(self.app.progress()), is_html=True)
        elif path == "/unavailable":
            body = f"<h1>{html.escape(UNAVAILABLE_COPY)}</h1>"
            self.send(503, render_page(body), is_html=True)
        else:
            self.send(404, b"Not found")

    do_HEAD = do_GET

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path != "/login":
            self.send(404, b"Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.send(400, b"Bad request")
            return
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        result = self.app.login(username, password)
        if not result.ok:
            event = "login_locked" if result.message == portal_core.MSG_LOCKED else "login_failed"
            self.log_event(event, clean_username(username))
            self.send(401, login_page(result.message), is_html=True)
            return
        user = result.user
        token = portal_core.sign_session(user.id, self.app.secret, self.app.clock(), user.password_hash)
        cookie = (f"{COOKIE_NAME}={token}; Path=/; HttpOnly; Secure; SameSite=Lax; "
                  f"Max-Age={portal_core.SESSION_LIFETIME}")
        self.log_event("login_ok", user.id)
        self.redirect(portal_core.landing_path(user), cookie=cookie)


def make_server(app: PortalApp, host: str, port: int, log_path: Path) -> ThreadingHTTPServer:
    handler = type("BoundPortalHandler", (PortalHandler,),
                   {"app": app, "log_path": Path(log_path), "log_lock": threading.Lock()})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


# --- command line ----------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    secret_path = data_dir / "secret.key"
    try:
        secret = secret_path.read_bytes()
    except OSError:
        print(f"error: cannot read {secret_path}; create it with the studio CLI first", file=sys.stderr)
        return 2
    try:
        users = portal_core.load_users(data_dir / "users.json")
    except PortalError as exc:
        print(f"error: {exc} (users.json)", file=sys.stderr)
        return 2
    if len(secret) < 32:
        print(f"error: {secret_path} is too short", file=sys.stderr)
        return 2
    data_root = Path(args.data_root) if args.data_root else None
    app = PortalApp(users, secret, Path(args.users_root), data_root,
                    users_path=data_dir / "users.json")
    server = make_server(app, "0.0.0.0", args.port, data_dir / "access.log")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Studio portal")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--data-dir", required=True)
    serve.add_argument("--users-root", required=True)
    serve.add_argument("--data-root")
    serve.add_argument("--port", type=int, default=9000)
    serve.set_defaults(func=cmd_serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
