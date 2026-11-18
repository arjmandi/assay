"""Modules: reasoning in code over the record.

A module sees what a long context forgets. It is a function of the journal
and of the action about to be taken (None when the status asks), and it
returns one line of advice or nothing. A module never refuses an action: it
advises, and the advice is printed on the receipt and in the status.

The built-ins are below. An operator adds one by listing a Python file under
`modules` in the registry; the file defines `NAME` and
`advise(events, pending)`, where `events` are the journal's events as
dicts and `pending` is `{"kind": "act" | "commit" | "reset", "name": NAME,
"params": {...}}` or None. Module files are the operator's code and run in
the daemon.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import AssayError
from .journal import Event, grades, is_paid
from .meters import STALL_WINDOW, coverage, hazards, predictions, recent, unit_paid
from .world import render_action

Pending = Mapping[str, Any] | None
Advise = Callable[[Sequence[Event], Pending], str | None]
LOOP_MISSES = 3


def _unit(events: Sequence[Event]) -> str:
    return "level" if events[-1]["win_levels"] > 1 else "unit"


def wall_spend(events: Sequence[Event], pending: Pending) -> str | None:
    """Long spend on one progress unit means probing has stopped paying."""
    spent = len(unit_paid(events))
    if spent < 25:
        return None
    return (
        f"{spent} paid actions on this {_unit(events)}; stop manual probing and model "
        "the mechanics offline (`assay python`) before spending more"
    )


def miss_streak(events: Sequence[Event], pending: Pending) -> str | None:
    """Repeated misses mean the story in the notes is wrong."""
    held, graded = recent(events)
    if graded - held < 3:
        return None
    return (
        f"{graded - held} of the last {graded} predictions missed: the mechanics story "
        "in NOTES.md is wrong; fix it before spending more actions"
    )


def null_forensics(events: Sequence[Event], pending: Pending) -> str | None:
    """A predicted change that observed nothing deserves a look at the raw
    observation before the hypothesis is closed."""
    last = next((event for event in reversed(events) if event.get("predict_ok") is not None), None)
    if last is None or not any(item["kind"] == "change" and not item["ok"] for item in grades(last)):
        return None
    return (
        f"e{last['id']} predicted change and observed nothing; run `assay view --event {last['id']}` "
        "and read the raw observation before closing that hypothesis"
    )


def park_with_test(events: Sequence[Event], pending: Pending) -> str | None:
    """Abandoning a state is safe only if the notes say what would reopen it."""
    if not pending or pending["kind"] != "reset":
        return None
    return "resetting: record in NOTES.md what would have to be true to revisit this state"


def specificity(events: Sequence[Event], pending: Pending) -> str | None:
    """Vagueness earns nothing."""
    counts = predictions(events)
    if counts.graded < 20 or counts.coerced * 2 <= counts.graded:
        return None
    return (
        f"specificity is {counts.graded - counts.coerced}/{counts.graded}: over half the graded "
        "outcomes are coerced free text, which earns nothing; state checkable outcomes"
    )


def hazard(events: Sequence[Event], pending: Pending) -> str | None:
    """An action that once lost the unit is named when it is about to be used."""
    seen = hazards(events)
    if not pending or pending["name"] not in seen:
        return None
    return (
        f"{pending['name']} entered a loss state at e{seen[pending['name']]}; say in your "
        "prediction what the worst case is, and know how the run recovers"
    )


def coverage_audit(events: Sequence[Event], pending: Pending) -> str | None:
    """Which actions this progress unit never exercised, and a halt on
    repeating the move that just missed. A conclusion that something is
    impossible rests on rules; the ones never exercised are the gaps."""
    if len(events) < 2:
        return None
    ledger = coverage(events)
    gaps = [
        f"{label} [{', '.join(names)}]"
        for label, names in (("never-productive actions", ledger.dead), ("untried actions", ledger.untried))
        if names
    ]
    if "frames" in events[-1]:
        from .grid import coverage_gap

        gaps += [gap for gap in [coverage_gap(events, unit_paid(events))] if gap]
    unexercised = "unexercised: " + "; ".join(gaps) + "." if gaps else "every available action has been productive."
    paid = [event for event in events if is_paid(event)]
    if pending and pending["kind"] != "reset" and paid:
        move = render_action(pending["name"], pending.get("params"))

        def same(event: Event) -> bool:
            return event.get("predict_ok") is False and render_action(event["action"], event["data"]) == move

        if len(paid) >= LOOP_MISSES and all(same(event) for event in paid[-LOOP_MISSES:]):
            return f"{move} has missed {LOOP_MISSES} times in a row: you are looping. Stop repeating it. {unexercised}"
        if same(paid[-1]):
            return (
                f"re-issuing {move} unmodified, and it just missed. Halt: revise the model "
                f"or probe an unexercised rule. {unexercised}"
            )
    if pending and pending["kind"] == "reset" and ledger.stalled:
        return f"resetting under a stall. {unexercised} Probe these before treating the {_unit(events)} as impossible."
    if pending is None and ledger.paid >= 3:
        stall = f" STALL: the last {STALL_WINDOW} actions changed nothing. {unexercised}" if ledger.stalled else ""
        return (
            f"coverage {_unit(events)} {ledger.unit + 1}: untried [{', '.join(ledger.untried) or 'none'}], "
            f"no-op-only [{', '.join(ledger.dead) or 'none'}].{stall}"
        )
    return None


BUILT_IN: dict[str, Advise] = {
    "wall_spend": wall_spend,
    "miss_streak": miss_streak,
    "null_forensics": null_forensics,
    "park_with_test": park_with_test,
    "specificity": specificity,
    "hazard": hazard,
    "coverage_audit": coverage_audit,
}


def load(registry: Mapping[str, Any]) -> dict[str, Advise]:
    """The built-ins and the registry's own module files, by name."""
    active = dict(BUILT_IN)
    for entry in registry.get("modules", ()):
        info = importlib.util.spec_from_file_location(f"assay_module_{Path(entry).stem}", entry)
        try:
            if info is None or info.loader is None:
                raise ImportError("not a Python file")
            module = importlib.util.module_from_spec(info)
            info.loader.exec_module(module)
            name, advise = str(module.NAME), module.advise
        except Exception as error:  # noqa: BLE001 - whatever the operator's file raised
            raise AssayError(
                f"module {entry} does not load: {type(error).__name__}: {error}",
                code="MODULE_CONTRACT",
                hint="a module file defines NAME and advise(events, pending)",
            ) from error
        if name in active or not callable(advise):
            raise AssayError(f"module {entry} needs its own NAME and a callable advise", code="MODULE_CONTRACT")
        active[name] = advise
    return active


def advisories(active: Mapping[str, Advise], events: Sequence[Event], pending: Pending) -> list[str]:
    """One `MODULE name | advice` line per module with something to say. A
    module that raises says so on its line and stops nothing."""
    lines = []
    for name, advise in active.items():
        try:
            message = advise(events, pending)
        except Exception as error:  # noqa: BLE001 - advice never breaks an action
            message = f"failed: {type(error).__name__}: {error}"
        if message:
            lines.append(f"MODULE {name} | {message}")
    return lines
