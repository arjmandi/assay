"""The diagnose mode: a run's record read against the five layers of
reasoning (README.md, "Reasoning layers"), each finding with its evidence
and the place to steer.

    python3 -m tools.eval diagnose --run-dir DIR [--json]

The layers, from the most general to the most specific: 1 the constitution,
2 the modules, 3 the registry and the adapter, 4 the addressable states, 5
the model. A finding names what the record shows (counts and event ids from
the journal, refusals from the activity log, the player's notes), the layer
where that failure usually starts, and what to change there. The rules are
the ones the packs' world cards use under "breaks first":

- layer 3: outcomes the code could not grade, and commands the harness
  refused for their form (a parameter, a state name, the prediction's
  syntax): the interaction itself;
- layer 4: predictions that name no particular result, declared states
  never graded, an outcome form that keeps missing, and Verified lines in
  the notes that cite nothing the record holds: the world model;
- layer 2: a form missed again and again with no hold between, a stalled
  progress unit, actions taken six times without changing the world, and a
  Stop gate that had to block: patterns a module catches;
- layer 1: a declared stop or a conclusion of impossibility that rests on
  untested rules, and milestone predictions that keep missing: a way of
  working the manual has to carry;
- layer 5: what remains when the layers above show nothing and the run is
  not won.

It reads the report's record (`report.build`, no audit run) and writes
nothing. `/assay:diagnose` runs it and explains the findings. A finding is a
place to look, not a verdict: the counts are the evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import EvalError
from .report import build, ids

LAYERS = {
    1: "the constitution",
    2: "the modules",
    3: "the registry and the adapter",
    4: "the addressable states",
    5: "the model",
}
# The refusal codes that mean the agent used the interface wrongly, as
# opposed to the world or the owner saying no.
FORM_CODES = frozenset({
    "ACTION_PARAMS", "ACTION_UNKNOWN", "ACTION_UNAVAILABLE", "COMMAND_ARGS", "CLI_USAGE", "PREDICTION_SYNTAX",
    "PREDICTION_REQUIRED", "STATE_UNKNOWN", "STATE_DECLARE",
})
# The refusals a module or a gate's demand produced.
DEMAND_CODES = frozenset({"MODULE_DEMAND", "DESTRUCTIVE_UNDECLARED"})
MISS_RATE = 0.3
MIN_GRADED = 5
LOOP_MISSES = 3
UNSPECIFIC_SHARE = 0.2
BLOCKS = 3


def finding(layer: int, what: str, evidence: str, steer: str) -> dict[str, Any]:
    return {"layer": layer, "what": what, "evidence": evidence, "steer": steer}


def diagnose(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The findings, by layer, from one report record."""
    found: list[dict[str, Any]] = []
    predictions = record["predictions"]
    counts = predictions["meter"]
    cover = record["coverage"]
    end = record["ending"]
    notes = record["notes"]

    # Layer 3: the interaction.
    ungraded = predictions["ungraded"]
    if ungraded:
        first = ungraded[0]
        found.append(finding(
            3, f"{len(ungraded)} outcome(s) the code could not grade",
            f"{ids([item['event'] for item in ungraded], 8)}; the first: {first['outcome']}: {first['actual']}",
            "the observation does not hold what the predictions read: check each state's path against the "
            "adapter's observation, and the world's timeouts",
        ))
    refused = {code: slot["count"] for code, slot in record["activity"]["refusals"].items() if code in FORM_CODES}
    if sum(refused.values()) >= 3:
        found.append(finding(
            3, f"{sum(refused.values())} commands refused for their form",
            ", ".join(f"{code} {count}" for code, count in sorted(refused.items())),
            "the registry's parameter descriptions, and the pack's note to the player on how to call the actions",
        ))

    # Layer 4: the world model.
    unspecific = cover["run"]["unspecific"]
    graded = counts["graded"] or 0
    if unspecific or (graded and counts["coerced"] / graded >= UNSPECIFIC_SHARE):
        evidence = []
        if unspecific:
            evidence.append(f"actions never given a specific outcome: {', '.join(unspecific)}")
        if counts["coerced"]:
            evidence.append(f"{counts['coerced']} of {graded} outcomes were free text graded as change")
        found.append(finding(
            4, "predictions that name no particular result", "; ".join(evidence),
            "declare the states the goal depends on and predict their readings; where every action changes the "
            "observation, `change` predicts nothing",
        ))
    if cover["states_never_graded"]:
        found.append(finding(
            4, "declared states never graded", ", ".join(cover["states_never_graded"]),
            "predict them, or drop them from the model",
        ))
    world = counts["world_model"]
    if world["graded"] >= MIN_GRADED and world["missed"] / world["graded"] >= MISS_RATE:
        worst = []
        for action, slot in predictions["actions"].items():
            for form, entry in slot["forms"].items():
                if entry["missed"] and entry["counted_as"] == ["world_model"]:
                    worst.append((len(entry["missed"]), action, form, entry["missed"]))
        worst.sort(reverse=True)
        named = "; ".join(f"{form} after {action}: missed {count} ({ids(missed, 5)})" for count, action, form, missed in worst[:3])
        found.append(finding(
            4, f"world-model misses {world['missed']} of {world['graded']}", named,
            "the model of these is wrong: the notes should carry each counter-fact, and the next predictions "
            "should use it",
        ))
    if notes is not None:
        unsupported = [item for item in notes["verified"] if item["verdict"] in ("none", "beyond")]
        if unsupported:
            found.append(finding(
                4, f"{len(unsupported)} Verified line(s) in the notes that cite nothing the record holds",
                "; ".join(item["line"][:80] for item in unsupported[:3]),
                "a Verified line cites the event ids that graded it; a line without one is an assumption",
            ))

    # Layer 2: patterns across the trace.
    for action, slot in predictions["actions"].items():
        for form, entry in slot["forms"].items():
            if len(entry["missed"]) >= LOOP_MISSES and not entry["held"]:
                found.append(finding(
                    2, f"{form} after {action} missed {len(entry['missed'])} times and never held",
                    ids(entry["missed"], 8),
                    "the miss_streak and coverage_audit modules in block mode (the registry's module_modes: "
                    "{\"miss_streak\": \"block\", \"coverage_audit\": \"block\"}), so a repeated miss halts the agent",
                ))
    unit = cover["unit"]
    if unit["stalled"]:
        found.append(finding(
            2, "the progress unit stalled", f"the last paid actions on unit {unit['unit'] + 1} changed nothing",
            "the coverage audit names what was never tried; set it to block (module_modes: {\"coverage_audit\": \"block\"})",
        ))
    if cover["run"]["dead"]:
        found.append(finding(
            2, "actions taken six times or more that never changed the world", ", ".join(cover["run"]["dead"]),
            "a module that halts on a no-op streak, or a registry note on what these actions need",
        ))
    demanded = {code: slot["count"] for code, slot in record["activity"]["refusals"].items() if code in DEMAND_CODES}
    if demanded:
        found.append(finding(
            2, f"{sum(demanded.values())} action(s) refused until the agent declared what a module demanded",
            ", ".join(f"{code} {count}" for code, count in sorted(demanded.items())),
            "the modules worked; if the same demand repeats, the player's prompt or the constitution should say it "
            "before the action",
        ))
    sessions = record["sessions"]
    if sessions and sessions["totals"]["blocks"] >= BLOCKS:
        found.append(finding(
            2, f"the Stop gate blocked {sessions['totals']['blocks']} stops",
            "the player tried to end its session before the run was over",
            "the player's prompt: when to stop, and how to declare a stop",
        ))

    # Layer 1: ways of working the manual carries.
    if end["outcome"] == "SELF_STOP":
        gaps = [*unit["untried"], *cover["run"]["unspecific"], *(cover["states_never_graded"] or [])]
        coverage = (record["check"] or {}).get("result") or {}
        never = (coverage.get("coverage") or {}).get("never_tested") or []
        if gaps or never:
            stated = f"declared: {end['declaration']}"
            if gaps:
                stated += f"; untested: {', '.join(sorted(set(gaps)))}"
            if never:
                stated += f"; the checker's rules never tested alone: {', '.join(never)}"
            found.append(finding(
                1, "a declared stop that rests on untested rules", stated,
                "the constitution's coverage audit: a stop stands on rules the record exercised; probe the gaps first",
            ))
    loose = [item for item in record["declarations"]["conclusions"] if not item["coverage_audit"]]
    if loose:
        found.append(finding(
            1, f"{len(loose)} conclusion(s) of impossibility without a coverage audit",
            ids([item["event"] for item in loose], 8),
            "the constitution's rule: enumerate the rules a conclusion rests on and cite the events that tested them",
        ))
    gamble = counts["gamble"]
    if gamble["graded"] >= 2 and gamble["missed"] / gamble["graded"] >= 0.5:
        found.append(finding(
            1, f"milestone predictions missed {gamble['missed']} of {gamble['graded']}",
            "the agent predicted progress it did not make",
            "the manual on predicting a milestone only when the model says it is one action away",
        ))

    # Layer 5: what is left.
    if not found and end["outcome"] not in ("WIN",):
        found.append(finding(
            5, "the run is not won and the layers above show nothing",
            f"{end['outcome']}: {end['why']}",
            "the model: a stronger one, or more budget, on the same world",
        ))
    return sorted(found, key=lambda item: item["layer"])


