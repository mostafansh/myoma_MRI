"""ITK-SNAP style Alt + left-drag window/level adjustment, one pane at a time.

The arithmetic lives in :func:`compute_window_level`, a pure function with no
Slicer, Qt, or VTK imports, so the interaction can be tested without a running
application.  Every framework import inside :class:`AltDragWindowLevel` is
deferred into the method that needs it, matching the rest of the package.

Only display nodes are touched.  Neither image pixels nor label values change.
"""

from __future__ import annotations

import math
import traceback
from typing import Any, Callable, Mapping


# Drag direction convention.  Positive ``dy`` means the pointer moved UP (VTK
# reports event positions with y growing upward) and positive ``dx`` means it
# moved RIGHT.  The intended feel is:
#     drag UP    -> more contrast  (the window narrows, so the multiplier < 1)
#     drag RIGHT -> more brightness (the window centre drops, so mid-grey moves
#                                    onto darker voxels and the image lightens)
# Flip a sign here if the reader reports that an axis feels backwards; nothing
# else in the package encodes the convention.
WINDOW_DRAG_SIGN = -1.0
LEVEL_DRAG_SIGN = -1.0

# Per-pixel gains at sensitivity 5.  The window update is multiplicative and so
# is already independent of the intensity units; the level update is additive
# and is scaled by the scalar range so a 400 px horizontal drag shifts the
# centre by the full range on every plane alike.
WINDOW_GAIN_PER_PIXEL = 0.006
LEVEL_GAIN_PER_PIXEL = 0.0025

MIN_SENSITIVITY = 1
MAX_SENSITIVITY = 10
DEFAULT_SENSITIVITY = 5

# Guard rails that keep the result finite for absurd drags and degenerate
# inputs.  ``math.exp`` raises above roughly 709, so the exponent is clamped
# well below that before the window is clamped into range.
_MAX_EXPONENT = 64.0
_WINDOW_FLOOR_ABSOLUTE = 1.0e-6
_WINDOW_FLOOR_FRACTION = 1.0e-6
_VALUE_LIMIT = 1.0e12

_OBSERVER_PRIORITY = 2.0


