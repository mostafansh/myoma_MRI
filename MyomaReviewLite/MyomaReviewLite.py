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
    ImageGeometry,
    QueueEntry,
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
from MyomaReviewLiteLib.domain import validate_correction_mask_values
from MyomaReviewLiteLib.figo_card import FigoCard, card_position, figo_label, missing_figo_ids
from MyomaReviewLiteLib.view_tools import ReviewViewTools
from MyomaReviewLiteLib.correction_tools import ReviewCorrectionTools
from MyomaReviewLiteLib.correction_panel import CorrectionPanel
from MyomaReviewLiteLib import theme
from MyomaReviewLiteLib.fonts import load_bundled_fonts
from MyomaReviewLiteLib.panel_model import (
    CASE_FILTER_STATUSES,
    CASE_FILTERS,
    DEFAULT_CASE_FILTER,
    bottom_hint,
    case_groups,
    matches_case_search,
    queue_strip_text,
)

ICON_DIR = Path(__file__).resolve().parent / "Resources" / "Icons"


VIEW_NAMES = {
    "sagittal": "MyomaLiteSagittal",
    "axial": "MyomaLiteAxial",
    "coronal": "MyomaLiteCoronal",
}
VIEW_ORIENTATIONS = {"sagittal": "Sagittal", "axial": "Axial", "coronal": "Coronal"}
SAFE_VOLUME_NAMES = {key: f"T2 {key.title()}" for key in VIEW_NAMES}
CUSTOM_LAYOUT_ID = 735
# Segment editor node reference to an auto-complete effect's preview
# segmentation (ResultPreviewNodeReferenceRole in Slicer's SegmentEditorEffects).
EFFECT_PREVIEW_REFERENCE_ROLE = "SegmentationResultPreview"
FIXED_SEGMENT_IDS = {
    1: "uterine_wall",
    2: "uterine_cavity",
    3: "myoma",
    4: "nabothian_cyst",
}
FIXED_SEGMENT_NAMES = {segment_id: LABEL_NAMES[value] for value, segment_id in FIXED_SEGMENT_IDS.items()}
# Segments of the former six-label set. Drafts saved before their removal still
# carry them; loading drops them and their voxels become background.
RETIRED_SEGMENT_IDS = ("cervix", "cesarean_scar_niche")

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

# page_stack pages: 0 is the box/FIGO page, 1 the segment correction page.
# The Box / Segment switch above them flips between the two once AI is shown.
BOX_PAGE, SEGMENT_PAGE = 0, 1

