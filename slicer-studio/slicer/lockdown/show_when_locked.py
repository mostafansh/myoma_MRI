# Run by start-studio.sh after the startup lockdown. Shows Slicer full screen
# only when the lockdown is installed and the menu bar is hidden; otherwise the
# process exits non-zero so the supervisor restarts it instead of serving an
# open session.
import os
import sys

try:
    sys.path.insert(0, "/opt/studio/lockdown")
    import slicer
    import studio_lockdown

    if not studio_lockdown.is_installed():
        raise RuntimeError("lockdown is not installed")
    window = slicer.util.mainWindow()
    # isVisible() is always false before the window is shown; isHidden() is
    # true only when the menu bar was explicitly hidden.
    if not window.menuBar().isHidden():
        raise RuntimeError("menu bar is not hidden")
except BaseException as error:
    sys.stderr.write("start-studio: refusing to show an unlocked Slicer: %r\n" % (error,))
    sys.stderr.flush()
    os._exit(1)

# No window manager runs on the virtual display, so showFullScreen alone leaves
# Slicer at its small default size; setGeometry fills the 1920x1080 screen.
window.showFullScreen()
window.setGeometry(0, 0, 1920, 1080)
