"""Integration tests for the relocatable project wrapper and Slicer launcher."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProjectToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _stage_package(self) -> tuple[Path, Path, Path]:
        package = self.root / "Portable Package with spaces"
        package.mkdir()
        shutil.copy2(
            PROJECT_ROOT / "Start-MyomaReviewLite.ps1",
            package / "Start-MyomaReviewLite.ps1",
        )
        shutil.copy2(PROJECT_ROOT / "reader_queue.py", package / "reader_queue.py")
        shutil.copytree(PROJECT_ROOT / "MyomaReviewLite", package / "MyomaReviewLite")
        dataset = package / "dataset root with spaces"
        dataset.mkdir()
        reader = package / "mostafa"
        reader.mkdir()
        (reader / "queue.json").write_text("{}", encoding="utf-8")
        shutil.copy2(
            PROJECT_ROOT / "mostafa" / "Start Review.cmd",
            reader / "Start Review.cmd",
        )
        self._write_config(package, "dataset root with spaces", "")
        return package, dataset, reader

    def _write_config(
        self, package: Path, dataset_root: str | Path, slicer_path: str | Path
    ) -> None:
        (package / "review-config.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "dataset_root": str(dataset_root),
                    "slicer_path": str(slicer_path),
                }
            ),
            encoding="utf-8",
        )

    def _fake_slicer(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "@echo off\r\n"
            '> "%MYOMA_LAUNCH_CAPTURE%" echo CASE=%MYOMA_REVIEW_LITE_CASE%\r\n'
            '>> "%MYOMA_LAUNCH_CAPTURE%" echo DATA=%MYOMA_REVIEW_LITE_DATA_ROOT%\r\n'
            '>> "%MYOMA_LAUNCH_CAPTURE%" echo ARGS=%*\r\n',
            encoding="utf-8",
            newline="",
        )
        return path

    def _wait_for_text(
        self, path: Path, expected: str, timeout_seconds: float = 5.0
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if path.is_file():
                try:
                    if expected in path.read_text(encoding="utf-8"):
                        return
                except OSError:
                    pass
            time.sleep(0.02)
        self.fail(f"timed out waiting for {expected!r} in {path}")

    def _resolve(
        self, package: Path, reader: Path, *extra: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(package / "Start-MyomaReviewLite.ps1"),
                "-ReaderDir",
                str(reader),
                *extra,
                "-ResolveOnly",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_python_queue_wrapper_uses_only_the_bundled_module(self) -> None:
        environment = os.environ.copy()
        environment.pop("MYOMA_REVIEW_LITE_MODULE_ROOT", None)

        result = subprocess.run(
            ["python", str(PROJECT_ROOT / "reader_queue.py"), "--help"],
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("create", result.stdout)
        self.assertIn("status", result.stdout)
        self.assertIn("migrate-paths", result.stdout)

    def test_copied_start_command_launches_with_relative_internal_paths(self) -> None:
        package, dataset, reader = self._stage_package()
        fake_slicer = self._fake_slicer(package / "tools" / "Fake Slicer.cmd")
        self._write_config(
            package,
            "dataset root with spaces",
            str(Path("tools") / fake_slicer.name),
        )
        capture = self.root / "launch capture.txt"
        environment = os.environ.copy()
        environment["MYOMA_LAUNCH_CAPTURE"] = str(capture)

        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(reader / "Start Review.cmd")],
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self._wait_for_text(capture, "ARGS=")
        launched = capture.read_text(encoding="utf-8")
        self.assertIn(f"CASE={(reader / 'queue.json').resolve()}", launched)
        self.assertIn(f"DATA={dataset.resolve()}", launched)
        self.assertIn(
            f'--additional-module-paths "{(package / "MyomaReviewLite").resolve()}"',
            launched,
        )
        self.assertIn("slicer.util.selectModule", launched)
        command = (reader / "Start Review.cmd").read_text(encoding="utf-8")
        self.assertIn('ReaderDir "%~dp0."', command)
        self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", command), command)

    def test_slicer_precedence_is_override_then_config_then_auto_detection(self) -> None:
        package, _, reader = self._stage_package()
        configured = self._fake_slicer(package / "tools" / "Configured Slicer.cmd")
        override = self._fake_slicer(package / "tools" / "Override Slicer.cmd")
        self._write_config(package, "dataset root with spaces", configured)

        overridden = self._resolve(
            package, reader, "-SlicerPath", str(override)
        )
        configured_result = self._resolve(package, reader)
        self._write_config(package, "dataset root with spaces", "")
        automatic = self._resolve(package, reader)

        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        self.assertEqual(Path(overridden.stdout.strip()), override.resolve())
        self.assertEqual(configured_result.returncode, 0, configured_result.stderr)
        self.assertEqual(Path(configured_result.stdout.strip()), configured.resolve())
        self.assertEqual(automatic.returncode, 0, automatic.stderr)
        self.assertIn("5.10", automatic.stdout)
        self.assertTrue(automatic.stdout.strip().endswith("Slicer.exe"))

    def test_absolute_config_values_are_accepted(self) -> None:
        package, _, reader = self._stage_package()
        external_dataset = self.root / "external dataset"
        external_dataset.mkdir()
        external_slicer = self._fake_slicer(self.root / "external tools" / "Slicer.cmd")
        self._write_config(package, external_dataset.resolve(), external_slicer.resolve())

        result = self._resolve(package, reader)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), external_slicer.resolve())

    def test_launcher_rejects_malformed_config_and_missing_dataset_roots(self) -> None:
        package, _, reader = self._stage_package()
        config_path = package / "review-config.json"
        invalid_values = (
            "{",
            json.dumps({"schema_version": "1.0", "slicer_path": ""}),
            json.dumps(
                {
                    "schema_version": "2.0",
                    "dataset_root": "dataset root with spaces",
                    "slicer_path": "",
                }
            ),
            json.dumps(
                {
                    "schema_version": "1.0",
                    "dataset_root": "missing dataset",
                    "slicer_path": "",
                }
            ),
            json.dumps(
                {
                    "schema_version": "1.0",
                    "dataset_root": "dataset root with spaces",
                    "slicer_path": 5,
                }
            ),
        )
        for value in invalid_values:
            with self.subTest(value=value):
                config_path.write_text(value, encoding="utf-8")
                result = self._resolve(package, reader)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr.strip())

    def test_launcher_refuses_reader_folder_without_queue(self) -> None:
        package, _, reader = self._stage_package()
        (reader / "queue.json").unlink()
        fake_slicer = self._fake_slicer(package / "Fake Slicer.cmd")
        self._write_config(package, "dataset root with spaces", fake_slicer)

        result = self._resolve(package, reader)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("queue.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
