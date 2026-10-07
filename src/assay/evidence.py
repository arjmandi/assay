"""The RECENT history lines of status and view: one line per event with the
paid-action counter, the progress unit, the action, the grade mark and what
changed. The dict form lives here; an observation kind renders its own form
(the frame world counts changed cells and animation frames)."""

from __future__ import annotations

from collections.abc import Sequence

from .core import canonical_action
from .extras import kind_for
from .records import Event
from .textobs import changed_count


def history_lines(events: Sequence[Event], count: int = 8) -> list[str]:
    paid = 0
    paid_at: dict[int, int] = {}
    for event in events:
        if event.counts_action:
            paid += 1
        paid_at[event.id] = paid
    lines: list[str] = []
    for event in events[-max(1, count) :]:
        if event.predict_ok is True:
            mark = " ✓"
        elif event.predict_ok is False:
            mark = " ✗"
        else:
            mark = ""
        kind = kind_for(event)
        if kind is not None:
            lines.append(kind.history_line(events, event, paid_at[event.id], mark))
            continue
        previous = events[event.id - 1] if event.id else None
        changed = (
            "start"
            if previous is None or previous.frames is not None
            else f"{changed_count(previous.observation, event.observation)} keys"
        )
        lines.append(
            f"  e{event.id:04d} a{paid_at[event.id]:04d} "
            f"L{min(event.win_levels, event.levels_completed + 1)} {canonical_action(event)}{mark} | {changed} | "
            f"{event.state}"
        )
    return lines
