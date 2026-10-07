"""Paid-action execution: act with a graded prediction, batch with per-step
claims, and reset with a reason. Every paid action is journaled by the broker
before the response is recorded, so a crash between spend and record is
recovered on the next start.

The public seam (paid_step, record_event, write_receipt, head_events,
level_advanced, validate_batch_tokens) is what an observation kind's own
executor builds on (the frame world's solve-plan executor in assay_grid).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .agenda import check_rehearsal, consume_approval
from .aggregates import open_aggregates, resolve_due
from .broker import broker_step
from .channels import channel_change_lines, check_channel_references
from .core import (
    AssayError,
    RunPaths,
    append_event,
    append_jsonl,
    atomic_json,
    canonical_action,
    load_events,
    load_jsonl,
    make_event,
    now_iso,
    parse_action,
    read_json,
)
from .extras import kind_for
from .integrity import extend_chain, redact, redact_mapping
from .modules import consult_modules, observe_outcome
from .predictions import grade_action_claims, grade_lines, parse_claims
from .registry import (
    action_spec,
    check_budget,
    check_registry_action,
    check_usd_budget,
    gate_mode,
    hand_cap,
    notes_cap,
    require_registry,
)
from .verifiers import admit_verifier
from .words import unit_noun


def write_receipt(paths: RunPaths, value: Mapping[str, Any]) -> dict[str, Any]:
    record = {"timestamp": now_iso(), **dict(value)}
    paths.receipts.mkdir(parents=True, exist_ok=True)
    event = record.get("end_event", record.get("start_event", "none"))
    atomic_json(paths.receipts / f"{time.time_ns()}-e{event}.json", record)
    append_jsonl(paths.activity, {"kind": "receipt", **record})
    return record


def _archive_notes(paths: RunPaths, completed_level: int) -> None:
    """Snapshot the live notes when a progress unit completes; the live file is kept."""
    if not paths.notes.exists():
        return
    archive = paths.state / "levels" / f"level-{completed_level}.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        archive.write_text(paths.notes.read_text())


def head_events(paths: RunPaths, at_event: int | None) -> list[dict[str, Any]]:
    events = load_events(paths)
    if not events:
        raise AssayError("timeline is empty")
    if events[-1]["state"] == "WIN":
        raise AssayError("the goal is already reached; this run is complete")
    if at_event is not None and at_event != int(events[-1]["id"]):
        raise AssayError(
            f"event guard failed: requested {at_event}, current {events[-1]['id']}"
        )
    return events


def paid_step(
    paths: RunPaths,
    token: str,
    reasoning: Mapping[str, Any] | None = None,
    note: str = "",
    *,
    registry: Mapping[str, Any],
    stepper: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], str | None, float]:
    prior = load_events(paths)[-1]
    name, data = parse_action(token, registry)
    if name == "RESET":
        raise AssayError("use `assay reset`; reset cannot hide inside a batch")
    kind = kind_for(prior)
    # A frame world advertises bare ids; the kind renders them as names.
    advertised = (
        kind.advertised_names(prior) if kind is not None else prior["available_actions"]
    )
    check_registry_action(name, advertised)
    secrets = tuple(registry.get("secrets") or ())
    reasoning = redact_mapping(reasoning, secrets)
    step = stepper or broker_step
    started = time.monotonic()
    response, mutation_id, warning = step(paths, name, data, reasoning)
    elapsed = time.monotonic() - started
    pending = make_event(response, name, data, prior, note=redact(note, secrets) or "")
    pending["mutation_id"] = mutation_id
    return pending, prior, warning, elapsed


def record_event(paths: RunPaths, pending: Mapping[str, Any]) -> dict[str, Any]:
    event = append_event(paths, pending)
    # The journal is chained: the exact appended line advances the
    # daemon-held rolling hash (tamper-evidence).
    try:
        last_line = paths.events.read_text().splitlines()[-1]
    except (FileNotFoundError, IndexError):
        last_line = ""
    if last_line:
        extend_chain(
            paths, last_line, int(event["id"]), win=str(event["state"]) == "WIN"
        )
    kind = kind_for(event)
    if kind is not None:
        kind.after_record(paths, event, load_events(paths))
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
            "next paid action (overflow is auto-archived when a progress unit completes)"
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
    module advisory lines."""
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


def level_advanced(event: Mapping[str, Any]) -> bool:
    level_before = event.get("level_before")
    return level_before is not None and int(event["levels_completed"]) > int(
        level_before
    )


_UNGATED_MARKER = {"optional": "gate_optional", "off": "gate_off"}


