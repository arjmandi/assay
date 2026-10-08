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
`view` flags itself, the claim help lists the extra's forms only
when help is rendered, and the extra's claim forms are known here by name
and shape (`FRAME_FORMS`), so a dict run refuses one before any spend
whether or not the extra is installed. A frame run that cannot import the
extra gets one clear refusal instead of a traceback.
"""

from __future__ import annotations

import dataclasses
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
    def history_change(self, events: Sequence[Event], event: Event) -> str: ...
    def canonical_action(self, event: Event) -> str | None: ...
    def advertised_names(self, event: Event) -> list[str]: ...
    def python_namespace(self, events: Sequence[Event]) -> dict[str, Any]: ...

    # The coverage audit's change signal for this shape: did the settled
    # observation change between two consecutive events?
    def changed(self, previous: Event, event: Event) -> bool: ...

    # CLI: the kernel declares the frame-only view flags (inert on a dict
    # run); the kind exports the grid history.
    def export_history(self, run: Run, destination: Path) -> Path: ...


@dataclasses.dataclass(frozen=True, slots=True)
class ClaimForm:
    """A claim form an observation kind owns: the kind's name, the claim
    kind and the pattern, known to the kernel so a run of another shape
    refuses the form by name without importing the kind."""

    kind: str
    name: str
    pattern: re.Pattern[str]


FRAME_KIND = "frames"

# The frame world's forms (verify/CLAIM_GRAMMAR.md), the one home of their
# patterns: `assay_grid.claims` reads them back for its own parsing and
# grading. Only the exact shape names a form, so prose that opens with one of
# the words (`move on`, `cell division`) stays commentary, as it always did.
FRAME_FORMS: tuple[ClaimForm, ...] = (
    ClaimForm(
        FRAME_KIND,
        "cell",
        re.compile(r"^cell\s+(\d+)\s*,\s*(\d+)\s*=\s*([0-9a-fA-F])$", re.IGNORECASE),
    ),
    ClaimForm(
        FRAME_KIND,
        "move",
        re.compile(
            r"^move\s+(\d+)\s*,\s*(\d+)\s+([+-]?\d+)\s*,\s*([+-]?\d+)$", re.IGNORECASE
        ),
    ),
    ClaimForm(FRAME_KIND, "vanish", re.compile(r"^vanish\s+(\d+)\s*,\s*(\d+)$", re.IGNORECASE)),
    ClaimForm(
        FRAME_KIND,
        "region",
        re.compile(r"^region\s+(\d+)\s*:\s*(\d+)\s*,\s*(\d+)\s*:\s*(\d+)$", re.IGNORECASE),
    ),
)


def foreign_form(part: str, kind: ObservationKind | None) -> ClaimForm | None:
    """The form of another observation kind that `part` spells, if any: what
    a run refuses by name before any spend (`predictions.parse_claims`).
    Reads the table above and imports nothing."""
    for form in FRAME_FORMS:
        if kind is not None and kind.name == form.kind:
            continue
        if form.pattern.match(part):
            return form
    return None


def refusal_text(form: ClaimForm, part: str) -> str:
    """The refusal of a claim in another kind's form, as the published
    journals' rule words it."""
    return (
        f"claim {part!r} is a {form.kind}-world form and this run does "
        "not admit it (frame-world forms are not admitted in 1.2.0)"
    )


_MISSING = (
    "this run has grid observations and the frame-world extra (assay_grid) is "
    "not importable: install the package with its grid extra "
    "(pip install -e '.[grid]') or run with the repository's src on the path"
)


def _grid_kind() -> ObservationKind:
    try:
        from assay_grid import KIND
    except ImportError as error:
        raise AssayError(f"{_MISSING} ({error})", code="EXTRA_MISSING") from error
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
        raise AssayError(f"{what} applies to frame worlds; this run has dict observations", code="COMMAND_ARGS")
    return kind
