"""The repo-root studio-module.json loads with the studio's own loader."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "slicer-studio"))

from studio_lib.manifest import load_manifest  # noqa: E402


class StudioManifestTests(unittest.TestCase):
    def test_studio_manifest_loads_with_the_studio_loader(self):
        manifest = load_manifest(REPO / "studio-module.json")
        self.assertEqual(manifest.start_module, "MyomaReviewLite")
        self.assertIs(manifest.uses_dataset, True)
        self.assertEqual(manifest.workspace_requires, ("queue.json",))


if __name__ == "__main__":
    unittest.main()
