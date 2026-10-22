"""Framework-independent contracts for the single-case Lite workflow.

The module intentionally has no Slicer, Qt, VTK, NumPy, or MyomaReviewLib
imports.  It is the source of truth for the public JSON schema and the
irreversible blinded/revealed state transition.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from numbers import Integral
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "1.0"
MODULE_VERSION = "1.1.0"
LABEL_NAMES = {
    0: "background",
    1: "uterine wall",
    2: "uterine cavity",
    3: "myoma",
    4: "Nabothian cyst",
}
AI_LABEL_VALUES = frozenset(LABEL_NAMES)
VOLUME_NAMES = ("sagittal", "axial", "coronal")
VALID_STATUSES = frozenset(("complete", "no_uterus", "wrong_image", "cannot_assess"))
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LESION_RE = re.compile(r"^M[0-9]{2,}$")


class ValidationError(ValueError):
    """Raised when a Lite workflow value violates its public contract."""


class ManifestError(ValidationError):
    """Raised when a case manifest cannot be safely opened."""


def _read_manifest_payload(path: str | Path) -> tuple[Mapping[str, Any], Path]:
    manifest_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"could not read case manifest: {manifest_path}") from exc
    if not isinstance(payload, Mapping):
        raise ManifestError("case manifest must contain a JSON object")
    return payload, manifest_path


class ReviewPhase(str, Enum):
    BLIND = "blind"
    AI_REVEALED = "ai_revealed"
    COMPLETE = "complete"
    TERMINAL = "terminal"


class FIGOKind(str, Enum):
    STANDARD = "standard"
    HYBRID = "hybrid"
    UNCERTAIN = "uncertain"


class Type8Location(str, Enum):
    CERVICAL = "cervical"
    ROUND_LIGAMENT = "round_ligament"
    BROAD_LIGAMENT = "broad_ligament"
    PARASITIC_OR_OTHER_EXTRAUTERINE = "parasitic_or_other_extrauterine"


class UncertainReason(str, Enum):
    ENDOMETRIAL_INTERFACE_UNCLEAR = "endometrial_interface_unclear"
    SEROSAL_INTERFACE_UNCLEAR = "serosal_interface_unclear"
    BOTH_INTERFACES_UNCLEAR = "both_interfaces_unclear"
    OTHERWISE_NOT_CLASSIFIABLE = "otherwise_not_classifiable"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _utc_timestamp(value: Any, field_name: str) -> str:
    value = _non_empty_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValidationError(f"{field_name} must include a UTC timezone")
    return value


def _identifier(value: Any, field_name: str) -> str:
    value = _non_empty_text(value, field_name)
    if not _ID_RE.fullmatch(value):
        raise ValidationError(f"{field_name} contains unsupported path or characters")
    return value


def _finite_vector(value: Any, field_name: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValidationError(f"{field_name} must contain exactly three values")
    try:
        vector = tuple(float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must contain numeric values") from exc
    if not all(math.isfinite(component) for component in vector):
        raise ValidationError(f"{field_name} must contain finite values")
    return vector  # type: ignore[return-value]


def _matrix(value: Any, field_name: str = "ijk_to_ras") -> tuple[float, ...]:
    if isinstance(value, (list, tuple)) and len(value) == 4 and all(
        isinstance(row, (list, tuple)) for row in value
    ):
        value = [component for row in value for component in row]
    if not isinstance(value, (list, tuple)) or len(value) != 16:
        raise ValidationError(f"{field_name} must contain 16 values")
    try:
        matrix = tuple(float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must contain numeric values") from exc
    if not all(math.isfinite(component) for component in matrix):
        raise ValidationError(f"{field_name} must contain finite values")
    return matrix


@dataclass(frozen=True)
class ImageGeometry:
    """The geometry subset needed to compare Slicer-loaded image nodes."""

    dimensions: tuple[int, int, int]
    ijk_to_ras: tuple[float, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImageGeometry":
        dimensions = value.get("dimensions")
        if not isinstance(dimensions, (list, tuple)) or len(dimensions) != 3:
            raise ValidationError("dimensions must contain exactly three values")
        try:
            parsed_dimensions = tuple(int(item) for item in dimensions)
        except (TypeError, ValueError) as exc:
            raise ValidationError("dimensions must contain integers") from exc
        if any(item <= 0 for item in parsed_dimensions):
            raise ValidationError("dimensions must be positive")
        return cls(parsed_dimensions, _matrix(value.get("ijk_to_ras")))


def validate_common_geometry(geometries: Iterable[Mapping[str, Any] | ImageGeometry]) -> ImageGeometry:
    parsed = [
        item if isinstance(item, ImageGeometry) else ImageGeometry.from_dict(item)
        for item in geometries
    ]
    if not parsed:
        raise ValidationError("at least one image geometry is required")
    reference = parsed[0]
    for index, item in enumerate(parsed[1:], start=1):
        if item.dimensions != reference.dimensions:
            raise ValidationError(f"image {index} dimensions do not match sagittal reference")
        # NIfTI stores the header in float32, so an oblique native grid
        # saved and reloaded by Slicer drifts by ~1e-5 mm; 1e-3 mm is still
        # far below any voxel.
        if any(abs(a - b) > 1e-3 for a, b in zip(item.ijk_to_ras, reference.ijk_to_ras)):
            raise ValidationError(f"image {index} IJK-to-RAS geometry does not match sagittal reference")
    return reference


def validate_ai_mask_values(values: Iterable[Any]) -> None:
    """Validate values obtained from a Slicer mask or a test iterable."""

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValidationError("AI mask values must be iterable") from exc
    for value in iterator:
        if isinstance(value, bool) or not isinstance(value, Integral) or int(value) not in AI_LABEL_VALUES:
            raise ValidationError("AI mask values must be a subset of integer labels 0 through 4")


def validate_correction_mask_values(values: Iterable[Any]) -> None:
    """Validate reviewer output independently from the unchanged AI input."""
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValidationError("correction mask values must be iterable") from exc
    for value in iterator:
        if isinstance(value, bool) or not isinstance(value, Integral) or int(value) not in AI_LABEL_VALUES:
            raise ValidationError("correction mask values must be a subset of integer labels 0 through 4")


@dataclass(frozen=True)
class CaseManifest:
    schema_version: str
    study_id: str
    reader_id: str
    case_id: str
    volumes: dict[str, Path]
    ai_mask: Path
    output_root: Path
    manifest_path: Path

    @classmethod
    def from_file(
        cls, path: str | Path, *, source_root: str | Path | None = None
    ) -> "CaseManifest":
        payload, manifest_path = _read_manifest_payload(path)
        return cls.from_dict(payload, manifest_path, source_root=source_root)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        manifest_path: str | Path,
        *,
        source_root: str | Path | None = None,
    ) -> "CaseManifest":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ManifestError(f"schema_version must be {SCHEMA_VERSION}")
        base = Path(manifest_path).expanduser().resolve().parent
        source_base = (
            base
            if source_root is None
            else Path(source_root).expanduser().resolve()
        )
        volumes_payload = payload.get("volumes")
        if not isinstance(volumes_payload, Mapping):
            raise ManifestError("volumes must contain sagittal, axial, and coronal paths")
        missing = [name for name in VOLUME_NAMES if name not in volumes_payload]
        if missing:
            raise ManifestError(f"missing volume path(s): {', '.join(missing)}")

        def resolve_file(value: Any, field_name: str) -> Path:
            raw = Path(_non_empty_text(value, field_name)).expanduser()
            resolved = raw if raw.is_absolute() else source_base / raw
            resolved = resolved.resolve()
            if not resolved.is_file() or resolved.is_symlink():
                raise ManifestError(f"{field_name} does not point to a regular file: {resolved}")
            return resolved

        volumes = {name: resolve_file(volumes_payload[name], f"volumes.{name}") for name in VOLUME_NAMES}
        ai_mask = resolve_file(payload.get("ai_mask"), "ai_mask")
        output_root_raw = Path(_non_empty_text(payload.get("output_root"), "output_root")).expanduser()
        output_root = (output_root_raw if output_root_raw.is_absolute() else base / output_root_raw).resolve()
        return cls(
            schema_version=SCHEMA_VERSION,
            study_id=_identifier(payload.get("study_id"), "study_id"),
            reader_id=_identifier(payload.get("reader_id"), "reader_id"),
            case_id=_identifier(payload.get("case_id"), "case_id"),
            volumes=volumes,
            ai_mask=ai_mask,
            output_root=output_root,
            manifest_path=Path(manifest_path).expanduser().resolve(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "reader_id": self.reader_id,
            "case_id": self.case_id,
            "volumes": {name: str(self.volumes[name]) for name in VOLUME_NAMES},
            "ai_mask": str(self.ai_mask),
            "output_root": str(self.output_root),
        }


@dataclass(frozen=True)
class DatasetManifest:
    """A single manifest containing many independently reviewable cases."""

    schema_version: str
    manifest_type: str
    study_id: str
    reader_id: str
    output_root: Path
    cases: tuple[CaseManifest, ...]
    manifest_path: Path

    @classmethod
    def from_file(
        cls, path: str | Path, *, source_root: str | Path | None = None
    ) -> "DatasetManifest":
        payload, manifest_path = _read_manifest_payload(path)
        return cls.from_dict(payload, manifest_path, source_root=source_root)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        manifest_path: str | Path,
        *,
        source_root: str | Path | None = None,
    ) -> "DatasetManifest":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ManifestError(f"schema_version must be {SCHEMA_VERSION}")
        if payload.get("manifest_type") != "dataset":
            raise ManifestError("manifest_type must be dataset")
        cases_payload = payload.get("cases")
        if isinstance(cases_payload, (str, bytes, bytearray)) or not isinstance(cases_payload, Sequence):
            raise ManifestError("cases must be an array")
        if not cases_payload:
            raise ManifestError("cases must contain at least one case")

        study_id = _identifier(payload.get("study_id"), "study_id")
        reader_id = _identifier(payload.get("reader_id"), "reader_id")
        output_root_raw = Path(_non_empty_text(payload.get("output_root"), "output_root")).expanduser()
        output_root = (
            output_root_raw
            if output_root_raw.is_absolute()
            else Path(manifest_path).expanduser().resolve().parent / output_root_raw
        ).resolve()

        cases: list[CaseManifest] = []
        case_ids: set[str] = set()
        for index, case_payload in enumerate(cases_payload):
            if not isinstance(case_payload, Mapping):
                raise ManifestError(f"cases[{index}] must be an object")
            case_id = _identifier(case_payload.get("case_id"), f"cases[{index}].case_id")
            if case_id in case_ids:
                raise ManifestError(f"case_id values must be unique: {case_id}")
            case_ids.add(case_id)
            normalized_case = dict(case_payload)
            normalized_case.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "study_id": study_id,
                    "reader_id": reader_id,
                    "output_root": str(output_root),
                }
            )
            try:
                cases.append(
                    CaseManifest.from_dict(
                        normalized_case,
                        manifest_path,
                        source_root=source_root,
                    )
                )
            except (ManifestError, ValidationError) as exc:
                raise ManifestError(f"cases[{index}] is invalid: {exc}") from exc

        return cls(
            schema_version=SCHEMA_VERSION,
            manifest_type="dataset",
            study_id=study_id,
            reader_id=reader_id,
            output_root=output_root,
            cases=tuple(cases),
            manifest_path=Path(manifest_path).expanduser().resolve(),
        )

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    def case_by_id(self, case_id: str) -> CaseManifest:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise ManifestError(f"unknown case_id: {case_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_type": self.manifest_type,
            "study_id": self.study_id,
            "reader_id": self.reader_id,
            "output_root": str(self.output_root),
            "cases": [
                {
                    "case_id": case.case_id,
                    "volumes": {name: str(case.volumes[name]) for name in VOLUME_NAMES},
                    "ai_mask": str(case.ai_mask),
                }
                for case in self.cases
            ],
        }


def load_manifest(
    path: str | Path, *, source_root: str | Path | None = None
) -> CaseManifest | DatasetManifest:
    """Load either the legacy one-case or the combined dataset manifest format."""

    payload, manifest_path = _read_manifest_payload(path)
    if payload.get("manifest_type") == "dataset":
        return DatasetManifest.from_dict(
            payload, manifest_path, source_root=source_root
        )
    return CaseManifest.from_dict(payload, manifest_path, source_root=source_root)
@dataclass(frozen=True)
class FIGOClassification:
    kind: FIGOKind
    type: int | None = None
    location: str | None = None
    endometrial: int | None = None
    serosal: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        kind = FIGOKind(self.kind)
        object.__setattr__(self, "kind", kind)
        if kind is FIGOKind.STANDARD:
            if isinstance(self.type, bool) or not isinstance(self.type, int) or not 0 <= self.type <= 8:
                raise ValidationError("standard FIGO type must be an integer from 0 through 8")
            if self.type == 8:
                if self.location not in {item.value for item in Type8Location}:
                    raise ValidationError("standard FIGO type 8 requires a valid location")
            elif self.location is not None:
                raise ValidationError("only FIGO type 8 may have a location")
        elif kind is FIGOKind.HYBRID:
            if self.type is not None or self.location is not None or self.reason is not None:
                raise ValidationError("hybrid FIGO must contain only endometrial and serosal values")
            for name, value in (("endometrial", self.endometrial), ("serosal", self.serosal)):
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 8:
                    raise ValidationError(f"hybrid FIGO {name} value must be an integer from 0 through 8")
        else:
            if self.type is not None or self.location is not None or self.endometrial is not None or self.serosal is not None:
                raise ValidationError("uncertain FIGO must contain only a reason")
            if self.reason not in {item.value for item in UncertainReason}:
                raise ValidationError("uncertain FIGO reason is not recognized")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | "FIGOClassification") -> "FIGOClassification":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValidationError("figo must be an object")
        try:
            return cls(
                kind=FIGOKind(value["kind"]),
                type=value.get("type"),
                location=value.get("location"),
                endometrial=value.get("endometrial"),
                serosal=value.get("serosal"),
                reason=value.get("reason"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("figo object is invalid") from exc

    def to_dict(self) -> dict[str, Any]:
        if self.kind is FIGOKind.STANDARD:
            payload: dict[str, Any] = {"kind": self.kind.value, "type": self.type}
            if self.type == 8:
                payload["location"] = self.location
            return payload
        if self.kind is FIGOKind.HYBRID:
            return {
                "kind": self.kind.value,
                "endometrial": self.endometrial,
                "serosal": self.serosal,
            }
        return {"kind": self.kind.value, "reason": self.reason}


FIGO = FIGOClassification


@dataclass
class Lesion:
    lesion_id: str
    center_ras: tuple[float, float, float]
    size_mm: tuple[float, float, float]
    figo: FIGOClassification | None = None
    differential_diagnosis: str = ""
    comment: str = ""
    # A question for the senior reviewer: None is no question, "" a tag
    # without text yet.
    question: str | None = None

    def __post_init__(self) -> None:
        if not _LESION_RE.fullmatch(self.lesion_id):
            raise ValidationError("lesion_id must be a public ID such as M01")
        self.center_ras = _finite_vector(self.center_ras, "center_ras")
        self.size_mm = _finite_vector(self.size_mm, "size_mm")
        if any(value <= 0 for value in self.size_mm):
            raise ValidationError("size_mm must contain positive values")
        if self.figo is not None:
            self.figo = FIGOClassification.from_dict(self.figo)
        if not isinstance(self.differential_diagnosis, str):
            raise ValidationError("differential_diagnosis must be text")
        self.differential_diagnosis = self.differential_diagnosis.strip()
        if not isinstance(self.comment, str):
            raise ValidationError("comment must be text")
        self.comment = self.comment.strip()
        if self.question is not None:
            if not isinstance(self.question, str):
                raise ValidationError("question must be text")
            self.question = self.question.strip()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Lesion":
        if not isinstance(value, Mapping):
            raise ValidationError("lesion must be an object")
        lesion_id = value.get("lesion_id", value.get("id"))
        figo = value.get("figo")
        return cls(
            lesion_id=lesion_id,
            center_ras=value.get("center_ras"),
            size_mm=value.get("size_mm"),
            figo=None if figo is None else FIGOClassification.from_dict(figo),
            differential_diagnosis=value.get("differential_diagnosis", ""),
            comment=value.get("comment", ""),
            question=value.get("question"),
        )

    def to_dict(self, *, require_figo: bool = False) -> dict[str, Any]:
        if require_figo and self.figo is None:
            raise ValidationError(f"lesion {self.lesion_id} has no FIGO classification")
        return {
            "lesion_id": self.lesion_id,
            "center_ras": list(self.center_ras),
            "size_mm": list(self.size_mm),
            "figo": None if self.figo is None else self.figo.to_dict(),
            "differential_diagnosis": self.differential_diagnosis,
            "comment": self.comment,
            "question": self.question,
        }


@dataclass
class ReviewState:
    study_id: str
    reader_id: str
    case_id: str
    started_at: str
    phase: str = ReviewPhase.BLIND.value
    ai_revealed_at: str | None = None
    completed_at: str | None = None
    zero_myomas: bool = False
    lesions: dict[str, Lesion] = field(default_factory=dict)
    blinded_snapshot: dict[str, Any] | None = None
    next_lesion_number: int = 1
    comment: str = ""
    # The reader's question on the whole case, for the senior reviewer.
    question: str = ""

    @classmethod
    def new(cls, study_id: str, reader_id: str, case_id: str, *, started_at: str | None = None) -> "ReviewState":
        state = cls(
            study_id=_identifier(study_id, "study_id"),
            reader_id=_identifier(reader_id, "reader_id"),
            case_id=_identifier(case_id, "case_id"),
            started_at=started_at or utc_now(),
        )
        state.validate()
        return state

    @classmethod
    def from_draft(cls, value: Mapping[str, Any]) -> "ReviewState":
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValidationError("draft schema_version is unsupported")
        lesions = [Lesion.from_dict(item) for item in value.get("lesions", [])]
        if len(lesions) != len({item.lesion_id for item in lesions}):
            raise ValidationError("lesion IDs must be unique")
        if value.get("split_plane") is not None:
            raise ValidationError("the cervix boundary plane is no longer supported")
        state = cls(
            study_id=_identifier(value.get("study_id"), "study_id"),
            reader_id=_identifier(value.get("reader_id"), "reader_id"),
            case_id=_identifier(value.get("case_id"), "case_id"),
            started_at=_non_empty_text(value.get("started_at"), "started_at"),
            phase=_non_empty_text(value.get("phase"), "phase"),
            ai_revealed_at=value.get("ai_revealed_at"),
            completed_at=value.get("completed_at"),
            zero_myomas=bool(value.get("zero_myomas", value.get("zero_myomas_confirmed", False))),
            lesions={item.lesion_id: item for item in lesions},
            blinded_snapshot=deepcopy(value.get("blinded_snapshot")),
            next_lesion_number=value.get("next_lesion_number", 1),
            comment=value.get("comment", ""),
            question=value.get("question") or "",
        )
        # Legacy revealed drafts were locked against annotation edits, so their
        # saved annotations are the original blinded record.
        if state.blinded_snapshot is None and state.phase in {
            ReviewPhase.AI_REVEALED.value, ReviewPhase.COMPLETE.value
        }:
            state.blinded_snapshot = state._annotation_snapshot()
        state.validate()
        known_ids = list(state.lesions)
        if isinstance(state.blinded_snapshot, Mapping):
            known_ids.extend(Lesion.from_dict(item).lesion_id for item in state.blinded_snapshot.get("lesions", []))
        if isinstance(state.next_lesion_number, int) and not isinstance(state.next_lesion_number, bool):
            state.next_lesion_number = max(state.next_lesion_number, max((int(item[1:]) for item in known_ids), default=0) + 1)
        state.validate()
        return state

    @classmethod
    def reopen_from_annotation(cls, value: Mapping[str, Any]) -> "ReviewState":
        """Turn a completed annotation back into an editable post-reveal state.

        The blinded snapshot and reveal timestamp carry over unchanged, so the
        reopened case still records what the reader said before seeing AI.
        """

        if value.get("status") != "complete":
            raise ValidationError("only completed reviews can be reopened")
        payload = dict(value)
        payload["phase"] = ReviewPhase.AI_REVEALED.value
        payload["completed_at"] = None
        state = cls.from_draft(payload)
        if state.ai_revealed_at is None or state.blinded_snapshot is None:
            raise ValidationError("completed review has no AI reveal record")
        return state

    @property
    def active_lesions(self) -> list[Lesion]:
        return sorted(self.lesions.values(), key=lambda item: int(item.lesion_id[1:]))

    @property
    def lesion_count(self) -> int:
        return len(self.lesions)

    @property
    def can_reveal(self) -> bool:
        return (
            self.phase == ReviewPhase.BLIND.value
            and (
                (self.zero_myomas and not self.lesions)
                or bool(self.lesions and all(item.figo is not None for item in self.lesions.values()))
            )
        )

    def validate(self) -> None:
        _identifier(self.study_id, "study_id")
        _identifier(self.reader_id, "reader_id")
        _identifier(self.case_id, "case_id")
        _utc_timestamp(self.started_at, "started_at")
        if self.ai_revealed_at is not None:
            _utc_timestamp(self.ai_revealed_at, "ai_revealed_at")
        if self.completed_at is not None:
            _utc_timestamp(self.completed_at, "completed_at")
        if self.phase not in {item.value for item in ReviewPhase}:
            raise ValidationError("phase is not recognized")
        if self.phase == ReviewPhase.BLIND.value and (self.ai_revealed_at is not None or self.blinded_snapshot is not None):
            raise ValidationError("a revealed record cannot return to the blind phase")
        if not isinstance(self.comment, str):
            raise ValidationError("comment must be text")
        self.comment = self.comment.strip()
        if not isinstance(self.question, str):
            raise ValidationError("question must be text")
        self.question = self.question.strip()
        if isinstance(self.next_lesion_number, bool) or not isinstance(self.next_lesion_number, int) or self.next_lesion_number < 1:
            raise ValidationError("next_lesion_number must be a positive integer")
        if len(self.lesions) != len(set(self.lesions)):
            raise ValidationError("lesion IDs must be unique")
        for key, lesion in self.lesions.items():
            if key != lesion.lesion_id:
                raise ValidationError("lesion mapping key must match lesion_id")
        if self.zero_myomas and self.lesions:
            raise ValidationError("zero_myomas is valid only when the lesion list is empty")
        if self.blinded_snapshot is not None:
            if not isinstance(self.blinded_snapshot, Mapping):
                raise ValidationError("blinded_snapshot must be an object")
            original = [Lesion.from_dict(item) for item in self.blinded_snapshot.get("lesions", [])]
            if len(original) != len({item.lesion_id for item in original}):
                raise ValidationError("blinded snapshot lesion IDs must be unique")
            original_zero = self.blinded_snapshot.get("zero_myomas", False)
            if not isinstance(original_zero, bool) or not (
                (original_zero and not original) or (not original_zero and original and all(item.figo is not None for item in original))
            ):
                raise ValidationError("blinded snapshot requires FIGO-complete or zero-myoma annotations")
            if self.blinded_snapshot.get("split_plane") is not None:
                raise ValidationError("the cervix boundary plane is no longer supported")
        if self.phase == ReviewPhase.AI_REVEALED.value and not self.ai_revealed_at:
            raise ValidationError("ai_revealed phase requires ai_revealed_at")
        if self.phase in {ReviewPhase.COMPLETE.value, ReviewPhase.TERMINAL.value} and not self.completed_at:
            raise ValidationError("terminal phase requires completed_at")

    def _ensure_blind(self) -> None:
        if self.phase != ReviewPhase.BLIND.value:
            raise ValidationError("ROI and FIGO annotations are locked after reveal")

    def _ensure_editable(self) -> None:
        if self.phase not in {ReviewPhase.BLIND.value, ReviewPhase.AI_REVEALED.value}:
            raise ValidationError("completed annotations are read-only")
        if self.phase == ReviewPhase.AI_REVEALED.value and self.blinded_snapshot is None:
            self.blinded_snapshot = self._annotation_snapshot()

    def _annotation_snapshot(self) -> dict[str, Any]:
        return {
            "zero_myomas": self.zero_myomas,
            "lesions": [item.to_dict() for item in self.active_lesions],
        }

    def add_lesion(self, center_ras: Sequence[float], size_mm: Sequence[float]) -> str:
        self._ensure_editable()
        sequence = max(self.next_lesion_number, max((int(item.lesion_id[1:]) for item in self.lesions.values()), default=0) + 1)
        lesion_id = f"M{sequence:02d}"
        self.lesions[lesion_id] = Lesion(lesion_id, tuple(center_ras), tuple(size_mm))
        self.next_lesion_number = sequence + 1
        self.zero_myomas = False
        self.validate()
        return lesion_id

    def update_lesion(self, lesion_id: str, center_ras: Sequence[float], size_mm: Sequence[float]) -> None:
        self._ensure_editable()
        if lesion_id not in self.lesions:
            raise ValidationError(f"unknown lesion: {lesion_id}")
        current = self.lesions[lesion_id]
        self.lesions[lesion_id] = replace(current, center_ras=tuple(center_ras), size_mm=tuple(size_mm))
        self.validate()

    def delete_lesion(self, lesion_id: str) -> None:
        self._ensure_editable()
        if lesion_id not in self.lesions:
            raise ValidationError(f"unknown lesion: {lesion_id}")
        del self.lesions[lesion_id]
        self.validate()

    def set_figo(self, lesion_id: str, figo: Mapping[str, Any] | FIGOClassification) -> None:
        self._ensure_editable()
        if lesion_id not in self.lesions:
            raise ValidationError(f"unknown lesion: {lesion_id}")
        current = self.lesions[lesion_id]
        self.lesions[lesion_id] = replace(current, figo=FIGOClassification.from_dict(figo))
        self.validate()

    def set_differential_diagnosis(self, lesion_id: str, text: str) -> None:
        self._ensure_editable()
        if lesion_id not in self.lesions:
            raise ValidationError(f"unknown lesion: {lesion_id}")
        current = self.lesions[lesion_id]
        self.lesions[lesion_id] = replace(current, differential_diagnosis=text)
        self.validate()

    def set_lesion_comment(self, lesion_id: str, text: str) -> None:
        self._ensure_editable()
        if lesion_id not in self.lesions:
            raise ValidationError(f"unknown lesion: {lesion_id}")
        self.lesions[lesion_id] = replace(self.lesions[lesion_id], comment=text)
        self.validate()

    def set_comment(self, text: str) -> None:
        """The reader's free-text note on the whole case."""

        self._ensure_editable()
        if not isinstance(text, str):
            raise ValidationError("comment must be text")
        self.comment = text.strip()

    def set_question(self, text: str) -> None:
        """The reader's question on the whole case, for the senior reviewer."""

        self._ensure_editable()
        if not isinstance(text, str):
            raise ValidationError("question must be text")
        self.question = text.strip()

    def set_lesion_question(self, lesion_id: str, text: str | None) -> None:
        """Tag a myoma with a question (None removes the tag)."""

        self._ensure_editable()
        if lesion_id not in self.lesions:
            raise ValidationError(f"unknown lesion: {lesion_id}")
        self.lesions[lesion_id] = replace(self.lesions[lesion_id], question=text)
        self.validate()

    @property
    def has_questions(self) -> bool:
        """Whether the case or any of its myomas asks the senior reviewer something."""

        return bool(self.question) or any(item.question is not None for item in self.active_lesions)

    def confirm_zero_myomas(self, confirmed: bool) -> None:
        self._ensure_editable()
        if confirmed and self.lesions:
            raise ValidationError("zero_myomas can be confirmed only with no active lesions")
        self.zero_myomas = bool(confirmed)
        self.validate()

    def reveal(self, *, revealed_at: str | None = None) -> None:
        if not self.can_reveal:
            raise ValidationError("every lesion needs valid FIGO data or zero myomas must be confirmed")
        timestamp = _utc_timestamp(revealed_at or utc_now(), "ai_revealed_at")
        self.blinded_snapshot = self._annotation_snapshot()
        self.phase = ReviewPhase.AI_REVEALED.value
        self.ai_revealed_at = timestamp
        self.validate()

    def complete(self, *, completed_at: str | None = None) -> dict[str, Any]:
        if self.phase != ReviewPhase.AI_REVEALED.value:
            raise ValidationError("AI correction must be revealed before completion")
        # Newly added correction lesions may still need FIGO. Validate before
        # advancing the phase so a failed completion remains editable.
        annotation = self.to_annotation(MODULE_VERSION, status="complete")
        timestamp = _utc_timestamp(completed_at or annotation["completed_at"], "completed_at")
        self.phase = ReviewPhase.COMPLETE.value
        self.completed_at = timestamp
        annotation["completed_at"] = timestamp
        return annotation

    def early_stop(self, status: str, *, completed_at: str | None = None) -> dict[str, Any]:
        if status not in {"no_uterus", "wrong_image", "cannot_assess"}:
            raise ValidationError("early-stop status is not recognized")
        if self.phase != ReviewPhase.BLIND.value:
            raise ValidationError("early-stop outcomes are available only before AI reveal")
        self.phase = ReviewPhase.TERMINAL.value
        self.completed_at = completed_at or utc_now()
        return self.to_annotation(MODULE_VERSION, status=status)

    def to_draft(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": SCHEMA_VERSION,
            "study_id": self.study_id,
            "reader_id": self.reader_id,
            "case_id": self.case_id,
            "phase": self.phase,
            "started_at": self.started_at,
            "ai_revealed_at": self.ai_revealed_at,
            "completed_at": self.completed_at,
            "zero_myomas": self.zero_myomas,
            "lesions": [item.to_dict() for item in self.active_lesions],
            "blinded_snapshot": deepcopy(self.blinded_snapshot),
            "next_lesion_number": self.next_lesion_number,
            "comment": self.comment,
            "question": self.question,
        }

    def to_annotation(self, module_version: str, *, status: str) -> dict[str, Any]:
        self.validate()
        if status not in VALID_STATUSES:
            raise ValidationError("status is not recognized")
        if status == "complete":
            if self.phase not in {ReviewPhase.AI_REVEALED.value, ReviewPhase.COMPLETE.value}:
                raise ValidationError("complete annotation requires the AI-revealed correction phase")
            figo_complete = (self.zero_myomas and not self.lesions) or bool(
                self.lesions and all(item.figo is not None for item in self.lesions.values())
            )
            if not figo_complete:
                raise ValidationError("complete annotation requires FIGO-complete or zero-myoma state")
        if self.zero_myomas and self.lesions:
            raise ValidationError("zero_myomas is valid only when the lesion list is empty")
        completed_at = self.completed_at or utc_now()
        lesions = [item.to_dict(require_figo=(status == "complete")) for item in self.active_lesions]
        return {
            "schema_version": SCHEMA_VERSION,
            "module_version": _non_empty_text(module_version, "module_version"),
            "study_id": self.study_id,
            "reader_id": self.reader_id,
            "case_id": self.case_id,
            "status": status,
            "started_at": self.started_at,
            "ai_revealed_at": self.ai_revealed_at,
            "completed_at": completed_at,
            "zero_myomas": self.zero_myomas,
            "lesions": lesions,
            "blinded_snapshot": deepcopy(self.blinded_snapshot),
            "label_names": dict(LABEL_NAMES),
            "corrected_labelmap": "corrected_labels.nii.gz" if status == "complete" else None,
            "comment": self.comment,
            "question": self.question,
        }
