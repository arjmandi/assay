"""assay_grid, the frame-world extra.

Everything a world whose observation is a grid needs beyond the kernel's frame
encoding: rendering, a scene dossier, perception helpers and the grid outcome
forms. The kernel selects this package by observation shape (`event.frames`
is set, see `assay.extras`), never by configuration, so the published run
directories keep rendering, inspecting and auditing with no registry change.
A dict world never imports it.

`KIND` is the one object the kernel talks to. Its methods are the extension
points listed in `assay.extras.ObservationKind`; the display and after-record
methods take the run and never reload the journal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assay.core import frame_at
from assay.records import Event, Grade, Outcome, Receipt

from . import analysis, coverage, outcomes, render, views
from .perception import build_scene_dossier

if TYPE_CHECKING:
    from assay.run import Run

__all__ = ["KIND", "FrameKind"]


class FrameKind:
    name = "frames"

    def applies(self, event: Event) -> bool:
        return event.frames is not None

    # -- outcomes --------------------------------------------------------------

    def outcome_patterns(self) -> Sequence[tuple[str, re.Pattern[str]]]:
        return outcomes.PATTERNS

    def outcome_fields(self, kind: str, match: re.Match[str]) -> dict[str, Any]:
        return outcomes.outcome_fields(kind, match)

    def prediction_help(self) -> str:
        return outcomes.HELP

    def grade_outcomes(
        self, plain: Sequence[Outcome], prior_event: Event, event: Event
    ) -> list[Grade]:
        return outcomes.grade_outcomes(plain, frame_at(prior_event), event)

    # -- after every recorded event -------------------------------------------

    def after_record(self, run: Run, event: Event) -> None:
        render.render_event(run.paths, event)
        build_scene_dossier(run)

    # -- display ---------------------------------------------------------------

    def status_head_lines(self, run: Run, event: Event) -> list[str]:
        return views.status_head_lines(run, event)

    def result_lines(self, run: Run, receipt: Receipt) -> list[str]:
        return views.result_lines(run, receipt)

    def view_text(self, run: Run, index: int, flags: Mapping[str, Any]) -> str:
        return views.view_text(run, index, flags)

    def history_change(self, events: Sequence[Event], event: Event) -> tuple[int | None, str]:
        return render.history_change(events, event)

    def canonical_action(self, event: Event) -> str | None:
        data = event.data
        if data and "x" in data and "y" in data:
            return f"{event.action}:{data['x']},{data['y']}"
        return None

    def advertised_names(self, event: Event) -> list[str]:
        return views.advertised_names(event)

    def python_namespace(self, events: Sequence[Event]) -> dict[str, Any]:
        return analysis.namespace(events)

    # -- the coverage audit's frame part ------------------------------------------

    def changed(self, previous: Event, event: Event) -> bool:
        return coverage.changed(previous, event)

    def coverage_gap(self, events: Sequence[Event], paid_indices: Sequence[int]) -> str | None:
        return coverage.coverage_gap(events, paid_indices)

    def coverage_telemetry(self, events: Sequence[Event], paid_indices: Sequence[int]) -> dict[str, Any]:
        return coverage.coverage_telemetry(events, paid_indices)

    # -- the command line -----------------------------------------------------------

    def export_history(self, run: Run, destination: Path) -> Path:
        return views.export_history(run, destination)


KIND = FrameKind()


def __getattr__(name: str) -> Any:
    # `from assay_grid import connected_components` and friends, for scripts
    # that import the perception helpers by name.
    from . import perception

    if hasattr(perception, name):
        return getattr(perception, name)
    raise AttributeError(name)
