"""Paid-action execution: act with a graded prediction, batch with per-step
claims, and reset with a reason. Every paid action is journaled by the broker
before the response is recorded, with its parsed claims, so a crash between
spend and record is recovered on the next start with its prediction and
grade (`recovered_pending`, docs/ARCHITECTURE.md section 6.5).

Every function here takes the run (docs/ARCHITECTURE.md section 6.3): the
journal is the held `run.events`, the one writer is `run.append`, and the
small files beside the journal are read from `run.paths` on demand. The
public seam (paid_step, record_event, write_receipt, head_events,
validate_batch_tokens) is what an observation kind's own
executor builds on (the frame world's solve-plan executor in assay_grid).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .agenda import check_rehearsal, consume_approval
from .aggregates import open_aggregates, resolve_due
from .channels import channel_change_lines, check_channel_references
from .core import (
    AssayError,
    append_jsonl,
    atomic_json,
    canonical_action,
    load_jsonl,
    make_event,
    now_iso,
    parse_action,
    read_json,
)
from .extras import kind_for
from .integrity import redact, redact_mapping
from .modules import consult_modules, observe_outcome
from .ops import Step
from .predictions import grade_lines, grade_pending, parse_claims
from .records import Claim, Event, Grade, Mutation, Receipt, ReceiptStep
from .registry import (
    action_spec,
    check_budget,
    check_registry_action,
    check_usd_budget,
    gate_mode,
    hand_cap,
    notes_cap,
    require_registry,
    validate_action,
)
from .verifiers import admit_verifier
from .words import unit_noun

if TYPE_CHECKING:
    from .run import Run

class Stepper(Protocol):
    """The daemon-side spend: the disk verified, the world stepped, and the
    mutation recorded with the parsed claims of an act or a commit step, their
    admitted verifier hashes included, so a crash between spend and record
    keeps the prediction (docs/ARCHITECTURE.md section 6.5). `claims` is None
    for a step that carries no claims of its own (a model-plan step, a reset),
    and the record then carries no `claims` key. Returns the observation, the
    mutation id and the finalization warning. The daemon's `spend` is the
    one implementation: paid actions execute in the daemon (`broker`), and
    every function here takes the stepper it spends through."""

    def __call__(
        self,
        run: Run,
        action: str,
        data: dict[str, Any] | None,
        reasoning: Mapping[str, Any] | None,
        *,
        claims: Sequence[Claim] | None = None,
    ) -> tuple[dict[str, Any], int, str | None]: ...


def write_receipt(run: Run, receipt: Receipt) -> Receipt:
    """Stamp the receipt, write it to `.assay/receipts/` and the activity log."""
    stamped = receipt.updated(timestamp=now_iso())
    record = stamped.to_json()
    run.paths.receipts.mkdir(parents=True, exist_ok=True)
    event = record.get("end_event", record.get("start_event", "none"))
    atomic_json(run.paths.receipts / f"{time.time_ns()}-e{event}.json", record)
    append_jsonl(run.paths.activity, {"kind": "receipt", **record})
    return stamped


def _archive_notes(run: Run, completed_level: int) -> None:
    """Snapshot the live notes when a progress unit completes; the live file is kept."""
    paths = run.paths
    if not paths.notes.exists():
        return
    archive = paths.state / "levels" / f"level-{completed_level}.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        archive.write_text(paths.notes.read_text())


def head_events(run: Run, at_event: int | None) -> list[Event]:
    """The journal as it stands before a paid command, checked against the
    caller's event guard; a snapshot, so a later append leaves it alone."""
    events = run.events
    if not events:
        raise AssayError("timeline is empty", code="TIMELINE_EMPTY")
    if events[-1].state == "WIN":
        raise AssayError(
            "the goal is already reached; this run is complete",
            code="RUN_COMPLETE",
            hint="`assay audit` gives the verdict and `assay export` the knowledge file for the next run",
        )
    if at_event is not None and at_event != events[-1].id:
        raise AssayError(
            f"event guard failed: requested {at_event}, current {events[-1].id}",
            code="EVENT_GUARD",
            hint=f"pass --at {events[-1].id}, or drop --at to act on the current event",
        )
    return list(events)


