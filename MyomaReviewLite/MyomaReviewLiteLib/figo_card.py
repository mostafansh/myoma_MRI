"""Slicer-independent helpers for the on-image FIGO card."""

from __future__ import annotations

from typing import Any, Mapping

from .domain import ReviewState


def figo_label(figo: Mapping[str, Any] | None) -> str:
    """Short card/summary label for a saved FIGO classification dict."""
    if figo is None:
        return "FIGO ?"
    kind = figo.get("kind")
    if kind == "hybrid":
        return f"Hybrid {figo.get('endometrial')}-{figo.get('serosal')}"
    if kind == "uncertain":
        return "Uncertain"
    label = f"FIGO {figo.get('type')}"
    location = figo.get("location")
    if location:
        label += f" ({str(location).replace('_', ' ')})"
    return label


def missing_figo_ids(state: ReviewState) -> list[str]:
    """IDs of active lesions that still have no FIGO classification, in list order."""
    return [lesion.lesion_id for lesion in state.active_lesions if lesion.figo is None]


def card_position(
    box_rect: tuple[float, float, float, float],
    card_size: tuple[int, int],
    view_size: tuple[int, int],
    device_pixel_ratio: float = 1.0,
    gap: int = 8,
) -> tuple[int, int]:
    """Top-left of the card in Qt widget pixels, beside the box and inside the view.

    box_rect is (x_min, y_min, x_max, y_max) in VTK slice-view XY (origin bottom-left,
    physical pixels); card_size and view_size are logical Qt pixels.
    """
    x_min, _y_min, x_max, y_max = (v / device_pixel_ratio for v in box_rect)
    card_w, card_h = card_size
    view_w, view_h = view_size
    x = x_max + gap
    if x + card_w > view_w:
        x = x_min - gap - card_w
    top = view_h - y_max
    x = min(max(x, 0), max(view_w - card_w, 0))
    y = min(max(top, 0), max(view_h - card_h, 0))
    return int(round(x)), int(round(y))
