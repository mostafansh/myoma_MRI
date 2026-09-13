"""An Alt-drag must disarm the Segment Editor and always re-arm it.

Segment Editor effects observe the same interactor as the drag, at a lower
priority, so a drag that left Paint armed would adjust the contrast and paint
a stroke at the same time.  Slicer is not needed here: once ``_view_context``
is stubbed, the press/move/release path imports nothing from it.
"""

from __future__ import annotations

import unittest

from MyomaReviewLiteLib.contrast_tools import AltDragWindowLevel


class FakeDisplay:
    def __init__(self) -> None:
        self.window, self.level, self.auto = 100.0, 50.0, True

    def GetWindow(self) -> float:
        return self.window

    def GetLevel(self) -> float:
        return self.level

    def StartModify(self) -> int:
        return 0

    def EndModify(self, previous: int) -> None:
        pass

    def SetAutoWindowLevel(self, value: bool) -> None:
        self.auto = bool(value)

    def SetWindowLevel(self, window: float, level: float) -> None:
        self.window, self.level = window, level


class FakeImage:
    def GetScalarRange(self):
        return (0.0, 1000.0)


class FakeVolume:
    def __init__(self) -> None:
        self.display = FakeDisplay()

    def GetImageData(self):
        return FakeImage()

    def GetDisplayNode(self):
        return self.display


class FakeStyle:
    AllActionsMask = 0b1111

    def __init__(self) -> None:
        self.actions = {1 << bit: True for bit in range(4)}

    def GetActionEnabled(self, action: int) -> bool:
        return self.actions[action]

    def SetActionEnabled(self, mask: int, enabled: bool) -> None:
        for action in list(self.actions):
            if mask & action:
                self.actions[action] = bool(enabled)


class FakeInteractor:
    def __init__(self) -> None:
        self.alt, self.position = False, (0, 0)

    def GetAltKey(self) -> bool:
        return self.alt

    def GetEventPosition(self):
        return self.position


class FakeEditor:
    def __init__(self, effect: str = "Paint") -> None:
        self.activeEffectName = effect
        self.history = [effect]

    def setActiveEffectByName(self, name: str) -> None:
        self.activeEffectName = name
        self.history.append(name)


class FakeLogic:
    def __init__(self, volume) -> None:
        self.volume_nodes = {"axial": volume}


def build(effect: str = "Paint"):
    volume = FakeVolume()
    tool = AltDragWindowLevel(FakeLogic(volume), {"axial": "MyomaLiteAxial"})
    editor = FakeEditor(effect)
    tool.editor_provider = lambda: editor
    interactor, style = FakeInteractor(), FakeStyle()
    context = {
        "view": object(),
        "interactor": interactor,
        "style": style,
        "volume": volume,
        "display": volume.display,
    }
    tool._view_context = lambda plane: context if plane == "axial" else None
    return tool, editor, interactor, style, volume


class EffectGuardTests(unittest.TestCase):
    def test_alt_drag_disarms_then_restores_the_active_effect(self) -> None:
        tool, editor, interactor, style, volume = build("Paint")
        interactor.alt = True
        interactor.position = (100, 100)
        tool._press(interactor, "axial")

        self.assertTrue(tool.dragging)
        self.assertEqual(editor.activeEffectName, "", "Paint stayed armed during the drag")
        self.assertFalse(any(style.actions.values()), "the pane's own actions stayed live")

        interactor.position = (160, 40)
        tool._move(interactor, "axial")
        self.assertNotEqual(volume.display.window, 100.0)
        self.assertFalse(volume.display.auto)

        tool._release(interactor, "axial")
        self.assertFalse(tool.dragging)
        self.assertEqual(editor.activeEffectName, "Paint", "Paint was not re-armed")
        self.assertTrue(all(style.actions.values()), "interactor actions were not restored")

    def test_press_without_alt_leaves_the_effect_alone(self) -> None:
        tool, editor, interactor, style, _volume = build("Paint")
        interactor.alt = False
        tool._press(interactor, "axial")

        self.assertFalse(tool.dragging)
        self.assertEqual(editor.activeEffectName, "Paint")
        self.assertEqual(editor.history, ["Paint"], "the editor was touched by a plain click")
        self.assertTrue(all(style.actions.values()))

    def test_no_active_effect_is_not_restored_as_an_empty_name(self) -> None:
        tool, editor, interactor, _style, _volume = build("")
        interactor.alt = True
        tool._press(interactor, "axial")
        tool._release(interactor, "axial")
        self.assertEqual(editor.history, [""], "an unarmed editor should never be written to")

    def test_a_lost_release_is_recovered_by_the_next_press(self) -> None:
        tool, editor, interactor, style, _volume = build("Erase")
        interactor.alt = True
        tool._press(interactor, "axial")
        self.assertEqual(editor.activeEffectName, "")

        # The release never arrives -- a modal dialog swallowed it.
        tool._press(interactor, "axial")
        self.assertTrue(tool.dragging, "the new drag should have started")
        self.assertEqual(editor.activeEffectName, "", "still mid-drag, so still disarmed")

        tool._release(interactor, "axial")
        self.assertEqual(editor.activeEffectName, "Erase")
        self.assertTrue(all(style.actions.values()))

    def test_unbind_during_a_drag_restores_everything(self) -> None:
        tool, editor, interactor, style, _volume = build("Paint")
        interactor.alt = True
        tool._press(interactor, "axial")
        tool.unbind()

        self.assertFalse(tool.dragging)
        self.assertEqual(editor.activeEffectName, "Paint")
        self.assertTrue(all(style.actions.values()))

    def test_events_from_another_pane_are_ignored(self) -> None:
        tool, _editor, interactor, _style, volume = build("Paint")
        interactor.alt = True
        interactor.position = (100, 100)
        tool._press(interactor, "axial")
        window = volume.display.window

        interactor.position = (400, 400)
        tool._move(interactor, "sagittal")
        self.assertEqual(volume.display.window, window, "another pane drove this pane's drag")
        tool._release(interactor, "sagittal")
        self.assertTrue(tool.dragging, "another pane ended this pane's drag")


if __name__ == "__main__":
    unittest.main()