def _finite(value: Any, fallback: float = 0.0) -> float:
    """Return ``value`` as a finite float, or ``fallback`` when it is not one."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return number if math.isfinite(number) else fallback


def _range_span(scalar_range: Any) -> float:
    """Width of a ``(low, high)`` pair; ``0.0`` for missing or unusable input."""
    try:
        low = float(scalar_range[0])
        high = float(scalar_range[1])
    except (TypeError, ValueError, OverflowError, IndexError, KeyError):
        return 0.0
    if not (math.isfinite(low) and math.isfinite(high)):
        return 0.0
    return abs(high - low)


def _sensitivity_gain(sensitivity: Any) -> float:
    """Clamp ``sensitivity`` into 1..10 and express it relative to the default."""
    try:
        value = int(round(float(sensitivity)))
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_SENSITIVITY
    value = max(MIN_SENSITIVITY, min(MAX_SENSITIVITY, value))
    return value / float(DEFAULT_SENSITIVITY)


def compute_window_level(
    window0: float,
    level0: float,
    scalar_range: Any,
    dx: float,
    dy: float,
    *,
    sensitivity: int = DEFAULT_SENSITIVITY,
    inverted: bool = False,
) -> tuple[float, float]:
    """Window and level for a drag of ``(dx, dy)`` pixels from ``(window0, level0)``.

    Both values are derived from the state captured when the drag started, never
    accumulated from the previous move, because accumulation drifts.  The window
    is updated multiplicatively so it stays strictly positive and remains
    responsive across orders of magnitude; the level is updated additively in
    units of the volume's scalar range.  The function is total: degenerate
    ranges, non-numeric inputs, and extreme deltas all yield finite values with
    a strictly positive window, and a zero drag returns the inputs unchanged.
    """
    span = _range_span(scalar_range)
    base_window = _finite(window0, 0.0)
    if base_window <= 0.0:
        base_window = span if span > 0.0 else 1.0
    if span <= 0.0:
        # No usable range: fall back to the current window so the level drag
        # still moves by a sensible amount instead of standing still.
        span = base_window
    base_level = _finite(level0, 0.0)

    gain = _sensitivity_gain(sensitivity) * (-1.0 if inverted else 1.0)
    move_x = _finite(dx, 0.0)
    move_y = _finite(dy, 0.0)

    exponent = WINDOW_DRAG_SIGN * gain * WINDOW_GAIN_PER_PIXEL * move_y
    exponent = max(-_MAX_EXPONENT, min(_MAX_EXPONENT, exponent))
    window = base_window * math.exp(exponent)
    level = base_level + LEVEL_DRAG_SIGN * gain * LEVEL_GAIN_PER_PIXEL * span * move_x

    floor = max(_WINDOW_FLOOR_ABSOLUTE, span * _WINDOW_FLOOR_FRACTION)
    if not math.isfinite(window):
        window = _VALUE_LIMIT if window > 0 else floor
    window = min(max(window, floor), _VALUE_LIMIT)
    if not math.isfinite(level):
        level = base_level
    level = min(max(level, -_VALUE_LIMIT), _VALUE_LIMIT)
    return float(window), float(level)


class AltDragWindowLevel:
    """Alt + left-drag window/level for the sagittal/axial/coronal panes, pane-independent.

    ``view_names`` maps a plane name to its slice view name and
    ``logic.volume_nodes`` maps the same plane names to scalar volume nodes;
    the drag adjusts that volume's display node only.  ``on_changed`` is called
    with the plane name once a drag finishes.

    Without Alt held the press is ignored entirely and Slicer handles the click
    as usual, so crosshair placement and Segment Editor painting are unaffected.
    With Alt held the drag additionally disarms the Segment Editor for its
    duration; see ``_suspend_effect``.
    """

    def __init__(
        self,
        logic: Any,
        view_names: Mapping[str, str],
        on_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.logic = logic
        self.view_names = dict(view_names)
        self.on_changed = on_changed
        # Set by the owning widget to expose the live qMRMLSegmentEditorWidget.
        self.editor_provider: Callable[[], Any] | None = None
        self._observers: list[tuple[Any, int]] = []
        self._drag: dict[str, Any] | None = None
        self._enabled = True
        self._inverted = False
        self._sensitivity = DEFAULT_SENSITIVITY

    @property
    def bound(self) -> bool:
        return bool(self._observers)

    @property
    def dragging(self) -> bool:
        return self._drag is not None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        if not self._enabled:
            # Leave the observers in place but never strand a suppressed pane.
            self._end_drag(notify=False)

    @property
    def inverted(self) -> bool:
        return self._inverted

    @inverted.setter
    def inverted(self, value: bool) -> None:
        self._inverted = bool(value)

    @property
    def sensitivity(self) -> int:
        return self._sensitivity

    @sensitivity.setter
    def sensitivity(self, value: int) -> None:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError, OverflowError):
            number = DEFAULT_SENSITIVITY
        self._sensitivity = max(MIN_SENSITIVITY, min(MAX_SENSITIVITY, number))

    def bind(self) -> None:
        """Attach press/move/release observers to every pane. Idempotent."""
        import vtk

        self.unbind()
        for plane in self.view_names:
            context = self._view_context(plane)
            if context is None:
                continue
            interactor = context["interactor"]
            for event, callback in (
                (vtk.vtkCommand.LeftButtonPressEvent, self._press),
                (vtk.vtkCommand.MouseMoveEvent, self._move),
                (vtk.vtkCommand.LeftButtonReleaseEvent, self._release),
            ):
                try:
                    tag = interactor.AddObserver(
                        event,
                        lambda caller, _event, name=plane, cb=callback: cb(caller, name),
                        _OBSERVER_PRIORITY,
                    )
                except Exception:
                    traceback.print_exc()
                    continue
                self._observers.append((interactor, tag))

    def unbind(self) -> None:
        """Remove every observer and restore any suppressed pane. Idempotent."""
        self._end_drag(notify=False)
        for interactor, tag in self._observers:
            try:
                interactor.RemoveObserver(tag)
            except Exception:
                pass
        self._observers = []

    def _press(self, caller: Any, plane: str) -> None:
        if self._drag is not None:
            # A release can go missing when a modal dialog opens mid-drag.
            # Recover the previous drag here instead of ignoring the press and
            # leaving the pane suppressed until the next release.
            self._end_drag(notify=False)
        if not self._enabled:
            return
        if not self._alt_pressed(caller):
            return  # Plain clicks belong to Slicer.
        context = self._view_context(plane)
        if context is None:
            return
        display = context["display"]
        try:
            window0 = float(display.GetWindow())
            level0 = float(display.GetLevel())
        except Exception:
            return
        position = self._event_position(caller)
        if position is None:
            return
        self._drag = {
            "plane": plane,
            "view": context["view"],
            "display": display,
            "x0": position[0],
            "y0": position[1],
            "window0": window0,
            "level0": level0,
            "scalar_range": self._scalar_range(context["volume"], window0, level0),
            "suppressed": self._suppress_interaction(context["style"]),
            "effect": self._suspend_effect(),
        }

    def _move(self, caller: Any, plane: str) -> None:
        drag = self._drag
        if drag is None or drag["plane"] != plane:
            return  # A drag owns its pane; other panes stay inert.
        if not self._enabled:
            self._end_drag(notify=False)
            return
        position = self._event_position(caller)
        if position is None:
            return
        window, level = compute_window_level(
            drag["window0"],
            drag["level0"],
            drag["scalar_range"],
            position[0] - drag["x0"],
            position[1] - drag["y0"],
            sensitivity=self._sensitivity,
            inverted=self._inverted,
        )
        self._apply(drag, window, level)

    def _release(self, caller: Any, plane: str) -> None:
        drag = self._drag
        if drag is None or drag["plane"] != plane:
            return
        self._move(caller, plane)
        self._end_drag(notify=True)

    def _apply(self, drag: dict[str, Any], window: float, level: float) -> None:
        display = drag["display"]
        try:
            previous = display.StartModify()
            try:
                display.SetAutoWindowLevel(False)
                display.SetWindowLevel(window, level)
            finally:
                display.EndModify(previous)
        except Exception:
            traceback.print_exc()
            self._end_drag(notify=False)
            return
        view = drag.get("view")
        if view is not None:
            try:
                view.scheduleRender()
            except Exception:
                pass

    def _end_drag(self, *, notify: bool) -> None:
        """Restore the pane's interaction state; safe to call with no drag."""
        drag = self._drag
        self._drag = None
        if drag is None:
            return
        self._restore_interaction(drag.get("suppressed"))
        self._resume_effect(drag.get("effect"))
        if notify and self.on_changed is not None:
            try:
                self.on_changed(drag["plane"])
            except Exception:
                traceback.print_exc()

    def _suspend_effect(self) -> str | None:
        """Disarm the Segment Editor for the duration of this drag.

        Suppressing the interactor style stops the slice view's own pan, zoom
        and crosshair actions, but Segment Editor effects observe the same
        interactor independently, at priority 1.0.  These observers run first
        at priority 2.0, so clearing the active effect here means the effect's
        handler finds nothing armed when the very same press reaches it.
        Without this, an Alt-drag to fix the contrast while Paint is active
        would also lay down a stroke.
        """
        provider = self.editor_provider
        if provider is None:
            return None
        try:
            editor = provider()
            if editor is None:
                return None
            name = getattr(editor, "activeEffectName", None)
            if name is None and hasattr(editor, "activeEffect"):
                effect = editor.activeEffect()
                name = getattr(effect, "name", None) if effect is not None else None
            if not name:
                return None
            editor.setActiveEffectByName("")
            return str(name)
        except Exception:
            traceback.print_exc()
            return None

    def _resume_effect(self, name: Any) -> None:
        """Re-arm the effect the drag disarmed. Never raises."""
        provider = self.editor_provider
        if not name or provider is None:
            return
        try:
            editor = provider()
            if editor is not None:
                editor.setActiveEffectByName(str(name))
        except Exception:
            traceback.print_exc()

    def _view_context(self, plane: str) -> dict[str, Any] | None:
        """Resolve the live slice view, interactor, style, volume and display node."""
        import slicer

        view_name = self.view_names.get(plane)
        volume = None
        try:
            volume = self.logic.volume_nodes.get(plane)
        except Exception:
            volume = None
        if view_name is None or volume is None:
            return None
        try:
            manager = slicer.app.layoutManager()
            if manager is None:
                return None
            slice_widget = manager.sliceWidget(view_name)
            if slice_widget is None:
                return None
            view = slice_widget.sliceView()
            interactor = view.interactor()
            if interactor is None:
                return None
            style = (
                view.interactorObserver()
                if hasattr(view, "interactorObserver")
                else interactor.GetInteractorStyle()
            )
            display = volume.GetDisplayNode()
        except Exception:
            return None
        if display is None:
            return None
        return {
            "view": view,
            "interactor": interactor,
            "style": style,
            "volume": volume,
            "display": display,
        }

    @staticmethod
    def _alt_pressed(interactor: Any) -> bool:
        getter = getattr(interactor, "GetAltKey", None)
        if getter is None:
            return False
        try:
            return bool(getter())
        except Exception:
            return False

    @staticmethod
    def _event_position(interactor: Any) -> tuple[int, int] | None:
        try:
            position = interactor.GetEventPosition()
            return int(position[0]), int(position[1])
        except Exception:
            return None

    @staticmethod
    def _scalar_range(volume: Any, window0: float, level0: float) -> tuple[float, float]:
        """Scalar range of the source image, falling back to the current window."""
        try:
            image = volume.GetImageData()
            if image is not None:
                low, high = image.GetScalarRange()
                if math.isfinite(low) and math.isfinite(high) and high > low:
                    return float(low), float(high)
        except Exception:
            pass
        half = abs(window0) / 2.0 if math.isfinite(window0) else 0.5
        centre = level0 if math.isfinite(level0) else 0.0
        return centre - half, centre + half

    def _suppress_interaction(self, style: Any) -> tuple[Any, str, Any] | None:
        """Disable this pane's actions, returning the state needed to restore them."""
        if style is None:
            return None
        try:
            if hasattr(style, "SetActionEnabled") and hasattr(style, "AllActionsMask"):
                mask = int(style.AllActionsMask)
                states = tuple(
                    (1 << bit, bool(style.GetActionEnabled(1 << bit)))
                    for bit in range(mask.bit_length())
                    if mask & (1 << bit)
                )
                scalar = (
                    style.GetScalarBarDisplayableManager()
                    if hasattr(style, "GetScalarBarDisplayableManager")
                    else None
                )
                scalar_states = None
                if scalar is not None and hasattr(scalar, "GetAdjustBackgroundWindowLevelEnabled"):
                    scalar_states = (
                        bool(scalar.GetAdjustBackgroundWindowLevelEnabled()),
                        bool(scalar.GetAdjustForegroundWindowLevelEnabled()),
                    )
                style.SetActionEnabled(mask, False)
                if scalar_states is not None:
                    scalar.SetAdjustBackgroundWindowLevelEnabled(False)
                    scalar.SetAdjustForegroundWindowLevelEnabled(False)
                return (style, "actions", (states, scalar, scalar_states))
            if hasattr(style, "SetEnabled"):
                prior = bool(style.GetEnabled()) if hasattr(style, "GetEnabled") else True
                style.SetEnabled(False)
                return (style, "enabled", prior)
        except Exception:
            traceback.print_exc()
        return None

    @staticmethod
    def _restore_interaction(saved: tuple[Any, str, Any] | None) -> None:
        if saved is None:
            return
        style, mode, prior = saved
        try:
            if mode == "actions":
                states, scalar, scalar_states = prior
                for action, enabled in states:
                    style.SetActionEnabled(action, enabled)
                if scalar_states is not None:
                    scalar.SetAdjustBackgroundWindowLevelEnabled(scalar_states[0])
                    scalar.SetAdjustForegroundWindowLevelEnabled(scalar_states[1])
            elif prior:
                style.SetEnabled(True)
        except Exception:
            traceback.print_exc()
