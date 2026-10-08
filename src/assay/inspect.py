"""Result, inspect and view for every world, and the status as one rendering
of the `Status` record (`status.py`, docs/ARCHITECTURE.md section 7.4). The
dict forms live here; the frame forms come from the observation kind
(`assay.extras`), selected per event by its shape. Every function takes the
run and renders the journal it holds; the small files beside it are read on
demand."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .core import AssayError, canonical_action
from .evidence import history_lines
from .extras import kind_for
from .records import Event, Receipt
from .registry import budget_line, gate_mode, gate_text, status_budget
from .status import actions_text, claims_text, observation_text, render_status, status_of
from .textobs import LINE_LIMIT, delta_lines, pretty_cuts
from .words import progress_text

if TYPE_CHECKING:
    from .run import Run

# The lines a receipt shows of the observation at most (status shows 48).
RECEIPT_OBSERVATION_LINES = 40


def _general_actions_line(event: Event, registry: Mapping[str, Any] | None) -> str:
    return actions_text(
        [str(value) for value in event.available_actions],
        [item["name"] for item in registry.get("actions", ())] if registry else None,
    )


def _observation_lines(event: Event, max_lines: int = 48) -> list[str]:
    return observation_text(event.observation, max_lines)


def _observation_note(event: Event, max_lines: int) -> str | None:
    """The receipt's line on what its observation block left out
    (docs/ARCHITECTURE.md section 7.6): the lines the cap omitted and, among
    the lines shown, the lines cut to the width, and the command that shows
    the event whole; None when nothing was cut."""
    total, omitted, shortened = pretty_cuts(event.observation, max_lines)
    cuts: list[str] = []
    if omitted:
        cuts.append(f"{omitted} of {total} lines omitted")
    if shortened:
        cuts.append(f"{shortened} line(s) cut at {LINE_LIMIT} characters")
    if not cuts:
        return None
    return (
        f"OBSERVATION | {', '.join(cuts)}; assay view --event {event.id} --json "
        "shows it in full"
    )


def _claim_meter_lines(run: Run) -> list[str]:
    """Claim meters: split miss rates, specificity, invalid count, VACUOUS under
    the rule the run's stats file is under, and the never-failed advisory."""
    from .status import _claims_blocks

    claims, vacuous = _claims_blocks(run)
    return [] if claims is None else claims_text(claims, vacuous)


def result_text(run: Run, receipt: Receipt) -> str:
    """Self-sufficient printout after a paid command: outcome, grade, new state."""
    events = run.events
    event = events[-1]
    lines = [f"OUTCOME | {receipt.outcome} | {receipt.detail}"]
    lines.extend(f"  {line}" for line in receipt.grade or ())
    lines.extend(str(line) for line in receipt.modules or ())
    lines.extend(str(line) for line in receipt.aggregates or ())
    lines.extend(str(line) for line in receipt.channels or ())
    for step in receipt.steps or ():
        mark = "·" if step.ungated else ("✓" if step.ok else "✗")
        lines.append(f"  e{step.event:04d} {step.action} {mark}")
        lines.extend(f"      {failure}" for failure in step.failed or ())
        lines.extend(f"      {item}" for item in step.invalid or ())
        if step.problem:
            lines.append(f"      {step.problem}")
    paid = sum(1 for item in events if item.counts_action)
    lines.append(
        f"EVENT | e{event.id} | {progress_text(event)} | paid actions {paid} | {event.state}"
    )
    kind = kind_for(event)
    if kind is not None:
        lines.extend(kind.result_lines(run, receipt))
        return "\n".join(lines)
    end = receipt.end_event
    registry = run.registry
    if event.id == end and end > 0:
        previous = events[end - 1]
        lines.append("KEY DELTA | last step (before -> after)")
        lines.extend(
            f"  {item}"
            for item in delta_lines(previous.observation, event.observation)
        )
    lines.extend(_observation_lines(event, max_lines=RECEIPT_OBSERVATION_LINES))
    note = _observation_note(event, RECEIPT_OBSERVATION_LINES)
    if note is not None:
        lines.append(note)
    lines.append(_general_actions_line(event, registry))
    if registry:
        lines.append(budget_line(registry, events))
    return "\n".join(lines)


