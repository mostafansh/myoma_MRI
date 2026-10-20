"""Single-case, clean-room MyomaReviewLite scripted module for Slicer 5.10.

This entry point intentionally does not import ``MyomaReview`` or
``MyomaReviewLib``.  All durable workflow semantics live in the sibling
framework-independent package; this file only orchestrates Slicer, Qt, and
the native Segment Editor.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

_MODULE_DIRECTORY = Path(__file__).resolve().parent
if str(_MODULE_DIRECTORY) not in sys.path:
    sys.path.append(str(_MODULE_DIRECTORY))

try:
    import ctk  # type: ignore
    import qt  # type: ignore
    import slicer  # type: ignore
    import vtk  # type: ignore
    from slicer.ScriptedLoadableModule import (  # type: ignore
        ScriptedLoadableModule,
        ScriptedLoadableModuleLogic,
        ScriptedLoadableModuleTest,
        ScriptedLoadableModuleWidget,
    )

    SLICER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover - Slicer-only path
    SLICER_AVAILABLE = False

    class ScriptedLoadableModule:  # type: ignore[no-redef]
        def __init__(self, parent: Any = None) -> None:
            self.parent = parent

    class ScriptedLoadableModuleLogic:  # type: ignore[no-redef]
        pass

    class ScriptedLoadableModuleWidget:  # type: ignore[no-redef]
        def __init__(self, parent: Any = None) -> None:
            self.parent = parent

        def setup(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

    class ScriptedLoadableModuleTest:  # type: ignore[no-redef]
        pass

from MyomaReviewLiteLib import (  # noqa: E402
    LABEL_NAMES,
    MODULE_VERSION,
    SCHEMA_VERSION,
    CaseManifest,
    CaseStorage,
    DatasetManifest,
    FIGOClassification,
    FIGOKind,
    ImageGeometry,
    QueueStatus,
    QueueSummary,
    ReviewSaveCoordinator,
    ReviewPhase,
    ReviewState,
    StorageError,
    TransitionBlocked,
    ValidationError,
    box_from_corners,
    load_manifest,
    validate_ai_mask_values,
    validate_common_geometry,
)
from MyomaReviewLiteLib.roi import (  # noqa: E402
    BoxGeometry,
    RoiTransaction,
    box_contains_point,
    pick_box_at_point,
    stable_public_id,
)
from MyomaReviewLiteLib.domain import CORRECTION_LABEL_NAMES, validate_correction_mask_values
from MyomaReviewLiteLib.view_tools import ReviewViewTools
from MyomaReviewLiteLib.correction_tools import ReviewCorrectionTools


VIEW_NAMES = {
    "sagittal": "MyomaLiteSagittal",
    "axial": "MyomaLiteAxial",
    "coronal": "MyomaLiteCoronal",
}
VIEW_ORIENTATIONS = {"sagittal": "Sagittal", "axial": "Axial", "coronal": "Coronal"}
SAFE_VOLUME_NAMES = {key: f"T2 {key.title()}" for key in VIEW_NAMES}
CUSTOM_LAYOUT_ID = 735
FIXED_SEGMENT_IDS = {
    1: "uterine_wall",
    2: "uterine_cavity",
    3: "myoma",
    4: "nabothian_cyst",
    5: "cervix",
    6: "cesarean_scar_niche",
}
FIXED_SEGMENT_NAMES = {segment_id: CORRECTION_LABEL_NAMES[value] for value, segment_id in FIXED_SEGMENT_IDS.items()}

# A markups ROI draws its slice outline at a hardcoded 3 px that no display-node
# property feeds, so the visible box is a model node instead: its slice
# intersection honours SliceIntersectionThickness, whose integer floor of 1 px is
# the thinnest line a Slicer 2-D view can draw.
ROI_OUTLINE_THICKNESS_PX = 1
# Kept equal to ROI_OUTLINE_THICKNESS_PX by request: every box, including the
# one currently being drawn or edited, draws at the thinnest possible line.
ROI_ACTIVE_OUTLINE_THICKNESS_PX = 1
ROI_OUTLINE_COLOR = (1.0, 0.85, 0.15)
# Interaction handles stay hidden until a box is clicked, and are drawn smaller
# than the 3.0 % default so they cover less of the T2 underneath.
ROI_HANDLE_SCALE_PERCENT = 1.5
ROI_PICK_TOLERANCE_MM = 1.0
# A revealed box keeps its selection when a handle just outside it is clicked.
ROI_ACTIVE_PICK_TOLERANCE_MM = 6.0

# Case-browser filter/search, ported from the sibling BratsReviewLite
# pilot's "Subjects" tab pattern so both pilots pick a case the same way.
CASE_FILTERS = (
    ("All", "all"),
    ("Unfinished", "unfinished"),
    ("Finished", "finished"),
    ("Attention", "attention"),
)
DEFAULT_CASE_FILTER = "all"

# The reviewer-configurable keyboard shortcut panel. Each row is
# (action id, label shown in the shortcut dialog, factory-default key
# sequence, the widget method the shortcut activates, the attribute name of
# a toolbar button to keep labelled with its current binding, and that
# button's base text without the binding). Bindings are stored per-user in
# QSettings under "MyomaReviewLite/Shortcuts/<action id>" so the defaults
# below only apply the first time a reviewer opens the module.
SHORTCUT_ACTIONS: tuple[tuple[str, str, str, str, str | None, str | None], ...] = (
    ("undo", "Undo", "Ctrl+Z", "on_undo", "undo_button", "Undo"),
    ("redo", "Redo", "Ctrl+Y", "on_redo", "redo_button", "Redo"),
    ("redo_alt", "Redo (alternate)", "Ctrl+Shift+Z", "on_redo", None, None),
    ("add_box", "Add myoma box", "", "on_add_box", "add_button", "Add myoma box"),
    ("redraw_box", "Redraw box", "", "on_redraw_box", "redraw_button", "Redraw"),
    ("delete_box", "Delete box", "", "on_delete_box", "delete_button", "Delete"),
    ("continue_to_figo", "Continue to FIGO typing", "", "on_continue_to_figo",
     "continue_to_figo_button", "Done boxing → continue to FIGO typing"),
    ("save_draft", "Save draft", "", "on_save_draft", "save_draft_button", "Save Draft"),
)
SHORTCUT_SETTINGS_PREFIX = "MyomaReviewLite/Shortcuts/"
# Mouse-button bindings, layered on the same action list as
# SHORTCUT_ACTIONS above. Values are raw Qt::MouseButton bit values (not
# looked up via `qt.Qt.*`, so this module keeps importing cleanly outside
# Slicer too). On Windows, Qt only distinguishes two "extra" buttons
# (the back/forward side buttons); anything past that is almost always
# remapped to a keystroke by the mouse's own driver software rather than
# reported as a real mouse button, so it already works as a keyboard
# shortcut above instead. "(none)" (0) is not a real button and marks an
# action as unbound.
MOUSE_BUTTON_LABELS: dict[int, str] = {
    0x0004: "Middle click",
    0x0008: "Side button 1 (Back)",
    0x0010: "Side button 2 (Forward)",
    0x0040: "Extra button 3",
    0x0080: "Extra button 4",
    0x0100: "Extra button 5",
    0x0200: "Extra button 6",
}
# Left/Right clicks a reviewer must always keep for normal editing.
MOUSE_BUTTON_RESERVED = (0x0001, 0x0002)
MOUSE_BUTTON_SETTINGS_PREFIX = "MyomaReviewLite/MouseShortcuts/"


def _mouse_button_label(value: int) -> str:
    if not value:
        return "(none)"
    return MOUSE_BUTTON_LABELS.get(value, f"Button (0x{value:X})")
CASE_STATUS_TITLES = {
    QueueStatus.NEW: "New",
    QueueStatus.IN_PROGRESS: "In progress",
    QueueStatus.COMPLETE: "Finished",
    QueueStatus.ATTENTION: "Attention",
}
CASE_STATUS_COLORS = {
    QueueStatus.NEW: "#546e7a",
    QueueStatus.IN_PROGRESS: "#ef6c00",
    QueueStatus.COMPLETE: "#2e7d32",
    QueueStatus.ATTENTION: "#c62828",
}
CASE_FILTER_STATUSES = {
    "all": frozenset(QueueStatus),
    "unfinished": frozenset({QueueStatus.NEW, QueueStatus.IN_PROGRESS}),
    "finished": frozenset({QueueStatus.COMPLETE}),
    "attention": frozenset({QueueStatus.ATTENTION}),
}


def validate_fixed_segment_ids(segment_ids: Iterable[str]) -> dict[str, list[str]]:
    """Require the six named correction segments.

    The returned diagnostic is useful to a Slicer UI error message and keeps
    missing/added segments actionable instead of silently normalizing them.
    """

    actual = {str(value) for value in segment_ids}
    expected = set(FIXED_SEGMENT_IDS.values())
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise ValidationError(
            "fixed Segment Editor set mismatch; "
            f"missing={', '.join(missing) or 'none'}; "
            f"unexpected={', '.join(unexpected) or 'none'}"
        )
    return {"missing": missing, "unexpected": unexpected}


def validate_fixed_segments(segments: dict[str, str]) -> None:
    """Reject added, deleted, or renamed segments."""

    validate_fixed_segment_ids(segments.keys())
    renamed = sorted(
        segment_id
        for segment_id, expected_name in FIXED_SEGMENT_NAMES.items()
        if str(segments[segment_id]).strip().lower() != expected_name.lower()
    )
    if renamed:
        raise ValidationError("fixed Segment Editor segments were renamed: " + ", ".join(renamed))


def validate_four_label_values(values: Iterable[Any]) -> None:
    validate_ai_mask_values(values)


class MyomaReviewLite(ScriptedLoadableModule):
    def __init__(self, parent: Any) -> None:
        super().__init__(parent)
        if parent is None:
            return
        parent.title = "MyomaReviewLite"
        parent.categories = ["Myoma Research"]
        parent.dependencies = ["Segmentations", "SegmentEditor", "Markups"]
        parent.contributors = ["Myoma Research Team"]
        parent.helpText = (
            "Single-case blinded myoma boxes and FIGO classification followed "
            "by native Segment Editor correction and reviewed annotations."
        )
        parent.acknowledgementText = "Built for controlled pelvic T2 MRI research review."
        if SLICER_AVAILABLE:
            slicer.selfTests["MyomaReviewLite"] = self.runTest

    def runTest(self) -> None:
        MyomaReviewLiteTest().runTest()


class MyomaReviewLiteLogic(ScriptedLoadableModuleLogic):
    """Slicer-facing case lifecycle for a generic artifact-derived queue."""

    def __init__(self) -> None:
        super().__init__()
        self.manifest: CaseManifest | None = None
        self.dataset_manifest: DatasetManifest | None = None
        self.queue_summary: QueueSummary | None = None
        self.manifest_source: Path | None = None
        self.storage: CaseStorage | None = None
        self.state: ReviewState | None = None
        self.final_annotation: dict[str, Any] | None = None
        self.volume_nodes: dict[str, Any] = {}
        self.ai_mask_node: Any = None
        self.segmentation_node: Any = None
        self.segment_editor_node: Any = None
        self.segment_ids: dict[int, str] = dict(FIXED_SEGMENT_IDS)
        self.read_only = False

    @property
    def case_id(self) -> str:
        if self.manifest is None:
            return ""
        return self.manifest.case_id

    @property
    def phase(self) -> str | None:
        return self.state.phase if self.state is not None else None

    @property
    def available_case_ids(self) -> tuple[str, ...]:
        if self.queue_summary is not None:
            return tuple(entry.case_id for entry in self.queue_summary.entries)
        if self.dataset_manifest is not None:
            return self.dataset_manifest.case_ids
        if self.manifest is not None:
            return (self.manifest.case_id,)
        return ()

    def open_manifest(self, manifest_path: str | Path, case_id: str | None = None) -> CaseManifest:
        source_path = Path(manifest_path).expanduser().resolve()
        data_root = os.environ.get("MYOMA_REVIEW_LITE_DATA_ROOT", "").strip()
        loaded = load_manifest(source_path, source_root=data_root or None)
        dataset_manifest = loaded if isinstance(loaded, DatasetManifest) else None
        if dataset_manifest is not None:
            queue_summary = QueueSummary.scan(dataset_manifest)
            if case_id is None:
                first_unfinished = queue_summary.first_unfinished()
                if first_unfinished is not None:
                    case_id = first_unfinished.case_id
                else:
                    first_complete = next(
                        (
                            entry
                            for entry in queue_summary.entries
                            if entry.status is QueueStatus.COMPLETE
                        ),
                        None,
                    )
                    if first_complete is None:
                        attention = queue_summary.counts[QueueStatus.ATTENTION]
                        self.dataset_manifest = dataset_manifest
                        self.queue_summary = queue_summary
                        self.manifest_source = source_path
                        raise StorageError(
                            f"Queue has no reviewable cases; {attention} case(s) require Attention"
                        )
                    case_id = first_complete.case_id
            try:
                selected_entry = queue_summary.entry(case_id)
            except KeyError as exc:
                raise StorageError(f"case_id {case_id} is not present in the selected manifest") from exc
            if selected_entry.status is QueueStatus.ATTENTION:
                self.dataset_manifest = dataset_manifest
                self.queue_summary = queue_summary
                self.manifest_source = source_path
                raise StorageError(
                    f"Case {case_id} requires Attention: {selected_entry.diagnostic or 'invalid artifacts'}"
                )
            manifest = dataset_manifest.case_by_id(case_id)
        else:
            queue_summary = None
            if case_id is not None and case_id != loaded.case_id:
                raise StorageError(f"case_id {case_id} is not present in the selected manifest")
            manifest = loaded
        storage = CaseStorage(manifest.output_root, manifest.case_id)
        final = storage.load_annotation()
        draft = None if final is not None else storage.load_draft()
        state = None if final is not None else (draft or ReviewState.new(manifest.study_id, manifest.reader_id, manifest.case_id))
        if final is None and (
            state.study_id != manifest.study_id or state.reader_id != manifest.reader_id
        ):
            raise StorageError("saved draft identity does not match the selected manifest")
        self.dataset_manifest = dataset_manifest
        self.queue_summary = queue_summary
        self.manifest_source = source_path
        self.manifest = manifest
        self.storage = storage
        self.state = state
        self.final_annotation = final
        self.read_only = final is not None
        self.volume_nodes = {}
        self.ai_mask_node = None
        self.segmentation_node = None
        self.segment_editor_node = None
        return manifest

    def refresh_queue(self) -> QueueSummary | None:
        if self.dataset_manifest is None:
            self.queue_summary = None
            return None
        self.queue_summary = QueueSummary.scan(self.dataset_manifest)
        return self.queue_summary

    def next_unfinished_case_id(self) -> str | None:
        summary = self.refresh_queue()
        if summary is None or not self.case_id:
            return None
        entry = summary.next_unfinished(self.case_id)
        return None if entry is None else entry.case_id

    def save_draft(self, segmentation_source: str | Path | bytes | bytearray | None = None) -> None:
        if self.storage is None or self.state is None:
            raise StorageError("no case is open")
        if self.read_only:
            raise StorageError("completed cases are read-only")
        self.storage.save_draft(self.state, segmentation_source)

    def reveal_ai(self) -> Path:
        """Persist the exposed phase before returning the AI path to the UI."""

        if self.manifest is None or self.storage is None or self.state is None:
            raise StorageError("no case is open")
        if self.read_only:
            raise StorageError("completed cases are read-only")
        self.state.reveal()
        self.storage.save_draft(self.state)
        return self.manifest.ai_mask

    def publish_early_stop(self, status: str) -> dict[str, Any]:
        if self.storage is None or self.state is None:
            raise StorageError("no case is open")
        annotation = self.state.early_stop(status)
        self.storage.publish_early_stop(annotation)
        self.final_annotation = annotation
        self.read_only = True
        self.refresh_queue()
        return annotation

    def publish_complete(self, corrected_labelmap: str | Path | bytes | bytearray) -> dict[str, Any]:
        if self.storage is None or self.state is None:
            raise StorageError("no case is open")
        if self.state.phase != ReviewPhase.AI_REVEALED.value:
            raise StorageError("case must be in ai_revealed phase before completion")
        prior_phase = self.state.phase
        prior_completed_at = self.state.completed_at
        annotation = self.state.complete()
        try:
            self.storage.publish_complete(annotation, corrected_labelmap)
        except Exception:
            self.state.phase = prior_phase
            self.state.completed_at = prior_completed_at
            raise
        self.final_annotation = annotation
        self.read_only = True
        self.refresh_queue()
        return annotation

    def reopen_complete(self, segmentation_source: str | Path | bytes | bytearray | None = None) -> ReviewState:
        """Return the open completed case to the post-reveal correction phase."""

        if self.storage is None or not self.read_only:
            raise StorageError("a completed case must be open")
        state = self.storage.reopen_complete(segmentation_source)
        self.state = state
        self.final_annotation = None
        self.read_only = False
        self.refresh_queue()
        return state

    def load_registered_volumes(self) -> None:
        if not SLICER_AVAILABLE or self.manifest is None:
            return
        self._clear_loaded_nodes()
        for plane in ("sagittal", "axial", "coronal"):
            node = slicer.util.loadVolume(
                str(self.manifest.volumes[plane]),
                {"name": SAFE_VOLUME_NAMES[plane], "singleFile": True},
            )
            if node is None:
                raise RuntimeError(f"Slicer could not load the {plane} volume")
            node.SetName(SAFE_VOLUME_NAMES[plane])
            self.volume_nodes[plane] = node
        validate_common_geometry([self._node_geometry(node) for node in self.volume_nodes.values()])
        self.activate_layout()

    @staticmethod
    def _node_geometry(node: Any) -> ImageGeometry:
        image = node.GetImageData()
        dimensions = tuple(int(value) for value in image.GetDimensions())
        matrix = vtk.vtkMatrix4x4()
        node.GetIJKToRASMatrix(matrix)
        values = [float(matrix.GetElement(row, column)) for row in range(4) for column in range(4)]
        return ImageGeometry(dimensions, tuple(values))

    def activate_layout(self) -> None:
        if not SLICER_AVAILABLE:
            return
        layout_manager = slicer.app.layoutManager()
        layout_node = layout_manager.layoutLogic().GetLayoutNode()
        if not layout_node.IsLayoutDescription(CUSTOM_LAYOUT_ID):
            layout_node.AddLayoutDescription(
                CUSTOM_LAYOUT_ID,
                """
                <layout type="horizontal">
                  <item><view class="vtkMRMLSliceNode" singletontag="MyomaLiteSagittal">
                    <property name="orientation" action="default">Sagittal</property>
                    <property name="viewlabel" action="default">SAG T2</property>
                  </view></item>
                  <item><view class="vtkMRMLSliceNode" singletontag="MyomaLiteAxial">
                    <property name="orientation" action="default">Axial</property>
                    <property name="viewlabel" action="default">AX T2</property>
                  </view></item>
                  <item><view class="vtkMRMLSliceNode" singletontag="MyomaLiteCoronal">
                    <property name="orientation" action="default">Coronal</property>
                    <property name="viewlabel" action="default">COR T2</property>
                  </view></item>
                </layout>
                """,
            )
        layout_manager.setLayout(CUSTOM_LAYOUT_ID)
        for plane, view_name in VIEW_NAMES.items():
            widget = layout_manager.sliceWidget(view_name)
            node = self.volume_nodes.get(plane)
            if widget is None or node is None:
                continue
            composite = widget.sliceLogic().GetSliceCompositeNode()
            composite.SetBackgroundVolumeID(node.GetID())
            composite.SetForegroundVolumeID(None)
            widget.sliceLogic().GetSliceNode().SetOrientation(VIEW_ORIENTATIONS[plane])
            widget.sliceLogic().FitSliceToAll()

    def load_ai_mask(self) -> Any:
        """Load AI data only after ``reveal_ai`` has durably saved the phase."""

        if not SLICER_AVAILABLE or self.manifest is None or self.state is None:
            raise StorageError("Slicer case is not ready")
        if self.state.phase != ReviewPhase.AI_REVEALED.value:
            raise StorageError("AI mask cannot be loaded before reveal")
        if self.ai_mask_node is None:
            self.ai_mask_node = slicer.util.loadLabelVolume(
                str(self.manifest.ai_mask), {"name": "AI mask (hidden source)", "singleFile": True}
            )
            if self.ai_mask_node is None:
                raise RuntimeError("Slicer could not load the AI mask")
            validate_common_geometry(
                [self._node_geometry(node) for node in self.volume_nodes.values()]
                + [self._node_geometry(self.ai_mask_node)]
            )
            values = slicer.util.arrayFromVolume(self.ai_mask_node)
            validate_four_label_values(value for value in values.flat)
            if self.storage is not None and self.storage.draft_segmentation_path.is_file():
                self._load_draft_segmentation()
            else:
                self._create_native_segmentation()
        return self.ai_mask_node

    def _load_draft_segmentation(self) -> None:
        if not SLICER_AVAILABLE or self.storage is None:
            raise StorageError("draft segmentation cannot be loaded outside Slicer")
        node = slicer.util.loadSegmentation(
            str(self.storage.draft_segmentation_path),
            {"name": "MyomaReviewLite resumed correction", "singleFile": True},
        )
        if node is None:
            raise RuntimeError("saved draft segmentation could not be loaded")
        self.segmentation_node = node
        self._normalize_segments()

    def load_completed_segmentation(self) -> None:
        """Display the saved result without loading or exposing the source AI mask."""
        if not self.read_only or self.storage is None:
            raise StorageError("a completed review must be open")
        if (self.final_annotation or {}).get("status") != "complete":
            return
        self.ai_mask_node = slicer.util.loadLabelVolume(
            str(self.storage.corrected_labelmap_path), {"name": "Saved corrected labels", "singleFile": True}
        )
        if self.ai_mask_node is None:
            raise StorageError("saved corrected labels could not be loaded")
        validate_common_geometry([self._node_geometry(self.volume_nodes["sagittal"]), self._node_geometry(self.ai_mask_node)])
        validate_correction_mask_values(slicer.util.arrayFromVolume(self.ai_mask_node).flat)
        self._create_native_segmentation()

    def _create_native_segmentation(self) -> None:
        if self.ai_mask_node is None:
            raise StorageError("AI mask is not loaded")
        self.segmentation_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode", "MyomaReviewLite correction"
        )
        self.segmentation_node.CreateDefaultDisplayNodes()
        self.segmentation_node.SetReferenceImageGeometryParameterFromVolumeNode(
            self.volume_nodes["sagittal"]
        )
        segmentation_logic = slicer.modules.segmentations.logic()
        if not segmentation_logic.ImportLabelmapToSegmentationNode(
            self.ai_mask_node, self.segmentation_node
        ):
            raise RuntimeError("AI labelmap could not be imported into Segment Editor")
        self._normalize_segments()

    def _normalize_segments(self) -> None:
        if self.segmentation_node is None:
            raise StorageError("segmentation node is not available")
        segmentation = self.segmentation_node.GetSegmentation()

        # Lite versions before 1.0 passed AddEmptySegment's ID and name in
        # reverse order after a failed import-name match.  Remove that exact
        # four-placeholder signature so an already-saved failed-reveal draft
        # can be repaired without discarding the imported label segments.
        legacy_placeholder_ids = []
        for label_value, stable_id in FIXED_SEGMENT_IDS.items():
            if label_value > 4:
                continue
            legacy_id = LABEL_NAMES[label_value]
            segment = segmentation.GetSegment(legacy_id)
            if segment is not None and str(segment.GetName()).lower() == stable_id.lower():
                legacy_placeholder_ids.append(legacy_id)
        if len(legacy_placeholder_ids) == 4:
            for segment_id in legacy_placeholder_ids:
                segmentation.RemoveSegment(segment_id)

        # Imported labelmaps may expose their values as names such as "1",
        # "Label_1", or "Label 1".  Map those aliases to stable public IDs,
        # then create an empty fixed segment when a label is genuinely absent.
        existing = [
            segmentation.GetNthSegmentID(index)
            for index in range(segmentation.GetNumberOfSegments())
        ]
        for label_value, stable_id in FIXED_SEGMENT_IDS.items():
            label_name = CORRECTION_LABEL_NAMES[label_value]
            found_id = stable_id if segmentation.GetSegment(stable_id) is not None else None
            aliases = {
                label_name.lower(),
                str(label_value),
                f"label_{label_value}",
                f"label {label_value}",
            }
            if found_id is None:
                for segment_id in existing:
                    segment = segmentation.GetSegment(segment_id)
                    if segment is not None and str(segment.GetName()).strip().lower() in aliases:
                        found_id = segment_id
                        break
            if found_id is None:
                found_id = segmentation.AddEmptySegment(stable_id, label_name)
            elif found_id != stable_id:
                if segmentation.GetSegment(stable_id) is not None:
                    raise ValidationError(f"cannot normalize duplicate segment ID: {stable_id}")
                source_segment = segmentation.GetSegment(found_id)
                normalized_segment = slicer.vtkSegment()
                normalized_segment.DeepCopy(source_segment)
                if not segmentation.AddSegment(normalized_segment, stable_id):
                    raise ValidationError(f"could not assign stable segment ID: {stable_id}")
                segmentation.RemoveSegment(found_id)
                found_id = stable_id
            segment = segmentation.GetSegment(found_id)
            if segment is None:
                raise ValidationError(f"normalized segment is unavailable: {stable_id}")
            segment.SetName(label_name)
            colors = {1: (0.85, 0.50, 0.50), 2: (1.0, 0.85, 0.2), 3: (0.2, 0.85, 0.3),
                      4: (0.4, 0.7, 1.0), 5: (0.7, 0.4, 0.95), 6: (1.0, 0.45, 0.1)}
            segment.SetColor(*colors[label_value])
        actual = [segmentation.GetNthSegmentID(index) for index in range(segmentation.GetNumberOfSegments())]
        validate_fixed_segment_ids(actual)
        self.segment_ids = dict(FIXED_SEGMENT_IDS)

    def setup_segment_editor(self, parent: Any = None) -> Any:
        if not SLICER_AVAILABLE or self.segmentation_node is None:
            return None
        self.segment_editor_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentEditorNode", "MyomaReviewLite editor"
        )
        editor = slicer.qMRMLSegmentEditorWidget()
        if parent is not None:
            editor.setParent(parent)
        editor.setMRMLScene(slicer.mrmlScene)
        editor.setMRMLSegmentEditorNode(self.segment_editor_node)
        self._configure_segment_editor_view_policy(editor)
        editor.setUndoEnabled(True)
        editor.setMaximumNumberOfUndoStates(10)
        editor.setSegmentationNode(self.segmentation_node)
        editor.setSourceVolumeNode(self.volume_nodes["sagittal"])
        editor.setAddRemoveSegmentButtonsVisible(False)
        editor.setSegmentationNodeSelectorVisible(False)
        editor.setSourceVolumeNodeSelectorVisible(False)
        editor.setReadOnly(self.read_only)
        editor.setCurrentSegmentID("myoma")
        return editor

    @staticmethod
    def _configure_segment_editor_view_policy(editor: Any) -> None:
        if hasattr(editor, "setAutoShowSourceVolumeNode"):
            editor.setAutoShowSourceVolumeNode(False)
        try:
            editor.autoShowSourceVolumeNode = False
        except Exception:
            pass
        # Do not call setEffectName or hide effects: the native editor must
        # expose every installed effect in correction mode.

    def validate_correction_segments(self) -> None:
        if self.segmentation_node is None:
            raise ValidationError("the correction segmentation is not loaded")
        segmentation = self.segmentation_node.GetSegmentation()
        actual = {
            segmentation.GetNthSegmentID(index): segmentation.GetSegment(
                segmentation.GetNthSegmentID(index)
            ).GetName()
            for index in range(segmentation.GetNumberOfSegments())
        }
        validate_fixed_segments(actual)

    def export_corrected_labelmap(self) -> Path:
        if not SLICER_AVAILABLE or self.storage is None or self.segmentation_node is None:
            raise StorageError("Slicer correction is not ready")
        self.validate_correction_segments()
        reference = self.volume_nodes["sagittal"]
        working = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "Lite export buffer")
        try:
            success = slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
                self.segmentation_node,
                list(FIXED_SEGMENT_IDS.values()),
                working,
                reference,
                slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY,
            )
            if not success:
                raise RuntimeError("corrected labelmap export failed")
            temporary = self.storage.case_directory / ".corrected_labels.tmp.nii.gz"
            if not slicer.util.saveNode(working, str(temporary)):
                raise RuntimeError("corrected labelmap could not be written")
            annotation = self.publish_complete(temporary)
            _ = annotation
            return self.storage.corrected_labelmap_path
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except (NameError, OSError):
                pass
            if working.GetScene() is slicer.mrmlScene:
                slicer.mrmlScene.RemoveNode(working)

    def _clear_loaded_nodes(self) -> None:
        if not SLICER_AVAILABLE:
            return
        for node in list(self.volume_nodes.values()) + [self.ai_mask_node, self.segmentation_node, self.segment_editor_node]:
            if node is not None and node.GetScene() is slicer.mrmlScene:
                slicer.mrmlScene.RemoveNode(node)
        self.volume_nodes = {}
        self.ai_mask_node = None
        self.segmentation_node = None
        self.segment_editor_node = None


if SLICER_AVAILABLE:  # pragma: no cover - exercised in Slicer smoke tests

    class LiteRoiLabelOverlay:
        """Display stable M## labels on every view whose slice intersects a box."""

        def __init__(self, widget: "MyomaReviewLiteWidget") -> None:
            self.widget = widget
            self.entries: dict[int, dict[str, Any]] = {}
            self.views: dict[str, dict[str, Any]] = {}
            self.enabled = True
            # When set, only this myoma is labelled (the FIGO stage shows one
            # box at a time); None labels every box.
            self.only_id: str | None = None

        def set_enabled(self, enabled: bool) -> None:
            self.enabled = bool(enabled)
            self.refresh()

        def set_only(self, public_id: str | None) -> None:
            if public_id == self.only_id:
                return
            self.only_id = public_id
            self.refresh()

        def bind_views(self) -> None:
            self.unbind_views()
            manager = slicer.app.layoutManager()
            for view_name in VIEW_NAMES.values():
                slice_widget = manager.sliceWidget(view_name)
                if slice_widget is None:
                    continue
                slice_view = slice_widget.sliceView()
                renderer = slice_view.renderWindow().GetRenderers().GetFirstRenderer()
                if renderer is None:
                    continue
                slice_node = slice_widget.sliceLogic().GetSliceNode()
                observer = slice_node.AddObserver(
                    vtk.vtkCommand.ModifiedEvent,
                    lambda _caller, _event, name=view_name: self.refresh_view(name),
                )
                self.views[view_name] = {"view": slice_view, "renderer": renderer, "slice_node": slice_node, "observer": observer}
            for entry in self.entries.values():
                self._create_actors(entry)
            self.refresh()

        def unbind_views(self) -> None:
            for entry in self.entries.values():
                for view_name, actor in list(entry["actors"].items()):
                    binding = self.views.get(view_name)
                    if binding:
                        binding["renderer"].RemoveActor2D(actor)
                entry["actors"].clear()
            for binding in self.views.values():
                try:
                    binding["slice_node"].RemoveObserver(binding["observer"])
                except Exception:
                    pass
            self.views.clear()

        def clear(self) -> None:
            self.unbind_views()
            for entry in self.entries.values():
                node = entry["node"]
                if entry.get("node_observer") is not None:
                    node.RemoveObserver(entry["node_observer"])
            self.entries.clear()

        def track(self, node: Any, public_id: str) -> None:
            self.untrack(node)
            observer = node.AddObserver(vtk.vtkCommand.ModifiedEvent, lambda *_: self.refresh())
            entry = {"node": node, "public_id": public_id, "actors": {}, "node_observer": observer}
            self.entries[id(node)] = entry
            self._create_actors(entry)
            self.refresh()

        def untrack(self, node: Any) -> None:
            entry = self.entries.pop(id(node), None)
            if not entry:
                return
            for view_name, actor in entry["actors"].items():
                binding = self.views.get(view_name)
                if binding:
                    binding["renderer"].RemoveActor2D(actor)
            try:
                node.RemoveObserver(entry["node_observer"])
            except Exception:
                pass

        def _create_actors(self, entry: dict[str, Any]) -> None:
            for view_name, binding in self.views.items():
                if view_name in entry["actors"]:
                    continue
                actor = vtk.vtkTextActor()
                actor.SetInput(entry["public_id"])
                actor.PickableOff()
                prop = actor.GetTextProperty()
                prop.SetFontSize(16)
                prop.BoldOn()
                prop.SetColor(1.0, 0.93, 0.2)
                binding["renderer"].AddActor2D(actor)
                entry["actors"][view_name] = actor

        def refresh(self) -> None:
            for view_name in tuple(self.views):
                self.refresh_view(view_name)

        def refresh_view(self, view_name: str) -> None:
            binding = self.views.get(view_name)
            if binding is None:
                return
            axis = 0 if "Sagittal" in view_name else 1 if "Coronal" in view_name else 2
            offset = float(binding["slice_node"].GetSliceOffset())
            for row, entry in enumerate(sorted(self.entries.values(), key=lambda item: item["public_id"])):
                actor = entry["actors"].get(view_name)
                node = entry["node"]
                if self.only_id is not None:
                    if entry["public_id"] != self.only_id:
                        actor.SetVisibility(False)
                        continue
                    row = 0
                bounds = [0.0] * 6
                try:
                    node.GetRASBounds(bounds)
                except Exception:
                    actor.SetVisibility(False)
                    continue
                visible = bounds[axis * 2] - 1e-4 <= offset <= bounds[axis * 2 + 1] + 1e-4
                actor.SetVisibility(bool(visible and self.enabled))
                if visible:
                    actor.SetDisplayPosition(8, 12 + row * 22)
            binding["view"].scheduleRender()


    class LiteRoiClickSelector:
        """Reveal one myoma box's handles when the reader clicks that box.

        The observers sit at a negative priority and never abort an event, so
        the Segment Editor effects, the corner drag tool, and the markups
        handles all still see every click first.
        """

        CLICK_SLOP_PIXELS = 3

        def __init__(self, widget: "MyomaReviewLiteWidget") -> None:
            self.widget = widget
            self.observers: list[tuple[Any, int]] = []
            self.press_xy: dict[str, tuple[int, int]] = {}

        def bind_views(self) -> None:
            self.unbind_views()
            manager = slicer.app.layoutManager()
            for view_name in VIEW_NAMES.values():
                slice_widget = manager.sliceWidget(view_name)
                if slice_widget is None:
                    continue
                interactor = slice_widget.sliceView().interactor()
                for event, callback in (
                    (vtk.vtkCommand.LeftButtonPressEvent, self._press),
                    (vtk.vtkCommand.LeftButtonReleaseEvent, self._release),
                ):
                    tag = interactor.AddObserver(
                        event,
                        lambda caller, _event, name=view_name, cb=callback: cb(caller, name),
                        -1.0,
                    )
                    self.observers.append((interactor, tag))

        def unbind_views(self) -> None:
            for interactor, tag in self.observers:
                try:
                    interactor.RemoveObserver(tag)
                except Exception:
                    pass
            self.observers = []
            self.press_xy = {}

        def _press(self, caller: Any, view_name: str) -> None:
            position = caller.GetEventPosition()
            self.press_xy[view_name] = (int(position[0]), int(position[1]))
            try:
                self.widget.begin_view_click()
            except Exception:
                traceback.print_exc()

        def _release(self, caller: Any, view_name: str) -> None:
            start = self.press_xy.pop(view_name, None)
            if start is None:
                return
            position = caller.GetEventPosition()
            end = (int(position[0]), int(position[1]))
            # A drag is a pan, a window/level change, or a handle being moved.
            if max(abs(end[0] - start[0]), abs(end[1] - start[1])) > self.CLICK_SLOP_PIXELS:
                return
            try:
                point_ras = self.widget.view_point_to_ras(view_name, end)
            except Exception:
                return
            try:
                self.widget.on_view_clicked(view_name, point_ras)
            except Exception:
                traceback.print_exc()


    def _draw_blocked_actions(style: Any) -> int:
        """The slice-view actions a box drag would trigger: left-drag window/level."""

        mask = 0
        for name in ("AdjustWindowLevelBackground", "AdjustWindowLevelForeground"):
            mask |= int(getattr(style, name, 0))
        return mask

    class LiteRectangleDrawTool:
        """Corner drag tool that restores every temporarily disabled action."""

        def __init__(self, widget: "MyomaReviewLiteWidget") -> None:
            self.widget = widget
            self.observers: list[tuple[Any, int]] = []
            self.styles: list[tuple[Any, str, Any]] = []
            self.active_view: str | None = None
            self.start_xy: tuple[int, int] | None = None
            self.update_lesion_id: str | None = None
            self.preview_node: Any = None

        @property
        def active(self) -> bool:
            return bool(self.observers)

        def start(self, update_lesion_id: str | None = None) -> None:
            self.cancel()
            self.update_lesion_id = update_lesion_id
            manager = slicer.app.layoutManager()
            for view_name in VIEW_NAMES.values():
                slice_widget = manager.sliceWidget(view_name)
                if slice_widget is None:
                    continue
                view = slice_widget.sliceView()
                interactor = view.interactor()
                style = view.interactorObserver() if hasattr(view, "interactorObserver") else interactor.GetInteractorStyle()
                if style is not None and hasattr(style, "SetActionEnabled") and hasattr(style, "AllActionsMask"):
                    mask = int(style.AllActionsMask)
                    states = tuple((1 << bit, bool(style.GetActionEnabled(1 << bit))) for bit in range(mask.bit_length()) if mask & (1 << bit))
                    scalar = style.GetScalarBarDisplayableManager() if hasattr(style, "GetScalarBarDisplayableManager") else None
                    scalar_states = None
                    if scalar is not None and hasattr(scalar, "GetAdjustBackgroundWindowLevelEnabled"):
                        scalar_states = (bool(scalar.GetAdjustBackgroundWindowLevelEnabled()), bool(scalar.GetAdjustForegroundWindowLevelEnabled()))
                    self.styles.append((style, "actions", (states, scalar, scalar_states)))
                    # Only plain left-drag window/level competes with drawing;
                    # zoom, pan and slice scrolling stay usable while placing.
                    style.SetActionEnabled(_draw_blocked_actions(style), False)
                    if scalar_states is not None:
                        scalar.SetAdjustBackgroundWindowLevelEnabled(False)
                        scalar.SetAdjustForegroundWindowLevelEnabled(False)
                elif style is not None and hasattr(style, "SetEnabled"):
                    prior = bool(style.GetEnabled()) if hasattr(style, "GetEnabled") else True
                    self.styles.append((style, "enabled", prior))
                    style.SetEnabled(False)
                for event, callback in (
                    (vtk.vtkCommand.LeftButtonPressEvent, self._press),
                    (vtk.vtkCommand.MouseMoveEvent, self._move),
                    (vtk.vtkCommand.LeftButtonReleaseEvent, self._release),
                ):
                    tag = interactor.AddObserver(event, lambda caller, _event, name=view_name, cb=callback: cb(caller, name), 2.0)
                    self.observers.append((interactor, tag))

        def cancel(self) -> None:
            if self.preview_node is not None:
                self.widget.remove_roi_preview(self.preview_node)
            for interactor, tag in self.observers:
                try:
                    interactor.RemoveObserver(tag)
                except Exception:
                    pass
            for style, mode, prior in self.styles:
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
            self.observers = []
            self.styles = []
            self.active_view = None
            self.start_xy = None
            self.update_lesion_id = None
            self.preview_node = None

        def _press(self, caller: Any, view_name: str) -> None:
            if self.active_view is None:
                self.active_view = view_name
                position = caller.GetEventPosition()
                self.start_xy = (int(position[0]), int(position[1]))

        def _move(self, caller: Any, view_name: str) -> None:
            if self.active_view != view_name or self.start_xy is None:
                return
            position = caller.GetEventPosition()
            try:
                geometry = self.widget.rectangle_geometry(view_name, self.start_xy, (int(position[0]), int(position[1])))
                self.preview_node = self.widget.update_roi_preview(self.preview_node, geometry, view_name)
            except Exception:
                return

        def _release(self, caller: Any, view_name: str) -> None:
            if self.active_view != view_name or self.start_xy is None:
                return
            position = caller.GetEventPosition()
            end = (int(position[0]), int(position[1]))
            if max(abs(end[0] - self.start_xy[0]), abs(end[1] - self.start_xy[1])) < 5:
                self.cancel()
                # draw_tool.active just dropped to false with no draft box to
                # follow it, so restore the other boxes and the normal cursor.
                self.widget._apply_roi_selection()
                return
            geometry = self.widget.rectangle_geometry(view_name, self.start_xy, end)
            update_id = self.update_lesion_id
            self.cancel()
            self.widget.accept_rectangle(geometry, update_id)


    class LiteShiftCrosshairToggle(qt.QObject):
        """Shows the small "+" crosshair only while Shift is held.

        Holding Shift and moving the mouse over one slice view already
        drags the shared crosshair position across all three views (stock
        Slicer behavior); this filter just makes the "+" appear on Shift
        down and disappear on Shift up, instead of staying put at its last
        position once Shift is released.
        """

        def __init__(self, crosshair_node: Any) -> None:
            super().__init__()
            self.crosshair_node = crosshair_node
            self.installed = False

        def install(self) -> None:
            if self.installed:
                return
            slicer.app.installEventFilter(self)
            self.installed = True

        def uninstall(self) -> None:
            if not self.installed:
                return
            slicer.app.removeEventFilter(self)
            self.installed = False

        def eventFilter(self, obj: Any, event: Any) -> bool:
            event_type = event.type()
            if event_type == qt.QEvent.KeyPress and event.key() == qt.Qt.Key_Shift and not event.isAutoRepeat():
                self.crosshair_node.SetCrosshairMode(slicer.vtkMRMLCrosshairNode.ShowSmallBasic)
            elif event_type == qt.QEvent.KeyRelease and event.key() == qt.Qt.Key_Shift and not event.isAutoRepeat():
                self.crosshair_node.SetCrosshairMode(slicer.vtkMRMLCrosshairNode.NoCrosshair)
            return False


    class LiteMouseButtonDispatcher(qt.QObject):
        """Fires a bound action when one of its bound mouse buttons is pressed.

        Installed application-wide (like LiteShiftCrosshairToggle) so a side
        button works no matter which slice view or control has focus. Only
        buttons a reviewer has explicitly bound are intercepted; unbound
        buttons -- and Left/Right, which are never bindable -- always pass
        through untouched.
        """

        def __init__(self, widget: "MyomaReviewLiteWidget") -> None:
            super().__init__()
            self.widget = widget
            # Qt::MouseButton value -> method name on ``widget``.
            self.bindings: dict[int, str] = {}
            self.enabled = False
            self.installed = False

        def install(self) -> None:
            if self.installed:
                return
            slicer.app.installEventFilter(self)
            self.installed = True

        def uninstall(self) -> None:
            if not self.installed:
                return
            slicer.app.removeEventFilter(self)
            self.installed = False

        def eventFilter(self, obj: Any, event: Any) -> bool:
            if not self.enabled or event.type() != qt.QEvent.MouseButtonPress:
                return False
            method_name = self.bindings.get(int(event.button()))
            if method_name is None:
                return False
            method = getattr(self.widget, method_name, None)
            if method is None:
                return False
            try:
                method()
            except Exception:
                traceback.print_exc()
            # Consumed: every bindable button does nothing else in this module,
            # so nothing downstream needs to see the press too.
            return True


    class LiteMouseButtonCapture(qt.QObject):
        """One-shot app-wide filter that reports the next mouse button pressed.

        Used by the shortcuts dialog's "Set mouse button..." control: armed on
        click, it removes itself and reports the very next button pressed
        anywhere in the application, mirroring how QKeySequenceEdit captures a
        key combination.
        """

        def __init__(self, on_captured: Callable[[int], None]) -> None:
            super().__init__()
            self.on_captured = on_captured

        def eventFilter(self, obj: Any, event: Any) -> bool:
            if event.type() == qt.QEvent.MouseButtonPress:
                slicer.app.removeEventFilter(self)
                self.on_captured(int(event.button()))
                return True
            return False


