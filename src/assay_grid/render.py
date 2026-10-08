"""Rendering for frame worlds: the palette, PNG images per event, and the
frame form of the history line. This is the one place pillow is imported.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from assay.core import AssayError, RunPaths, canonical_action, frame_at
from assay.records import Event

if TYPE_CHECKING:
    from assay.run import Run

# A stable, high-contrast rendering palette. The numeric grid remains ground truth.
PALETTE = np.asarray(
    [
        (0, 0, 0),
        (0, 116, 217),
        (255, 65, 54),
        (46, 204, 64),
        (255, 220, 0),
        (170, 170, 170),
        (240, 18, 190),
        (255, 133, 27),
        (127, 219, 255),
        (135, 12, 37),
        (255, 255, 255),
        (85, 85, 85),
        (177, 13, 201),
        (127, 255, 212),
        (57, 204, 204),
        (255, 110, 199),
    ],
    dtype=np.uint8,
)


def image_path(paths: RunPaths, event_id: int, frame_id: int | None = None) -> Path:
    suffix = "settled" if frame_id is None else f"frame-{frame_id:03d}"
    return paths.state / "images" / f"event-{event_id:05d}-{suffix}.png"


def render_grid(grid: np.ndarray[Any, Any], destination: Path, *, scale: int = 8) -> Path:
    array = np.asarray(grid, dtype=np.int16)
    if array.ndim != 2 or (
        array.size and (int(array.min()) < 0 or int(array.max()) > 15)
    ):
        raise AssayError("cannot render a grid with colors outside 0..15", code="CORRUPT_RECORD")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = Image.fromarray(PALETTE[array].astype(np.uint8), mode="RGB")
    image = image.resize(
        (array.shape[1] * scale, array.shape[0] * scale), Image.Resampling.NEAREST
    )
    image.save(destination)
    return destination.resolve()


def render_event(paths: RunPaths, event: Event, *, all_frames: bool = False) -> list[Path]:
    event_id = event.id
    output = [render_grid(frame_at(event), image_path(paths, event_id))]
    if all_frames:
        output.extend(
            render_grid(frame_at(event, index), image_path(paths, event_id, index))
            for index in range(len(event.frames or ()))
        )
    return output


def current_image(run: Run) -> Path:
    events = run.events
    if not events:
        raise AssayError("timeline is empty", code="TIMELINE_EMPTY")
    destination = image_path(run.paths, events[-1].id)
    if not destination.exists():
        render_event(run.paths, events[-1])
    return destination.resolve()


def history_line(events: Sequence[Event], event: Event, paid: int, mark: str) -> str:
    grid = frame_at(event)
    previous = frame_at(events[event.id - 1]) if event.id else None
    changed = (
        "start"
        if previous is None or previous.shape != grid.shape
        else f"{int(np.count_nonzero(previous != grid))} cells"
    )
    return (
        f"  e{event.id:04d} a{paid:04d} "
        f"L{min(event.win_levels, event.levels_completed + 1)} {canonical_action(event)}{mark} | {changed} | "
        f"frames={len(event.frames or ())} | {event.state}"
    )
