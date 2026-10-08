"""The journal meters the display and the behavior modules share: the events
of the current progress unit and the paid actions among them, the recent
prediction window, and the specificity counts. Pure functions over the held
events: `inspect` renders them on the status lines and `modules` reads them
for its advisories, so each exists once and the two surfaces cannot drift."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from .records import Event


def unit_indices(events: Sequence[Event]) -> list[int]:
    """Indices of the events on the current progress unit, in order: the
    tail of the journal whose `levels_completed` equals the last event's."""
    if not events:
        return []
    completed = events[-1].levels_completed
    indices: list[int] = []
    for index in range(len(events) - 1, -1, -1):
        if events[index].levels_completed != completed:
            break
        indices.append(index)
    return list(reversed(indices))


def level_action_count(events: Sequence[Event]) -> int:
    """Paid actions on the current progress unit."""
    return sum(1 for index in unit_indices(events) if events[index].counts_action)


def recent_predictions(events: Sequence[Event], window: int = 10) -> tuple[int, int]:
    """(hits, graded) over the last `window` graded predictions."""
    graded = [event for event in events if event.predict_ok is not None]
    recent = graded[-window:]
    hits = sum(1 for event in recent if event.predict_ok)
    return hits, len(recent)


@dataclasses.dataclass(frozen=True, slots=True)
class Specificity:
    """The specificity counts over every grade of the run. `graded` is every
    grade but a note; `coerced` the free-text claims coerced to `change`,
    counted among the gradable grades as the CLAIMS line always counted
    them; `machine` the kernel-generated predictions of model-plan steps.
    The CLAIMS line reads `specific` over `graded`; the specificity module
    reads the agent's own claims, `graded` less `machine`, since a machine
    prediction is never the agent's vagueness."""

    graded: int
    coerced: int
    machine: int

    @property
    def specific(self) -> int:
        return self.graded - self.coerced

    @property
    def agent_graded(self) -> int:
        return self.graded - self.machine


def specificity(events: Sequence[Event]) -> Specificity:
    """The one count behind the CLAIMS line's specificity and the
    specificity module's advisory: the share of graded claims that are not
    coerced free text."""
    graded = coerced = machine = 0
    for event in events:
        for item in event.grade:
            if item.kind == "note":
                continue
            graded += 1
            if item.machine:
                machine += 1
            if item.invalid or item.ungradable:
                continue
            if item.kind == "coerced":
                coerced += 1
    return Specificity(graded=graded, coerced=coerced, machine=machine)
