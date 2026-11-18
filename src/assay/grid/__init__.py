"""The frame-world extra: what a world whose observation is a grid needs
beyond the kernel.

The kernel keeps frames as rows of hex digits and loads this package only
when an event carries them, so a dict world needs neither it nor pillow. It
gives the agent its eyes on a grid: an image of every settled frame, the
transition worded as moved, vanished and appeared components with a compact
cell diff, the exact board or a crop of it as text, a scene summary, and the
grid helpers of `assay python`.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .. import AssayError
from ..journal import Event, Paths, Run
from ..world import render_action
from .perception import (
    connected_components,
    frame_delta,
    infer_lattice,
    line_graph,
    motion_trace,
    repeated_shapes,
    transition_story,
)

REGION_EDGE = 8
# A stable, high-contrast palette. The numeric grid stays the ground truth.
PALETTE = np.asarray(
    [
        (0, 0, 0), (0, 116, 217), (255, 65, 54), (46, 204, 64), (255, 220, 0), (170, 170, 170),
        (240, 18, 190), (255, 133, 27), (127, 219, 255), (135, 12, 37), (255, 255, 255), (85, 85, 85),
        (177, 13, 201), (127, 255, 212), (57, 204, 204), (255, 110, 199),
    ],
    dtype=np.uint8,
)


def grid_of(event: Event, frame: int = -1) -> np.ndarray:
    """One frame of an event as an integer array; the last one is the settled one."""
    rows = event["frames"][frame]
    cells = np.frombuffer("".join(rows).encode(), dtype=np.uint8).astype(np.int16) - ord("0")
    cells[cells > 9] -= ord("a") - ord("0") - 10
    return cells.reshape(len(rows), len(rows[0]))


def render(paths: Paths, event: Event, frame: int | None = None, *, scale: int = 8) -> Path:
    """The image of the settled frame of an event (or of one animation
    frame), written once under `.assay/images/`."""
    suffix = "settled" if frame is None else f"frame-{frame:03d}"
    target = paths.images / f"event-{event['id']:05d}-{suffix}.png"
    if not target.exists():
        try:
            from PIL import Image
        except ImportError as error:
            raise AssayError(
                "a frame world needs pillow to render its images",
                code="EXTRA_MISSING",
                hint="install it into the interpreter that runs assay: pip install pillow",
            ) from error
        grid = grid_of(event, -1 if frame is None else frame)
        target.parent.mkdir(parents=True, exist_ok=True)
        image = Image.fromarray(PALETTE[grid], mode="RGB")
        image.resize((grid.shape[1] * scale, grid.shape[0] * scale), Image.Resampling.NEAREST).save(target)
    return target.resolve()


# --- text views of a grid --------------------------------------------------------


def _bbox(mask: np.ndarray, margin: int = 1) -> tuple[int, int, int, int]:
    cells = np.argwhere(mask)
    height, width = mask.shape
    if not len(cells):
        return 0, height, 0, width
    return (
        max(0, int(cells[:, 0].min()) - margin),
        min(height, int(cells[:, 0].max()) + margin + 1),
        max(0, int(cells[:, 1].min()) - margin),
        min(width, int(cells[:, 1].max()) + margin + 1),
    )


def _grid_text(grid: np.ndarray, bounds: tuple[int, int, int, int]) -> list[str]:
    top, bottom, left, right = bounds
    return [f"     cols {left}..{right - 1}"] + [
        f"{row:>3}  " + "".join(format(int(cell), "x") for cell in grid[row, left:right]) for row in range(top, bottom)
    ]


def _masked_text(grid: np.ndarray, mask: np.ndarray, bounds: tuple[int, int, int, int]) -> list[str]:
    """The changed cells only, with '·' for the unchanged ones."""
    top, bottom, left, right = bounds
    lines, last = [f"     cols {left}..{right - 1}"], None
    for row in range(top, bottom):
        if not mask[row, left:right].any():
            continue
        if last is not None and row - last > 1:
            lines.append(f"      ⋮ {row - last - 1} unchanged rows")
        lines.append(
            f"{row:>3}  "
            + "".join(format(int(grid[row, col]), "x") if mask[row, col] else "·" for col in range(left, right))
        )
        last = row
    return lines


def _diff(previous: np.ndarray, current: np.ndarray, mask: np.ndarray) -> list[str]:
    box = _bbox(mask, margin=1)
    return [
        "DIFF | before: changed cells only ('·' unchanged)",
        *_masked_text(previous, mask, box),
        "DIFF | after",
        *_masked_text(current, mask, box),
    ]


def transition_lines(before: Event, after: Event) -> list[str]:
    """For the receipt: the last step worded, and a cell diff while it stays compact."""
    previous, current = grid_of(before), grid_of(after)
    lines = ["TRANSITION | last step, worded"]
    lines += [f"  {item}" for item in transition_story(previous, current)["lines"]]
    if previous.shape == current.shape:
        mask = previous != current
        box = _bbox(mask, margin=1)
        if mask.any() and int(mask.sum()) <= 200 and box[1] - box[0] <= 18:
            lines += _diff(previous, current, mask)
    return lines


def _scene(grid: np.ndarray) -> list[str]:
    counts = Counter(int(value) for value in grid.ravel())
    colors = " ".join(f"{color:x}:{count}" for color, count in counts.most_common(8))
    lines = [
        f"SCENE | {grid.shape[0]}x{grid.shape[1]} | colors {colors} | "
        f"monochrome components {len(connected_components(grid))}"
    ]
    lattice = infer_lattice(grid).get("candidates", ())
    if lattice:
        lines.append(
            "LATTICE HYPOTHESES | "
            + " · ".join(
                f"scale {item['scale']} origin(row={item.get('origin', [0, 0])[0]},"
                f"col={item.get('origin', [0, 0])[1]}) support={item['support_fraction']:.2f}"
                for item in lattice[:3]
            )
        )
    for item in repeated_shapes(grid, min_size=2, limit=5):
        centers = " ".join(f"({center[1]:g},{center[0]:g})" for center in item.get("centers", ())[:6])
        lines.append(
            f"REPEATED SHAPE | color={item['color']:x} size={item['size']} "
            f"shape={item['shape'][0]}x{item['shape'][1]} count={item['count']} centers(x,y)={centers}"
        )
    return lines


def _candidates(grid: np.ndarray, limit: int = 12) -> list[dict[str, Any]]:
    """Components worth pointing at, ranked by salience: not the background,
    not a single cell, not a strip along the edge of the screen."""
    components = connected_components(grid)
    background = int(np.argmax(np.bincount(grid.ravel(), minlength=16)))
    height, width = grid.shape
    repeats = Counter((item["color"], *item["shape_key"][:3]) for item in components)
    found = []
    for item in components:
        row0, row1, col0, col1 = item["bbox"]
        tall, wide = row1 - row0 + 1, col1 - col0 + 1
        edge = row0 == 0 or col0 == 0 or row1 == height - 1 or col1 == width - 1
        if item["color"] == background or item["size"] < 2 or item["size"] > grid.size // 4:
            continue
        if edge and (tall >= height // 2 or wide >= width // 2):
            continue
        found.append(
            {
                **item,
                "repeats": repeats[(item["color"], tall, wide, item["shape_key"][2])],
                "density": item["size"] / max(1, tall * wide),
            }
        )
    found.sort(key=lambda item: (-min(item["repeats"], 8), -min(item["size"], 256), -item["density"], item["bbox"]))
    return found[:limit]


def _point_action(run: Run, event: Event) -> str | None:
    """The advertised action that takes a point (integer x and y), if any."""
    advertised = {str(name) for name in event["available_actions"]}
    for action in run.registry["actions"]:
        if set(action["params"]) == {"x", "y"} and (not advertised or action["name"] in advertised):
            return str(action["name"])
    return None


def _crop(value: str, shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    try:
        rows, columns = value.split(",", 1)
        top, bottom = (int(item) for item in rows.split(":", 1))
        left, right = (int(item) for item in columns.split(":", 1))
    except ValueError:
        raise AssayError("crop format is R0:R1,C0:C1, with half-open bounds", code="COMMAND_ARGS") from None
    if not (0 <= top < bottom <= shape[0] and 0 <= left < right <= shape[1]):
        raise AssayError(f"crop must fit rows 0..{shape[0] - 1}, cols 0..{shape[1] - 1}", code="COMMAND_ARGS")
    return top, bottom, left, right


def view_lines(run: Run, index: int, flags: Mapping[str, Any]) -> list[str]:
    """`assay view` on a frame event: the image, what changed since the
    previous settled frame, the board (whole under `full`), the scene, the
    animation frames, the candidates for a point action, an exact crop."""
    events = run.events
    event, grid = events[index], grid_of(events[index])
    previous = grid_of(events[index - 1]) if index else None
    full, frames = bool(flags.get("full")), bool(flags.get("frames"))
    lines = [f"IMAGE | {render(run.paths, event)}"]
    mask = previous != grid if previous is not None and previous.shape == grid.shape else None
    bounds = (0, grid.shape[0], 0, grid.shape[1]) if full or mask is None else _bbox(mask, margin=2)
    if mask is not None:
        lines.append(
            f"SETTLED DIFF | {int(mask.sum())} cells | crop rows {bounds[0]}..{bounds[1] - 1}, "
            f"cols {bounds[2]}..{bounds[3] - 1}"
        )
    if previous is not None:
        lines.append("TRANSITION | since the previous settled frame")
        lines += [f"  {item}" for item in transition_story(previous, grid)["lines"]]
    if mask is not None and not full and not mask.any():
        lines.append("BOARD | settled frame unchanged; --full redraws it")
    else:
        lines += ["BOARD | color indices 0-f", *_grid_text(grid, bounds)]
    if mask is not None and previous is not None and mask.any() and int(mask.sum()) <= mask.size * 3 // 5:
        lines += _diff(previous, grid, mask)
    lines += _scene(grid)
    count = event["n_frames"]
    if count > 1:
        lines.append("ANIMATION | consecutive causal deltas")
        first = previous if previous is not None and previous.shape == grid.shape else grid_of(event, 0)
        sequence = [first, *(grid_of(event, frame) for frame in range(count))]
        for number, trace in enumerate(motion_trace(sequence), 1):
            delta, box = trace["delta"], trace["delta"].get("bbox")
            region = "none" if box is None else f"rows {box[0]}..{box[1]}, cols {box[2]}..{box[3]}"
            flips = ", ".join(
                f"{item['from']:x}->{item['to']:x}×{item['count']}" for item in delta.get("color_changes", ())[:3]
            )
            lines.append(
                f"  frame {number}/{count}: {delta['changed_cells']} changed; {region}" + (f"; {flips}" if flips else "")
            )
            for moved in trace.get("moved", ())[:3]:
                lines.append(
                    f"    MOVED | color={moved['color']:x} size={moved['size']} "
                    f"({moved['from'][1]:g},{moved['from'][0]:g})->({moved['to'][1]:g},{moved['to'][0]:g}) x,y"
                )
            if frames:
                lines += _grid_text(sequence[number], (0, grid.shape[0], 0, grid.shape[1]))
        if frames:
            lines.append("FRAME IMAGES | " + " ".join(str(render(run.paths, event, frame)) for frame in range(count)))
    point = _point_action(run, event)
    if point is not None:
        lines.append("POINT CANDIDATES | salience-ranked components; hypotheses only")
        for item in _candidates(grid):
            top, bottom, left, right = item["bbox"]
            y, x = item["representative"]
            lines.append(
                f"  {point} x={x} y={y} color={item['color']:x} size={item['size']} "
                f"repeats={item['repeats']} bbox=row {top}..{bottom}, col {left}..{right}"
            )
    if flags.get("crop"):
        crop = _crop(str(flags["crop"]), grid.shape)
        lines += [f"EXACT CROP | rows {crop[0]}..{crop[1] - 1}, cols {crop[2]}..{crop[3] - 1}", *_grid_text(grid, crop)]
    return lines


# --- the coverage audit's frame part -----------------------------------------------


def coverage_gap(events: Sequence[Event], paid: Sequence[int]) -> str | None:
    """Which regions of the grid a point action has never probed on the
    current progress unit, once one has been used at all."""
    rows = events[-1]["frames"][-1]
    height, width = len(rows), len(rows[0])
    columns, bands = -(-width // REGION_EDGE), -(-height // REGION_EDGE)
    probed = set()
    for index in paid:
        data = events[index]["data"] or {}
        if isinstance(data.get("x"), int) and isinstance(data.get("y"), int):
            probed.add((min(data["x"] // REGION_EDGE, columns - 1), min(data["y"] // REGION_EDGE, bands - 1)))
    missing = [(bx, by) for by in range(bands) for bx in range(columns) if (bx, by) not in probed]
    if not probed or not missing:
        return None
    examples = "; ".join(
        f"x{bx * REGION_EDGE}-{min(width, (bx + 1) * REGION_EDGE) - 1},"
        f"y{by * REGION_EDGE}-{min(height, (by + 1) * REGION_EDGE) - 1}"
        for bx, by in missing[:3]
    )
    return f"{len(missing)}/{columns * bands} grid regions unprobed (e.g. {examples})"


# --- `assay python` on a frame world -----------------------------------------------


def shortest_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    passable: np.ndarray | Callable[[tuple[int, int]], bool],
    *,
    diagonal: bool = False,
) -> list[tuple[int, int]] | None:
    """Breadth-first search over a passability mask or predicate; cells are (row, col)."""
    if callable(passable):
        allowed = passable
    else:
        mask = np.asarray(passable, dtype=bool)

        def allowed(cell: tuple[int, int]) -> bool:
            return 0 <= cell[0] < mask.shape[0] and 0 <= cell[1] < mask.shape[1] and bool(mask[cell])

    steps = [(-1, 0), (1, 0), (0, -1), (0, 1)] + ([(-1, -1), (-1, 1), (1, -1), (1, 1)] if diagonal else [])
    queue, parent = deque([start]), {start: start}
    while queue:
        cell = queue.popleft()
        if cell == goal:
            route = [cell]
            while route[-1] != start:
                route.append(parent[route[-1]])
            return route[::-1]
        for dr, dc in steps:
            successor = (cell[0] + dr, cell[1] + dc)
            if successor not in parent and allowed(successor):
                parent[successor] = cell
                queue.append(successor)
    return None


def crop(grid: np.ndarray, rows: tuple[int, int], cols: tuple[int, int]) -> np.ndarray:
    return np.asarray(grid)[rows[0] : rows[1], cols[0] : cols[1]].copy()


def sample(grid: np.ndarray, coordinates: Iterable[tuple[int, int]]) -> list[int]:
    return [int(np.asarray(grid)[row, column]) for row, column in coordinates]


def namespace(events: Sequence[Event]) -> dict[str, Any]:
    """The frame half of the `assay python` namespace: the settled grids and
    animation frames as arrays, and the perception helpers."""
    settled = [grid_of(event) for event in events]
    frames = [[grid_of(event, index) for index in range(event["n_frames"])] for event in events]
    return {
        "grid": settled[-1],
        "previous": settled[-2] if len(settled) > 1 else None,
        "settled": settled,
        "frames": frames,
        "transitions": [
            {
                "event": event["id"],
                "action": render_action(event["action"], event["data"]),
                "before": settled[index - 1],
                "after": settled[index],
                "frames": frames[index],
                "state": event["state"],
                "level": event["levels_completed"] + 1,
            }
            for index, event in enumerate(events)
            if index
        ],
        "crop": crop,
        "sample": sample,
        "connected_components": connected_components,
        "frame_delta": frame_delta,
        "infer_lattice": infer_lattice,
        "line_graph": line_graph,
        "motion_trace": motion_trace,
        "repeated_shapes": repeated_shapes,
        "shortest_path": shortest_path,
    }