def paid_step(
    run: Run,
    name: str,
    data: dict[str, Any] | None,
    reasoning: Mapping[str, Any] | None = None,
    note: str = "",
    *,
    stepper: Stepper,
    claims: Sequence[Claim] | None = None,
) -> tuple[Event, Event, str | None, float]:
    """One validated action (`registry.validate_action` or `parse_action`
    ran before) through the stepper: the affordance check, the redaction,
    the spend, the pending event with its mutation id."""
    registry = require_registry(run)
    prior = run.events[-1]
    _refuse_reset_in_batch(name)
    kind = kind_for(prior)
    # A frame world advertises bare ids; the kind renders them as names.
    advertised: Sequence[Any] = (
        kind.advertised_names(prior) if kind is not None else prior.available_actions
    )
    check_registry_action(name, advertised)
    secrets = tuple(registry.get("secrets") or ())
    reasoning = redact_mapping(reasoning, secrets)
    started = time.monotonic()
    response, mutation_id, warning = stepper(run, name, data, reasoning, claims=claims)
    elapsed = time.monotonic() - started
    pending = make_event(response, name, data, prior, note=redact(note, secrets) or "")
    pending = pending.updated(mutation_id=mutation_id)
    return pending, prior, warning, elapsed


def record_event(run: Run, pending: Event) -> Event:
    """Append the event through the one writer (the chain and the anchors
    follow inside it), then the kind's after-record work and the notes
    archive on a completed progress unit."""
    event = run.append(pending)
    kind = kind_for(event)
    if kind is not None:
        kind.after_record(run, event)
    if event.level_advanced:
        _archive_notes(run, int(event.levels_completed))
    return event


def _notes_hard_stop(run: Run) -> None:
    """The notes-cap block: past 2× the cap, paid actions refuse until trimmed.
    The advisory tier lives in status; this is the last resort."""
    cap = notes_cap(run.registry)
    if cap is None:
        return
    try:
        size = len(run.paths.notes.read_text())
    except FileNotFoundError:
        return
    if size > 2 * cap:
        raise AssayError(
            f"NOTES.md is {size} chars, more than twice the {cap}-char cap",
            code="NOTES_CAP",
            hint=(
                "one page is the contract: archive detail elsewhere and trim before the next "
                "paid action (overflow is auto-archived when a progress unit completes)"
            ),
        )


def _enforce_registry_gates(
    run: Run,
    *,
    kind: str,
    actions: Sequence[tuple[str, dict[str, Any] | None]],
    claims_per_action: Sequence[Sequence[Claim]],
    declares: Mapping[str, str] | None,
    in_batch: bool,
) -> list[str]:
    """All pre-spend teeth beyond schema/claims/affordance/budget, over the
    validated (name, params) of every action. Returns module advisory
    lines."""
    registry = require_registry(run)
    check_usd_budget(load_jsonl(run.paths.activity), registry)
    _notes_hard_stop(run)
    advisories: list[str] = []
    declares = dict(declares or {})
    for (name, params), claims in zip(actions, claims_per_action):
        spec = action_spec(registry, name) or {}
        if spec.get("destructive"):
            if in_batch:
                raise AssayError(
                    f"{name} is registered destructive: destructive actions are banned inside batches",
                    code="BATCH_FORBIDDEN",
                    hint=f"take it as a single `assay act {name} ...` with its declaration",
                )
            worst = str(declares.get("worst_case", "")).strip()
            recovery = str(declares.get("recovery", "")).strip()
            if not worst or not recovery:
                raise AssayError(
                    f"{name} is registered destructive and refuses without a "
                    "declared worst case and recovery plan",
                    code="DESTRUCTIVE_UNDECLARED",
                    hint=(
                        'add --declare "worst_case=<what the worst outcome is>" --declare '
                        '"recovery=<how the run recovers>"; the demand is structural (named, '
                        "non-empty), never a demand for optimism; declaring always unlocks the action"
                    ),
                )
        if spec.get("approval"):
            if in_batch:
                raise AssayError(
                    f"{name} is approval-gated and cannot hide inside a batch",
                    code="BATCH_FORBIDDEN",
                    hint=f"take it as a single `assay act {name} ...` once the owner has run `assay approve {name}`",
                )
            consume_approval(run, name)
        check_rehearsal(run, name)
        pending = {
            "kind": kind,
            "name": name,
            "params": params,
            "claims": list(claims),
            "declares": declares,
        }
        advisories.extend(consult_modules(run, pending))
    # De-duplicate module lines a long batch would repeat.
    seen: set[str] = set()
    unique = []
    for line in advisories:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique


