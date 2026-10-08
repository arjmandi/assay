"""The RECENT history lines of status and view: one record per event with
the paid-action counter, the progress unit, the action, the grade mark and
what changed, rendered by one function. The dict change text lives here; an
observation kind computes its own (the frame world counts changed cells, and
its line carries the animation frame count)."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from .core import canonical_action
from .extras import kind_for
from .records import Event
from .textobs import changed_count


@dataclasses.dataclass(frozen=True, slots=True)
class RecentLine:
    """One history line's facts: the event, the paid-action counter after
    it, the progress unit it was on, the action as the receipt names it,
    whether the prediction held (None when nothing was graded), how much
    changed since the previous event (None at the start, else a count in
    `changed_unit`, the noun the observation kind supplies: keys on a dict
    world, cells on a frame world), the animation frame count on a frame
    world (None on a dict world), and the state."""

    event: int
    paid: int
    unit: int
    action: str
    predict_ok: bool | None
    changed: int | None
    changed_unit: str
    frames: int | None
    state: str


def recent_lines(events: Sequence[Event], count: int = 8) -> list[RecentLine]:
    paid = 0
    paid_at: dict[int, int] = {}
    for event in events:
        if event.counts_action:
            paid += 1
        paid_at[event.id] = paid
    lines: list[RecentLine] = []
    for event in events[-max(1, count) :]:
        kind = kind_for(event)
        if kind is not None:
            changed, changed_unit = kind.history_change(events, event)
            frames: int | None = len(event.frames or ())
        else:
            previous = events[event.id - 1] if event.id else None
            changed = (
                None
                if previous is None or previous.frames is not None
                else changed_count(previous.observation, event.observation)
            )
            changed_unit = "keys"
            frames = None
        lines.append(
            RecentLine(
                event=event.id,
                paid=paid_at[event.id],
                unit=min(event.win_levels, event.levels_completed + 1),
                action=canonical_action(event),
                predict_ok=event.predict_ok,
                changed=changed,
                changed_unit=changed_unit,
                frames=frames,
                state=str(event.state),
            )
        )
    return lines


def history_text(lines: Sequence[RecentLine]) -> list[str]:
    rendered: list[str] = []
    for line in lines:
        if line.predict_ok is True:
            mark = " ✓"
        elif line.predict_ok is False:
            mark = " ✗"
        else:
            mark = ""
        change = "start" if line.changed is None else f"{line.changed} {line.changed_unit}"
        frames = f"frames={line.frames} | " if line.frames is not None else ""
        rendered.append(
            f"  e{line.event:04d} a{line.paid:04d} "
            f"L{line.unit} {line.action}{mark} | {change} | {frames}{line.state}"
        )
    return rendered


def history_lines(events: Sequence[Event], count: int = 8) -> list[str]:
    return history_text(recent_lines(events, count))
