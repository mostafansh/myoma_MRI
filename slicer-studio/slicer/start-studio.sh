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
IFS=: read -r -a module_paths <<< "$STUDIO_MODULE_PATHS"

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

# No window manager runs on the virtual display, so showFullScreen alone leaves
# Slicer at its small default size; setGeometry fills the 1920x1080 screen.
while kill -0 "$xvfb_pid" 2>/dev/null; do
    /opt/slicer/Slicer --no-splash --additional-module-paths "${module_paths[@]}" \
        --python-code "import os; slicer.util.selectModule(os.environ['STUDIO_START_MODULE']); w = slicer.util.mainWindow(); w.showFullScreen(); w.setGeometry(0, 0, 1920, 1080)"
    sleep 2
done
echo "start-studio: Xvfb exited" >&2
exit 1