def _general_inspect_text(run: Run, index: int, *, full: bool = False) -> str:
    """Inspect one dict-observation event: KEY DELTA plus pretty JSON."""
    events = run.events
    event = events[index]
    lines = [
        f"RUN | event {index} | {progress_text(event)} | paid actions {sum(1 for item in events if item.counts_action)} | state {event.state}",
        f"CAUSE | {canonical_action(event)}",
        _general_actions_line(event, run.registry),
    ]
    if index:
        lines.append("KEY DELTA | since previous observation")
        lines.extend(
            f"  {item}"
            for item in delta_lines(events[index - 1].observation, event.observation)
        )
    lines.extend(_observation_lines(event, max_lines=200 if full else 48))
    return "\n".join(lines)


def status_text(run: Run, *, history: int = 8) -> str:
    """The status lines: the `Status` record of the run, rendered, under the
    registry's `status_budget` when it sets one (docs/ARCHITECTURE.md
    section 7.6)."""
    return render_status(status_of(run, history=history), budget=status_budget(run.registry))


def gate_lines(registry: Mapping[str, Any], events: Sequence[Event]) -> list[str]:
    """The GATE line of a control-arm run; nothing under `required`."""
    from .integrity import ungated_permitted

    mode = gate_mode(registry)
    if mode == "required":
        return []
    return [gate_text(mode, len(ungated_permitted(events)))]


@dataclasses.dataclass(frozen=True, slots=True)
class View:
    """What `assay view` knows (docs/ARCHITECTURE.md section 7.3): the event
    inspected, the previous one (None at event 0), the rendered lines,
    since the frame extra's view is prose, and the export's path when
    `--export` wrote one."""

    event: Event
    previous: Event | None
    lines: tuple[str, ...]
    exported: str | None = None

    def to_json(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "event": self.event.to_json(),
            "previous": None if self.previous is None else self.previous.to_json(),
            "lines": list(self.lines),
        }
        if self.exported is not None:
            output["exported"] = self.exported
        return output


def view_of(
    run: Run,
    *,
    event_id: int | None = None,
    history: int = 0,
    flags: Mapping[str, Any] | None = None,
) -> View:
    """`assay view`: inspect one event (the observation kind renders its own
    form and honors its own flags), then the history tail."""
    events = run.events
    if not events:
        raise AssayError("timeline is empty", code="TIMELINE_EMPTY")
    index = len(events) - 1 if event_id is None else event_id
    if not 0 <= index < len(events):
        raise AssayError(f"event must be in 0..{len(events) - 1}", code="COMMAND_ARGS")
    event = events[index]
    kind = kind_for(event)
    flags = dict(flags or {})
    if kind is not None:
        parts = [kind.view_text(run, index, flags)]
    else:
        parts = [_general_inspect_text(run, index, full=bool(flags.get("grid")))]
        if flags.get("frames") or flags.get("crop") is not None:
            parts.append(
                "NOTE | this run has dict observations; --frames/--crop do not apply"
            )
    if history:
        parts.append("HISTORY | cause -> observed result")
        parts.extend(history_lines(events[: index + 1], history))
    return View(
        event=event,
        previous=events[index - 1] if index else None,
        lines=tuple("\n".join(parts).split("\n")),
    )


def view_lines_text(view: View) -> str:
    """The view as printed: its lines, then the EXPORTED line when there is
    an export."""
    text = "\n".join(view.lines)
    if view.exported is not None:
        text += f"\nEXPORTED | {view.exported}"
    return text


def view_text(
    run: Run,
    *,
    event_id: int | None = None,
    history: int = 0,
    flags: Mapping[str, Any] | None = None,
) -> str:
    return "\n".join(view_of(run, event_id=event_id, history=history, flags=flags).lines)