# The reviewer-configurable keyboard shortcut panel. Each row is
# (action id, label shown in the shortcut dialog, factory-default key
# sequence, the widget method the shortcut activates, the attribute name of
# a toolbar button to keep labelled with its current binding, and that
# button's base text without the binding). Bindings are stored per-user in
# QSettings under "MyomaReviewLite/Shortcuts/<action id>" so the defaults
# below only apply the first time a reviewer opens the module.
SHORTCUT_ACTIONS: tuple[tuple[str, str, str, str, str | None, str | None], ...] = (
    ("undo", "Undo", "Ctrl+Z", "on_undo", None, None),
    ("redo", "Redo", "Ctrl+Y", "on_redo", None, None),
    ("redo_alt", "Redo (alternate)", "Ctrl+Shift+Z", "on_redo", None, None),
    ("add_box", "Add myoma box", "", "on_add_box", "add_button", "＋  Add myoma box"),
    ("delete_box", "Delete box", "", "on_delete_box", "delete_button", "Delete"),
    ("save_draft", "Save draft", "", "on_save_draft", "save_draft_button", "Save draft"),
    ("show_myomas", "Show segmented myomas", "V", "on_show_myomas", None, None),
    ("toggle_roi_boxes", "Show / hide boxes", "Q", "on_toggle_roi_boxes", "show_roi_check", "Show ROI boxes"),
    ("toggle_ai_mask", "Show / hide AI mask", "A", "on_toggle_ai_mask", None, None),
    ("segment_1", "Pick uterine wall", "1", "on_pick_segment_1", None, None),
    ("segment_2", "Pick uterine cavity", "2", "on_pick_segment_2", None, None),
    ("segment_3", "Pick myoma", "3", "on_pick_segment_3", None, None),
    ("segment_4", "Pick Nabothian cyst", "4", "on_pick_segment_4", None, None),
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


def validate_fixed_segment_ids(segment_ids: Iterable[str]) -> dict[str, list[str]]:
    """Require the four named correction segments.

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


def slice_cuts_bounds(slice_node: Any, bounds: list[float]) -> bool:
    """Whether the slice plane passes through the RAS box ``bounds``.

    Projects the plane and the box's corners onto the slice normal rather
    than comparing GetSliceOffset() with one RAS axis: the offset is the
    plane's position along the normal, so a view whose normal points down an
    axis (this module's sagittal is -R) reports the negated coordinate.
    """

    matrix = slice_node.GetSliceToRAS()
    normal = [matrix.GetElement(row, 2) for row in range(3)]
    plane = sum(normal[row] * matrix.GetElement(row, 3) for row in range(3))
    reach = sum(abs(normal[row]) * (bounds[row * 2 + 1] - bounds[row * 2]) / 2.0 for row in range(3))
    centre = sum(normal[row] * (bounds[row * 2] + bounds[row * 2 + 1]) / 2.0 for row in range(3))
    return centre - reach - 1e-4 <= plane <= centre + reach + 1e-4


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
        # Axial and coronal keep their acquired grids (registered into the
        # sagittal's world space), so only the mask is held to the sagittal grid.
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
            if widget is not None:
                theme.style_slice_bar(widget)
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
                [self._node_geometry(self.volume_nodes["sagittal"]), self._node_geometry(self.ai_mask_node)]
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
            legacy_id = LABEL_NAMES[label_value]
            segment = segmentation.GetSegment(legacy_id)
            if segment is not None and str(segment.GetName()).lower() == stable_id.lower():
                legacy_placeholder_ids.append(legacy_id)
        if len(legacy_placeholder_ids) == 4:
            for segment_id in legacy_placeholder_ids:
                segmentation.RemoveSegment(segment_id)

        for segment_id in RETIRED_SEGMENT_IDS:
            if segmentation.GetSegment(segment_id) is not None:
                segmentation.RemoveSegment(segment_id)

        # Imported labelmaps may expose their values as names such as "1",
        # "Label_1", or "Label 1".  Map those aliases to stable public IDs,
        # then create an empty fixed segment when a label is genuinely absent.
        existing = [
            segmentation.GetNthSegmentID(index)
            for index in range(segmentation.GetNumberOfSegments())
        ]
        for label_value, stable_id in FIXED_SEGMENT_IDS.items():
            label_name = LABEL_NAMES[label_value]
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
                      4: (0.4, 0.7, 1.0)}
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
        # Fill between slices and Grow from seeds keep an unapplied result in a
        # "<segmentation> preview" node that only Apply or Cancel removes; it
        # holds every segment, so left behind it draws this case's labels on
        # the next case, blind phase included.
        preview_node = None
        if self.segment_editor_node is not None:
            preview_node = self.segment_editor_node.GetNodeReference(EFFECT_PREVIEW_REFERENCE_ROLE)
        for node in list(self.volume_nodes.values()) + [
            self.ai_mask_node,
            preview_node,
            self.segmentation_node,
            self.segment_editor_node,
        ]:
            if node is not None and node.GetScene() is slicer.mrmlScene:
                slicer.mrmlScene.RemoveNode(node)
        self.volume_nodes = {}
        self.ai_mask_node = None
        self.segmentation_node = None
        self.segment_editor_node = None


if SLICER_AVAILABLE:  # pragma: no cover - exercised in Slicer smoke tests

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
            self.widget.accept_rectangle(geometry, update_id, view_name)


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
        # Q hides every box on the box page too; each page keeps its own choice.
        self.box_page_boxes_shown = True
        self.active_roi_id: str | None = None
        self.active_roi_geometry: BoxGeometry | None = None
        self.roi_click_baseline: BoxGeometry | None = None
        self.committing_roi_geometry = False
        self.roi_transaction = RoiTransaction()
        self.roi_draft_node: Any = None
        self.roi_draft_update_id: str | None = None
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
        self._released_action_shortcuts: list[tuple[Any, list[Any]]] = []
        self.shortcut_bindings: dict[str, Any] = {}
        self.mouse_button_bindings: dict[str, int] = {}
        self.mouse_button_dispatcher: Any = None
        self.view_tools: Any = None
        self.correction_tools: Any = None
        self.shift_crosshair_toggle: Any = None
        self._case_filter = DEFAULT_CASE_FILTER
        # Slicer turns dark while this module is shown; see theme.DarkAppStyle.
        self.app_style = theme.DarkAppStyle()
        # ...and shows our name and logo instead of Slicer's; see theme.AppBranding.
        self.app_branding = theme.AppBranding(
            str(Path(__file__).resolve().parent / "Resources" / "Icons" / "MyomaReviewLite.svg")
        )
        self._hidden_panel_chrome: list[Any] = []
        self._close_filter: Any = None
        # Placing/redrawing a box swaps the slice-view cursor for a small
        # yellow dot and hides every other myoma box so the reader gets an
        # uncluttered view of the anatomy underneath; see _apply_roi_selection
        # and _has_unresolved_roi.
        self._place_box_cursor: Any = None
        self._box_cursor_applied = False
        # The on-image FIGO card (built in _build_ui), the view it sits in,
        # and the observers that keep it beside its box.
        self.figo_card: FigoCard | None = None
        self._figo_card_view: str | None = None
        # The saved FIGO the open card last showed; refresh_ui only pushes a
        # different one, so a half-made 8/Hybrid choice survives a refresh.
        self._figo_card_figo: dict[str, Any] | None = None
        self._figo_card_observers: list[tuple[Any, int]] = []
        self._last_view_name: str | None = None
        self._card_escape_shortcuts: dict[str, Any] = {}

    def onReload(self) -> None:
        # Slicer's Reload re-reads only this file. Drop the helper package too,
        # or the new widget code runs against the old MyomaReviewLiteLib classes.
        for name in [
            name for name in sys.modules
            if name == "MyomaReviewLiteLib" or name.startswith("MyomaReviewLiteLib.")
        ]:
            del sys.modules[name]
        super().onReload()

    def setup(self) -> None:
        super().setup()
        if not SLICER_AVAILABLE:
            return
        # Before the stylesheet asks for it; a failed load falls back to Segoe UI.
        load_bundled_fonts(Path(__file__).resolve().parent / "Resources" / "Fonts")
        self._build_ui()
        self._install_shortcuts()
        self._install_mouse_bindings()
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
        if getattr(self, "correction_panel", None) is not None:
            keys = (("toggle_ai_mask", "mask"), ("show_myomas", "myomas"), ("toggle_roi_boxes", "boxes"))
            bound = [
                (self.shortcut_bindings[action_id].key.toString(), text)
                for action_id, text in keys
                if self.shortcut_bindings.get(action_id) is not None
            ]
            self.correction_panel.set_key_hint(" · ".join(f"{key} {text}" for key, text in bound if key))
            self.correction_panel.set_segment_keys({
                FIXED_SEGMENT_IDS[value]: self.shortcut_bindings[f"segment_{value}"].key.toString()
                for value in (1, 2, 3, 4)
                if self.shortcut_bindings.get(f"segment_{value}") is not None
            })

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
        # The "Reload & Test" collapsible is added by ScriptedLoadableModuleWidget
        # only when Developer Mode is on, which some reading workstations have
        # enabled; it has nothing to do with reading. Slicer's own logo and
        # "Help & Acknowledgement" box are handled in _hide_panel_chrome.
        if hasattr(self, "reloadCollapsibleButton"):
            try:
                self._hide_widget(self.reloadCollapsibleButton)
            except Exception:
                pass

    # Slicer's own chrome around the module: the menu bar, the toolbars, the
    # status bar (only its error-log button lives there), the
    # logo above the module panel and the panel's "Help & Acknowledgement"
    # box. The panel shows the help box again every time a module is
    # selected, so all of it is hidden on every enter and given back on exit,
    # where other modules expect it. Slicer saves which toolbars are shown when
    # its window closes, so they also come back just before that (see
    # _make_close_filter); otherwise plain Slicer would start without them.
    # The logo spot stays: theme.AppBranding shows our name and logo there.
    PANEL_CHROME_NAMES = ("HelpCollapsibleButton",)

    def _panel_chrome_widgets(self) -> list[Any]:
        try:
            main_window = slicer.util.mainWindow()
        except Exception:
            return []
        if main_window is None:
            return []
        widgets = [main_window.menuBar(), main_window.statusBar()]
        widgets += [bar for bar in main_window.findChildren(qt.QToolBar) if bar.parent() is main_window]
        for name in self.PANEL_CHROME_NAMES:
            widget = main_window.findChild(qt.QWidget, name)
            if widget is not None:
                widgets.append(widget)
        return widgets

    def _hide_panel_chrome(self) -> None:
        for widget in self._panel_chrome_widgets():
            if widget is None or widget.isHidden():
                continue
            widget.visible = False
            if not any(widget is hidden for hidden in self._hidden_panel_chrome):
                self._hidden_panel_chrome.append(widget)
        self._set_close_filter(True)

    def _restore_panel_chrome(self) -> None:
        for widget in self._hidden_panel_chrome:
            try:
                widget.visible = True
            except Exception:
                pass
        self._hidden_panel_chrome = []
        self._set_close_filter(False)

    def _set_close_filter(self, on: bool) -> None:
        try:
            main_window = slicer.util.mainWindow()
        except Exception:
            return
        if main_window is None:
            return
        if self._close_filter is None:
            self._close_filter = self._make_close_filter()
        try:
            if on:
                main_window.installEventFilter(self._close_filter)
            else:
                main_window.removeEventFilter(self._close_filter)
        except Exception:
            pass

    def _make_close_filter(self) -> Any:
        """Give Slicer its chrome back before the closing window saves its layout."""

        widget = self

        class _CloseFilter(qt.QObject):
            def eventFilter(self, obj, event):
                if event.type() == qt.QEvent.Close:
                    widget._restore_panel_chrome()
                return False

        return _CloseFilter()

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
        # The look lives in MyomaReviewLiteLib/theme.py: widgets here only get
        # a "role" and the panel stylesheet decides how each role is drawn.
        self.parent.objectName = theme.PANEL_OBJECT_NAME
        self.parent.setAttribute(qt.Qt.WA_StyledBackground, True)
        self.parent.setStyleSheet(theme.PANEL_QSS)
        self.layout.setContentsMargins(10, 8, 10, 8)

        # --- Header: the open case between previous / next case buttons ---
        header = qt.QHBoxLayout()
        header.setSpacing(6)
        self.prev_case_button = self._icon_button("chevron-left.svg", "Previous case", self.on_previous_case)
        header.addWidget(self.prev_case_button)
        title_block = qt.QVBoxLayout()
        title_block.setSpacing(0)
        self.study_label = theme.set_role(qt.QLabel("Myoma review"), "title")
        self.study_label.alignment = qt.Qt.AlignCenter
        title_block.addWidget(self.study_label)
        self.case_header = theme.set_role(qt.QLabel("No case open"), "muted")
        self.case_header.alignment = qt.Qt.AlignCenter
        title_block.addWidget(self.case_header)
        header.addLayout(title_block, 1)
        self.next_case_button = self._icon_button("chevron-right.svg", "Next case", self.on_next_case)
        header.addWidget(self.next_case_button)
        self.layout.addLayout(header)

        # --- Queue strip: progress at a glance; clicking it opens the drawer ---
        self.queue_toggle = theme.set_role(qt.QToolButton(), "strip")
        self.queue_toggle.checkable = True
        self.queue_toggle.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
        self.queue_toggle.setMinimumHeight(52)
        self.queue_toggle.setCursor(qt.QCursor(qt.Qt.PointingHandCursor))
        self.queue_toggle.connect("toggled(bool)", self._on_queue_toggled)
        strip_layout = qt.QVBoxLayout(self.queue_toggle)
        strip_layout.setContentsMargins(10, 8, 10, 8)
        strip_layout.setSpacing(6)
        strip_row = qt.QHBoxLayout()
        strip_row.addWidget(qt.QLabel("Queue"))
        strip_row.addStretch(1)
        self.progress_label = theme.set_role(qt.QLabel(""), "muted")
        strip_row.addWidget(self.progress_label)
        arrow = qt.QLabel()
        arrow.setPixmap(qt.QIcon(str(ICON_DIR / "chevron-down.svg")).pixmap(14, 14))
        strip_row.addWidget(arrow)
        strip_layout.addLayout(strip_row)
        self.progress_bar = qt.QFrame()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet(f"background: {theme.FIELD}; border-radius: 2px;")
        bar_layout = qt.QHBoxLayout(self.progress_bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(0)
        self.progress_segments: dict[QueueStatus, Any] = {}
        for status in (QueueStatus.COMPLETE, QueueStatus.IN_PROGRESS, QueueStatus.ATTENTION, QueueStatus.NEW):
            segment = qt.QFrame()
            colour = theme.STATUS_COLOURS[status.value][0]
            segment.setStyleSheet(f"background: {colour}; border-radius: 0px;")
            segment.visible = False
            bar_layout.addWidget(segment)
            self.progress_segments[status] = segment
        strip_layout.addWidget(self.progress_bar)
        # The strip's labels are decoration: clicks belong to the button.
        for child in self.queue_toggle.findChildren(qt.QWidget):
            child.setAttribute(qt.Qt.WA_TransparentForMouseEvents)
        self.layout.addWidget(self.queue_toggle)

        # Picking a case and annotating it never need to be on screen at once,
        # so the drawer takes the annotation's place while it is open.
        self.content_stack = qt.QStackedWidget()
        self.layout.addWidget(self.content_stack, 1)
        self.annotation_tab = qt.QWidget()
        annotation_layout = qt.QVBoxLayout(self.annotation_tab)
        annotation_layout.setContentsMargins(0, 10, 0, 0)
        annotation_layout.setSpacing(8)
        self.content_stack.addWidget(self.annotation_tab)
        self.queue_drawer = qt.QWidget()
        cases_layout = qt.QVBoxLayout(self.queue_drawer)
        cases_layout.setContentsMargins(0, 10, 0, 0)
        cases_layout.setSpacing(8)
        self.content_stack.addWidget(self.queue_drawer)

        # --- Queue drawer: filter pills, search, grouped case rows, manifest ---
        filter_row = qt.QHBoxLayout()
        filter_row.setSpacing(6)
        self.filter_buttons: dict[str, Any] = {}
        for title, key in CASE_FILTERS:
            button = theme.set_role(qt.QToolButton(), "pill")
            button.text = title
            button.checkable = True
            button.checked = key == DEFAULT_CASE_FILTER
            button.setCursor(qt.QCursor(qt.Qt.PointingHandCursor))
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

        self.case_list = theme.set_role(qt.QListWidget(), "rows")
        self.case_list.setItemDelegate(theme.make_row_delegate(self.case_list))
        self.case_list.setMouseTracking(True)
        self.case_list.minimumHeight = 200
        self.case_list.connect("itemClicked(QListWidgetItem*)", self.on_case_chosen)
        cases_layout.addWidget(self.case_list, 1)
        self.case_empty_label = theme.set_role(qt.QLabel("No cases match"), "muted")
        self.case_empty_label.alignment = qt.Qt.AlignCenter
        self.case_empty_label.visible = False
        cases_layout.addWidget(self.case_empty_label)

        manifest_row = qt.QHBoxLayout()
        self.manifest_label = theme.set_role(qt.QLabel(""), "muted")
        manifest_row.addWidget(self.manifest_label, 1)
        self.manifest_button = theme.set_role(qt.QPushButton("Open case manifest"), "link")
        self.manifest_button.setCursor(qt.QCursor(qt.Qt.PointingHandCursor))
        self.manifest_button.connect("clicked()", self.choose_manifest)
        manifest_row.addWidget(self.manifest_button)
        cases_layout.addLayout(manifest_row)

        self.view_tools = ReviewViewTools(self.logic, self.parent)
        # Lets an Alt-drag disarm the Segment Editor for the drag's duration,
        # so adjusting the contrast can never also paint.
        self.view_tools.alt_drag.editor_provider = lambda: self.segment_editor_widget
        # No on-screen contrast panel: ReviewViewTools no longer builds one
        # (Alt + left-drag in a slice view is the only way to adjust
        # window/level now, see AltDragWindowLevel), so there is nothing of
        # its to add to the layout here.

        # --- Annotation: Box / Segment switch, read-only banner, the page, the action bar ---
        # Segment stays off until AI is revealed; from then on both pages are open.
        self.mode_bar = theme.set_role(qt.QFrame(), "switch")
        mode_layout = qt.QHBoxLayout(self.mode_bar)
        mode_layout.setContentsMargins(3, 3, 3, 3)
        mode_layout.setSpacing(3)
        self.mode_buttons: dict[int, Any] = {}
        for page, title in ((BOX_PAGE, "Box"), (SEGMENT_PAGE, "Segment")):
            button = theme.set_role(qt.QToolButton(), "switch")
            button.text = title
            button.checkable = True
            button.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
            button.connect("clicked()", lambda page=page: self._on_mode_clicked(page))
            mode_layout.addWidget(button)
            self.mode_buttons[page] = button
        self.box_mode_button = self.mode_buttons[BOX_PAGE]
        self.segment_mode_button = self.mode_buttons[SEGMENT_PAGE]
        self.segment_mode_button.toolTip = "Opens once AI is revealed"
        annotation_layout.addWidget(self.mode_bar)

        self.readonly_banner = theme.set_role(qt.QFrame(), "banner")
        banner_layout = qt.QVBoxLayout(self.readonly_banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        banner_layout.setSpacing(2)
        banner_layout.addWidget(theme.set_role(qt.QLabel("Finished"), "banner-title"))
        banner_layout.addWidget(theme.set_role(qt.QLabel("Read-only. Nothing here can be changed."), "banner-text"))
        self.readonly_banner.visible = False
        annotation_layout.addWidget(self.readonly_banner)

        # The case comment stays folded away until the reader wants it: an
        # icon in the action bar opens it as a pop-over just above the bar
        # (_position_comment_overlay), covering the page instead of pushing it.
        self.comment_toggle = theme.set_role(qt.QToolButton(), "icon")
        self.comment_toggle.checkable = True
        self.comment_toggle.toolButtonStyle = qt.Qt.ToolButtonIconOnly
        self.comment_toggle.setIconSize(qt.QSize(16, 16))
        self.comment_toggle.setFixedSize(38, 34)
        self.comment_toggle.connect("toggled(bool)", self.on_comment_toggled)
        self.case_comment_box = theme.set_role(qt.QFrame(self.annotation_tab), "popover")
        comment_box_layout = qt.QVBoxLayout(self.case_comment_box)
        comment_box_layout.setContentsMargins(10, 10, 10, 10)
        comment_box_layout.setSpacing(6)
        comment_title_row = qt.QHBoxLayout()
        comment_title_row.addWidget(theme.set_role(qt.QLabel("Case comment"), "strong"))
        comment_title_row.addStretch(1)
        comment_title_row.addWidget(theme.set_role(qt.QLabel("Esc to close"), "hint-muted"))
        comment_box_layout.addLayout(comment_title_row)
        self.case_comment_edit = qt.QPlainTextEdit()
        self.case_comment_edit.placeholderText = "Comment on the whole case"
        self.case_comment_edit.setFixedHeight(88)
        comment_box_layout.addWidget(self.case_comment_edit)
        self.case_comment_box.visible = False
        self.case_comment_edit.visible = False
        comment_escape = qt.QShortcut(qt.QKeySequence("Esc"), self.case_comment_box)
        comment_escape.context = qt.Qt.WidgetWithChildrenShortcut
        comment_escape.connect("activated()", lambda: setattr(self.comment_toggle, "checked", False))
        self._comment_overlay_filter = self._make_comment_overlay_filter()

        self.page_stack = qt.QStackedWidget()
        self.page_stack.connect("currentChanged(int)", lambda _index: self._on_page_changed())
        annotation_layout.addWidget(self.page_stack, 1)
        # The blinded annotation page: boxes, FIGO (on the image card) and
        # early stops live here; the same page is used after reveal.
        blind = qt.QWidget()
        blind_layout = qt.QVBoxLayout(blind)
        blind_layout.setContentsMargins(0, 0, 0, 0)
        blind_layout.setSpacing(6)
        myoma_header = qt.QHBoxLayout()
        self.myoma_section_label = theme.set_role(qt.QLabel("MYOMAS · 0"), "section")
        myoma_header.addWidget(self.myoma_section_label)
        myoma_header.addStretch(1)
        self.myoma_edit_hint = theme.set_role(qt.QLabel("Click a box in the views to edit"), "hint-muted")
        myoma_header.addWidget(self.myoma_edit_hint, 0, qt.Qt.AlignBottom)
        blind_layout.addLayout(myoma_header)
        # In place of the empty list until the first box is drawn.
        self.empty_state = qt.QWidget()
        empty_slot = qt.QVBoxLayout(self.empty_state)
        empty_slot.setContentsMargins(0, 0, 0, 0)
        empty_card = theme.set_role(qt.QFrame(), "empty")
        empty_slot.addWidget(empty_card)
        empty_slot.addStretch(1)
        empty_layout = qt.QVBoxLayout(empty_card)
        empty_layout.setContentsMargins(12, 18, 12, 18)
        empty_layout.setSpacing(6)
        empty_title = theme.set_role(qt.QLabel("No boxes yet"), "strong")
        empty_text = theme.set_role(qt.QLabel("Draw a box around each myoma in any view."), "muted")
        empty_text.wordWrap = True
        for label in (empty_title, empty_text):
            label.alignment = qt.Qt.AlignCenter
            empty_layout.addWidget(label)
        self.empty_add_button = theme.set_role(qt.QPushButton("＋  Add myoma box"), "primary")
        self.empty_add_button.connect("clicked()", self.on_add_box)
        empty_layout.addWidget(self.empty_add_button, 0, qt.Qt.AlignHCenter)
        self.empty_state.visible = False
        blind_layout.addWidget(self.empty_state, 1)
        self.lesion_list = theme.set_role(qt.QListWidget(), "rows")
        self.lesion_list.setItemDelegate(theme.make_row_delegate(self.lesion_list))
        self.lesion_list.setMouseTracking(True)
        self.lesion_list.connect("currentRowChanged(int)", self._on_lesion_row_changed)
        self.lesion_list.connect("itemClicked(QListWidgetItem*)", self._on_lesion_item_clicked)
        self._press_hit_current_row = False
        self._lesion_list_filter = self._make_lesion_list_filter()
        self.lesion_list.viewport().installEventFilter(self._lesion_list_filter)
        list_escape = qt.QShortcut(qt.QKeySequence("Esc"), self.lesion_list)
        list_escape.context = qt.Qt.WidgetShortcut
        list_escape.connect("activated()", self.clear_lesion_selection)
        blind_layout.addWidget(self.lesion_list, 1)
        self.box_actions = qt.QWidget()
        actions = qt.QHBoxLayout(self.box_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.add_button = theme.set_role(qt.QPushButton("＋  Add myoma box"), "ghost")
        self.add_button.connect("clicked()", self.on_add_box)
        self.delete_button = qt.QPushButton("Delete")
        self.delete_button.toolTip = "Delete the selected myoma box"
        self.delete_button.connect("clicked()", self.on_delete_box)
        actions.addWidget(self.add_button, 1)
        actions.addWidget(self.delete_button)
        blind_layout.addWidget(self.box_actions)
        # Only shown while a box is being placed or redrawn.
        self.roi_edit_bar = qt.QWidget()
        roi_transaction_actions = qt.QHBoxLayout(self.roi_edit_bar)
        roi_transaction_actions.setContentsMargins(0, 0, 0, 0)
        self.save_roi_button = theme.set_role(qt.QPushButton("Save ROI"), "primary")
        self.save_roi_button.connect("clicked()", self.save_roi_edit)
        self.cancel_roi_button = qt.QPushButton("Cancel ROI")
        self.cancel_roi_button.connect("clicked()", self.cancel_roi_edit)
        roi_transaction_actions.addWidget(self.save_roi_button, 1)
        roi_transaction_actions.addWidget(self.cancel_roi_button)
        blind_layout.addWidget(self.roi_edit_bar)
        self._set_roi_edit_buttons(False)
        # Only asked before reveal; once AI is shown the answer is settled.
        # Folded to one line unless the case has no boxes (_refresh_nothing_to_box).
        self.nothing_to_box_section = theme.set_role(qt.QFrame(), "card")
        nothing_layout = qt.QVBoxLayout(self.nothing_to_box_section)
        nothing_layout.setContentsMargins(10, 6, 10, 8)
        nothing_layout.setSpacing(6)
        blind_layout.addWidget(self.nothing_to_box_section)
        self.nothing_to_box_toggle = theme.set_role(qt.QToolButton(), "fold")
        self.nothing_to_box_toggle.text = "Nothing to box? No uterus, wrong image, cannot assess"
        self.nothing_to_box_toggle.checkable = True
        self.nothing_to_box_toggle.toolButtonStyle = qt.Qt.ToolButtonTextBesideIcon
        self.nothing_to_box_toggle.arrowType = qt.Qt.RightArrow
        self.nothing_to_box_toggle.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
        self.nothing_to_box_toggle.connect("toggled(bool)", self._set_nothing_to_box_open)
        nothing_layout.addWidget(self.nothing_to_box_toggle)
        self.nothing_to_box_body = qt.QWidget()
        body_layout = qt.QVBoxLayout(self.nothing_to_box_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        self.zero_check = theme.set_role(qt.QPushButton(), "choice")
        self.zero_check.checkable = True
        self.zero_check.connect("toggled(bool)", self.on_zero_toggled)
        body_layout.addWidget(self.zero_check)
        self._refresh_zero_text()
        body_layout.addWidget(theme.set_role(qt.QLabel("Or stop this case early:"), "hint-muted"))
        # Early stops answer "is there anything to box at all", so they sit
        # with the zero-myoma confirmation.
        stops = qt.QHBoxLayout()
        stops.setSpacing(6)
        self.early_stop_buttons = []
        for text_value, status in (("No uterus", "no_uterus"), ("Wrong image", "wrong_image"), ("Cannot assess", "cannot_assess")):
            button = qt.QPushButton(text_value)
            button.connect("clicked()", lambda _checked=False, value=status: self.on_early_stop(value))
            stops.addWidget(button, 1)
            self.early_stop_buttons.append(button)
        body_layout.addLayout(stops)
        self.early_stop_confirm = theme.set_role(qt.QFrame(), "confirm")
        confirm_layout = qt.QVBoxLayout(self.early_stop_confirm)
        confirm_layout.setContentsMargins(10, 8, 10, 8)
        confirm_layout.setSpacing(6)
        self.early_stop_question = theme.set_role(qt.QLabel(""), "strong")
        self.early_stop_question.wordWrap = True
        confirm_layout.addWidget(self.early_stop_question)
        confirm_note = theme.set_role(
            qt.QLabel("This is saved as the case's final outcome and the case is closed."), "hint-muted"
        )
        confirm_note.wordWrap = True
        confirm_layout.addWidget(confirm_note)
        confirm_row = qt.QHBoxLayout()
        confirm_row.addStretch(1)
        self.early_stop_cancel_button = qt.QPushButton("Cancel")
        self.early_stop_cancel_button.connect("clicked()", self.cancel_early_stop)
        confirm_row.addWidget(self.early_stop_cancel_button)
        self.early_stop_confirm_button = theme.set_role(qt.QPushButton("End case"), "primary")
        self.early_stop_confirm_button.connect("clicked()", self.on_confirm_early_stop)
        confirm_row.addWidget(self.early_stop_confirm_button)
        confirm_layout.addLayout(confirm_row)
        self.early_stop_confirm.visible = False
        self._pending_early_stop: str | None = None
        body_layout.addWidget(self.early_stop_confirm)
        self.nothing_to_box_body.visible = False
        nothing_layout.addWidget(self.nothing_to_box_body)
        self._nothing_to_box_case: str | None = None
        self.page_stack.addWidget(blind)

        correction = qt.QWidget()
        correction_layout = qt.QVBoxLayout(correction)
        correction_layout.setContentsMargins(0, 0, 0, 0)
        correction_layout.setSpacing(6)
        # Our own segment rows and tools; Slicer's Segment Editor works behind them.
        self.correction_panel = CorrectionPanel(
            ICON_DIR,
            {FIXED_SEGMENT_IDS[value]: FIXED_SEGMENT_NAMES[FIXED_SEGMENT_IDS[value]] for value in (1, 2, 3, 4)},
        )
        correction_layout.addWidget(self.correction_panel.widget)
        correction_layout.addStretch(1)
        self.show_roi_check = qt.QCheckBox("Show ROI boxes")
        self.show_roi_check.checked = False
        self.show_roi_check.connect("toggled(bool)", self.on_show_roi_toggled)
        correction_layout.addWidget(self.show_roi_check)
        self.correction_tools = ReviewCorrectionTools(self)
        self.page_stack.addWidget(correction)

        # --- Action bar: what blocks or follows, then the page's actions ---
        self.action_bar = theme.set_role(qt.QFrame(), "footer")
        footer_layout = qt.QVBoxLayout(self.action_bar)
        footer_layout.setContentsMargins(10, 8, 10, 10)
        footer_layout.setSpacing(8)
        self.status_label = theme.set_role(qt.QLabel("Open one prepared case to begin."), "hint-muted")
        self.status_label.wordWrap = True
        footer_layout.addWidget(self.status_label)
        footer_row = qt.QHBoxLayout()
        footer_row.setSpacing(6)
        footer_row.addWidget(self.comment_toggle)
        self.save_draft_button = qt.QPushButton("Save draft")
        self.save_draft_button.connect("clicked()", self.on_save_draft)
        footer_row.addWidget(self.save_draft_button)
        self.reveal_button = theme.set_role(qt.QPushButton("Reveal AI  →"), "primary")
        self.reveal_button.connect("clicked()", self.on_reveal_ai)
        footer_row.addWidget(self.reveal_button, 1)
        self.complete_button = theme.set_role(qt.QPushButton("Export corrected labels and complete"), "primary")
        self.complete_button.connect("clicked()", self.on_complete)
        footer_row.addWidget(self.complete_button, 1)
        self.reopen_button = qt.QPushButton("Reopen case for editing")
        self.reopen_button.toolTip = (
            "Return this completed case to AI correction. The published results are "
            "backed up, and the case must be completed again."
        )
        self.reopen_button.connect("clicked()", self.on_reopen_case)
        footer_row.addWidget(self.reopen_button, 1)
        self.footer_next_button = theme.set_role(qt.QPushButton("Next case  →"), "primary")
        self.footer_next_button.connect("clicked()", self.on_next_case)
        footer_row.addWidget(self.footer_next_button, 1)
        footer_layout.addLayout(footer_row)
        annotation_layout.addWidget(self.action_bar)
        self._refresh_comment_toggle()
        self.figo_card = FigoCard(
            self._on_card_figo, self._on_card_text, self._on_card_closed, self._position_figo_card
        )
        # The card lives on a slice view, outside this panel's stylesheet.
        self.figo_card.widget.setStyleSheet(theme.CARD_QSS)
        # The per-view Esc shortcuts (_bind_card_escape_shortcuts) overlap the
        # card's own while focus is in the card; whichever Qt picks must close.
        for shortcut in self.figo_card.widget.findChildren(qt.QShortcut):
            shortcut.connect("activatedAmbiguously()", self.close_figo_card)
        self._on_page_changed()

    def _icon_button(self, icon_name: str, tooltip: str, slot: Callable[[], None]) -> Any:
        button = theme.set_role(qt.QToolButton(), "icon")
        button.setIcon(qt.QIcon(str(ICON_DIR / icon_name)))
        button.setIconSize(qt.QSize(16, 16))
        button.setFixedSize(32, 32)
        button.toolTip = tooltip
        button.setAccessibleName(tooltip)
        button.enabled = False
        button.connect("clicked()", slot)
        return button

    def _on_queue_toggled(self, checked: bool) -> None:
        self.content_stack.setCurrentIndex(1 if checked else 0)
        if checked:
            self._refresh_case_list()
            self.case_search.setFocus()

    def _set_roi_edit_buttons(self, active: bool) -> None:
        """Enable Save/Cancel ROI, and show their bar, only while a box is open for editing."""

        self.save_roi_button.enabled = active
        self.cancel_roi_button.enabled = active
        self.roi_edit_bar.visible = active

    def _style_lesion_item(self, item: Any, lesion_id: str, figo: Any) -> None:
        """A myoma row: its ID, and a green FIGO tag or an amber "FIGO ?" one."""

        colours = theme.FIGO_SET_COLOURS if figo is not None else theme.FIGO_MISSING_COLOURS
        item.setData(theme.ROW_TITLE, lesion_id)
        item.setData(theme.ROW_TAG, figo_label(figo))
        item.setData(theme.ROW_COLOURS, list(colours))

    def _refresh_myoma_count(self) -> None:
        count = self.lesion_list.count
        self.myoma_section_label.text = f"MYOMAS · {count}"
        self._refresh_empty_state()

    def _refresh_empty_state(self) -> None:
        """No boxes and still free to draw one: the empty card replaces the list."""

        count = self.lesion_list.count
        editable = bool(self.add_button.enabled) and not self.logic.read_only
        empty = count == 0 and editable
        self.empty_state.visible = empty
        self.empty_add_button.enabled = empty
        self.lesion_list.visible = not empty
        self.box_actions.visible = not empty
        self.myoma_edit_hint.visible = count > 0 and editable

    def _on_page_changed(self) -> None:
        self._refresh_action_bar()
        self._position_comment_overlay()

    def _on_mode_clicked(self, page: int) -> None:
        """Box shows the box/FIGO page, Segment the correction page (after reveal)."""
        if page == SEGMENT_PAGE and self.logic.segmentation_node is None:
            self._refresh_action_bar()
            return
        # A refused switch (a box still being placed) leaves the page as it is.
        self._show_page(page)
        self._refresh_action_bar()

    def _set_hint(self, text_value: str, tone: str) -> None:
        self.status_label.text = text_value
        self._set_role_state(self.status_label, "role", f"hint-{tone}")

    @staticmethod
    def _set_role_state(widget: Any, name: str, value: str) -> None:
        """Set a dynamic property the stylesheet reads, and redraw with it."""
        if widget.property(name) == value:
            return
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_nothing_to_box_open(self, opened: bool) -> None:
        self.nothing_to_box_toggle.blockSignals(True)
        self.nothing_to_box_toggle.checked = bool(opened)
        self.nothing_to_box_toggle.blockSignals(False)
        self.nothing_to_box_toggle.arrowType = qt.Qt.DownArrow if opened else qt.Qt.RightArrow
        self.nothing_to_box_body.visible = bool(opened)

    def _refresh_action_bar(self) -> None:
        """Step states, the banner, and which of the action bar's buttons show."""
        if not hasattr(self, "action_bar"):
            return
        state = self.logic.state
        read_only = self.logic.read_only
        complete_record = bool(
            read_only and (self.logic.final_annotation or {}).get("status") == "complete"
        )
        on_correction = self.page_stack.currentIndex == 1
        revealed = self.logic.segmentation_node is not None
        self.box_mode_button.enabled = state is not None or read_only
        self.segment_mode_button.enabled = revealed
        for page, button in self.mode_buttons.items():
            button.checked = page == self.page_stack.currentIndex
        self.readonly_banner.visible = complete_record
        blind = state is not None and state.phase == ReviewPhase.BLIND.value
        self.reveal_button.visible = not read_only and blind and not on_correction
        self.complete_button.visible = not read_only and on_correction
        self.save_draft_button.visible = not read_only
        self.reopen_button.visible = complete_record
        self.footer_next_button.visible = read_only
        self.footer_next_button.enabled = self.next_case_button.enabled

    def _position_comment_overlay(self) -> None:
        """Lay the open comment box just above the action bar, over the page."""

        box = self.case_comment_box
        if box.isHidden():
            return
        height = box.sizeHint.height()
        bar_top = self.action_bar.mapTo(self.annotation_tab, qt.QPoint(0, 0)).y()
        top = max(bar_top - 6 - height, 0)
        box.setGeometry(12, top, max(self.annotation_tab.width - 24, 120), height)
        box.raise_()

    def _set_comment_overlay_tracking(self, on: bool) -> None:
        """While the box is open: follow the bar's moves, and fold the box
        when the reader clicks anywhere else. Installed only while open, so Qt
        never holds the filter through a Python object that dies first."""

        app = qt.QApplication.instance()
        try:
            if on:
                self.action_bar.installEventFilter(self._comment_overlay_filter)
                app.installEventFilter(self._comment_overlay_filter)
            else:
                self.action_bar.removeEventFilter(self._comment_overlay_filter)
                app.removeEventFilter(self._comment_overlay_filter)
        except Exception:
            pass

    def _fold_comment_if_clicked_outside(self, clicked: Any) -> None:
        if not isinstance(clicked, qt.QWidget):
            # The same press also passes through its window; judge it once,
            # at the widget that receives it.
            return
        for own in (self.case_comment_box, self.comment_toggle):
            # The bar's own click already toggles the box.
            if clicked is own or own.isAncestorOf(clicked):
                return
        # After the click is handled, so it still reaches what was clicked.
        qt.QTimer.singleShot(0, lambda: setattr(self.comment_toggle, "checked", False))

    def _make_comment_overlay_filter(self) -> Any:
        widget = self

        class _CommentOverlayFilter(qt.QObject):
            def eventFilter(self, obj, event):
                kind = event.type()
                if kind == qt.QEvent.MouseButtonPress:
                    widget._fold_comment_if_clicked_outside(obj)
                elif kind in (qt.QEvent.Move, qt.QEvent.Resize) and obj is widget.action_bar:
                    widget._position_comment_overlay()
                return False

        return _CommentOverlayFilter()

    def _focus_annotation_tab(self) -> None:
        """Close the queue drawer once a case is loaded and ready.

        Called after every successful case load (case picked from the list,
        previous/next, case manifest chosen, or launched via the case env var)
        so the reviewer always lands on the review screen, not the browser.
        """
        if hasattr(self, "content_stack"):
            self.queue_toggle.checked = False
            self.content_stack.setCurrentIndex(0)

    def _claim_shortcut_keys(self) -> None:
        """Take our keys away from Slicer's own menu actions while we are open.

        Slicer's Edit > Undo / Redo already own Ctrl+Z / Ctrl+Y. Two shortcuts
        on one key are ambiguous to Qt, which then runs neither, so the menu
        actions lose just those keys until ``_release_shortcut_keys``.
        """

        self._release_shortcut_keys()
        main_window = slicer.util.mainWindow()
        if main_window is None:
            return
        ours = [shortcut.key for shortcut in self.shortcuts if not shortcut.key.isEmpty()]
        for action in main_window.findChildren(qt.QAction):
            current = list(action.shortcuts())
            kept = [
                sequence for sequence in current
                if not any(sequence.matches(key) == qt.QKeySequence.ExactMatch for key in ours)
            ]
            if len(kept) != len(current):
                self._released_action_shortcuts.append((action, current))
                action.setShortcuts(kept)

    def _release_shortcut_keys(self) -> None:
        for action, shortcuts in self._released_action_shortcuts:
            try:
                action.setShortcuts(shortcuts)
            except Exception:
                pass
        self._released_action_shortcuts = []

    def enter(self) -> None:
        self.app_style.enter()
        self._hide_panel_chrome()
        # The module panel re-shows its help box after enter() runs.
        qt.QTimer.singleShot(0, self._hide_panel_chrome)
        self.app_branding.enter()
        # Slicer's module panel resets its layout margins after setup; put ours back.
        self.layout.setContentsMargins(10, 8, 10, 8)
        for shortcut in self.shortcuts:
            shortcut.enabled = True
        self._claim_shortcut_keys()
        if self.mouse_button_dispatcher is not None:
            self.mouse_button_dispatcher.enabled = True
        if self.view_tools is not None:
            self.view_tools.refresh()
        if self.click_selector is not None:
            # Not gated on existing boxes: the reader may draw the first one
            # after coming back, and nothing else re-binds the observers.
            self.click_selector.bind_views()
        self._bind_card_escape_shortcuts()

    def exit(self) -> None:
        self.app_style.exit()
        self.app_branding.exit()
        self._restore_panel_chrome()
        # The card belongs to the module's views; leaving the module saves
        # and closes it rather than leaving it floating over another module.
        self.close_figo_card()
        for shortcut in self.shortcuts:
            shortcut.enabled = False
        self._release_shortcut_keys()
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
        self.close_figo_card()
        if self.segment_editor_widget is not None:
            self.segment_editor_widget.setActiveEffectByName("")
        changed = index != self.page_stack.currentIndex
        self.page_stack.setCurrentIndex(index)
        if changed and index == 0:
            # The FIGO page opens on every box; picking one narrows to it.
            self.clear_lesion_selection()
        self._set_roi_visibility(
            self.box_page_boxes_shown if index == BOX_PAGE else bool(self.show_roi_check.checked)
        )

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
        source = self.annotation_undo if undo else self.annotation_redo
        step_ready = bool(source) and source[-1]["phase"] == self.logic.state.phase
        if not step_ready and not (undo and self._has_unbanked_edit()):
            # Nothing to restore: leave an open card (and a half-made choice
            # on it) alone rather than closing and reopening it.
            return
        # The flush below closes an open card (banking its typed text as its
        # own undo step); bring it back afterwards for the same box, refreshed
        # from the restored state, unless the undo removed that box.
        card = self.figo_card
        reopen = (card.lesion_id, self._figo_card_view) if card is not None and card.is_open else None
        # Give the drag its own undo entry, or this keystroke would throw it
        # away and revert whatever the reader did before it instead.
        self._flush_pending_roi_drag()
        try:
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
        finally:
            if reopen is not None and reopen[0] in self.logic.state.lesions:
                self.set_active_roi(reopen[0])
                self.open_figo_card(*reopen)

    def _has_unbanked_edit(self) -> bool:
        """Whether the flush would add an undo step: a dragged box or card text."""

        card = self.figo_card
        if card is not None and card.has_unsaved_text():
            return True
        lesion_id = self.active_roi_id
        node = self.roi_nodes.get(lesion_id) if lesion_id else None
        previous = self.active_roi_geometry
        if (
            node is None
            or previous is None
            or node.GetScene() is not slicer.mrmlScene
            or not self._roi_edits_allowed()
        ):
            return False
        current = self._roi_geometry_or_none(node)
        return current is not None and any(
            abs(new - old) > 1e-4
            for new, old in zip(
                current.center_ras + current.size_mm, previous.center_ras + previous.size_mm
            )
        )

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
        """Rebuild the drawer's grouped case rows under the filter and search text."""
        if not hasattr(self, "case_list"):
            return
        summary = self.logic.queue_summary
        needle = str(self.case_search.text) if hasattr(self, "case_search") else ""
        if summary is not None:
            groups = case_groups(summary.entries, self._case_filter, needle)
        else:
            # A single-case manifest has no queue statuses; list it plainly.
            groups = [("", [QueueEntry(case_id, None) for case_id in self.logic.available_case_ids
                            if matches_case_search(case_id, needle)])]
            groups = [group for group in groups if group[1]]
        current_row = -1
        self.case_list.blockSignals(True)
        try:
            self.case_list.clear()
            for title, entries in groups:
                if title:
                    header = qt.QListWidgetItem(title)
                    header.setFlags(qt.Qt.NoItemFlags)
                    header.setData(theme.ROW_HEADER, True)
                    header.setData(theme.ROW_TITLE, title)
                    self.case_list.addItem(header)
                for entry in entries:
                    status = entry.status
                    tag = "" if status is None else CASE_STATUS_TITLES[status]
                    if entry.case_id == self.logic.case_id:
                        tag = "open now"
                    item = qt.QListWidgetItem(f"{entry.case_id}    {tag}" if tag else entry.case_id)
                    item.setData(qt.Qt.UserRole, entry.case_id)
                    item.setData(theme.ROW_TITLE, entry.case_id)
                    item.setData(theme.ROW_TAG, tag)
                    if status is not None:
                        dot, tag_bg, tag_fg = theme.STATUS_COLOURS[status.value]
                        item.setData(theme.ROW_COLOURS, [dot, tag_bg, tag_fg])
                    if entry.diagnostic:
                        item.setToolTip(entry.diagnostic)
                        if status is QueueStatus.ATTENTION:
                            item.setData(theme.ROW_SUBTITLE, entry.diagnostic)
                    self.case_list.addItem(item)
                    if entry.case_id == self.logic.case_id:
                        current_row = self.case_list.count - 1
            self.case_empty_label.visible = not groups and bool(self.logic.available_case_ids)
            if current_row >= 0:
                self.case_list.setCurrentRow(current_row)
                self.case_list.scrollToItem(self.case_list.item(current_row))
        finally:
            self.case_list.blockSignals(False)
        self._refresh_progress()

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
        self._refresh_case_nav()
        summary = self.logic.queue_summary
        if summary is None:
            self.progress_label.text = ""
            return
        counts = summary.counts
        self.progress_label.text = queue_strip_text(counts)
        for status, segment in self.progress_segments.items():
            count = counts[status]
            segment.visible = count > 0
            # Stretch factors make each segment's width its share of the cases.
            self.progress_bar.layout().setStretchFactor(segment, count)
        pill_counts = {
            key: sum(counts[status] for status in statuses)
            for key, statuses in CASE_FILTER_STATUSES.items()
        }
        for title, key in CASE_FILTERS:
            self.filter_buttons[key].text = f"{title}  {pill_counts.get(key, 0)}"

    def _refresh_case_nav(self) -> None:
        """Enable previous / next only when an openable case lies that way."""
        summary = self.logic.queue_summary
        case_id = self.logic.case_id
        for button, step in ((self.prev_case_button, -1), (self.next_case_button, +1)):
            button.enabled = bool(summary is not None and case_id and summary.neighbour(case_id, step))

    def on_previous_case(self) -> None:
        self._open_neighbour(-1)

    def on_next_case(self) -> None:
        self._open_neighbour(+1)

    def _open_neighbour(self, step: int) -> None:
        if self.logic.manifest_source is None or not self.logic.case_id:
            return
        # Rescan first: a case may have turned finished or Attention meanwhile.
        summary = self.logic.refresh_queue()
        neighbour = None if summary is None else summary.neighbour(self.logic.case_id, step)
        if neighbour is None:
            self._refresh_case_nav()
            return
        # The same guarded path as picking from the list: unsaved work is
        # saved, and an unfinished box edit blocks the switch.
        self.open_case(self.logic.manifest_source, neighbour.case_id)

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
        # Group headers carry no case.
        selected_case_id = str(item.data(qt.Qt.UserRole) or "")
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
        self._load_case_comment()
        self.shutdown_saved = False
        self.save_coordinator.clear_dirty()
        self._refresh_case_list()
        manifest = self.logic.manifest
        self.study_label.text = self.logic.case_id
        self.case_header.text = f"{manifest.study_id} · Reader {manifest.reader_id}"  # type: ignore[union-attr]
        self.manifest_label.text = (
            f"Manifest: {manifest.study_id} ({len(self.logic.available_case_ids)} cases)"  # type: ignore[union-attr]
        )
        self.manifest_button.text = "Change manifest"
        self.logic.load_registered_volumes()
        self.view_tools.refresh()
        self.roi_boxes_visible = True
        self.box_page_boxes_shown = True
        self.rebuild_roi_nodes()
        if self.click_selector is not None:
            self.click_selector.bind_views()
        self._bind_card_escape_shortcuts()
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
        self.correction_panel.set_read_only(read_only)
        self.correction_tools.refresh()
        # Whether there is anything to box is settled once AI is shown.
        self.nothing_to_box_section.visible = self.logic.segmentation_node is None
        if state is None:
            if not self.logic.read_only or self.logic.final_annotation is None:
                return
            # Filled on load; keeping the row here must not pull the views anywhere.
            selected_id = self._selected_lesion_id()
            self.lesion_list.blockSignals(True)
            self.lesion_list.clear()
            for lesion in self.logic.final_annotation.get("lesions", []):
                lesion_id = str(lesion.get("lesion_id", ""))
                item = qt.QListWidgetItem(f"{lesion_id} · {figo_label(lesion.get('figo'))}")
                item.setData(qt.Qt.UserRole, lesion_id)
                self._style_lesion_item(item, lesion_id, lesion.get("figo"))
                self.lesion_list.addItem(item)
            self._refresh_myoma_count()
            self.lesion_list.setCurrentRow(self._lesion_row(selected_id) if selected_id else -1)
            self.lesion_list.blockSignals(False)
            for control in (
                self.add_button,
                self.delete_button,
                self.zero_check,
                self.reveal_button,
                self.save_draft_button,
                self.complete_button,
                *self.early_stop_buttons,
            ):
                control.enabled = False
            self.reveal_button.toolTip = ""
            self._refresh_roi_arming()
            status = str(self.logic.final_annotation.get("status", "complete")).replace("_", " ")
            if status == "complete":
                self._set_hint(
                    "Reopening backs up the published results. You will have to complete the case again.",
                    "muted",
                )
            else:
                self._set_hint(f"Final outcome: {status}. This record is read-only.", "muted")
            self._refresh_progress()
            self._refresh_action_bar()
            return
        selected_id = self._selected_lesion_id()
        self.lesion_list.blockSignals(True)
        self.lesion_list.clear()
        for lesion in state.active_lesions:
            item = qt.QListWidgetItem(self.roi_label(lesion.lesion_id))
            item.setData(qt.Qt.UserRole, lesion.lesion_id)
            self._style_lesion_item(item, lesion.lesion_id, lesion.to_dict().get("figo"))
            self.lesion_list.addItem(item)
        self._refresh_myoma_count()
        # Nothing is picked for the reader: with no myoma selected every box shows.
        self.lesion_list.setCurrentRow(self._lesion_row(selected_id) if selected_id else -1)
        self.lesion_list.blockSignals(False)
        locked = read_only or state.phase not in (ReviewPhase.BLIND.value, ReviewPhase.AI_REVEALED.value)
        # One page before and after reveal: boxes can still be added, redrawn
        # or deleted once AI is shown; the blinded snapshot stays frozen.
        for control in (self.add_button, self.delete_button):
            control.enabled = not locked
        self._refresh_empty_state()
        self.zero_check.blockSignals(True)
        self.zero_check.checked = state.zero_myomas
        self.zero_check.enabled = not locked and not state.lesions
        self.zero_check.blockSignals(False)
        self._refresh_zero_text()
        self.reveal_button.enabled = bool(state.can_reveal)
        missing = missing_figo_ids(state)
        self.reveal_button.toolTip = f"FIGO missing: {', '.join(missing)}" if missing else ""
        self.save_draft_button.enabled = not self.logic.read_only
        self.complete_button.enabled = state.phase == ReviewPhase.AI_REVEALED.value
        for button in self.early_stop_buttons:
            button.enabled = not locked and state.phase == ReviewPhase.BLIND.value
        if not self.early_stop_buttons[0].enabled:
            self.cancel_early_stop()
        self._refresh_roi_arming()
        # A new case opens "Nothing to box?" when it has no boxes; after that
        # it stays however the reader leaves it.
        if self._nothing_to_box_case != state.case_id:
            self._nothing_to_box_case = state.case_id
            self._set_nothing_to_box_open(state.lesion_count == 0)
        hint, tone = bottom_hint(state.phase, missing, state.lesion_count, state.zero_myomas)
        self._set_hint(hint, tone)
        self._refresh_progress()
        self._refresh_action_bar()
        # Keep an open card's highlight in step with the saved FIGO (say, after
        # an undo); opening/closing is not ours. Only a changed value is pushed,
        # so a half-made 8/Hybrid choice on the card is not reset.
        card = self.figo_card
        if card is not None and card.is_open and card.lesion_id in state.lesions:
            saved = state.lesions[card.lesion_id].to_dict().get("figo")
            if saved != self._figo_card_figo:
                card.set_figo(saved)
                self._figo_card_figo = saved

    def _refresh_roi_arming(self) -> None:
        if self.roi_nodes:
            self._apply_roi_selection()

    def _set_status(self, text_value: str) -> None:
        if hasattr(self, "status_label"):
            self._set_hint(text_value, "muted")

    def _show_error(self, message: str) -> None:
        self._set_status(message)
        if SLICER_AVAILABLE:
            qt.QMessageBox.warning(slicer.util.mainWindow(), "MyomaReviewLite", message)

    def _selected_lesion_id(self) -> str | None:
        item = self.lesion_list.currentItem()
        return str(item.data(qt.Qt.UserRole)) if item is not None else None

    def _lesion_row(self, lesion_id: str | None) -> int:
        for row in range(self.lesion_list.count):
            if str(self.lesion_list.item(row).data(qt.Qt.UserRole)) == lesion_id:
                return row
        return -1

    def _select_lesion_row(self, lesion_id: str) -> None:
        """Make ``lesion_id`` the current row without re-running the row handler."""

        row = self._lesion_row(lesion_id)
        if row < 0:
            return
        self.lesion_list.blockSignals(True)
        self.lesion_list.setCurrentRow(row)
        self.lesion_list.blockSignals(False)

    def clear_lesion_selection(self) -> None:
        """Select no myoma: no row, no box in hand, no card; every box shows again."""

        if self._has_unresolved_roi():
            return
        self.close_figo_card()
        self.lesion_list.blockSignals(True)
        self.lesion_list.setCurrentRow(-1)
        self.lesion_list.clearSelection()
        self.lesion_list.blockSignals(False)
        self.set_active_roi(None)

    def _make_lesion_list_filter(self) -> Any:
        """Note, before Qt moves the current row, whether a press hit that row.

        A click on the row that is already selected (with its card up), or on
        the empty list below the rows, deselects. itemClicked alone cannot
        tell a click on the current row from one that just made it current.
        """

        widget = self

        class _LesionListFilter(qt.QObject):
            def eventFilter(self, obj, event):
                if event.type() == qt.QEvent.MouseButtonPress:
                    item = widget.lesion_list.itemAt(event.pos())
                    widget._press_hit_current_row = (
                        item is not None and widget.lesion_list.row(item) == widget.lesion_list.currentRow
                    )
                    if item is None and widget._lesions_pickable():
                        widget.clear_lesion_selection()
                        return True
                return False

        return _LesionListFilter()

    def _lesions_pickable(self) -> bool:
        """Whether a list pick may select a box: a case is open and no box is being placed."""

        has_case = self.logic.state is not None or (
            self.logic.read_only and self.logic.final_annotation is not None
        )
        return has_case and not self._has_unresolved_roi()

    def _on_lesion_row_changed(self, _index: int) -> None:
        # Picking a myoma from the list selects its box, brings it on screen
        # and opens its card. Not while a box is being placed (every other box
        # is hidden then).
        if not self._lesions_pickable():
            return
        lesion_id = self._selected_lesion_id()
        self._reveal_lesion(lesion_id)
        self.open_figo_card(lesion_id)

    def _on_lesion_item_clicked(self, item: Any) -> None:
        """A click on the row that was already current toggles it.

        With its card up, the click deselects (every box shows again). With
        the card closed by Esc it reopens the card: currentRowChanged does not
        fire then, so the card could otherwise not be brought back.
        """

        hit_current = self._press_hit_current_row
        self._press_hit_current_row = False
        if item is None or not self._lesions_pickable():
            return
        lesion_id = str(item.data(qt.Qt.UserRole))
        card = self.figo_card
        if card is not None and card.is_open and card.lesion_id == lesion_id:
            if hit_current:
                self.clear_lesion_selection()
            return
        self._reveal_lesion(lesion_id)
        self.open_figo_card(lesion_id)

    def _reveal_lesion(self, lesion_id: str | None) -> None:
        """Show one myoma's box: select it and centre the three T2 views on it.

        A box is only visible on the slices it crosses, so without centring a
        myoma picked from the list could leave its box off the current slice.
        """

        if not lesion_id or lesion_id not in self.roi_nodes:
            return
        self.set_active_roi(lesion_id)
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

    def on_add_box(self) -> None:
        if self._has_unresolved_roi():
            self._show_error("Save or cancel the current myoma box first.")
            return
        # Drawing needs the views, so the card goes before the drag starts.
        self.close_figo_card()
        if not self.box_page_boxes_shown:
            # A new box is drawn among the others, so they come back first.
            self.box_page_boxes_shown = True
            self._set_roi_visibility(True)
        # An armed box would follow the drawing drag too, so disarm it first.
        self.set_active_roi(None)
        if self.draw_tool is not None:
            self.draw_tool.start()
            # Hide every other box and swap in the placement cursor now that
            # draw_tool.active is true, i.e. _has_unresolved_roi() flipped.
            self._apply_roi_selection()

    def on_delete_box(self) -> None:
        # Closing first banks the card's typed text as its own undo step.
        self.close_figo_card()
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
                slicer.mrmlScene.RemoveNode(node)
            self._remove_roi_outline(lesion_id)
            self._write_current_draft()
            self.refresh_ui()
        except Exception as exc:
            self._show_error(str(exc))

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
            self._refresh_zero_text()
            self._show_error(str(exc))

    def _refresh_zero_text(self) -> None:
        self.zero_check.text = (
            "✓  No myomas in this case — confirmed" if self.zero_check.checked
            else "No myomas in this case"
        )

    def on_show_myomas(self) -> None:
        if self.logic.segmentation_node is None:
            self._set_status("No segmentation is shown yet; reveal AI first.")
            return
        self.correction_tools.show_myomas()

    def on_save_draft(self) -> None:
        try:
            # Text typed on the card lives only in the card until it closes.
            self.close_figo_card()
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
            and not state.comment
        )

    def _write_current_draft(self) -> None:
        # A box dragged by its handles lives only in the node until this runs.
        self._commit_active_roi_geometry()
        self._commit_case_comment()
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
        markups node has to land there first or it is silently dropped. The
        same goes for Ddx/comment text still sitting in an open FIGO card, so
        the card is closed (which saves that text) first.
        """

        self.close_figo_card()
        self._commit_active_roi_geometry()
        self._commit_case_comment()

    def _commit_case_comment(self) -> None:
        """Fold the typed case comment into the state; the editor is its source."""

        state = self.logic.state
        if state is None or self.logic.read_only or not hasattr(self, "case_comment_edit"):
            return
        text = str(self.case_comment_edit.plainText).strip()
        if text != state.comment:
            state.set_comment(text)

    def _refresh_comment_toggle(self) -> None:
        expanded = bool(self.comment_toggle.checked)
        has_text = bool(str(self.case_comment_edit.plainText).strip())
        self.comment_toggle.text = "Case comment" + (" (written)" if has_text and not expanded else "")
        self.comment_toggle.toolTip = self.comment_toggle.text
        self.comment_toggle.setIcon(qt.QIcon(str(ICON_DIR / ("comment-written.svg" if has_text else "comment.svg"))))

    def on_comment_toggled(self, expanded: bool) -> None:
        self.case_comment_box.visible = bool(expanded)
        self.case_comment_edit.visible = bool(expanded)
        self._set_comment_overlay_tracking(bool(expanded))
        if expanded:
            self._position_comment_overlay()
            self.case_comment_edit.setFocus()
        else:
            try:
                self._write_current_draft()
            except Exception as exc:
                self._show_error(f"Comment could not be saved: {exc}")
        self._refresh_comment_toggle()

    def _load_case_comment(self) -> None:
        state = self.logic.state
        text = state.comment if state is not None else str((self.logic.final_annotation or {}).get("comment", ""))
        self.case_comment_edit.plainText = text
        self.case_comment_edit.readOnly = bool(self.logic.read_only)
        self.comment_toggle.blockSignals(True)
        self.comment_toggle.checked = False
        self.comment_toggle.blockSignals(False)
        self.case_comment_box.visible = False
        self.case_comment_edit.visible = False
        self._set_comment_overlay_tracking(False)
        self._refresh_comment_toggle()

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
        # Before anything else, so typed card text still lands in this case.
        self.close_figo_card()
        self._unobserve_figo_card()
        self.annotation_undo.clear()
        self.annotation_redo.clear()
        if self.correction_tools is not None:
            self.correction_tools.cleanup()
        self._remove_segmentation_observer()
        if self.segment_editor_widget is not None:
            editor = self.segment_editor_widget
            self.correction_panel.unbind()
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
            self.annotation_undo.clear()
            self.annotation_redo.clear()
            self.logic.load_ai_mask()
            self._open_correction_page()
            self.refresh_ui()
        except Exception as exc:
            self._show_error(f"AI reveal failed: {exc}")

    def _open_correction_page(self) -> None:
        # The card never shows on the correction page.
        self.close_figo_card()
        if self.segment_editor_widget is not None:
            return
        self.segment_editor_widget = self.logic.setup_segment_editor(self.correction_panel.editor_host)
        if self.segment_editor_widget is not None:
            self.correction_panel.bind(
                self.segment_editor_widget, self.logic.segment_editor_node, self.logic.segmentation_node
            )
            self.correction_panel.set_read_only(self.logic.read_only)
        self.view_tools.restore_views(reset_orientation=False, fit=False)
        if self.click_selector is not None:
            self.click_selector.bind_views()
        self._set_roi_visibility(False)
        self.page_stack.setCurrentIndex(1)
        self._observe_segmentation()
        self.correction_tools.refresh()

    EARLY_STOP_TITLES = {"no_uterus": "No uterus", "wrong_image": "Wrong image", "cannot_assess": "Cannot assess"}

    def on_early_stop(self, status: str) -> None:
        """Ask in the panel before an early stop ends the case."""
        if self.logic.state is None:
            return
        self._pending_early_stop = status
        self.early_stop_question.text = f"End this case as \u201c{self.EARLY_STOP_TITLES.get(status, status)}\u201d?"
        self.early_stop_confirm_button.text = f"End case: {self.EARLY_STOP_TITLES.get(status, status)}"
        self.early_stop_confirm.visible = True
        for button, value in zip(self.early_stop_buttons, self.EARLY_STOP_TITLES):
            self._set_role_state(button, "role", "primary" if value == status else "")

    def cancel_early_stop(self) -> None:
        self._pending_early_stop = None
        self.early_stop_confirm.visible = False
        for button in self.early_stop_buttons:
            self._set_role_state(button, "role", "")

    def on_confirm_early_stop(self) -> None:
        status = self._pending_early_stop
        self.cancel_early_stop()
        if status is None or self.logic.state is None:
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

        The open FIGO card hides and returns with its box, so it is placed again.
        """

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
                outline.SetDisplayVisibility(
                    bool(self.roi_boxes_visible and not hide_all and not self._roi_hidden_by_focus(identifier))
                )
        # The cursor is the corner-drag affordance, so it only holds for the
        # initial drag; once the square is set and handle fine-tuning takes
        # over (roi_draft_node exists, draw_tool is no longer active) the
        # normal cursor comes back even though the other boxes stay hidden.
        self._update_box_edit_cursor(bool(self.draw_tool is not None and self.draw_tool.active))
        self._position_figo_card()

    def _roi_hidden_by_focus(self, identifier: str) -> bool:
        """On the FIGO page a selected myoma is shown alone; with none selected, all show."""

        on_figo_page = hasattr(self, "page_stack") and self.page_stack.currentIndex == 0
        return bool(on_figo_page and self.active_roi_id is not None and identifier != self.active_roi_id)

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

        if not self.roi_boxes_visible or self._has_unresolved_roi():
            return
        self._last_view_name = view_name
        candidates = []
        for lesion_id, node in self.roi_nodes.items():
            if node.GetScene() is not slicer.mrmlScene:
                continue
            # A box hidden behind the selected one cannot be clicked: a click
            # beside the selected box deselects, and every box shows again.
            if self._roi_hidden_by_focus(lesion_id):
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
            # Empty image: the deselected box takes its card and row with it.
            self.clear_lesion_selection()
            return
        self._select_lesion_row(lesion_id)
        self.open_figo_card(lesion_id, view_name)

    # -- on-image FIGO card ------------------------------------------------
    def _card_payload(self, lesion_id: str) -> dict[str, Any] | None:
        """The saved FIGO/Ddx/comment for one box; a completed case reads its record."""

        state = self.logic.state
        if self.logic.read_only or state is None:
            return next(
                (
                    dict(item)
                    for item in (self.logic.final_annotation or {}).get("lesions", [])
                    if str(item.get("lesion_id")) == lesion_id
                ),
                None,
            )
        lesion = state.lesions.get(lesion_id)
        return lesion.to_dict() if lesion is not None else None

    def roi_label(self, lesion_id: str) -> str:
        """A box's row text: its ID and FIGO status."""

        figo = (self._card_payload(lesion_id) or {}).get("figo")
        return f"{lesion_id} · {figo_label(figo)}"

    def open_figo_card(self, lesion_id: str, view_name: str | None = None) -> None:
        """Show one box's FIGO card beside it, in the view the reader is working in."""

        card = self.figo_card
        if card is None or not hasattr(self, "page_stack") or self.page_stack.currentIndex == 1:
            return
        if not lesion_id or lesion_id not in self.roi_nodes:
            return
        view_name = view_name or self._last_view_name or VIEW_NAMES["sagittal"]
        if card.is_open and card.lesion_id == lesion_id and self._figo_card_view == view_name:
            # Re-opening would throw away a half-made choice (8 without a
            # location, Hybrid with one side) for nothing.
            self._position_figo_card()
            return
        slice_widget = slicer.app.layoutManager().sliceWidget(view_name)
        if slice_widget is None:
            return
        # Close first, so text typed on the old card is saved before this
        # box's values are read (it may be the same box, in another view).
        card.close()
        payload = self._card_payload(lesion_id)
        if payload is None:
            return
        card.open(
            slice_widget.sliceView(),
            lesion_id,
            payload.get("figo"),
            payload.get("differential_diagnosis", ""),
            payload.get("comment", ""),
            bool(self.logic.read_only),
        )
        self._figo_card_view = view_name
        self._figo_card_figo = payload.get("figo")
        self._bind_card_escape_shortcuts()
        self._set_card_escape_enabled(True)
        self._observe_figo_card(lesion_id)
        self._position_figo_card()

    def close_figo_card(self) -> None:
        if self.figo_card is not None:
            self.figo_card.close()

    def _on_card_closed(self) -> None:
        self._unobserve_figo_card()
        self._set_card_escape_enabled(False)
        self._figo_card_view = None

    def _observe_figo_card(self, lesion_id: str) -> None:
        """Follow pans, zooms, slice scrolls and box drags with the card."""

        self._unobserve_figo_card()
        nodes = []
        manager = slicer.app.layoutManager()
        for view_name in VIEW_NAMES.values():
            slice_widget = manager.sliceWidget(view_name)
            if slice_widget is not None:
                nodes.append(slice_widget.mrmlSliceNode())
        roi = self.roi_nodes.get(lesion_id)
        if roi is not None:
            nodes.append(roi)
        for node in nodes:
            tag = node.AddObserver(vtk.vtkCommand.ModifiedEvent, lambda *_: self._position_figo_card())
            self._figo_card_observers.append((node, tag))

    def _unobserve_figo_card(self) -> None:
        for node, tag in self._figo_card_observers:
            try:
                node.RemoveObserver(tag)
            except Exception:
                pass
        self._figo_card_observers = []

    def _position_figo_card(self) -> None:
        """Put the card clear of its box's projection, or where the reader dragged it.

        The box is not drawn off its slices, while the boxes are hidden, or
        while another box is being placed; the card then stays where it was
        (top right of the view if it has not been placed yet) so the reader
        keeps their FIGO choice in sight while scrolling.
        """

        card = self.figo_card
        if card is None or not card.is_open:
            return
        node = self.roi_nodes.get(card.lesion_id) if card.lesion_id else None
        slice_widget = (
            slicer.app.layoutManager().sliceWidget(self._figo_card_view)
            if self._figo_card_view
            else None
        )
        if node is None or slice_widget is None:
            card.widget.visible = False
            return
        outline = self.roi_outline_nodes.get(card.lesion_id)
        drawn = bool(
            self.roi_boxes_visible
            and not self._has_unresolved_roi()
            and (node.GetDisplayVisibility() or (outline is not None and outline.GetDisplayVisibility()))
        )
        if not drawn:
            self._keep_figo_card_up(slice_widget)
            return
        bounds = [0.0] * 6
        node.GetRASBounds(bounds)
        slice_node = slice_widget.mrmlSliceNode()
        # Only on slices that cut the box.
        if not slice_cuts_bounds(slice_node, bounds):
            self._keep_figo_card_up(slice_widget)
            return
        inverse = vtk.vtkMatrix4x4()
        vtk.vtkMatrix4x4.Invert(slice_node.GetXYToRAS(), inverse)
        xs: list[float] = []
        ys: list[float] = []
        for r in bounds[0:2]:
            for a in bounds[2:4]:
                for s in bounds[4:6]:
                    point = inverse.MultiplyPoint([r, a, s, 1.0])
                    xs.append(point[0])
                    ys.append(point[1])
        view = slice_widget.sliceView()
        try:
            ratio = float(view.devicePixelRatioF())
        except Exception:
            ratio = 1.0
        x, y = card_position(
            (min(xs), min(ys), max(xs), max(ys)),
            card.size(),
            (int(view.width), int(view.height)),
            device_pixel_ratio=ratio or 1.0,
        )
        card.move_to(x, y)
        if card.widget.isHidden():
            card.widget.visible = True

    def _keep_figo_card_up(self, slice_widget: Any) -> None:
        """Leave the card where it is while its box is not drawn."""
        card = self.figo_card
        if not card.placed:
            width, _height = card.size()
            card.place_at(max(int(slice_widget.sliceView().width) - width - 8, 0), 8)
        if card.widget.isHidden():
            card.widget.visible = True

    def _bind_card_escape_shortcuts(self) -> None:
        """One Esc shortcut per T2 view; armed only while the card is open.

        The card's own Esc only fires with focus inside the card, but the
        reader is usually working in the view; disarmed, the views keep Esc.
        """

        manager = slicer.app.layoutManager()
        if manager is None:
            return
        armed = self.figo_card is not None and self.figo_card.is_open
        for view_name in VIEW_NAMES.values():
            slice_widget = manager.sliceWidget(view_name)
            if slice_widget is None:
                continue
            existing = self._card_escape_shortcuts.get(view_name)
            if existing is not None:
                try:
                    if existing.parent() is not None:
                        continue
                except Exception:
                    pass
            shortcut = qt.QShortcut(qt.QKeySequence("Esc"), slice_widget)
            shortcut.context = qt.Qt.WidgetWithChildrenShortcut
            shortcut.connect("activated()", self.close_figo_card)
            # With focus inside the card (a child of this view) the card's own
            # Esc matches too; Qt then fires neither, only "ambiguously".
            shortcut.connect("activatedAmbiguously()", self.close_figo_card)
            shortcut.enabled = armed
            self._card_escape_shortcuts[view_name] = shortcut

    def _set_card_escape_enabled(self, enabled: bool) -> None:
        for shortcut in self._card_escape_shortcuts.values():
            try:
                shortcut.enabled = bool(enabled)
            except Exception:
                pass

    def _dispose_figo_card(self) -> None:
        """Take the card and its Esc shortcuts off the views, which outlive this widget."""

        self.close_figo_card()
        self._unobserve_figo_card()
        for shortcut in self._card_escape_shortcuts.values():
            try:
                shortcut.enabled = False
                shortcut.deleteLater()
            except Exception:
                pass
        self._card_escape_shortcuts = {}
        if self.figo_card is not None:
            # Unparent only: the Python wrapper owns the frame, and a
            # deleteLater on top of that deletes it twice and crashes Slicer.
            try:
                self.figo_card.dispose()
                self.figo_card.widget.hide()
                self.figo_card.widget.setParent(None)
            except Exception:
                pass
            self.figo_card = None

    def _on_card_figo(self, lesion_id: str, figo: dict) -> None:
        """Save one FIGO choice made on the card as its own undo step."""

        state = self.logic.state
        if state is None or self.logic.read_only or lesion_id not in state.lesions:
            return
        self._remember_annotation()
        try:
            state.set_figo(lesion_id, figo)
        except Exception as exc:
            self.annotation_undo.pop()
            self._show_error(str(exc))
            return
        self._write_current_draft()
        self.refresh_ui()

    def _on_card_text(self, lesion_id: str, ddx: str, comment: str) -> None:
        """Save the card's Ddx and comment together, as one undo step."""

        state = self.logic.state
        if state is None or self.logic.read_only or lesion_id not in state.lesions:
            return
        self._remember_annotation()
        try:
            state.set_differential_diagnosis(lesion_id, ddx)
            state.set_lesion_comment(lesion_id, comment)
        except Exception as exc:
            self.logic.state = ReviewState.from_draft(self.annotation_undo.pop())
            self._show_error(str(exc))
            return
        self._write_current_draft()
        self.refresh_ui()

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

    def accept_rectangle(
        self, geometry: BoxGeometry, update_lesion_id: str | None, view_name: str | None = None
    ) -> None:
        if self.roi_draft_node is not None:
            raise ValidationError("save or cancel the current ROI edit first")
        if view_name is not None:
            # The saved box's card opens in the view it was drawn in.
            self._last_view_name = view_name
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
            self._set_roi_edit_buttons(True)
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
                slicer.mrmlScene.RemoveNode(old_node)
            self.roi_nodes[lesion_id] = node
            self._stop_live_outline_sync()
            self._remove_draft_outline()
            self._style_roi_display(node, handles=False, outline=False)
            node.SetDisplayVisibility(False)
            self._sync_roi_outline(lesion_id, geometry).SetDisplayVisibility(self.roi_boxes_visible)
            self.roi_draft_node = None
            self.roi_draft_update_id = None
            self._set_roi_edit_buttons(False)
            self.roi_transaction.save()
            self._write_current_draft()
            self.refresh_ui()
            self._select_lesion_row(lesion_id)
        except Exception:
            self.cancel_roi_edit()
            raise
        if not self.logic.read_only:
            self.set_active_roi(lesion_id)
        self.open_figo_card(lesion_id)

    def cancel_roi_edit(self) -> None:
        self._stop_live_outline_sync()
        self._remove_draft_outline()
        if self.roi_draft_node is not None and self.roi_draft_node.GetScene() is slicer.mrmlScene:
            slicer.mrmlScene.RemoveNode(self.roi_draft_node)
        self.roi_draft_node = None
        self.roi_draft_update_id = None
        if hasattr(self, "save_roi_button"):
            self._set_roi_edit_buttons(False)
        self.roi_transaction.cancel()
        # roi_draft_node just cleared, i.e. _has_unresolved_roi() may have
        # flipped false: restore the other boxes and the cursor.
        self._apply_roi_selection()

    def rebuild_roi_nodes(self) -> None:
        self.cancel_roi_edit()
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
        if self.roi_draft_node is not None:
            self.roi_draft_node.SetDisplayVisibility(True)

    def on_toggle_roi_boxes(self) -> None:
        """The "Show / hide boxes" key: every box on the box page (before or
        after reveal), the "Show ROI boxes" switch on the correction page."""
        if self.page_stack.currentIndex == BOX_PAGE:
            has_case = self.logic.state is not None or self.logic.final_annotation is not None
            # A box being drawn or redrawn stays on screen until it is saved.
            if not has_case or self._has_unresolved_roi():
                return
            self.box_page_boxes_shown = not self.box_page_boxes_shown
            self._set_roi_visibility(self.box_page_boxes_shown)
            self._set_status("Boxes shown." if self.box_page_boxes_shown else "Boxes hidden. Press Q to show them.")
            return
        if not self.show_roi_check.enabled:
            return
        self.show_roi_check.checked = not self.show_roi_check.checked

    def _pick_segment(self, label_value: int) -> None:
        """Keys 1-4 pick a label by its value in the mask, on the correction page only."""
        if self.page_stack.currentIndex != SEGMENT_PAGE or self.segment_editor_widget is None:
            return
        self.correction_panel.select_segment(FIXED_SEGMENT_IDS[label_value])

    def on_pick_segment_1(self) -> None:
        self._pick_segment(1)

    def on_pick_segment_2(self) -> None:
        self._pick_segment(2)

    def on_pick_segment_3(self) -> None:
        self._pick_segment(3)

    def on_pick_segment_4(self) -> None:
        self._pick_segment(4)

    def on_toggle_ai_mask(self) -> None:
        """The "Show / hide AI mask" key: once AI is revealed, on either page."""
        if self.logic.segmentation_node is None:
            return
        shown = self.correction_tools.toggle_mask()
        self._set_status("AI mask shown." if shown else "AI mask hidden. Press the key again to show it.")

    def on_show_roi_toggled(self, checked: bool) -> None:
        if self.logic.segmentation_node is not None:
            self._set_roi_visibility(bool(checked))

    def on_application_shutdown(self) -> None:
        if self.shutdown_saved:
            return
        try:
            self.close_figo_card()
            self._write_current_draft()
            self.save_coordinator.clear_dirty()
            self.shutdown_saved = True
        except Exception:
            traceback.print_exc()

    def cleanup(self) -> None:
        if SLICER_AVAILABLE:
            self.exit()
            if hasattr(self, "comment_toggle"):
                self._set_comment_overlay_tracking(False)
            if hasattr(self, "_lesion_list_filter"):
                try:
                    self.lesion_list.viewport().removeEventFilter(self._lesion_list_filter)
                except Exception:
                    pass
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
            self._dispose_figo_card()
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