else:

    class LiteRoiLabelOverlay:  # pragma: no cover - only a fallback symbol
        def __init__(self, widget: Any) -> None:
            self.widget = widget

        def bind_views(self) -> None:
            pass

        def unbind_views(self) -> None:
            pass

        def clear(self) -> None:
            pass

        def track(self, node: Any, public_id: str) -> None:
            pass

        def untrack(self, node: Any) -> None:
            pass

        def refresh(self) -> None:
            pass

        def set_enabled(self, enabled: bool) -> None:
            pass

        def set_only(self, public_id: str | None) -> None:
            pass


    class LiteRoiClickSelector:  # pragma: no cover - only a fallback symbol
        def __init__(self, widget: Any) -> None:
            self.widget = widget

        def bind_views(self) -> None:
            pass

        def unbind_views(self) -> None:
            pass


    class LiteRectangleDrawTool:  # pragma: no cover - only a fallback symbol
        def __init__(self, widget: Any) -> None:
            self.widget = widget

        @property
        def active(self) -> bool:
            return False

        def start(self, update_lesion_id: str | None = None) -> None:
            raise RuntimeError("ROI drawing requires a running Slicer application")

        def cancel(self) -> None:
            pass


    class LiteShiftCrosshairToggle:  # pragma: no cover - only a fallback symbol
        def __init__(self, crosshair_node: Any) -> None:
            self.crosshair_node = crosshair_node

        def install(self) -> None:
            pass

        def uninstall(self) -> None:
            pass


