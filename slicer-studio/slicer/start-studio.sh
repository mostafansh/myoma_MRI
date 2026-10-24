#!/usr/bin/env bash
set -u

# The platform passes these; refuse to serve an unconfigured Slicer.
for var in STUDIO_MODULE_PATHS STUDIO_START_MODULE; do
    if [ -z "${!var:-}" ]; then
        echo "start-studio: $var is not set (the studio's compose file provides it)" >&2
        exit 1
    fi
done

# One --additional-module-paths argument per colon-separated path.
module_paths=()
IFS=: read -r -a raw_paths <<< "$STUDIO_MODULE_PATHS"
for path in "${raw_paths[@]}"; do
    [ -n "$path" ] && module_paths+=("$path")
done
if [ "${#module_paths[@]}" -eq 0 ]; then
    echo "start-studio: STUDIO_MODULE_PATHS has no usable path" >&2
    exit 1
fi

# Lockdown runs from Slicer's startup script (see /opt/studio/lockdown/startup.py).
export SLICER_STUDIO=1
export SLICERRC=/opt/studio/lockdown/startup.py

# Keep a helper alive: restart it a second after it exits.
supervise() {
    while true; do
        "$@"
        sleep 1
    done &
}

# A container restart keeps /tmp, and a leftover lock makes Xvfb refuse to start.
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1

Xvfb :1 -screen 0 1920x1080x24 -nolisten tcp &
xvfb_pid=$!

# Bounded wait for the display socket; fail loudly rather than serve a dead screen.
for _ in $(seq 1 100); do
    [ -S /tmp/.X11-unix/X1 ] && break
    kill -0 "$xvfb_pid" 2>/dev/null || { echo "start-studio: Xvfb exited" >&2; exit 1; }
    sleep 0.1
done
[ -S /tmp/.X11-unix/X1 ] || { echo "start-studio: display :1 did not come up within 10 s" >&2; exit 1; }

supervise x11vnc -display :1 -localhost -rfbport 5900 -forever -shared -nopw -noclipboard -nosetclipboard -quiet
supervise websockify --web /usr/share/novnc 6080 localhost:5900

# Nothing a session plants may persist into the next one: drop the saved module
# and extension search paths from the user's settings before each launch.
scrub_settings() {
    local ini=/home/user/.config/slicer.org/Slicer.ini
    [ -f "$ini" ] || return 0
    awk '/^\[/ { section = $0 }
         section == "[Modules]" && /^AdditionalPaths/ { next }
         section == "[Extensions]" && /InstallPath/ { next }
         { print }' "$ini" > "$ini.tmp" && mv "$ini.tmp" "$ini"
}

# show_when_locked.py refuses (exit 1) to show an unlocked Slicer; this loop then
# restarts Slicer. It also sizes the window: no window manager runs here.
while kill -0 "$xvfb_pid" 2>/dev/null; do
    scrub_settings
    /opt/slicer/Slicer --no-splash --additional-module-paths "${module_paths[@]}" \
        --python-code "exec(open('/opt/studio/lockdown/show_when_locked.py').read())"
    sleep 2
done
echo "start-studio: Xvfb exited" >&2
exit 1
