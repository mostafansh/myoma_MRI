# Review Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Readers on the local network open `https://192.168.0.201` in a browser, log in, and review in their own always-on MyomaReviewLite, served from the machine that holds the data.

**Architecture:**
- **Reader containers.** One Docker image runs Linux Slicer 5.10 with the module on a virtual display (Xvfb → x11vnc → noVNC). Compose runs one container per reader.
- **Portal.** A stdlib-only Python portal (login, session cookie, Caddy `forward_auth`, admin progress page).
- **Caddy** terminates HTTPS and routes `/<reader>/*` to that reader's container.
- **Generated config.** `docker-compose.yml` and the `Caddyfile` are generated from `readers.json`.
- **Studio mode.** The module gains a studio mode that locks Slicer's UI (Level 1 blinding). The AI mask path goes through one method, so Level 2 can replace it later.

**Tech Stack:**
- Python 3.12 (stdlib only outside Slicer), `unittest`
- 3D Slicer 5.10 (PythonQt `qt`)
- Docker Desktop (WSL2) on Windows 11 Pro; Ubuntu 22.04 base image
- Xvfb, x11vnc, noVNC/websockify
- Caddy 2
- PowerShell 7

**Spec:** `docs/superpowers/specs/2026-10-21-review-studio-design.md`

## Global Constraints

- **Unchanged behavior:**
  - Studio mode is on only when `MYOMA_REVIEW_LITE_STUDIO` equals exactly `"1"`. Otherwise the module behaves exactly as today.
  - No edits to `MyomaReviewLiteLib/domain.py` or `storage.py`, and result file formats do not change.
- **No drive-letter paths** in committed or on-disk `.cmd .json .md .ps1 .py .svg .txt` files (`tests/test_package_portability.py`). The dataset path is read from `review-config.json` at render time. Generated files live in `studio/generated/` as `docker-compose.yml` and `Caddyfile`, which the test does not scan, and are gitignored.
- **Dependencies:** the portal, `studio_config.py` and all tests use the Python standard library only.
- **Reader id:** must match `^[a-z][a-z0-9_-]{0,31}$`. Login lowercases the username before lookup.
- **Ports:**
  - Caddy publishes 443, the only published port.
  - Portal listens on 9000, noVNC/websockify on 6080 and x11vnc on 5900, bound to localhost inside the container.
- **Session cookie:** `studio_session`, lifetime 43200 s, `HttpOnly; Secure; SameSite=Lax; Path=/`.
- **Lockout:** 5 failures within 600 s locks the username for 600 s.
- **Password hash format:** `scrypt$16384$8$1$<salt hex>$<hash hex>`, with a 16-byte salt and a 64-byte key.
- **Exact copy:**
  - unavailable page: `"Data drive unavailable, tell the study admin"`
  - login errors: `"Wrong username or password"` and `"Too many attempts. Try again in 10 minutes."`
- **Viewer landing path:** `/<reader>/vnc.html?autoconnect=1&reconnect=1&resize=scale&quality=6&compression=2&path=<reader>/websockify`
- **Test commands:**
  - Pure module tests, from `MyomaReviewLite/`: `python -m unittest discover -s tests -p "<file>" -v`.
  - Repo-level tests, from the repo root: `python -m unittest discover -s tests -p "<file>" -v`.
  - Slicer tests (PowerShell, from `MyomaReviewLite/`), called **SLICER_TEST `<file>`**: `$env:MYOMA_TEST_SCRIPT="tests/<file>"; $env:MYOMA_TEST_LOG="$env:TEMP\myoma_<file>.log"; & "$env:LOCALAPPDATA\slicer.org\3D Slicer 5.10.0\Slicer.exe" --no-splash --python-script tests/run_slicer_test.py; Get-Content "$env:TEMP\myoma_<file>.log" -Tail 15`
- **Pre-existing error:** `test_runtime_files_do_not_reference_shortcuts` errors in a worktree because `mostafa/` is gitignored. It is not caused by this work, and its result must not change.

## Review Focus

1. **Prefix collision:** reader `ali` must not reach `/ali2/...`. `authorize` compares the whole first path segment. (Task 4)
2. **Path tricks:** `/mostafa/../reader2/vnc.html` and `/mostafa/%2e%2e/reader2/` must be refused for `mostafa`. `authorize` unquotes and normalizes before comparing. (Task 4)
3. **The WebSocket path is guarded too:** `/reader2/websockify` with mostafa's cookie returns 403, not 200. (Task 5)
4. **A tampered or expired cookie** redirects to `/login` and is never accepted. (Task 4)
5. **A missing or malformed `readers.json`** makes `serve` exit with a clear message instead of starting an open portal. (Task 5)

