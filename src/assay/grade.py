"""Grading: every outcome against the world's own response.

An outcome evaluates on one transition (the event before the action, the
event after it, the measured step time) to one of four verdicts, each with
the actual value, the machine's statement of what happened:

    held        the predicate is true of the transition: a fact
    missed      it is false: the actual value is the counter-fact
    invalid     a verifier crashed, timed out or printed something else
    ungradable  a state could not be read, or the result settled too late

A verifier is the agent's own program, `def verify(before, after) ->
(ok, actual)`. It is hashed and stored before the action it grades, and the
stored copy is what runs, in the sandbox. After each grading it runs once
more on the identity transition (before, before): a verifier whose identity
verdict equals its real verdict every time does not use the transition, and
the meters stop counting its passes (`meters.vacuous`).

Grades are final when written. `predict_ok` on the event is false if any
outcome missed, null if none missed but one was invalid or ungradable, true
otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from . import sandbox, states
from .delta import changed
from .grammar import STATE_KINDS, Outcome, bucket
from .journal import Event, Paths, Run, advanced, observation_of

Grade = dict[str, Any]


def admit(paths: Paths, outcomes: Sequence[Outcome]) -> list[Outcome]:
    """Before any spend: store every verifier an outcome names under its
    hash, and put the hash on the outcome."""
    return [
        {**outcome, "verifier_hash": states.store_program(paths, outcome["path"], "a verifier")}
        if outcome["kind"] == "verify"
        else outcome
        for outcome in outcomes
    ]


def _grade(outcome: Outcome, ok: bool, actual: str, **flags: Any) -> Grade:
    graded = {**outcome, "ok": ok, "actual": actual, "bucket": bucket(outcome), **flags}
    if outcome.get("coerced"):
        graded["kind"] = "coerced"
    return graded


def _number(value: Any) -> float | None:
    return None if isinstance(value, bool) or not isinstance(value, (int, float)) else float(value)


def _observation(outcome: Outcome, before: Event, after: Event) -> Grade:
    """`noop`, `change`, `win` and `level+1`: read off the two events."""
    kind = outcome["kind"]
    count, unit = changed(before, after)
    moved = count is None or count > 0 or advanced(after)
    what = (
        f"{count} {unit} changed" if count
        else "the frame changed its size" if count is None
        else "level advanced" if advanced(after)
        else "no observed change"
    )
    if kind == "noop":
        return _grade(outcome, not moved, what)
    if kind == "change":
        return _grade(outcome, moved, what)
    if kind == "win":
        return _grade(outcome, after["state"] == "WIN", f"state {after['state']}")
    return _grade(
        outcome,
        advanced(after),
        f"level advanced to {after['levels_completed']} completed" if advanced(after) else "level did not advance",
    )


def _state(run: Run, outcome: Outcome, before: Event, after: Event) -> Grade:
    """An outcome on a declared state: its reading after the action, and for
    a delta or a crossing its reading before."""
    name, kind = outcome["channel"], outcome["kind"]

    def ungradable(why: Any) -> Grade:
        return _grade(outcome, False, f"UNGRADABLE: {why}", ungradable=True)

    ok, now = states.read(run, name, after, remember=True)
    if not ok:
        return ungradable(now)
    if kind == "channel_eq":
        expected, tolerance = outcome["value"], outcome.get("tol")
        if tolerance is not None:
            if _number(now) is None or _number(expected) is None:
                return ungradable(f"a tolerance needs numbers, got {json.dumps(now)}")
            held = abs(float(now) - float(expected)) <= tolerance
        elif _number(expected) is not None:
            held = _number(now) == float(expected)
        else:
            held = now == expected
        return _grade(outcome, held, f"ch {name} = {json.dumps(now)}")
    ok, was = states.read(run, name, before)
    if not ok:
        return ungradable(was)
    old, new = _number(was), _number(now)
    if old is None or new is None:
        return ungradable(f"a delta or a crossing needs numbers, got {json.dumps(was)} -> {json.dumps(now)}")
    if kind == "channel_delta":
        delta, target = new - old, outcome.get("value")
        held = {
            "sign": delta > 0 if outcome.get("sign") == "+" else delta < 0,
            "=": delta == target,
            ">=": target is not None and delta >= target,
            "<=": target is not None and delta <= target,
        }[outcome["op"]]
        return _grade(outcome, held, f"ch {name} moved {delta:+g} ({old:g} -> {new:g})")
    threshold = float(outcome["value"])
    rose, fell = old < threshold <= new, old > threshold >= new
    held = {"below": rose, "above": fell}.get(outcome.get("direction", ""), rose or fell)
    return _grade(outcome, held, f"ch {name} went {old:g} -> {new:g} (threshold {threshold:g})")


def _verifier(paths: Paths, outcome: Outcome, before: Event, after: Event) -> Grade:
    program = paths.programs / f"{outcome['verifier_hash']}.py"
    views = observation_of(before), observation_of(after)
    ok, result = sandbox.run(program, "verify", [*views])
    if not ok:
        return _grade(outcome, False, f"INVALID_CLAIM: verifier {result}", invalid=True, verifier=True)
    same, identity = sandbox.run(program, "verify", [views[0], views[0]])
    return _grade(
        outcome,
        result["ok"],
        result["actual"],
        verifier=True,
        identity_verdict=identity["ok"] if same else "invalid",
    )


def grade(run: Run, outcomes: Sequence[Outcome], before: Event, after: Event, elapsed: float) -> list[Grade]:
    """Grade every outcome of one paid action. `after` is the pending event,
    graded before it is appended."""
    graded = []
    for outcome in outcomes:
        window = outcome.get("window_s")
        if outcome["kind"] == "note":
            continue
        if window is not None and elapsed > window:
            graded.append(
                _grade(
                    outcome,
                    False,
                    f"UNGRADABLE: settled after {elapsed:.2f}s, outside the declared {window:g}s window",
                    ungradable=True,
                )
            )
            continue
        try:
            if outcome["kind"] == "verify":
                graded.append(_verifier(run.paths, outcome, before, after))
            elif outcome["kind"] in STATE_KINDS:
                graded.append(_state(run, outcome, before, after))
            else:
                graded.append(_observation(outcome, before, after))
        except Exception as error:  # noqa: BLE001 - the world has stepped: the event is journaled whatever grading does
            graded.append(_grade(outcome, False, f"INVALID_CLAIM: grading failed: {type(error).__name__}: {error}", invalid=True))
    return graded


def void(item: Grade) -> bool:
    """A grade that is neither held nor missed."""
    return bool(item.get("invalid") or item.get("ungradable"))


def verdict(graded: Sequence[Grade]) -> bool | None:
    """`predict_ok` for one action."""
    if any(not item["ok"] for item in graded if not void(item)):
        return False
    return None if any(void(item) for item in graded) else True


def lines(graded: Sequence[Grade]) -> list[str]:
    """Each outcome with its mark. The actual value is shown where the
    prediction did not hold: the counter-fact is what the agent reads first."""
    return [
        f"! {item['text']} | {item['actual']}" if void(item)
        else f"✓ {item['text']}" if item["ok"]
        else f"✗ {item['text']} | {item['actual']}"
        for item in graded
    ]
