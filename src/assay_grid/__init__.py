"""assay_grid, the frame-world extra.

Everything a world whose observation is a grid needs beyond the kernel's frame
encoding: rendering, a scene dossier, perception helpers, the grid claim
forms, the executable-rules tier, and the legacy numbered-action vocabulary.
The kernel selects this package by observation shape (`"frames" in event`,
see `assay.extras`), never by configuration, so the published run directories
keep rendering, inspecting and auditing with no registry change. A dict world
never imports it.

`KIND` is the one object the kernel talks to. Its methods are the extension
points listed in `assay.extras.ObservationKind`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from assay.core import RunPaths, frame_at

from . import analysis, claims, coverage, legacy, render, solve, views
from .perception import build_scene_dossier

__all__ = ["KIND", "FrameKind"]


class FrameKind:
    name = "frames"

    def applies(self, event: Mapping[str, Any]) -> bool:
        return "frames" in event

    # -- claims --------------------------------------------------------------

    def claim_patterns(self) -> Sequence[tuple[str, re.Pattern[str]]]:
        return claims.PATTERNS

    def claim_fields(self, kind: str, match: re.Match[str]) -> dict[str, Any]:
        return claims.claim_fields(kind, match)

    def claims_help(self) -> str:
        return claims.HELP

    def grade_claims(
        self,
        plain: Sequence[Mapping[str, Any]],
        prior_event: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return claims.grade_claims(plain, frame_at(prior_event), event)

    # -- after every recorded event -------------------------------------------

    def after_record(
        self, paths: RunPaths, event: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> None:
        render.render_event(paths, event)
        build_scene_dossier(paths, events)

    # -- display ---------------------------------------------------------------

    def status_head_lines(
        self, paths: RunPaths, event: Mapping[str, Any], registry: Mapping[str, Any] | None
    ) -> list[str]:
        return views.status_head_lines(paths, event, registry)

    def status_lines(
        self, paths: RunPaths, event: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> list[str]:
        return views.rules_lines(paths, event)

    def result_lines(
        self, paths: RunPaths, receipt: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> list[str]:
        return views.result_lines(paths, receipt, events)

    def view_text(
        self, paths: RunPaths, events: Sequence[Mapping[str, Any]], index: int, flags: Mapping[str, Any]
    ) -> str:
        return views.view_text(paths, events, index, flags)

    def history_line(
        self, events: Sequence[Mapping[str, Any]], event: Mapping[str, Any], paid: int, mark: str
    ) -> str:
        return render.history_line(events, event, paid, mark)

    def canonical_action(self, event: Mapping[str, Any]) -> str | None:
        data = event.get("data")
        if data and "x" in data and "y" in data:
            return f"{event['action']}:{data['x']},{data['y']}"
        return None

    def advertised_names(self, event: Mapping[str, Any]) -> list[str]:
        return legacy.advertised_names(event)

    def python_namespace(self, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return analysis.namespace(events)

    # -- the coverage audit's frame part ------------------------------------------

    def coverage_gap(self, events: Sequence[Mapping[str, Any]], paid_indices: Sequence[int]) -> str | None:
        return coverage.coverage_gap(events, paid_indices)

    def coverage_telemetry(self, events: Sequence[Mapping[str, Any]], paid_indices: Sequence[int]) -> dict[str, Any]:
        return coverage.coverage_telemetry(events, paid_indices)

    # -- the legacy numbered-action path ------------------------------------------

    def legacy_parse_action(self, token: str) -> tuple[str, dict[str, Any] | None]:
        return legacy.parse_action(token)

    def legacy_action_token(self, action: str, coordinates: Sequence[str]) -> str:
        return legacy.action_token(action, coordinates)

    def legacy_check_action(self, token: str, available: Sequence[Any]) -> None:
        legacy.check_public_action(token, available)

    def legacy_nudges(self, events: Sequence[Mapping[str, Any]]) -> list[str]:
        return legacy.nudges(events)

    def wall_spend_advice(self, level_actions: int) -> str | None:
        return legacy.wall_spend_advice(level_actions)

    def execute_plan(self, paths: RunPaths, reference: str, at_event: int | None) -> dict[str, Any]:
        return solve.execute_solve_plan(paths, reference, at_event=at_event)

    # -- the command line -----------------------------------------------------------

    def cli_handle(self, paths: RunPaths, args: Any) -> None:
        solve.handle_rules(paths, args)

    def export_history(self, paths: RunPaths, destination: Path) -> Path:
        return views.export_history(paths, destination)


KIND = FrameKind()


def __getattr__(name: str) -> Any:
    # `from assay_grid import connected_components` and friends, for scripts
    # that import the perception helpers by name.
    from . import perception

    if hasattr(perception, name):
        return getattr(perception, name)
    raise AttributeError(name)