## File Structure

| File | Responsibility |
|---|---|
| `studio/slicer/Dockerfile`, `studio/slicer/start-studio.sh` | Reader image: Slicer, Xvfb, x11vnc, noVNC; supervised Slicer launch |
| `MyomaReviewLite/MyomaReviewLiteLib/studio_mode.py` | `is_studio_mode`, the action-lock rule, `lock_down(main_window)` |
| `MyomaReviewLite/MyomaReviewLite.py` | Calls into studio mode; the `ai_mask_path()` seam |
| `studio/portal/portal_core.py` | Pure logic: readers file, password hashing, sessions, throttle, authorization, dataset check, `PortalApp` |
| `studio/portal/studio_portal.py` | HTTP handler, HTML pages, access log, CLI (`serve`, `set-password`) |
| `studio/portal/Dockerfile` | Portal image (python:3.12-slim, with the module library for `queue_report`) |
| `studio/studio_config.py` | Renders `studio/generated/docker-compose.yml` and `studio/generated/Caddyfile` |
| `studio/backup-readers.ps1` | Nightly dated mirror of reader results and `readers.json`, keeping 7 copies |
| `studio/README.md` | Setup guide (router, firewall, Docker, certificate, adding a reader) and acceptance checklist |
| `studio/.gitignore` | `readers/`, `portal-data/`, `generated/`, `logs/` |

---

### Task 1: Reader Slicer image, with the module proven on Linux

This resolves spec open items 1 and 3 first, because every later task depends on them.

**Files:**
- Create: `studio/slicer/Dockerfile`, `studio/slicer/start-studio.sh`, `studio/.gitignore`

**Interfaces:**
- Produces: image `myoma-studio-slicer`. Module at `/opt/MyomaReviewLite`, noVNC on container port 6080, entrypoint `/usr/local/bin/start-studio.sh`, running as non-root user `reader` (uid 1000).
  - Environment defaults: `MYOMA_REVIEW_LITE_CASE=/review/queue.json`, `MYOMA_REVIEW_LITE_DATA_ROOT=/data`, `MYOMA_REVIEW_LITE_STUDIO=1`, `DISPLAY=:1`.

- [ ] **Step 1: Write `studio/slicer/Dockerfile`.**
  - Base: `ubuntu:22.04`.
  - `ARG SLICER_URL`, required: the Linux 5.10.x `.tar.gz` link from download.slicer.org. Unpack it to `/opt/slicer` and put it on `PATH`.
  - apt packages:
    - `xvfb x11vnc novnc websockify procps`
    - the X11/GL/Qt runtime libraries Slicer needs: `libgl1 libglu1-mesa libegl1 libxt6 libxrender1 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 libnss3 libxcomposite1 libxcursor1 libxi6 libxrandr2 libxtst6 libxdamage1 libasound2 libpulse-mainloop-glib0 libdbus-1-3 libfontconfig1 libsm6 libice6`
  - Copy `MyomaReviewLite/` to `/opt/MyomaReviewLite`; the build context is the repo root.
  - Add a build-time check: `RUN Slicer --version`.
- [ ] **Step 2: Write `start-studio.sh`** (bash, `set -u`):
  1. Start `Xvfb :1 -screen 0 1920x1080x24 -nolisten tcp`.
  2. Start `x11vnc -display :1 -localhost -rfbport 5900 -forever -shared -nopw -noclipboard -nosetclipboard -quiet`.
  3. Start `websockify --web /usr/share/novnc 6080 localhost:5900`.
  4. Loop forever: run `Slicer --no-splash --additional-module-paths /opt/MyomaReviewLite --python-code "slicer.util.selectModule('MyomaReviewLite'); slicer.util.mainWindow().showFullScreen()"`, then `sleep 2`.
