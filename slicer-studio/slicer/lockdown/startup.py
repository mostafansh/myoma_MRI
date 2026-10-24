# Startup lockdown. The image points SLICERRC at this file, so Slicer runs it
# once at startup, with the main window already available.
#
# Spike (Slicer 5.10.0, Xvfb 1920x1080, xwd screenshot about every 0.5 s from
# launch; the window first paints 6.7-10 s after launch; each run's first
# non-black frame is the first paint):
#   SLICERRC env var   first painted frame already locked (hook ran at 6.7 s)  -> chosen
#   ~/.slicerrc.py     first painted frame already locked (hook ran at 9.3 s)
#   --python-script    first painted frame already locked (hook ran at 10.0 s)
#   --python-code      one frame (<0.65 s) with the menu bar, then locked
#   QTimer.singleShot  one frame (<0.5 s) with the menu bar, then locked
# In every option the main window is visible when the hook runs, so a flash is
# possible in principle; with SLICERRC none was seen at 0.5 s sampling.
# The env var is chosen over ~/.slicerrc.py because a mounted home folder
# cannot hide it.
#
# Slicer logs an exception from a startup script and carries on, which would
# serve an unlocked window. So any failure ends the process; the supervisor in
# start-studio.sh then restarts it.
import os
import sys

try:
    sys.path.insert(0, "/opt/studio/lockdown")

    import studio_lockdown

    studio_lockdown.install()
except BaseException:
    import traceback

    sys.stderr.write("studio startup FAILED, exiting:\n" + traceback.format_exc() + "\n")
    sys.stderr.flush()
    sys.stdout.flush()
    os._exit(1)
