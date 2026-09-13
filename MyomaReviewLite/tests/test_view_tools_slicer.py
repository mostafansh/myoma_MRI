"""Run with Slicer --no-splash --python-script <this file>."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import qt
import slicer
import vtk


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from MyomaReviewLite import MyomaReviewLiteLogic  # noqa: E402
from MyomaReviewLiteLib.view_tools import ReviewViewTools, VIEW_NAMES  # noqa: E402


def test_true_planes_sources_and_contrast() -> None:
    logic = MyomaReviewLiteLogic()
    parent = qt.QWidget()
    controller = ReviewViewTools(logic, parent)
    assert not controller.widget.enabled
    arrays = {}
    for index, plane in enumerate(VIEW_NAMES):
        volume = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", plane)
        arrays[plane] = np.arange(8 * 9 * 10, dtype=np.float32).reshape(8, 9, 10) + 1000 * index
        slicer.util.updateVolumeFromArray(volume, arrays[plane])
        volume.CreateDefaultDisplayNodes()
        logic.volume_nodes[plane] = volume
    logic.activate_layout()
    controller.refresh()
    assert controller.widget.enabled

    # Restore an incorrectly selected source and a genuinely oblique slice.
    for plane, name in VIEW_NAMES.items():
        view = slicer.app.layoutManager().sliceWidget(name)
        view.sliceLogic().GetSliceCompositeNode().SetBackgroundVolumeID(logic.volume_nodes["sagittal"].GetID())
        oblique = vtk.vtkTransform()
        oblique.RotateWXYZ(25, 1, 1, 0)
        view.mrmlSliceNode().GetSliceToRAS().DeepCopy(oblique.GetMatrix())
        view.mrmlSliceNode().UpdateMatrices()
    controller.restore_views()
    for plane, name in VIEW_NAMES.items():
        view = slicer.app.layoutManager().sliceWidget(name)
        assert view.sliceLogic().GetSliceCompositeNode().GetBackgroundVolumeID() == logic.volume_nodes[plane].GetID()
        expected = vtk.vtkMatrix3x3()
        getattr(slicer.vtkMRMLSliceNode, f"Get{plane.title()}SliceToRASMatrix")(expected)
        actual = view.mrmlSliceNode().GetSliceToRAS()
        assert all(abs(expected.GetElement(r, c) - actual.GetElement(r, c)) < 1e-6 for r in range(3) for c in range(3))

    controller.set_contrast("all", automatic=False, window=500, level=250)
    controller.set_contrast("axial", automatic=False, window=300, level=750)
    for plane, volume in logic.volume_nodes.items():
        display = volume.GetDisplayNode()
        assert not display.GetAutoWindowLevel()
        assert display.GetWindow() == (300 if plane == "axial" else 500)
        assert display.GetLevel() == (750 if plane == "axial" else 250)
        assert np.array_equal(slicer.util.arrayFromVolume(volume), arrays[plane])
    controller.target_combo.currentIndex = controller.target_combo.findData("axial")
    assert controller.window_spin.value == 300
    controller.window_spin.value = 200
    assert logic.volume_nodes["axial"].GetDisplayNode().GetWindow() == 200
    assert logic.volume_nodes["sagittal"].GetDisplayNode().GetWindow() == 500

    # External native-display changes also update the numeric controls.
    logic.volume_nodes["axial"].GetDisplayNode().SetWindowLevel(444, 222)
    assert controller.window_spin.value == 444
    assert controller.level_spin.value == 222
    controller.set_contrast("all", automatic=True)
    assert all(node.GetDisplayNode().GetAutoWindowLevel() for node in logic.volume_nodes.values())
    assert controller.auto_check.checked
    assert not controller.window_spin.enabled
    try:
        controller.set_contrast("all", automatic=False, window=0, level=1)
    except ValueError:
        pass
    else:
        raise AssertionError("Nonpositive windows must be rejected")

    controller.cleanup()
    assert not controller._observations
    for volume in logic.volume_nodes.values():
        slicer.mrmlScene.RemoveNode(volume)
    logic.volume_nodes = {}
    controller.refresh()
    assert not controller.widget.enabled
    controller.cleanup()
    parent.deleteLater()


if __name__ == "__main__":
    try:
        test_true_planes_sources_and_contrast()
    except Exception:
        traceback.print_exc()
        slicer.app.exit(1)
    else:
        sys.stderr.write("MyomaReviewLite view tools: PASS\n")
        sys.stderr.flush()
        slicer.app.exit(0)