- [ ] **Step 3: Build.** Run `docker build -f studio/slicer/Dockerfile --build-arg SLICER_URL=<url> -t myoma-studio-slicer .`. Expected: the build ends with Slicer printing `5.10.x`.
- [ ] **Step 4: Run every Slicer test inside the container.** For each `MyomaReviewLite/tests/test_*_slicer.py`, run `docker run --rm --entrypoint bash -w /opt/MyomaReviewLite -e MYOMA_REVIEW_LITE_STUDIO=0 -e MYOMA_TEST_SCRIPT=tests/<file> -e MYOMA_TEST_LOG=/tmp/t.log myoma-studio-slicer -c "xvfb-run -s '-screen 0 1920x1080x24' Slicer --no-splash --python-script tests/run_slicer_test.py; tail -15 /tmp/t.log"`.
  - Expected: each log ends in the same PASS line as on Windows.
  - Any failure is a Linux incompatibility. Fix it in the module, keeping Windows green (re-run that file's SLICER_TEST on Windows), before continuing.
- [ ] **Step 5: Smoke-test a real case and time it.**
  1. From the repo root, run `python reader_queue.py create --reader-id smoke --study-id smoke --reader-dir <scratch>/smoke --case-list <one case id>`.
  2. Run the image with `-p 127.0.0.1:6080:6080`, mounting the dataset root read-only at `/data` and `<scratch>/smoke` at `/review`.
  3. Open `http://localhost:6080/vnc.html?autoconnect=1&resize=scale`.
  4. Complete the case: box, reveal, correct, FIGO, finish.
  5. Time it from case open to the slices showing.

  Expected:
  - The case completes and `annotations.json` appears in `<scratch>/smoke/<case>`.
  - If loading takes more than 10 s, record that in `studio/README.md` under "Known limits", along with the spec's fallback (copy the dataset into the WSL2 filesystem).
- [ ] **Step 6: Commit.** Run `git add studio/slicer studio/.gitignore` (plus any module fixes) and `git commit -m "Add the reader Slicer image and prove the module on Linux Slicer"`.

### Task 2: AI-mask path seam (for Level 2)

**Files:**
- Modify: `MyomaReviewLite/MyomaReviewLite.py:412-421` (`reveal_ai`) and `:528-537` (`load_ai_mask`)
- Test: `MyomaReviewLite/tests/test_ai_mask_seam.py`

**Interfaces:**
- Produces: `MyomaReviewLiteLogic.ai_mask_path(self) -> Path`. It raises `StorageError("no case is open")` without a manifest, and otherwise returns `self.manifest.ai_mask`. `reveal_ai()` returns `self.ai_mask_path()`, and `load_ai_mask()` loads `str(self.ai_mask_path())`.

- [ ] **Step 1: Write the failing test.** It scans the module source:

```python
SOURCE = (Path(__file__).resolve().parents[1] / "MyomaReviewLite.py").read_text(encoding="utf-8")

def test_ai_mask_path_is_read_in_one_place(self):
    self.assertEqual(SOURCE.count("manifest.ai_mask"), 1)
    seam = SOURCE.index("def ai_mask_path(")
    next_def = SOURCE.index("\n    def ", seam + 1)
    self.assertTrue(seam < SOURCE.index("manifest.ai_mask") < next_def)

def test_reveal_and_load_use_the_seam(self):
    for method in ("def reveal_ai(", "def load_ai_mask("):
        start = SOURCE.index(method)
        body = SOURCE[start:SOURCE.index("\n    def ", start + 1)]
        with self.subTest(method):
            self.assertIn("self.ai_mask_path()", body)
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_ai_mask_seam.py" -v`. Expected: FAIL (count is 2).
- [ ] **Step 3: Add `ai_mask_path()`** immediately before `reveal_ai`, and route both callers through it. Its docstring says that Level 2 replaces this method with a fetch from the reveal service.
- [ ] **Step 4: Run** the new test, then SLICER_TEST `test_ai_segment_normalization_slicer.py` and SLICER_TEST `test_reopen_slicer.py`. Expected: all PASS.
- [ ] **Step 5: Commit.** Message: `"Read the AI mask path through one method so a reveal service can replace it"`.

### Task 3: Studio mode (Level 1 lockdown)

**Files:**
- Create: `MyomaReviewLite/MyomaReviewLiteLib/studio_mode.py`
- Modify: `MyomaReviewLite/MyomaReviewLite.py`: `setup()` (~1178), `enter()` (2065), `exit()` (2086), `choose_manifest()` (2218), and the manifest button (1591)
- Test: `MyomaReviewLite/tests/test_studio_mode.py` (pure), `MyomaReviewLite/tests/test_studio_mode_slicer.py`

**Interfaces:**
- Produces:
  - `STUDIO_ENV = "MYOMA_REVIEW_LITE_STUDIO"`
  - `is_studio_mode(environ: Mapping[str, str] | None = None) -> bool`: `None` means `os.environ`
  - `LOCKED_ACTION_PATTERN = re.compile(r"^(File|Edit|View|Window|Help)\w*Action$")`
  - `locked_action_names(names: Iterable[str]) -> list[str]`: the names that match, in their original order
  - `lock_down(main_window) -> None`, which needs Qt and does four things:
    1. hides the menu bar, status bar, every `QToolBar` and every `QDockWidget` except `PanelDockWidget`
    2. disables every `QAction` whose `objectName` matches and clears its shortcuts
    3. calls `setAcceptDrops(False)`
    4. is safe to call repeatedly

- [ ] **Step 1: Write the failing pure tests.**

```python
def test_only_exact_one_turns_studio_mode_on(self):
    for value, expected in (("1", True), ("0", False), ("", False), ("true", False), ("yes", False)):
        with self.subTest(value=value):
            self.assertIs(studio_mode.is_studio_mode({"MYOMA_REVIEW_LITE_STUDIO": value}), expected)
    self.assertFalse(studio_mode.is_studio_mode({}))

def test_locked_actions_cover_file_window_and_settings(self):
    names = ["FileAddDataAction", "WindowPythonInteractorAction", "EditApplicationSettingsAction",
             "ViewExtensionsManagerAction", "HelpAboutAction", "MyomaUndoAction", ""]
    self.assertEqual(studio_mode.locked_action_names(names), names[:5])
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_studio_mode.py" -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `studio_mode.py`.** `lock_down` imports `qt` inside the function, so the pure tests import without Slicer.
- [ ] **Step 4: Write the failing Slicer test** `test_studio_mode_slicer.py`, following `test_widget_layout_slicer.py`'s `_new_widget()` pattern. Set `os.environ["MYOMA_REVIEW_LITE_STUDIO"] = "1"` before building the widget, and stub `qt.QFileDialog.getOpenFileName` and `slicer.util.selectModule` to record their calls. Asserts:
  - after `widget.enter()` then `widget.exit()`: `mainWindow().menuBar().visible` is `False`, and the Python console dock (`PythonConsoleDockWidget`) is not visible
  - every main-window `QAction` named by `locked_action_names(...)` is disabled with no shortcuts, and at least one locked name contains `"Python"`; otherwise print the action names and fail, because Slicer named them differently
  - `mainWindow().acceptDrops()` is `False`
  - `widget.manifest_button.visible` is `False`, and `widget.choose_manifest()` records no dialog call
  - after `exit()` and processing events (`slicer.app.processEvents()`), `selectModule` was called with `"MyomaReviewLite"`
  - a second section with the variable set to `"0"`: after `enter()`/`exit()` the menu bar is visible again (today's behavior), and `choose_manifest()` does call the stub
  - the test ends with `slicer.app.exit(0)` on success and `exit(1)` on failure, like the others.
- [ ] **Step 5: Run** SLICER_TEST `test_studio_mode_slicer.py`. Expected: FAIL (no studio behavior yet).
- [ ] **Step 6: Wire it into the widget.** In studio mode:
  - `setup()` and `enter()` call `studio_mode.lock_down(slicer.util.mainWindow())`
  - `_build_ui` hides `manifest_button`
  - `choose_manifest()` returns before the dialog
  - `exit()` skips `_restore_panel_chrome()` and schedules `qt.QTimer.singleShot(0, lambda: slicer.util.selectModule("MyomaReviewLite"))`.
  Outside studio mode, every path is unchanged.
- [ ] **Step 7: Run** SLICER_TEST `test_studio_mode_slicer.py`, SLICER_TEST `test_widget_layout_slicer.py`, SLICER_TEST `test_action_bar_slicer.py` and `test_studio_mode.py`. Expected: all PASS. Then rebuild the image (Task 1 Step 3) and repeat Task 1 Step 4 for `test_studio_mode_slicer.py`. Expected: PASS on Linux too.
- [ ] **Step 8: Commit.** Message: `"Add studio mode: lock Slicer's menus, console, file dialogs and module switching"`.

### Task 4: Portal core logic

**Files:**
- Create: `studio/portal/portal_core.py`
- Test: `tests/test_studio_portal_core.py` (adds `studio/portal` to `sys.path`)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Reader: id: str; display_name: str; password_hash: str; admin: bool`
  - `class PortalError(Exception)`
  - `load_readers(path: Path) -> dict[str, Reader]`: raises `PortalError` on a missing file, invalid JSON, a bad id or a duplicate. The file format is `{"readers": [{"id", "display_name", "password_hash", "admin"}]}`.
  - `save_readers(path: Path, readers: dict[str, Reader]) -> None`: atomic write
  - `hash_password(password: str) -> str`, `verify_password(password: str, stored: str) -> bool` (constant-time compare)
  - `sign_session(reader_id: str, secret: bytes, now: float) -> str`, `read_session(token: str | None, secret: bytes, now: float) -> str | None`
  - `class LoginThrottle: is_locked(user, now) -> bool; record_failure(user, now) -> None; reset(user) -> None`
  - `authorize(reader: Reader, forwarded_uri: str) -> bool`
  - `dataset_available(data_root: Path) -> bool`: true when it is a directory containing at least one `sub-*` entry
  - `landing_path(reader: Reader) -> str`: the Global Constraints viewer path for readers, and `/admin` for admins
  - `@dataclass class AuthResult: status: int; location: str | None`, and `@dataclass class LoginResult: ok: bool; reader: Reader | None; message: str | None`
  - `class PortalApp(readers, secret, data_root, readers_root, clock=time.time)` with methods:
    - `login(username, password) -> LoginResult`
    - `check_auth(token, forwarded_uri) -> AuthResult`: returns 302 to `/login` for no or invalid session, 302 to `/unavailable` when the dataset is unavailable, 403 when not authorized, otherwise 200
    - `reader_for(token) -> Reader | None`
    - `progress() -> list[dict]`: one dict per non-admin reader, `{"id", "display_name", "total", "counts", "error"}`, from `MyomaReviewLiteLib.reader_queue.queue_report(readers_root / id, data_root=data_root)`; `error` is set instead when the report raises

- [ ] **Step 1: Write the failing tests** (`now` is a fixed float; readers use `hash_password("pw")`):

```python
def test_password_round_trip_and_format(self):
    stored = portal_core.hash_password("s3cret")
    self.assertTrue(stored.startswith("scrypt$16384$8$1$"))
    self.assertTrue(portal_core.verify_password("s3cret", stored))
    self.assertFalse(portal_core.verify_password("S3cret", stored))

def test_session_rejects_tamper_and_expiry(self):
    token = portal_core.sign_session("mostafa", b"k" * 32, now=1000.0)
    self.assertEqual(portal_core.read_session(token, b"k" * 32, now=1000.0 + 43199), "mostafa")
    self.assertIsNone(portal_core.read_session(token, b"k" * 32, now=1000.0 + 43201))
    self.assertIsNone(portal_core.read_session(token, b"x" * 32, now=1001.0))
    self.assertIsNone(portal_core.read_session(token[:-1] + ("0" if token[-1] != "0" else "1"), b"k" * 32, now=1001.0))
    self.assertIsNone(portal_core.read_session(None, b"k" * 32, now=1001.0))

def test_throttle_locks_after_five_failures_for_ten_minutes(self):
    t = portal_core.LoginThrottle()
    for i in range(5): t.record_failure("ali", 100.0 + i)
    self.assertTrue(t.is_locked("ali", 105.0))
    self.assertFalse(t.is_locked("ali", 104.0 + 601))
    t2 = portal_core.LoginThrottle()
    for i in range(5): t2.record_failure("ali", 100.0 + i * 200)   # spread beyond the 600 s window
    self.assertFalse(t2.is_locked("ali", 900.0))

def test_authorize_whole_segment_and_normalized(self):
    ali = Reader("ali", "Ali", "-", False)
    self.assertTrue(portal_core.authorize(ali, "/ali/vnc.html?x=1"))
    self.assertTrue(portal_core.authorize(ali, "/ali/websockify"))
    for uri in ("/ali2/vnc.html", "/reader2/", "/ali/../reader2/vnc.html", "/ali/%2e%2e/reader2/", "/admin", "/"):
        with self.subTest(uri=uri):
            self.assertFalse(portal_core.authorize(ali, uri))
    self.assertTrue(portal_core.authorize(Reader("boss", "B", "-", True), "/ali/vnc.html"))

def test_check_auth_outcomes(self):
    # app over a temp data_root containing sub-001/, readers {"mostafa"}, secret b"k"*32, clock=lambda: 1000.0
    token = portal_core.sign_session("mostafa", b"k" * 32, now=1000.0)
    self.assertEqual(app.check_auth(None, "/mostafa/").location, "/login")
    self.assertEqual(app.check_auth(token, "/reader2/").status, 403)
    self.assertEqual(app.check_auth(token, "/mostafa/vnc.html").status, 200)
    shutil.rmtree(data_root / "sub-001")
    self.assertEqual(app.check_auth(token, "/mostafa/").location, "/unavailable")

def test_login_is_case_insensitive_and_messages_are_exact(self):
    self.assertTrue(app.login("Mostafa", "pw").ok)
    self.assertEqual(app.login("mostafa", "nope").message, "Wrong username or password")
    self.assertEqual(app.login("nobody", "pw").message, "Wrong username or password")
    for _ in range(5): app.login("mostafa", "nope")
    self.assertEqual(app.login("mostafa", "pw").message, "Too many attempts. Try again in 10 minutes.")

def test_load_readers_rejects_bad_files(self):
    ok = '{"id": "ali", "display_name": "Ali", "password_hash": "-", "admin": false}'
    for text in ("", "{", '{"readers": [{"id": "Bad Id", "display_name": "x", "password_hash": "-", "admin": false}]}',
                 '{"readers": [' + ok + "," + ok + "]}"):
        with self.subTest(text=text[:20]), self.assertRaises(portal_core.PortalError):
            path.write_text(text, encoding="utf-8"); portal_core.load_readers(path)
    with self.assertRaises(portal_core.PortalError):
        portal_core.load_readers(path.with_name("missing.json"))

def test_progress_reports_queue_errors_per_reader(self):
    # readers_root has no queue.json for mostafa
    self.assertEqual(app.progress()[0]["id"], "mostafa")
    self.assertIsNotNone(app.progress()[0]["error"])
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_studio_portal_core.py" -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `portal_core.py`.**
  - Session token: `base64url(json {"r": id, "exp": now + 43200})` + `"."` + `hmac.new(secret, payload, "sha256").hexdigest()`; compare with `hmac.compare_digest`.
  - `authorize`: `posixpath.normpath(urllib.parse.unquote(urlsplit(uri).path))`, then compare the first segment.
  - `progress` imports `reader_queue` from the `MyomaReviewLiteLib` it finds on `sys.path`. The tests add `MyomaReviewLite/` to `sys.path`.
- [ ] **Step 4: Run** the same command. Expected: all PASS.
- [ ] **Step 5: Commit.** Message: `"Add the studio portal's login, session and authorization logic"`.

### Task 5: Portal HTTP server and CLI

**Files:**
- Create: `studio/portal/studio_portal.py`, `studio/portal/Dockerfile`
- Test: `tests/test_studio_portal_server.py`

**Interfaces:**
- Consumes: everything in Task 4's Produces.
- Produces:
  - `make_server(app: PortalApp, host: str, port: int, log_path: Path) -> ThreadingHTTPServer`
  - CLI `python studio_portal.py serve --data-dir DIR --data-root DIR --readers-root DIR [--port 9000]`. `DIR` holds `readers.json`, `secret.key` and `access.log`.
  - CLI `python studio_portal.py set-password READER --data-dir DIR [--name NAME] [--admin]`. It prompts twice with `getpass`, creates `readers.json` and a 32-byte `secret.key` if missing, and adds or updates the reader.
  - Routes:
    - `GET /`: 302 to `/login`
    - `GET /login`: the form
    - `POST /login`: urlencoded `username`, `password`. Success sets the cookie and returns 302 to `landing_path`; failure re-shows the form with the message, status 401.
    - `GET /logout`: clears the cookie, 302 to `/login`
    - `GET /auth`: uses `X-Forwarded-Uri` and the cookie; maps `AuthResult` to its status and `Location`
    - `GET /admin`: admins only, otherwise 302 to `/login`. An HTML table of `progress()`.
    - `GET /unavailable`: status 503 with the exact unavailable copy
  - Access log line: `"<ISO time>\t<event>\t<reader or ->\t<ip>"`, with event in `login_ok, login_failed, login_locked, logout`. The IP comes from the first `X-Forwarded-For` value, else the peer address.

- [ ] **Step 1: Write the failing tests.** Start `make_server` on port 0 in a thread, over a temp data dir with readers `mostafa` (pw `"pw"`) and admin `boss`. Use `http.client` so redirects are not followed.
  - `test_login_sets_cookie_and_lands_on_viewer`: POST good credentials → 302, `Location` equals `landing_path`, and `Set-Cookie` contains `studio_session=`, `HttpOnly`, `Secure`, `SameSite=Lax` and `Max-Age=43200`.
  - `test_auth_guards_other_readers_websocket`: with mostafa's cookie, `GET /auth` with `X-Forwarded-Uri: /reader2/websockify` → 403, and `/mostafa/websockify` → 200.
  - `test_auth_without_cookie_redirects_to_login`: → 302 with `Location: /login`.
  - `test_admin_page_lists_readers_only_for_admin`: boss sees `"mostafa"` in the body; mostafa gets 302 to `/login`.
  - `test_unavailable_page_copy`: body contains `"Data drive unavailable, tell the study admin"`, status 503.
  - `test_access_log_records_failures`: after a bad login, the log has one line whose second field is `login_failed`.
  - `test_serve_refuses_malformed_readers_file`: run `subprocess.run([sys.executable, "studio/portal/studio_portal.py", "serve", "--data-dir", d, ...])` with `readers.json` set to `"{"`. Expected: exit code 2, stderr contains `readers.json`, and nothing listens.
- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_studio_portal_server.py" -v`. Expected: FAIL.
- [ ] **Step 3: Implement `studio_portal.py`.**
  - HTML is inline, minimal and readable, with a centred card. The login form shows the message slot.
  - `serve` exits 2 with `error: <PortalError text>` on a bad `readers.json`.
- [ ] **Step 4: Write `studio/portal/Dockerfile`.**
  - Base `python:3.12-slim`.
  - Copy `studio/portal/` and `MyomaReviewLite/MyomaReviewLiteLib/` (build context: the repo root), and set `PYTHONPATH` to include the module's parent.
  - `CMD ["python", "/app/studio_portal.py", "serve", "--data-dir", "/portal", "--data-root", "/data", "--readers-root", "/readers", "--port", "9000"]`
- [ ] **Step 5: Run** the server tests and Task 4's tests. Expected: all PASS. Then run `docker build -f studio/portal/Dockerfile -t myoma-studio-portal .`. Expected: success.
- [ ] **Step 6: Commit.** Message: `"Add the studio portal server: login, forward-auth, admin progress, access log"`.

### Task 6: Generated compose file and Caddyfile

**Files:**
- Create: `studio/studio_config.py`
- Test: `tests/test_studio_config.py`

**Interfaces:**
- Consumes: `portal_core.load_readers`, `Reader`.
- Produces:
  - `render_compose(reader_ids: list[str], dataset_root: str, readers_root: str, portal_data_dir: str) -> str`
  - `render_caddyfile(reader_ids: list[str], server_address: str) -> str`
  - CLI `python studio/studio_config.py render --server-address 192.168.0.201 [--review-config review-config.json] [--portal-data studio/portal-data] [--readers-root studio/readers] [--out studio/generated]`. It writes `docker-compose.yml` and `Caddyfile`. Admin readers get no container.
- Compose content:
  - Services:
    - `caddy`: `caddy:2.8`, publishes `443:443`, mounts the generated Caddyfile and a `caddy-data` volume
    - `portal`: image `myoma-studio-portal`; mounts the portal data (read-write), the dataset at `/data` (read-only) and the readers root at `/readers` (read-only)
    - one `slicer-<id>` per non-admin reader: image `myoma-studio-slicer`; mounts the dataset at `/data` (read-only), `<readers_root>/<id>` at `/review` (read-write) and a named volume `settings-<id>` at `/home/reader/.config`; `MYOMA_REVIEW_LITE_STUDIO=1`
  - All services: `restart: unless-stopped`, on network `studio`. Only Caddy publishes ports.
  - Paths are written with forward slashes and quoted.
- Caddyfile per reader:
  ```
  handle /<id>/* {
      forward_auth portal:9000 { uri /auth }
      uri strip_prefix /<id>
      reverse_proxy slicer-<id>:6080
  }
  ```
  Plus `handle` for `/login* /logout /admin* /unavailable` → `reverse_proxy portal:9000`, and `handle / { redir /login }`, inside a site block `<server_address> { tls internal ... }`.

- [ ] **Step 1: Write the failing tests:**
  - `test_compose_has_one_slicer_per_reader_and_readonly_dataset`: for ids `["ali", "mostafa"]`, the output contains `slicer-ali:` and `slicer-mostafa:`, and contains the dataset mount ending `:/data:ro` three times (two readers and the portal). `"443:443"` appears exactly once, and `"6080:"` never appears.
  - `test_caddyfile_guards_every_reader_route`: for each id, the block `handle /<id>/*` is followed by `forward_auth portal:9000` before `reverse_proxy slicer-<id>:6080`, and the site starts with `192.168.0.201 {` and contains `tls internal`.
  - `test_render_skips_admins_and_rejects_bad_ids`: the CLI over a `readers.json` with admin `boss` writes no `slicer-boss`. `render_compose(["Bad Id"], ...)` raises `ValueError`.
  - `test_render_reads_dataset_root_from_review_config`: a temp `review-config.json` with `"dataset_root": "/srv/data"` → the compose output contains `"/srv/data:/data:ro"`.
- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_studio_config.py" -v`. Expected: FAIL.
- [ ] **Step 3: Implement `studio_config.py`.** Write YAML by hand with string templates; no PyYAML. Read `dataset_root` the same way `reader_queue.load_review_config` does, so a package-relative path resolves the same.
- [ ] **Step 4: Run** the tests, then `python -m unittest discover -s tests -p "test_package_portability.py" -v` with a rendered `studio/generated/` present. Expected: the new tests PASS, and portability fails only on the pre-existing `mostafa/` error.
- [ ] **Step 5: Commit.** Message: `"Generate the studio's compose file and Caddyfile from readers.json"`.

### Task 7: Nightly backup

**Files:**
- Create: `studio/backup-readers.ps1`
- Test: `tests/test_studio_backup.py`

**Interfaces:**
- Produces: `backup-readers.ps1 -ReadersRoot <dir> -PortalDataDir <dir> -BackupRoot <dir> [-Keep 7]`.
  - It creates `<BackupRoot>\readers-yyyyMMdd-HHmmss-fff\` holding a `readers\` mirror (robocopy `/E`; exit codes below 8 count as success) and a copy of `readers.json`.
  - It then deletes the oldest `readers-*` folders beyond `-Keep`.
  - It exits non-zero if robocopy reports 8 or more, or if `ReadersRoot` is missing.

- [ ] **Step 1: Write the failing tests** (run with `pwsh -NoProfile -File`, like `test_project_tools.py` runs its scripts):
  - `test_backup_copies_results_and_readers_file`: the latest folder contains `readers/mostafa/case1/annotations.json` and `readers.json`.
  - `test_backup_keeps_only_the_newest_seven`: run 9 times → exactly 7 `readers-*` folders, and the 2 oldest are gone.
  - `test_backup_fails_on_missing_source`: a non-existent `-ReadersRoot` → non-zero exit code and nothing created.
- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_studio_backup.py" -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `backup-readers.ps1`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `"Add the studio's nightly results backup"`.

### Task 8: Setup guide and on-network acceptance

**Files:**
- Create: `studio/README.md`
- Modify: `README.md` (one line linking `studio/README.md`)

- [ ] **Step 1: Write `studio/README.md`.** Sections, each as numbered commands or clicks:
  1. **Router:** find the model on the sticker or Status page, record it, and set a DHCP address reservation for the server at `192.168.0.201`. Turn UPnP off and add no port forwarding.
  2. **Server:**
     - power plan set to never sleep
     - Docker Desktop with "Start when you sign in" and Windows automatic sign-in
     - firewall: `New-NetFirewallRule -DisplayName "Myoma studio" -Direction Inbound -Protocol TCP -LocalPort 443 -RemoteAddress 192.168.0.0/24 -Action Allow`, then check `Get-NetFirewallRule` for any broader Docker-created rule on 443 and restrict it
  3. **Build the images** (Task 1 and Task 5 commands).
  4. **Add a reader:**
     1. `reader_queue.py create`
     2. `set-password` (run through `docker compose run --rm portal ...` or local Python)
     3. `studio_config.py render`
     4. `docker compose -f studio/generated/docker-compose.yml up -d`
  5. **Laptop certificate:** export Caddy's root certificate from the `caddy-data` volume, then either install it on each laptop or accept the browser warning once.
  6. **Backup:** register a daily 02:00 task with `Register-ScheduledTask` running `backup-readers.ps1` to the second USB drive.
  7. **Data drive unplugged:** reconnect it, then `docker compose restart`.
  8. **Known limits:** two logins as one reader share one screen; Task 1 Step 5's load-time result goes here.
- [ ] **Step 2: Bring the studio up** with two real readers (`docker compose up -d`). Expected: `docker compose ps` shows `caddy`, `portal` and both `slicer-*` as running.
- [ ] **Step 3: Run the acceptance checklist on the real network** (spec "Testing", manual). Record each result as a checked line in `studio/README.md` under "Acceptance 2026-10-…":
  - [ ] laptop A (wired) and laptop B (WiFi) log in as different readers at the same time
  - [ ] each completes a case: box, reveal, correct, FIGO, finish
  - [ ] A cannot open B's path, including typing `/<b>/vnc.html` directly (403)
  - [ ] keys A, Q, 1–4, Ctrl+Z and Alt-drag reach Slicer in the browser (spec open item 2). If any is swallowed, note it and add an alternative key in studio mode before calling this done.
  - [ ] brushing is acceptable over WiFi
  - [ ] after pulling the cable or WiFi mid-case and reconnecting, nothing is lost
  - [ ] after `docker exec slicer-<a> pkill -f SlicerApp` mid-case, Slicer comes back within about 30 s with the reader's draft
  - [ ] after rebooting the server, the studio comes back without manual steps
  - [ ] the 5 wrong-password lockout shows the exact message
  - [ ] the admin page shows both readers' progress
- [ ] **Step 4: Full regression.**
  - Every pure module test in `MyomaReviewLite/tests` (as in the Direction A plan's list, plus `test_studio_mode.py` and `test_ai_mask_seam.py`).
  - The repo-level `tests/`.
  - Every SLICER_TEST on Windows.
  Expected: all PASS, apart from the pre-existing `mostafa/` error.
- [ ] **Step 5: Commit.** Message: `"Document studio setup and record on-network acceptance"`.