class MyomaReviewLiteWidget(ScriptedLoadableModuleWidget):
    """One-page blinded review plus the native Segment Editor correction page."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.logic = MyomaReviewLiteLogic()
        self.roi_nodes: dict[str, Any] = {}
        self.roi_outline_nodes: dict[str, Any] = {}
        self.roi_draft_outline: Any = None
        self.roi_outline_sources: dict[str, Any] = {}
        self.roi_live_sync: tuple[Any, int] | None = None
        self.roi_boxes_visible = True
        self.active_roi_id: str | None = None
        self.active_roi_geometry: BoxGeometry | None = None
        # Gates the blind page between "boxing" and "FIGO typing": the reader
        # places every myoma box first, then explicitly moves on to FIGO.
        self._boxing_stage_done = False
        self.roi_click_baseline: BoxGeometry | None = None
        self.committing_roi_geometry = False
        self.roi_transaction = RoiTransaction()
        self.roi_draft_node: Any = None
        self.roi_draft_update_id: str | None = None
        self.roi_overlay: LiteRoiLabelOverlay | None = None
        self.draw_tool: LiteRectangleDrawTool | None = None
        self.click_selector: LiteRoiClickSelector | None = None
        self.segment_editor_widget: Any = None
        self.save_coordinator = ReviewSaveCoordinator()
        self.segmentation_observer: tuple[Any, int] | None = None
        self.autosave_timer: Any = None
        self.shutdown_saved = False
        self.annotation_undo: list[dict[str, Any]] = []
        self.annotation_redo: list[dict[str, Any]] = []
        self.shortcuts: list[Any] = []
        self.shortcut_bindings: dict[str, Any] = {}
        self.mouse_button_bindings: dict[str, int] = {}
        self.mouse_button_dispatcher: Any = None
        self.view_tools: Any = None
        self.correction_tools: Any = None
        self.shift_crosshair_toggle: Any = None
        self._case_filter = DEFAULT_CASE_FILTER
        # Placing/redrawing a box swaps the slice-view cursor for a small
        # yellow dot and hides every other myoma box so the reader gets an
        # uncluttered view of the anatomy underneath; see _apply_roi_selection
        # and _has_unresolved_roi.
        self._place_box_cursor: Any = None
        self._box_cursor_applied = False

    def setup(self) -> None:
        super().setup()
        if not SLICER_AVAILABLE:
            return
        self._build_ui()
        self._install_shortcuts()
        self._install_mouse_bindings()
        self.roi_overlay = LiteRoiLabelOverlay(self)
        self.draw_tool = LiteRectangleDrawTool(self)
        self.click_selector = LiteRoiClickSelector(self)
        self._enable_synced_crosshair()
        self._disable_oblique_rotate()
        self._hide_data_probe()
        self._hide_module_panel_boilerplate()
        self.autosave_timer = qt.QTimer()
        self.autosave_timer.setInterval(60_000)
        self.autosave_timer.connect("timeout()", self.on_autosave_timeout)
        self.autosave_timer.start()
        slicer.app.connect("aboutToQuit()", self.on_application_shutdown)
        launch_path = os.environ.get("MYOMA_REVIEW_LITE_CASE", "").strip()
        if launch_path:
            qt.QTimer.singleShot(0, lambda path=launch_path: self.open_case(path))

    def _shortcut_setting_key(self, action_id: str) -> str:
        return f"{SHORTCUT_SETTINGS_PREFIX}{action_id}"

    def _shortcut_key_sequence(self, settings: Any, action_id: str, default_key: str) -> str:
        stored = settings.value(self._shortcut_setting_key(action_id), default_key)
        return str(stored) if stored is not None else ""

    def _install_shortcuts(self) -> None:
        # Bindings are per-reviewer: each one is read from QSettings (falling
        # back to the factory default the first time) so the panel opened via
        # "Keyboard shortcuts..." can rebind them without a restart.
        settings = qt.QSettings()
        for action_id, _label, default_key, method_name, *_ in SHORTCUT_ACTIONS:
            key_text = self._shortcut_key_sequence(settings, action_id, default_key)
            shortcut = qt.QShortcut(qt.QKeySequence(key_text), slicer.util.mainWindow())
            shortcut.connect("activated()", getattr(self, method_name))
            shortcut.enabled = False
            self.shortcuts.append(shortcut)
            self.shortcut_bindings[action_id] = shortcut
        self._refresh_shortcut_button_labels()

    def _mouse_button_setting_key(self, action_id: str) -> str:
        return f"{MOUSE_BUTTON_SETTINGS_PREFIX}{action_id}"

    def _mouse_button_value(self, settings: Any, action_id: str) -> int:
        # No factory default here (unlike the keyboard bindings): not every
        # mouse has a spare button, so a reviewer opts in explicitly instead
        # of inheriting a binding that might not exist on their hardware.
        stored = settings.value(self._mouse_button_setting_key(action_id), 0)
        try:
            return int(stored)
        except (TypeError, ValueError):
            return 0

    def _install_mouse_bindings(self) -> None:
        settings = qt.QSettings()
        self.mouse_button_dispatcher = LiteMouseButtonDispatcher(self)
        for action_id, _label, _default_key, method_name, *_ in SHORTCUT_ACTIONS:
            value = self._mouse_button_value(settings, action_id)
            self.mouse_button_bindings[action_id] = value
            if value:
                self.mouse_button_dispatcher.bindings[value] = method_name
        self.mouse_button_dispatcher.install()
        self._refresh_shortcut_button_labels()

    def _refresh_shortcut_button_labels(self) -> None:
        # Keeps each toolbar button's label showing its *current* binding
        # (which may differ from the factory default once a reviewer has
        # rebound it), rather than a stale hint baked in at build time.
        for action_id, _label, _default_key, _method_name, button_attr, base_text in SHORTCUT_ACTIONS:
            if button_attr is None:
                continue
            button = getattr(self, button_attr, None)
            if button is None:
                continue
            shortcut = self.shortcut_bindings.get(action_id)
            key_text = shortcut.key.toString() if shortcut is not None else ""
            mouse_value = self.mouse_button_bindings.get(action_id, 0)
            mouse_text = _mouse_button_label(mouse_value) if mouse_value else ""
            hint = ", ".join(text for text in (key_text, mouse_text) if text)
            button.text = f"{base_text} ({hint})" if hint else base_text

    def on_configure_shortcuts(self) -> None:
        """Open the shortcut panel so a reviewer can rebind any action, by key or by mouse button."""
        settings = qt.QSettings()
        dialog = qt.QDialog(slicer.util.mainWindow())
        dialog.setWindowTitle("Shortcuts")
        layout = qt.QVBoxLayout(dialog)
        form = qt.QFormLayout()
        layout.addLayout(form)
        editors: dict[str, Any] = {}
        mouse_buttons: dict[str, int] = dict(self.mouse_button_bindings)
        mouse_labels: dict[str, Any] = {}
        # Holds the in-progress capture filter between the click that arms it
        # and the button press it is waiting for; nothing else keeps it
        # referenced, and an unfinished capture is torn down below once the
        # dialog closes either way.
        capture_state: dict[str, Any] = {"active": None}

        def _mouse_button_text(action_id: str) -> str:
            return _mouse_button_label(mouse_buttons.get(action_id, 0))

        def _set_mouse_button(action_id: str, value: int) -> None:
            mouse_buttons[action_id] = value
            mouse_labels[action_id].text = _mouse_button_text(action_id)

        def _start_capture(action_id: str, capture_button: Any) -> None:
            if capture_state["active"] is not None:
                return  # Only one capture armed at a time.
            original_text = capture_button.text
            capture_button.text = "Press a mouse button..."
            capture_button.enabled = False

            def _finish(value: int) -> None:
                capture_state["active"] = None
                capture_button.text = original_text
                capture_button.enabled = True
                if value in MOUSE_BUTTON_RESERVED:
                    qt.QMessageBox.warning(
                        dialog,
                        "Reserved button",
                        "The left and right mouse buttons can't be reassigned; "
                        "they are needed for normal editing.",
                    )
                    return
                _set_mouse_button(action_id, value)

            capture = LiteMouseButtonCapture(_finish)
            capture_state["active"] = capture
            # Installed after (and so, per Qt's filter order, checked before)
            # the persistent LiteMouseButtonDispatcher, so a button that is
            # already bound to some other action fires this capture instead
            # of that action while the dialog is waiting for a press.
            slicer.app.installEventFilter(capture)

        for action_id, label, _default_key, _method_name, *_ in SHORTCUT_ACTIONS:
            editor = qt.QKeySequenceEdit()
            shortcut = self.shortcut_bindings.get(action_id)
            if shortcut is not None:
                editor.setKeySequence(shortcut.key)
            editors[action_id] = editor
            row = qt.QHBoxLayout()
            row.addWidget(editor, 1)
            clear_button = qt.QToolButton()
            clear_button.text = "Clear"
            clear_button.connect("clicked()", lambda _checked=False, e=editor: e.clear())
            row.addWidget(clear_button)

            mouse_label = qt.QLabel(_mouse_button_text(action_id))
            mouse_label.minimumWidth = 110
            mouse_labels[action_id] = mouse_label
            row.addWidget(mouse_label)
            capture_button = qt.QToolButton()
            capture_button.text = "Set mouse button..."
            capture_button.connect(
                "clicked()",
                lambda _checked=False, a=action_id, b=capture_button: _start_capture(a, b),
            )
            row.addWidget(capture_button)
            mouse_clear_button = qt.QToolButton()
            mouse_clear_button.text = "Clear"
            mouse_clear_button.connect(
                "clicked()", lambda _checked=False, a=action_id: _set_mouse_button(a, 0)
            )
            row.addWidget(mouse_clear_button)

            form.addRow(label, row)
        hint = qt.QLabel(
            "Click a field and press the new key combination, or click \"Set "
            "mouse button...\" and press a mouse button, to rebind an action. "
            "Leave a field (or a mouse binding) at empty / \"(none)\" to "
            "disable it. Left and right click always stay reserved for "
            "editing. A mouse's extra buttons beyond its two side buttons "
            "are usually remapped to a keystroke by the mouse's own driver "
            "software instead of sending a real click -- if \"Set mouse "
            "button...\" never catches one, try binding it as a keyboard "
            "shortcut above instead."
        )
        hint.wordWrap = True
        layout.addWidget(hint)
        restore_button = qt.QPushButton("Restore factory defaults")

        def _restore_defaults() -> None:
            for action_id, _label, default_key, _method_name, *_ in SHORTCUT_ACTIONS:
                editors[action_id].setKeySequence(qt.QKeySequence(default_key))
                _set_mouse_button(action_id, 0)

        restore_button.connect("clicked()", _restore_defaults)
        layout.addWidget(restore_button)
        buttons = qt.QDialogButtonBox(qt.QDialogButtonBox.Save | qt.QDialogButtonBox.Cancel)
        buttons.connect("accepted()", dialog.accept)
        buttons.connect("rejected()", dialog.reject)
        layout.addWidget(buttons)

        accepted = dialog.exec_() == qt.QDialog.Accepted
        if capture_state["active"] is not None:
            # The dialog closed (Save, Cancel, or Esc) mid-capture; the next
            # button press must not still be waiting for this dialog.
            try:
                slicer.app.removeEventFilter(capture_state["active"])
            except Exception:
                pass
            capture_state["active"] = None
        if not accepted:
            return

        new_key_text = {
            action_id: editor.keySequence().toString() for action_id, editor in editors.items()
        }
        labels = {action_id: label for action_id, label, *_ in SHORTCUT_ACTIONS}
        method_by_action = {
            action_id: method_name for action_id, _label, _default_key, method_name, *_ in SHORTCUT_ACTIONS
        }
        seen: dict[str, str] = {}
        conflicts: list[str] = []
        for action_id, key_text in new_key_text.items():
            if not key_text:
                continue
            if key_text in seen:
                conflicts.append(f"{labels[seen[key_text]]} and {labels[action_id]} both use \"{key_text}\"")
            else:
                seen[key_text] = action_id
        seen_mouse: dict[int, str] = {}
        for action_id, value in mouse_buttons.items():
            if not value:
                continue
            if value in seen_mouse:
                conflicts.append(
                    f"{labels[seen_mouse[value]]} and {labels[action_id]} both use "
                    f"{_mouse_button_label(value)}"
                )
            else:
                seen_mouse[value] = action_id
        if conflicts:
            qt.QMessageBox.warning(
                slicer.util.mainWindow(),
                "Shortcut conflict",
                "These shortcuts were not saved because they collide:\n" + "\n".join(conflicts),
            )
            return

        for action_id, key_text in new_key_text.items():
            settings.setValue(self._shortcut_setting_key(action_id), key_text)
            self.shortcut_bindings[action_id].setKey(qt.QKeySequence(key_text))
        for action_id, value in mouse_buttons.items():
            settings.setValue(self._mouse_button_setting_key(action_id), value)
        self.mouse_button_bindings = mouse_buttons
        if self.mouse_button_dispatcher is not None:
            self.mouse_button_dispatcher.bindings = {
                value: method_by_action[action_id]
                for action_id, value in mouse_buttons.items()
                if value
            }
        self._refresh_shortcut_button_labels()

    def _enable_synced_crosshair(self) -> None:
        # A small "+" that follows the mouse in whichever slice view is
        # hovered, and is projected onto the other two slice views at the
        # matching RAS position, so hovering a voxel in sagittal shows
        # exactly where that voxel sits on the current coronal/axial
        # slices (and vice versa). This is stock Slicer behavior driven by
        # the shared vtkMRMLCrosshairNode. It stays hidden until Shift is
        # held (see LiteShiftCrosshairToggle) rather than being shown all
        # the time.
        try:
            crosshair_node = slicer.util.getNode("Crosshair")
        except Exception:
            crosshair_node = None
        if crosshair_node is None:
            return
        crosshair_node.SetCrosshairMode(slicer.vtkMRMLCrosshairNode.NoCrosshair)
        crosshair_node.SetCrosshairThickness(slicer.vtkMRMLCrosshairNode.Fine)

    def _hide_data_probe(self) -> None:
        # Stock Slicer's Data Probe panel (the "Data Probe" collapsible
        # button with the "Show Zoomed Slice" checkbox and L/F/B coordinate
        # readout, docked under the module panel) eats vertical space this
        # module never uses, so it is removed outright.
        try:
            main_window = slicer.util.mainWindow()
        except Exception:
            return
        if main_window is None:
            return
        seen: set[int] = set()
        # Known objectName first (fast path when it matches Slicer's build).
        for object_name in ("DataProbeCollapsibleWidget", "DataProbeCollapsibleWidgetArea"):
            try:
                widget = slicer.util.findChild(main_window, object_name)
            except Exception:
                widget = None
            if widget is not None:
                seen.add(id(widget))
                self._hide_widget(widget)
        # Fallback: hunt down the "Data Probe" ctkCollapsibleButton by its
        # visible title, in case the objectName differs across Slicer
        # builds/versions.
        try:
            candidates = list(slicer.util.findChildren(main_window, className="ctkCollapsibleButton"))
        except Exception:
            candidates = []
        for candidate in candidates:
            if id(candidate) in seen:
                continue
            try:
                title = str(candidate.text)
            except Exception:
                continue
            if title.strip().lower() == "data probe":
                seen.add(id(candidate))
                self._hide_widget(candidate)
        try:
            data_probe_instance = slicer.modules.DataProbeInstance
        except Exception:
            data_probe_instance = None
        if data_probe_instance is not None:
            try:
                self._hide_widget(data_probe_instance.infoWidget)
            except Exception:
                pass

    def _hide_module_panel_boilerplate(self) -> None:
        # Stock Slicer boilerplate that has nothing to do with reading:
        # the "Help & Acknowledgement" collapsible (built by the module
        # panel itself from parent.helpText/acknowledgementText) and the
        # "Reload & Test" collapsible (added by ScriptedLoadableModuleWidget
        # only when Developer Mode is on, which some reading workstations
        # have enabled). Both just eat vertical space above the actual
        # module UI, so they are hidden the same way _hide_data_probe hides
        # the Data Probe panel.
        if hasattr(self, "reloadCollapsibleButton"):
            try:
                self._hide_widget(self.reloadCollapsibleButton)
            except Exception:
                pass
        try:
            main_window = slicer.util.mainWindow()
        except Exception:
            return
        if main_window is None:
            return
        seen: set[int] = set()
        try:
            widget = slicer.util.findChild(main_window, "HelpCollapsibleButton")
        except Exception:
            widget = None
        if widget is not None:
            seen.add(id(widget))
            self._hide_widget(widget)
        try:
            candidates = list(slicer.util.findChildren(main_window, className="ctkCollapsibleButton"))
        except Exception:
            candidates = []
        hidden_titles = {"help & acknowledgement", "reload && test", "reload & test"}
        for candidate in candidates:
            if id(candidate) in seen:
                continue
            try:
                title = str(candidate.text).strip().lower()
            except Exception:
                continue
            if title in hidden_titles:
                seen.add(id(candidate))
                self._hide_widget(candidate)

    @staticmethod
    def _hide_widget(widget: Any) -> None:
        # Setting visible=False is enough for Qt's layouts to stop
        # reserving space for it; the container itself is left alone so
        # sibling widgets (the module panel, the module selector, ...)
        # are never touched.
        try:
            widget.visible = False
            widget.setMaximumHeight(0)
        except Exception:
            pass

    def _disable_oblique_rotate(self) -> None:
        # Ctrl + left-drag on a slice view is stock Slicer's "rotate slice
        # to oblique" gesture. This viewer only ever shows fixed axial/
        # sagittal/coronal planes, nothing here uses obliques, and that
        # gesture has been observed to crash Slicer itself (a native
        # access violation inside Slicer's own MRMLDisplayableManager.dll,
        # not a bug in this module) on at least one reading workstation.
        # Disable just that one action; pan/zoom/window-level/crosshair
        # are untouched.
        try:
            manager = slicer.app.layoutManager()
        except Exception:
            return
        for view_name in VIEW_NAMES.values():
            try:
                slice_widget = manager.sliceWidget(view_name)
                if slice_widget is None:
                    continue
                view = slice_widget.sliceView()
                interactor = view.interactor()
                style = view.interactorObserver() if hasattr(view, "interactorObserver") else interactor.GetInteractorStyle()
                if style is not None and hasattr(style, "SetActionEnabled") and hasattr(style, "Rotate"):
                    style.SetActionEnabled(style.Rotate, False)
            except Exception:
                traceback.print_exc()
        try:
            crosshair_node = slicer.util.getNode("Crosshair")
        except Exception:
            crosshair_node = None
        if crosshair_node is None:
            return
        self.shift_crosshair_toggle = LiteShiftCrosshairToggle(crosshair_node)
        self.shift_crosshair_toggle.install()

    def _build_ui(self) -> None:
        header = qt.QHBoxLayout()
        self.manifest_button = qt.QPushButton("Open case manifest")
        self.manifest_button.connect("clicked()", self.choose_manifest)
        header.addWidget(self.manifest_button)
        self.shortcuts_button = qt.QPushButton("Keyboard shortcuts…")
        self.shortcuts_button.connect("clicked()", self.on_configure_shortcuts)
        header.addWidget(self.shortcuts_button)
        self.case_header = qt.QLabel("No case open")
        header.addWidget(self.case_header, 1)
        self.layout.addLayout(header)
        self.status_label = qt.QLabel("Open one prepared case to begin.")
        self.status_label.wordWrap = True
        self.layout.addWidget(self.status_label)

        # Two focus states, not one long scroll: picking a case and
        # annotating it are different tasks and never need to be on screen
        # at once. Choosing a case jumps here to "Annotation"; see
        # _focus_annotation_tab. Ported from the BratsReviewLite
        # Subjects/Annotation tab split.
        self.tab_widget = qt.QTabWidget()
        self.layout.addWidget(self.tab_widget, 1)

        self.cases_tab = qt.QWidget()
        cases_tab_layout = qt.QVBoxLayout(self.cases_tab)
        self.tab_widget.addTab(self.cases_tab, "Cases")

        self.annotation_tab = qt.QWidget()
        annotation_layout = qt.QVBoxLayout(self.annotation_tab)
        self.tab_widget.addTab(self.annotation_tab, "Annotation")

        cases_box = qt.QGroupBox("Cases")
        cases_layout = qt.QVBoxLayout(cases_box)
        self.progress_label = qt.QLabel("")
        cases_layout.addWidget(self.progress_label)

        filter_row = qt.QHBoxLayout()
        self.filter_buttons: dict[str, Any] = {}
        for title, key in CASE_FILTERS:
            button = qt.QToolButton()
            button.text = title
            button.checkable = True
            button.checked = key == DEFAULT_CASE_FILTER
            button.connect(
                "clicked()", lambda _checked=False, name=key: self.on_case_filter(name)
            )
            filter_row.addWidget(button)
            self.filter_buttons[key] = button
        filter_row.addStretch(1)
        cases_layout.addLayout(filter_row)

        self.case_search = qt.QLineEdit()
        self.case_search.placeholderText = "Jump to a case, for example 1 or sub-00001"
        self.case_search.connect("textChanged(QString)", self.on_case_search)
        cases_layout.addWidget(self.case_search)

        self.case_list = qt.QListWidget()
        self.case_list.alternatingRowColors = True
        self.case_list.minimumHeight = 200
        self.case_list.connect("itemClicked(QListWidgetItem*)", self.on_case_chosen)
        # The list is this tab's whole job now, so let it claim the room.
        cases_layout.addWidget(self.case_list, 1)
        cases_tab_layout.addWidget(cases_box, 1)

        self.view_tools = ReviewViewTools(self.logic, self.parent)
        # Lets an Alt-drag disarm the Segment Editor for the drag's duration,
        # so adjusting the contrast can never also paint.
        self.view_tools.alt_drag.editor_provider = lambda: self.segment_editor_widget
        # No on-screen contrast panel: ReviewViewTools no longer builds one
        # (Alt + left-drag in a slice view is the only way to adjust
        # window/level now, see AltDragWindowLevel), so there is nothing of
        # its to add to the layout here.
        history_row = qt.QHBoxLayout()
        self.undo_button = qt.QPushButton("Undo")
        self.undo_button.connect("clicked()", self.on_undo)
        self.redo_button = qt.QPushButton("Redo")
        self.redo_button.connect("clicked()", self.on_redo)
        history_row.addWidget(self.undo_button)
        history_row.addWidget(self.redo_button)
        annotation_layout.addLayout(history_row)
        self.reopen_button = qt.QPushButton("Reopen case for editing")
        self.reopen_button.toolTip = (
            "Return this completed case to AI correction. The published results are "
            "backed up, and the case must be completed again."
        )
        self.reopen_button.connect("clicked()", self.on_reopen_case)
        self.reopen_button.visible = False
        annotation_layout.addWidget(self.reopen_button)

        self.page_stack = qt.QStackedWidget()
        annotation_layout.addWidget(self.page_stack, 1)
        blind = qt.QWidget()
        blind_layout = qt.QVBoxLayout(blind)
        self.blind_stack = qt.QStackedWidget()
        blind_layout.addWidget(self.blind_stack, 1)

        # --- Stage 1: myoma boxing. The reader places every box on this
        # page first; FIGO typing only becomes reachable once they are done. ---
        boxing_page = qt.QWidget()
        boxing_layout = qt.QVBoxLayout(boxing_page)
        self.lesion_list = qt.QListWidget()
        self.lesion_list.connect("currentRowChanged(int)", self._on_lesion_row_changed)
        boxing_layout.addWidget(self.lesion_list, 1)
        actions = qt.QHBoxLayout()
        self.add_button = qt.QPushButton("Add myoma box")
        self.add_button.connect("clicked()", self.on_add_box)
        self.redraw_button = qt.QPushButton("Redraw")
        self.redraw_button.connect("clicked()", self.on_redraw_box)
        self.delete_button = qt.QPushButton("Delete")
        self.delete_button.connect("clicked()", self.on_delete_box)
        for button in (self.add_button, self.redraw_button, self.delete_button):
            actions.addWidget(button)
        boxing_layout.addLayout(actions)
        roi_transaction_actions = qt.QHBoxLayout()
        self.save_roi_button = qt.QPushButton("Save ROI")
        self.save_roi_button.enabled = False
        self.save_roi_button.connect("clicked()", self.save_roi_edit)
        self.cancel_roi_button = qt.QPushButton("Cancel ROI")
        self.cancel_roi_button.enabled = False
        self.cancel_roi_button.connect("clicked()", self.cancel_roi_edit)
        roi_transaction_actions.addWidget(self.save_roi_button)
        roi_transaction_actions.addWidget(self.cancel_roi_button)
        boxing_layout.addLayout(roi_transaction_actions)
        self.zero_check = qt.QCheckBox("Confirm zero myomas")
        self.zero_check.connect("toggled(bool)", self.on_zero_toggled)
        boxing_layout.addWidget(self.zero_check)
        # Early stops answer "is there anything to box at all", so they sit
        # with the zero-myoma confirmation rather than with FIGO typing.
        stops = qt.QHBoxLayout()
        self.early_stop_buttons = []
        for text_value, status in (("No uterus", "no_uterus"), ("Wrong image", "wrong_image"), ("Cannot assess", "cannot_assess")):
            button = qt.QPushButton(text_value)
            button.connect("clicked()", lambda _checked=False, value=status: self.on_early_stop(value))
            stops.addWidget(button)
            self.early_stop_buttons.append(button)
        boxing_layout.addLayout(stops)
        self.continue_to_figo_button = qt.QPushButton("Done boxing → continue to FIGO typing")
        self.continue_to_figo_button.connect("clicked()", self.on_continue_to_figo)
        boxing_layout.addWidget(self.continue_to_figo_button)
        self.blind_stack.addWidget(boxing_page)

        # --- Stage 2: FIGO typing. Reachable only after boxing is done; the
        # FIGO/type values are chosen from fixed options, never free-typed. ---
        figo_page = qt.QWidget()
        figo_page_layout = qt.QVBoxLayout(figo_page)
        self.back_to_boxing_button = qt.QPushButton("← Back to myoma boxes")
        self.back_to_boxing_button.connect("clicked()", self.on_back_to_boxing)
        figo_page_layout.addWidget(self.back_to_boxing_button)
        figo_form = qt.QFormLayout()
        self.figo_lesion_combo = qt.QComboBox()
        self.figo_lesion_combo.connect("currentIndexChanged(int)", self._on_figo_lesion_changed)
        figo_form.addRow("Selected lesion", self.figo_lesion_combo)
        self.figo_kind_combo = qt.QComboBox()
        for kind in FIGOKind:
            self.figo_kind_combo.addItem(kind.value, kind.value)
        self.figo_kind_combo.connect("currentIndexChanged(int)", self._refresh_figo_controls)
        figo_form.addRow("FIGO kind", self.figo_kind_combo)
        self.figo_type_combo = qt.QComboBox()
        for type_value in range(9):
            self.figo_type_combo.addItem(str(type_value), type_value)
        self.figo_type_combo.connect("currentIndexChanged(int)", self._refresh_figo_controls)
        figo_form.addRow("Standard type", self.figo_type_combo)
        self.figo_location_combo = qt.QComboBox()
        for location in ("cervical", "round_ligament", "broad_ligament", "parasitic_or_other_extrauterine"):
            self.figo_location_combo.addItem(location, location)
        figo_form.addRow("Type 8 location", self.figo_location_combo)
        self.figo_endometrial_combo = qt.QComboBox()
        for type_value in range(9):
            self.figo_endometrial_combo.addItem(str(type_value), type_value)
        figo_form.addRow("Hybrid endometrial", self.figo_endometrial_combo)
        self.figo_serosal_combo = qt.QComboBox()
        for type_value in range(9):
            self.figo_serosal_combo.addItem(str(type_value), type_value)
        figo_form.addRow("Hybrid serosal", self.figo_serosal_combo)
        self.figo_reason_combo = qt.QComboBox()
        for reason in ("endometrial_interface_unclear", "serosal_interface_unclear", "both_interfaces_unclear", "otherwise_not_classifiable"):
            self.figo_reason_combo.addItem(reason, reason)
        figo_form.addRow("Uncertain reason", self.figo_reason_combo)
        self.ddx_edit = qt.QLineEdit()
        self.ddx_edit.maxLength = 4000
        self.ddx_edit.placeholderText = "Reviewer differential diagnosis / notes"
        figo_form.addRow("Myoma Ddx", self.ddx_edit)
        self.apply_figo_button = qt.QPushButton("Save FIGO and Ddx")
        self.apply_figo_button.connect("clicked()", self.on_apply_figo)
        figo_form.addRow("", self.apply_figo_button)
        figo_page_layout.addLayout(figo_form)

        lower = qt.QHBoxLayout()
        self.save_draft_button = qt.QPushButton("Save Draft")
        self.save_draft_button.connect("clicked()", self.on_save_draft)
        self.reveal_button = qt.QPushButton("Reveal AI")
        self.reveal_button.connect("clicked()", self.on_reveal_ai)
        self.return_correction_button = qt.QPushButton("Return to segmentation")
        self.return_correction_button.connect("clicked()", lambda: self._show_page(1))
        self.return_correction_button.visible = False
        lower.addWidget(self.save_draft_button)
        lower.addWidget(self.reveal_button)
        lower.addWidget(self.return_correction_button)
        figo_page_layout.addLayout(lower)
        self.blind_stack.addWidget(figo_page)
        self.page_stack.addWidget(blind)

        correction = qt.QWidget()
        correction_layout = qt.QVBoxLayout(correction)
        correction_actions = qt.QHBoxLayout()
        self.edit_annotations_button = qt.QPushButton("Myomas / FIGO / Ddx")
        self.edit_annotations_button.connect("clicked()", lambda: self._show_page(0))
        correction_actions.addWidget(self.edit_annotations_button)
        self.correction_save_button = qt.QPushButton("Save Draft")
        self.correction_save_button.connect("clicked()", self.on_save_draft)
        correction_actions.addWidget(self.correction_save_button)
        correction_layout.addLayout(correction_actions)
        self.show_roi_check = qt.QCheckBox("Show ROI boxes")
        self.show_roi_check.checked = False
        self.show_roi_check.connect("toggled(bool)", self.on_show_roi_toggled)
        correction_layout.addWidget(self.show_roi_check)
        self.correction_tools = ReviewCorrectionTools(self)
        correction_layout.addWidget(self.correction_tools.widget)
        self.segment_editor_host = qt.QWidget()
        self.segment_editor_host_layout = qt.QVBoxLayout(self.segment_editor_host)
        correction_layout.addWidget(self.segment_editor_host, 1)
        self.complete_button = qt.QPushButton("Export corrected labels and complete")
        self.complete_button.connect("clicked()", self.on_complete)
        correction_layout.addWidget(self.complete_button)
        self.page_stack.addWidget(correction)
        self._refresh_figo_controls()

    def _focus_annotation_tab(self) -> None:
        """Switch to the Annotation tab once a case is loaded and ready.

        Called after every successful case load (case picked from the list,
        case manifest chosen, or launched via the case env var) so the
        reviewer always lands on the review screen, not the browser.
        """
        if hasattr(self, "tab_widget") and hasattr(self, "annotation_tab"):
            self.tab_widget.setCurrentWidget(self.annotation_tab)

    def enter(self) -> None:
        for shortcut in self.shortcuts:
            shortcut.enabled = True
        if self.mouse_button_dispatcher is not None:
            self.mouse_button_dispatcher.enabled = True
        if self.view_tools is not None:
            self.view_tools.refresh()
        if self.click_selector is not None:
            # Not gated on existing boxes: the reader may draw the first one
            # after coming back, and nothing else re-binds the observers.
            self.click_selector.bind_views()

    def exit(self) -> None:
        for shortcut in self.shortcuts:
            shortcut.enabled = False
        if self.mouse_button_dispatcher is not None:
            self.mouse_button_dispatcher.enabled = False
        if self.segment_editor_widget is not None:
            self.segment_editor_widget.setActiveEffectByName("")
        if self.draw_tool is not None:
            self.draw_tool.cancel()
        if self.roi_nodes:
            self.set_active_roi(None)
        # set_active_roi(None) no-ops when nothing was selected, which would
        # otherwise leave a cancelled drag's cursor/hidden boxes stuck; make
        # the reset explicit and unconditional here instead.
        self._apply_roi_selection()
        if self.click_selector is not None:
            self.click_selector.unbind_views()

    def _show_page(self, index: int) -> None:
        if self._has_unresolved_roi():
            self._show_error("Save or cancel the current myoma box first.")
            return
        if self.segment_editor_widget is not None:
            self.segment_editor_widget.setActiveEffectByName("")
        self.page_stack.setCurrentIndex(index)
        self._set_roi_visibility(index == 0 or bool(self.show_roi_check.checked))

    def _remember_annotation(self) -> None:
        if self.logic.state is not None:
            self.annotation_undo.append(self.logic.state.to_draft())
            self.annotation_undo = self.annotation_undo[-30:]
            self.annotation_redo.clear()

    def _restore_annotation(self, undo: bool) -> None:
        if self.logic.state is None or self.logic.read_only:
            return
        if self._has_unresolved_roi():
            self.cancel_roi_edit()
            if self.draw_tool is not None:
                self.draw_tool.cancel()
            # draw_tool.cancel() above can itself flip _has_unresolved_roi()
            # from true to false (an aborted drag with no draft box yet), so
            # the cursor/hidden-boxes reset has to run again here.
            self._apply_roi_selection()
            return
        # Give the drag its own undo entry, or this keystroke would throw it
        # away and revert whatever the reader did before it instead.
        self._flush_pending_roi_drag()
        source, target = (self.annotation_undo, self.annotation_redo) if undo else (self.annotation_redo, self.annotation_undo)
        if not source:
            return
        previous = source[-1]
        if previous["phase"] != self.logic.state.phase:
            return
        target.append(self.logic.state.to_draft())
        next_number = self.logic.state.next_lesion_number
        self.logic.state = ReviewState.from_draft(source.pop())
        self.logic.state.next_lesion_number = max(next_number, self.logic.state.next_lesion_number)
        self.rebuild_roi_nodes()
        self._write_current_draft()
        self.refresh_ui()

    def on_undo(self) -> None:
        if self.logic.read_only:
            return
        if self.page_stack.currentIndex == 1 and self.segment_editor_widget is not None:
            self.segment_editor_widget.undo()
        else:
            self._restore_annotation(True)

    def on_redo(self) -> None:
        if self.logic.read_only:
            return
        if self.page_stack.currentIndex == 1 and self.segment_editor_widget is not None:
            self.segment_editor_widget.redo()
        else:
            self._restore_annotation(False)

    def choose_manifest(self) -> None:
        path = qt.QFileDialog.getOpenFileName(slicer.util.mainWindow(), "Open MyomaReviewLite case manifest", "", "JSON (*.json)")
        if path:
            self.open_case(path)

    def _refresh_case_list(self) -> None:
        """Rebuild the case list under the active filter and search text."""
        if not hasattr(self, "case_list"):
            return
        summary = self.logic.queue_summary
        allowed = CASE_FILTER_STATUSES.get(self._case_filter, CASE_FILTER_STATUSES["all"])
        needle = str(self.case_search.text) if hasattr(self, "case_search") else ""
        current_row = -1
        self.case_list.blockSignals(True)
        try:
            self.case_list.clear()
            for case_id in self.logic.available_case_ids:
                entry = None if summary is None else summary.entry(case_id)
                status = None if entry is None else entry.status
                if status is not None and status not in allowed:
                    continue
                if not self._matches_case_search(case_id, needle):
                    continue
                title = "" if status is None else CASE_STATUS_TITLES[status]
                item = qt.QListWidgetItem(f"{case_id}    {title}" if title else case_id)
                item.setData(qt.Qt.UserRole, case_id)
                if entry is not None and entry.diagnostic:
                    item.setToolTip(entry.diagnostic)
                if status is not None:
                    # A PythonQt colour quirk must never cost us the list.
                    try:
                        item.setForeground(qt.QBrush(qt.QColor(CASE_STATUS_COLORS[status])))
                    except Exception:
                        traceback.print_exc()
                self.case_list.addItem(item)
                if case_id == self.logic.case_id:
                    current_row = self.case_list.count - 1
            if current_row >= 0:
                self.case_list.setCurrentRow(current_row)
                self.case_list.scrollToItem(self.case_list.item(current_row))
        finally:
            self.case_list.blockSignals(False)
        self._refresh_progress()

    @staticmethod
    def _matches_case_search(case_id: str, needle: str) -> bool:
        """Match on the ID, or on the case number however it is typed."""
        needle = (needle or "").strip().lower()
        if not needle:
            return True
        if needle in case_id.lower():
            return True
        digits = "".join(character for character in needle if character.isdigit())
        tail = "".join(character for character in case_id if character.isdigit())
        if not digits or not tail:
            return False
        try:
            return int(digits) == int(tail)
        except ValueError:
            return False

    def on_case_filter(self, name: str) -> None:
        self._case_filter = name if name in CASE_FILTER_STATUSES else "all"
        for key, button in self.filter_buttons.items():
            button.checked = key == self._case_filter
        self._refresh_case_list()

    def on_case_search(self, _text: str = "") -> None:
        self._refresh_case_list()

    def _refresh_progress(self) -> None:
        if not hasattr(self, "progress_label"):
            return
        summary = self.logic.queue_summary
        if summary is None:
            self.progress_label.text = ""
            return
        counts = summary.counts
        self.progress_label.text = (
            f"Complete {counts[QueueStatus.COMPLETE]} Â· "
            f"In progress {counts[QueueStatus.IN_PROGRESS]} Â· "
            f"New {counts[QueueStatus.NEW]} Â· "
            f"Attention {counts[QueueStatus.ATTENTION]}"
        )

    def _no_reviewable_message(self) -> str:
        summary = self.logic.queue_summary
        if summary is None:
            return "No unfinished case remains."
        attention = summary.counts[QueueStatus.ATTENTION]
        total = len(summary.entries)
        complete = summary.counts[QueueStatus.COMPLETE]
        if attention:
            return f"No reviewable cases remain; {attention} case(s) require Attention."
        return f"{complete}/{total} complete"

    def on_case_chosen(self, item: Any) -> None:
        if item is None or self.logic.manifest_source is None:
            return
        selected_case_id = str(item.data(qt.Qt.UserRole))
        # Re-opening the case already on screen would only cost a reload.
        if not selected_case_id or selected_case_id == self.logic.case_id:
            return
        summary = self.logic.refresh_queue()
        if summary is not None:
            entry = summary.entry(selected_case_id)
            if entry.status is QueueStatus.ATTENTION:
                self._show_error(
                    f"{selected_case_id} requires Attention: "
                    f"{entry.diagnostic or 'invalid artifacts'}"
                )
                self._refresh_case_list()
                return
        self.open_case(self.logic.manifest_source, selected_case_id)

    def open_case(self, manifest_path: str | Path, case_id: str | None = None) -> None:
        source_path = Path(manifest_path).expanduser().resolve()
        try:
            self._flush_pending_roi_drag()
            has_current = self.logic.manifest is not None
            changing_case = has_current and (
                self.logic.manifest_source != source_path
                or case_id is None
                or case_id != self.logic.case_id
            )
            if changing_case:
                self.save_coordinator.transition(
                    unresolved_roi=self._has_unresolved_roi(),
                    save_current=self._write_current_draft,
                    teardown_current=self._teardown_current_case,
                    open_target=lambda: self._load_case(source_path, case_id),
                )
            else:
                self._load_case(source_path, case_id)
        except Exception as exc:
            traceback.print_exc()
            self._show_error(f"Case could not be opened: {exc}")
            self._refresh_case_list()

    def _load_case(self, manifest_path: Path, case_id: str | None) -> None:
        self.logic.open_manifest(manifest_path, case_id=case_id)
        self._boxing_stage_done = self.logic.phase != ReviewPhase.BLIND.value
        self.shutdown_saved = False
        self.save_coordinator.clear_dirty()
        self._refresh_case_list()
        self.case_header.text = f"Reader {self.logic.manifest.reader_id} Â· Case {self.logic.case_id}"  # type: ignore[union-attr]
        self.logic.load_registered_volumes()
        self.view_tools.refresh()
        self.roi_boxes_visible = True
        self.rebuild_roi_nodes()
        if self.click_selector is not None:
            self.click_selector.bind_views()
        self.page_stack.setCurrentIndex(0)
        if self.logic.read_only:
            if (self.logic.final_annotation or {}).get("status") == "complete":
                self.logic.load_completed_segmentation()
                self._open_correction_page()
            self.refresh_ui()
            if (
                self.logic.queue_summary is not None
                and self.logic.queue_summary.first_unfinished() is None
            ):
                self._set_status(self._no_reviewable_message())
            self._focus_annotation_tab()
            return
        if self.logic.phase == ReviewPhase.AI_REVEALED.value:
            self.logic.load_ai_mask()
            self._open_correction_page()
        self.refresh_ui()
        self._focus_annotation_tab()

    def refresh_ui(self) -> None:
        state = self.logic.state
        read_only = self.logic.read_only
        if self.segment_editor_widget is not None:
            self.segment_editor_widget.setReadOnly(read_only)
            if read_only:
                self.segment_editor_widget.setActiveEffectByName("")
        self.undo_button.enabled = not read_only and state is not None
        self.redo_button.enabled = not read_only and state is not None
        self.correction_save_button.enabled = not read_only
        self.ddx_edit.enabled = not read_only
        self.correction_tools.refresh()
        self.reopen_button.visible = bool(
            read_only and (self.logic.final_annotation or {}).get("status") == "complete"
        )
        if state is None:
            if not self.logic.read_only or self.logic.final_annotation is None:
                return
            self.lesion_list.clear()
            self.figo_lesion_combo.blockSignals(True)
            self.figo_lesion_combo.clear()
            for lesion in self.logic.final_annotation.get("lesions", []):
                lesion_id = str(lesion.get("lesion_id", ""))
                item = qt.QListWidgetItem(f"{lesion_id} Â· final")
                item.setData(qt.Qt.UserRole, lesion_id)
                self.lesion_list.addItem(item)
                self.figo_lesion_combo.addItem(lesion_id, lesion_id)
            self.figo_lesion_combo.blockSignals(False)
            if self.lesion_list.count:
                self.lesion_list.setCurrentRow(0)
            for control in (
                self.add_button,
                self.redraw_button,
                self.delete_button,
                self.zero_check,
                self.figo_lesion_combo,
                self.figo_kind_combo,
                self.apply_figo_button,
                self.reveal_button,
                self.save_draft_button,
                self.complete_button,
                *self.early_stop_buttons,
            ):
                control.enabled = False
            self.figo_lesion_combo.enabled = True
            self.back_to_boxing_button.enabled = False
            self.continue_to_figo_button.enabled = False
            self.blind_stack.setCurrentIndex(1)
            self.return_correction_button.visible = self.logic.segmentation_node is not None
            self._load_selected_annotation()
            self._refresh_roi_arming()
            status = str(self.logic.final_annotation.get("status", "complete")).replace("_", " ")
            self._set_status(f"Final outcome: {status}. This record is read-only.")
            self._refresh_progress()
            return
        self.zero_check.blockSignals(True)
        self.zero_check.checked = state.zero_myomas
        self.zero_check.enabled = state.phase == ReviewPhase.BLIND.value and not state.lesions
        self.zero_check.blockSignals(False)
        selected_id = self._selected_lesion_id()
        self.lesion_list.blockSignals(True)
        self.figo_lesion_combo.blockSignals(True)
        self.lesion_list.clear()
        self.figo_lesion_combo.clear()
        for lesion in state.active_lesions:
            label = lesion.lesion_id + (" Â· FIGO pending" if lesion.figo is None else " Â· FIGO set")
            item = qt.QListWidgetItem(label)
            item.setData(qt.Qt.UserRole, lesion.lesion_id)
            self.lesion_list.addItem(item)
            self.figo_lesion_combo.addItem(label, lesion.lesion_id)
        selected_index = self.figo_lesion_combo.findData(selected_id) if selected_id else 0
        if self.figo_lesion_combo.count:
            selected_index = max(0, selected_index)
            self.figo_lesion_combo.setCurrentIndex(selected_index)
            self.lesion_list.setCurrentRow(selected_index)
        self.lesion_list.blockSignals(False)
        self.figo_lesion_combo.blockSignals(False)
        locked = read_only or state.phase not in (ReviewPhase.BLIND.value, ReviewPhase.AI_REVEALED.value)
        # Boxing and FIGO typing are separate stages of the annotation page.
        # A revealed case opens on FIGO, but the reader may go back to the
        # boxes to add, redraw or delete one; the blinded snapshot is frozen.
        in_figo_stage = self._boxing_stage_done
        self.blind_stack.setCurrentIndex(1 if in_figo_stage else 0)
        for control in (self.add_button, self.redraw_button, self.delete_button, self.zero_check):
            control.enabled = not locked and not in_figo_stage
        for control in (self.figo_lesion_combo, self.figo_kind_combo, self.apply_figo_button):
            control.enabled = not locked
        self.zero_check.enabled = not locked and not in_figo_stage and not state.lesions
        self.continue_to_figo_button.enabled = not locked and not in_figo_stage and bool(state.zero_myomas or state.lesions)
        self.back_to_boxing_button.enabled = not locked and in_figo_stage
        self.reveal_button.enabled = bool(state.can_reveal)
        self.reveal_button.visible = state.phase == ReviewPhase.BLIND.value
        self.return_correction_button.visible = state.phase != ReviewPhase.BLIND.value
        self.save_draft_button.enabled = not self.logic.read_only
        self.complete_button.enabled = state.phase == ReviewPhase.AI_REVEALED.value
        for button in self.early_stop_buttons:
            button.enabled = not locked and not in_figo_stage and state.phase == ReviewPhase.BLIND.value
        self._load_selected_annotation()
        self._refresh_roi_arming()
        self._set_status(f"Phase: {state.phase} Â· {state.lesion_count} myomas (no count limit)")
        self._refresh_progress()

    def _refresh_roi_arming(self) -> None:
        if self.roi_nodes:
            self._apply_roi_selection()

    def _set_status(self, text_value: str) -> None:
        if hasattr(self, "status_label"):
            self.status_label.text = text_value

    def _show_error(self, message: str) -> None:
        self._set_status(message)
        if SLICER_AVAILABLE:
            qt.QMessageBox.warning(slicer.util.mainWindow(), "MyomaReviewLite", message)

    def _selected_lesion_id(self) -> str | None:
        item = self.lesion_list.currentItem()
        return str(item.data(qt.Qt.UserRole)) if item is not None else None

    def _on_lesion_row_changed(self, index: int) -> None:
        if not hasattr(self, "figo_lesion_combo"):
            return
        self.figo_lesion_combo.blockSignals(True)
        self.figo_lesion_combo.setCurrentIndex(index)
        self.figo_lesion_combo.blockSignals(False)
        self._load_selected_annotation()
        # Picking a myoma from the list changes the selected box with it. Not
        # while a box is being placed (every other box is hidden then), and not
        # for a completed case's read-only list, which is filled on load and
        # must not pull the views anywhere.
        if self.logic.state is not None and not self._has_unresolved_roi():
            self._reveal_lesion(self._selected_lesion_id())

    def _on_figo_lesion_changed(self, index: int) -> None:
        self.lesion_list.blockSignals(True)
        self.lesion_list.setCurrentRow(index)
        self.lesion_list.blockSignals(False)
        self._load_selected_annotation()
        # Picking a myoma while typing FIGO shows its box. A box picked by
        # clicking it in a view is already revealed (on_view_clicked reveals
        # first, then syncs this combo), so the views stay put in that case.
        # In the boxing stage the combo is only synced, never chosen by hand,
        # so nothing is revealed there.
        if self.blind_stack.currentIndex == 1:
            lesion_id = self._selected_lesion_id()
            self._reveal_lesion(lesion_id, navigate=lesion_id != self.active_roi_id)

    def _reveal_lesion(self, lesion_id: str | None, navigate: bool = True) -> None:
        """Show one myoma's box: reveal it, and optionally bring it on screen.

        A box is only visible on the slices it crosses, so with ``navigate``
        the three T2 views are centred on the box; otherwise choosing a myoma
        from the FIGO list could leave its box off the current slice.

        While typing FIGO the list selection already is the shown box (see
        ``_figo_focus_active``), so it is redrawn instead of being put in hand.
        """

        if not lesion_id or lesion_id not in self.roi_nodes:
            return
        if self._figo_focus_active():
            self._apply_roi_selection()
        else:
            self.set_active_roi(lesion_id)
        if not navigate:
            return
        node = self.roi_nodes[lesion_id]
        if node.GetScene() is not slicer.mrmlScene:
            return
        center = [0.0, 0.0, 0.0]
        node.GetCenterWorld(center)
        manager = slicer.app.layoutManager()
        for view_name in VIEW_NAMES.values():
            view = manager.sliceWidget(view_name)
            if view is not None:
                view.mrmlSliceNode().JumpSliceByCentering(*center)

    def _load_selected_annotation(self) -> None:
        if not hasattr(self, "ddx_edit"):
            return
        lesion_id = self._selected_lesion_id()
        state = self.logic.state
        lesion = state.lesions.get(lesion_id) if state is not None else None
        payload = lesion.to_dict() if lesion is not None else next(
            (item for item in (self.logic.final_annotation or {}).get("lesions", []) if item.get("lesion_id") == lesion_id), {}
        )
        figo = payload.get("figo") or {"kind": "standard", "type": 0}
        self.figo_kind_combo.setCurrentIndex(max(0, self.figo_kind_combo.findData(figo["kind"])))
        self.figo_type_combo.setCurrentIndex(max(0, self.figo_type_combo.findData(figo.get("type", 0))))
        self.figo_location_combo.setCurrentIndex(max(0, self.figo_location_combo.findData(figo.get("location"))))
        self.figo_endometrial_combo.setCurrentIndex(max(0, self.figo_endometrial_combo.findData(figo.get("endometrial", 0))))
        self.figo_serosal_combo.setCurrentIndex(max(0, self.figo_serosal_combo.findData(figo.get("serosal", 0))))
        self.figo_reason_combo.setCurrentIndex(max(0, self.figo_reason_combo.findData(figo.get("reason"))))
        self.ddx_edit.text = payload.get("differential_diagnosis", "")
        for control in (self.figo_kind_combo, self.figo_type_combo, self.figo_location_combo,
                        self.figo_endometrial_combo, self.figo_serosal_combo, self.figo_reason_combo,
                        self.ddx_edit, self.apply_figo_button):
            control.enabled = bool(payload) and not self.logic.read_only
        self._refresh_figo_controls()

    def on_add_box(self) -> None:
        if self._has_unresolved_roi():
            self._show_error("Save or cancel the current myoma box first.")
            return
        # An armed box would follow the drawing drag too, so disarm it first.
        self.set_active_roi(None)
        if self.draw_tool is not None:
            self.draw_tool.start()
            # Hide every other box and swap in the placement cursor now that
            # draw_tool.active is true, i.e. _has_unresolved_roi() flipped.
            self._apply_roi_selection()

    def on_redraw_box(self) -> None:
        if self._has_unresolved_roi():
            self._show_error("Save or cancel the current myoma box first.")
            return
        lesion_id = self._selected_lesion_id()
        self.set_active_roi(None)
        if lesion_id and self.draw_tool is not None:
            self.draw_tool.start(lesion_id)
            self._apply_roi_selection()

    def on_delete_box(self) -> None:
        lesion_id = self._selected_lesion_id()
        if not lesion_id or self.logic.state is None:
            return
        try:
            self._remember_annotation()
            self.logic.state.delete_lesion(lesion_id)
            if self.active_roi_id == lesion_id:
                self.active_roi_id = None
                self.active_roi_geometry = None
            node = self.roi_nodes.pop(lesion_id, None)
            if node is not None:
                if self.roi_overlay is not None:
                    self.roi_overlay.untrack(node)
                slicer.mrmlScene.RemoveNode(node)
            self._remove_roi_outline(lesion_id)
            self._write_current_draft()
            self.refresh_ui()
        except Exception as exc:
            self._show_error(str(exc))

    def on_continue_to_figo(self) -> None:
        if self._has_unresolved_roi():
            self._show_error("Save or cancel the current myoma box first.")
            return
        state = self.logic.state
        if state is None or not (state.zero_myomas or state.lesions):
            self._show_error("Add at least one myoma box, or confirm zero myomas, before continuing.")
            return
        self._boxing_stage_done = True
        # FIGO typing pages through the myomas in order, so it opens on the
        # first one rather than on whichever box was drawn or picked last.
        if self.lesion_list.count:
            # refresh_ui() syncs the combo and form from the list, and the FIGO
            # stage is not up yet, so keep the row handler from arming a box.
            self.lesion_list.blockSignals(True)
            self.lesion_list.setCurrentRow(0)
            self.lesion_list.blockSignals(False)
        self.refresh_ui()
        if state.lesions:
            self._reveal_lesion(self._selected_lesion_id())

    def on_back_to_boxing(self) -> None:
        if self._has_unresolved_roi():
            self._show_error("Save or cancel the current myoma box first.")
            return
        self._boxing_stage_done = False
        self.refresh_ui()

    def on_zero_toggled(self, checked: bool) -> None:
        if self.logic.state is None:
            return
        try:
            self._remember_annotation()
            self.logic.state.confirm_zero_myomas(bool(checked))
            self._write_current_draft()
            self.refresh_ui()
        except Exception as exc:
            self.zero_check.blockSignals(True)
            self.zero_check.checked = False
            self.zero_check.blockSignals(False)
            self._show_error(str(exc))

    def _refresh_figo_controls(self, _index: int = 0) -> None:
        if not hasattr(self, "figo_kind_combo"):
            return
        kind = str(self.figo_kind_combo.itemData(self.figo_kind_combo.currentIndex))
        standard_type = self.figo_type_combo.itemData(self.figo_type_combo.currentIndex)
        self.figo_type_combo.visible = kind == FIGOKind.STANDARD.value
        self.figo_location_combo.visible = kind == FIGOKind.STANDARD.value and standard_type == 8
        self.figo_endometrial_combo.visible = kind == FIGOKind.HYBRID.value
        self.figo_serosal_combo.visible = kind == FIGOKind.HYBRID.value
        self.figo_reason_combo.visible = kind == FIGOKind.UNCERTAIN.value

    def on_apply_figo(self) -> None:
        state = self.logic.state
        lesion_id = self._selected_lesion_id()
        if state is None:
            return
        if lesion_id is None and self.figo_lesion_combo.currentIndex >= 0:
            lesion_id = str(self.figo_lesion_combo.itemData(self.figo_lesion_combo.currentIndex))
        if not lesion_id:
            return
        kind = str(self.figo_kind_combo.itemData(self.figo_kind_combo.currentIndex))
        try:
            if kind == FIGOKind.STANDARD.value:
                standard_type = int(self.figo_type_combo.itemData(self.figo_type_combo.currentIndex))
                value: dict[str, Any] = {"kind": kind, "type": standard_type}
                if standard_type == 8:
                    value["location"] = str(self.figo_location_combo.itemData(self.figo_location_combo.currentIndex))
            elif kind == FIGOKind.HYBRID.value:
                value = {
                    "kind": kind,
                    "endometrial": int(self.figo_endometrial_combo.itemData(self.figo_endometrial_combo.currentIndex)),
                    "serosal": int(self.figo_serosal_combo.itemData(self.figo_serosal_combo.currentIndex)),
                }
            else:
                value = {"kind": kind, "reason": str(self.figo_reason_combo.itemData(self.figo_reason_combo.currentIndex))}
            FIGOClassification.from_dict(value)
            self._remember_annotation()
            state.set_figo(lesion_id, value)
            state.set_differential_diagnosis(lesion_id, str(self.ddx_edit.text))
            self._write_current_draft()
            # Saving FIGO advances straight to the next myoma so the reader
            # can page through the whole list without reselecting each one;
            # saving the last myoma instead offers to move on to AI reveal.
            ordered_ids = [lesion.lesion_id for lesion in state.active_lesions]
            position = ordered_ids.index(lesion_id) if lesion_id in ordered_ids else -1
            if 0 <= position < len(ordered_ids) - 1:
                self.lesion_list.setCurrentRow(position + 1)
            self.refresh_ui()
            if position == len(ordered_ids) - 1 and self.logic.phase == ReviewPhase.BLIND.value:
                self._offer_ai_reveal_after_last_figo()
        except Exception as exc:
            self._show_error(str(exc))

    def _offer_ai_reveal_after_last_figo(self) -> None:
        answer = qt.QMessageBox.question(
            slicer.util.mainWindow(),
            "FIGO saved",
            "FIGO and Ddx saved for the last myoma. Reveal AI now?",
        )
        if answer == qt.QMessageBox.Yes:
            self.on_reveal_ai()

    def on_save_draft(self) -> None:
        try:
            self._write_current_draft()
            self.save_coordinator.clear_dirty()
            self._set_status("Draft saved.")
        except Exception as exc:
            self._show_error(f"Draft could not be saved: {exc}")

    @staticmethod
    def _state_is_untouched(state: Any) -> bool:
        # A freshly opened case starts life as an in-memory ReviewState.new()
        # that nothing has acted on yet: BLIND phase, no zero-myomas
        # confirmation, no boxes. Persisting that to disk as draft.json would
        # make classify_case() (queue.py) report the case as "in progress"
        # even though the reader never touched it, just because they opened
        # it and then moved on (case switch, or Slicer shutdown). Only write
        # once the state actually diverges from that pristine snapshot.
        return (
            state.phase == ReviewPhase.BLIND.value
            and not state.zero_myomas
            and not state.lesions
            and state.blinded_snapshot is None
        )

    def _write_current_draft(self) -> None:
        # A box dragged by its handles lives only in the node until this runs.
        self._commit_active_roi_geometry()
        if self.logic.state is None or self.logic.read_only:
            return
        if self._state_is_untouched(self.logic.state):
            return
        segmentation_source: Path | None = None
        try:
            segmentation_source = self._save_segmentation_snapshot()
            self.logic.save_draft(segmentation_source)
        finally:
            if segmentation_source is not None:
                try:
                    segmentation_source.unlink()
                except OSError:
                    pass

    def _flush_pending_roi_drag(self) -> None:
        """Fold a box dragged by its handles into the state before it is read.

        The blinded snapshot, the published annotation and the undo stack are
        all built from ``logic.state``, so a drag that still lives only in the
        markups node has to land there first or it is silently dropped.
        """

        self._commit_active_roi_geometry()

    def _has_unresolved_roi(self) -> bool:
        return bool(
            self.roi_draft_node is not None
            or self.roi_transaction.active
            or (self.draw_tool is not None and self.draw_tool.active)
        )

    def on_autosave_timeout(self) -> None:
        if (
            self.logic.state is None
            or self.logic.read_only
            or self.logic.phase != ReviewPhase.AI_REVEALED.value
        ):
            return
        try:
            if self.save_coordinator.autosave(self._write_current_draft):
                self._set_status("Correction draft autosaved.")
        except Exception as exc:
            self._show_error(f"Correction autosave failed; it will retry: {exc}")

    def _on_segmentation_modified(self, *_args: Any) -> None:
        if self.logic.phase == ReviewPhase.AI_REVEALED.value and not self.logic.read_only:
            self.save_coordinator.mark_dirty()

    def _observe_segmentation(self) -> None:
        self._remove_segmentation_observer()
        if self.logic.segmentation_node is None:
            return
        observed = self.logic.segmentation_node.GetSegmentation()
        tag = observed.AddObserver(
            slicer.vtkSegmentation.SegmentModified,
            self._on_segmentation_modified,
        )
        self.segmentation_observer = (observed, tag)
        self.save_coordinator.clear_dirty()

    def _remove_segmentation_observer(self) -> None:
        if self.segmentation_observer is None:
            return
        observed, tag = self.segmentation_observer
        try:
            observed.RemoveObserver(tag)
        except Exception:
            pass
        self.segmentation_observer = None

    def _teardown_current_case(self) -> None:
        self.annotation_undo.clear()
        self.annotation_redo.clear()
        if self.correction_tools is not None:
            self.correction_tools.cleanup()
        self._remove_segmentation_observer()
        if self.segment_editor_widget is not None:
            editor = self.segment_editor_widget
            for method_name in (
                "setSegmentationNode",
                "setSourceVolumeNode",
                "setMRMLSegmentEditorNode",
            ):
                try:
                    getattr(editor, method_name)(None)
                except Exception:
                    pass
            try:
                editor.setMRMLScene(None)
            except Exception:
                pass
            try:
                self.segment_editor_host_layout.removeWidget(editor)
            except Exception:
                pass
            try:
                editor.close()
                editor.deleteLater()
            except Exception:
                pass
            self.segment_editor_widget = None
        if self.draw_tool is not None:
            self.draw_tool.cancel()
        # The case's own boxes are about to be torn down below, but the
        # cursor belongs to the slice views, which persist across cases, so
        # it needs an explicit reset here too.
        self._update_box_edit_cursor(False)
        if self.click_selector is not None:
            self.click_selector.unbind_views()
        if self.roi_overlay is not None:
            self.roi_overlay.clear()
        self.active_roi_id = None
        self.active_roi_geometry = None
        self.roi_click_baseline = None
        self._stop_live_outline_sync()
        self._remove_draft_outline()
        self._clear_roi_outlines()
        self.roi_outline_sources.clear()
        for node in list(self.roi_nodes.values()) + [self.roi_draft_node]:
            if node is not None and node.GetScene() is slicer.mrmlScene:
                slicer.mrmlScene.RemoveNode(node)
        self.roi_nodes = {}
        self.roi_boxes_visible = True
        self.roi_draft_node = None
        self.roi_draft_update_id = None
        self.roi_transaction.cancel()
        self.logic._clear_loaded_nodes()
        if hasattr(self, "page_stack"):
            self.page_stack.setCurrentIndex(0)

    def _save_segmentation_snapshot(self) -> Path | None:
        if self.logic.state is None or self.logic.state.phase != ReviewPhase.AI_REVEALED.value or self.logic.segmentation_node is None:
            return None
        if self.logic.storage is None:
            return None
        self.logic.storage.case_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.logic.storage.case_directory / ".draft.seg.nrrd.tmp.seg.nrrd"
        if not slicer.util.saveNode(self.logic.segmentation_node, str(temporary)):
            raise RuntimeError("draft segmentation could not be written")
        return temporary

    def on_reveal_ai(self) -> None:
        try:
            if self._has_unresolved_roi():
                raise ValidationError("Save or cancel the current myoma box before revealing AI.")
            # reveal() freezes the blinded record from state.lesions, so a
            # dragged box must be committed while the phase is still blind.
            self._flush_pending_roi_drag()
            self.logic.reveal_ai()
            self._boxing_stage_done = True
            self.annotation_undo.clear()
            self.annotation_redo.clear()
            self.logic.load_ai_mask()
            self._open_correction_page()
            self.refresh_ui()
        except Exception as exc:
            self._show_error(f"AI reveal failed: {exc}")

    def _open_correction_page(self) -> None:
        if self.segment_editor_widget is not None:
            return
        self.segment_editor_widget = self.logic.setup_segment_editor(self.segment_editor_host)
        if self.segment_editor_widget is not None:
            self.segment_editor_host_layout.addWidget(self.segment_editor_widget)
        self.view_tools.restore_views(reset_orientation=False, fit=False)
        if self.roi_overlay is not None:
            self.roi_overlay.bind_views()
        if self.click_selector is not None:
            self.click_selector.bind_views()
        self._set_roi_visibility(False)
        self.page_stack.setCurrentIndex(1)
        self._observe_segmentation()
        self.correction_tools.refresh()

    def on_early_stop(self, status: str) -> None:
        if self.logic.state is None:
            return
        answer = qt.QMessageBox.question(slicer.util.mainWindow(), "Confirm early stop", f"Save {status.replace('_', ' ')} as the final outcome?")
        if answer != qt.QMessageBox.Yes:
            return
        try:
            self._flush_pending_roi_drag()
            self.logic.publish_early_stop(status)
            self._advance_after_finalization(f"Final outcome saved: {status}")
        except Exception as exc:
            self._show_error(str(exc))

    def on_complete(self) -> None:
        if self._has_unresolved_roi():
            self._show_error("Save or cancel the current myoma box before completing the case.")
            return
        answer = qt.QMessageBox.question(
            slicer.util.mainWindow(),
            "Confirm completion",
            "Publish the corrected labels and final annotations for this case?",
        )
        if answer != qt.QMessageBox.Yes:
            return
        source: Path | None = None
        try:
            # publish_complete serialises state.lesions and then deletes the
            # draft, so an uncommitted drag would be unrecoverable.
            self._flush_pending_roi_drag()
            self.logic.validate_correction_segments()
            source = self._save_corrected_snapshot()
            self.logic.publish_complete(source)
            self._advance_after_finalization("Completed labels and annotations saved.")
        except Exception as exc:
            self._show_error(f"Completion failed: {exc}")
        finally:
            if source is not None:
                try:
                    source.unlink()
                except OSError:
                    pass

    def on_reopen_case(self) -> None:
        if not self.logic.read_only or self.logic.manifest_source is None:
            return
        answer = qt.QMessageBox.question(
            slicer.util.mainWindow(),
            "Reopen completed case",
            "Reopen this case to edit myoma boxes, FIGO and labels? "
            "The published results are backed up, and the case stays unfinished "
            "until you complete it again.",
        )
        if answer != qt.QMessageBox.Yes:
            return
        source: Path | None = None
        try:
            # The read-only case shows the saved corrected labels; carrying them
            # over as the draft segmentation keeps the earlier corrections.
            if self.logic.segmentation_node is not None and self.logic.storage is not None:
                source = self.logic.storage.case_directory / ".reopen.tmp.seg.nrrd"
                if not slicer.util.saveNode(self.logic.segmentation_node, str(source)):
                    raise RuntimeError("the saved labels could not be carried over")
            self.logic.reopen_complete(source)
            manifest_source = self.logic.manifest_source
            case_id = self.logic.case_id
            self._teardown_current_case()
            self._load_case(manifest_source, case_id)
            self._set_status("Case reopened. Complete it again when your edits are done.")
        except Exception as exc:
            traceback.print_exc()
            self._show_error(f"Case could not be reopened: {exc}")
            self._refresh_case_list()
        finally:
            if source is not None:
                try:
                    source.unlink()
                except OSError:
                    pass

    def _advance_after_finalization(self, completion_message: str) -> None:
        # The case is published and read-only now: nothing may stay in hand.
        self.set_active_roi(None)
        target = self.logic.next_unfinished_case_id()
        self._refresh_case_list()
        if target is None or self.logic.manifest_source is None:
            self.refresh_ui()
            self._set_status(f"{completion_message} {self._no_reviewable_message()}")
            return
        source = self.logic.manifest_source
        self._set_status(f"{completion_message} Opening {target}.")
        self.open_case(source, target)

    def _save_corrected_snapshot(self) -> Path:
        if self.logic.storage is None or self.logic.segmentation_node is None:
            raise StorageError("correction segmentation is not loaded")
        temporary = self.logic.storage.case_directory / ".corrected_labels.tmp.nii.gz"
        working = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "Lite corrected export")
        try:
            success = slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
                self.logic.segmentation_node,
                list(FIXED_SEGMENT_IDS.values()),
                working,
                self.logic.volume_nodes["sagittal"],
                slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY,
            )
            if not success or not slicer.util.saveNode(working, str(temporary)):
                raise RuntimeError("corrected labelmap could not be exported")
        finally:
            if working.GetScene() is slicer.mrmlScene:
                slicer.mrmlScene.RemoveNode(working)
        return temporary

    def _style_roi_display(self, node: Any, *, handles: bool, outline: bool = True) -> None:
        """Hide or reveal the handle circles Slicer draws on one myoma box.

        ``SetLocked`` does not touch the handles, so ``SetHandlesInteractive``
        is what both hides them and keeps a box from being dragged. The circle
        at the box centre is a separate control-point glyph and needs its own call.

        ``outline`` is off wherever a model node already draws the box, so the
        hardcoded 3 px markups outline never competes with the thin one.
        """

        if node is None:
            return
        if node.GetDisplayNode() is None:
            node.CreateDefaultDisplayNodes()
        display = node.GetDisplayNode()
        if display is None:
            return
        was_modified = display.StartModify()
        try:
            display.SetFillVisibility(False)
            display.SetOutlineVisibility(bool(outline))
            display.SetHandlesInteractive(bool(handles))
            for method_name in ("SetTranslationHandleVisibility", "SetScaleHandleVisibility"):
                if hasattr(display, method_name):
                    getattr(display, method_name)(bool(handles))
            if hasattr(display, "SetRotationHandleVisibility"):
                display.SetRotationHandleVisibility(False)
            if hasattr(display, "SetInteractionHandleScale"):
                display.SetInteractionHandleScale(float(ROI_HANDLE_SCALE_PERCENT))
            if hasattr(display, "SetPropertiesLabelVisibility"):
                display.SetPropertiesLabelVisibility(False)
            if hasattr(display, "SetPointLabelsVisibility"):
                display.SetPointLabelsVisibility(False)
        finally:
            display.EndModify(was_modified)
        # The centre control point only exists once the box has a non-zero size.
        if node.GetNumberOfControlPoints() > 0 and hasattr(node, "SetNthControlPointVisibility"):
            node.SetNthControlPointVisibility(0, bool(handles))

    def _release_place_mode(self) -> None:
        """Undo the active-place-node side effect of adding a markups node."""

        interaction = slicer.app.applicationLogic().GetInteractionNode()
        if interaction is not None:
            interaction.SwitchToViewTransformMode()

    def _create_outline_node(self, name: str) -> Any:
        """Add one hidden model node whose slice intersection draws a myoma box.

        Both nodes are flagged before they reach the scene: afterwards the
        subject hierarchy has already claimed them, so they would clutter the
        reader's Data module and be written into any saved scene.
        """

        node = slicer.vtkMRMLModelNode()
        node.SetName(name)
        node.SetSelectable(False)
        node.SetHideFromEditors(True)
        node.SetSaveWithScene(False)
        node = slicer.mrmlScene.AddNode(node)
        display = slicer.vtkMRMLModelDisplayNode()
        display.SetHideFromEditors(True)
        display.SetSaveWithScene(False)
        display = slicer.mrmlScene.AddNode(display)
        node.SetAndObserveDisplayNodeID(display.GetID())
        source = vtk.vtkCubeSource()
        if hasattr(node, "SetPolyDataConnection"):
            node.SetPolyDataConnection(source.GetOutputPort())
        else:  # pragma: no cover - older API
            source.Update()
            node.SetAndObservePolyData(source.GetOutput())
        self.roi_outline_sources[node.GetID()] = source
        return node

    def _update_outline_node(
        self, node: Any, geometry: BoxGeometry, name: str, *, bold: bool = False
    ) -> Any:
        """Create or move one model box drawn as a slice-intersection outline.

        Every myoma box on screen goes through here, so a saved box, the box
        being dragged and the rubber band while drawing all share one line
        weight; only ``bold`` distinguishes the box currently in hand.
        """

        if node is None or node.GetScene() is not slicer.mrmlScene:
            node = self._create_outline_node(name)
        source = self.roi_outline_sources.get(node.GetID())
        if source is None:
            source = vtk.vtkCubeSource()
            if hasattr(node, "SetPolyDataConnection"):
                node.SetPolyDataConnection(source.GetOutputPort())
            self.roi_outline_sources[node.GetID()] = source
        lengths = geometry.size_mm
        current = (
            tuple(source.GetCenter()) + (source.GetXLength(), source.GetYLength(), source.GetZLength())
        )
        wanted = geometry.center_ras + lengths
        # Nothing debounces a handle drag, so skip the rebuild when it did not move.
        if any(abs(new - old) > 1e-6 for new, old in zip(wanted, current)):
            source.SetCenter(*geometry.center_ras)
            source.SetXLength(lengths[0])
            source.SetYLength(lengths[1])
            source.SetZLength(lengths[2])
            if not hasattr(node, "SetPolyDataConnection"):  # pragma: no cover - older API
                source.Update()
                node.SetAndObservePolyData(source.GetOutput())
        self._style_outline_display(node, bold=bold)
        return node

    def _style_outline_display(self, node: Any, *, bold: bool) -> None:
        display = node.GetDisplayNode() if node is not None else None
        if display is None:
            return
        was_modified = display.StartModify()
        try:
            # Visibility2D defaults to off, so without this the box never draws.
            display.SetVisibility2D(True)
            display.SetVisibility3D(False)
            display.SetColor(*ROI_OUTLINE_COLOR)
            display.SetSliceIntersectionThickness(
                int(ROI_ACTIVE_OUTLINE_THICKNESS_PX if bold else ROI_OUTLINE_THICKNESS_PX)
            )
            if hasattr(display, "SetSliceIntersectionOpacity"):
                display.SetSliceIntersectionOpacity(1.0)
            if hasattr(display, "SetFolderDisplayOverrideAllowed"):
                # Otherwise a hierarchy folder can impose one thickness on every
                # box and bold stops differing from thin.
                display.SetFolderDisplayOverrideAllowed(False)
            display.SetSelectable(False)
        finally:
            display.EndModify(was_modified)

    def _discard_outline_node(self, node: Any) -> None:
        if node is None:
            return
        self.roi_outline_sources.pop(node.GetID(), None)
        if node.GetScene() is not slicer.mrmlScene:
            return
        display = node.GetDisplayNode()
        if display is not None and display.GetScene() is slicer.mrmlScene:
            slicer.mrmlScene.RemoveNode(display)
        slicer.mrmlScene.RemoveNode(node)

    def _sync_roi_outline(self, lesion_id: str, geometry: BoxGeometry, *, bold: bool = False) -> Any:
        node = self._update_outline_node(
            self.roi_outline_nodes.get(lesion_id),
            geometry,
            f"Myoma {lesion_id} outline",
            bold=bold,
        )
        node.SetAttribute("MyomaReviewLite.LesionID", lesion_id)
        self.roi_outline_nodes[lesion_id] = node
        return node

    def _start_live_outline_sync(self, node: Any, callback: Any) -> None:
        """Keep one box's outline glued to the markups node being dragged."""

        self._stop_live_outline_sync()
        if node is None:
            return
        tag = node.AddObserver(vtk.vtkCommand.ModifiedEvent, lambda *_: callback())
        self.roi_live_sync = (node, tag)

    def _stop_live_outline_sync(self) -> None:
        if self.roi_live_sync is None:
            return
        node, tag = self.roi_live_sync
        try:
            node.RemoveObserver(tag)
        except Exception:
            pass
        self.roi_live_sync = None

    def _sync_active_outline(self) -> None:
        lesion_id = self.active_roi_id
        node = self.roi_nodes.get(lesion_id) if lesion_id else None
        if node is None or node.GetScene() is not slicer.mrmlScene:
            return
        geometry = self._roi_geometry_or_none(node)
        if geometry is None:
            return
        self._sync_roi_outline(lesion_id, geometry, bold=True).SetDisplayVisibility(
            self.roi_boxes_visible
        )

    def _sync_draft_outline(self) -> None:
        node = self.roi_draft_node
        if node is None or node.GetScene() is not slicer.mrmlScene:
            return
        geometry = self._roi_geometry_or_none(node)
        if geometry is None:
            return
        self.roi_draft_outline = self._update_outline_node(
            self.roi_draft_outline, geometry, "Lite ROI draft outline", bold=True
        )
        self.roi_draft_outline.SetDisplayVisibility(True)

    def _remove_draft_outline(self) -> None:
        node = self.roi_draft_outline
        self.roi_draft_outline = None
        self._discard_outline_node(node)

    def _remove_roi_outline(self, lesion_id: str) -> None:
        self._discard_outline_node(self.roi_outline_nodes.pop(lesion_id, None))

    def _clear_roi_outlines(self) -> None:
        for lesion_id in list(self.roi_outline_nodes):
            self._remove_roi_outline(lesion_id)

    def _roi_geometry(self, node: Any) -> BoxGeometry:
        center = [0.0, 0.0, 0.0]
        size = [0.0, 0.0, 0.0]
        node.GetCenterWorld(center)
        node.GetSizeWorld(size)
        return BoxGeometry(tuple(center), tuple(abs(value) for value in size))

    def _roi_geometry_or_none(self, node: Any) -> BoxGeometry | None:
        try:
            return self._roi_geometry(node)
        except ValidationError:
            return None

    def _roi_edits_allowed(self) -> bool:
        """Whether this case's box geometry may change at all."""

        state = self.logic.state
        return bool(
            state is not None
            and not self.logic.read_only
            and state.phase in (ReviewPhase.BLIND.value, ReviewPhase.AI_REVEALED.value)
        )

    def _roi_handles_draggable(self) -> bool:
        """Whether a revealed box may be dragged right now.

        Geometry belongs to the annotation page; on the correction page a
        revealed box shows its handles but stays locked, so a Segment Editor
        stroke can never drag it.
        """

        return self._roi_edits_allowed() and self.page_stack.currentIndex == 0

    def _figo_focus_active(self) -> bool:
        """Whether the reader is typing FIGO on the blinded annotation page.

        Boxing has its own stage, so by now the boxes are settled: FIGO typing
        looks at one myoma at a time and never edits geometry. After reveal the
        annotation page keeps its click-to-edit handles, so it is excluded.
        """

        state = self.logic.state
        return bool(
            state is not None
            and not self.logic.read_only
            and state.phase == ReviewPhase.BLIND.value
            and self.page_stack.currentIndex == 0
            and self.blind_stack.currentIndex == 1
        )

    def _apply_roi_selection(self) -> None:
        """Push the current selection, visibility and page onto every box.

        Slicer offers no visible-but-immovable handle: ``SetHandlesInteractive``
        both shows and arms them, and ``SetLocked`` does not disable them. A box
        that must not move therefore gets no handles at all. This has to run
        again after a page change, because the page decides that.

        While a box is being placed or redrawn (``_has_unresolved_roi``), every
        *other* box is hidden outright regardless of selection, for a clear,
        uncluttered view while the reader judges the new box's extent; see
        _update_box_edit_cursor for the matching cursor swap.

        While FIGO typing (``_figo_focus_active``) it is the list selection that
        decides what is drawn: the selected myoma alone, as a thin outline with
        no handles, and every other box and label hidden.
        """

        focus = self._figo_focus_active()
        if focus and self.active_roi_id is not None:
            # Nothing may stay in hand while typing FIGO. This flushes a pending
            # drag, disarms the box, and re-enters here with nothing selected.
            self.set_active_roi(None)
            return
        focus_id = self._selected_lesion_id() if focus else None
        if self.roi_overlay is not None:
            self.roi_overlay.set_only(focus_id if focus else None)
        draggable = self._roi_handles_draggable()
        hide_all = self._has_unresolved_roi()
        for identifier, node in self.roi_nodes.items():
            selected = identifier == self.active_roi_id
            armed = selected and draggable
            node.SetLocked(not armed)
            self._style_roi_display(node, handles=armed, outline=False)
            node.SetDisplayVisibility(bool(selected and self.roi_boxes_visible and not hide_all))
            outline = self.roi_outline_nodes.get(identifier)
            if outline is not None:
                self._style_outline_display(outline, bold=selected)
                shown = not focus or identifier == focus_id
                outline.SetDisplayVisibility(bool(shown and self.roi_boxes_visible and not hide_all))
        # The cursor is the corner-drag affordance, so it only holds for the
        # initial drag; once the square is set and handle fine-tuning takes
        # over (roi_draft_node exists, draw_tool is no longer active) the
        # normal cursor comes back even though the other boxes stay hidden.
        self._update_box_edit_cursor(bool(self.draw_tool is not None and self.draw_tool.active))

    def _box_placement_cursor(self) -> Any:
        """A small filled yellow dot: shown while placing or redrawing a box."""
        if self._place_box_cursor is None:
            diameter = 8
            pixmap = qt.QPixmap(diameter, diameter)
            pixmap.fill(qt.Qt.transparent)
            painter = qt.QPainter(pixmap)
            try:
                painter.setRenderHint(qt.QPainter.Antialiasing, True)
                painter.setPen(qt.QPen(qt.QColor(140, 105, 0), 1))
                painter.setBrush(qt.QBrush(qt.QColor(255, 213, 0)))
                painter.drawEllipse(1, 1, diameter - 2, diameter - 2)
            finally:
                painter.end()
            self._place_box_cursor = qt.QCursor(pixmap, diameter // 2, diameter // 2)
        return self._place_box_cursor

    def _update_box_edit_cursor(self, active: bool) -> None:
        """Swap the slice-view cursor for the yellow dot while placing/redrawing.

        A plain ``view.setCursor(...)`` does not stick: the slice view's own
        interactor style (crosshair/pan/zoom navigation) reasserts its own
        cursor on practically every mouse event, so the widget-level cursor
        gets overwritten within a frame. ``QApplication`` override cursors sit
        above that and win regardless, which is the same mechanism Slicer's
        own placement tools use for their cursors. The stack must be pushed
        and popped in matching pairs, hence the ``_box_cursor_applied`` guard.
        """
        if active and not self._box_cursor_applied:
            slicer.app.setOverrideCursor(self._box_placement_cursor())
            self._box_cursor_applied = True
        elif not active and self._box_cursor_applied:
            slicer.app.restoreOverrideCursor()
            self._box_cursor_applied = False

    def set_active_roi(self, lesion_id: str | None) -> None:
        """Reveal the handles of one myoma box and hide every other box's.

        A revealed box is unlocked so its handles can be dragged directly; the
        moved geometry is written back to the draft when the box is deselected.
        """

        if lesion_id is not None and lesion_id not in self.roi_nodes:
            lesion_id = None
        if lesion_id == self.active_roi_id:
            return
        self._commit_active_roi_geometry()
        self.active_roi_id = lesion_id
        self._apply_roi_selection()
        if lesion_id is None:
            self.active_roi_geometry = None
            self._stop_live_outline_sync()
            return
        self.active_roi_geometry = self._roi_geometry_or_none(self.roi_nodes[lesion_id])
        self._start_live_outline_sync(self.roi_nodes[lesion_id], self._sync_active_outline)
        self._release_place_mode()

    def _commit_active_roi_geometry(self) -> None:
        """Persist a box that was dragged by its revealed handles.

        Saving the draft re-enters this method, so the guard keeps one drag from
        being written twice, and the baseline is re-armed afterwards so a second
        drag of the same box is still noticed.
        """

        if self.committing_roi_geometry:
            return
        lesion_id = self.active_roi_id
        node = self.roi_nodes.get(lesion_id) if lesion_id else None
        previous = self.active_roi_geometry
        self.active_roi_geometry = None
        if node is None or previous is None or node.GetScene() is not slicer.mrmlScene:
            return
        current = self._roi_geometry_or_none(node)
        if current is None:
            return
        if self.active_roi_id == lesion_id:
            self.active_roi_geometry = current
        if self.logic.state is None or not self._roi_edits_allowed():
            return
        moved = any(
            abs(new - old) > 1e-4
            for new, old in zip(
                current.center_ras + current.size_mm, previous.center_ras + previous.size_mm
            )
        )
        if not moved:
            return
        self.committing_roi_geometry = True
        try:
            self._remember_annotation()
            self.logic.state.update_lesion(lesion_id, current.center_ras, current.size_mm)
            self._sync_roi_outline(lesion_id, current, bold=lesion_id == self.active_roi_id)
            self._write_current_draft()
            self._set_status(f"{lesion_id} moved. The new box was saved to the draft.")
        except Exception as exc:
            self._show_error(str(exc))
        finally:
            self.committing_roi_geometry = False

    def begin_view_click(self) -> None:
        """Record where the revealed box was when the mouse went down.

        Movement is then measured over this one press/release cycle, so an
        earlier drag that has not been saved yet cannot make an ordinary click
        look like the tail of a drag.
        """

        node = self.roi_nodes.get(self.active_roi_id) if self.active_roi_id else None
        self.roi_click_baseline = (
            self._roi_geometry_or_none(node)
            if node is not None and node.GetScene() is slicer.mrmlScene
            else None
        )

    def _active_roi_was_dragged(self) -> bool:
        """Whether the revealed box moved during the click being handled."""

        node = self.roi_nodes.get(self.active_roi_id) if self.active_roi_id else None
        previous = self.roi_click_baseline or self.active_roi_geometry
        if node is None or previous is None or node.GetScene() is not slicer.mrmlScene:
            return False
        current = self._roi_geometry_or_none(node)
        if current is None:
            return False
        return any(
            abs(new - old) > 1e-4
            for new, old in zip(
                current.center_ras + current.size_mm, previous.center_ras + previous.size_mm
            )
        )

    def on_view_clicked(self, view_name: str, point_ras: tuple[float, float, float]) -> None:
        """Select the myoma box under a click in a T2 view, or clear the selection."""

        # Only the myoma chosen in the FIGO list is drawn there, so a click
        # must not select a box the reader cannot see.
        if not self.roi_boxes_visible or self._has_unresolved_roi() or self._figo_focus_active():
            return
        candidates = []
        for lesion_id, node in self.roi_nodes.items():
            if node.GetScene() is not slicer.mrmlScene:
                continue
            geometry = self._roi_geometry_or_none(node)
            if geometry is not None:
                candidates.append((lesion_id, geometry))
        lesion_id = pick_box_at_point(point_ras, candidates, tolerance_mm=ROI_PICK_TOLERANCE_MM)
        dragged = self._active_roi_was_dragged()
        self.roi_click_baseline = None
        if lesion_id != self.active_roi_id and self.active_roi_id is not None:
            # Releasing a handle just outside the box, or letting go at the end
            # of a drag, must not hand the selection to a neighbour. An ordinary
            # click on a box nested inside this one still switches to it.
            active = dict(candidates).get(self.active_roi_id)
            if (
                (lesion_id is None or dragged)
                and active is not None
                and box_contains_point(
                    active, point_ras, tolerance_mm=ROI_ACTIVE_PICK_TOLERANCE_MM
                )
            ):
                return
        self.set_active_roi(lesion_id)
        if lesion_id is None:
            return
        index = self.figo_lesion_combo.findData(lesion_id)
        if index >= 0:
            self.figo_lesion_combo.setCurrentIndex(index)

    def update_roi_preview(self, preview_node: Any, geometry: BoxGeometry, view_name: str) -> Any:
        node = self._update_outline_node(preview_node, geometry, "Lite ROI preview", bold=True)
        node.SetDisplayVisibility(True)
        return node

    def remove_roi_preview(self, preview_node: Any) -> None:
        self._discard_outline_node(preview_node)

    def view_point_to_ras(self, view_name: str, point_xy: tuple[int, int]) -> tuple[float, float, float]:
        view_widget = slicer.app.layoutManager().sliceWidget(view_name)
        if view_widget is None:
            raise RuntimeError("requested Lite slice view is not available")
        view = view_widget.sliceView()
        ras = view.convertXYZToRAS(view.convertDeviceToXYZ([int(point_xy[0]), int(point_xy[1])]))
        return tuple(float(value) for value in ras[:3])

    def rectangle_geometry(self, view_name: str, start_xy: tuple[int, int], end_xy: tuple[int, int]) -> BoxGeometry:
        view_widget = slicer.app.layoutManager().sliceWidget(view_name)
        if view_widget is None:
            raise RuntimeError("requested Lite slice view is not available")
        corners = [
            self.view_point_to_ras(view_name, point)
            for point in ((start_xy[0], start_xy[1]), (end_xy[0], start_xy[1]), end_xy, (start_xy[0], end_xy[1]))
        ]
        matrix = view_widget.sliceLogic().GetSliceNode().GetSliceToRAS()
        normal = tuple(float(matrix.GetElement(row, 2)) for row in range(3))
        length = math.sqrt(sum(value * value for value in normal)) or 1.0
        normal = tuple(value / length for value in normal)
        normal_axis = max(range(3), key=lambda index: abs(normal[index]))
        extents = [max(point[index] for point in corners) - min(point[index] for point in corners) for index in range(3)]
        in_plane = [extents[index] for index in range(3) if index != normal_axis]
        depth = max(min(in_plane), 0.5)
        for index in range(3):
            extents[index] = max(extents[index], depth if index == normal_axis else 0.5)
        center = tuple(sum(point[index] for point in corners) / 4.0 for index in range(3))
        return BoxGeometry(center, tuple(extents))

    def accept_rectangle(self, geometry: BoxGeometry, update_lesion_id: str | None) -> None:
        if self.roi_draft_node is not None:
            raise ValidationError("save or cancel the current ROI edit first")
        # The box being drawn owns the handles; drop any earlier reveal, which
        # also flushes a pending drag before the node it belongs to is replaced.
        self.set_active_roi(None)
        self.roi_draft_update_id = update_lesion_id
        self.roi_transaction.begin(geometry)
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode", "Lite ROI draft")
        node.SetCenterWorld(*geometry.center_ras)
        node.SetSizeWorld(*geometry.size_mm)
        node.SetLocked(False)
        node.SetDisplayVisibility(True)
        # A box being drawn needs its handles, but its line must match every
        # other box, so the fat markups outline gives way to a bold model one.
        self._style_roi_display(node, handles=True, outline=False)
        self.roi_draft_node = node
        self._sync_draft_outline()
        self._start_live_outline_sync(node, self._sync_draft_outline)
        # roi_draft_node just went from None to set, i.e. _has_unresolved_roi()
        # flipped true: hide the other boxes.
        self._apply_roi_selection()
        if hasattr(self, "save_roi_button"):
            self.save_roi_button.enabled = True
            self.cancel_roi_button.enabled = True
        self._set_status("Adjust the native 3-D handles, then choose Save ROI or Cancel ROI.")

    def save_roi_edit(self) -> None:
        if self.roi_draft_node is None or self.logic.state is None:
            return
        center = [0.0, 0.0, 0.0]
        size = [0.0, 0.0, 0.0]
        self.roi_draft_node.GetCenterWorld(center)
        self.roi_draft_node.GetSizeWorld(size)
        geometry = BoxGeometry(tuple(center), tuple(abs(value) for value in size))
        try:
            self._remember_annotation()
            if self.roi_draft_update_id is None:
                lesion_id = self.logic.state.add_lesion(geometry.center_ras, geometry.size_mm)
            else:
                lesion_id = self.roi_draft_update_id
                self.logic.state.update_lesion(lesion_id, geometry.center_ras, geometry.size_mm)
            node = self.roi_draft_node
            node.SetName(f"Myoma {lesion_id}")
            node.SetAttribute("MyomaReviewLite.LesionID", lesion_id)
            node.SetLocked(True)
            old_node = self.roi_nodes.get(lesion_id)
            if old_node is not None and old_node is not node:
                if self.roi_overlay is not None:
                    self.roi_overlay.untrack(old_node)
                slicer.mrmlScene.RemoveNode(old_node)
            self.roi_nodes[lesion_id] = node
            self._stop_live_outline_sync()
            self._remove_draft_outline()
            self._style_roi_display(node, handles=False, outline=False)
            node.SetDisplayVisibility(False)
            self._sync_roi_outline(lesion_id, geometry).SetDisplayVisibility(self.roi_boxes_visible)
            self.roi_draft_node = None
            self.roi_draft_update_id = None
            self.save_roi_button.enabled = False
            self.cancel_roi_button.enabled = False
            self.roi_transaction.save()
            self._write_current_draft()
            if self.roi_overlay is not None:
                self.roi_overlay.track(node, lesion_id)
            self.refresh_ui()
            self.figo_lesion_combo.setCurrentIndex(self.figo_lesion_combo.findData(lesion_id))
        except Exception:
            self.cancel_roi_edit()
            raise

    def cancel_roi_edit(self) -> None:
        self._stop_live_outline_sync()
        self._remove_draft_outline()
        if self.roi_draft_node is not None and self.roi_draft_node.GetScene() is slicer.mrmlScene:
            slicer.mrmlScene.RemoveNode(self.roi_draft_node)
        self.roi_draft_node = None
        self.roi_draft_update_id = None
        if hasattr(self, "save_roi_button"):
            self.save_roi_button.enabled = False
            self.cancel_roi_button.enabled = False
        self.roi_transaction.cancel()
        # roi_draft_node just cleared, i.e. _has_unresolved_roi() may have
        # flipped false: restore the other boxes and the cursor.
        self._apply_roi_selection()

    def rebuild_roi_nodes(self) -> None:
        self.cancel_roi_edit()
        overlay_was_bound = bool(getattr(self.roi_overlay, "views", None))
        if self.roi_overlay is not None:
            self.roi_overlay.clear()
        self.active_roi_id = None
        self.active_roi_geometry = None
        self._stop_live_outline_sync()
        self._clear_roi_outlines()
        for node in self.roi_nodes.values():
            if node.GetScene() is slicer.mrmlScene:
                slicer.mrmlScene.RemoveNode(node)
        self.roi_nodes = {}
        if self.logic.state is not None:
            lesion_values = [
                (lesion.lesion_id, lesion.center_ras, lesion.size_mm)
                for lesion in self.logic.state.active_lesions
            ]
        else:
            lesion_values = [
                (
                    str(lesion.get("lesion_id")),
                    tuple(float(value) for value in lesion.get("center_ras", ())),
                    tuple(float(value) for value in lesion.get("size_mm", ())),
                )
                for lesion in (self.logic.final_annotation or {}).get("lesions", [])
            ]
        for lesion_id, center_ras, size_mm in lesion_values:
            node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode", f"Myoma {lesion_id}")
            node.SetCenterWorld(*center_ras)
            node.SetSizeWorld(*size_mm)
            node.SetLocked(True)
            self._style_roi_display(node, handles=False, outline=False)
            node.SetDisplayVisibility(False)
            node.SetAttribute("MyomaReviewLite.LesionID", lesion_id)
            self.roi_nodes[lesion_id] = node
            try:
                outline = self._sync_roi_outline(lesion_id, BoxGeometry(center_ras, size_mm))
            except ValidationError:
                traceback.print_exc()
            else:
                outline.SetDisplayVisibility(self.roi_boxes_visible)
            if self.roi_overlay is not None:
                self.roi_overlay.track(node, lesion_id)
        # clear() dropped every renderer binding, so the labels tracked above
        # would have no actors until the next case was opened.
        if overlay_was_bound and self.roi_overlay is not None:
            self.roi_overlay.bind_views()
        self._release_place_mode()

    def _set_roi_visibility(self, visible: bool) -> None:
        self.roi_boxes_visible = bool(visible)
        if not self.roi_boxes_visible:
            self.set_active_roi(None)
        else:
            # This runs after a page change, so bank any drag made while the
            # box was still draggable before the new page re-locks it.
            self._commit_active_roi_geometry()
        self._apply_roi_selection()
        if self.roi_overlay is not None:
            self.roi_overlay.set_enabled(bool(visible))
        if self.roi_draft_node is not None:
            self.roi_draft_node.SetDisplayVisibility(True)

    def on_show_roi_toggled(self, checked: bool) -> None:
        if self.logic.segmentation_node is not None:
            self._set_roi_visibility(bool(checked))

    def on_application_shutdown(self) -> None:
        if self.shutdown_saved:
            return
        try:
            self._write_current_draft()
            self.save_coordinator.clear_dirty()
            self.shutdown_saved = True
        except Exception:
            traceback.print_exc()

    def cleanup(self) -> None:
        if SLICER_AVAILABLE:
            self.exit()
            for shortcut in self.shortcuts:
                shortcut.deleteLater()
            self.shortcuts = []
            if self.mouse_button_dispatcher is not None:
                self.mouse_button_dispatcher.uninstall()
                self.mouse_button_dispatcher = None
            if self.shift_crosshair_toggle is not None:
                self.shift_crosshair_toggle.uninstall()
                self.shift_crosshair_toggle = None
            if self.view_tools is not None:
                self.view_tools.cleanup()
            self.on_application_shutdown()
            if self.autosave_timer is not None:
                self.autosave_timer.stop()
            try:
                slicer.app.disconnect("aboutToQuit()", self.on_application_shutdown)
            except Exception:
                pass
            self._teardown_current_case()
        super().cleanup()


class MyomaReviewLiteTest(ScriptedLoadableModuleTest):
    def runTest(self) -> None:
        self.test_pure_contracts()
        if SLICER_AVAILABLE:
            self.test_slicer_dependencies()

    def test_pure_contracts(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "1.0")
        self.assertEqual(LABEL_NAMES[4], "Nabothian cyst")
        validate_fixed_segment_ids(FIXED_SEGMENT_IDS.values())
        self.assertEqual(box_from_corners((0, 0, 0), (2, 4, 6)).size_mm, (2.0, 4.0, 6.0))

    def test_slicer_dependencies(self) -> None:
        self.assertIsNotNone(slicer.modules.segmentations)
        self.assertIsNotNone(slicer.modules.segmenteditor)
        self.assertTrue(hasattr(slicer, "vtkMRMLMarkupsROINode"))
