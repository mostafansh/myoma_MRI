"""Slicer-independent pieces of the Lite corner-to-corner ROI workflow."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .domain import ValidationError


@dataclass(frozen=True)
class BoxGeometry:
    center_ras: tuple[float, float, float]
    size_mm: tuple[float, float, float]

    def __post_init__(self) -> None:
        if len(self.center_ras) != 3 or len(self.size_mm) != 3:
            raise ValidationError("box geometry requires three center and size values")
        center = tuple(float(value) for value in self.center_ras)
        size = tuple(float(value) for value in self.size_mm)
        if not all(math.isfinite(value) for value in center + size):
            raise ValidationError("box geometry values must be finite")
        if any(value <= 0 for value in size):
            raise ValidationError("box dimensions must be positive")
        object.__setattr__(self, "center_ras", center)
        object.__setattr__(self, "size_mm", size)

    def to_dict(self) -> dict[str, object]:
        return {"center_ras": list(self.center_ras), "size_mm": list(self.size_mm)}


def box_from_corners(
    first_ras: Sequence[float], second_ras: Sequence[float], *, minimum_size_mm: float = 0.5
) -> BoxGeometry:
    """Create the axis-aligned box seeded by two opposite drag corners."""

    if minimum_size_mm <= 0 or not math.isfinite(float(minimum_size_mm)):
        raise ValidationError("minimum_size_mm must be positive and finite")
    if len(first_ras) != 3 or len(second_ras) != 3:
        raise ValidationError("drag corners require three coordinates")
    first = tuple(float(value) for value in first_ras)
    second = tuple(float(value) for value in second_ras)
    if not all(math.isfinite(value) for value in first + second):
        raise ValidationError("drag corners must be finite")
    minimum = float(minimum_size_mm)
    size = tuple(max(abs(a - b), minimum) for a, b in zip(first, second))
    center = tuple((a + b) / 2.0 for a, b in zip(first, second))
    return BoxGeometry(center, size)


class RoiTransaction:
    """Save/cancel transaction used by both drag creation and handle editing."""

    def __init__(self) -> None:
        self.original: BoxGeometry | None = None
        self.current: BoxGeometry | None = None
        self.active = False

    def begin(self, geometry: BoxGeometry) -> None:
        if self.active:
            raise ValidationError("an ROI transaction is already active")
        self.original = geometry
        self.current = geometry
        self.active = True

    def preview(self, geometry: BoxGeometry) -> None:
        if not self.active:
            raise ValidationError("no ROI transaction is active")
        self.current = geometry

    def save(self) -> BoxGeometry:
        if not self.active or self.current is None:
            raise ValidationError("no ROI transaction is active")
        result = self.current
        self.original = None
        self.current = None
        self.active = False
        return result

    def cancel(self) -> BoxGeometry | None:
        result = self.original
        self.original = None
        self.current = None
        self.active = False
        return result


def stable_public_id(sequence: int) -> str:
    if sequence < 1:
        raise ValidationError("ROI sequence must be positive")
    return f"M{int(sequence):02d}"


def box_contains_point(
    geometry: BoxGeometry, point_ras: Sequence[float], *, tolerance_mm: float = 0.0
) -> bool:
    """Report whether an axis-aligned box covers a RAS point."""

    if len(point_ras) != 3:
        raise ValidationError("a RAS point requires three coordinates")
    point = tuple(float(value) for value in point_ras)
    if not all(math.isfinite(value) for value in point):
        raise ValidationError("a RAS point must be finite")
    tolerance = float(tolerance_mm)
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValidationError("tolerance_mm must be zero or positive and finite")
    return all(
        abs(coordinate - center) <= size / 2.0 + tolerance
        for coordinate, center, size in zip(point, geometry.center_ras, geometry.size_mm)
    )


def pick_box_at_point(
    point_ras: Sequence[float],
    candidates: Sequence[tuple[str, BoxGeometry]],
    *,
    tolerance_mm: float = 0.0,
) -> str | None:
    """Return the identifier of the tightest box covering a clicked RAS point.

    Overlapping and nested myoma boxes are common, so the smallest covering box
    wins and ties break on the identifier to keep repeated clicks deterministic.
    """

    hits = [
        (geometry.size_mm[0] * geometry.size_mm[1] * geometry.size_mm[2], str(identifier))
        for identifier, geometry in candidates
        if box_contains_point(geometry, point_ras, tolerance_mm=tolerance_mm)
    ]
    if not hits:
        return None
    return min(hits)[1]
