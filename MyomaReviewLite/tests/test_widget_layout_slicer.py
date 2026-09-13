"""Slicer-runtime regression test for the MyomaReviewLite module panel."""

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

import MyomaReviewLite as bundled_module  # noqa: E402
from MyomaReviewLite import MyomaReviewLiteWidget  # noqa: E402

assert Path(bundled_module.__file__).resolve().parent == MODULE_ROOT


def test_widget_uses_slicer_supplied_layout() -> None:
    parent = slicer.qMRMLWidget()
    parent.setLayout(qt.QVBoxLayout())
    parent.setMRMLScene(slicer.mrmlScene)
    slicer_layout = parent.layout()

    widget = MyomaReviewLiteWidget(parent)
    widget._build_ui()

    assert parent.layout() is slicer_layout
    assert slicer_layout.indexOf(widget.tab_widget) >= 0
    assert widget.tab_widget.indexOf(widget.cases_tab) >= 0
    assert widget.tab_widget.indexOf(widget.annotation_tab) >= 0
    assert widget.annotation_tab.layout().indexOf(widget.page_stack) >= 0
    assert widget.manifest_button.text == "Open case manifest"
    assert widget.case_list.count == 0

    # Opening a case must land the reviewer on the Annotation tab, not the
    # case browser they picked it from.
    widget.tab_widget.setCurrentWidget(widget.cases_tab)
    widget._focus_annotation_tab()
    assert widget.tab_widget.currentWidget() is widget.annotation_tab


try:
    test_widget_uses_slicer_supplied_layout()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    sys.stderr.write("MyomaReviewLite widget layout: PASS\n")
    sys.stderr.flush()
    slicer.app.exit(0)
