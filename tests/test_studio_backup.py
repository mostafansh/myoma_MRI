"""Tests for studio/backup-readers.ps1, the nightly results backup."""

from __future__ import annotations

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "studio" / "backup-readers.ps1"


class StudioBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.readers = self.root / "readers"
        case = self.readers / "mostafa" / "case1"
        case.mkdir(parents=True)
        (case / "annotations.json").write_text('{"ok": true}', encoding="utf-8")
        self.portal = self.root / "portal-data"
        self.portal.mkdir()
        (self.portal / "readers.json").write_text("{}", encoding="utf-8")
        (self.portal / "secret.key").write_text("secret", encoding="utf-8")
        self.backup = self.root / "backup"
        self.backup.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(
        self, readers: Path | None = None, portal: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(SCRIPT),
                "-ReadersRoot",
                str(readers or self.readers),
                "-PortalDataDir",
                str(portal or self.portal),
                "-BackupRoot",
                str(self.backup),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def _snapshots(self) -> list[Path]:
        return sorted(p for p in self.backup.glob("readers-*") if p.is_dir())

    def test_backup_copies_results_and_readers_file(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        latest = self._snapshots()[-1]
        self.assertTrue((latest / "readers" / "mostafa" / "case1" / "annotations.json").is_file())
        self.assertTrue((latest / "readers.json").is_file())
        self.assertFalse((latest / "secret.key").exists())

    def test_backup_keeps_only_the_newest_seven(self) -> None:
        first_names: list[str] = []
        for index in range(9):
            result = self._run()
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            if index < 2:
                first_names.append(self._snapshots()[-1].name)
        remaining = [p.name for p in self._snapshots()]
        self.assertEqual(len(remaining), 7)
        for name in first_names:
            self.assertNotIn(name, remaining)

    def test_prune_leaves_unrelated_folders_alone(self) -> None:
        other = self.backup / "readers-notes"
        other.mkdir()
        keep = self.backup / "photos"
        keep.mkdir()
        for _ in range(9):
            self.assertEqual(self._run().returncode, 0)
        self.assertTrue(other.is_dir())
        self.assertTrue(keep.is_dir())
        self.assertEqual(len(self._snapshots()) - 1, 7)

    def test_missing_readers_file_warns_but_backs_up(self) -> None:
        (self.portal / "readers.json").unlink()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("readers.json", result.stdout + result.stderr)
        latest = self._snapshots()[-1]
        self.assertTrue((latest / "readers" / "mostafa" / "case1" / "annotations.json").is_file())
        self.assertFalse((latest / "readers.json").exists())

    def test_backup_fails_on_missing_source(self) -> None:
        result = self._run(readers=self.root / "does-not-exist")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_failed_copy_leaves_no_snapshot_and_keeps_old_ones(self) -> None:
        for _ in range(2):
            self.assertEqual(self._run().returncode, 0)
        before = [p.name for p in self._snapshots()]
        self.assertEqual(len(before), 2)

        # Hold a source file open with no sharing so robocopy cannot read it.
        locked = self.readers / "mostafa" / "case1" / "locked.bin"
        locked.write_bytes(b"x")
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            str(locked), 0x80000000, 0, None, 3, 0x80, None
        )
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            result = self._run()
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual([p.name for p in self._snapshots()], before)
        self.assertEqual(
            sorted(p.name for p in self.backup.iterdir()), sorted(before)
        )


if __name__ == "__main__":
    unittest.main()
