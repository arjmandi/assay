"""Paid-action execution: act with a graded prediction, batch with per-step
claims, execute a rules.py solve-plan, and reset with a reason.

Every paid action is journaled by the broker before the response is recorded,
so a crash between spend and record is recovered on the next start.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .agenda import check_rehearsal, consume_approval
from .aggregates import open_aggregates, resolve_due
from .broker import broker_step
from .channels import check_channel_references
from .core import (
    AssayError,
    RunPaths,
    append_event,
    append_jsonl,
    atomic_json,
    canonical_action,
    check_public_action,
    context_for,
    frame_at,
    import_path,
    load_events,
    load_jsonl,
    make_event,
    now_iso,
    parse_action,
    read_json,
    rows_to_grid,
)
from .evidence import observation_hash, render_event
from .integrity import extend_chain, redact, redact_mapping
from .modules import consult_modules, observe_outcome
from .perception import build_scene_dossier
from .predictions import grade_action_claims, grade_lines, parse_claims
from .registry import (
    action_spec,
    check_budget,
    check_registry_action,
    check_usd_budget,
    hand_cap,
    load_registry,
    notes_cap,
)
from .verifiers import admit_verifier
from .rules import (
    _call,
    _freeze,
    _is_unknown,
    _unknown_reason,
    check_contract,
    rules_hash,
)


def _receipt(paths: RunPaths, value: Mapping[str, Any]) -> dict[str, Any]:
    record = {"timestamp": now_iso(), **dict(value)}
    paths.receipts.mkdir(parents=True, exist_ok=True)
    event = record.get("end_event", record.get("start_event", "none"))
    atomic_json(paths.receipts / f"{time.time_ns()}-e{event}.json", record)
    append_jsonl(paths.activity, {"kind": "receipt", **record})
    return record


def _archive_notes(paths: RunPaths, completed_level: int) -> None:
    """Snapshot the live notes when a level completes; the live file is kept."""
    if not paths.notes.exists():
        return
    archive = paths.state / "levels" / f"level-{completed_level}.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        archive.write_text(paths.notes.read_text())


def _head_events(paths: RunPaths, at_event: int | None) -> list[dict[str, Any]]:
    events = load_events(paths)
    if not events:
        raise AssayError("timeline is empty")
    if events[-1]["state"] == "WIN":
        raise AssayError("the game is already complete")
    if at_event is not None and at_event != int(events[-1]["id"]):
        raise AssayError(
            f"event guard failed: requested {at_event}, current {events[-1]['id']}"
        )
    return events


def _paid_step(
    paths: RunPaths,
    token: str,
    reasoning: Mapping[str, Any] | None = None,
    note: str = "",
    registry: Mapping[str, Any] | None = None,
    stepper: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], str | None, float]:
    prior = load_events(paths)[-1]
    name, data = parse_action(token, registry)
    if name == "RESET":
        raise AssayError("use `assay reset`; reset cannot hide inside a batch")
    if registry is not None:
        check_registry_action(name, prior["available_actions"])
    else:
        check_public_action(token, prior["available_actions"])
    secrets = tuple((registry or {}).get("secrets") or ())
    reasoning = redact_mapping(reasoning, secrets)
    step = stepper or broker_step
    started = time.monotonic()
    response, mutation_id, warning = step(paths, name, data, reasoning)
    elapsed = time.monotonic() - started
    pending = make_event(response, name, data, prior, note=redact(note, secrets) or "")
    pending["mutation_id"] = mutation_id
    return pending, prior, warning, elapsed


def _record(paths: RunPaths, pending: Mapping[str, Any]) -> dict[str, Any]:
    event = append_event(paths, pending)
    if load_registry(paths) is not None:
        # Registry runs are chained: the exact appended line advances the
        # daemon-held rolling hash (tamper-evidence).
        try:
            last_line = paths.events.read_text().splitlines()[-1]
        except (FileNotFoundError, IndexError):
            last_line = ""
        if last_line:
            extend_chain(
                paths, last_line, int(event["id"]), win=str(event["state"]) == "WIN"
            )
    if "frames" in event:
        render_event(paths, event)
        build_scene_dossier(paths, load_events(paths))
    level_before = event.get("level_before")
    if level_before is not None and int(event["levels_completed"]) > int(level_before):
        _archive_notes(paths, int(event["levels_completed"]))
    return event


def _notes_hard_stop(paths: RunPaths, registry: Mapping[str, Any] | None) -> None:
    """The notes-cap block: past 2× the cap, paid actions refuse until trimmed.
    The advisory tier lives in status; this is the last resort."""
    cap = notes_cap(registry)
    if cap is None:
        return
    try:
        size = len(paths.notes.read_text())
    except FileNotFoundError:
        return
    if size > 2 * cap:
        raise AssayError(
            f"NOTES.md is {size} chars — more than twice the {cap}-char cap. One "
            "page is the contract: archive detail elsewhere and trim before the "
            "next paid action (overflow is auto-archived at level completions)"
        )


def _enforce_registry_gates(
    paths: RunPaths,
    registry: Mapping[str, Any] | None,
    events: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    tokens: Sequence[str],
    claims_per_token: Sequence[Sequence[Mapping[str, Any]]],
    declares: Mapping[str, str] | None,
    in_batch: bool,
) -> list[str]:
    """All pre-spend teeth beyond schema/claims/affordance/budget. Returns
    module advisory lines. Everything here is registry-gated: the numbered-action path
    never reaches it."""
    if registry is None:
        return []
    check_usd_budget(load_jsonl(paths.activity), registry)
    _notes_hard_stop(paths, registry)
    advisories: list[str] = []
    declares = dict(declares or {})
    for token, claims in zip(tokens, claims_per_token):
        name, params = parse_action(token, registry)
        spec = action_spec(registry, name) or {}
        if spec.get("destructive"):
            if in_batch:
                raise AssayError(
                    f"{name} is registered destructive: destructive actions are "
                    "banned inside batches; take it as a single "
                    "`assay act` with its declaration"
                )
            worst = str(declares.get("worst_case", "")).strip()
            recovery = str(declares.get("recovery", "")).strip()
            if not worst or not recovery:
                raise AssayError(
                    f"{name} is registered destructive and refuses without a "
                    'declared worst case and recovery plan: add --declare '
                    '"worst_case=<what the worst outcome is>" --declare '
                    '"recovery=<how the run recovers>" — the demand is structural '
                    "(named, non-empty), never a demand for optimism; declaring "
                    "always unlocks the action"
                )
        if spec.get("approval"):
            if in_batch:
                raise AssayError(
                    f"{name} is approval-gated and cannot hide inside a batch"
                )
            consume_approval(paths, name)
        check_rehearsal(paths, registry, name)
        pending = {
            "kind": kind,
            "name": name,
            "params": params,
            "claims": list(claims),
            "declares": declares,
        }
        advisories.extend(consult_modules(paths, registry, events, pending))
    # De-duplicate module lines a long batch would repeat.
    seen: set[str] = set()
    unique = []
    for line in advisories:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique


def _admit_claims(paths: RunPaths, claims: Sequence[dict[str, Any]]) -> None:
    """Admit verifier claims (read, hash, store, journal) before any spend."""
    for claim in claims:
        if claim["kind"] == "verify":
            admit_verifier(paths, claim)


def _grade_summary(
    graded: Sequence[Mapping[str, Any]],
) -> tuple[bool, bool, bool | None]:
    """(missed, invalid_any, predict_ok-journal-value) for one graded action.
    UNGRADABLE outcomes (stale window, unreadable channel) count like invalid:
    their own meter, never a miss, and they halt a containing batch."""
    invalid_any = any(
        item.get("invalid") or item.get("ungradable") for item in graded
    )
    missed = any(
        not item["ok"]
        for item in graded
        if not item.get("invalid") and not item.get("ungradable")
    )
    if missed:
        predict_ok: bool | None = False
    elif invalid_any:
        predict_ok = None
    else:
        predict_ok = True
    return missed, invalid_any, predict_ok


def _level_advanced(event: Mapping[str, Any]) -> bool:
    level_before = event.get("level_before")
    return level_before is not None and int(event["levels_completed"]) > int(
        level_before
    )


def execute_action(
    paths: RunPaths,
    token: str,
    *,
    predict: str,
    because: str | None = None,
    at_event: int | None = None,
    declares: Mapping[str, str] | None = None,
    stepper: Any = None,
) -> dict[str, Any]:
    registry = load_registry(paths)
    claims = parse_claims(predict, general=registry is not None)
    check_channel_references(paths, claims)
    _admit_claims(paths, claims)
    events = _head_events(paths, at_event)
    advisories = _enforce_registry_gates(
        paths,
        registry,
        events,
        kind="act",
        tokens=[token],
        claims_per_token=[claims],
        declares=declares,
        in_batch=False,
    )
    check_budget(events, registry, planned=1)
    start_event = int(events[-1]["id"])
    secrets = tuple((registry or {}).get("secrets") or ())
    predict_journal = redact(predict, secrets) or ""
    because_journal = redact(because, secrets)
    reasoning: dict[str, Any] = {"predict": predict}
    if because:
        reasoning["because"] = because
    if declares:
        reasoning["declares"] = dict(declares)
    pending, prior, warning, elapsed = _paid_step(
        paths, token, reasoning, note=because or "", registry=registry, stepper=stepper
    )
    graded = grade_action_claims(paths, claims, prior, pending, elapsed_s=elapsed)
    missed, invalid_any, predict_ok = _grade_summary(graded)
    ok = not missed and not invalid_any
    pending["predict"] = predict_journal
    pending["predict_ok"] = predict_ok
    pending["grade"] = graded
    if declares:
        pending["declares"] = {
            key: redact(str(value), secrets) for key, value in declares.items()
        }
    event = _record(paths, pending)
    all_events = load_events(paths)
    observe_outcome(paths, registry, all_events, event)
    aggregate_lines: list[str] = []
    if registry is not None:
        aggregate_lines.extend(open_aggregates(paths, claims, all_events))
        aggregate_lines.extend(resolve_due(paths, all_events))
    lines = grade_lines(graded)
    if event["state"] == "WIN":
        outcome, detail = "GAME_COMPLETE", "the game is complete"
    elif _level_advanced(event):
        outcome = "LEVEL_COMPLETE"
        detail = (
            f"level {int(event['level_before']) + 1} complete; notes archived — "
            "re-verify carried assumptions on the new board"
        )
        if not ok:
            detail += "; prediction also missed — treat the mechanics as unproven"
    elif event["state"] == "GAME_OVER":
        outcome = "GAME_OVER"
        detail = "environment reported GAME_OVER; `assay reset` restarts the level"
    elif missed:
        outcome = "SURPRISE"
        first_failed = next(line for line in lines if line.startswith("✗"))
        detail = f"prediction missed: {first_failed[2:]}"
    elif invalid_any:
        outcome = "INVALID_CLAIM"
        first_invalid = next(line for line in lines if line.startswith("!"))
        detail = f"verifier did not grade: {first_invalid[2:]}"
    else:
        outcome, detail = "PREDICTED", "result matched the prediction"
    if warning:
        detail += f"; scorecard finalization warning: {warning}"
    receipt: dict[str, Any] = {
        "kind": "act",
        "outcome": outcome,
        "detail": detail,
        "start_event": start_event,
        "end_event": int(event["id"]),
        "level": min(
            int(event["win_levels"]), int(event["levels_completed"]) + 1
        ),
        "action": canonical_action(event),
        "predict": predict_journal,
        "grade": lines,
        "because": because_journal,
    }
    if advisories:
        receipt["modules"] = advisories
    if aggregate_lines:
        receipt["aggregates"] = aggregate_lines
    return _receipt(paths, receipt)


def parse_step(raw: str) -> tuple[str, str]:
    action, separator, predict = raw.partition("::")
    if not separator or not action.strip() or not predict.strip():
        raise AssayError(
            'each step needs its own prediction: --step "ACTION1 :: <claims>"'
        )
    # Case is normalized later by parse_action (action names only); parameter
    # values in registry runs keep the case the agent typed.
    return action.strip(), predict.strip()


def _validate_batch_tokens(
    tokens: Sequence[str], registry: Mapping[str, Any] | None = None
) -> None:
    """Reject a bad token before any step spends an action."""
    for token in tokens:
        name, _ = parse_action(token, registry)
        if name == "RESET":
            raise AssayError("use `assay reset`; reset cannot hide inside a batch")


def execute_steps(
    paths: RunPaths,
    raw_steps: Sequence[str],
    *,
    at_event: int | None = None,
    declares: Mapping[str, str] | None = None,
    stepper: Any = None,
) -> dict[str, Any]:
    if not raw_steps:
        raise AssayError("no steps supplied")
    registry = load_registry(paths)
    cap = hand_cap(registry)
    if cap is not None and len(raw_steps) > cap:
        from .model import batching_rights

        _, reason = batching_rights(paths)
        raise AssayError(
            f"the batching law caps hand-written batches at {cap} steps "
            f"(got {len(raw_steps)}); longer batches belong to a replay-fit model "
            f"plan (`assay model replay` then `assay model solve`) — currently: {reason}"
        )
    parsed: list[tuple[str, str, list[dict[str, Any]]]] = []
    for raw in raw_steps:
        token, predict = parse_step(raw)
        parsed.append((token, predict, parse_claims(predict, general=registry is not None)))
    _validate_batch_tokens([token for token, _, _ in parsed], registry)
    for _, _, claims in parsed:
        check_channel_references(paths, claims)
        _admit_claims(paths, claims)
    events = _head_events(paths, at_event)
    advisories = _enforce_registry_gates(
        paths,
        registry,
        events,
        kind="commit",
        tokens=[token for token, _, _ in parsed],
        claims_per_token=[claims for _, _, claims in parsed],
        declares=declares,
        in_batch=True,
    )
    check_budget(events, registry, planned=len(parsed))
    start_event = int(events[-1]["id"])
    secrets = tuple((registry or {}).get("secrets") or ())
    records: list[dict[str, Any]] = []
    outcome = "PREDICTED"
    detail = f"all {len(parsed)} steps landed as predicted"
    last_warning: str | None = None
    all_claims: list[dict[str, Any]] = []
    for index, (token, predict, claims) in enumerate(parsed):
        pending, prior, warning, elapsed = _paid_step(
            paths, token, {"predict": predict}, registry=registry, stepper=stepper
        )
        last_warning = warning or last_warning
        graded = grade_action_claims(paths, claims, prior, pending, elapsed_s=elapsed)
        missed, invalid_any, predict_ok = _grade_summary(graded)
        ok = not missed and not invalid_any
        all_claims.extend(claims)
        pending["predict"] = redact(predict, secrets) or ""
        pending["predict_ok"] = predict_ok
        pending["grade"] = graded
        event = _record(paths, pending)
        observe_outcome(paths, registry, load_events(paths), event)
        failed = [line for line in grade_lines(graded) if line.startswith("✗")]
        invalid_lines = [line for line in grade_lines(graded) if line.startswith("!")]
        record = {
            "event": int(event["id"]),
            "action": canonical_action(event),
            "ok": ok,
            "failed": failed,
        }
        if invalid_lines:
            record["invalid"] = invalid_lines
        records.append(record)
        remaining = len(parsed) - index - 1
        discarded = f"; {remaining} remaining steps were discarded" if remaining else ""
        if event["state"] == "WIN":
            outcome, detail = "GAME_COMPLETE", f"the game is complete{discarded}"
            break
        if _level_advanced(event):
            outcome = "LEVEL_COMPLETE"
            detail = f"level advanced after {canonical_action(event)}{discarded}"
            if not ok:
                detail += "; prediction also missed — treat the mechanics as unproven"
            break
        if event["state"] == "GAME_OVER":
            outcome = "GAME_OVER"
            detail = (
                f"environment reported GAME_OVER after {canonical_action(event)}"
                f"{discarded}"
            )
            break
        if missed:
            outcome = "SURPRISE"
            detail = f"step {index + 1} missed: {failed[0][2:]}{discarded}"
            break
        if invalid_any:
            outcome = "INVALID_CLAIM"
            detail = (
                f"step {index + 1} raised an invalid claim: "
                f"{invalid_lines[0][2:]}{discarded}"
            )
            break
    if last_warning:
        detail += f"; scorecard finalization warning: {last_warning}"
    final_events = load_events(paths)
    aggregate_lines: list[str] = []
    if registry is not None:
        aggregate_lines.extend(open_aggregates(paths, all_claims, final_events))
        aggregate_lines.extend(resolve_due(paths, final_events))
    receipt: dict[str, Any] = {
        "kind": "commit",
        "outcome": outcome,
        "detail": detail,
        "start_event": start_event,
        "end_event": int(final_events[-1]["id"]),
        "steps": records,
    }
    if advisories:
        receipt["modules"] = advisories
    if aggregate_lines:
        receipt["aggregates"] = aggregate_lines
    return _receipt(paths, receipt)


def execute_model_plan(
    paths: RunPaths,
    reference: str,
    *,
    at_event: int | None = None,
    stepper: Any = None,
) -> dict[str, Any]:
    """Execute a model plan (registry runs): the ONLY way past the hand-batch
    cap. Rights are exactly replay-fit on THIS journal; every
    step carries machine-generated channel predictions, marked machine."""
    from .channels import channel_value
    from .model import batching_rights, model_hash, plan_path

    registry = load_registry(paths)
    if registry is None:
        raise AssayError(
            "model plans belong to registry runs; this numbered-action run has the "
            "rules.py tier instead"
        )
    events = _head_events(paths, at_event)
    candidate = Path(reference[1:] if reference.startswith("@") else reference)
    path = candidate if candidate.is_absolute() else paths.root / candidate
    try:
        path.resolve().relative_to(paths.root.resolve())
    except ValueError as error:
        raise AssayError("plan reference must stay inside the run directory") from error
    plan = read_json(path)
    if not isinstance(plan, dict) or plan.get("kind") != "model-plan":
        raise AssayError(
            "commit on a registry run accepts only a model plan written by "
            "`assay model solve` (or --step batches under the hand cap)"
        )
    if str(path.resolve()) != str(plan_path(paths).resolve()):
        raise AssayError("model plans execute from .assay/model_plan.json only")
    rights, reason = batching_rights(paths)
    if not rights:
        raise AssayError(f"model plan refused: {reason}")
    source = plan.get("source") or {}
    if source.get("model_hash") != model_hash(paths):
        raise AssayError("plan is stale (model.py changed); rerun `assay model solve`")
    if int(source.get("event", -1)) != int(events[-1]["id"]):
        raise AssayError("plan is stale (the journal moved); rerun `assay model solve`")
    actions = [str(item) for item in plan.get("actions") or ()]
    predictions = plan.get("predictions") or ()
    if not actions or len(actions) != len(predictions):
        raise AssayError("plan needs one prediction per action; rerun `assay model solve`")
    _validate_batch_tokens(actions, registry)
    _enforce_registry_gates(
        paths,
        registry,
        events,
        kind="commit",
        tokens=actions,
        claims_per_token=[[] for _ in actions],
        declares=None,
        in_batch=True,
    )
    check_budget(events, registry, planned=len(actions))
    start_event = int(events[-1]["id"])
    records: list[dict[str, Any]] = []
    outcome = "PREDICTED"
    detail = f"all {len(actions)} model-plan steps landed as predicted"
    last_warning: str | None = None
    for index, (token, expected) in enumerate(zip(actions, predictions)):
        pending, _, warning, _ = _paid_step(
            paths,
            token,
            {"plan": "model", "plan_step": index},
            registry=registry,
            stepper=stepper,
        )
        last_warning = warning or last_warning
        graded: list[dict[str, Any]] = []
        ok = True
        problem = ""
        for name, predicted in (expected or {}).items():
            got_ok, actual = channel_value(paths, str(name), pending)
            held = bool(got_ok) and actual == predicted
            graded.append(
                {
                    "kind": "plan_step",
                    "machine": True,
                    "bucket": "world_model",
                    "channel": str(name),
                    "text": f"model: ch {name} = {predicted!r}",
                    "ok": held,
                    "actual": f"ch {name} = {actual!r}" if got_ok else f"UNGRADABLE: {actual}",
                }
            )
            if not held:
                ok = False
                problem = f"ch {name}: predicted {predicted!r}, actual {actual!r}"
        pending["predict_ok"] = ok
        pending["grade"] = graded
        event = _record(paths, pending)
        observe_outcome(paths, registry, load_events(paths), event)
        records.append(
            {
                "event": int(event["id"]),
                "action": canonical_action(event),
                "ok": ok,
                "machine": True,
                "kind": "plan_step",
                **({"problem": problem} if problem else {}),
            }
        )
        remaining = len(actions) - index - 1
        discarded = f"; {remaining} remaining steps were discarded" if remaining else ""
        if event["state"] == "WIN":
            outcome, detail = "GAME_COMPLETE", f"the game is complete{discarded}"
            break
        if _level_advanced(event):
            outcome = "LEVEL_COMPLETE"
            detail = f"level advanced after {canonical_action(event)}{discarded}"
            break
        if event["state"] == "GAME_OVER":
            outcome = "GAME_OVER"
            detail = f"environment reported GAME_OVER{discarded}"
            break
        if not ok:
            outcome = "SURPRISE"
            detail = (
                f"model-plan step {index + 1} diverged: {problem}; the model is "
                f"contradicted — rerun `assay model replay`{discarded}"
            )
            break
    if last_warning:
        detail += f"; scorecard finalization warning: {last_warning}"
    return _receipt(
        paths,
        {
            "kind": "commit",
            "outcome": outcome,
            "detail": detail,
            "start_event": start_event,
            "end_event": int(load_events(paths)[-1]["id"]),
            "plan": "model",
            "steps": records,
        },
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
    events = _head_events(paths, at_event)
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
    _validate_batch_tokens(actions)
    predictions = plan["predictions"]
    records: list[dict[str, Any]] = []
    outcome = "PREDICTED"
    detail = f"all {len(actions)} steps landed as predicted"
    last_warning: str | None = None
    with import_path(paths.rules, "rules") as module:
        check_contract(module)
        for index, (token, expected) in enumerate(zip(actions, predictions)):
            pending, _, warning, _ = _paid_step(
                paths, token, {"plan": reference, "plan_step": index}
            )
            last_warning = warning or last_warning
            event = _record(paths, pending)
            advanced = _level_advanced(event)
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
    return _receipt(
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


def reset_level(
    paths: RunPaths,
    *,
    because: str | None = None,
    at_event: int | None = None,
    stepper: Any = None,
) -> dict[str, Any]:
    events = _head_events(paths, at_event)
    registry = load_registry(paths)
    advisories = _enforce_registry_gates(
        paths,
        registry,
        events,
        kind="reset",
        tokens=[],
        claims_per_token=[],
        declares=None,
        in_batch=False,
    )
    if registry is not None:
        # Reset itself is never destructive-flagged, but modules may advise
        # (park-with-test); consult with a reset pending explicitly.
        advisories.extend(
            consult_modules(
                paths,
                registry,
                events,
                {"kind": "reset", "name": "RESET", "params": None, "claims": [], "declares": {}},
            )
        )
    check_budget(events, registry, planned=1)
    prior = events[-1]
    reason = because
    if prior["state"] == "GAME_OVER":
        reason = reason or "environment reported GAME_OVER"
    elif not reason:
        raise AssayError(
            'reset needs --because "<why this board is worth abandoning>" '
            "(GAME_OVER excepted)"
        )
    secrets = tuple((registry or {}).get("secrets") or ())
    reason = redact(reason, secrets) or reason
    step = stepper or broker_step
    response, mutation_id, warning = step(paths, "RESET", None, {"because": reason})
    pending = make_event(response, "RESET", None, prior, note=reason)
    pending["mutation_id"] = mutation_id
    event = _record(paths, pending)
    observe_outcome(paths, registry, load_events(paths), event)
    detail = "current board rewound; completed levels and action history preserved"
    if warning:
        detail += f"; scorecard finalization warning: {warning}"
    receipt: dict[str, Any] = {
        "kind": "reset",
        "outcome": "RESET",
        "detail": detail,
        "start_event": int(prior["id"]),
        "end_event": int(event["id"]),
        "because": reason,
    }
    if advisories:
        receipt["modules"] = list(dict.fromkeys(advisories))
    return _receipt(paths, receipt)