def lines(record: dict[str, Any], found: list[dict[str, Any]]) -> list[str]:
    end = record["ending"]
    cap = end["cap"] if end["cap"] is not None else "no cap"
    out = [f"DIAGNOSE | {record['run']['name']} | {end['outcome']} | paid {end['paid']}/{cap} | {len(found)} finding(s)"]
    for layer in sorted({item["layer"] for item in found}):
        mine = [item for item in found if item["layer"] == layer]
        out.append(f"LAYER {layer} | {LAYERS[layer]} | {len(mine)} finding(s)")
        for item in mine:
            out.append(f"  - {item['what']}: {item['evidence']}")
            out.append(f"    steer: {item['steer']}")
    if not found:
        out.append("NOTHING | the record shows no failure the layers name")
    return out


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m tools.eval diagnose",
                                     description="A run's record read against the five layers of reasoning.")
    parser.add_argument("--run-dir", type=Path, required=True, metavar="DIR")
    parser.add_argument("--json", action="store_true", help="the findings as one JSON object")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        record = build(args.run_dir.resolve())
    except EvalError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
    found = diagnose(record)
    if args.json:
        print(json.dumps({"run": record["run"]["dir"], "outcome": record["ending"]["outcome"], "findings": found}, indent=2))
    else:
        print("\n".join(lines(record, found)))
    return 0
