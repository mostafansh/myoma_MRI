#!/usr/bin/env bash
set -u

Xvfb :1 -screen 0 1920x1080x24 -nolisten tcp &
x11vnc -display :1 -localhost -rfbport 5900 -forever -shared -nopw -noclipboard -nosetclipboard -quiet &
websockify --web /usr/share/novnc 6080 localhost:5900 &

# No window manager runs on the virtual display, so showFullScreen alone leaves
# Slicer at its small default size; setGeometry fills the 1920x1080 screen.
while true; do
    Slicer --no-splash --additional-module-paths /opt/MyomaReviewLite \
        --python-code "slicer.util.selectModule('MyomaReviewLite'); w = slicer.util.mainWindow(); w.showFullScreen(); w.setGeometry(0, 0, 1920, 1080)"
    sleep 2
done
