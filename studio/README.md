# Review studio: setup and acceptance

The studio lets readers open `https://<server>` in a browser on the local
network, log in at the portal and review cases in their own 3D Slicer, which is
streamed to the browser through noVNC. Everything runs in Docker on the server.
Each reader has a private container on an internal network with no internet
access, and Caddy plus the portal are the only things the laptops can reach.

Design: `docs/superpowers/specs/2026-10-21-review-studio-design.md`.

All commands below run in PowerShell from the repository root on the server,
unless a step says otherwise. `<dataset path>` is the `dataset_root` value in
`review-config.json`.

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
   New-NetFirewallRule -DisplayName "Myoma studio" -Direction Inbound -Protocol TCP -LocalPort 443 -RemoteAddress 192.168.0.0/24 -Action Allow
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
   address (the `--server-address` you render with) and not to every interface
   on the host.
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
docker build -f studio/slicer/Dockerfile -t myoma-studio-slicer .
docker build -f studio/portal/Dockerfile -t myoma-studio-portal .
```

The Slicer image defaults to Slicer 5.10.0 (build args `SLICER_URL` and
`SLICER_SHA512`). Slicer containers have no internet access at runtime; that is
intended.

## 4. Add a reader

Run these for each new reader. `<id>` is lowercase letters, digits, `_` or `-`,
starting with a letter. The ids `admin`, `login`, `logout`, `auth`,
`unavailable` and `static` are reserved.

1. Create the reader's queue (optionally `--case-list <file>` for a subset):

   ```powershell
   python reader_queue.py create --reader-id <id> --study-id <study> --reader-dir studio/readers/<id>
   ```

2. Set the reader's password. It prompts twice and never echoes. The first run
   also creates `studio/portal-data/readers.json` and `secret.key`. Add one
   administrator the same way with `--admin` (the admin gets the progress page
   and has no container).

   ```powershell
   python studio/portal/studio_portal.py set-password <id> --data-dir studio/portal-data --name "<Display name>"
   python studio/portal/studio_portal.py set-password <admin-id> --data-dir studio/portal-data --admin
   ```

3. Render the compose file and Caddyfile:

   ```powershell
   python studio/studio_config.py render --server-address 192.168.0.201
   ```

   Defaults: `--review-config review-config.json`, `--portal-data studio/portal-data`,
   `--readers-root studio/readers`, `--out studio/generated`. The output is
   regenerated, never edited by hand.

4. Start or update the studio:

   ```powershell
   docker compose -f studio/generated/docker-compose.yml up -d --remove-orphans
   docker compose -f studio/generated/docker-compose.yml ps
   ```

   `ps` should list `caddy`, `portal` and one `slicer-<id>` per reader as running.
   `--remove-orphans` stops and removes the containers of readers that are no
   longer in the file. Containers are named `myoma-studio-<service>-1`, for
   example `myoma-studio-slicer-<id>-1`.

   The portal re-reads `readers.json` whenever it changes, so a new reader, a
   password reset or a removed reader takes effect immediately and the portal
   container does not need a restart. A new reader still needs the render (step 3)
   and `up -d` above to get a Slicer container. If `readers.json` cannot be parsed, the portal keeps
   the last good set and logs the error (`docker compose ... logs portal`).

Reset a password: run `set-password <id> --data-dir studio/portal-data` again.
It is live as soon as the file is saved; the old password stops working, but
sessions already signed in stay valid until they expire (up to 12 hours).

Remove a reader: delete their entry from `studio/portal-data/readers.json`, then
render again (step 3) and run `up -d --remove-orphans` (step 4). Their cookie
stops working at once, and the container and network are removed. Their results
in `studio/readers/<id>` stay on disk; delete or archive them yourself.
Passwords are at least 8 characters; `set-password` refuses shorter ones.

`studio/readers/` and `studio/portal-data/` hold reader results, password hashes
and the session secret. They are git-ignored; do not commit or share them.

Useful afterwards (these use service names, not container names):

```powershell
docker compose -f studio/generated/docker-compose.yml logs portal
docker compose -f studio/generated/docker-compose.yml restart slicer-<id>
docker compose -f studio/generated/docker-compose.yml exec slicer-<id> pkill -f SlicerApp
```

The portal writes login events to `studio/portal-data/access.log`.

## 5. Laptop certificate

Caddy signs the studio's certificate with its own root CA, so browsers warn
until that root is trusted. Export it once:

```powershell
docker compose -f studio/generated/docker-compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt studio/caddy-root.crt
```

Then either:

- install it on each laptop (Windows: double-click `caddy-root.crt`, Install
  Certificate, Local Machine, "Trusted Root Certification Authorities"; or
  `certutil -addstore -f Root caddy-root.crt` in an elevated prompt), or
- accept the browser warning once per laptop and browser.

The root lives in the `caddy-data` volume. Do not run `docker compose down -v`
on the live studio: it deletes that volume and the certificate must be
re-exported and reinstalled. Plain `down` and `up -d` keep it.

## 6. Backup

`studio/backup-readers.ps1` copies all readers' results and `readers.json` into
a timestamped folder on the backup drive and keeps the newest 7 (`-Keep`).
`secret.key` is deliberately not copied. Try it once by hand, then register it
daily at 02:00, running as the server account (replace the placeholders):

```powershell
.\studio\backup-readers.ps1 -ReadersRoot studio/readers -PortalDataDir studio/portal-data -BackupRoot <backup drive folder>

