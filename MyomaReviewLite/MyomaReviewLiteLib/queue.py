"""Artifact-derived status and navigation for independent reader queues."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .domain import (
    MODULE_VERSION,
    SCHEMA_VERSION,
    DatasetManifest,
    ReviewPhase,
    ReviewState,
    ValidationError,
    _non_empty_text,
)
from .storage import CaseStorage


_TRAILING_NUMBER_RE = re.compile(r"^(.*?)([0-9]+)$")


class QueueStatus(str, Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    ATTENTION = "attention"


class TransitionBlocked(RuntimeError):
    """Raised when unresolved interactive state makes navigation unsafe."""


class ReviewSaveCoordinator:
    """Track segmentation dirtiness and enforce save-before-navigation order."""

    def __init__(self) -> None:
        self.dirty = False

    def mark_dirty(self) -> None:
        self.dirty = True

    def clear_dirty(self) -> None:
        self.dirty = False

    def autosave(self, save: Callable[[], None]) -> bool:
        if not self.dirty:
            return False
        save()
        self.dirty = False
        return True

    def transition(
        self,
        *,
        unresolved_roi: bool,
        save_current: Callable[[], None],
        teardown_current: Callable[[], None],
        open_target: Callable[[], None],
    ) -> None:
        if unresolved_roi:
            raise TransitionBlocked("save or cancel the unresolved ROI edit before navigation")
        save_current()
        self.dirty = False
        teardown_current()
        open_target()


@dataclass(frozen=True)
class QueueEntry:
    case_id: str
    status: QueueStatus
    diagnostic: str | None = None


def numeric_case_key(case_id: str) -> tuple[Any, ...]:
    """Sort subject IDs naturally while retaining deterministic lexical fallback."""

    match = _TRAILING_NUMBER_RE.fullmatch(case_id)
    if match is None:
        return (1, case_id.casefold(), case_id)
    return (0, match.group(1).casefold(), int(match.group(2)), case_id.casefold())


def _regular_artifact(path: Path) -> tuple[bool, str | None]:
    if not path.exists() and not path.is_symlink():
        return False, None
    if path.is_symlink() or not path.is_file():
        return True, f"{path.name} must be a regular file"
    return True, None


def _load_json_object(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path.name} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValidationError(f"{path.name} must contain a JSON object")
    return value


def _validate_draft(
    payload: Mapping[str, Any], manifest: DatasetManifest, case_id: str
) -> ReviewState:
    state = ReviewState.from_draft(payload)
    if (
        state.study_id != manifest.study_id
        or state.reader_id != manifest.reader_id
        or state.case_id != case_id
    ):
        raise ValidationError("draft identity does not match the queue")
    if state.phase not in {ReviewPhase.BLIND.value, ReviewPhase.AI_REVEALED.value}:
        raise ValidationError("draft contains a final review phase")
    if state.completed_at is not None:
        raise ValidationError("unfinished draft cannot contain completed_at")
    return state


def _validate_annotation(
    payload: Mapping[str, Any], manifest: DatasetManifest, case_id: str
) -> str:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("annotations.json schema_version is unsupported")
    _non_empty_text(payload.get("module_version"), "module_version")
    if payload.get("study_id") != manifest.study_id:
        raise ValidationError("annotation study_id does not match the queue")
    if payload.get("reader_id") != manifest.reader_id:
        raise ValidationError("annotation reader_id does not match the queue")
    if payload.get("case_id") != case_id:
        raise ValidationError("annotation case_id does not match the queue")
    status = _non_empty_text(payload.get("status"), "status")
    phase = (
        ReviewPhase.COMPLETE.value
        if status == "complete"
        else ReviewPhase.TERMINAL.value
    )
    state_payload = dict(payload)
    state_payload["phase"] = phase
    state = ReviewState.from_draft(state_payload)
    state.to_annotation(str(payload.get("module_version", MODULE_VERSION)), status=status)
    corrected = payload.get("corrected_labelmap")
    if status == "complete" and corrected != "corrected_labels.nii.gz":
        raise ValidationError("complete annotation must reference corrected_labels.nii.gz")
    if status != "complete" and corrected is not None:
        raise ValidationError("early-stop annotation cannot reference corrected labels")
    return status


def classify_case(manifest: DatasetManifest, case_id: str) -> QueueEntry:
    """Classify one case strictly from its four possible result artifacts."""

    storage = CaseStorage(manifest.output_root, case_id)
    artifacts = {
        "draft": storage.draft_path,
        "draft_segmentation": storage.draft_segmentation_path,
        "annotation": storage.annotation_path,
        "corrected_labelmap": storage.corrected_labelmap_path,
    }
    presence: dict[str, bool] = {}
    for name, path in artifacts.items():
        present, problem = _regular_artifact(path)
        presence[name] = present
        if problem is not None:
            return QueueEntry(case_id, QueueStatus.ATTENTION, problem)

    if presence["annotation"]:
        if presence["draft"] or presence["draft_segmentation"]:
            return QueueEntry(
                case_id,
                QueueStatus.ATTENTION,
                "final annotation conflicts with remaining draft artifacts",
            )
        try:
            status = _validate_annotation(
                _load_json_object(storage.annotation_path), manifest, case_id
            )
        except (OSError, ValueError, TypeError) as exc:
            return QueueEntry(case_id, QueueStatus.ATTENTION, str(exc))
        if status == "complete" and not presence["corrected_labelmap"]:
            return QueueEntry(
                case_id,
                QueueStatus.ATTENTION,
                "complete annotation is missing corrected_labels.nii.gz",
            )
        if status != "complete" and presence["corrected_labelmap"]:
            return QueueEntry(
                case_id,
                QueueStatus.ATTENTION,
                "early-stop annotation conflicts with corrected_labels.nii.gz",
            )
        return QueueEntry(case_id, QueueStatus.COMPLETE)

    if presence["corrected_labelmap"]:
        return QueueEntry(
            case_id,
            QueueStatus.ATTENTION,
            "corrected_labels.nii.gz has no final annotations.json",
        )
    if presence["draft"]:
        try:
            state = _validate_draft(
                _load_json_object(storage.draft_path), manifest, case_id
            )
        except (OSError, ValueError, TypeError) as exc:
            return QueueEntry(case_id, QueueStatus.ATTENTION, str(exc))
        if presence["draft_segmentation"] and state.phase != ReviewPhase.AI_REVEALED.value:
            return QueueEntry(
                case_id,
                QueueStatus.ATTENTION,
                "blind draft conflicts with draft.seg.nrrd",
            )
        return QueueEntry(case_id, QueueStatus.IN_PROGRESS)
    if presence["draft_segmentation"]:
        return QueueEntry(
            case_id,
            QueueStatus.ATTENTION,
            "draft.seg.nrrd has no draft.json",
        )
    return QueueEntry(case_id, QueueStatus.NEW)


@dataclass(frozen=True)
class QueueSummary:
    entries: tuple[QueueEntry, ...]

    @classmethod
    def scan(cls, manifest: DatasetManifest) -> "QueueSummary":
        entries = [classify_case(manifest, case.case_id) for case in manifest.cases]
        return cls(tuple(sorted(entries, key=lambda entry: numeric_case_key(entry.case_id))))

    @property
    def counts(self) -> dict[QueueStatus, int]:
        return {
            status: sum(entry.status is status for entry in self.entries)
            for status in QueueStatus
        }

    def entry(self, case_id: str) -> QueueEntry:
        for entry in self.entries:
            if entry.case_id == case_id:
                return entry
        raise KeyError(case_id)

    def first_unfinished(self) -> QueueEntry | None:
        for entry in self.entries:
            if entry.status in {QueueStatus.NEW, QueueStatus.IN_PROGRESS}:
                return entry
        return None

    def neighbour(self, case_id: str, step: int) -> QueueEntry | None:
        """The nearest openable case before (step -1) or after (+1) ``case_id``.

        Attention cases cannot be opened, so they are skipped; the list does
        not wrap, and an unknown ``case_id`` has no neighbour.
        """
        index = next(
            (i for i, entry in enumerate(self.entries) if entry.case_id == case_id), None
        )
        if index is None:
            return None
        index += step
        while 0 <= index < len(self.entries):
            if self.entries[index].status is not QueueStatus.ATTENTION:
                return self.entries[index]
            index += step
        return None

    def next_unfinished(self, after_case_id: str) -> QueueEntry | None:
        if not self.entries:
            return None
        try:
            current = next(
                index
                for index, entry in enumerate(self.entries)
                if entry.case_id == after_case_id
            )
        except StopIteration as exc:
            raise KeyError(after_case_id) from exc
        for offset in range(1, len(self.entries) + 1):
            candidate = self.entries[(current + offset) % len(self.entries)]
            if candidate.status in {QueueStatus.NEW, QueueStatus.IN_PROGRESS}:
                return candidate
        return None
