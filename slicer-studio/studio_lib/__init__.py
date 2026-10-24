"""Pure-Python validation of module manifests and deployment folders."""

from __future__ import annotations

import sys
from pathlib import Path

_PORTAL = str(Path(__file__).resolve().parents[1] / "portal")
if _PORTAL not in sys.path:
    sys.path.insert(0, _PORTAL)
