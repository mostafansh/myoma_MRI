# Slicer Studio Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the review studio into a generic `slicer-studio/` platform that runs any one Slicer module per deployment, described by a `studio-module.json` manifest. MyomaReviewLite becomes its first module and a sample `hello-module` its second.

**Architecture:**
- **Pure core.** `slicer-studio/studio_lib/` (manifest, deployment config, rendering) and `slicer-studio/portal/` stay pure Python 3.12 standard library. The portal reads module progress only from `progress.json` files.
- **One generic image.** A single generic Slicer image mounts the module read-only and runs a studio-owned lockdown at startup.
- **One CLI.** `slicer-studio/studio.py` drives deployments that live outside the repo.
- **Myoma changes.** MyomaReviewLite loses its lockdown code, keys its own studio tweaks on `SLICER_STUDIO`, and writes `progress.json`.

**Tech Stack:**
- Python 3.12 stdlib and `unittest`
- 3D Slicer 5.10.0 (PythonQt)
- Docker Desktop (WSL2): Ubuntu 22.04 image with Xvfb/x11vnc/noVNC, Caddy 2.8
- PowerShell 7

**Spec:** `docs/superpowers/specs/2026-10-22-slicer-studio-split-design.md` (builds on `docs/superpowers/specs/2026-10-21-review-studio-design.md`)

## Global Constraints

- **Boundary:** no file or path under `slicer-studio/` contains `myoma` in any case. The boundary test builds that word from pieces, so the test file itself passes.
- **Vocabulary:** the studio says **user** and **workspace**, never reader or study, in code, CLI and README. The only exception is quoting a module's own tool in an example.
- **Dependencies:** the portal, `studio_lib`, `studio.py` and all `slicer-studio/tests` use the Python standard library only. The portal image is `python:3.12-slim` with no pip installs.
- **No drive-letter paths** in committed or on-disk `.cmd .json .md .ps1 .py .svg .txt` files under the repo (`tests/test_package_portability.py`). Deployments live outside the repo.
- **Manifest** (`studio-module.json`):
  - Keys: `schema` (=1), `name` (`^[a-z][a-z0-9-]{0,31}$`), `title`, `module_paths` (non-empty list of folders inside the manifest folder), `start_module` (required), `uses_dataset` (bool, default false), `env` (string values; placeholders only `{workspace}`→`/workspace`, `{dataset}`→`/data`), `workspace_requires` (list of relative file names).
  - Unknown keys are an error.
- **Deployment config** (`<deploy-dir>/studio.json`):
  - Keys: `schema` (=1), `module`, `server_address`, `dataset`.
  - `dataset` is required exactly when `uses_dataset` is true.
  - Paths are absolute or relative to `<deploy-dir>`.
- **Container contract:**
  - `module_paths[n]` → `/modules/n` read-only
  - `users/<id>` → `/workspace` read-write
  - dataset → `/data` read-only, only when `uses_dataset` is true
  - environment: `SLICER_STUDIO=1`, `STUDIO_START_MODULE`, `STUDIO_MODULE_PATHS` (`/modules/0:/modules/1:...`), plus the expanded manifest `env`
- **progress.json:** an object with exactly `total`, `done`, `in_progress`, `needs_attention`, each a non-negative `int` (`bool` rejected). Anything else displays as `—`.
- **Carried over unchanged from the built studio:**
  - id regex `^[a-z][a-z0-9_-]{0,31}$` and reserved ids `admin login logout auth unavailable static`
  - scrypt `scrypt$16384$8$1$…`
  - cookie `studio_session`, 43200 s, `HttpOnly; Secure; SameSite=Lax; Path=/`
  - lockout of 5 failures in 600 s → 600 s, safe under concurrent attempts
  - live reload of the users file
  - log-field sanitising
  - 8-character minimum password
  - exact copy `"Wrong username or password"`, `"Too many attempts. Try again in 10 minutes."`, `"Data drive unavailable, tell the study admin"`
  - landing path `/<id>/vnc.html?autoconnect=1&reconnect=1&resize=scale&quality=6&compression=2&path=<id>/websockify`
  - Caddy `<server_address>:443:443`, `tls internal`, global `default_sni <server_address>`, per-user `route { forward_auth portal:9000 { uri /auth }  uri strip_prefix /<id>  reverse_proxy slicer-<id>:6080 }`
  - networks: `edge` (caddy, portal) and per user `net-<id>` with `internal: true` (caddy, slicer-<id>)
  - `init: true`, `restart: unless-stopped`
- **Compose project name:** `studio-` + the deploy-dir folder name, lowercased, with runs of characters outside `[a-z0-9]` turned into a single `-`, edge `-` trimmed, and the whole name cut to 63 characters.
- **Test commands:**
  - Studio tests, from the repo root: `python -m unittest discover -s slicer-studio/tests -p "<file>" -v`
  - Module pure tests, from `MyomaReviewLite/`: `python -m unittest discover -s tests -p "<file>" -v`
  - **SLICER_TEST `<dir>` `<file>`** (PowerShell, from `<dir>`): `$env:MYOMA_REVIEW_LITE_CASE=$null; $env:MYOMA_TEST_SCRIPT="tests/<file>"; $env:MYOMA_TEST_LOG="$env:TEMP\st_<file>.log"; Start-Process -Wait "$env:LOCALAPPDATA\slicer.org\3D Slicer 5.10.0\Slicer.exe" -ArgumentList '--no-splash','--python-script','tests/run_slicer_test.py'; Get-Content "$env:TEMP\st_<file>.log" -Tail 15`
  - **IMAGE_TEST:** the same Slicer test run inside `slicer-studio-slicer`:
    - mount the module or test folder at `/modules/0`
    - set `-e PYTHONDONTWRITEBYTECODE=1 -e MYOMA_TEST_SCRIPT=... -e MYOMA_TEST_LOG=/tmp/t.log`
    - `--entrypoint bash ... -c "cd /modules/0 && xvfb-run -a -s '-screen 0 1920x1080x24' Slicer --no-splash --python-script tests/run_slicer_test.py; tail -15 /tmp/t.log"`
    - Myoma's Slicer tests that need a queue also need `-e MYOMA_REVIEW_LITE_TEST_QUEUE=/testq/queue.json` with a scratch queue mounted at `/testq`.