def _ungated_act(registry: Mapping[str, Any] | None, predict: str | None) -> bool:
    """Whether this act goes unpredicted under the registry's gate mode. Under
    `off` a supplied prediction is refused before any spend: the control arm
    removes the instrument, it does not make it voluntary."""
    mode = gate_mode(registry)
    bare = not (predict or "").strip()
    if mode == "off":
        if not bare:
            raise AssayError(
                "the prediction gate is off for this run (registry gate: off): "
                "`assay act` takes no --predict here, nothing is graded, and the "
                "audit marks the run invalid for scoring"
            )
        return True
    return mode == "optional" and bare


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
    registry = require_registry(paths)
    events = head_events(paths, at_event)
    # The control arms: gate optional admits a bare act, gate off admits only
    # bare acts (the instrument is removed). Either way the act is journaled
    # UNGATED and the audit keeps the run invalid for scoring.
    ungated = _ungated_act(registry, predict)
    # No observation kind's own claim forms are admitted (owner decision O1).
    claims = [] if ungated else parse_claims(predict)
    check_channel_references(paths, claims)
    _admit_claims(paths, claims)
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
    secrets = tuple(registry.get("secrets") or ())
    predict_journal = redact(predict, secrets) or ""
    because_journal = redact(because, secrets)
    reasoning: dict[str, Any] = {"predict": predict}
    if because:
        reasoning["because"] = because
    if declares:
        reasoning["declares"] = dict(declares)
    pending, prior, warning, elapsed = paid_step(
        paths, token, reasoning, note=because or "", registry=registry, stepper=stepper
    )
    graded = (
        [] if ungated
        else grade_action_claims(paths, claims, prior, pending, elapsed_s=elapsed)
    )
    missed, invalid_any, predict_ok = _grade_summary(graded)
    if ungated:
        predict_ok = None
    ok = not missed and not invalid_any
    pending["predict"] = None if ungated else predict_journal
    pending["predict_ok"] = predict_ok
    pending["grade"] = graded
    if ungated:
        pending[_UNGATED_MARKER[gate_mode(registry)]] = True
    if declares:
        pending["declares"] = {
            key: redact(str(value), secrets) for key, value in declares.items()
        }
    event = record_event(paths, pending)
    all_events = load_events(paths)
    observe_outcome(paths, registry, all_events, event)
    aggregate_lines: list[str] = []
    aggregate_lines.extend(open_aggregates(paths, claims, all_events))
    aggregate_lines.extend(resolve_due(paths, all_events))
    lines = grade_lines(graded)
    if event["state"] == "WIN":
        outcome, detail = "GAME_COMPLETE", "the goal is reached; this run is complete"
    elif level_advanced(event):
        outcome = "LEVEL_COMPLETE"
        detail = (
            f"{unit_noun(event['win_levels'])} {int(event['level_before']) + 1} complete; "
            f"notes archived — re-verify carried assumptions in the new {unit_noun(event['win_levels'])}"
        )
        if not ok:
            detail += "; prediction also missed — treat the mechanics as unproven"
    elif event["state"] == "GAME_OVER":
        outcome = "GAME_OVER"
        detail = (
            "environment reported GAME_OVER; `assay reset` restarts the current "
            f"{unit_noun(event['win_levels'])}"
        )
    elif ungated:
        outcome = "UNGATED"
        detail = (
            f"no prediction (gate: {gate_mode(registry)}); nothing graded — the audit "
            "counts this event as UNGATED"
        )
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
        "predict": None if ungated else predict_journal,
        "grade": lines,
        "because": because_journal,
    }
    if advisories:
        receipt["modules"] = advisories
    if aggregate_lines:
        receipt["aggregates"] = aggregate_lines
    changed = channel_change_lines(paths, prior, event)
    if changed:
        receipt["channels"] = changed
    return write_receipt(paths, receipt)


def parse_step(raw: str, *, allow_bare: bool = False) -> tuple[str, str]:
    action, separator, predict = raw.partition("::")
    if allow_bare and action.strip() and not predict.strip():
        return action.strip(), ""  # gate: optional — an unpredicted step
    if not separator or not action.strip() or not predict.strip():
        raise AssayError(
            'each step needs its own prediction: --step "NAME pname=value :: <claims>"'
        )
    # Case is normalized later by parse_action (action names only); parameter
    # values in registry runs keep the case the agent typed.
    return action.strip(), predict.strip()


