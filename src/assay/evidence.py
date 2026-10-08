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
    the grade mark (a check, a cross, or None when nothing was graded),
    what changed since the previous event, the animation frame count on a
    frame world (None on a dict world), and the state."""

    event: int
    paid: int
    unit: int
    action: str
    mark: str | None
    change: str
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
        if event.predict_ok is True:
            mark: str | None = "✓"
        elif event.predict_ok is False:
            mark = "✗"
        else:
            mark = None
        kind = kind_for(event)
        if kind is not None:
            change = kind.history_change(events, event)
            frames: int | None = len(event.frames or ())
        else:
            previous = events[event.id - 1] if event.id else None
            change = (
                "start"
                if previous is None or previous.frames is not None
                else f"{changed_count(previous.observation, event.observation)} keys"
            )
            frames = None
        lines.append(
            RecentLine(
                event=event.id,
                paid=paid_at[event.id],
                unit=min(event.win_levels, event.levels_completed + 1),
                action=canonical_action(event),
                mark=mark,
                change=change,
                frames=frames,
                state=str(event.state),
            )
        )
    return lines


def history_text(lines: Sequence[RecentLine]) -> list[str]:
    rendered: list[str] = []
    for line in lines:
        mark = f" {line.mark}" if line.mark else ""
        frames = f"frames={line.frames} | " if line.frames is not None else ""
        rendered.append(
            f"  e{line.event:04d} a{line.paid:04d} "
            f"L{line.unit} {line.action}{mark} | {line.change} | {frames}{line.state}"
        )
    return rendered


def history_lines(events: Sequence[Event], count: int = 8) -> list[str]:
    return history_text(recent_lines(events, count))
