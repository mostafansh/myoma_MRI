"""Create and inspect validated, independent MyomaReviewLite reader queues."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import nibabel as nib
import numpy as np

from .domain import DatasetManifest, ManifestError, SCHEMA_VERSION, _identifier
from .queue import QueueStatus, QueueSummary, numeric_case_key


_CASE_DIRECTORY_RE = re.compile(r"^sub-[0-9]+$")
_RESULT_NAMES = frozenset(
    {"draft.json", "draft.seg.nrrd", "annotations.json", "corrected_labels.nii.gz"}
)


class QueueCreationError(RuntimeError):
    """Raised when a queue cannot be created without ambiguity or data risk."""


@dataclass(frozen=True)
class QueueCreationResult:
    queue_path: Path
    case_ids: tuple[str, ...]
    created: bool


@dataclass(frozen=True)
class ReviewConfig:
    config_path: Path
    dataset_root: Path
    slicer_path: Path | None


@dataclass(frozen=True)
class MigrationResult:
    queue_path: Path
    case_ids: tuple[str, ...]
    changed: bool


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _regular_directory(value: str | Path, field_name: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_dir():
        raise QueueCreationError(f"{field_name} is not a regular directory: {path}")
    return path


def load_review_config(path: str | Path) -> ReviewConfig:
    config_path = Path(path).expanduser().resolve()
    if config_path.is_symlink() or not config_path.is_file():
        raise QueueCreationError(f"configuration file was not found: {config_path}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueueCreationError(f"configuration is malformed: {config_path}") from exc
    if not isinstance(payload, Mapping):
        raise QueueCreationError("configuration must contain a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise QueueCreationError(f"configuration schema_version must be {SCHEMA_VERSION}")
    dataset_value = payload.get("dataset_root")
    if not isinstance(dataset_value, str) or not dataset_value.strip():
        raise QueueCreationError("configuration dataset_root must be a non-empty string")
    slicer_value = payload.get("slicer_path", "")
    if not isinstance(slicer_value, str):
        raise QueueCreationError("configuration slicer_path must be a string")
    base = config_path.parent
    dataset_path = Path(dataset_value.strip()).expanduser()
    if not dataset_path.is_absolute():
        dataset_path = base / dataset_path
    dataset_root = _regular_directory(dataset_path, "configured dataset root")
    slicer_path: Path | None = None
    if slicer_value.strip():
        slicer_path = Path(slicer_value.strip()).expanduser()
        if not slicer_path.is_absolute():
            slicer_path = base / slicer_path
        slicer_path = slicer_path.resolve()
    return ReviewConfig(config_path, dataset_root, slicer_path)


def _resolve_data_root(
    data_root: str | Path | None,
    config_path: str | Path | None,
) -> Path:
    if data_root is not None:
        return _regular_directory(data_root, "data root")
    selected_config = config_path or (_package_root() / "review-config.json")
    return load_review_config(selected_config).dataset_root


def _is_inside(path: Path, possible_parent: Path) -> bool:
    try:
        path.relative_to(possible_parent)
    except ValueError:
        return False
    return True


def _read_case_list(value: str | Path | Sequence[str] | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, Path)):
        items = [str(item).strip() for item in value]
    else:
        text_value = str(value)
        possible_path = Path(text_value).expanduser()
        if possible_path.is_file():
            try:
                text = possible_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise QueueCreationError(f"case list could not be read: {possible_path}") from exc
            if possible_path.suffix.casefold() == ".json":
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise QueueCreationError("case-list JSON is malformed") from exc
                if isinstance(payload, dict):
                    payload = payload.get("case_ids")
                if not isinstance(payload, list):
                    raise QueueCreationError("case-list JSON must be an array or contain case_ids")
                items = [str(item).strip() for item in payload]
            else:
                items = [item.strip() for item in re.split(r"[,\r\n]+", text)]
        else:
            items = [item.strip() for item in text_value.split(",")]
    items = [item for item in items if item]
    if not items:
        raise QueueCreationError("case list must contain at least one case ID")
    duplicates = sorted({item for item in items if items.count(item) > 1}, key=numeric_case_key)
    if duplicates:
        raise QueueCreationError("duplicate case IDs: " + ", ".join(duplicates))
    for item in items:
        try:
            _identifier(item, "case_id")
        except ValueError as exc:
            raise QueueCreationError(str(exc)) from exc
    return items


def _discover_case_ids(data_root: Path) -> list[str]:
    case_ids = [
        path.name
        for path in data_root.iterdir()
        if path.is_dir() and not path.is_symlink() and _CASE_DIRECTORY_RE.fullmatch(path.name)
    ]
    if not case_ids:
        raise QueueCreationError(f"no subject directories were found under {data_root}")
    return sorted(case_ids, key=numeric_case_key)


def _expected_source_paths(data_root: Path, case_id: str) -> dict[str, Path]:
    directory = data_root / case_id
    if directory.is_symlink() or not directory.is_dir():
        raise QueueCreationError(f"unknown case ID: {case_id}")
    expected = {
        "sagittal": directory / f"{case_id}_sag_t2.nii.gz",
        "axial": directory / f"{case_id}_ax_t2.nii.gz",
        "coronal": directory / f"{case_id}_cor_t2.nii.gz",
        "mask": directory / f"{case_id}_mask_0.nii.gz",
    }
    for path in expected.values():
        if path.is_symlink() or not path.is_file():
            raise QueueCreationError(f"expected source file is missing or unsafe: {path}")
    nifti_names = sorted(path.name for path in directory.glob("*.nii.gz"))
    expected_names = sorted(path.name for path in expected.values())
    if nifti_names != expected_names:
        unexpected = sorted(set(nifti_names) - set(expected_names))
        missing = sorted(set(expected_names) - set(nifti_names))
        details = []
        if missing:
            details.append("missing=" + ", ".join(missing))
        if unexpected:
            details.append("unexpected=" + ", ".join(unexpected))
        raise QueueCreationError(
            f"case {case_id} filenames do not match the expected four files"
            + (": " + "; ".join(details) if details else "")
        )
    return {name: path.resolve() for name, path in expected.items()}


def _validate_case_images(case_id: str, paths: dict[str, Path]) -> None:
    images: dict[str, nib.spatialimages.SpatialImage] = {}
    try:
        for name, path in paths.items():
            images[name] = nib.load(str(path), mmap=False)
    except Exception as exc:
        raise QueueCreationError(f"case {case_id} contains an unreadable NIfTI file") from exc
    reference = images["sagittal"]
    reference_shape = tuple(int(value) for value in reference.shape)
    reference_affine = np.asarray(reference.affine, dtype=float)
    for name, image in images.items():
        if len(image.shape) != 3:
            raise QueueCreationError(f"case {case_id} source images must be three-dimensional")
    # The mask is edited on the acquired sagittal grid, so it must match it
    # exactly. Axial and coronal keep their own acquired grids, already
    # registered into the sagittal's world space, and are only resliced for
    # display.
    mask = images["mask"]
    if tuple(int(value) for value in mask.shape) != reference_shape or not np.allclose(
        np.asarray(mask.affine, dtype=float), reference_affine, rtol=0.0, atol=1e-5
    ):
        raise QueueCreationError(
            f"case {case_id} mask geometry does not match the sagittal reference"
        )
    try:
        mask_values = np.unique(np.asanyarray(images["mask"].dataobj))
    except Exception as exc:
        raise QueueCreationError(f"case {case_id} mask data could not be read") from exc
    if (
        not np.all(np.isfinite(mask_values))
        or not np.all(mask_values == np.floor(mask_values))
        or not set(int(value) for value in mask_values).issubset({0, 1, 2, 3, 4})
    ):
        raise QueueCreationError(f"case {case_id} mask must contain only labels 0 through 4")


def _manifest_payload(
    *,
    reader_id: str,
    study_id: str,
    data_root: Path,
    case_ids: Sequence[str],
) -> dict[str, Any]:
    cases = []
    for case_id in case_ids:
        paths = _expected_source_paths(data_root, case_id)
        _validate_case_images(case_id, paths)
        cases.append(
            {
                "case_id": case_id,
                "volumes": {
                    "sagittal": f"{case_id}/{paths['sagittal'].name}",
                    "axial": f"{case_id}/{paths['axial'].name}",
                    "coronal": f"{case_id}/{paths['coronal'].name}",
                },
                "ai_mask": f"{case_id}/{paths['mask'].name}",
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": "dataset",
        "study_id": study_id,
        "reader_id": reader_id,
        "output_root": ".",
        "cases": cases,
    }


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _start_command(launcher_path: Path, reader_dir: Path) -> str:
    try:
        relative_launcher = os.path.relpath(launcher_path, reader_dir)
    except ValueError as exc:
        raise QueueCreationError(
            "generic launcher and reader directory must be on the same drive"
        ) from exc
    relative_launcher = relative_launcher.replace("/", "\\")
    return (
        "@echo off\r\n"
        "powershell.exe -NoProfile -ExecutionPolicy Bypass "
        f'-File "%~dp0{relative_launcher}" -ReaderDir "%~dp0."\r\n'
        "if errorlevel 1 pause\r\n"
    )


def _has_results(reader_dir: Path) -> bool:
    if not reader_dir.exists():
        return False
    return any(
        path.is_file() and path.name in _RESULT_NAMES
        for path in reader_dir.rglob("*")
    )


def create_queue(
    *,
    reader_id: str,
    study_id: str,
    data_root: str | Path,
    reader_dir: str | Path,
    case_list: str | Path | Sequence[str] | None = None,
    launcher_path: str | Path | None = None,
) -> QueueCreationResult:
    try:
        normalized_reader_id = _identifier(reader_id, "reader_id")
        normalized_study_id = _identifier(study_id, "study_id")
    except ValueError as exc:
        raise QueueCreationError(str(exc)) from exc
    source = Path(data_root).expanduser().resolve()
    destination = Path(reader_dir).expanduser().resolve()
    if source.is_symlink() or not source.is_dir():
        raise QueueCreationError(f"data root is not a regular directory: {source}")
    if _is_inside(destination, source):
        raise QueueCreationError("reader output directory cannot be inside the source dataset")
    launcher = Path(
        launcher_path or destination.parent / "Start-MyomaReviewLite.ps1"
    ).expanduser().resolve()
    if launcher.is_symlink() or not launcher.is_file():
        raise QueueCreationError(f"generic launcher is missing or unsafe: {launcher}")
    start_path = destination / "Start Review.cmd"
    start_text = _start_command(launcher, destination)

    available = _discover_case_ids(source)
    requested = _read_case_list(case_list)
    if requested is None:
        selected = available
    else:
        unknown = sorted(set(requested) - set(available), key=numeric_case_key)
        if unknown:
            raise QueueCreationError("unknown case IDs: " + ", ".join(unknown))
        selected = sorted(requested, key=numeric_case_key)

    payload = _manifest_payload(
        reader_id=normalized_reader_id,
        study_id=normalized_study_id,
        data_root=source,
        case_ids=selected,
    )
    queue_path = destination / "queue.json"
    expected_text = _json_text(payload)
    created = not queue_path.exists()
    if queue_path.exists():
        if queue_path.is_symlink() or not queue_path.is_file():
            raise QueueCreationError("conflicting queue.json is not a regular file")
        try:
            existing = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QueueCreationError("conflicting queue.json cannot be read") from exc
        if existing != payload:
            suffix = " and result artifacts already exist" if _has_results(destination) else ""
            raise QueueCreationError("refusing to replace a conflicting queue" + suffix)
    else:
        if _has_results(destination):
            raise QueueCreationError("result artifacts exist without a matching queue.json")
        _atomic_text(start_path, start_text)
        _atomic_text(queue_path, expected_text)

    if not start_path.is_file() or start_path.read_text(encoding="utf-8") != start_text:
        _atomic_text(start_path, start_text)
    return QueueCreationResult(queue_path, tuple(selected), created)


def queue_report(
    reader_dir: str | Path,
    *,
    data_root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(reader_dir).expanduser().resolve()
    queue_path = directory / "queue.json"
    try:
        source_root = _resolve_data_root(data_root, config_path)
        manifest = DatasetManifest.from_file(queue_path, source_root=source_root)
    except (ManifestError, OSError) as exc:
        raise QueueCreationError(f"reader queue could not be opened: {queue_path}") from exc
    summary = QueueSummary.scan(manifest)
    first = summary.first_unfinished()
    return {
        "reader_id": manifest.reader_id,
        "study_id": manifest.study_id,
        "total": len(summary.entries),
        "counts": {
            status.value: summary.counts[status]
            for status in sorted(QueueStatus, key=lambda value: value.value)
        },
        "first_unfinished": None if first is None else first.case_id,
        "entries": [
            {
                "case_id": entry.case_id,
                "status": entry.status.value,
                **({"diagnostic": entry.diagnostic} if entry.diagnostic else {}),
            }
            for entry in summary.entries
        ],
    }


def _artifact_hashes(reader_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(reader_dir.rglob("*")):
        if path.name not in _RESULT_NAMES:
            continue
        if path.is_symlink() or not path.is_file():
            raise QueueCreationError(f"result artifact is not a regular file: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        hashes[path.relative_to(reader_dir).as_posix()] = digest.hexdigest()
    return hashes


def _portable_source(case_id: str, filename: str) -> str:
    return PurePosixPath(case_id, filename).as_posix()


def _source_matches(
    value: Any,
    *,
    expected_path: Path,
    expected_portable: str,
) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw.resolve() == expected_path.resolve()
    normalized = PurePosixPath(value.replace("\\", "/")).as_posix()
    return normalized == expected_portable


def migrate_paths(
    *,
    reader_dir: str | Path,
    data_root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> MigrationResult:
    directory = _regular_directory(reader_dir, "reader directory")
    source_root = _resolve_data_root(data_root, config_path)
    queue_path = directory / "queue.json"
    if queue_path.is_symlink() or not queue_path.is_file():
        raise QueueCreationError(f"reader queue was not found: {queue_path}")
    try:
        original_bytes = queue_path.read_bytes()
        payload = json.loads(original_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueueCreationError(f"reader queue is malformed: {queue_path}") from exc
    if not isinstance(payload, dict):
        raise QueueCreationError("reader queue must contain a JSON object")
    if payload.get("reader_id") != directory.name:
        raise QueueCreationError("queue reader_id does not match the reader directory")
    if payload.get("output_root") != ".":
        raise QueueCreationError("queue output_root must remain '.'")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise QueueCreationError("queue cases must be a non-empty array")
    case_ids = [case.get("case_id") if isinstance(case, dict) else None for case in cases]
    if any(not isinstance(case_id, str) for case_id in case_ids):
        raise QueueCreationError("queue cases contain an invalid case_id")
    typed_case_ids = [str(case_id) for case_id in case_ids]
    if typed_case_ids != sorted(typed_case_ids, key=numeric_case_key):
        raise QueueCreationError("queue cases are not in numeric case order")
    assigned = set(typed_case_ids)
    if len(assigned) != len(typed_case_ids):
        raise QueueCreationError("queue contains duplicate case assignments")

    artifact_hashes_before = _artifact_hashes(directory)
    for relative_name in artifact_hashes_before:
        artifact_case = PurePosixPath(relative_name).parts[0]
        if artifact_case not in assigned:
            raise QueueCreationError(
                f"result artifact belongs to an unassigned case: {relative_name}"
            )

    changed = False
    for case in cases:
        case_id = str(case["case_id"])
        paths = _expected_source_paths(source_root, case_id)
        _validate_case_images(case_id, paths)
        volumes = case.get("volumes")
        if not isinstance(volumes, dict):
            raise QueueCreationError(f"case {case_id} volumes are malformed")
        expected_values = {
            "sagittal": _portable_source(case_id, paths["sagittal"].name),
            "axial": _portable_source(case_id, paths["axial"].name),
            "coronal": _portable_source(case_id, paths["coronal"].name),
            "mask": _portable_source(case_id, paths["mask"].name),
        }
        for name in ("sagittal", "axial", "coronal"):
            if not _source_matches(
                volumes.get(name),
                expected_path=paths[name],
                expected_portable=expected_values[name],
            ):
                raise QueueCreationError(
                    f"case {case_id} {name} source does not match the configured dataset"
                )
            if volumes[name] != expected_values[name]:
                volumes[name] = expected_values[name]
                changed = True
        if not _source_matches(
            case.get("ai_mask"),
            expected_path=paths["mask"],
            expected_portable=expected_values["mask"],
        ):
            raise QueueCreationError(
                f"case {case_id} mask source does not match the configured dataset"
            )
        if case["ai_mask"] != expected_values["mask"]:
            case["ai_mask"] = expected_values["mask"]
            changed = True

    try:
        manifest = DatasetManifest.from_dict(
            payload, queue_path, source_root=source_root
        )
    except (ManifestError, ValueError, TypeError) as exc:
        raise QueueCreationError(f"reader queue could not be validated: {exc}") from exc
    summary = QueueSummary.scan(manifest)
    attention = [entry for entry in summary.entries if entry.status is QueueStatus.ATTENTION]
    if attention:
        details = "; ".join(
            f"{entry.case_id}: {entry.diagnostic}" for entry in attention
        )
        raise QueueCreationError(f"result artifacts require attention: {details}")

    if not changed:
        return MigrationResult(queue_path, tuple(typed_case_ids), False)
    _atomic_text(queue_path, _json_text(payload))
    if _artifact_hashes(directory) != artifact_hashes_before:
        _atomic_text(queue_path, original_bytes.decode("utf-8"))
        raise QueueCreationError(
            "result artifacts changed during migration; the original queue was restored"
        )
    return MigrationResult(queue_path, tuple(typed_case_ids), True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="validate sources and create a reader queue")
    create.add_argument("--reader-id", required=True)
    create.add_argument("--study-id", required=True)
    create.add_argument("--reader-dir", required=True)
    create.add_argument("--case-list")
    create.add_argument("--config")
    create.add_argument("--data-root")
    status = commands.add_parser("status", help="scan result artifacts and report queue status")
    status.add_argument("--reader-dir", required=True)
    status.add_argument("--config")
    status.add_argument("--data-root")
    status.add_argument("--json", action="store_true", dest="as_json")
    migrate = commands.add_parser(
        "migrate-paths", help="atomically convert queue sources to portable paths"
    )
    migrate.add_argument("--reader-dir", required=True)
    migrate.add_argument("--config")
    migrate.add_argument("--data-root")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "create":
            data_root = _resolve_data_root(arguments.data_root, arguments.config)
            result = create_queue(
                reader_id=arguments.reader_id,
                study_id=arguments.study_id,
                data_root=data_root,
                reader_dir=arguments.reader_dir,
                case_list=arguments.case_list,
                launcher_path=_package_root() / "Start-MyomaReviewLite.ps1",
            )
            action = "created" if result.created else "already matches"
            print(f"Queue {action}: {result.queue_path} ({len(result.case_ids)} cases)")
            return 0
        if arguments.command == "migrate-paths":
            result = migrate_paths(
                reader_dir=arguments.reader_dir,
                data_root=arguments.data_root,
                config_path=arguments.config,
            )
            action = "migrated" if result.changed else "already portable"
            print(
                f"Queue {action}: {result.queue_path} ({len(result.case_ids)} cases)"
            )
            return 0
        report = queue_report(
            arguments.reader_dir,
            data_root=arguments.data_root,
            config_path=arguments.config,
        )
        if arguments.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            counts = report["counts"]
            print(
                f"Reader {report['reader_id']}: {counts['complete']}/{report['total']} complete; "
                f"{counts['in_progress']} in progress; {counts['new']} new; "
                f"{counts['attention']} attention"
            )
            for entry in report["entries"]:
                diagnostic = f" - {entry['diagnostic']}" if "diagnostic" in entry else ""
                print(f"{entry['case_id']}: {entry['status']}{diagnostic}")
        return 0
    except QueueCreationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
