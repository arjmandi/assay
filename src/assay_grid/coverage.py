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
diagnosis found its absence at a stop to be the larger cost.

The object ledger leaves out components on the frame's edge and on HUD rows
(rows that change on nearly every paid action, a draining counter): a third
of its 2026-10-26 examples were the background or a bar.

The regime ledger answers what both cannot: whether an action was tried in
the regime a conclusion needs. The mover is the small color that arrives in
cells when a non-point action changes the frame; what it covered is the color
it stands on. For each click it records the clicked cell's color at test time
and the color under the mover, and for each non-point action that is not a
move the color under the mover and the last move before it. At a stop it
prints the pairs the unit showed but never exercised, color matches first
(the pink control pressed while the mover stands on pink): on 2026-10-27 the
missing pair was the whole break in four games."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from assay.records import Event

REGION_EDGE = 8
# A component this large a share of the board is background, not an object.
BACKGROUND_SHARE = 0.25
OBJECT_MIN_CELLS = 2
OBJECT_EXAMPLES = 4
# A row that changes on this share of the unit's paid transitions is a HUD
# row (a counter that drains per action), judged over at least this many.
HUD_SHARE = 0.8
HUD_MIN_TRANSITIONS = 5
# The mover is a color that arrived in at least this many non-point moves.
MOVER_MIN_MOVES = 3
REGIME_EXAMPLES = 4
# A color covering more than this share of the frame is a floor.
FLOOR_SHARE = 0.1


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
            top, bottom, left, right = row, row, col, col
            while stack:
                y, x = stack.pop()
                cells += 1
                top, bottom, left, right = min(top, y), max(bottom, y), min(left, x), max(right, x)
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < height and 0 <= nx < width and labels[ny][nx] == -1 and rows[ny][nx] == color:
                        labels[ny][nx] = label
                        stack.append((ny, nx))
            components.append({
                "color": color, "size": cells, "at": (col, row),
                "edge": top == 0 or left == 0 or bottom == height - 1 or right == width - 1,
                "rows": (top, bottom),
            })
    return labels, components


def _is_object(component: dict[str, Any], board: int, hud: set[int]) -> bool:
    """A board object: neither a speck nor the background, off the frame's
    edge, and not lying on HUD rows alone."""
    top, bottom = component["rows"]
    return (
        OBJECT_MIN_CELLS <= component["size"] <= board * BACKGROUND_SHARE
        and not component["edge"]
        and not all(row in hud for row in range(top, bottom + 1))
    )


def _settled(event: Event) -> Sequence[Any] | None:
    return event.frames[-1] if event.frames else None


def _point(event: Event) -> tuple[int, int] | None:
    data = event.data or {}
    x, y = data.get("x"), data.get("y")
    if isinstance(x, int) and isinstance(y, int) and not isinstance(x, bool) and not isinstance(y, bool):
        return x, y
    return None


def _transitions(events: Sequence[Event], paid_indices: Sequence[int]) -> list[tuple[int, Sequence[Any], Sequence[Any]]]:
    """(index, before, after) for every paid event of the unit whose settled
    frame and its predecessor's have the same shape."""
    found = []
    for index in paid_indices:
        if index == 0:
            continue
        before, after = _settled(events[index - 1]), _settled(events[index])
        if before is not None and after is not None and len(before) == len(after) and before:
            found.append((index, before, after))
    return found


def hud_rows(events: Sequence[Event], paid_indices: Sequence[int]) -> set[int]:
    """The rows that change on nearly every paid transition of the unit."""
    moves = _transitions(events, paid_indices)
    if len(moves) < HUD_MIN_TRANSITIONS:
        return set()
    counts: Counter[int] = Counter()
    for _, before, after in moves:
        counts.update(row for row, (old, new) in enumerate(zip(before, after)) if old != new)
    return {row for row, count in counts.items() if count >= HUD_SHARE * len(moves)}


def _arrivals(before: Sequence[Any], after: Sequence[Any], hud: set[int]) -> list[tuple[int, int, Any, Any]]:
    """(x, y, old, new) of every cell that changed, off the HUD rows."""
    cells = []
    for row, (old_row, new_row) in enumerate(zip(before, after)):
        if row in hud or old_row == new_row:
            continue
        for col, (old, new) in enumerate(zip(old_row, new_row)):
            if old != new:
                cells.append((col, row, old, new))
    return cells


def _mover(
    events: Sequence[Event], moves: Sequence[tuple[int, Sequence[Any], Sequence[Any]]], hud: set[int]
) -> Any:
    """The mover's color: of the colors that arrived in at least
    MOVER_MIN_MOVES non-point moves, the one with the fewest cells on the
    current frame (the background arrives as often, and is large)."""
    arrived: Counter[Any] = Counter()
    for index, before, after in moves:
        if _point(events[index]) is None:
            arrived.update({new for _, _, _, new in _arrivals(before, after, hud)})
    current = _settled(events[-1]) or ()
    sizes: Counter[Any] = Counter(cell for row in current for cell in row)
    candidates = [color for color, count in arrived.items() if count >= MOVER_MIN_MOVES and sizes[color] > 0]
    return min(candidates, key=lambda color: (sizes[color], str(color))) if candidates else None


