# Slicer Studio: setup and acceptance

Slicer Studio lets users open `https://<server>` in a browser on the local
network, log in at the portal and work in their own 3D Slicer, which is streamed
to the browser through noVNC. Everything runs in Docker on the server. Each user
has a private container on an internal network with no internet access, and
Caddy plus the portal are the only things the laptops can reach.

The studio is generic. What runs inside Slicer is a **module**, described by a
`studio-module.json` manifest (module folders, the module to start, whether it
uses a shared dataset, environment variables, files each workspace must hold).
Each user gets a private workspace folder mounted at `/workspace`; a module that
wants progress shown to the administrator writes `progress.json` there (section 10).

Design and the module contract: `docs/superpowers/specs/2026-10-22-slicer-studio-split-design.md`.

All commands below run in PowerShell from the repository root on the server,
unless a step says otherwise. `<deploy-dir>` is the deployment folder (outside
the repository, section 5) and `<dataset path>` is the folder of the shared
dataset, if your module uses one.

## 1. Router

1. Find the router model on its sticker or on its Status page, and write it down
   here: `Router model: ____________`.
2. Open the router's admin page, find the DHCP address reservation (static
   lease) settings and reserve `192.168.0.201` for the server's network adapter.
3. Turn UPnP off.
4. Add no port forwarding. The studio must stay reachable from the local
   network only.

## 2. Server

1. Power plan: never sleep.

   ```powershell
   powercfg /change standby-timeout-ac 0
   powercfg /change hibernate-timeout-ac 0
   ```

2. Docker Desktop: Settings, General, tick "Start Docker Desktop when you sign in".
3. Windows automatic sign-in, so the studio comes back after a reboot. Run
   `netplwiz`, untick "Users must enter a user name and password to use this
   computer", apply, and enter the server account's password once. (This stores
   the password on the machine; use a dedicated local account for the server.)
4. Firewall. Allow port 443 from the local subnet only:

   ```powershell
   New-NetFirewallRule -DisplayName "Slicer Studio" -Direction Inbound -Protocol TCP -LocalPort 443 -RemoteAddress 192.168.0.0/24 -Action Allow
   ```

   Docker Desktop may also have created broader rules for 443, and some of them
   are program rules (they match the Docker executable, not a port), which a
   port-based listing cannot see. List both kinds and restrict or remove any
   that allow any remote address:

   ```powershell
   Get-NetFirewallPortFilter -Protocol TCP | Where-Object LocalPort -eq 443 |
       ForEach-Object {
           $rule = $_ | Get-NetFirewallRule
           $addr = $_ | Get-NetFirewallAddressFilter
           [pscustomobject]@{ Name = $rule.DisplayName; Enabled = $rule.Enabled; Action = $rule.Action; Remote = $addr.RemoteAddress }
       }

   Get-NetFirewallRule -Direction Inbound -Action Allow | Where-Object DisplayName -like '*docker*' |
       ForEach-Object {
           [pscustomobject]@{
               Name    = $_.DisplayName
               Enabled = $_.Enabled
               Program = ($_ | Get-NetFirewallApplicationFilter).Program
               Remote  = ($_ | Get-NetFirewallAddressFilter).RemoteAddress
           }
       }
   ```

   To restrict a rule: `Set-NetFirewallRule -DisplayName "<name>" -RemoteAddress 192.168.0.0/24`.

   As a second line of defence the generated compose file publishes caddy's
   port as `<server address>:443:443`, so 443 is bound only to the server's LAN
   address (the `--server-address` you pass to `init`) and not to every
   interface on the host.
5. Check from another device on the LAN (a laptop, not the server) that
   `https://192.168.0.201` answers. On the server, confirm nothing listens on
   443 on any other interface; only the server address and Docker's proxy
   should appear:

   ```powershell
   Get-NetTCPConnection -LocalPort 443 -State Listen
   ```

## 3. Build the images

Both images build from the repository root.

```powershell
docker build -f slicer-studio/slicer/Dockerfile -t slicer-studio-slicer .
docker build -f slicer-studio/portal/Dockerfile -t slicer-studio-portal .
```

The Slicer image defaults to Slicer 5.10.0 (build args `SLICER_URL` and
`SLICER_SHA512`). It contains no module; the module folders are mounted from the
host at run time. Slicer containers have no internet access at runtime; that is
intended.

## 4. Commands at a glance

```powershell
python slicer-studio/studio.py init <deploy-dir> --module <abs path>/studio-module.json --server-address 192.168.0.201 [--dataset "<dataset path>"]
python slicer-studio/studio.py add-user <deploy-dir> <id> [--name "<Display name>"] [--admin]
python slicer-studio/studio.py remove-user <deploy-dir> <id>
python slicer-studio/studio.py render <deploy-dir>
python slicer-studio/studio.py up <deploy-dir>
python slicer-studio/studio.py down <deploy-dir>
python slicer-studio/studio.py restart <deploy-dir>
python slicer-studio/studio.py status <deploy-dir>
```

`down`, `restart` and `status` drive the generated compose file, so `up` or
`render` must have run first. `down` never removes volumes.

## 5. Create a deployment

