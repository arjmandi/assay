"""The frame world's part of the coverage audit: its change signal (the
settled frame against the previous event's) and which regions of the grid a
point action (one with integer x and y parameters) has probed on the current
progress unit. Region buckets come from the observed frame shape, never from
a constant. Surfaced only inside the conclusion gap phrase and the stall
message, never in the every-status line (the handoff review found the region
meter to be constant pressure on click worlds)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from assay.records import Event

REGION_EDGE = 8


def changed(previous: Event, event: Event) -> bool:
    """Did the world change between two consecutive frame events? The
    settled frame of each compared, and the observation object when either
    event has no frames, exactly as the kernel's coverage audit compared them
    before the signal moved behind the kind."""
    if event.frames is None or previous.frames is None:
        return event.observation != previous.observation
    return event.frames[-1] != previous.frames[-1]


def _regions(events: Sequence[Event], paid_indices: Sequence[int]) -> dict[str, Any] | None:
    last = events[-1]
    if not last.frames:
        return None
    rows = last.frames[-1]
    height, width = len(rows), (len(rows[0]) if rows else 0)
    if not height or not width:
        return None
    columns = max(1, (width + REGION_EDGE - 1) // REGION_EDGE)
    lines = max(1, (height + REGION_EDGE - 1) // REGION_EDGE)
    probed: set[tuple[int, int]] = set()
    point_used = False
    for index in paid_indices:
        data = events[index].data or {}
        x, y = data.get("x"), data.get("y")
        if isinstance(x, int) and isinstance(y, int) and not isinstance(x, bool):
            point_used = True
            probed.add((min(x // REGION_EDGE, columns - 1), min(y // REGION_EDGE, lines - 1)))
    if not point_used:
        return None
    total = columns * lines
    examples: list[str] = []
    for by in range(lines):
        for bx in range(columns):
            if (bx, by) not in probed and len(examples) < 3:
                examples.append(
                    f"x{bx * REGION_EDGE}-{min(width, bx * REGION_EDGE + REGION_EDGE) - 1},"
                    f"y{by * REGION_EDGE}-{min(height, by * REGION_EDGE + REGION_EDGE) - 1}"
                )
    return {"probed": len(probed), "total": total, "examples": examples}


def coverage_gap(events: Sequence[Event], paid_indices: Sequence[int]) -> str | None:
    regions = _regions(events, paid_indices)
    if regions is None or regions["probed"] >= regions["total"]:
        return None
    unprobed = regions["total"] - regions["probed"]
    tail = f" (e.g. {'; '.join(regions['examples'])})" if regions["examples"] else ""
    return f"{unprobed}/{regions['total']} grid regions unprobed{tail}"


def coverage_telemetry(events: Sequence[Event], paid_indices: Sequence[int]) -> dict[str, Any]:
    regions = _regions(events, paid_indices)
    if regions is None:
        return {"regions_probed": 0, "regions_total": 0}
    return {"regions_probed": regions["probed"], "regions_total": regions["total"]}
