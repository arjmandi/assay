"""The frame world's part of the coverage audit: its change signal (the
settled frame against the previous event's), which regions of the grid a
point action (one with integer x and y parameters) has probed on the current
progress unit, and which objects of the current board (4-connected
single-color components, the board-sized background left out) no point
action has ever touched. Region buckets come from the observed frame shape,
never from a constant.

The object ledger answers what the action ledger cannot on a level that
advertises one action: "untried [none]" is true of every click-only level
from its first status. Regions and objects are surfaced in the conclusion gap
phrase and the stall message, and in the status line at a stop (a stall or a
verdict in the notes), not on every status: the handoff review found the
region meter to be constant pressure on click worlds, and the 2026-10-26
diagnosis found its absence at a stop to be the larger cost."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from assay.records import Event

REGION_EDGE = 8
# A component this large a share of the board is background, not an object.
BACKGROUND_SHARE = 0.25
OBJECT_MIN_CELLS = 2
OBJECT_EXAMPLES = 4


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


def _points(events: Sequence[Event], paid_indices: Sequence[int]) -> list[tuple[int, int]]:
    """The (x, y) of every point action on the unit."""
    points: list[tuple[int, int]] = []
    for index in paid_indices:
        data = events[index].data or {}
        x, y = data.get("x"), data.get("y")
        if isinstance(x, int) and isinstance(y, int) and not isinstance(x, bool) and not isinstance(y, bool):
            points.append((x, y))
    return points


def _labels(rows: Sequence[str]) -> tuple[list[list[int]], list[dict[str, Any]]]:
    """4-connected single-color components of a settled frame: a label per
    cell and, per label, its color, size and a representative cell (x, y)."""
    height, width = len(rows), len(rows[0]) if rows else 0
    labels = [[-1] * width for _ in range(height)]
    components: list[dict[str, Any]] = []
    for row in range(height):
        for col in range(width):
            if labels[row][col] != -1:
                continue
            color = rows[row][col]
            label = len(components)
            labels[row][col] = label
            stack = [(row, col)]
            cells = 0
            while stack:
                y, x = stack.pop()
                cells += 1
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < height and 0 <= nx < width and labels[ny][nx] == -1 and rows[ny][nx] == color:
                        labels[ny][nx] = label
                        stack.append((ny, nx))
            components.append({"color": color, "size": cells, "at": (col, row)})
    return labels, components


def _objects(events: Sequence[Event], paid_indices: Sequence[int]) -> dict[str, Any] | None:
    """The current board's objects and which of them a point action on this
    unit has touched; None when the unit used no point action."""
    last = events[-1]
    points = _points(events, paid_indices)
    if not last.frames or not points:
        return None
    rows = last.frames[-1]
    if not rows or not rows[0]:
        return None
    labels, components = _labels(rows)
    board = len(rows) * len(rows[0])
    objects = {
        label
        for label, component in enumerate(components)
        if OBJECT_MIN_CELLS <= component["size"] <= board * BACKGROUND_SHARE
    }
    touched = {
        labels[y][x]
        for x, y in points
        if 0 <= y < len(labels) and 0 <= x < len(labels[0])
    } & objects
    untouched = sorted(objects - touched, key=lambda label: (-components[label]["size"], components[label]["at"]))
    examples = [
        f"color {components[label]['color']} at {components[label]['at'][0]},{components[label]['at'][1]} "
        f"({components[label]['size']} cells)"
        for label in untouched[:OBJECT_EXAMPLES]
    ]
    return {"objects": len(objects), "touched": len(touched), "examples": examples}


def _regions_phrase(events: Sequence[Event], paid_indices: Sequence[int]) -> str | None:
    regions = _regions(events, paid_indices)
    if regions is None or regions["probed"] >= regions["total"]:
        return None
    unprobed = regions["total"] - regions["probed"]
    tail = f" (e.g. {'; '.join(regions['examples'])})" if regions["examples"] else ""
    return f"{unprobed}/{regions['total']} grid regions unprobed{tail}"


def objects_phrase(events: Sequence[Event], paid_indices: Sequence[int]) -> str | None:
    found = _objects(events, paid_indices)
    if found is None or found["touched"] >= found["objects"]:
        return None
    untouched = found["objects"] - found["touched"]
    tail = f" (e.g. {'; '.join(found['examples'])})" if found["examples"] else ""
    return f"{untouched}/{found['objects']} board objects never clicked on this unit{tail}"


def coverage_objects(events: Sequence[Event], paid_indices: Sequence[int]) -> str | None:
    """The object ledger for the status line: what the board holds that no
    point action touched, or that every object was touched; None when the
    unit used no point action."""
    found = _objects(events, paid_indices)
    if found is None:
        return None
    if found["touched"] >= found["objects"]:
        return f"all {found['objects']} board objects clicked on this unit"
    return objects_phrase(events, paid_indices)


def coverage_gap(events: Sequence[Event], paid_indices: Sequence[int]) -> str | None:
    parts = [part for part in (objects_phrase(events, paid_indices), _regions_phrase(events, paid_indices)) if part]
    return "; ".join(parts) if parts else None


def coverage_telemetry(events: Sequence[Event], paid_indices: Sequence[int]) -> dict[str, Any]:
    regions = _regions(events, paid_indices)
    found = _objects(events, paid_indices)
    telemetry = (
        {"regions_probed": 0, "regions_total": 0}
        if regions is None
        else {"regions_probed": regions["probed"], "regions_total": regions["total"]}
    )
    if found is not None:
        telemetry.update({"objects_touched": found["touched"], "objects_total": found["objects"]})
    return telemetry