A deployment is a folder holding everything private to one studio: user
workspaces, password hashes, the session secret and the generated files. It
must be outside the repository (`init` refuses a folder inside it).

1. Initialise it. Pass an **absolute** `--module` path: a relative path is
   resolved against `<deploy-dir>`, not the current folder.

   ```powershell
   python slicer-studio/studio.py init <deploy-dir> --module <abs path>/studio-module.json --server-address 192.168.0.201 --dataset "<dataset path>"
   ```

   Leave `--dataset` out for a module that does not use one.
2. Prepare each user's workspace with your module's own preparation step, writing
   into `<deploy-dir>/users/<id>` (whatever files the manifest's
   `workspace_requires` lists must exist there). `<id>` is lowercase letters,
   digits, `_` or `-`, starting with a letter. The ids `admin`, `login`,
   `logout`, `auth`, `unavailable` and `static` are reserved.
3. Add the users. Each prompts twice for a password (at least 8 characters,
   never echoed). Add one administrator with `--admin`: the admin gets the
   progress page at `/admin` and has no container.

   ```powershell
   python slicer-studio/studio.py add-user <deploy-dir> <id> --name "<Display name>"
   python slicer-studio/studio.py add-user <deploy-dir> <admin-id> --admin
   ```

4. Start it. `up` renders the compose file and Caddyfile (under
   `<deploy-dir>/generated`, regenerated and never edited by hand) and runs
   `docker compose up -d --remove-orphans`.

   ```powershell
   python slicer-studio/studio.py up <deploy-dir>
   python slicer-studio/studio.py status <deploy-dir>
   ```

   `status` lists `caddy`, `portal` and one `slicer-<id>` per user as running,
   then each user's progress. The compose project is named from the deployment
   folder, `studio-<folder name>`.

`<deploy-dir>/users` and `<deploy-dir>/portal` hold user results, password
hashes and the session secret. Do not share or commit them.

Useful afterwards (use service names, not container names):

```powershell
docker compose -f <deploy-dir>/generated/docker-compose.yml logs portal
docker compose -f <deploy-dir>/generated/docker-compose.yml restart slicer-<id>
docker compose -f <deploy-dir>/generated/docker-compose.yml exec slicer-<id> pkill -f SlicerApp
```

The portal writes login events to `<deploy-dir>/portal/access.log`.

## 6. Add, reset or remove a user

The portal re-reads `users.json` whenever it changes, so a new user, a password
reset or a removed user takes effect immediately and the portal container does
not need a restart. If the file cannot be parsed, the portal keeps the last good
set and logs the error.

- Add a user: prepare the workspace (section 5, step 2), `add-user`, then `up`
  to start that user's Slicer.
- Reset a password: run `add-user <deploy-dir> <id>` again. The old password
  stops working at once, but sessions already signed in stay valid until they
  expire (up to 12 hours).
- Remove a user: `remove-user <deploy-dir> <id>`, then `up` to stop their Slicer
  and remove its container and network. Their cookie stops working at once. Their
  workspace in `<deploy-dir>/users/<id>` stays on disk; delete or archive it
  yourself.

## 7. Laptop certificate

Caddy signs the studio's certificate with its own root CA, so browsers warn
until that root is trusted. Export it once (the project name is shown by
`docker compose ls`; it is `studio-<deploy folder name>`):

```powershell
docker compose -f <deploy-dir>/generated/docker-compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt caddy-root.crt
```

Then either:

- install it on each laptop (Windows: double-click `caddy-root.crt`, Install
  Certificate, Local Machine, "Trusted Root Certification Authorities"; or
  `certutil -addstore -f Root caddy-root.crt` in an elevated prompt), or
- accept the browser warning once per laptop and browser.

The root lives in the `<project>_caddy-data` volume. Do not run
`docker compose down -v` on the live studio: it deletes that volume and the
certificate must be re-exported and reinstalled. `studio.py down` and `up` keep it.

## 8. Backup

`slicer-studio/backup-workspaces.ps1` copies all users' workspaces and
`portal/users.json` into a timestamped folder on the backup drive and keeps the
newest 7 (`-Keep`). `secret.key` is deliberately not copied. Try it once by
hand, then register it daily at 02:00, running as the server account (replace
the placeholders):

