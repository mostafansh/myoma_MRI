"""What the panel shows, decided without Qt: step bar, hint line, queue drawer."""

from __future__ import annotations

from typing import Mapping, Sequence

from .queue import QueueEntry, QueueStatus

STEP_TITLES = ("Box", "FIGO", "Reveal AI", "Correct")

# Queue-drawer filter pills, in display order, and the one checked on start.
CASE_FILTERS = (
    ("To do", "todo"),
    ("Finished", "finished"),
    ("Attention", "attention"),
    ("All", "all"),
)
DEFAULT_CASE_FILTER = "todo"
CASE_FILTER_STATUSES: dict[str, frozenset[QueueStatus]] = {
    "todo": frozenset({QueueStatus.ATTENTION, QueueStatus.IN_PROGRESS, QueueStatus.NEW}),
    "finished": frozenset({QueueStatus.COMPLETE}),
    "attention": frozenset({QueueStatus.ATTENTION}),
    "all": frozenset(QueueStatus),
}
CASE_GROUPS = (
    ("NEEDS ATTENTION", QueueStatus.ATTENTION),
    ("IN PROGRESS", QueueStatus.IN_PROGRESS),
    ("NEW", QueueStatus.NEW),
    ("FINISHED", QueueStatus.COMPLETE),
)

_DONE_PHASES = {"complete", "terminal"}


def step_states(
    phase: str | None, lesion_count: int, zero_confirmed: bool, missing_figo: int
) -> tuple[str, str, str, str]:
    """"done" / "now" / "todo" for Box, FIGO, Reveal AI and Correct."""

    if phase is None:
        return ("todo",) * 4
    if phase in _DONE_PHASES:
        return ("done",) * 4
    if phase == "ai_revealed":
        return ("done", "done", "done", "now")
    boxed = lesion_count > 0 or zero_confirmed
    classified = zero_confirmed or (lesion_count > 0 and missing_figo == 0)
    done = (boxed, boxed and classified, False, False)
    states = ["done" if flag else "todo" for flag in done]
    states[states.index("todo")] = "now"
    return tuple(states)  # type: ignore[return-value]


def bottom_hint(
    phase: str | None, missing_ids: Sequence[str], lesion_count: int, zero_confirmed: bool
) -> tuple[str, str]:
    """The bottom bar's hint line and its tone ("warning", "ok" or "muted")."""

    if phase == "ai_revealed":
        return "Correct the AI labels, then export and complete.", "muted"
    if phase != "blind":
        return "", "muted"
    if missing_ids:
        verb = "needs" if len(missing_ids) == 1 else "need"
        return f"{', '.join(missing_ids)} {verb} a FIGO type before you can reveal", "warning"
    if zero_confirmed:
        return "No myomas confirmed. Revealing locks your blind read.", "ok"
    if lesion_count == 0:
        return "Box at least one myoma, or confirm there are none", "muted"
    return "All myomas have a FIGO type. Revealing locks your blind read.", "ok"


def matches_case_search(case_id: str, needle: str) -> bool:
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


def case_groups(
    entries: Sequence[QueueEntry], filter_key: str, needle: str
) -> list[tuple[str, list[QueueEntry]]]:
    """The drawer's case list: titled groups in a fixed order, empty ones left out."""

    allowed = CASE_FILTER_STATUSES.get(filter_key, CASE_FILTER_STATUSES["all"])
    groups = []
    for title, status in CASE_GROUPS:
        if status not in allowed:
            continue
        members = [
            entry for entry in entries
            if entry.status is status and matches_case_search(entry.case_id, needle)
        ]
        if members:
            groups.append((title, members))
    return groups


def queue_strip_text(counts: Mapping[QueueStatus, int]) -> str:
    """"12 of 40 finished · 3 need attention" for the collapsed queue strip."""

    total = sum(counts.values())
    text = f"{counts.get(QueueStatus.COMPLETE, 0)} of {total} finished"
    attention = counts.get(QueueStatus.ATTENTION, 0)
    if attention:
        text += f" · {attention} {'needs' if attention == 1 else 'need'} attention"
    return text
