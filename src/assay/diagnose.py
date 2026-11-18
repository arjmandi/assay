"""The record read against the five layers of reasoning.

What an agent needs to reason lives at five layers, from the most general to
the most specific. Each holds a different kind of knowledge, is written by a
different party, and is a place to steer:

    1. the constitution   how to learn any world; the operator writes it once
    2. the modules        reasoning in code over the record; the operator adds them
    3. the registry and   one world and the only way to touch it; the world's
       the adapter        operator writes them
    4. the addressable    the world model the agent builds by checked prediction
       states
    5. the model          everything behind the model's API; not the harness's

A failure points at the layer to change. `diagnose` reads a run's journal,
its refusals, its declared states and its notes, and reports each finding
under the layer where that kind of failure starts, with its evidence (counts
and event ids) and what to change there. A finding is a place to look, not a
verdict.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from . import meters, states
from .grade import void
from .journal import Event, Run, grades, is_paid, read_jsonl
from .world import OVER, render_action

LAYERS = {
    1: "the constitution",
    2: "the modules",
    3: "the registry and the adapter",
    4: "the addressable states",
    5: "the model",
}
MISS_RATE = 0.3  # a miss rate at or above this is a finding
MIN_GRADED = 5  # over at least this many graded outcomes
COERCED_SHARE = 0.2
REPEATS = 3
INTERACTION = ("ACTION_PARAMS", "ACTION_UNKNOWN", "ACTION_UNAVAILABLE", "WORLD_ERROR", "OBSERVATION_INVALID")
LANGUAGE = ("PREDICTION_SYNTAX", "PREDICTION_REQUIRED")
_CITATION = re.compile(r"\be(\d+)\b")

Finding = dict[str, Any]


def _ids(values: Sequence[int], limit: int = 8) -> str:
    shown = ", ".join(f"e{value}" for value in values[:limit])
    return shown + (f" and {len(values) - limit} more" if len(values) > limit else "")


def form_of(item: dict[str, Any]) -> str:
    """An outcome's form, its text with the value left out: what was tested
    about an action, whatever value each prediction named."""
    kind, name = item["kind"], item.get("channel")
    if kind == "channel_eq":
        return f"ch {name} = V"
    if kind == "channel_delta":
        return f"ch {name} delta sign {item['sign']}" if item.get("sign") else f"ch {name} delta {item['op']} V"
    if kind == "channel_cross":
        return f"ch {name} crosses V"
    if kind == "verify":
        return f"verify:{item['path']}"
    return "level+1" if kind == "level_up" else str(kind)


def _forms(events: Sequence[Event]) -> dict[tuple[str, str], tuple[list[int], list[int]]]:
    """Per action and outcome form, the events where it held and where it missed."""
    found: dict[tuple[str, str], tuple[list[int], list[int]]] = {}
    for event in events:
        for item in grades(event):
            if not void(item) and item["kind"] != "coerced":
                slot = found.setdefault((event["action"], form_of(item)), ([], []))
                slot[0 if item["ok"] else 1].append(event["id"])
    return found


def _unsupported_notes(run: Run) -> list[str]:
    """The lines under a Verified heading of the notes that cite no event the
    journal holds as a paid action with a held outcome."""
    try:
        text = run.paths.notes.read_text()
    except FileNotFoundError:
        return []
    held = {event["id"] for event in run.events if any(item["ok"] for item in grades(event) if not void(item))}
    verified, loose = False, []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            verified = "verified" in stripped.lower() and "unverified" not in stripped.lower()
        elif verified and stripped:
            cited = {int(number) for number in _CITATION.findall(stripped)}
            if not cited & held:
                loose.append(stripped.lstrip("-*+ ")[:80])
    return loose


def findings(run: Run) -> list[Finding]:
    events = run.events
    paid = [event for event in events if is_paid(event)]
    counts = meters.predictions(events)
    refused: dict[str, int] = {}
    for record in read_jsonl(run.paths.refusals):
        refused[record["code"]] = refused.get(record["code"], 0) + 1
    forms = _forms(events)
    found: list[Finding] = []

    def add(layer: int, what: str, evidence: str, steer: str) -> None:
        found.append({"layer": layer, "what": what, "evidence": evidence, "steer": steer})

    # Layer 1: a way of working the agent lacks.
    if counts.graded and counts.coerced / counts.graded >= COERCED_SHARE:
        add(
            1,
            "predictions written as free text",
            f"{counts.coerced} of {counts.graded} graded outcomes were coerced to `change`",
            "the manual's section on the prediction grammar: the agent is not using the forms",
        )
    language = {code: refused[code] for code in LANGUAGE if code in refused}
    if sum(language.values()) >= REPEATS:
        add(
            1,
            "predictions refused for their form",
            ", ".join(f"{code} {count}" for code, count in language.items()),
            "the manual's grammar table and its examples",
        )
    hollow = meters.vacuous(events)
    if hollow:
        add(
            1,
            f"{len(hollow)} vacuous verifier(s)",
            ", ".join(f"{digest[:12]} graded {count}" for digest, count in hollow.items()),
            "the manual's verifier contract: a verifier asserts the transition, not a tautology",
        )

    # Layer 2: a pattern across the trace that code would catch.
    repeats = [
        event["id"]
        for previous, event in zip(paid, paid[1:])
        if previous.get("predict_ok") is False
        and event.get("predict_ok") is False
        and render_action(previous["action"], previous["data"]) == render_action(event["action"], event["data"])
    ]
    if repeats:
        add(
            2,
            "a move that had just missed was re-issued unchanged and missed again",
            _ids(repeats),
            "the coverage_audit module advises against it; a module of your own can say more for this world",
        )
    for (action, form), (held, missed) in forms.items():
        if len(missed) >= REPEATS and not held:
            add(
                2,
                f"`{form}` after {action} missed {len(missed)} times and never held",
                _ids(missed),
                "a module that names this pattern when it recurs",
            )
    ledger = meters.coverage(events)
    if ledger.stalled:
        add(
            2,
            "the current progress unit stalled",
            f"its last {meters.STALL_WINDOW} paid actions changed nothing; untried [{', '.join(ledger.untried) or 'none'}]",
            "a module that halts a streak of actions that change nothing",
        )
    if ledger.dead:
        add(
            2,
            f"actions taken {meters.DEAD_MIN_TRIES} times or more on this progress unit that never changed the world",
            ", ".join(ledger.dead),
            "a module that names them, or a registry description of what these actions need",
        )

    # Layer 3: the interaction with the world itself.
    unread = [event["id"] for event in events if any(item.get("ungradable") for item in grades(event))]
    if unread:
        first = next(item for item in grades(events[unread[0]]) if item.get("ungradable"))
        add(
            3,
            f"{len(unread)} action(s) with an outcome the code could not grade",
            f"{_ids(unread)}; the first: {first['text']}: {first['actual']}",
            "the observation does not hold what the predictions read: check the adapter's "
            "observation against the states' paths, and the world's step time",
        )
    interaction = {code: refused[code] for code in INTERACTION if code in refused}
    if sum(interaction.values()) >= REPEATS:
        add(
            3,
            "actions refused at the world's boundary",
            ", ".join(f"{code} {count}" for code, count in interaction.items()),
            "the registry's parameter schemas and descriptions, and the adapter's errors",
        )
    if refused.get("BUDGET_EXHAUSTED") and events[-1]["state"] != "WIN":
        add(
            3,
            "the action budget ran out before the goal",
            f"paid {run.paid} of {run.cap}",
            "the registry's budget, against how the agent spent it (layers 2 and 4)",
        )

    # Layer 4: the world model the agent builds.
    graded, wrong = counts.world_model
    if graded >= MIN_GRADED and wrong / graded >= MISS_RATE:
        worst = sorted(
            ((len(miss), action, form, miss) for (action, form), (_, miss) in forms.items() if miss), reverse=True
        )
        add(
            4,
            f"world-model misses {wrong} of {graded}",
            "; ".join(f"`{form}` after {action}: {count} ({_ids(miss, 5)})" for count, action, form, miss in worst[:3]),
            "the agent's model of these is wrong: its notes should carry each counter-fact",
        )
    if refused.get("STATE_UNKNOWN"):
        add(
            4,
            "predictions named states that were never declared",
            f"{refused['STATE_UNKNOWN']} refusal(s)",
            "the agent reasons about referents it has not registered: declare states first",
        )
    declared = states.declared(run.paths)
    named = {item["channel"] for event in events for item in grades(event) if item.get("channel")}
    idle = sorted(set(declared) - named)
    specific = {event["action"] for event in paid if any(item["kind"] not in ("change", "coerced", "noop") for item in grades(event))}
    vague = sorted({event["action"] for event in paid if event["action"] != "RESET"} - specific)
    if idle or (vague and len(paid) >= MIN_GRADED):
        evidence = [f"declared and never predicted: {', '.join(idle)}"] if idle else []
        evidence += [f"only ever predicted as change or noop: {', '.join(vague)}"] if vague else []
        add(
            4,
            "parts of the world the model does not address",
            "; ".join(evidence),
            "declare the states the goal depends on and predict their readings on every action",
        )
    crashed = [event["id"] for event in events if any(item.get("invalid") for item in grades(event))]
    if crashed:
        add(4, f"{len(crashed)} action(s) graded by a verifier that crashed", _ids(crashed), "test a verifier offline with `assay python` before naming it")
    loose = _unsupported_notes(run)
    if loose:
        add(
            4,
            f"{len(loose)} Verified line(s) in the notes cite no held outcome",
            "; ".join(loose[:3]),
            "a Verified line cites the event that graded it; without one it is an assumption",
        )

    # Layer 5: what the harness does not hold.
    graded, wrong = counts.gamble
    world_holds = not any(item["layer"] == 4 and item["what"].startswith("world-model") for item in found)
    if graded >= 2 and wrong / graded >= 0.5 and world_holds:
        add(
            5,
            f"progress predictions missed {wrong} of {graded} while the world model held",
            "the agent predicts the mechanics and does not turn them into progress",
            "the model: a stronger one, or more budget, on the same world",
        )
    spent = run.cap is not None and run.paid >= run.cap
    if not found and events[-1]["state"] != "WIN" and spent:
        add(
            5,
            "the budget is spent, the run is not won and the layers above show nothing",
            f"{events[-1]['state']} after {run.paid} paid actions",
            "the model: a stronger one, or more budget, on the same world",
        )
    return sorted(found, key=lambda item: item["layer"])


def report(run: Run) -> str:
    found = findings(run)
    cap = run.cap if run.cap is not None else "no cap"
    lines = [
        f"DIAGNOSE | {run.config['world_id']} | {run.last['state']} | paid {run.paid}/{cap} | {len(found)} finding(s)"
    ]
    for layer, name in LAYERS.items():
        mine = [item for item in found if item["layer"] == layer]
        if mine:
            lines.append(f"LAYER {layer} | {name} | {len(mine)} finding(s)")
        for item in mine:
            lines += [f"  - {item['what']}: {item['evidence']}", f"    steer: {item['steer']}"]
    if not found:
        lines.append("NOTHING | the record shows no failure the layers name")
    if run.last["state"] not in OVER and (run.cap is None or run.paid < run.cap):
        lines.append("OPEN | the run is not over: these are the findings so far")
    return "\n".join(lines)
