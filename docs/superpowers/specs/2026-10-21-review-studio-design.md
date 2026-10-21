# Review studio (browser access over the local network): design

Date: 2026-10-21. Status: agreed in brainstorming; awaiting review of this written spec.

## What we are building

Today each reader runs 3D Slicer 5.10 with the MyomaReviewLite module on the machine that holds the data. The studio lets a reader bring their own laptop, join the local network (cable or WiFi), open one address in a browser, log in, and review in the same MyomaReviewLite they use today. The images never leave the server.

### Understanding

What was said:

- The data stays on one dedicated on-site machine (this PC; dataset on the `M:` SSD).
- Readers bring their own laptops and connect by wired LAN or WiFi through the user's own TP-Link router (not hospital IT).
- 1–3 readers work at the same time.
- Reuse the existing Slicer module rather than rewriting the viewer.
- Blinding: Level 1 (lock Slicer's UI) now, built so Level 2 (reveal service) can be added later without reworking the module.

Assumptions (correct these if wrong):

- The server stays powered on and wired to the router while a study runs.
- Readers use a current Chrome or Edge.
- Results keep today's on-disk format so the existing analysis scripts are unchanged.
- One fixed container per reader is acceptable (no on-demand start/stop).

### Out of scope

- Access from outside the local network (internet, VPN, cloud).
- More than about 10 concurrent readers, or a second server.
- A rewritten web-native viewer (OHIF/Cornerstone).
- Level 2 blinding itself (only the seam for it is in scope).

## Architecture

```
laptop (browser) ──WiFi/LAN──► TP-Link router ──cable──► server 192.168.0.201
                                                          │
                                         Caddy :443 (HTTPS, tls internal)
                                          ├─ /login, /admin ─► studio-portal
                                          ├─ /mostafa/*  ─► slicer-mostafa  (noVNC → Xvfb → Slicer)
                                          └─ /reader2/*  ─► slicer-reader2
                                         every /<reader>/* request passes forward_auth
                                         to studio-portal, which allows only that reader
```

Everything runs in Docker Desktop (WSL2 backend) on Windows 11 Pro, from one `docker-compose.yml`. All services use `restart: unless-stopped`, so they come back after a crash or reboot.

### 1. Network and access

- The server keeps a fixed address through a DHCP reservation on the router (TP-Link: Advanced → Network → DHCP Server → Address Reservation): `192.168.0.201`.
- Readers open `https://192.168.0.201`. A local name such as `studio.lan` is optional, if the router supports local DNS.
- The Windows firewall allows inbound port 443 from `192.168.0.0/24` only. Container ports are not published to the network; only Caddy is.
- The router keeps UPnP off and has no port forwarding to the server. Readers' laptops reach the server only from inside this network.
- Recommended, not required: a second router used only by the studio, with no internet uplink. The current network is shared with other devices (`.101`, `.109`, `.145`).
- HTTPS uses Caddy's internal certificate authority. On first visit each laptop either accepts the browser warning once or installs Caddy's root certificate. The setup guide documents both.

### 2. studio-portal (login, routing, admin)

A small Python web service, about 200 lines, with no database.

- `readers.json` (server-only, outside git): reader id, display name, password hash (stdlib `hashlib.scrypt`, so the portal needs no third-party packages), and an admin flag.
- `GET/POST /login`: a form. On success it sets a signed, HttpOnly, Secure session cookie (12 h) and redirects to `/<reader>/`. After 5 failed attempts in 10 minutes, it locks that username for 10 minutes.
- `GET /auth` (used by Caddy `forward_auth`): returns 200 only if the cookie is valid and its reader id matches the first path segment, or the user is an admin. Otherwise it returns a redirect to `/login`. This covers the noVNC WebSocket upgrade too.
- `GET /logout` clears the cookie. The reader's Slicer keeps running, and the next login lands in the same session.
- `GET /admin` (admins only): one table per reader built from `reader_queue.py status --json` (done / in progress / remaining). It refreshes on load; nothing is pushed live.
- An access log line (time, reader, IP, event) for each login, logout and failed attempt, written to `access.log` in the portal data directory (`studio/portal-data/access.log`).
- A helper command adds a reader or resets a password: `python studio_portal.py set-password READER`.

### 3. Reader containers

One image, `myoma-studio-slicer`, used by every reader:

- Linux Slicer 5.10 and the `MyomaReviewLite` module folder.
- Xvfb (virtual display, 1920×1080) with no window manager, so there is no desktop or menu to escape to; Slicer runs full-screen. x11vnc serves the display, and noVNC/websockify serves x11vnc to the browser on an internal port.
- noVNC: clipboard off, no file transfer, scaling to the browser window, and adaptive quality (lower quality while moving, sharp at rest) so WiFi stays usable.
- `start-studio.sh` replaces `Start-MyomaReviewLite.ps1` inside the container. It does the following:
  - sets `MYOMA_REVIEW_LITE_CASE=/review/queue.json`, `MYOMA_REVIEW_LITE_DATA_ROOT=/data` and `MYOMA_REVIEW_LITE_STUDIO=1`
  - launches `Slicer --no-splash --additional-module-paths /opt/MyomaReviewLite --python-code "slicer.util.selectModule('MyomaReviewLite')"`
  - restarts Slicer if it exits.
- Rendering is in software (Mesa llvmpipe). The views are 2D slices, so GPU passthrough is not needed.

Per reader, in compose. `docker-compose.yml` and the `Caddyfile` are generated from `readers.json` by `studio_config.py render`, so they never drift apart and no drive-letter path is committed to git:

| Service | Mounts |
|---|---|
| `slicer-<reader>` | dataset root (`dataset_root` in `review-config.json`) → `/data` (read-only); `studio\readers\<reader>` → `/review` (read-write) |

Adding a reader takes three steps:
1. `reader_queue.py create` into `studio\readers\<reader>`
2. Run `set-password`, which adds the reader to `readers.json`.
3. Run `studio_config.py render`, then `docker compose up -d`.

### 4. Studio mode in the module (Level 1 lockdown)

Studio mode turns on only when `MYOMA_REVIEW_LITE_STUDIO=1` is set. Without it, the module behaves exactly as it does today.

- **Chrome stays hidden.** `exit()` (`MyomaReviewLite.py:2086`) does not call `_restore_panel_chrome()`; menu bar, status bar, toolbars and the module selector stay hidden for the whole session.
- **No module switching.** If any other module becomes current, the module selects MyomaReviewLite again.
- **No Python console or error log.** Their docks and toggle actions are hidden and disabled. The console's shortcuts are removed (Ctrl+3, and the Python console menu and toolbar actions).
- **No file dialogs.** The "Change manifest" button (`MyomaReviewLite.py:2398`) is hidden, and the manifest picker (`MyomaReviewLite.py:2219`) refuses to open. Slicer's global Open/Save/Add Data actions (Ctrl+O, Ctrl+S) are disabled.
- **No drag-and-drop loading** into the Slicer window.
- **Fixed queue.** The queue always comes from `MYOMA_REVIEW_LITE_CASE`.
- **Implementation.** All lockdown lives in one new helper, `MyomaReviewLiteLib/studio_mode.py` (`is_studio_mode()`, `lock_down(main_window)`), called from `enter()`/`setup()`. The main module file gains only the calls and guards.

### 5. The Level 2 seam

- The AI mask is read through one method, `load_ai_mask()` (`MyomaReviewLite.py:528`). Studio work keeps that true: no other code path may open `manifest.ai_mask` directly.
- Today the path is read in exactly two places: `reveal_ai()` (`MyomaReviewLite.py:421`) and `load_ai_mask()` (`MyomaReviewLite.py:537`). Both get the mask file path from one provider function. In Level 1 it returns `manifest.ai_mask`.
- Level 2 later swaps the provider for one that fetches from studio-portal, which checks the reader's `draft.json` for a saved `blinded_snapshot` and logs the reveal. It also stops mounting AI masks into reader containers. No other module code changes.

### 6. Storage and backup

- Result files are unchanged: `draft.json`, `draft.seg.nrrd`, `annotations.json` and `corrected_labels.nii.gz` per case, under `studio\readers\<reader>`.
- Drafts autosave as today. A closed browser, a dropped WiFi link or a logout loses nothing, because the Slicer session continues on the server.
- Nightly backup: a Windows scheduled task mirrors `studio\readers\` and `studio\portal\readers.json` to a second USB drive on the server, keeping 7 dated copies. The router's USB storage is not used.

## Failure handling

| Situation | Behavior |
|---|---|
| Reader's browser or WiFi drops | Slicer keeps running. Reconnecting and logging in again lands in the same view. |
| Slicer crashes | `start-studio.sh` restarts it. The module reopens the reader's queue and its last draft loads. |
| Server reboots | Docker Desktop starts at login, and the containers restart. The server is set to log in automatically, or Docker runs as a service. |
| `M:` SSD unplugged | studio-portal also mounts the dataset read-only and checks it on every `/auth`. While it is unreadable, readers get a "Data drive unavailable, tell the study admin" page instead of a broken Slicer. After reconnecting the drive, the admin runs `docker compose restart`, because Docker's mount of a replugged USB drive goes stale. |
| Wrong password 5 times | The username is locked for 10 minutes, and the attempt is logged. |
| Two people log in as the same reader | Both see the same Slicer (noVNC shared session). This is documented, not prevented. |

## Testing

- **Unit (outside Slicer):**
  - `studio_mode.is_studio_mode()` with and without the variable
  - studio-portal login, lockout, cookie signing, and the `/auth` reader-to-path rule, including a reader trying another reader's path, which must be refused
  - `readers.json` handling
- **Slicer tests** (existing `run_slicer_test.py` harness), with studio mode on:
  - chrome stays hidden after `exit()`
  - module switching snaps back
  - the manifest picker refuses to open
  - the Python console action is disabled
  - the AI mask is reachable only through `load_ai_mask()`.
- **Existing test suite** passes unchanged with studio mode off.
- **Manual acceptance on the real network:**
  - two laptops, one on WiFi and one wired, logged in as different readers at once
  - each completes a case: box, reveal, correct, FIGO, finish
  - neither can open the other's path
  - the keys A, Q, 1–4 and Alt-drag work in the browser
  - brushing is acceptable over WiFi
  - after pulling the network cable mid-case and reconnecting, nothing is lost
  - a server reboot brings everything back.

## Open items checked at the start of implementation

1. **Linux Slicer 5.10 compatibility.** Run the module in the container first: fonts load, the panel renders, and a full case completes. If this fails, the studio design does not change, but the module needs fixes before anything else.
2. **Browser key capture.** Check that the browser or noVNC does not swallow Alt-drag, Ctrl+Z or the 1–4 keys. If any are swallowed, add noVNC key remapping or an alternative key in studio mode.
3. **Dataset read speed.** Measure dataset reads from `M:` through Docker's WSL2 mount (`/mnt/m`). If case loading is noticeably slow, copy the dataset into the WSL2 filesystem, read-only.
4. **Router model** (sticker or Status page) and its address reservation menu, for the setup guide.
