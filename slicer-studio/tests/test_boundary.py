"""The platform must stay module-agnostic: no module name appears under slicer-studio/."""

from __future__ import annotations

import unittest
from pathlib import Path

WORD = "myo" + "ma"
ROOT = Path(__file__).resolve().parents[1]   # slicer-studio/


class BoundaryTests(unittest.TestCase):
    def test_no_module_names_in_the_platform(self):
        offenders = []
        for path in ROOT.rglob("*"):
            rel = path.relative_to(ROOT).as_posix()
            if "__pycache__" in rel:
                continue
            if WORD in rel.lower():
                offenders.append(rel)
            elif path.is_file():
                try:
                    if WORD in path.read_text(encoding="utf-8").lower():
                        offenders.append(rel)
                except UnicodeDecodeError:
                    pass
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
