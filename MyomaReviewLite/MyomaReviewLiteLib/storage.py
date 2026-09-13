"""Minimal atomic storage for one MyomaReviewLite case."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .domain import ReviewState, ValidationError, _identifier


class StorageError(RuntimeError):
    """Base class for case-storage failures."""


class ImmutableCaseError(StorageError):
    """Raised when a completed case would be overwritten."""


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _read_source(source: str | Path | bytes | bytearray) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    path = Path(source).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise StorageError(f"storage source is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise StorageError(f"could not read storage source: {path}") from exc


class CaseStorage:
    """Own the exact files for one case and refuse completed-case overwrite."""

    def __init__(self, output_root: str | Path, case_id: str) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.case_id = _identifier(case_id, "case_id")
        self.case_directory = self.output_root / self.case_id
        self.draft_path = self.case_directory / "draft.json"
        self.draft_segmentation_path = self.case_directory / "draft.seg.nrrd"
        self.annotation_path = self.case_directory / "annotations.json"
        self.corrected_labelmap_path = self.case_directory / "corrected_labels.nii.gz"

    @property
    def is_complete(self) -> bool:
        return self.annotation_path.is_file() and not self.annotation_path.is_symlink()

    @property
    def read_only(self) -> bool:
        return self.is_complete

    def _refuse_if_complete(self) -> None:
        if self.is_complete:
            raise ImmutableCaseError(f"case {self.case_id} has an immutable annotations.json")

    def load_draft(self) -> ReviewState | None:
        if not self.draft_path.exists():
            return None
        if self.draft_path.is_symlink() or not self.draft_path.is_file():
            raise StorageError("draft.json must be a regular file")
        try:
            payload = json.loads(self.draft_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError("draft.json could not be read") from exc
        try:
            state = ReviewState.from_draft(payload)
        except (TypeError, ValueError) as exc:
            raise StorageError("draft.json does not contain valid Lite state") from exc
        if state.case_id != self.case_id:
            raise StorageError("draft case_id does not match the selected case")
        return state

    def load_annotation(self) -> dict[str, Any] | None:
        if not self.annotation_path.exists():
            return None
        if self.annotation_path.is_symlink() or not self.annotation_path.is_file():
            raise StorageError("annotations.json must be a regular file")
        try:
            payload = json.loads(self.annotation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError("annotations.json could not be read") from exc
        if not isinstance(payload, dict) or payload.get("case_id") != self.case_id:
            raise StorageError("annotations.json does not contain this case")
        return payload

    def save_draft(
        self,
        state: ReviewState,
        segmentation: str | Path | bytes | bytearray | None = None,
    ) -> None:
        self._refuse_if_complete()
        if state.case_id != self.case_id:
            raise StorageError("state case_id does not match the selected case")
        payload = state.to_draft()
        self._validate_reveal_record(payload)
        if segmentation is not None:
            _atomic_bytes(self.draft_segmentation_path, _read_source(segmentation))
        _atomic_bytes(self.draft_path, _json_bytes(payload))

    def _validate_reveal_record(self, payload: dict[str, Any]) -> None:
        """Corrections may change the current annotation, never its baseline."""
        previous = self.load_draft()
        if previous is not None and previous.ai_revealed_at is not None:
            if payload.get("ai_revealed_at") != previous.ai_revealed_at:
                raise StorageError("the original AI reveal timestamp cannot be changed")
            if payload.get("blinded_snapshot") != previous.blinded_snapshot:
                raise StorageError("the original blinded annotations cannot be changed")

    def remove_drafts(self) -> None:
        for path in (self.draft_path, self.draft_segmentation_path):
            try:
                if path.exists() and not path.is_symlink():
                    path.unlink()
            except OSError as exc:
                raise StorageError(f"could not remove draft file: {path}") from exc

    def publish_complete(
        self,
        annotation: dict[str, Any],
        corrected_labelmap: str | Path | bytes | bytearray,
    ) -> None:
        self._refuse_if_complete()
        self._validate_annotation(annotation, expected_status="complete")
        if annotation.get("corrected_labelmap") != "corrected_labels.nii.gz":
            raise StorageError("complete annotation must point to corrected_labels.nii.gz")
        # The labelmap becomes visible before the JSON commit marker; if the
        # process dies here, the case remains resumable rather than complete.
        _atomic_bytes(self.corrected_labelmap_path, _read_source(corrected_labelmap))
        _atomic_bytes(self.annotation_path, _json_bytes(annotation))
        self.remove_drafts()

    def publish_early_stop(self, annotation: dict[str, Any]) -> None:
        self._refuse_if_complete()
        self._validate_annotation(annotation, expected_status=None)
        if annotation.get("status") == "complete":
            raise StorageError("publish_early_stop requires a terminal early-stop status")
        if annotation.get("corrected_labelmap") is not None:
            raise StorageError("early-stop annotation cannot contain a corrected labelmap")
        _atomic_bytes(self.annotation_path, _json_bytes(annotation))
        self.remove_drafts()

    def _validate_annotation(self, annotation: Any, expected_status: str | None) -> None:
        if not isinstance(annotation, dict):
            raise StorageError("annotation must be a JSON object")
        if annotation.get("case_id") != self.case_id:
            raise StorageError("annotation case_id does not match the selected case")
        status = annotation.get("status")
        if expected_status is not None and status != expected_status:
            raise StorageError(f"annotation status must be {expected_status}")
        self._validate_reveal_record(annotation)