def _admit_claims(run: Run, claims: Sequence[Claim]) -> list[Claim]:
    """Admit verifier claims (read, hash, store, journal) before any spend;
    the admitted claims carry their verifier hashes."""
    return [
        admit_verifier(run.paths, claim) if claim.kind == "verify" else claim
        for claim in claims
    ]


def _grade_summary(
    graded: Sequence[Grade],
) -> tuple[bool, bool, bool | None]:
    """(missed, invalid_any, predict_ok-journal-value) for one graded action.
    UNGRADABLE outcomes (stale window, unreadable channel) count like invalid:
    their own meter, never a miss, and they halt a containing batch."""
    invalid_any = any(item.invalid or item.ungradable for item in graded)
    missed = any(
        not item.ok for item in graded if not item.invalid and not item.ungradable
    )
    if missed:
        predict_ok: bool | None = False
    elif invalid_any:
        predict_ok = None
    else:
        predict_ok = True
    return missed, invalid_any, predict_ok


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
                "the prediction gate is off for this run (registry gate: off): nothing is "
                "graded, and the audit marks the run invalid for scoring",
                code="GATE_OFF",
                hint="take the action without --predict",
            )
        return True
    return mode == "optional" and bare


def _graded_pending(
    pending: Event,
    *,
    ungated: bool,
    predict: str | None,
    predict_ok: bool | None,
    graded: Sequence[Grade],
    marker: str | None,
    declares: Mapping[str, str] | None,
    secrets: Sequence[str],
) -> Event:
    """The pending event with its prediction fields on the line."""
    pending = pending.updated(
        predict=None if ungated else predict,
        predict_ok=predict_ok,
        grade=tuple(graded),
    )
    if ungated and marker is not None:
        pending = pending.updated(**{marker: True})
    if declares:
        pending = pending.updated(
            declares={key: redact(str(value), secrets) or "" for key, value in declares.items()}
        )
    return pending


def recovered_pending(run: Run, mutation: Mutation, prior: Event, pending: Event) -> Event:
    """The pending event of a recovered spend with its prediction on the line
    (docs/ARCHITECTURE.md section 6.5): the record's claims, their admitted
    verifier hashes included, graded against the stored response through the
    live path's grader with the step duration unknown, and `predict`,
    `predict_ok`, `grade` and `declares` from the record's reasoning, so the
    event is gated by its fields like the one the daemon would have written.
    A record whose claims are empty was a bare act of a control arm, and it
    is journaled as the live path journals one: UNGATED, with the mode's
    marker. The caller passes only a record that carries `claims`."""
    registry = run.registry
    secrets = tuple((registry or {}).get("secrets") or ())
    reasoning = mutation.reasoning or {}
    predict = str(reasoning.get("predict") or "")
    declares = reasoning.get("declares")
    claims = list(mutation.claims or ())
    ungated = not claims
    graded = [] if ungated else grade_pending(run, claims, prior, pending, elapsed_s=None)
    _, _, predict_ok = _grade_summary(graded)
    return _graded_pending(
        pending,
        ungated=ungated,
        predict=redact(predict, secrets) or "",
        predict_ok=None if ungated else predict_ok,
        graded=graded,
        marker=_UNGATED_MARKER.get(gate_mode(registry)),
        declares=dict(declares) if isinstance(declares, Mapping) else None,
        secrets=secrets,
    )


