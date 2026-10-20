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


TESTS = (
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
