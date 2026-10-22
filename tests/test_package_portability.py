"""Static package invariants that prevent machine-bound distributions."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".cmd", ".json", ".md", ".ps1", ".py", ".svg", ".txt"}


def _candidate_files() -> list[Path]:
    """Files git tracks or would track; git-ignored scratch is skipped. Walks the tree without git."""
    try:
        done = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=PROJECT_ROOT, capture_output=True, timeout=60, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return [path for path in PROJECT_ROOT.rglob("*") if path.is_file()]
    names = [n for n in done.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
    return [path for path in (PROJECT_ROOT / n for n in names) if path.is_file()]


class PackagePortabilityTests(unittest.TestCase):
    def test_obsolete_shortcuts_are_absent(self) -> None:
        names = ("data" + ".lnk", "MyomaReviewLite-module" + ".lnk")
        for name in names:
            with self.subTest(name=name):
                self.assertFalse((PROJECT_ROOT / name).exists())

    def test_only_review_config_contains_drive_letter_paths(self) -> None:
        drive_path = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")
        offenders: list[str] = []
        for path in _candidate_files():
            if (
                path.name == "review-config.json"
                or (path.suffix.casefold() not in TEXT_SUFFIXES and path.name != "CMakeLists.txt")
            ):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            if drive_path.search(text):
                offenders.append(path.relative_to(PROJECT_ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_runtime_files_do_not_reference_shortcuts(self) -> None:
        forbidden = ("data" + ".lnk", "MyomaReviewLite-module" + ".lnk")
        runtime_files = (
            PROJECT_ROOT / "reader_queue.py",
            PROJECT_ROOT / "Start-MyomaReviewLite.ps1",
            PROJECT_ROOT / "mostafa" / "Start Review.cmd",
        )
        for path in runtime_files:
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                with self.subTest(path=path.name, name=name):
                    self.assertNotIn(name, text)

    def test_bundled_module_contains_no_python_cache_directories(self) -> None:
        caches = [
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in (PROJECT_ROOT / "MyomaReviewLite").rglob("__pycache__")
        ]
        self.assertEqual(caches, [])


if __name__ == "__main__":
    unittest.main()
