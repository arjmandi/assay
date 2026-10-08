"""The frame halves of status, result, inspect, view and export: board text,
settled and animation diffs, the scene summary, click candidates, the
advertised-action line, and the npz export."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from assay.core import AssayError, canonical_action, frame_at
from assay.records import Event, Receipt
from assay.registry import budget_line
from assay.words import progress_text

if TYPE_CHECKING:
    from assay.run import Run

from .perception import (
    connected_components,
    infer_lattice,
    motion_trace,
    repeated_shapes,
    transition_story,
)
from .render import current_image, render_event


def _bbox(mask: np.ndarray[Any, Any], margin: int = 1) -> tuple[int, int, int, int]:
    cells = np.argwhere(mask)
    height, width = mask.shape
    if not len(cells):
        return 0, height, 0, width
    top = max(0, int(cells[:, 0].min()) - margin)
    bottom = min(height, int(cells[:, 0].max()) + margin + 1)
    left = max(0, int(cells[:, 1].min()) - margin)
    right = min(width, int(cells[:, 1].max()) + margin + 1)
    return top, bottom, left, right


def _grid_text(grid: np.ndarray[Any, Any], bounds: tuple[int, int, int, int]) -> str:
    top, bottom, left, right = bounds
    rows = [f"     cols {left}..{right - 1}"]
    rows.extend(
        f"{row:>3}  "
        + "".join(format(int(cell), "x") for cell in grid[row, left:right])
        for row in range(top, bottom)
    )
    return "\n".join(rows)


def _masked_grid_text(
    grid: np.ndarray[Any, Any], mask: np.ndarray[Any, Any], bounds: tuple[int, int, int, int]
) -> str:
    top, bottom, left, right = bounds
    lines = [f"     cols {left}..{right - 1}"]
    active = [row for row in range(top, bottom) if bool(mask[row, left:right].any())]
    last: int | None = None
    for row in active:
        if last is not None and row - last > 1:
            lines.append(f"      ⋮ {row - last - 1} unchanged rows")
        lines.append(
            f"{row:>3}  "
            + "".join(
                format(int(grid[row, col]), "x") if mask[row, col] else "·"
                for col in range(left, right)
            )
        )
        last = row
    return "\n".join(lines)


def _salient_components(grid: np.ndarray[Any, Any], limit: int = 12) -> list[dict[str, Any]]:
    """Rank neutral click hypotheses without flooding the output with pixel noise."""
    components = connected_components(grid)
    counts = np.bincount(grid.ravel(), minlength=16)
    background = int(np.argmax(counts))
    height, width = grid.shape
    shape_counts = Counter(
        (
            item["color"],
            item["shape_key"][0],
            item["shape_key"][1],
            item["shape_key"][2],
        )
        for item in components
    )
    candidates = []
    for item in components:
        if item["color"] == background or item["size"] < 2:
            continue
        if item["size"] > grid.size // 4:
            continue
        row0, row1, col0, col1 = item["bbox"]
        box_height, box_width = row1 - row0 + 1, col1 - col0 + 1
        # Long screen-edge strips are usually chrome or framing, not a useful
        # representative click point. They remain visible in the board itself.
        frame_like = (box_height >= height // 2 or box_width >= width // 2) and (
            row0 == 0 or col0 == 0 or row1 == height - 1 or col1 == width - 1
        )
        if frame_like:
            continue
        key = (item["color"], box_height, box_width, item["shape_key"][2])
        density = item["size"] / max(1, box_height * box_width)
        candidates.append(
            {
                **item,
                "repeat_count": shape_counts[key],
                "density": density,
            }
        )
    candidates.sort(
        key=lambda item: (
            -min(int(item["repeat_count"]), 8),
            -min(int(item["size"]), 256),
            -float(item["density"]),
            item["bbox"],
        )
    )
    return candidates[:limit]


def _scene_summary(grid: np.ndarray[Any, Any]) -> list[str]:
    counts = Counter(int(value) for value in grid.ravel())
    components = connected_components(grid)
    repeated = repeated_shapes(grid, min_size=2, limit=5)
    colors = " ".join(f"{color:x}:{count}" for color, count in counts.most_common(8))
    lines = [
        f"SCENE | {grid.shape[0]}x{grid.shape[1]} | colors {colors} | monochrome components {len(components)}",
    ]
    lattice = infer_lattice(grid).get("candidates", ())
    if lattice:
        guesses = []
        for item in lattice[:3]:
            origin = item.get("origin", [0, 0])
            guesses.append(
                f"scale {item['scale']} origin(row={origin[0]},col={origin[1]}) support={item['support_fraction']:.2f}"
            )
        lines.append("LATTICE HYPOTHESES | " + " · ".join(guesses))
    for item in repeated:
        centers = " ".join(
            f"({center[1]:g},{center[0]:g})" for center in item.get("centers", ())[:6]
        )
        lines.append(
            f"REPEATED SHAPE | color={item['color']:x} size={item['size']} shape={item['shape'][0]}x{item['shape'][1]} count={item['count']} centers(x,y)={centers}"
        )
    return lines


def advertised_names(event: Event) -> list[str]:
    """A frame world advertises bare action ids; registered names carry the
    ACTION prefix. The affordance check compares names, so render the ids."""
    return [f"ACTION{int(value)}" for value in event.available_actions]


def available_line(event: Event) -> str:
    # Bare action numbers: semantics are earned by acting, never assumed.
    return "ACTIONS | available: " + (
        " · ".join(str(number) for number in event.available_actions) or "none"
    ) + " · RESET (built-in)"


def status_head_lines(run: Run, event: Event) -> list[str]:
    images = render_event(run.paths, event)
    return [f"IMAGE | {images[0]}", available_line(event)]


def result_lines(run: Run, receipt: Receipt) -> list[str]:
    """After a paid command on a frame world: the worded transition, a compact
    cell diff, the image, the available actions and the budget."""
    events = run.events
    event = events[-1]
    lines: list[str] = []
    end = receipt.end_event
    if event.id == end and end > 0:
        previous = frame_at(events[end - 1])
        current = frame_at(event)
        story = transition_story(previous, current)
        lines.append("TRANSITION | last step, worded")
        lines.extend(f"  {item}" for item in story["lines"])
        if previous.shape == current.shape:
            mask = previous != current
            box = _bbox(mask, margin=1)
            # Cell-exact compare inline only while it stays compact; `assay view`
            # renders the same DIFF for bigger transitions.
            if bool(mask.any()) and int(mask.sum()) <= 200 and box[1] - box[0] <= 18:
                lines.extend(
                    [
                        "DIFF | before: changed cells only ('·' unchanged)",
                        _masked_grid_text(previous, mask, box),
                        "DIFF | after",
                        _masked_grid_text(current, mask, box),
                    ]
                )
    lines.append(f"IMAGE | {current_image(run)}")
    lines.append(available_line(event))
    registry = run.registry
    if registry:
        lines.append(budget_line(registry, events))
    return lines


def inspect_text(
    run: Run,
    index: int,
    *,
    full: bool = False,
    frames: bool = False,
) -> str:
    events = run.events
    event = events[index]
    grid = frame_at(event)
    previous = frame_at(events[index - 1]) if index else None
    lines = [
        f"RUN | event {index} | {progress_text(event)} | paid actions {sum(1 for item in events if item.counts_action)} | state {event.state}",
        f"CAUSE | {canonical_action(event)} | frames {len(event.frames or ())}",
        available_line(event),
    ]
    changed: np.ndarray[Any, Any] | None = None
    if previous is not None and previous.shape == grid.shape:
        changed = previous != grid
        bounds = (
            (0, grid.shape[0], 0, grid.shape[1]) if full else _bbox(changed, margin=2)
        )
        lines.append(
            f"SETTLED DIFF | {int(changed.sum())} cells | crop rows {bounds[0]}..{bounds[1] - 1}, cols {bounds[2]}..{bounds[3] - 1}"
        )
    else:
        bounds = (0, grid.shape[0], 0, grid.shape[1])
    if previous is not None:
        story = transition_story(previous, grid)
        lines.append("TRANSITION | since previous settled board")
        lines.extend(f"  {item}" for item in story["lines"])
    if changed is not None and not full and not bool(changed.any()):
        lines.append("BOARD | settled frame unchanged; use --grid to redraw it")
    else:
        lines.extend(["BOARD | color indices 0-f", _grid_text(grid, bounds)])

    if (
        changed is not None
        and previous is not None
        and bool(changed.any())
        and int(changed.sum()) <= changed.size * 3 // 5
    ):
        diff_bounds = _bbox(changed, margin=1)
        lines.extend(
            [
                "DIFF | before: changed cells only ('·' unchanged)",
                _masked_grid_text(previous, changed, diff_bounds),
                "DIFF | after",
                _masked_grid_text(grid, changed, diff_bounds),
            ]
        )

    lines.extend(_scene_summary(grid))

    frame_count = len(event.frames or ())
    if frame_count > 1:
        lines.append("ANIMATION | consecutive causal deltas")
        prior_frame = (
            previous
            if previous is not None and previous.shape == grid.shape
            else frame_at(event, 0)
        )
        animation_frames = [prior_frame] + [
            frame_at(event, index) for index in range(frame_count)
        ]
        traces = motion_trace(animation_frames)
        for frame_index, trace in enumerate(traces):
            delta = trace["delta"]
            box = delta.get("bbox")
            region = (
                "none"
                if box is None
                else f"rows {box[0]}..{box[1]}, cols {box[2]}..{box[3]}"
            )
            changes = ", ".join(
                f"{item['from']:x}->{item['to']:x}×{item['count']}"
                for item in delta.get("color_changes", ())[:3]
            )
            lines.append(
                f"  frame {frame_index + 1}/{frame_count}: {delta['changed_cells']} changed; {region}"
                + (f"; {changes}" if changes else "")
            )
            for moved in trace.get("moved", ())[:3]:
                before_center, after_center = moved["from"], moved["to"]
                lines.append(
                    f"    MOVED | color={moved['color']:x} size={moved['size']} "
                    f"({before_center[1]:g},{before_center[0]:g})->({after_center[1]:g},{after_center[0]:g}) x,y"
                )
            if frames:
                current = animation_frames[frame_index + 1]
                lines.append(
                    _grid_text(current, (0, current.shape[0], 0, current.shape[1]))
                )

    if 6 in event.available_actions:
        candidates = _salient_components(grid)
        lines.append(
            "CLICK CANDIDATES | salience-ranked monochrome representatives; hypotheses only"
        )
        for item in candidates:
            top, bottom, left, right = item["bbox"]
            y, x = item["representative"]
            lines.append(
                f"  ACTION6:{x},{y} color={item['color']:x} size={item['size']} repeats={item['repeat_count']} "
                f"bbox=row {top}..{bottom}, col {left}..{right}"
            )
    return "\n".join(lines)


def view_text(run: Run, index: int, flags: Mapping[str, Any]) -> str:
    """`assay view` on a frame world: image, inspect, frame images, an exact
    crop, history. The history line is appended by the caller."""
    event = run.events[index]
    grid = bool(flags.get("grid"))
    frames = bool(flags.get("frames"))
    crop = parse_crop(flags.get("crop"))
    images = render_event(run.paths, event, all_frames=frames)
    lines = [
        f"IMAGE | {images[0]}",
        inspect_text(run, index, full=grid, frames=frames),
    ]
    if frames and len(images) > 1:
        lines.append("FRAME IMAGES | " + " ".join(str(path) for path in images[1:]))
    if crop is not None:
        array = frame_at(event)
        top, bottom, left, right = crop
        if not (
            0 <= top < bottom <= array.shape[0] and 0 <= left < right <= array.shape[1]
        ):
            raise AssayError(
                f"crop must fit rows 0..{array.shape[0] - 1}, cols 0..{array.shape[1] - 1}",
                code="COMMAND_ARGS",
            )
        lines.extend(
            [
                f"EXACT CROP | rows {top}..{bottom - 1}, cols {left}..{right - 1}",
                _grid_text(array, crop),
            ]
        )
    return "\n".join(lines)


def parse_crop(value: str | None) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    try:
        rows, columns = value.split(",", 1)
        top, bottom = (int(item) for item in rows.split(":", 1))
        left, right = (int(item) for item in columns.split(":", 1))
        return top, bottom, left, right
    except (ValueError, TypeError):
        raise AssayError(
            "crop format is R0:R1,C0:C1, using half-open bounds",
            code="COMMAND_ARGS",
        ) from None


def export_history(run: Run, destination: Path) -> Path:
    paths = run.paths
    if destination.suffix.lower() != ".npz":
        raise AssayError("observation export filename must end in .npz", code="COMMAND_ARGS")
    try:
        destination.resolve().relative_to(paths.root.resolve())
    except ValueError as error:
        raise AssayError(
            "observation export must stay inside the run directory",
            code="PATH_INVALID",
        ) from error
    try:
        destination.resolve().relative_to(paths.state.resolve())
    except ValueError:
        pass
    else:
        raise AssayError("do not write analysis output inside .assay", code="PATH_INVALID")
    events = run.events
    if events and events[-1].frames is None:
        raise AssayError(
            "this run has dict observations; there is no grid history to export",
            code="COMMAND_ARGS",
        )
    frames: list[np.ndarray[Any, Any]] = []
    frame_events: list[int] = []
    for event in events:
        for index in range(len(event.frames or ())):
            frames.append(frame_at(event, index))
            frame_events.append(event.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        destination,
        frames=np.stack(frames),
        frame_events=np.asarray(frame_events, dtype=np.int32),
        settled=np.stack([frame_at(event) for event in events]),
        actions=np.asarray([canonical_action(event) for event in events]),
        levels=np.asarray(
            [event.levels_completed for event in events], dtype=np.int16
        ),
        states=np.asarray([event.state for event in events]),
        available=np.asarray(
            [
                [number in event.available_actions for number in range(1, 8)]
                for event in events
            ],
            dtype=bool,
        ),
    )
    return destination.resolve()
