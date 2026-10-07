"""The executable-rules tier on the command line: `assay rules help|init|
replay|solve`, and `assay commit @.assay/plan.json`, which executes a
solve-plan step by step with the plan's own per-step predictions, halting on
the first surprise. Runs without a registry only (the rules tier's registry
counterpart is the general world model, `assay model`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from assay.core import (
    AssayError,
    RunPaths,
    append_jsonl,
    atomic_json,
    canonical_action,
    context_for,
    frame_at,
    import_path,
    load_events,
    read_json,
    rows_to_grid,
)
from assay.live import (
    head_events,
    level_advanced,
    paid_step,
    record_event,
    validate_batch_tokens,
    write_receipt,
)
from assay.registry import load_registry

from .render import observation_hash
from .rules import (
    RULES_HELP,
    _call,
    _freeze,
    _is_unknown,
    _unknown_reason,
    check_contract,
    init_rules,
    replay_rules,
    rules_hash,
    solve_rules,
)


def _load_solve_plan(paths: RunPaths, reference: str) -> dict[str, Any]:
    candidate = Path(reference[1:] if reference.startswith("@") else reference)
    path = candidate if candidate.is_absolute() else paths.root / candidate
    try:
        path.resolve().relative_to(paths.root.resolve())
    except ValueError as error:
        raise AssayError(
            "plan reference must stay inside the run directory"
        ) from error
    value = read_json(path)
    if not isinstance(value, dict) or value.get("kind") != "solve-plan":
        raise AssayError(
            "commit accepts only a solve-plan written by `assay rules solve`"
        )
    actions = value.get("actions")
    predictions = value.get("predictions")
    if (
        not isinstance(actions, list)
        or not actions
        or not isinstance(predictions, list)
        or len(predictions) != len(actions)
    ):
        raise AssayError(
            "plan needs one prediction per action; rerun `assay rules solve`"
        )
    return value


def execute_solve_plan(
    paths: RunPaths,
    reference: str,
    *,
    at_event: int | None = None,
) -> dict[str, Any]:
    events = head_events(paths, at_event)
    registry = load_registry(paths)
    if registry is not None:
        raise AssayError(
            "solve-plans belong to the grid rules tier; a registry run has none"
        )
    plan = _load_solve_plan(paths, reference)
    latest = events[-1]
    current = {
        "event": int(latest["id"]),
        "observation_hash": observation_hash(frame_at(latest)),
        "rules_hash": rules_hash(paths),
    }
    source = plan.get("source")
    if not isinstance(source, dict):
        raise AssayError("plan has no source provenance; rerun `assay rules solve`")
    stale = [name for name, expected in current.items() if source.get(name) != expected]
    if stale:
        raise AssayError(
            f"plan is stale ({', '.join(stale)} changed); rerun `assay rules solve`"
        )
    start_event = int(latest["id"])
    actions = [str(item).upper() for item in plan["actions"]]
    validate_batch_tokens(actions)
    predictions = plan["predictions"]
    records: list[dict[str, Any]] = []
    outcome = "PREDICTED"
    detail = f"all {len(actions)} steps landed as predicted"
    last_warning: str | None = None
    with import_path(paths.rules, "rules") as module:
        check_contract(module)
        for index, (token, expected) in enumerate(zip(actions, predictions)):
            pending, _, warning, _ = paid_step(
                paths, token, {"plan": reference, "plan_step": index}
            )
            last_warning = warning or last_warning
            event = record_event(paths, pending)
            advanced = level_advanced(event)
            ok = False
            problem = ""
            if expected.get("level_up"):
                ok = advanced
                if not ok:
                    problem = (
                        "plan predicted level completion here, but the level "
                        "did not advance"
                    )
            elif advanced:
                problem = (
                    "level advanced earlier than the plan predicted; "
                    "rules.py is wrong somewhere"
                )
            elif "rows" in expected:
                predicted_grid = rows_to_grid(expected["rows"])
                actual = frame_at(event)
                ok = predicted_grid.shape == actual.shape and bool(
                    np.array_equal(predicted_grid, actual)
                )
                if not ok:
                    problem = "board differs from the render(state) prediction"
            else:
                all_events = load_events(paths)
                context = context_for(all_events, len(all_events) - 1)
                grounded = _call(module, "initial", frame_at(event), context)
                if grounded is None or _is_unknown(grounded):
                    reason = (
                        _unknown_reason(grounded)
                        if grounded is not None
                        else "initial() returned None"
                    )
                    problem = f"board after this step is not groundable ({reason})"
                else:
                    actual_view = _freeze(_call(module, "observe", grounded))
                    ok = actual_view == expected.get("observe")
                    if not ok:
                        problem = (
                            "observe(state) differs from the plan's prediction"
                        )
            records.append(
                {
                    "event": int(event["id"]),
                    "action": canonical_action(event),
                    "ok": ok,
                    # Machine-generated plan-step predictions never
                    # enter the agent's claim meters.
                    "machine": True,
                    "kind": "plan_step",
                    **({"problem": problem} if problem else {}),
                }
            )
            remaining = len(actions) - index - 1
            discarded = (
                f"; {remaining} remaining steps were discarded" if remaining else ""
            )
            if event["state"] == "WIN":
                outcome, detail = "GAME_COMPLETE", f"the game is complete{discarded}"
                break
            if advanced:
                outcome = "LEVEL_COMPLETE"
                detail = (
                    f"plan completed the level as predicted{discarded}"
                    if ok
                    else f"{problem}{discarded}"
                )
                break
            if event["state"] == "GAME_OVER":
                outcome = "GAME_OVER"
                detail = (
                    "environment reported GAME_OVER during the plan; "
                    f"run `assay rules replay` to find the broken rule{discarded}"
                )
                break
            if not ok:
                outcome = "SURPRISE"
                detail = (
                    f"step {index + 1} ({token}) diverged: {problem}; "
                    f"run `assay rules replay` to find the broken rule{discarded}"
                )
                break
    if last_warning:
        detail += f"; scorecard finalization warning: {last_warning}"
    return write_receipt(
        paths,
        {
            "kind": "commit",
            "outcome": outcome,
            "detail": detail,
            "start_event": start_event,
            "end_event": int(load_events(paths)[-1]["id"]),
            "plan": reference,
            "steps": records,
        },
    )


def _print_replay(result: dict[str, Any]) -> None:
    pixels = (
        "render() not defined"
        if result["pixel_checks"] == 0
        else f"exact pixels {result['pixel_matches']}/{result['pixel_checks']}"
    )
    print(
        f"REPLAY | {result['status']} | {result['explained']}/{result['transitions']} "
        f"transitions explained | {pixels}"
    )
    mismatch = result.get("first_mismatch")
    if mismatch:
        print(
            f"FIRST MISMATCH | e{mismatch['event']} {mismatch['action']} | {mismatch['detail']}"
        )
        if "expected" in mismatch:
            print(f"  expected observe: {mismatch['expected']}")
            print(f"  actual observe:   {mismatch['actual']}")
    for gap in result.get("gaps", ()):
        print(f"GAP | {gap}")
    if result["status"] == "HISTORY_FIT":
        print("NOTE | fits recorded history; unseen mechanics can still differ")


def _print_solve(result: dict[str, Any]) -> None:
    print(
        f"SOLVE | {result['status']} | nodes {result['nodes']} | frontier {result['frontier']} "
        f"| {result['elapsed_seconds']:.3f}s | unknown edges {result['unknown_edges']} "
        f"| dead pruned {result['dead_pruned']}"
    )
    if result.get("replay_status") != "HISTORY_FIT":
        print(
            f"REPLAY | {result['replay_status']} | {len(result.get('replay_gaps', ()))} gaps "
            "— the plan trusts rules that history has not verified"
        )
    if result.get("actions"):
        print("ACTIONS | " + " ".join(result["actions"]))
        print(f"PLAN | {result['plan']} — execute with `assay commit @.assay/plan.json`")
    elif result["status"] == "NO_PLAN_IN_MODEL":
        print(
            "NOTE | no goal state reachable inside rules.py; actions() or step() are "
            "too narrow, or the level needs something unmodeled"
        )
    if result["status"] in {"TIME_LIMIT", "NODE_LIMIT"}:
        print(
            "NOTE | a bound was reached; not evidence that no solution exists "
            "(--seconds / --max-nodes raise it)"
        )
    if result.get("unknown_edges") and not result.get("actions"):
        print(
            f"NOTE | {result['unknown_edges']} Unknown edge(s) were skipped during "
            "search; extending step() may open routes"
        )


def handle_rules(paths: RunPaths, args: Any) -> None:
    if read_json(paths.registry, None) is not None:
        raise AssayError(
            "the rules tier applies to grid runs; this registry run "
            "has none — model mechanics with `assay python` instead"
        )
    if args.rules_command == "help":
        print(RULES_HELP)
    elif args.rules_command == "init":
        print(f"CREATED | {init_rules(paths)}")
        print(RULES_HELP)
    elif args.rules_command == "replay":
        result = replay_rules(paths)
        atomic_json(paths.verification, result)
        append_jsonl(paths.activity, {"kind": "rules_replay", **result})
        _print_replay(result)
    elif args.rules_command == "solve":
        result = solve_rules(
            paths, seconds=args.seconds, max_nodes=args.max_nodes
        )
        append_jsonl(paths.activity, {"kind": "rules_solve", **result})
        _print_solve(result)
    else:
        raise AssayError(f"unsupported rules command {args.rules_command}")