def validate_batch_tokens(
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
    registry = require_registry(paths)
    cap = hand_cap(registry)
    if cap is not None and len(raw_steps) > cap:
        from .model import batching_rights

        _, reason = batching_rights(paths)
        raise AssayError(
            f"the batching law caps hand-written batches at {cap} steps "
            f"(got {len(raw_steps)}); longer batches belong to a replay-fit model "
            f"plan (`assay model replay` then `assay model solve`) — currently: {reason}"
        )
    mode = gate_mode(registry)
    bare_ok = mode in _UNGATED_MARKER
    events = head_events(paths, at_event)
    parsed: list[tuple[str, str, list[dict[str, Any]]]] = []
    for raw in raw_steps:
        token, predict = parse_step(raw, allow_bare=bare_ok)
        if mode == "off" and predict:
            raise AssayError(
                "the prediction gate is off for this run (registry gate: off): "
                'steps are bare (`--step "ACTION"`), nothing is graded, and the '
                "audit marks the run invalid for scoring"
            )
        claims = [] if bare_ok and not predict else parse_claims(predict)
        parsed.append((token, predict, claims))
    validate_batch_tokens([token for token, _, _ in parsed], registry)
    for _, _, claims in parsed:
        check_channel_references(paths, claims)
        _admit_claims(paths, claims)
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
    secrets = tuple(registry.get("secrets") or ())
    records: list[dict[str, Any]] = []
    outcome = "PREDICTED"
    detail = f"all {len(parsed)} steps landed as predicted"
    last_warning: str | None = None
    all_claims: list[dict[str, Any]] = []
    for index, (token, predict, claims) in enumerate(parsed):
        pending, prior, warning, elapsed = paid_step(
            paths, token, {"predict": predict}, registry=registry, stepper=stepper
        )
        last_warning = warning or last_warning
        ungated = bare_ok and not predict
        graded = (
            [] if ungated
            else grade_action_claims(paths, claims, prior, pending, elapsed_s=elapsed)
        )
        missed, invalid_any, predict_ok = _grade_summary(graded)
        if ungated:
            predict_ok = None
        ok = not missed and not invalid_any
        all_claims.extend(claims)
        pending["predict"] = None if ungated else (redact(predict, secrets) or "")
        pending["predict_ok"] = predict_ok
        pending["grade"] = graded
        if ungated:
            pending[_UNGATED_MARKER[mode]] = True
        if declares:
            # The same record a single act carries: a declaration made for a
            # batch is evidence on every step it covered.
            pending["declares"] = {
                key: redact(str(value), secrets) for key, value in declares.items()
            }
        event = record_event(paths, pending)
        observe_outcome(paths, registry, load_events(paths), event)
        failed = [line for line in grade_lines(graded) if line.startswith("✗")]
        invalid_lines = [line for line in grade_lines(graded) if line.startswith("!")]
        record = {
            "event": int(event["id"]),
            "action": canonical_action(event),
            "ok": ok,
            "failed": failed,
        }
        if ungated:
            record["ungated"] = True
        if invalid_lines:
            record["invalid"] = invalid_lines
        records.append(record)
        remaining = len(parsed) - index - 1
        discarded = f"; {remaining} remaining steps were discarded" if remaining else ""
        if event["state"] == "WIN":
            outcome, detail = "GAME_COMPLETE", f"the goal is reached; this run is complete{discarded}"
            break
        if level_advanced(event):
            outcome = "LEVEL_COMPLETE"
            detail = (
                f"{unit_noun(event['win_levels'])} advanced after "
                f"{canonical_action(event)}{discarded}"
            )
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
    changed = channel_change_lines(paths, events[-1], final_events[-1])
    if changed:
        receipt["channels"] = changed
    return write_receipt(paths, receipt)


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

    registry = require_registry(paths)
    events = head_events(paths, at_event)
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
    validate_batch_tokens(actions, registry)
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
        pending, _, warning, _ = paid_step(
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
        event = record_event(paths, pending)
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
            outcome, detail = "GAME_COMPLETE", f"the goal is reached; this run is complete{discarded}"
            break
        if level_advanced(event):
            outcome = "LEVEL_COMPLETE"
            detail = (
                f"{unit_noun(event['win_levels'])} advanced after "
                f"{canonical_action(event)}{discarded}"
            )
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
    return write_receipt(
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


def reset_level(
    paths: RunPaths,
    *,
    because: str | None = None,
    at_event: int | None = None,
    declares: Mapping[str, str] | None = None,
    stepper: Any = None,
) -> dict[str, Any]:
    events = head_events(paths, at_event)
    registry = require_registry(paths)
    declared = dict(declares or {})
    advisories = _enforce_registry_gates(
        paths,
        registry,
        events,
        kind="reset",
        tokens=[],
        claims_per_token=[],
        declares=declared,
        in_batch=False,
    )
    # Reset itself is never destructive-flagged, but modules may advise or
    # demand (park-with-test, a conclusion expressed as a reset); consult
    # with the reset pending and its declarations explicitly.
    advisories.extend(
        consult_modules(
            paths,
            registry,
            events,
            {
                "kind": "reset",
                "name": "RESET",
                "params": None,
                "claims": [],
                "declares": declared,
            },
        )
    )
    check_budget(events, registry, planned=1)
    prior = events[-1]
    reason = because
    if prior["state"] == "GAME_OVER":
        reason = reason or "environment reported GAME_OVER"
    elif not reason:
        raise AssayError(
            'reset needs --because "<why this state is worth abandoning>" '
            "(GAME_OVER excepted)"
        )
    secrets = tuple((registry or {}).get("secrets") or ())
    reason = redact(reason, secrets) or reason
    step = stepper or broker_step
    response, mutation_id, warning = step(paths, "RESET", None, {"because": reason})
    pending = make_event(response, "RESET", None, prior, note=reason)
    pending["mutation_id"] = mutation_id
    if declared:
        pending["declares"] = {
            key: redact(str(value), secrets) for key, value in declared.items()
        }
    event = record_event(paths, pending)
    observe_outcome(paths, registry, load_events(paths), event)
    detail = (
        f"current {unit_noun(prior['win_levels'])} rewound; completed "
        f"{unit_noun(prior['win_levels'])}s and action history preserved"
    )
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
    return write_receipt(paths, receipt)