- **Known pre-existing failures** (must not change): `tests/test_package_portability.py::test_runtime_files_do_not_reference_shortcuts` and 5 `tests/test_project_tools.py` WinError 3 errors, all caused by the missing gitignored `mostafa/`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A `module_paths` entry that escapes the manifest folder** through `..` or a symlink or junction must be rejected, never mounted. (Task 2: `test_module_path_escape_is_rejected`)
2. **A deploy-dir folder name with spaces, capitals or punctuation** (`My Pilot (2026)`) must still give a valid compose project name (`studio-my-pilot-2026`). (Task 2: `test_project_name_is_sanitised`)
3. **A `progress.json` that is malformed, partial, has negative numbers or booleans, or is huge** shows `—` and never breaks `/admin` or `status`. (Task 1: `test_read_progress_rejects_bad_files`)
4. **A dataset path with spaces or `$`** renders as a quoted compose path, with `$` escaped as `$$`. (Task 3: `test_paths_with_spaces_and_dollar_are_quoted`)
5. **A user listed in `users.json` whose workspace folder is missing or incomplete at render time** gets a warning line, the other users still render, and `/admin` shows `—` for that user. (Task 3: `test_render_warns_for_unprepared_users`; Task 1: covered by test 3's missing-file case)

## File Structure

| File | Responsibility |
|---|---|
| `slicer-studio/portal/portal_core.py` | Moved from `studio/portal/`; renamed to users and workspaces; adds `read_progress` |
| `slicer-studio/portal/studio_portal.py` | Moved; `serve` with `--users-root`, optional `--data-root`, `STUDIO_TITLE`; `set-password` removed (the CLI owns it) |
| `slicer-studio/portal/Dockerfile` | `python:3.12-slim`, standard library only |
| `slicer-studio/studio_lib/manifest.py` | `ModuleManifest`, `load_manifest`, `expand_env` |
| `slicer-studio/studio_lib/deployment.py` | `Deployment`, `load_deployment`, `project_name`, `check_address`, `missing_workspace_files` |
| `slicer-studio/studio_lib/render.py` | Moved from `studio/studio_config.py`; renders from a `Deployment` |
| `slicer-studio/studio.py` | CLI: `init`, `add-user`, `remove-user`, `render`, `up`, `down`, `restart`, `status` |
| `slicer-studio/slicer/Dockerfile`, `start-studio.sh` | Generic image; `lockdown/studio_lockdown.py` and `lockdown/startup.py` baked in |
| `slicer-studio/backup-workspaces.ps1` | Moved and renamed backup |
| `slicer-studio/examples/hello-module/` | `studio-module.json`, `HelloStudio/HelloStudio.py` |
| `slicer-studio/README.md` | Generic setup guide (from `studio/README.md`) |
| `slicer-studio/tests/` | All studio tests, including the boundary test |
| `studio-module.json` (repo root) | MyomaReviewLite's manifest |
| `MyomaReviewLite/...` | Drop `studio_mode.py`; `SLICER_STUDIO`; `progress_counts` and `progress.json` |
| `studio/`, `tests/test_studio_*.py` | Deleted once their replacements land |

---

### Task 1: Portal moved and made generic

**Files:**
- Create: `slicer-studio/portal/{portal_core.py,studio_portal.py,Dockerfile}`, by `git mv` from `studio/portal/` and then editing
- Create: `slicer-studio/tests/{test_portal_core.py,test_portal_server.py,test_boundary.py}`, by `git mv` from `tests/test_studio_portal_core.py` and `tests/test_studio_portal_server.py` and then editing
- Modify: `.dockerignore` (add `slicer-studio/**/__pycache__` if the existing `**/` patterns don't already cover it)

**Interfaces:**
- Produces, in `portal_core`:
  - `USER_ID_RE`, `RESERVED_IDS`
  - `@dataclass(frozen=True) class User: id: str; display_name: str; password_hash: str; admin: bool`
  - `load_users(path) -> dict[str, User]`, `save_users(path, users) -> None`. The file format is `{"users": [...]}`, with the same validation as today's `load_readers`.
  - `hash_password`, `verify_password`, `sign_session`, `read_session`, `LoginThrottle`: unchanged
  - `authorize(user: User, forwarded_uri: str) -> bool`, `landing_path(user: User) -> str`
  - `PROGRESS_KEYS = ("total", "done", "in_progress", "needs_attention")`
  - `read_progress(path: Path) -> dict[str, int] | None`
  - `PortalApp(users, secret, workspaces_root: Path, data_root: Path | None = None, clock=time.time, users_path: Path | None = None)`, with methods:
    - `login`, `user_for(token)`
    - `check_auth`: skips the dataset check when `data_root` is None
    - `progress() -> list[dict]`: rows `{"id", "display_name", "progress": dict | None}`, non-admin users only
- Produces, for `studio_portal`:
  - `serve --data-dir DIR --users-root DIR [--data-root DIR] [--port 9000]`, reading `users.json` and `secret.key` from `--data-dir` and logging to `<data-dir>/access.log`
  - The page title is `os.environ.get("STUDIO_TITLE", "Slicer Studio")` and is HTML-escaped.
  - The `set-password` subcommand is removed; Task 4's CLI owns user management.

- [ ] **Step 1: Move the files.**
  - `git mv` the portal files and the two test files to their new places.
  - In the moved tests, change the `sys.path` setup to add `slicer-studio/portal`, and drop the `MyomaReviewLite` path.
  - Delete the tests that only covered `set-password` from `test_portal_server.py`. The 8-character minimum is re-tested in Task 4.
- [ ] **Step 2: Write the failing tests.**
  - Rename `Reader` to `User` and `readers` to `users` throughout the moved tests.
  - Replace the progress tests with:

```python
def test_read_progress_accepts_exact_shape(self):
    p = self.tmp / "progress.json"; p.write_text('{"total": 3, "done": 1, "in_progress": 1, "needs_attention": 0}', encoding="utf-8")
    self.assertEqual(portal_core.read_progress(p), {"total": 3, "done": 1, "in_progress": 1, "needs_attention": 0})

def test_read_progress_rejects_bad_files(self):
    p = self.tmp / "progress.json"
    for text in ("", "{", "[]", '{"total": 3}', '{"total": -1, "done": 0, "in_progress": 0, "needs_attention": 0}',
                 '{"total": true, "done": 0, "in_progress": 0, "needs_attention": 0}',
                 '{"total": 1, "done": 0, "in_progress": 0, "needs_attention": 0, "x": 1}',
                 '{"total": "3", "done": 0, "in_progress": 0, "needs_attention": 0}'):
        with self.subTest(text=text):
            p.write_text(text, encoding="utf-8"); self.assertIsNone(portal_core.read_progress(p))
    p.write_bytes(b"{" + b" " * (2 * 1024 * 1024) + b"}")          # > 1 MiB is refused unread
    self.assertIsNone(portal_core.read_progress(p))
    self.assertIsNone(portal_core.read_progress(self.tmp / "missing.json"))

def test_progress_rows_use_progress_json_only(self):
    # workspaces/ali/progress.json valid; workspaces/sara/ missing; admin boss excluded
    rows = {r["id"]: r for r in app.progress()}
    self.assertEqual(set(rows), {"ali", "sara"})
    self.assertEqual(rows["ali"]["progress"]["done"], 1)
    self.assertIsNone(rows["sara"]["progress"])

def test_check_auth_without_dataset_skips_unavailable(self):
    app = portal_core.PortalApp(users, b"k" * 32, workspaces_root=self.tmp, data_root=None, clock=lambda: 1000.0)
    self.assertEqual(app.check_auth(token, "/ali/vnc.html").status, 200)
```

  - In `test_portal_server.py`:
    - `/admin` shows `"—"` for a user without progress and the numbers for a user with it.
    - The page carries `STUDIO_TITLE` when set, escaped. Set it in `setUp` to `"Hello <b>"` and assert `<b>` is absent.
  - `test_boundary.py`:

```python
WORD = "myo" + "ma"
ROOT = Path(__file__).resolve().parents[1]   # slicer-studio/

def test_no_module_names_in_the_platform(self):
    offenders = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT).as_posix()
        if "__pycache__" in rel:
            continue
        if WORD in rel.lower():
            offenders.append(rel)
        elif path.is_file():
            try:
                if WORD in path.read_text(encoding="utf-8").lower():
                    offenders.append(rel)
            except UnicodeDecodeError:
                pass
    self.assertEqual(offenders, [])
```

- [ ] **Step 3: Run** `python -m unittest discover -s slicer-studio/tests -v`. Expected: FAIL (old names, `read_progress` missing, boundary offenders).
- [ ] **Step 4: Implement the renames, `read_progress` and the progress rows.**
  - `read_progress` refuses files over 1 MiB before reading and requires exactly `PROGRESS_KEYS`.
  - Make `data_root` optional.
  - Add `STUDIO_TITLE`.
  - Remove `set-password`, every `MyomaReviewLiteLib` reference and the nibabel/numpy pip line from the Dockerfile.
  - Keep all security behaviour byte-for-byte: messages, cookie attributes, throttle, reload, sanitising.
- [ ] **Step 5: Run** the studio tests. Expected: all PASS, and the boundary test passes for `portal/`. Then run `docker build -f slicer-studio/portal/Dockerfile -t slicer-studio-portal .` from the repo root. Expected: success, with no pip step in the build log.
- [ ] **Step 6: Commit.** Message: `"Move the portal into slicer-studio and make it module-agnostic: users, workspaces, progress.json"`.

### Task 2: Manifest and deployment config

**Files:**
- Create: `slicer-studio/studio_lib/{__init__.py,manifest.py,deployment.py}`
- Test: `slicer-studio/tests/{test_manifest.py,test_deployment.py}`

**Interfaces:**
- Consumes: `portal_core.USER_ID_RE`, `RESERVED_IDS`. Add `slicer-studio/portal` to `sys.path` in `studio_lib/__init__.py`.
- Produces, in `manifest.py`:
  - `class ConfigError(Exception)`. Its message always names the file and the key.
  - `@dataclass(frozen=True) class ModuleManifest: path: Path; name: str; title: str; module_dirs: tuple[Path, ...]; start_module: str; uses_dataset: bool; env: dict[str, str]; workspace_requires: tuple[str, ...]`. `module_dirs` holds absolute, resolved host paths.
  - `load_manifest(path: Path) -> ModuleManifest`
  - `expand_env(manifest: ModuleManifest) -> dict[str, str]`
- Produces, in `deployment.py`:
  - `@dataclass(frozen=True) class Deployment: root: Path; manifest: ModuleManifest; dataset: Path | None; server_address: str`, with properties `users_dir`, `portal_dir`, `generated_dir`, `config_path`.
  - `load_deployment(root: Path) -> Deployment`
  - `project_name(root: Path) -> str`
  - `check_address(text: str) -> None`, moved from `studio_config._check_address`
  - `missing_workspace_files(deployment: Deployment, user_id: str) -> list[str]`

- [ ] **Step 1: Write the failing tests.** Each builds its fixtures in a temp dir.

```python
def test_valid_manifest_loads(self):        # every key present; module_dirs resolved; defaults when optional keys absent
def test_unknown_key_is_an_error(self):     # {"...", "modul_paths": [...]} -> ConfigError mentioning "modul_paths"
def test_required_keys(self):               # each of schema/name/title/module_paths/start_module removed -> ConfigError naming it
def test_name_and_types(self):              # bad name "Myo Review"; module_paths "x" (not list) or []; uses_dataset "yes"; env value 3; schema 2
def test_module_path_escape_is_rejected(self):
    # "../outside", an absolute path, and a symlink/junction inside the folder pointing outside -> ConfigError
    # (create the link with os.symlink, falling back to `cmd /c mklink /J` on Windows; skipTest only if both are refused)
def test_module_path_must_be_a_folder(self): # missing or a file -> ConfigError
def test_env_placeholders(self):
    m = load_manifest(...)   # env {"A": "{workspace}/q.json", "B": "{dataset}"} with uses_dataset true
    self.assertEqual(expand_env(m), {"A": "/workspace/q.json", "B": "/data"})
    # {dataset} with uses_dataset false -> ConfigError; "{other}" -> ConfigError; literal "{{x}}" -> "{x}"
def test_workspace_requires_are_relative_names(self):  # "../x", "/abs", "" -> ConfigError

def test_deployment_loads_relative_and_absolute_paths(self)
def test_dataset_required_exactly_when_used(self)      # missing when used -> error; present when unused -> error
def test_bad_server_address(self)                      # same accept/reject cases as today's _check_address tests, now via check_address
def test_project_name_is_sanitised(self):
    self.assertEqual(project_name(Path("x") / "My Pilot (2026)"), "studio-my-pilot-2026")
    self.assertEqual(project_name(Path("x") / ("a" * 100)), ("studio-" + "a" * 100)[:63])
    self.assertEqual(project_name(Path("x") / "---"), "studio")   # nothing left -> bare "studio"
def test_missing_workspace_files(self)                 # lists names absent from users/<id>/
```

- [ ] **Step 2: Run** `python -m unittest discover -s slicer-studio/tests -p "test_manifest.py" -v` and the same for `test_deployment.py`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement.**
  - Resolve each module path with `Path.resolve()`, then require `resolved.is_relative_to(manifest_dir.resolve())`.
  - Expand placeholders with a single regex pass over `\{(workspace|dataset)\}`. Anything else in braces is an error, except a doubled `{{`/`}}`.
- [ ] **Step 4: Run** both files. Expected: PASS. Run the boundary test too.
- [ ] **Step 5: Commit.** Message: `"Add the studio's module manifest and deployment config"`.

### Task 3: Rendering from a deployment

**Files:**
- Create: `slicer-studio/studio_lib/render.py`, by `git mv` from `studio/studio_config.py` and then editing
- Test: `slicer-studio/tests/test_render.py`, by `git mv` from `tests/test_studio_config.py` and then editing

**Interfaces:**
- Consumes: `Deployment`, `expand_env`, `project_name`, `check_address`, `missing_workspace_files` (Task 2); `portal_core.load_users` and `USER_ID_RE` (Task 1).
- Produces:
  - `render_compose(deployment: Deployment, user_ids: list[str]) -> str`
  - `render_caddyfile(user_ids: list[str], server_address: str) -> str`: unchanged output
  - `@dataclass class RenderResult: user_ids: list[str]; warnings: list[str]`
  - `render(deployment: Deployment) -> RenderResult`: reads `portal/users.json`, renders the non-admin users, and writes `generated/docker-compose.yml` and `generated/Caddyfile`. Nothing is written if validation fails.

- Compose differences from today:
  - **Project name:** `name: <project_name(root)>`.
  - **Images:** `slicer-studio-slicer` and `slicer-studio-portal`.
  - **Portal mounts:** `<portal_dir>:/portal`, `<users_dir>:/workspaces:ro`, and `<dataset>:/data:ro` only if `uses_dataset`.
  - **Portal command:** `--users-root /workspaces`, plus `--data-root /data` only if `uses_dataset`.
  - **Portal environment:** `STUDIO_TITLE=<title>`.
  - **Each `slicer-<id>` service mounts:**
    - every `module_dirs[n]:/modules/n:ro`
    - `<users_dir>/<id>:/workspace`
    - the dataset, only if `uses_dataset`
    - `settings-<id>:/home/user/.config`
  - **Each `slicer-<id>` service environment:** `SLICER_STUDIO=1`, `STUDIO_START_MODULE`, `STUDIO_MODULE_PATHS` and the expanded manifest `env`.
  - **Quoting:** every host path is forward-slashed, JSON-quoted, with `$` written as `$$`.

- [ ] **Step 1: Write the failing tests.**
  - Adapt the moved tests to build a temp deployment with `init`-like fixtures: write `studio.json`, a manifest, `users.json` via `save_users`, and the workspace folders.
  - Keep, adapted:
    - network isolation
    - a single published `<addr>:443:443`
    - `default_sni`
    - `init: true`
    - the reserved and invalid id `ValueError`
    - the whole `DockerBackedTests` class (`caddy adapt` auth-before-strip, the old-template negative, `docker compose config`)
  - Add:

```python
def test_module_mounts_and_env(self):
    text = render_compose(dep, ["ali"])
    self.assertIn(f'{_q(mod_dir)}:/modules/0:ro', text)          # use the module's real resolved dir
    for line in ('SLICER_STUDIO: "1"', 'STUDIO_START_MODULE: "HelloStudio"', 'STUDIO_MODULE_PATHS: "/modules/0"',
                 'A: "/workspace/q.json"'):
        self.assertIn(line, text)
def test_dataset_only_when_used(self)               # uses_dataset false -> no ":/data:ro" and no "--data-root"
def test_paths_with_spaces_and_dollar_are_quoted(self):
    # dataset folder "data $HOME set" -> appears as "…/data $$HOME set" + ":/data:ro" inside double quotes
def test_render_warns_for_unprepared_users(self):
    # manifest workspace_requires ["queue.json"]; ali has it, sara's folder lacks it, tom's folder missing
    result = render(dep)
    self.assertEqual(result.user_ids, ["ali", "sara", "tom"])
    self.assertEqual(len(result.warnings), 2)
    self.assertTrue((dep.generated_dir / "docker-compose.yml").is_file())
def test_render_writes_nothing_on_invalid_users_file(self)   # users.json "{" -> ConfigError/PortalError surfaced, generated/ absent
```

- [ ] **Step 2: Run** `python -m unittest discover -s slicer-studio/tests -p "test_render.py" -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Drop `read_dataset_root`, the `review-config.json` handling and the CLI `main()`; the CLI moves to Task 4.
- [ ] **Step 4: Run** `test_render.py` with Docker running. Expected: PASS with no skips. Run the boundary test too.
- [ ] **Step 5: Commit.** Message: `"Render the studio's compose file and Caddyfile from a deployment and its module manifest"`.

### Task 4: The `studio.py` CLI

**Files:**
- Create: `slicer-studio/studio.py`
- Test: `slicer-studio/tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1 to 3.
- Produces: `main(argv: list[str] | None = None, *, getpass=getpass.getpass, run=subprocess.run) -> int`. Exit codes: 0 for OK, 2 for user or config errors, 1 for docker failures.
  - `init DEPLOY --module M --server-address A [--dataset D]`
    - Refuses a non-empty `DEPLOY`.
    - Refuses a `DEPLOY` inside the git work tree that contains `studio.py`, found by walking up to `.git`.
    - Writes `studio.json`, with paths stored exactly as given.
    - Creates `users/`, `portal/` with `secret.key` (32 bytes, mode 0600 where possible) and an empty `users.json`, and `generated/`.
  - `add-user DEPLOY ID [--name N] [--admin]`
    - Validates the id with `USER_ID_RE` and `RESERVED_IDS`.
    - For non-admins, creates `users/ID/` and refuses with exit 2 if `missing_workspace_files` is non-empty, naming the missing files.
    - Prompts twice. The passwords must match and be at least 8 characters.
    - Adds the user, or replaces the password if the user exists, through `save_users`.
  - `remove-user DEPLOY ID`: removes the user from `users.json` and keeps the workspace. Prints `Run "studio.py up DEPLOY" to stop their Slicer.`
  - `render DEPLOY`: prints the warnings and the generated paths.
  - `up | down | restart DEPLOY`:
    - `up`: render, then `docker compose -f generated/docker-compose.yml up -d --remove-orphans`
    - `down`: `docker compose ... down`, never `-v`
    - `restart`: `docker compose ... restart`
  - `status DEPLOY`: `docker compose ... ps`, plus one line per non-admin user, `<id>  <done>/<total> done, <in_progress> in progress, <needs_attention> need attention`, or `<id>  —`.

- [ ] **Step 1: Write the failing tests.** All run against temp dirs. `getpass` and `run` are injected fakes; no Docker is used.
  - `test_init_creates_layout_and_refuses_non_empty`
  - `test_init_refuses_inside_repo`: target `<repo>/slicer-studio/tests/_tmp_deploy`. Exit 2, nothing created.
  - `test_add_user_requires_workspace_files`: a manifest with `workspace_requires ["queue.json"]`. Exit 2, and the message contains `queue.json`. After creating the file, exit 0, and `users.json` has the user.
  - `test_add_user_password_rules`: a mismatch gives 2, `"short"` gives 2 with "at least 8", and `users.json` is unchanged both times.
  - `test_add_user_resets_existing_password`: the hash changes and the user count stays 1.
  - `test_add_user_rejects_reserved_and_bad_ids`
  - `test_admin_needs_no_workspace_files`
  - `test_remove_user_keeps_workspace`
  - `test_up_renders_then_calls_compose`: the fake `run` records argv. Assert the `docker compose -f <generated>/docker-compose.yml up -d --remove-orphans` call, and that `generated/` exists first.
  - `test_down_never_removes_volumes`: `-v` is not in argv.
  - `test_status_prints_progress_lines`: one user with progress, one without (`—`).
- [ ] **Step 2: Run** `python -m unittest discover -s slicer-studio/tests -p "test_cli.py" -v`. Expected: FAIL.
- [ ] **Step 3: Implement `studio.py`** with `argparse` subcommands. Docker failures (non-zero `run`) print the command and return 1.
- [ ] **Step 4: Run** all studio tests. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `"Add the studio CLI: init, add-user, remove-user, render, up/down/restart, status"`.

### Task 5: Generic Slicer image and studio lockdown

**Files:**
- Create: `slicer-studio/slicer/{Dockerfile,start-studio.sh}`, by `git mv` from `studio/slicer/` and then editing; also move `studio/.gitattributes` to `slicer-studio/.gitattributes`
- Create: `slicer-studio/slicer/lockdown/studio_lockdown.py` (logic moved from `MyomaReviewLite/MyomaReviewLiteLib/studio_mode.py`) and `slicer-studio/slicer/lockdown/startup.py`
- Test: `slicer-studio/tests/test_lockdown.py` (pure); `slicer-studio/tests/slicer/{run_slicer_test.py,test_lockdown_slicer.py}`. The runner is a copy of the module's runner with the same `MYOMA_TEST_*` variable names renamed to `STUDIO_TEST_SCRIPT` and `STUDIO_TEST_LOG`.

**Interfaces:**
- Produces, in `studio_lockdown`:
  - `STUDIO_ENV = "SLICER_STUDIO"`, `START_ENV = "STUDIO_START_MODULE"`
  - `LOCKED_ACTION_PATTERN`, `KEPT_DOCK_NAME = "PanelDockWidget"`: unchanged
  - `is_studio_mode(environ=None) -> bool`: true only for an exact `"1"`
  - `locked_action_names(names) -> list[str]`
  - `lock_down(main_window) -> None`: the same behaviour as today, including each dock's toggle action and the re-hide guard
  - `keep_on_module(module_name: str) -> None`: whenever the current module changes to anything else, re-select `module_name` on the next event-loop turn; idempotent
  - `install(environ=None) -> None`: when in studio mode, runs `lock_down(mainWindow)` and `keep_on_module(environ[START_ENV])`; otherwise does nothing
- `startup.py`: `import studio_lockdown; studio_lockdown.install()`. The image runs it at Slicer startup via the mechanism chosen in Step 1.
- Image: `slicer-studio-slicer`.
  - The user is `user` (uid 1000), with the home folder `/home/user`.
  - The lockdown lives at `/opt/studio/lockdown/`.
  - There is no module code in the image.
  - `start-studio.sh` refuses to start, with a message and exit 1, when `STUDIO_MODULE_PATHS` or `STUDIO_START_MODULE` is empty. It passes `--additional-module-paths` with each colon-separated path as a separate argument. It selects the start module, then calls `showFullScreen()` and `setGeometry(0,0,1920,1080)` as today.

- [ ] **Step 1: Spike the startup hook** (spec open item 1).
  - In a throwaway container, compare three ways to run `startup.py`:
    - (a) a `.slicerrc.py` in the user's home, or `SLICERRC=/opt/studio/lockdown/startup.py`
    - (b) `--python-script`
    - (c) `--python-code`
  - For each, take X screenshots at 0.5 s intervals from launch, using `xwd` in the throwaway container only, and record which option first shows Slicer with no menu bar.
  - Pick the earliest option that runs with the main window available. Record the finding in `slicer-studio/README.md` Known limits ("menu flash: gone" or "menu flash: under N s").
- [ ] **Step 2: Write the failing tests.**
  - Pure `test_lockdown.py`, loading `studio_lockdown.py` by path via `importlib.util`:
    - the brief's earlier pure tests, ported: an exact `"1"` only, and the locked-name filter
    - `test_install_is_a_no_op_outside_studio`: fake environ `{}` and a fake main window object; nothing is called
  - `test_lockdown_slicer.py`: port `MyomaReviewLite/tests/test_studio_mode_slicer.py`'s assertions so they run without Myoma, against Slicer's bare main window:
    - menu bar hidden
    - console dock hidden after two triggers and a direct `show()`
    - locked actions disabled with no shortcuts
    - `acceptDrops()` false
  - Add `keep_on_module`: install with `STUDIO_START_MODULE="Data"`, then `slicer.util.selectModule("Welcome")` and process events. Assert `slicer.util.selectedModule() == "Data"`.
- [ ] **Step 3: Run** the pure test, and SLICER_TEST `slicer-studio` `test_lockdown_slicer.py`, using the studio runner `tests/slicer/run_slicer_test.py` and the variables `STUDIO_TEST_SCRIPT=tests/slicer/test_lockdown_slicer.py` and `STUDIO_TEST_LOG`. Expected: FAIL.
- [ ] **Step 4: Implement** `studio_lockdown.py`, `startup.py`, the Dockerfile (no `COPY MyomaReviewLite`, `COPY slicer-studio/slicer/lockdown /opt/studio/lockdown`) and `start-studio.sh`.
- [ ] **Step 5: Run.**
  - The pure test and the Windows Slicer test: PASS.
  - Build `docker build -f slicer-studio/slicer/Dockerfile -t slicer-studio-slicer .`. The cached Slicer layer is reused.
  - Run IMAGE_TEST with `slicer-studio/` mounted at `/modules/0` (`STUDIO_TEST_*` variables): `test_lockdown_slicer.py` PASS.
  - Run the image with no `STUDIO_*` variables: it exits 1 and the log names the missing variable.
  - Run the boundary test.
- [ ] **Step 6: Commit.** Message: `"Make the Slicer image module-agnostic and move the lockdown into the studio"`.

### Task 6: MyomaReviewLite as a studio module

**Files:**
- Delete: `MyomaReviewLite/MyomaReviewLiteLib/studio_mode.py`, `MyomaReviewLite/tests/test_studio_mode.py`, `MyomaReviewLite/tests/test_studio_mode_slicer.py`
- Modify: `MyomaReviewLite/MyomaReviewLite.py` (the `studio_mode` import and its uses at about lines 94, 1204, 1608, 2081, 2106 and 2241; the logic's save sites `save_draft`, `reveal_ai`, `publish_early_stop` and `publish_complete`)
- Modify: `MyomaReviewLite/MyomaReviewLiteLib/queue.py` (add `progress_counts`)
- Create: `studio-module.json` (repo root), with exactly the spec's section 1 content
- Test: `MyomaReviewLite/tests/test_queue.py` (extend), `MyomaReviewLite/tests/test_studio_hooks_slicer.py` (new), `MyomaReviewLite/tests/test_studio_manifest.py` (new, pure)

**Interfaces:**
- Produces:
  - `queue.progress_counts(summary: QueueSummary) -> dict[str, int]` with the keys `total`, `done`, `in_progress`, `needs_attention`. `done` counts `COMPLETE`, `in_progress` counts `IN_PROGRESS`, `needs_attention` counts `ATTENTION`.
  - `MyomaReviewLiteLogic._write_progress() -> None`: re-scans the queue and atomically writes `<dataset_manifest.output_root>/progress.json` using the same atomic-write approach as `storage.py`. It never raises into the caller: a failure is logged and swallowed, so a save never fails because of progress.
  - The module-level `_studio_mode() -> bool`, returning `os.environ.get("SLICER_STUDIO") == "1"`, replaces `studio_mode.is_studio_mode()`.

- [ ] **Step 1: Check spec open item 2.** Read how `reader_queue.create_queue` sets `output_root` in `queue.json`. Record whether results land in the reader folder, so that `/workspace` equals the reader folder.
  - If not, `studio-module.json`'s `workspace_requires` and the Task 8 instructions must account for it.
  - If a code change would be needed, stop and report NEEDS_CONTEXT.
- [ ] **Step 2: Write the failing tests.**
  - `test_progress_counts_maps_statuses`: build a `QueueSummary` with 2 COMPLETE, 1 IN_PROGRESS, 1 ATTENTION and 3 NEW. Expect `{"total": 7, "done": 2, "in_progress": 1, "needs_attention": 1}`.
  - `test_studio_manifest_loads_with_the_studio_loader`: load `<repo>/studio-module.json` with `slicer-studio/studio_lib/manifest.load_manifest`, adding `slicer-studio` to `sys.path`. Assert `start_module == "MyomaReviewLite"`, `uses_dataset is True` and `workspace_requires == ("queue.json",)`.
  - `test_studio_hooks_slicer.py`, with `SLICER_STUDIO=1`:
    - `manifest_button` is hidden
    - `choose_manifest()` makes no dialog call
    - after `enter()`/`exit()` the menu bar stays hidden
  - Without it: the button is visible, the dialog stub is called, and the chrome is restored after `exit()`. Restore `os.environ` at the end.
  - `test_studio_hooks_slicer.py` also: open the test queue, call `logic.save_draft()`, and assert `<output_root>/progress.json` exists and matches `progress_counts(logic.refresh_queue())`.
- [ ] **Step 3: Run.** Expected: FAIL.
  - Pure: `python -m unittest discover -s tests -p "test_queue.py" -v` and `... -p "test_studio_manifest.py" -v`, from `MyomaReviewLite/`.
  - SLICER_TEST `MyomaReviewLite` `test_studio_hooks_slicer.py`.
- [ ] **Step 4: Implement.**
  - Delete `studio_mode.py` and the two old tests, and remove the `lock_down` calls; the studio's startup does that now.
  - Remove the `exit()` re-select; `keep_on_module` replaces it.
  - Keep: hide the manifest button, refuse the picker, skip `_restore_panel_chrome()` in `exit()`, all under `_studio_mode()`.
  - Call `_write_progress()` at the end of each successful storage write.
  - Add `studio-module.json`.
- [ ] **Step 5: Run.**
  - Every pure module test.
  - Every `MyomaReviewLite/tests/test_*_slicer.py` on Windows: PASS.
  - Build the image, then run IMAGE_TEST for `test_studio_hooks_slicer.py`, `test_widget_layout_slicer.py` and `test_roi_handles_slicer.py`, with the module at `/modules/0` and a scratch queue at `/testq`: PASS.
  - The boundary test.
- [ ] **Step 6: Commit.** Message: `"Make MyomaReviewLite a studio module: SLICER_STUDIO hooks, progress.json, studio-module.json"`.

### Task 7: Sample module and workspace backup

**Files:**
- Create: `slicer-studio/examples/hello-module/studio-module.json`, `slicer-studio/examples/hello-module/HelloStudio/HelloStudio.py` (a scripted module) and `slicer-studio/examples/hello-module/README.md`
- Create: `slicer-studio/backup-workspaces.ps1`, by `git mv` from `studio/backup-readers.ps1` and then editing
- Test: `slicer-studio/tests/test_backup.py`, by `git mv` from `tests/test_studio_backup.py` and then editing; `slicer-studio/tests/test_examples.py`

**Interfaces:**
- **`HelloStudio`.** Its panel shows `User workspace: /workspace` (from `os.environ.get("STUDIO_WORKSPACE", "/workspace")`) and a "Count" button. Each click increments a counter stored in `/workspace/hello.json` and writes `progress.json` with `{"total": 10, "done": min(count, 10), "in_progress": 0, "needs_attention": 0}`.
- **Its manifest:** `name` "hello", `title` "Hello Studio", `module_paths` ["HelloStudio"], `start_module` "HelloStudio", `uses_dataset` false, `env` {"STUDIO_WORKSPACE": "{workspace}"}.
- **`backup-workspaces.ps1 -DeployDir D -BackupRoot B [-Keep 7]`:**
  - It copies `D/users/` to `<snapshot>/users/` and `D/portal/users.json` to `<snapshot>/users.json`.
  - It does not copy `secret.key`.
  - Snapshots are named `workspaces-yyyyMMdd-HHmmss-fff`, with the staging name `<name>.partial`. The prune pattern is `^workspaces-\d{8}-\d{6}-\d{3}$`.
  - A missing `D/users` is fatal (exit 1, nothing created).
  - All other behaviour, including the existing tests' cases, carries over.

- [ ] **Step 1: Write the failing tests.**
  - The moved backup tests, renamed to the new parameters and folder names.
  - `test_examples.py`: `load_manifest` on the hello manifest succeeds.
  - A pure test that imports `HelloStudio.py`'s progress helper. Keep the helper `def progress_for(count: int) -> dict` at module level so it imports without Slicer. `progress_for(12)["done"] == 10`.
- [ ] **Step 2: Run** `python -m unittest discover -s slicer-studio/tests -p "test_backup.py" -v` and `... -p "test_examples.py" -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the backup changes and the sample module.
- [ ] **Step 4: Run** both test files and the boundary test. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `"Add the hello-module sample and back up studio workspaces"`.

### Task 8: Guide, removal of the old studio, and two end-to-end runs

**Files:**
- Create: `slicer-studio/README.md` (from `studio/README.md`, made generic)
- Modify: `README.md` (repo root), so the studio line links `slicer-studio/README.md` and points to the root `studio-module.json`
- Delete: `studio/` (anything left), and the old `tests/test_studio_*.py` if any remain
- Modify: `.dockerignore` (drop `studio/...` entries; ignore no deployment paths, since deployments are outside the repo)

- [ ] **Step 1: Write `slicer-studio/README.md`.** Sections, as numbered commands:
  1. What the studio is, and the module contract (link to the spec).
  2. Router.
  3. Server: firewall, Docker auto-start and auto sign-in. Carry over the current text, including the Docker program-rule listing and the LAN-only 443 check.
  4. Build the two images.
  5. Create a deployment: `init`, then the module's own preparation (for Myoma: `python reader_queue.py create ... --reader-dir <deploy-dir>/users/<id>`), then `add-user`, then `up`.
  6. Add, reset or remove a user: live reload, `up` to start or stop a Slicer.
  7. Certificate export from `<project>_caddy-data`.
  8. Backup: `Register-ScheduledTask` daily at 02:00 running `backup-workspaces.ps1`.
  9. Data drive replug: `studio.py restart`.
  10. Writing a module: point to `examples/hello-module/` and the progress.json format.
  11. Known limits: carried over, plus the Task 5 menu-flash finding.
  12. On-network acceptance checklist: carried over, UNCHECKED.

  Use placeholders, not drive letters. Keep the `myoma` word out, apart from the module preparation example. Phrase that example as `<your module's own setup command>`, and put the MyomaReviewLite example in the root `README.md` instead.
- [ ] **Step 2: Delete `studio/`.** Search the repo for references, using grep for `studio/` paths and `studio_config`; only `slicer-studio/` and the specs and plans may remain. Then run every test suite:
  - all `slicer-studio/tests`, with Docker running
  - all repo `tests/`: only the known pre-existing failures
  - all module pure tests
  - every Windows SLICER_TEST in `MyomaReviewLite/tests`
- [ ] **Step 3: End-to-end run 1, MyomaReviewLite**, on loopback only. The scratch deploy-dir is outside the repo.
  1. `studio.py init <scratch>/myoma-e2e --module <repo>/studio-module.json --dataset <dataset_root from review-config.json> --server-address 127.0.0.1`
  2. Create 2 users' queues (1–2 cases each) with `reader_queue.py create --reader-dir <scratch>/myoma-e2e/users/<id>`.
  3. `add-user` both users and one admin, with random passwords fed through a small Python driver calling `studio.main(..., getpass=...)` so no secret is printed.
  4. `render`.
  5. Write a compose override with caddy `ports: !override ["127.0.0.1:8443:443"]`, and run `docker compose -f generated/docker-compose.yml -f <override> up -d`.

  Verify:
  - (a) `/` redirects to `/login`.
  - (b) Login gives a 302 to the landing path and sets the cookie.
  - (c) The user's own `vnc.html` returns 200.
  - (d) The other user's returns 403.
  - (e) With no cookie, the request goes to `/login`.
  - (f) The WebSocket gives 101 for the user's own path and 403 for the other's.
  - (g) After a `logic.save_draft()` in user A's Slicer, `/admin` shows numbers for A. Trigger it with `docker compose exec slicer-<A> ...` running a short Slicer Python command, or by saving through the UI over VNC.
  - (h) A startup screenshot shows the Myoma panel, no menus and the case loaded. Note the menu-flash result.
  - (i) User A's container cannot reach `slicer-<B>:6080` or `portal:9000`.

  Then `down -v` and delete the scratch dir.
- [ ] **Step 4: End-to-end run 2, hello-module.** Same as Step 3, with `--module <repo>/slicer-studio/examples/hello-module/studio-module.json` and no `--dataset`.
  - Check (a)–(f) and (i).
  - (g): click "Count" via `xdotool` in a throwaway exec, or call the module's button handler with `docker compose exec` and Slicer Python, then confirm `/admin` shows `1/10`.
  - (h): a screenshot shows the HelloStudio panel.

  Tear down.
- [ ] **Step 5: Record the results.**
  - Add a "Verified locally on <date>" section in `slicer-studio/README.md` with both runs' results.
  - Update the root `README.md`.
  - Run the boundary test one last time.
- [ ] **Step 6: Commit.** Message: `"Document Slicer Studio, remove the old studio folder, record two-module local verification"`.