def regime_ledger(events: Sequence[Event], paid_indices: Sequence[int]) -> dict[str, Any] | None:
    """The regimes the unit's paid actions were exercised in: per click
    target (the current frame's component under the click), the colors it
    had and the colors under the mover when clicked, against the colors it
    showed and the mover stood on; per non-point action that is not a move,
    the colors under the mover and the moves before it, against those seen.
    None when the unit has no frames."""
    if not events or _settled(events[-1]) is None:
        return None
    hud = hud_rows(events, paid_indices)
    moves = _transitions(events, paid_indices)
    mover = _mover(events, moves, hud)
    current = _settled(events[-1]) or ()
    labels, components = _labels(current)
    under: Any = None
    last_move: str | None = None
    stood: set[Any] = set()
    directions: set[str] = set()
    clicked: dict[int, dict[str, Any]] = {}
    # The clicks issued from each color the mover stood on: a regime the
    # player was already clicking from is the nearest miss.
    busy: Counter[Any] = Counter()
    shown: dict[int, set[Any]] = {}
    used: dict[str, dict[str, Any]] = {}
    by_index = {index: (before, after) for index, before, after in moves}
    for index in paid_indices:
        event = events[index]
        point = _point(event)
        prior = _settled(events[index - 1]) if index else None
        if point is not None:
            x, y = point
            if prior is not None and 0 <= y < len(prior) and 0 <= x < len(prior[y]) and 0 <= y < len(labels) and 0 <= x < len(labels[0]):
                target = labels[y][x]
                record = clicked.setdefault(target, {"colors": set(), "under": set(), "cells": set(), "count": 0})
                record["count"] += 1
                record["colors"].add(prior[y][x])
                record["cells"].add((x, y))
                if under is not None:
                    record["under"].add(under)
                    busy[under] += 1
        elif index in by_index and mover is not None:
            before, after = by_index[index]
            covered = [old for _, _, old, new in _arrivals(before, after, hud) if new == mover and old != mover]
            if covered:
                under = Counter(covered).most_common(1)[0][0]
                last_move = str(event.action).upper()
                stood.add(under)
                directions.add(last_move)
                continue
        if point is None:
            name = str(event.action).upper()
            if name != "RESET":
                record = used.setdefault(name, {"under": set(), "after": set(), "count": 0})
                record["count"] += 1
                if under is not None:
                    record["under"].add(under)
                if last_move is not None:
                    record["after"].add(last_move)
    for target, record in clicked.items():
        shown[target] = {
            frame[y][x]
            for index in paid_indices
            if (frame := _settled(events[index])) is not None
            for x, y in record["cells"]
            if 0 <= y < len(frame) and 0 <= x < len(frame[y])
        }
    # Floors are not regimes: a color covering this share of the frame is
    # where the mover walks, not what it stands on.
    board = sum(len(row) for row in current) or 1
    sizes: Counter[Any] = Counter(cell for row in current for cell in row)
    stood = {color for color in stood if sizes[color] <= board * FLOOR_SHARE}
    pairs: list[tuple[bool, int, int, str]] = []
    for target, record in clicked.items():
        component = components[target]
        if not _is_object(component, board, hud):
            continue
        name = f"{component['at'][0]},{component['at'][1]} (color {component['color']})"
        weight = record["count"]
        for color in sorted(shown[target] - record["colors"], key=str):
            pairs.append((False, 0, weight, f"{name} never clicked while color {color}"))
        for color in sorted(stood - record["under"], key=str):
            match = color in record["colors"] or color == component["color"]
            pairs.append((match, busy[color], weight, f"{name} never clicked with the mover on color {color}"))
    for action, record in sorted(used.items()):
        if action in directions:
            continue
        for color in sorted(stood - record["under"], key=str):
            pairs.append((False, busy[color], record["count"], f"{action} never used with the mover on color {color}"))
        for move in sorted(directions - record["after"]):
            pairs.append((False, 0, record["count"], f"{action} never used after {move}"))
    pairs.sort(key=lambda pair: (not pair[0], -pair[1], -pair[2]))
    return {"mover": mover, "untested": [text for *_, text in pairs], "hud_rows": len(hud)}


def regimes_phrase(events: Sequence[Event], paid_indices: Sequence[int]) -> str | None:
    found = regime_ledger(events, paid_indices)
    if found is None or not found["untested"]:
        return None
    shown = found["untested"][:REGIME_EXAMPLES]
    more = len(found["untested"]) - len(shown)
    return (
        f"{len(found['untested'])} regime pair(s) the unit showed and never exercised: "
        + "; ".join(shown)
        + (f"; and {more} more" if more else "")
    )


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
    hud = hud_rows(events, paid_indices)
    objects = {label for label, component in enumerate(components) if _is_object(component, board, hud)}
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
    parts = [
        part
        for part in (
            regimes_phrase(events, paid_indices),
            objects_phrase(events, paid_indices),
            _regions_phrase(events, paid_indices),
        )
        if part
    ]
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
    regimes = regime_ledger(events, paid_indices)
    if regimes is not None:
        telemetry["regimes_untested"] = len(regimes["untested"])
    return telemetry
