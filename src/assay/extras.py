"""The hook through which an observation kind extends the kernel.

The kernel knows two observation shapes: a JSON object under `observation`
(every world) and a list of integer grids under `frames` (frame worlds). The
journal format has both, so the frame encoding stays in `core`. Everything
else a frame world wants (rendering, a scene dossier, perception helpers, grid
claim forms, the executable-rules tier, the legacy numbered-action vocabulary)
lives in the extra package `assay_grid`, selected here by observation shape
and never by configuration: registries are pinned per run and the published
run directories carry no such key.

A dict run never imports `assay_grid`, so the kernel can be loaded without
pillow and without the extra at all: the parser declares the frame-only
subcommand and flags itself, and the claim help lists the extra's forms only
when help is rendered. A frame run that cannot import the extra gets one clear
refusal instead of a traceback.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from .core import AssayError, RunPaths


class ObservationKind(Protocol):
    """What an observation kind provides. `assay_grid.KIND` is the one
    implementation; a second kind would implement the same names."""

    name: str

    def applies(self, event: Mapping[str, Any]) -> bool: ...

    # Claims: extra patterns, their field extraction, their grader, the help.
    def claim_patterns(self) -> Sequence[tuple[str, re.Pattern[str]]]: ...
    def claim_fields(self, kind: str, match: re.Match[str]) -> dict[str, Any]: ...
    def claims_help(self) -> str: ...
    def grade_claims(
        self,
        claims: Sequence[Mapping[str, Any]],
        prior_event: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> list[dict[str, Any]]: ...

    # After every recorded event: render, dossier.
    def after_record(
        self, paths: RunPaths, event: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> None: ...

    # Display.
    def status_head_lines(
        self, paths: RunPaths, event: Mapping[str, Any], registry: Mapping[str, Any] | None
    ) -> list[str]: ...
    def status_lines(
        self, paths: RunPaths, event: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> list[str]: ...
    def result_lines(
        self, paths: RunPaths, receipt: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> list[str]: ...
    def view_text(
        self, paths: RunPaths, events: Sequence[Mapping[str, Any]], index: int, flags: Mapping[str, Any]
    ) -> str: ...
    def history_line(
        self, events: Sequence[Mapping[str, Any]], event: Mapping[str, Any], paid: int, mark: str
    ) -> str: ...
    def canonical_action(self, event: Mapping[str, Any]) -> str | None: ...
    def advertised_names(self, event: Mapping[str, Any]) -> list[str]: ...
    def python_namespace(self, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]: ...

    # The legacy numbered-action path (runs without a registry).
    def legacy_parse_action(self, token: str) -> tuple[str, dict[str, Any] | None]: ...
    def legacy_action_token(self, action: str, coordinates: Sequence[str]) -> str: ...
    def legacy_check_action(self, token: str, available: Sequence[Any]) -> None: ...
    def legacy_nudges(self, events: Sequence[Mapping[str, Any]]) -> list[str]: ...
    def wall_spend_advice(self, level_actions: int) -> str | None: ...
    def execute_plan(self, paths: RunPaths, reference: str, at_event: int | None) -> dict[str, Any]: ...

    # CLI: the kernel declares the frame-only subcommand and flags (inert on
    # a dict run); the kind handles them and exports the grid history.
    def cli_handle(self, paths: RunPaths, args: Any) -> None: ...
    def export_history(self, paths: RunPaths, destination: Path) -> Path: ...


_MISSING = (
    "this run has grid observations and the frame-world extra (assay_grid) is "
    "not importable: install the package with its grid extra "
    "(pip install -e '.[grid]') or run with the repository's src on the path"
)


def _grid_kind() -> ObservationKind:
    try:
        from assay_grid import KIND
    except ImportError as error:
        raise AssayError(f"{_MISSING} ({error})") from error
    return KIND


def kind_for(event: Mapping[str, Any] | None) -> ObservationKind | None:
    """The observation kind of one event, or None for the dict shape. Imports
    the extra lazily and only when an event actually has frames."""
    if event is not None and "frames" in event:
        return _grid_kind()
    return None


def all_kinds() -> list[ObservationKind]:
    """Every importable kind, for help text and the parser. An extra that is
    not installed is simply absent here; it is only required by a run that
    has its observations."""
    try:
        from assay_grid import KIND
    except ImportError:
        return []
    return [KIND]


def require_kind(event: Mapping[str, Any] | None, what: str) -> ObservationKind:
    kind = kind_for(event)
    if kind is None:
        raise AssayError(f"{what} applies to frame worlds; this run has dict observations")
    return kind
