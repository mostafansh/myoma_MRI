# Slicer Studio as a separate platform: design

Date: 2026-10-22. Status: agreed in brainstorming; awaiting review of this written spec.

Builds on: `docs/superpowers/specs/2026-10-21-review-studio-design.md` (the review studio as built on this branch, PR mostafansh/myoma_MRI#2).

## What we are building

The review studio currently serves one module, MyomaReviewLite, and is tied to it in five places:
- the Slicer image copies the module
- the environment variable names are Myoma's
- the lockdown lives in the module's library
- the portal imports the module to count progress
- the config reads Myoma's `review-config.json`

This work splits it into **Slicer Studio**, a generic platform, and makes MyomaReviewLite its first module.

### Understanding

What was said:
- Modules added on top can be **any Slicer module**, not only queue-based reader studies.
- **One module per deployment.** Everyone who logs in to a deployment gets that module. Switching modules means changing the deployment and restarting.
- The studio lives in **this repository** in a `slicer-studio/` folder with a strict boundary: no Myoma names. It can move to its own repository later.
- **One generic Slicer image.** The module folder is mounted read-only at runtime, not baked into a per-module image.
- Progress is reported through a file the module writes, not by the portal importing the module.
- The lockdown moves into the studio.

Assumptions (correct these if wrong):
- Slicer stays at 5.10.0 for all modules.
- Modules need no extra Python packages inside Slicer. A module that does is handled when one appears, with a derived image.
- Nothing has been deployed for real yet, so no data migration from the current `studio/` layout is needed.

### Out of scope

- Several modules in one deployment, or a module picker after login.
- Non-Slicer tools such as OHIF, web apps or notebooks.
- A separate git repository or package publishing.
- Level 2 blinding (unchanged: still a future reveal service behind `ai_mask_path()`).

## 1. The module contract

A module ships `studio-module.json`. Paths in it are relative to the folder containing the manifest. MyomaReviewLite's manifest sits at the repo root:

```json
{
  "schema": 1,
  "name": "myoma-review",
  "title": "Myoma review",
  "module_paths": ["MyomaReviewLite"],
  "start_module": "MyomaReviewLite",
  "uses_dataset": true,
  "env": {
    "MYOMA_REVIEW_LITE_CASE": "{workspace}/queue.json",
    "MYOMA_REVIEW_LITE_DATA_ROOT": "{dataset}"
  },
  "workspace_requires": ["queue.json"]
}
```

| Key | Required | Meaning |
|---|---|---|
| `schema` | yes | Must be `1`. |
| `name` | yes | Matches `^[a-z][a-z0-9-]{0,31}$`. Used in image labels and messages. |
| `title` | yes | Shown on the login page and `/admin`. |
| `module_paths` | yes | A non-empty list of folders, each containing Slicer module code. Each must resolve inside the manifest's folder. |
| `start_module` | yes | The Slicer module name selected at start. Users are kept on it. |
| `uses_dataset` | no (default false) | If true, the deployment's dataset is mounted read-only at `/data`. |
| `env` | no | Extra environment variables for the module. Values may use only `{workspace}` (`/workspace`) and `{dataset}` (`/data`; an error if `uses_dataset` is false). |
| `workspace_requires` | no | Files that must exist in a user's workspace before that user can be added or rendered. |

Unknown keys are an error, so typos fail loudly.

What the studio guarantees to every module:
- Slicer 5.10.0 in a locked-down full-screen window, opened on `start_module`.
- `module_paths` mounted read-only at `/modules/0`, `/modules/1`, and so on.
- A private per-user workspace at `/workspace`, read-write, included in backups.
- `SLICER_STUDIO=1` and `STUDIO_START_MODULE=<start_module>` in the environment.

What a module may optionally do: write `/workspace/progress.json`:

```json
{"total": 302, "done": 40, "in_progress": 2, "needs_attention": 1}
```

All four keys are non-negative integers. `/admin` shows them, and shows "—" when the file is missing, unreadable or invalid.

Not part of the contract:
- **Creating a user's initial work** (for Myoma, `reader_queue.py create`). That is the module's own tool, documented in the module's README.
- **Blinding and data rules.** Those belong to the module.

## 2. Layout

```
<repo>/
├─ slicer-studio/                 platform; a boundary test forbids "myoma" (any case) under it
│  ├─ studio.py                   CLI: init, add-user, render, up, down, restart, status
│  ├─ studio_lib/                 manifest + deployment config validation, rendering (pure Python)
│  ├─ slicer/                     Dockerfile, start-studio.sh, lockdown/studio_lockdown.py
│  ├─ portal/                     portal_core.py, studio_portal.py, Dockerfile
│  ├─ backup-workspaces.ps1
│  ├─ examples/hello-module/      sample Slicer module + studio-module.json
│  ├─ tests/
│  └─ README.md                   generic setup guide (router, firewall, certificate, backup, known limits)
├─ studio-module.json             MyomaReviewLite's manifest
├─ MyomaReviewLite/
└─ (studio/ is removed)
```

The studio uses the words **user** and **workspace**, never reader or study.

## 3. Deployments live outside the repo

A deployment folder sits at a location the admin chooses, outside the git checkout. It holds patient-linked results and the session secret, which should not sit in a checkout. It also keeps drive-letter paths out of repo files, which `tests/test_package_portability.py` forbids.

```
<deploy-dir>/
├─ studio.json      {"schema": 1, "module": "<path to studio-module.json>", "dataset": "<dataset path>", "server_address": "192.168.0.201"}
├─ users/<id>/      workspaces
├─ portal/          users.json, secret.key, access.log
└─ generated/       docker-compose.yml, Caddyfile
```

`studio.json` rules:
- `module` must point to an existing manifest.
- `dataset` is required exactly when the manifest has `uses_dataset`.
- `server_address` is validated as it is today.

Paths may be absolute or relative to `<deploy-dir>`.

CLI (`python slicer-studio/studio.py <command> <deploy-dir> ...`):

| Command | Does |
|---|---|
| `init <deploy-dir> --module M --server-address A [--dataset D]` | Creates the folder structure and `studio.json`. Refuses a non-empty folder. Creates `secret.key`. |
| `add-user <deploy-dir> ID [--name N] [--admin]` | Validates the id (same regex and reserved ids as today). Creates `users/ID/` if missing. Checks `workspace_requires` (non-admins only), refusing with a message that names the missing files. Prompts for the password (minimum 8 characters) and writes `users.json`. Resetting a password uses the same command. |
| `remove-user <deploy-dir> ID` | Removes the user from `users.json`. The workspace is kept. Prints a reminder to run `up`. |
| `render <deploy-dir>` | Validates everything and writes `generated/`. |
| `up` / `down` / `restart` | Renders, then runs `docker compose` with `--remove-orphans` for `up`. The project name is `studio-<deploy-dir folder name, sanitised>`. |
| `status <deploy-dir>` | Shows the compose service states plus each user's progress (the same data as `/admin`). |

The portal's live reload of `users.json` (built on this branch) means adding, resetting and removing users takes effect without restarting the portal. `up` is still needed to start or stop a user's Slicer container.

## 4. Components

### Generic Slicer image (`slicer-studio/slicer/`)

- Same base as today: Ubuntu 22.04, Slicer 5.10.0 with a SHA-512 check, Xvfb, x11vnc and noVNC, with the same supervision and restart behaviour.
- **No module code is baked in.**
- `start-studio.sh` builds `--additional-module-paths` from `STUDIO_MODULE_PATHS` (colon-separated) and selects `STUDIO_START_MODULE`. If either variable is missing, it refuses to start and logs why.
- **Lockdown** moves from `MyomaReviewLiteLib/studio_mode.py` to `slicer/lockdown/studio_lockdown.py`, baked into the image and run at Slicer startup.
  - The lockdown itself is unchanged:
    - chrome hidden
    - File/Edit/View/Window/Help actions disabled
    - each hidden dock's toggle action disabled, with a re-hide guard
    - no drag-and-drop
  - New: it keeps the user on `STUDIO_START_MODULE`. Whenever another module becomes current, it re-selects the start module. This replaces the logic in Myoma's `exit()`.
  - It should run as early as Slicer allows, before the first paint if possible. Whether the brief menu flash disappears is checked with a startup screenshot and documented either way.

### MyomaReviewLite

- `MyomaReviewLiteLib/studio_mode.py` and its tests are removed.
- The module's own studio behaviour is triggered by `SLICER_STUDIO == "1"`:
  - hide "Change manifest"
  - refuse the manifest picker
  - don't restore the chrome on `exit()`

  Everything else behaves exactly as today when the variable is unset.
- **Progress:** after every successful save, draft or final, the module writes `progress.json` atomically to the output root:
  - `total` is the number of queue entries
  - `done` is the number completed or early-stopped
  - `in_progress` is the number with a draft
  - `needs_attention` is the number with a diagnostic

  Outside the studio, the file is harmless.
- `studio-module.json` is added at the repo root.
- Queue and result formats don't change. `domain.py` and `storage.py` don't change, apart from an optional pure helper for the progress counts if it fits there better than in `queue.py`.

### Portal (`slicer-studio/portal/`)

- **Renamed to users:** `users.json`, `--users-root`, `User` instead of `Reader`. All security behaviour and its tests carry over unchanged:
  - scrypt hashes and HMAC sessions
  - the 12 h session cookie
  - the concurrent-safe lockout
  - live reload
  - reserved ids
  - log sanitising
  - the 8-character minimum password
- `/admin` reads `/workspaces/<id>/progress.json`, mounted read-only. It never imports module code.
- The portal image is `python:3.12-slim` with the standard library only. The nibabel and numpy installs go away.
- The login page and `/admin` show the manifest's `title`, passed in through `STUDIO_TITLE`.

### Rendering (`slicer-studio/studio_lib/`)

Produces the same topology as today:
- Caddy publishes `<server_address>:443:443` only, with `tls internal` and `default_sni`.
- Per-user routes use `route { forward_auth; uri strip_prefix; reverse_proxy }`.
- `edge` holds Caddy and the portal. `net-<id>` is an internal network holding Caddy and `slicer-<id>`.
- Containers run with `init: true` and `restart: unless-stopped`.
- The compose project gets a `name:`.

New mounts and environment:
- Each `module_paths[n]` is mounted read-only at `/modules/n`.
- The dataset is mounted at `/data:ro` only if `uses_dataset` is true.
- `users/<id>` is mounted at `/workspace`.
- The `env` placeholders are expanded.
- `SLICER_STUDIO=1`, `STUDIO_START_MODULE` and `STUDIO_MODULE_PATHS` are set.

### Backup

`backup-workspaces.ps1 -DeployDir D -BackupRoot B [-Keep 7]`
- copies `users/` and `portal/users.json`
- does not copy `secret.key`
- builds each snapshot under a staging `.partial` name, the same as today
- names snapshots `workspaces-<stamp>`

### Sample module (`slicer-studio/examples/hello-module/`)

- A minimal scripted Slicer module, `HelloStudio`.
- Its panel shows the user id and the workspace path, with a button that increments a counter. Each click writes `/workspace/progress.json` (`total: 10`, `done: <count>`).
- It has its own `studio-module.json` with `uses_dataset: false`.
- It proves the platform is generic and serves as the template for new modules.

## 5. Error handling

| Situation | Behaviour |
|---|---|
| Invalid manifest or `studio.json` | `render`/`up` exits 2, and the message names the file, the key and the problem. Nothing is written to `generated/`. |
| `module_paths` resolves outside the manifest folder | Rejected, including through `..` and symlinks. |
| Non-admin user's workspace lacks a `workspace_requires` file | `add-user` refuses. `render` lists that user with a warning and still renders, so one unprepared user doesn't block others. That user's Slicer starts, and the module shows its own "no queue" state. |
| `STUDIO_MODULE_PATHS` or `STUDIO_START_MODULE` missing in the container | `start-studio.sh` exits non-zero with a message. The container restarts, and the cause is visible in `docker compose logs`. |
| `progress.json` missing or invalid | `/admin` and `status` show "—" for that user. |
| Data drive unplugged | Unchanged: the portal's `/unavailable` page. Only checked when `uses_dataset` is true. |

## 6. Testing

- **Moved and renamed:** the portal core, portal server, render and backup tests, rewritten for users and workspaces. These include the Docker-backed `caddy adapt` auth-before-strip test and the `docker compose config` test.
- **New:**
  - The boundary test: no case-insensitive `myoma` under `slicer-studio/`.
  - Manifest and `studio.json` validation, covering every rule in sections 1 and 3, including path escape.
  - The CLI commands `init`, `add-user` (including the `workspace_requires` refusal), `remove-user`, `render` and `status`, run against temp deployment folders.
  - The lockdown's pure rules. The Slicer lockdown test moves to `slicer-studio/tests/` and runs under Windows Slicer and in the image, including the keep-on-start-module rule.
  - Myoma: progress calculation (pure), `progress.json` written on save (Slicer test), and the existing Slicer tests passing with `SLICER_STUDIO=1` and without it.
- **Local end-to-end run**, on `127.0.0.1:8443` only, then torn down. It runs twice: once with MyomaReviewLite and once with `hello-module`. Each run checks:
  - the login redirect and cookie
  - a user's own viewer returns 200, another user's returns 403
  - the WebSocket returns 101 or 403 the same way
  - `/admin` shows the progress counts written by the module
  - a startup screenshot shows the locked-down start module, with a note on whether the menu flash is gone
  - a user's container can't reach the other user's container or the portal
- The repo's existing tests pass, apart from the known `mostafa/` errors.

## 7. Open items checked at the start of implementation

1. **When the startup script runs.** Can Slicer 5.10 run a startup script before the main window first paints (for example `.slicerrc.py` versus `--python-script` versus `--python-code`)? If none can, the lockdown runs as early as possible and the flash stays documented.
2. **`/workspace` versus Myoma's output root.** Confirm that MyomaReviewLite writes results next to `queue.json` (its reader folder) so that `/workspace` is that folder, as `/review` is today.