```powershell
.\slicer-studio\backup-workspaces.ps1 -DeployDir <deploy-dir> -BackupRoot <backup drive folder>

$repo = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $repo `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\slicer-studio\backup-workspaces.ps1`" -DeployDir `"<deploy-dir>`" -BackupRoot `"<backup drive folder>`""
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
Register-ScheduledTask -TaskName "Slicer Studio backup" -Action $action -Trigger $trigger -Description "Nightly copy of Slicer Studio workspaces"
```

Check later with `Get-ScheduledTaskInfo -TaskName "Slicer Studio backup"` (look
at `LastTaskResult`, 0 is success) and by looking inside the backup folder.
Restore by copying a `workspaces-<stamp>/users` folder back over
`<deploy-dir>/users` and `users.json` back to `<deploy-dir>/portal/users.json`.

## 9. Data drive unplugged

If the dataset drive is unplugged, the portal sends logged-in users to an
"unavailable" page. After reconnecting it, restart the studio so the containers
see the drive again:

```powershell
python slicer-studio/studio.py restart <deploy-dir>
```

## 10. Writing a module

Start from `slicer-studio/examples/hello-module/` (see its README): a
`studio-module.json` plus a scripted module. The platform provides:

- `/workspace`: the user's private folder, writable. Keep all state there and
  write files atomically (temp file, then `os.replace`).
- `/data`: the shared dataset, read-only, if `uses_dataset` is true.
- the environment variables from the manifest's `env` (`{workspace}` becomes
  `/workspace`, `{dataset}` becomes `/data`), plus `SLICER_STUDIO=1` and
  `STUDIO_USER` (the user's id).
- a lockdown: Slicer starts straight into `start_module`, with menus, other
  modules and the Python console hidden.

To show progress at `/admin`, write `/workspace/progress.json` with exactly four
non-negative integers:

```json
{"total": 10, "done": 3, "in_progress": 0, "needs_attention": 0}
```

Anything else (extra keys, wrong types, a file over 1 MiB) is ignored and the
admin page shows a dash.

## Known limits

- Two logins as the same user share one screen: both see and control the same
  Slicer.
- Logout does not revoke a copied session token; it stays valid for up to 12
  hours. Removing the user does revoke it at once.
- Level 1 lockdown is UI only. A dock created after startup is not guarded until
  the next start, and Copy/Paste and other unnamed Slicer shortcuts survive.
- Startup lockdown: it runs from `SLICERRC` at Slicer startup. Screenshots taken
  every 0.5 s showed the first painted frame already locked, so no menu flash
  was observed; a shorter flash cannot be ruled out. If the lockdown or the start
  module fails, Slicer refuses to show and the container restarts it (fail
  closed).
- The axial view rendered black in earlier container screenshots, and also in
  Slicer on Windows. Still to check (see the checklist).
- A backup killed mid-run leaves a `workspaces-*.partial` folder in the backup
  location. Delete it by hand.
- There is no sign-out link on the viewer. Users leave by closing the tab, and
  an open viewer stays connected until then.
- After replugging the data drive, run `studio.py restart` (section 9).
- `/admin` reads every user's progress file on each load and can be slow with
  large queues.
- A relative `--module` given to `init` is resolved against the deploy dir, so
  always pass an absolute path.

## Verified locally on 2026-10-22

Two full runs on the server against `https://127.0.0.1:8443`, each with two
test users and one admin (scratch data, random passwords, removed afterwards).
Caddy was published on loopback only through a compose override
(`ports: !override ["127.0.0.1:8443:443"]`) started with
`docker compose -f <deploy-dir>/generated/docker-compose.yml -f <override> up -d`.
Not on the real network.

Run 1: the review module (`--dataset` set, one to two cases per user). Run 2:
`examples/hello-module` (no dataset).

| Check | Run 1 (review module) | Run 2 (hello-module) |
| --- | --- | --- |
| (a) `/` redirects to `/login` | pass | pass |
| (b) POST `/login` gives 302 to the user's landing path and sets `studio_session` | pass | pass |
| (c) own `/<A>/vnc.html` returns 200 | pass | pass |
| (d) other user's `/<B>/vnc.html` returns 403 | pass | pass |
| (e) no cookie redirects to `/login` | pass | pass |
| (f) WebSocket: 101 on own path, 403 on the other's | pass | pass |
| (g) `/admin` shows numbers after the module wrote `progress.json` | pass: a draft saved over VNC gave 2 total, 0 done, 1 in progress | pass: one click on Count gave 10 total, 1 done |
| (h) startup screenshot | module panel, no menus, case loaded, axial view black | HelloStudio panel with `User: <id>` (confirms `STUDIO_USER`) |
| (i) user A's container cannot reach `slicer-<B>:6080` or `portal:9000` | pass (name resolution fails) | pass |

The portal, Caddy routing and per-user isolation behaved the same with both
modules. Running these also found that the portal's compose `command` replaced
the image's whole CMD and stopped the container; it now spells out the full
command (fixed with a test).

## On-network acceptance (to run with two laptops)

Acceptance 2026-10-__ (laptop A wired, laptop B on WiFi). Tick each line as it
passes.

- [ ] laptop A (wired) and laptop B (WiFi) log in as different users at the same time
- [ ] each completes a full session with the module
- [ ] A cannot open B's path, including typing `/<b>/vnc.html` directly (403)
- [ ] keys the module relies on reach Slicer in the browser; if any is swallowed, note it and add an alternative key in studio mode
- [ ] the axial view shows the image (it rendered black in earlier checks)
- [ ] interaction is acceptable over WiFi
- [ ] after pulling the cable or WiFi mid-session and reconnecting, nothing is lost
- [ ] after `docker compose -f <deploy-dir>/generated/docker-compose.yml exec slicer-<a> pkill -f SlicerApp` mid-session, Slicer comes back within about 30 s with the user's saved work
- [ ] after rebooting the server, the studio comes back without manual steps
- [ ] the 5 wrong-password lockout shows the exact message
- [ ] the admin page shows both users' progress