$repo = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $repo `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\studio\backup-readers.ps1`" -ReadersRoot `"$repo\studio\readers`" -PortalDataDir `"$repo\studio\portal-data`" -BackupRoot `"<backup drive folder>`""
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
Register-ScheduledTask -TaskName "Myoma studio backup" -Action $action -Trigger $trigger -Description "Nightly copy of studio reader results"
```

Check later with `Get-ScheduledTaskInfo -TaskName "Myoma studio backup"` (look at
`LastTaskResult`, 0 is success) and by looking inside the backup folder.
Restore by copying a `readers-<stamp>/readers` folder back over `studio/readers`.

## 7. Data drive unplugged

If the dataset drive is unplugged, the portal sends logged-in readers to an
"unavailable" page. After reconnecting it, restart the studio so the containers
see the drive again:

```powershell
docker compose -f studio/generated/docker-compose.yml restart
```

## Known limits

- Two logins as the same reader share one screen: both see and control the same
  Slicer.
- Logout does not revoke a copied session token; it stays valid for up to 12
  hours.
- Level 1 lockdown is UI only. A dock created after startup is not guarded until
  the next module enter, and Copy/Paste and other unnamed Slicer shortcuts
  survive.
- The axial view rendered black in container screenshots, and also in Slicer on
  Windows. Still to check (see the checklist).
- A backup killed mid-run leaves a `readers-*.partial` folder in the backup
  location. Delete it by hand.
- On every Slicer start or restart, the reader may see Slicer's full menus for
  under a second before the lockdown applies.
- There is no sign-out link on the viewer. Readers leave by closing the tab, and
  an open viewer stays connected until then.
- After replugging the data drive, run `docker compose ... restart` (section 7).
- The admin page reads every reader's queue on each load. With two readers of
  302 cases each it took about 15 seconds on this server, so expect it to be slow.

## Verified locally on 2026-10-21

Run on the server against `https://127.0.0.1:8443` with a compose override that
published caddy on loopback only (an override adding `127.0.0.1:8443:443`; compose appends it to the server-address binding, or use `!override` to replace it), two test readers and one admin (scratch data,
removed afterwards). Not on the real network.

- [x] (a) `/` redirects to `/login`
- [x] (b) POST `/login` as reader A gives 302 to A's landing path and sets `studio_session`
- [x] (c) with A's cookie, `/<A>/vnc.html` returns 200 with the noVNC page
- [x] (d) with A's cookie, `/<B>/vnc.html` returns 403
- [x] (e) with no cookie, `/<A>/vnc.html` redirects to `/login`
- [x] (f) with A's cookie, WebSocket upgrade to `/<A>/websockify` returns 101 and to `/<B>/websockify` returns 403
- [x] (g) the admin page lists both readers with counts
- [x] (h) in-container screenshot: Myoma panel with A's first case loaded, no Slicer menus; axial view black
- [x] (i) `pkill -f SlicerApp` in A's container: Slicer relaunched within seconds and reloaded the case
- [x] (j) A's container cannot resolve or reach `slicer-<B>:6080` or `portal:9000`, and has no route to the internet

## On-network acceptance (to run with two laptops)

Acceptance 2026-10-__ (laptop A wired, laptop B on WiFi). Tick each line as it
passes.

- [ ] laptop A (wired) and laptop B (WiFi) log in as different readers at the same time
- [ ] each completes a case: box, reveal, correct, FIGO, finish
- [ ] A cannot open B's path, including typing `/<b>/vnc.html` directly (403)
- [ ] keys A, Q, 1-4, Ctrl+Z and Alt-drag reach Slicer in the browser; if any is swallowed, note it and add an alternative key in studio mode
- [ ] the axial view shows the image (it rendered black in earlier checks)
- [ ] brushing is acceptable over WiFi
- [ ] after pulling the cable or WiFi mid-case and reconnecting, nothing is lost
- [ ] after `docker compose -f studio/generated/docker-compose.yml exec slicer-<a> pkill -f SlicerApp` mid-case, Slicer comes back within about 30 s with the reader's draft
- [ ] after rebooting the server, the studio comes back without manual steps
- [ ] the 5 wrong-password lockout shows the exact message
- [ ] the admin page shows both readers' progress