def execute_action(
    run: Run,
    action: str,
    params: Mapping[str, Any] | None,
    *,
    predict: str,
    because: str | None = None,
    at_event: int | None = None,
    declares: Mapping[str, str] | None = None,
    stepper: Stepper,
) -> Receipt:
    """One paid action from its registered name and parameters object, as
    the wire carries them (docs/ARCHITECTURE.md section 7.2): validated
    against the pinned registry before anything else."""
    registry = require_registry(run)
    name, data = validate_action(registry, action, params)
    events = head_events(run, at_event)
    # The control arms: gate optional admits a bare act, gate off admits only
    # bare acts (the instrument is removed). Either way the act is journaled
    # UNGATED and the audit keeps the run invalid for scoring.
    ungated = _ungated_act(registry, predict)
    # No observation kind's own claim forms are admitted: parse_claims runs
    # without a kind, so a frame form is refused by name before any spend, the
    # rule every published journal was recorded under.
    claims = [] if ungated else parse_claims(predict)
    check_channel_references(run, claims)
    claims = _admit_claims(run, claims)
    advisories = _enforce_registry_gates(
        run,
        kind="act",
        actions=[(name, data)],
        claims_per_action=[claims],
        declares=declares,
        in_batch=False,
    )
    check_budget(events, registry, planned=1)
    start_event = events[-1].id
    secrets = tuple(registry.get("secrets") or ())
    predict_journal = redact(predict, secrets) or ""
    because_journal = redact(because, secrets)
    reasoning: dict[str, Any] = {"predict": predict}
    if because:
        reasoning["because"] = because
    if declares:
        reasoning["declares"] = dict(declares)
    pending, prior, warning, elapsed = paid_step(
        run, name, data, reasoning, note=because or "", stepper=stepper, claims=claims
    )
    graded = (
        [] if ungated
        else grade_pending(run, claims, prior, pending, elapsed_s=elapsed)
    )
    missed, invalid_any, predict_ok = _grade_summary(graded)
    if ungated:
        predict_ok = None
    ok = not missed and not invalid_any
    pending = _graded_pending(
        pending,
        ungated=ungated,
        predict=predict_journal,
        predict_ok=predict_ok,
        graded=graded,
        marker=_UNGATED_MARKER.get(gate_mode(registry)),
        declares=declares,
        secrets=secrets,
    )
    event = record_event(run, pending)
    observe_outcome(run, event)
    aggregate_lines: list[str] = []
    aggregate_lines.extend(open_aggregates(run, claims))
    aggregate_lines.extend(resolve_due(run))
    lines = grade_lines(graded)
    if event.state == "WIN":
        outcome, detail = "GAME_COMPLETE", "the goal is reached; this run is complete"
    elif event.level_advanced:
        outcome = "LEVEL_COMPLETE"
        detail = (
            f"{unit_noun(event.win_levels)} {int(event.level_before or 0) + 1} complete; "
            f"notes archived; re-verify carried assumptions in the new {unit_noun(event.win_levels)}"
        )
        if not ok:
            detail += "; prediction also missed: treat the mechanics as unproven"
    elif event.state == "GAME_OVER":
        outcome = "GAME_OVER"
        detail = (
            "environment reported GAME_OVER; `assay reset` restarts the current "
            f"{unit_noun(event.win_levels)}"
        )
    elif ungated:
        outcome = "UNGATED"
        detail = (
            f"no prediction (gate: {gate_mode(registry)}); nothing graded; the audit "
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
        detail += f"; finalization warning from the world: {warning}"
    changed = channel_change_lines(run, prior, event)
    receipt = Receipt(
        kind="act",
        outcome=outcome,
        detail=detail,
        start_event=start_event,
        end_event=event.id,
        level=min(event.win_levels, event.levels_completed + 1),
        action=canonical_action(event),
        predict=None if ungated else predict_journal,
        grade=tuple(lines),
        because=because_journal,
        modules=tuple(advisories) if advisories else None,
        aggregates=tuple(aggregate_lines) if aggregate_lines else None,
        channels=tuple(changed) if changed else None,
    )
    return write_receipt(run, receipt)


STEP_SYNTAX = 'each step needs its own prediction: --step "NAME pname=value :: <claims>"'
STEP_HINT = "`assay act --help` lists the claim forms"


def split_step(raw: str) -> tuple[str, str | None]:
    """The client's half of a step: `NAME pname=value :: claims` into the
    action token and the claims text, None when the step carries none (no
    `::`, or nothing after it); whether a bare step is admitted is the
    daemon's rule (`execute_steps`), by the registry's gate. The token keeps
    the case the agent typed; `parse_action` folds the name."""
    action, separator, predict = raw.partition("::")
    if not action.strip():
        raise AssayError(STEP_SYNTAX, code="PREDICTION_REQUIRED", hint=STEP_HINT)
    return action.strip(), (predict.strip() or None) if separator else None


def _refuse_reset_in_batch(name: str) -> None:
    if name == "RESET":
        raise AssayError(
            "reset cannot hide inside a batch", code="BATCH_FORBIDDEN", hint="use `assay reset`"
        )


def validate_batch_tokens(
    tokens: Sequence[str], registry: Mapping[str, Any]
) -> list[tuple[str, dict[str, Any] | None]]:
    """The typed tokens of a model plan, each parsed against the registry
    before any step spends an action; a reset is refused."""
    actions: list[tuple[str, dict[str, Any] | None]] = []
    for token in tokens:
        name, data = parse_action(token, registry)
        _refuse_reset_in_batch(name)
        actions.append((name, data))
    return actions


def execute_steps(
    run: Run,
    steps: Sequence[Step],
    *,
    at_event: int | None = None,
    declares: Mapping[str, str] | None = None,
    stepper: Stepper,
) -> Receipt:
    """A hand-written batch of `{action, params, predict}` steps, as the
    wire carries them (docs/ARCHITECTURE.md section 7.2): every action
    validated against the pinned registry and every prediction parsed
    before the first step spends. A step without a prediction is admitted
    under `gate: optional` and `gate: off` (journaled UNGATED) and refused
    under the required gate."""
    if not steps:
        raise AssayError(
            "no steps supplied",
            code="COMMAND_ARGS",
            hint='pass --step "NAME pname=value :: claims", repeated in execution order',
        )
    registry = require_registry(run)
    cap = hand_cap(registry)
    if cap is not None and len(steps) > cap:
        from .model import batching_rights

        _, reason = batching_rights(run)
        raise AssayError(
            f"the batching law caps hand-written batches at {cap} steps (got {len(steps)})",
            code="BATCH_CAP",
            hint=(
                "longer batches belong to a replay-fit model plan (`assay model replay` then "
                f"`assay model solve`); currently: {reason}"
            ),
        )
    mode = gate_mode(registry)
    bare_ok = mode in _UNGATED_MARKER
    events = head_events(run, at_event)
    parsed: list[tuple[str, dict[str, Any] | None, str, list[Claim]]] = []
    for step in steps:
        name, data = validate_action(registry, step.action, step.params)
        _refuse_reset_in_batch(name)
        predict = (step.predict or "").strip()
        if not predict and not bare_ok:
            raise AssayError(STEP_SYNTAX, code="PREDICTION_REQUIRED", hint=STEP_HINT)
        if mode == "off" and predict:
            raise AssayError(
                "the prediction gate is off for this run (registry gate: off): nothing is "
                "graded, and the audit marks the run invalid for scoring",
                code="GATE_OFF",
                hint='steps are bare on this run: --step "ACTION"',
            )
        claims = [] if bare_ok and not predict else parse_claims(predict)
        parsed.append((name, data, predict, claims))
    admitted: list[tuple[str, dict[str, Any] | None, str, list[Claim]]] = []
    for name, data, predict, claims in parsed:
        check_channel_references(run, claims)
        admitted.append((name, data, predict, _admit_claims(run, claims)))
    parsed = admitted
    advisories = _enforce_registry_gates(
        run,
        kind="commit",
        actions=[(name, data) for name, data, _, _ in parsed],
        claims_per_action=[claims for _, _, _, claims in parsed],
        declares=declares,
        in_batch=True,
    )
    check_budget(events, registry, planned=len(parsed))
    start_event = events[-1].id
    before = events[-1]
    secrets = tuple(registry.get("secrets") or ())
    records: list[ReceiptStep] = []
    outcome = "PREDICTED"
    detail = f"all {len(parsed)} steps landed as predicted"
    last_warning: str | None = None
    all_claims: list[Claim] = []
    for index, (name, data, predict, claims) in enumerate(parsed):
        pending, prior, warning, elapsed = paid_step(
            run, name, data, {"predict": predict}, stepper=stepper, claims=claims
        )
        last_warning = warning or last_warning
        ungated = bare_ok and not predict
        graded = (
            [] if ungated
            else grade_pending(run, claims, prior, pending, elapsed_s=elapsed)
        )
        missed, invalid_any, predict_ok = _grade_summary(graded)
        if ungated:
            predict_ok = None
        ok = not missed and not invalid_any
        all_claims.extend(claims)
        # The same record a single act carries: a declaration made for a
        # batch is evidence on every step it covered.
        pending = _graded_pending(
            pending,
            ungated=ungated,
            predict=redact(predict, secrets) or "",
            predict_ok=predict_ok,
            graded=graded,
            marker=_UNGATED_MARKER.get(mode),
            declares=declares,
            secrets=secrets,
        )
        event = record_event(run, pending)
        observe_outcome(run, event)
        failed = [line for line in grade_lines(graded) if line.startswith("✗")]
        invalid_lines = [line for line in grade_lines(graded) if line.startswith("!")]
        records.append(
            ReceiptStep(
                event=event.id,
                action=canonical_action(event),
                ok=ok,
                failed=tuple(failed),
                invalid=tuple(invalid_lines) if invalid_lines else None,
                ungated=ungated,
            )
        )
        remaining = len(parsed) - index - 1
        discarded = f"; {remaining} remaining steps were discarded" if remaining else ""
        if event.state == "WIN":
            outcome, detail = "GAME_COMPLETE", f"the goal is reached; this run is complete{discarded}"
            break
        if event.level_advanced:
            outcome = "LEVEL_COMPLETE"
            detail = (
                f"{unit_noun(event.win_levels)} advanced after "
                f"{canonical_action(event)}{discarded}"
            )
            if not ok:
                detail += "; prediction also missed: treat the mechanics as unproven"
            break
        if event.state == "GAME_OVER":
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
        detail += f"; finalization warning from the world: {last_warning}"
    final_events = run.events
    aggregate_lines: list[str] = []
    aggregate_lines.extend(open_aggregates(run, all_claims))
    aggregate_lines.extend(resolve_due(run))
    changed = channel_change_lines(run, before, final_events[-1])
    receipt = Receipt(
        kind="commit",
        outcome=outcome,
        detail=detail,
        start_event=start_event,
        end_event=final_events[-1].id,
        steps=tuple(records),
        modules=tuple(advisories) if advisories else None,
        aggregates=tuple(aggregate_lines) if aggregate_lines else None,
        channels=tuple(changed) if changed else None,
    )
    return write_receipt(run, receipt)


RESOLVE_HINT = "rerun `assay model solve`"


def execute_model_plan(
    run: Run,
    reference: str,
    *,
    at_event: int | None = None,
    stepper: Stepper,
) -> Receipt:
    """Execute a model plan (registry runs): the ONLY way past the hand-batch
    cap. Rights are exactly replay-fit on THIS journal; every
    step carries machine-generated channel predictions, marked machine."""
    from .channels import channel_value
    from .model import batching_rights, model_hash, plan_path

    paths = run.paths
    registry = require_registry(run)
    events = head_events(run, at_event)
    candidate = Path(reference[1:] if reference.startswith("@") else reference)
    path = candidate if candidate.is_absolute() else paths.root / candidate
    try:
        path.resolve().relative_to(paths.root.resolve())
    except ValueError as error:
        raise AssayError(
            "plan reference must stay inside the run directory",
            code="PATH_INVALID",
            hint="pass @.assay/model_plan.json, the file `assay model solve` writes",
        ) from error
    plan = read_json(path)
    if not isinstance(plan, dict) or plan.get("kind") != "model-plan":
        raise AssayError(
            "commit on a registry run accepts only a model plan written by `assay model solve`",
            code="PLAN_INVALID",
            hint='pass @.assay/model_plan.json, or --step "NAME pname=value :: claims" batches under the hand cap',
        )
    if str(path.resolve()) != str(plan_path(paths).resolve()):
        raise AssayError(
            "model plans execute from .assay/model_plan.json only",
            code="PLAN_INVALID",
            hint="pass @.assay/model_plan.json, the file `assay model solve` writes",
        )
    rights, reason = batching_rights(run)
    if not rights:
        raise AssayError(
            f"model plan refused: {reason}",
            code="BATCHING_RIGHTS",
            hint="`assay model replay` grades the model on this journal and can earn batching rights",
        )
    source = plan.get("source") or {}
    if source.get("model_hash") != model_hash(paths):
        raise AssayError("plan is stale (model.py changed)", code="PLAN_STALE", hint=RESOLVE_HINT)
    if int(source.get("event", -1)) != events[-1].id:
        raise AssayError("plan is stale (the journal moved)", code="PLAN_STALE", hint=RESOLVE_HINT)
    actions = [str(item) for item in plan.get("actions") or ()]
    predictions = plan.get("predictions") or ()
    if not actions or len(actions) != len(predictions):
        raise AssayError("plan needs one prediction per action", code="PLAN_INVALID", hint=RESOLVE_HINT)
    parsed = validate_batch_tokens(actions, registry)
    # The modules' advisories ride on this receipt like on an act's or a
    # hand batch's; they were consulted and discarded before #24.
    advisories = _enforce_registry_gates(
        run,
        kind="commit",
        actions=parsed,
        claims_per_action=[[] for _ in parsed],
        declares=None,
        in_batch=True,
    )
    check_budget(events, registry, planned=len(actions))
    start_event = events[-1].id
    records: list[ReceiptStep] = []
    outcome = "PREDICTED"
    detail = f"all {len(actions)} model-plan steps landed as predicted"
    last_warning: str | None = None
    for index, ((name, data), expected) in enumerate(zip(parsed, predictions)):
        pending, _, warning, _ = paid_step(
            run,
            name,
            data,
            {"plan": "model", "plan_step": index},
            stepper=stepper,
        )
        last_warning = warning or last_warning
        graded: list[Grade] = []
        ok = True
        problem = ""
        for name, predicted in (expected or {}).items():
            got_ok, actual = channel_value(run, str(name), pending)
            held = bool(got_ok) and actual == predicted
            graded.append(
                Grade(
                    kind="plan_step",
                    text=f"model: ch {name} = {predicted!r}",
                    ok=held,
                    actual=f"ch {name} = {actual!r}" if got_ok else f"UNGRADABLE: {actual}",
                    bucket="world_model",
                    channel=str(name),
                    machine=True,
                )
            )
            if not held:
                ok = False
                problem = f"ch {name}: predicted {predicted!r}, actual {actual!r}"
        pending = pending.updated(predict_ok=ok, grade=tuple(graded))
        event = record_event(run, pending)
        observe_outcome(run, event)
        records.append(
            ReceiptStep(
                event=event.id,
                action=canonical_action(event),
                ok=ok,
                machine=True,
                kind="plan_step",
                problem=problem or None,
            )
        )
        remaining = len(actions) - index - 1
        discarded = f"; {remaining} remaining steps were discarded" if remaining else ""
        if event.state == "WIN":
            outcome, detail = "GAME_COMPLETE", f"the goal is reached; this run is complete{discarded}"
            break
        if event.level_advanced:
            outcome = "LEVEL_COMPLETE"
            detail = (
                f"{unit_noun(event.win_levels)} advanced after "
                f"{canonical_action(event)}{discarded}"
            )
            break
        if event.state == "GAME_OVER":
            outcome = "GAME_OVER"
            detail = f"environment reported GAME_OVER{discarded}"
            break
        if not ok:
            outcome = "SURPRISE"
            detail = (
                f"model-plan step {index + 1} diverged: {problem}; the model is "
                f"contradicted; rerun `assay model replay`{discarded}"
            )
            break
    if last_warning:
        detail += f"; finalization warning from the world: {last_warning}"
    return write_receipt(
        run,
        Receipt(
            kind="commit",
            outcome=outcome,
            detail=detail,
            start_event=start_event,
            end_event=run.events[-1].id,
            plan="model",
            steps=tuple(records),
            modules=tuple(advisories) if advisories else None,
        ),
    )


def reset_level(
    run: Run,
    *,
    because: str | None = None,
    at_event: int | None = None,
    declares: Mapping[str, str] | None = None,
    stepper: Stepper,
) -> Receipt:
    events = head_events(run, at_event)
    registry = require_registry(run)
    declared = dict(declares or {})
    advisories = _enforce_registry_gates(
        run,
        kind="reset",
        actions=[],
        claims_per_action=[],
        declares=declared,
        in_batch=False,
    )
    # Reset itself is never destructive-flagged, but modules may advise or
    # demand (park-with-test, a conclusion expressed as a reset); consult
    # with the reset pending and its declarations explicitly.
    advisories.extend(
        consult_modules(
            run,
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
    if prior.state == "GAME_OVER":
        reason = reason or "environment reported GAME_OVER"
    elif not reason:
        raise AssayError(
            'reset needs --because "<why this state is worth abandoning>" '
            "(GAME_OVER excepted)",
            code="RESET_REASON",
        )
    secrets = tuple(registry.get("secrets") or ())
    reason = redact(reason, secrets) or reason
    response, mutation_id, warning = stepper(run, "RESET", None, {"because": reason})
    pending = make_event(response, "RESET", None, prior, note=reason).updated(
        mutation_id=mutation_id
    )
    if declared:
        pending = pending.updated(
            declares={key: redact(str(value), secrets) or "" for key, value in declared.items()}
        )
    event = record_event(run, pending)
    observe_outcome(run, event)
    detail = (
        f"current {unit_noun(prior.win_levels)} rewound; completed "
        f"{unit_noun(prior.win_levels)}s and action history preserved"
    )
    if warning:
        detail += f"; finalization warning from the world: {warning}"
    receipt = Receipt(
        kind="reset",
        outcome="RESET",
        detail=detail,
        start_event=prior.id,
        end_event=event.id,
        because=reason,
        modules=tuple(dict.fromkeys(advisories)) if advisories else None,
    )
    return write_receipt(run, receipt)
