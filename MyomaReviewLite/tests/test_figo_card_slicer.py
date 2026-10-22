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


def _click(card, name):
    _child(card, qt.QAbstractButton, name).click()


def _shown(card, name):
    return _child(card, qt.QWidget, name).isVisibleTo(card.widget)


def test_a_type_button_saves_at_once():
    card, _p, figo, _t, _c = _card()
    button = _child(card, qt.QToolButton, "figo_type_3")
    button.click()
    assert figo == [("M01", {"kind": "standard", "type": 3})], figo
    assert button.checked
    assert _child(card, qt.QGroupBox, "figo_type_group").title == "FIGO type"


def test_type_8_waits_for_a_location():
    card, _p, figo, _t, _c = _card()
    assert not _shown(card, "figo_location_group")
    _click(card, "figo_type_8")
    assert figo == []
    assert _shown(card, "figo_location_group")
    _click(card, "figo_location_cervical")
    assert figo == [("M01", {"kind": "standard", "type": 8, "location": "cervical"})], figo


def test_hybrid_waits_for_both_sides_and_collapses_the_type_row():
    card, _p, figo, _t, _c = _card()
    _click(card, "figo_hybrid")
    assert not _shown(card, "figo_type_group"), "the type row stayed open under Hybrid"
    bar = _child(card, qt.QToolButton, "figo_type_collapsed")
    assert bar.isVisibleTo(card.widget)
    assert bar.text == "FIGO type 0–8 · hidden while Hybrid is picked"
    assert _child(card, qt.QGroupBox, "figo_endo_group").title == "Endometrial side"
    assert _child(card, qt.QGroupBox, "figo_sero_group").title == "Serosal side"
    _click(card, "figo_endo_1")
    assert figo == []
    _click(card, "figo_sero_6")
    assert figo == [("M01", {"kind": "hybrid", "endometrial": 1, "serosal": 6})], figo
    _click(card, "figo_sero_5")
    assert len(figo) == 2 and figo[1][1]["serosal"] == 5, figo
    bar.click()
    assert _shown(card, "figo_type_group") and not bar.isVisibleTo(card.widget)
    assert _child(card, qt.QToolButton, "figo_hybrid").checked, "expanding changed the kind"


def test_uncertain_saves_after_a_reason():
    card, _p, figo, _t, _c = _card()
    _click(card, "figo_uncertain")
    assert figo == []
    assert _child(card, qt.QGroupBox, "figo_reason_group").title == "Reason"
    _click(card, "figo_reason_both_interfaces_unclear")
    assert figo == [("M01", {"kind": "uncertain", "reason": "both_interfaces_unclear"})], figo


def test_no_dropdowns_remain():
    card, _p, _f, _t, _c = _card()
    assert len(card.widget.findChildren(qt.QComboBox)) == 0


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
    card.set_figo({"kind": "hybrid", "endometrial": 2, "serosal": 5})
    for name in ("figo_hybrid", "figo_endo_2", "figo_sero_5"):
        assert _child(card, qt.QToolButton, name).checked, name
    assert not _shown(card, "figo_type_group"), "a loaded Hybrid left the type row open"
    assert figo == []


def test_read_only_disables_every_control():
    card, _p, _f, _t, _c = _card(read_only=True)
    for cls in (qt.QAbstractButton, qt.QLineEdit):
        for w in card.widget.findChildren(cls):
            if w.objectName in ("figo_close", "figo_type_collapsed"):
                assert w.enabled
            else:
                assert not w.enabled, w.objectName


def test_read_only_card_can_expand_but_not_change():
    figo_calls = []
    card = FigoCard(lambda lid, f: figo_calls.append(f), lambda *a: None, lambda: None)
    parent = qt.QWidget()
    parent.resize(400, 300)
    card.open(parent, "M01", {"kind": "hybrid", "endometrial": 2, "serosal": 5}, "", "", True)
    bar = _child(card, qt.QToolButton, "figo_type_collapsed")
    assert bar.enabled and bar.isVisibleTo(card.widget)
    bar.click()
    assert _shown(card, "figo_type_group")
    for prefix in ("figo_type_", "figo_endo_", "figo_sero_", "figo_reason_", "figo_location_"):
        for button in card.widget.findChildren(qt.QAbstractButton):
            if button.objectName.startswith(prefix) and button.objectName != "figo_type_collapsed":
                assert not button.enabled, button.objectName
                button.click()
    assert figo_calls == [], figo_calls


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
    assert _child(card, qt.QToolButton, "figo_endo_2").checked
    assert _child(card, qt.QToolButton, "figo_sero_5").checked
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
    side = _child(card, qt.QToolButton, "figo_endo_3")
    assert side.height >= button_height, f"side buttons squashed to {side.height}px"
    assert resizes, "the owner was not told to re-place the grown card"
    _child(card, qt.QToolButton, "figo_type_collapsed").click()
    slicer.app.processEvents()
    assert button.height >= button_height, f"type buttons squashed to {button.height}px"
    _child(card, qt.QToolButton, "figo_type_3").click()
    slicer.app.processEvents()
    assert card.widget.height == closed_height, "the card did not shrink back"


def test_question_tag_saves_on_toggle_and_text_on_close() -> None:
    questions = []
    card = FigoCard(lambda *a: None, lambda *a: None, lambda: None, on_question=lambda lid, q: questions.append((lid, q)))
    parent = qt.QWidget()
    parent.resize(400, 300)
    card.open(parent, "M01", None, "", "", False)
    toggle = _child(card, qt.QToolButton, "figo_question")
    text = _child(card, qt.QLineEdit, "figo_question_text")
    assert not toggle.checked and not text.isVisibleTo(card.widget)
    toggle.click()
    assert questions == [("M01", "")], questions
    assert text.isVisibleTo(card.widget)
    text.text = "  Type 2 or 3?  "
    card.close()
    assert questions[-1] == ("M01", "Type 2 or 3?"), questions

    # Reopened with its question, the tag is on; untagging removes it at once.
    questions.clear()
    card.open(parent, "M01", None, "", "", False, question="Type 2 or 3?")
    assert toggle.checked and text.text == "Type 2 or 3?"
    assert questions == [], "opening the card saved something"
    toggle.click()
    assert questions == [("M01", None)], questions
    card.close()
    assert questions == [("M01", None)], "closing saved the question again"


TESTS = (
    test_extra_rows_grow_the_card_instead_of_squashing_the_buttons,
    test_no_dropdowns_remain,
    test_read_only_card_can_expand_but_not_change,
    test_close_hides_even_when_saving_text_fails,
    test_reclicking_the_selected_kind_keeps_its_choices,
    test_a_type_button_saves_at_once,
    test_type_8_waits_for_a_location,
    test_hybrid_waits_for_both_sides_and_collapses_the_type_row,
    test_uncertain_saves_after_a_reason,
    test_text_is_flushed_on_close_only_when_changed,
    test_set_figo_updates_without_firing,
    test_read_only_disables_every_control,
    test_question_tag_saves_on_toggle_and_text_on_close,
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
