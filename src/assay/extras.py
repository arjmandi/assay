"""The hook through which an observation kind extends the kernel.

The kernel knows two observation shapes: a JSON object under `observation`
(every world) and a list of integer grids under `frames` (frame worlds). The
journal format has both, so the frame encoding stays in `core`. Everything
else a frame world wants (rendering, a scene dossier, perception helpers, the
grid claim forms) lives in the extra package `assay_grid`, selected here by
observation shape
and never by configuration: registries are pinned per run and the published
run directories carry no such key.

A dict run never imports `assay_grid`, so the kernel can be loaded without
pillow and without the extra at all: the parser declares the frame-only
`view` flags itself, and the claim help lists the extra's forms only
when help is rendered. A frame run that cannot import the extra gets one clear
refusal instead of a traceback.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .core import AssayError
from .records import Claim, Event, Grade, Receipt

if TYPE_CHECKING:
    from .run import Run


class ObservationKind(Protocol):
    """What an observation kind provides. `assay_grid.KIND` is the one
    implementation; a second kind would implement the same names. The display
    and after-record methods take the run and never reload the journal."""

    name: str

    def applies(self, event: Event) -> bool: ...

    # Claims: extra patterns, their field extraction, their grader, the help.
    def claim_patterns(self) -> Sequence[tuple[str, re.Pattern[str]]]: ...
    def claim_fields(self, kind: str, match: re.Match[str]) -> dict[str, Any]: ...
    def claims_help(self) -> str: ...
    def grade_claims(
        self, claims: Sequence[Claim], prior_event: Event, event: Event
    ) -> list[Grade]: ...

    # After every recorded event: render, dossier.
    def after_record(self, run: Run, event: Event) -> None: ...

    # Display.
    def status_head_lines(self, run: Run, event: Event) -> list[str]: ...
    def result_lines(self, run: Run, receipt: Receipt) -> list[str]: ...
    def view_text(self, run: Run, index: int, flags: Mapping[str, Any]) -> str: ...
    def history_line(
        self, events: Sequence[Event], event: Event, paid: int, mark: str
    ) -> str: ...
    def canonical_action(self, event: Event) -> str | None: ...
    def advertised_names(self, event: Event) -> list[str]: ...
    def python_namespace(self, events: Sequence[Event]) -> dict[str, Any]: ...

    # CLI: the kernel declares the frame-only view flags (inert on a dict
    # run); the kind exports the grid history.
    def export_history(self, run: Run, destination: Path) -> Path: ...


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


def kind_for(event: Event | None) -> ObservationKind | None:
    """The observation kind of one event, or None for the dict shape. Imports
    the extra lazily and only when an event actually has frames."""
    if event is not None and event.frames is not None:
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


def require_kind(event: Event | None, what: str) -> ObservationKind:
    kind = kind_for(event)
    if kind is None:
        raise AssayError(f"{what} applies to frame worlds; this run has dict observations")
    return kind
