"""Run with Slicer --no-splash --python-script <this file>.

Drives AltDragWindowLevel through a real interactor: a plain click is ignored,
an Alt+drag changes only the pane it started in, and every interactor action bit
the tool disabled is restored on release, on unbind, and on a mid-drag unbind.

Only display nodes are touched; no file and no dataset is opened.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import slicer
import vtk


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from MyomaReviewLite import MyomaReviewLiteLogic  # noqa: E402
from MyomaReviewLiteLib.contrast_tools import AltDragWindowLevel  # noqa: E402
from MyomaReviewLiteLib.view_tools import VIEW_NAMES  # noqa: E402


PLANE_NAMES = tuple(VIEW_NAMES)


def _make_volumes(logic: MyomaReviewLiteLogic) -> None:
    for index, plane in enumerate(PLANE_NAMES):
        volume = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", plane)
        array = np.arange(8 * 9 * 10, dtype=np.float32).reshape(8, 9, 10) * (index + 1)
        slicer.util.updateVolumeFromArray(volume, array)
        volume.CreateDefaultDisplayNodes()
        display = volume.GetDisplayNode()
        display.SetAutoWindowLevel(False)
        display.SetWindowLevel(200.0, 100.0)
        logic.volume_nodes[plane] = volume


def _view(plane: str):
    return slicer.app.layoutManager().sliceWidget(VIEW_NAMES[plane]).sliceView()


def _interactor(plane: str):
    return _view(plane).interactor()


def _style(plane: str):
    view = _view(plane)
    if hasattr(view, "interactorObserver"):
        return view.interactorObserver()
    return view.interactor().GetInteractorStyle()


def _action_bits(plane: str) -> dict[int, bool]:
    """Snapshot every action-enabled flag the tool is allowed to touch."""

    style = _style(plane)
    if not (hasattr(style, "SetActionEnabled") and hasattr(style, "AllActionsMask")):
        return {}
    mask = int(style.AllActionsMask)
    return {
        1 << bit: bool(style.GetActionEnabled(1 << bit))
        for bit in range(mask.bit_length())
        if mask & (1 << bit)
    }


def _levels(logic: MyomaReviewLiteLogic) -> dict[str, tuple[float, float]]:
    return {
        plane: (
            float(node.GetDisplayNode().GetWindow()),
            float(node.GetDisplayNode().GetLevel()),
        )
        for plane, node in logic.volume_nodes.items()
    }


def _press(plane: str, position: tuple[int, int], *, alt: bool) -> None:
    interactor = _interactor(plane)
    interactor.SetAltKey(1 if alt else 0)
    interactor.SetEventPosition(position[0], position[1])
    interactor.InvokeEvent(vtk.vtkCommand.LeftButtonPressEvent)


def _move(plane: str, position: tuple[int, int]) -> None:
    interactor = _interactor(plane)
    interactor.SetEventPosition(position[0], position[1])
    interactor.InvokeEvent(vtk.vtkCommand.MouseMoveEvent)


def _release(plane: str, position: tuple[int, int]) -> None:
    interactor = _interactor(plane)
    interactor.SetEventPosition(position[0], position[1])
    interactor.InvokeEvent(vtk.vtkCommand.LeftButtonReleaseEvent)
    interactor.SetAltKey(0)


def test_alt_drag_binds_isolates_and_restores() -> None:
    logic = MyomaReviewLiteLogic()
    _make_volumes(logic)
    logic.activate_layout()

    changed: list[str] = []
    tool = AltDragWindowLevel(logic, VIEW_NAMES, on_changed=changed.append)
    before = {plane: _action_bits(plane) for plane in PLANE_NAMES}

    assert not tool.bound
    tool.bind()
    assert tool.bound
    tool.bind()  # bind() is idempotent: it unbinds first.
    assert tool.bound
    for plane in PLANE_NAMES:
        assert _action_bits(plane) == before[plane], plane

    # A plain click belongs to Slicer.
    baseline = _levels(logic)
    _press("sagittal", (100, 100), alt=False)
    _move("sagittal", (140, 60))
    _release("sagittal", (140, 60))
    assert not tool.dragging
    assert _levels(logic) == baseline
    assert changed == []

    # Alt+drag suppresses this pane only, and adjusts this pane only.
    _press("sagittal", (100, 100), alt=True)
    assert tool.dragging
    assert all(not enabled for enabled in _action_bits("sagittal").values())
    for plane in ("axial", "coronal"):
        assert _action_bits(plane) == before[plane], plane
    _move("sagittal", (100, 160))
    dragged = _levels(logic)
    assert dragged["sagittal"][0] < baseline["sagittal"][0], dragged["sagittal"]  # up narrows the window
    _move("sagittal", (180, 160))
    dragged = _levels(logic)
    assert dragged["sagittal"][1] < baseline["sagittal"][1], dragged["sagittal"]  # right lowers the level
    for plane in ("axial", "coronal"):
        assert dragged[plane] == baseline[plane], plane

    # Another pane's events are ignored while a drag owns its pane.
    _move("axial", (400, 400))
    assert _levels(logic)["axial"] == baseline["axial"]

    _release("sagittal", (180, 160))
    assert not tool.dragging
    assert changed == ["sagittal"]
    for plane in PLANE_NAMES:
        assert _action_bits(plane) == before[plane], plane
    assert not logic.volume_nodes["sagittal"].GetDisplayNode().GetAutoWindowLevel()

    # A second pane drags independently of the first.
    first = _levels(logic)
    _press("coronal", (120, 120), alt=True)
    _move("coronal", (120, 40))
    _release("coronal", (120, 40))
    after = _levels(logic)
    assert after["coronal"][0] > first["coronal"][0], (first["coronal"], after["coronal"])  # down widens
    assert after["sagittal"] == first["sagittal"]
    assert changed == ["sagittal", "coronal"]

    # Unbinding mid-drag must not strand a pane with its actions disabled.
    _press("axial", (90, 90), alt=True)
    assert tool.dragging
    assert all(not enabled for enabled in _action_bits("axial").values())
    tool.unbind()
    assert not tool.bound
    assert not tool.dragging
    for plane in PLANE_NAMES:
        assert _action_bits(plane) == before[plane], plane

    # After unbinding the tool is inert.
    quiet = _levels(logic)
    _press("sagittal", (100, 100), alt=True)
    _move("sagittal", (100, 200))
    _release("sagittal", (100, 200))
    assert _levels(logic) == quiet
    tool.unbind()  # idempotent

    # Disabling an active drag restores the pane too.
    tool.bind()
    _press("axial", (110, 110), alt=True)
    assert tool.dragging
    tool.enabled = False
    assert not tool.dragging
    for plane in PLANE_NAMES:
        assert _action_bits(plane) == before[plane], plane
    tool.enabled = True

    # Sensitivity and inversion are clamped, not validated away.
    tool.sensitivity = 99
    assert tool.sensitivity == 10
    tool.sensitivity = 0
    assert tool.sensitivity == 1
    tool.sensitivity = "not a number"
    assert tool.sensitivity == 5
    tool.inverted = True
    assert tool.inverted is True

    tool.unbind()
    for volume in logic.volume_nodes.values():
        slicer.mrmlScene.RemoveNode(volume)
    logic.volume_nodes = {}


if __name__ == "__main__":
    try:
        test_alt_drag_binds_isolates_and_restores()
    except Exception:
        traceback.print_exc()
        slicer.app.exit(1)
    else:
        sys.stderr.write("MyomaReviewLite alt-drag: PASS\n")
        sys.stderr.flush()
        slicer.app.exit(0)
