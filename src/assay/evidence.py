"""The RECENT history lines of status and view: one line per event with the
paid-action counter, the progress unit, the action, the grade mark and what
changed. The dict form lives here; an observation kind renders its own form
(the frame world counts changed cells and animation frames)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .core import canonical_action
from .extras import kind_for
from .textobs import changed_count


def history_lines(events: Sequence[Mapping[str, object]], count: int = 8) -> list[str]:
    paid = 0
    paid_at: dict[int, int] = {}
    for event in events:
        if event.get("counts_action"):
            paid += 1
        paid_at[int(event["id"])] = paid
    lines: list[str] = []
    for event in events[-max(1, count) :]:
        if event.get("predict_ok") is True:
            mark = " ✓"
        elif event.get("predict_ok") is False:
            mark = " ✗"
        else:
            mark = ""
        kind = kind_for(event)
        if kind is not None:
            lines.append(kind.history_line(events, event, paid_at[int(event["id"])], mark))
            continue
        previous = events[int(event["id"]) - 1] if int(event["id"]) else None
        changed = (
            "start"
            if previous is None or "frames" in previous
            else f"{changed_count(previous['observation'], event['observation'])} keys"
        )
        lines.append(
            f"  e{int(event['id']):04d} a{paid_at[int(event['id'])]:04d} "
            f"L{min(int(event['win_levels']), int(event['levels_completed']) + 1)} {canonical_action(event)}{mark} | {changed} | "
            f"{event['state']}"
        )
    return lines
