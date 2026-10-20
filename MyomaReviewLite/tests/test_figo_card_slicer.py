"""Slicer-runtime tests for the on-image FIGO card panel.

Run with: Slicer --no-splash --python-script tests/test_figo_card_slicer.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import qt
import slicer


MODULE_ROOT = Path(__file__).resolve().parents[1]
for module_name in tuple(sys.modules):
    if module_name == "MyomaReviewLite" or module_name.startswith("MyomaReviewLiteLib"):
        del sys.modules[module_name]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != MODULE_ROOT]
sys.path.insert(0, str(MODULE_ROOT))

from MyomaReviewLiteLib.figo_card import FigoCard  # noqa: E402


def _card(ddx="a", comment="", read_only=False):
    figo_calls, text_calls, closes = [], [], []
    card = FigoCard(
        on_figo=lambda lid, figo: figo_calls.append((lid, dict(figo))),
        on_text=lambda lid, d, c: text_calls.append((lid, d, c)),
        on_close=lambda: closes.append(1),
    )
    parent = qt.QWidget()
    parent.resize(400, 300)
    card.open(parent, "M01", None, ddx, comment, read_only)
    return card, parent, figo_calls, text_calls, closes


def _child(card, cls, name):
    found = card.widget.findChild(cls, name)
    assert found is not None, name
    return found


def _choose(combo, value):
    index = combo.findData(value)
    assert index >= 0, value
    combo.setCurrentIndex(index)


def test_a_type_button_saves_at_once():
    card, _p, figo, _t, _c = _card()
    button = _child(card, qt.QToolButton, "figo_type_3")
    button.click()
    assert figo == [("M01", {"kind": "standard", "type": 3})], figo
    assert button.checked


def test_type_8_waits_for_a_location():
    card, _p, figo, _t, _c = _card()
    _child(card, qt.QToolButton, "figo_type_8").click()
    assert figo == []
    combo = _child(card, qt.QComboBox, "figo_location")
    assert combo.isVisibleTo(card.widget)
    _choose(combo, "cervical")
    assert figo == [("M01", {"kind": "standard", "type": 8, "location": "cervical"})], figo


def test_hybrid_waits_for_both_parts():
    card, _p, figo, _t, _c = _card()
    _child(card, qt.QToolButton, "figo_hybrid").click()
    _choose(_child(card, qt.QComboBox, "figo_endometrial"), 2)
    assert figo == []
    serosal = _child(card, qt.QComboBox, "figo_serosal")
    _choose(serosal, 5)
    assert figo == [("M01", {"kind": "hybrid", "endometrial": 2, "serosal": 5})], figo
    _choose(serosal, 6)
    assert len(figo) == 2 and figo[1][1]["serosal"] == 6, figo


def test_uncertain_saves_after_a_reason():
    card, _p, figo, _t, _c = _card()
    _child(card, qt.QToolButton, "figo_uncertain").click()
    assert figo == []
    _choose(_child(card, qt.QComboBox, "figo_reason"), "both_interfaces_unclear")
    assert figo == [("M01", {"kind": "uncertain", "reason": "both_interfaces_unclear"})], figo


def test_text_is_flushed_on_close_only_when_changed():
    card, parent, _f, text, closes = _card(ddx="a")
    card.close()
    assert text == [] and len(closes) == 1, (text, closes)
    card.open(parent, "M01", None, "a", "", False)
    _child(card, qt.QLineEdit, "figo_ddx").text = "b"
    card.close()
    assert text == [("M01", "b", "")], text
    assert len(closes) == 2


def test_set_figo_updates_without_firing():
    card, _p, figo, _t, _c = _card()
    card.set_figo({"kind": "standard", "type": 5})
    assert _child(card, qt.QToolButton, "figo_type_5").checked
    assert figo == []
    card.set_figo(None)
    assert not _child(card, qt.QToolButton, "figo_type_5").checked


def test_read_only_disables_every_control():
    card, _p, _f, _t, _c = _card(read_only=True)
    for cls in (qt.QToolButton, qt.QComboBox, qt.QLineEdit):
        for w in card.widget.findChildren(cls):
            if w.objectName == "figo_close":
                assert w.enabled
            else:
                assert not w.enabled, w.objectName


def test_wheel_over_an_unfocused_list_does_not_save():
    card, parent, figo, _t, _c = _card()
    parent.show()
    _child(card, qt.QToolButton, "figo_hybrid").click()
    _choose(_child(card, qt.QComboBox, "figo_endometrial"), 2)
    serosal = _child(card, qt.QComboBox, "figo_serosal")
    _choose(serosal, 5)
    assert len(figo) == 1, figo
    serosal.clearFocus()
    assert not serosal.hasFocus()
    event = qt.QWheelEvent(
        qt.QPointF(5, 5), qt.QPointF(5, 5), qt.QPoint(0, 0), qt.QPoint(0, -120),
        0, qt.Qt.Vertical, qt.Qt.NoButton, qt.Qt.NoModifier,
    )
    qt.QApplication.sendEvent(serosal, event)
    assert len(figo) == 1, ("wheel changed a saved FIGO", figo)
    assert serosal.currentIndex == serosal.findData(5)


def test_close_hides_even_when_saving_text_fails():
    figo_calls, closes = [], []

    def boom(lid, d, c):
        raise RuntimeError("save failed")

    card = FigoCard(on_figo=lambda *a: figo_calls.append(a), on_text=boom, on_close=lambda: closes.append(1))
    parent = qt.QWidget()
    parent.resize(400, 300)
    card.open(parent, "M01", None, "a", "", False)
    _child(card, qt.QLineEdit, "figo_ddx").text = "b"
    raised = False
    try:
        card.close()
    except RuntimeError:
        raised = True
    assert raised, "the failure was swallowed"
    assert not card.is_open
    assert not card.widget.visible, "the card is stuck on screen"
    assert len(closes) == 1, closes


def test_reclicking_the_selected_kind_keeps_its_choices():
    card, _p, figo, _t, _c = _card()
    card.set_figo({"kind": "hybrid", "endometrial": 2, "serosal": 5})
    _child(card, qt.QToolButton, "figo_hybrid").click()
    endo = _child(card, qt.QComboBox, "figo_endometrial")
    sero = _child(card, qt.QComboBox, "figo_serosal")
    assert endo.itemData(endo.currentIndex) == 2 and sero.itemData(sero.currentIndex) == 5
    assert figo == [], figo


def test_extra_rows_grow_the_card_instead_of_squashing_the_buttons():
    resizes = []
    card = FigoCard(lambda *a: None, lambda *a: None, lambda: None, on_resize=lambda: resizes.append(1))
    parent = qt.QWidget()
    parent.resize(400, 400)
    parent.show()
    card.open(parent, "M01", None, "", "", False)
    slicer.app.processEvents()
    button = _child(card, qt.QToolButton, "figo_type_3")
    button_height = button.sizeHint.height()
    closed_height = card.widget.height
    _child(card, qt.QToolButton, "figo_hybrid").click()
    slicer.app.processEvents()
    assert card.widget.height > closed_height, "the card did not grow for the hybrid rows"
    assert button.height >= button_height, f"type buttons squashed to {button.height}px"
    assert resizes, "the owner was not told to re-place the grown card"
    _child(card, qt.QToolButton, "figo_type_3").click()
    slicer.app.processEvents()
    assert card.widget.height == closed_height, "the card did not shrink back"


TESTS = (
    test_extra_rows_grow_the_card_instead_of_squashing_the_buttons,
    test_wheel_over_an_unfocused_list_does_not_save,
    test_close_hides_even_when_saving_text_fails,
    test_reclicking_the_selected_kind_keeps_its_choices,
    test_a_type_button_saves_at_once,
    test_type_8_waits_for_a_location,
    test_hybrid_waits_for_both_parts,
    test_uncertain_saves_after_a_reason,
    test_text_is_flushed_on_close_only_when_changed,
    test_set_figo_updates_without_firing,
    test_read_only_disables_every_control,
)

failures = 0
for test in TESTS:
    try:
        test()
    except Exception:
        failures += 1
        sys.stderr.write(f"MyomaReviewLite FIGO card: FAIL {test.__name__}\n")
        traceback.print_exc()
    else:
        sys.stderr.write(f"MyomaReviewLite FIGO card: PASS {test.__name__}\n")
sys.stderr.flush()
slicer.app.exit(1 if failures else 0)
