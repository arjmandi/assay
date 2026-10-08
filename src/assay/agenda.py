"""Agenda: the host-pinned goal, the goal-proposal lane, owner authority,
and the emergence meter.

The standing goal and its predicate are HOST state: the goal channel (the
environment's win state) is pinned at registration and re-presented in every
status until code says achieved; an agent-invented proxy can never silently
replace it (the eight-of-eight proxy-chasing record). Subgoals live in notes
text, where they lived in every winning run on record.

The goal-proposal lane is vision-1's counterpart: the agent may AT ANY TIME
propose a standing-goal revision (`assay goal propose`), journaled, surfaced in
status, and the OWNER ratifies (`assay goal ratify --token`). Ratification
changes the displayed standing-goal text; the win predicate itself only
changes at registration. The owner token is minted at `assay start`, printed
once for the launcher to store outside the run, and only its sha256 is kept in
the run state; the agent cannot recover it from artifacts. The same token
authorizes one-shot action approvals (`assay approve`) and liveness waivers
(`assay waive`).

The three owner operations run in the daemon against the hash it holds
(docs/ARCHITECTURE.md section 7.2): an approval lives in the daemon's memory
(`run.approvals`, the run the daemon holds) for its 600 seconds and is consumed
there by the paid path; a waiver is the `liveness_waived` activity record,
rebuilt into `run.waivers` at every load; a ratification is the daemon's write
of `goal.json`. None of the three has a file of its own, so nothing the agent
writes can grant one.

The emergence meter is free journal counters over agent-initiated acts no
module demanded: self-declared channels, self-authored verifiers, self-built
models, self-proposed goals: measuring emergence instead of assuming it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core import AssayError, RunPaths, append_jsonl, atomic_json, load_jsonl, read_json

if TYPE_CHECKING:
    from .run import Run

APPROVAL_EXPIRY_SECONDS = 600.0


def owner_path(paths: RunPaths) -> Path:
    return paths.state / "owner.json"


def goal_path(paths: RunPaths) -> Path:
    return paths.state / "goal.json"


def proposals_path(paths: RunPaths) -> Path:
    return paths.state / "proposals.jsonl"


def mint_owner_token(paths: RunPaths) -> str:
    """Mint the run's owner token at start; store only its hash. Returns the
    token exactly once; the caller prints it for the owner/launcher."""
    # token_urlsafe uses the URL-safe base64 alphabet, which includes "-", so
    # ~1 in 32 tokens starts with "-", which argparse then reads as a missing
    # value for `--token`. Re-mint until it does not lead with a dash.
    token = secrets.token_urlsafe(24)
    while token.startswith("-"):
        token = secrets.token_urlsafe(24)
    atomic_json(
        owner_path(paths),
        {"sha256": hashlib.sha256(token.encode()).hexdigest(), "minted_at": time.time()},
    )
    return token


def require_owner(run: Run, token: str | None) -> None:
    """The owner token against the held hash."""
    if not run.owner_hash:
        raise AssayError(
            "no owner token was minted for this run; owner operations are unavailable",
            code="OWNER_TOKEN",
        )
    if not token or not hmac.compare_digest(
        hashlib.sha256(token.encode()).hexdigest(), run.owner_hash
    ):
        raise AssayError(
            "owner authority required: pass --token <the token printed at start>. "
            "The agent proposes; the owner ratifies.",
            code="OWNER_TOKEN",
            hint="ask the operator to run this command with --token; the agent never holds the token",
        )


def standing_goal(run: Run) -> dict[str, Any]:
    """The goal presented in status: ratified revision > registry text > the
    built-in predicate description."""
    ratified = read_json(goal_path(run.paths), None)
    if isinstance(ratified, dict) and ratified.get("text"):
        return {"text": str(ratified["text"]), "source": "ratified"}
    registered = ((run.registry or {}).get("goal") or {}).get("text")
    if registered:
        return {"text": str(registered), "source": "registry"}
    return {
        "text": "reach the environment's WIN state (every progress unit complete)",
        "source": "host",
    }


def propose_goal(run: Run, text: str, because: str | None) -> dict[str, Any]:
    if not text or not text.strip():
        raise AssayError("a goal proposal needs non-empty text", code="COMMAND_ARGS")
    proposals = load_jsonl(proposals_path(run.paths))
    record = {
        "kind": "goal_proposed",
        "id": len(proposals) + 1,
        "text": text.strip(),
        "because": (because or "").strip(),
        "status": "pending",
    }
    append_jsonl(proposals_path(run.paths), record)
    append_jsonl(run.paths.activity, record)
    return record


def proposal_text(record: Mapping[str, Any]) -> str:
    """The line `assay goal propose` prints for the proposal it journaled."""
    return (
        f"GOAL | proposal #{record['id']} journaled, awaiting owner "
        "ratification (`assay goal ratify ID --token ...`)"
    )


def proposals_text(proposals: Sequence[Mapping[str, Any]]) -> list[str]:
    """The lines `assay goal list` prints: one per proposal with its status,
    or the one line saying there is none."""
    if not proposals:
        return ["GOAL | no proposals"]
    return [
        f"  #{entry['id']} [{entry['status']}] {entry['text']}"
        + (f"; {entry['because']}" if entry.get("because") else "")
        for entry in proposals
    ]


def list_proposals(run: Run) -> list[dict[str, Any]]:
    proposals = load_jsonl(proposals_path(run.paths))
    resolved: dict[int, str] = {}
    for record in proposals:
        if record.get("kind") == "goal_resolved":
            resolved[int(record["id"])] = str(record["status"])
    output = []
    for record in proposals:
        if record.get("kind") != "goal_proposed":
            continue
        entry = dict(record)
        entry["status"] = resolved.get(int(record["id"]), "pending")
        output.append(entry)
    return output


def ratify_goal(run: Run, proposal_id: int, token: str | None) -> dict[str, Any]:
    require_owner(run, token)
    proposals = {int(item["id"]): item for item in list_proposals(run)}
    proposal = proposals.get(int(proposal_id))
    if proposal is None:
        raise AssayError(f"no goal proposal with id {proposal_id}", code="GOAL_PROPOSAL")
    if proposal["status"] != "pending":
        raise AssayError(f"proposal {proposal_id} is already {proposal['status']}", code="GOAL_PROPOSAL")
    append_jsonl(
        proposals_path(run.paths),
        {"kind": "goal_resolved", "id": int(proposal_id), "status": "ratified"},
    )
    atomic_json(
        goal_path(run.paths),
        {"text": proposal["text"], "ratified_from": int(proposal_id), "at": time.time()},
    )
    append_jsonl(
        run.paths.activity,
        {"kind": "goal_ratified", "id": int(proposal_id), "text": proposal["text"]},
    )
    return proposal


def grant_approval(run: Run, action: str, token: str | None) -> str:
    """One-shot, expiring owner approval for an approval-flagged action, held
    on the run the daemon holds and nowhere else, stamped on the monotonic
    clock so a wall-clock change neither extends nor cuts it; a new grant
    replaces an older one. Returns the action's name as held."""
    require_owner(run, token)
    name = action.upper()
    run.approvals[name] = time.monotonic()
    append_jsonl(run.paths.activity, {"kind": "approval_granted", "action": name})
    return name


def check_approval(run: Run, action: str) -> str:
    """Default-deny: a fresh, unused grant for this action must be held;
    refused otherwise, before any spend. The grant is consumed apart, after
    the disk is verified (`consume_approval`), so a refusal does not burn
    it. Returns the action's name as held."""
    name = action.upper()
    granted_at = run.approvals.get(name)
    if granted_at is None:
        raise AssayError(
            f"{action} is approval-gated (default-deny) and has no fresh approval",
            code="APPROVAL_REQUIRED",
            hint=f"ask the operator to grant one use with `assay approve {action} --token ...`",
        )
    if time.monotonic() - granted_at > APPROVAL_EXPIRY_SECONDS:
        raise AssayError(
            f"{action}'s approval expired after {int(APPROVAL_EXPIRY_SECONDS)}s "
            "(default-deny with timeout)",
            code="APPROVAL_REQUIRED",
            hint=f"ask the operator to run `assay approve {action} --token ...` again",
        )
    return name


def consume_approval(run: Run, action: str) -> None:
    """Use the held grant up: checked again, deleted, and `approval_used`
    recorded. The daemon calls it after the disk is verified and before the
    world step (section 8.3)."""
    name = check_approval(run, action)
    del run.approvals[name]
    append_jsonl(run.paths.activity, {"kind": "approval_used", "action": name})


def grant_waiver(run: Run, action: str, token: str | None, because: str) -> dict[str, Any]:
    """Owner waiver for a liveness rehearsal quota: explicit and journaled.
    The activity record is the waiver; the held set is rebuilt from the
    records at every load (`load_waivers`). Returns the record."""
    require_owner(run, token)
    if not because or not because.strip():
        raise AssayError("a liveness waiver needs --because <why it is safe now>", code="COMMAND_ARGS")
    name = action.upper()
    record = {"kind": "liveness_waived", "action": name, "because": because.strip()}
    run.waivers.add(name)
    append_jsonl(run.paths.activity, record)
    return record


def load_waivers(activity: Iterable[Mapping[str, Any]]) -> set[str]:
    """The waived actions, from the `liveness_waived` records of the
    activity log: what `Run.load` holds as `run.waivers`."""
    return {
        str(record["action"]).upper()
        for record in activity
        if record.get("kind") == "liveness_waived" and isinstance(record.get("action"), str)
    }


def has_waiver(run: Run, action: str) -> bool:
    return action.upper() in run.waivers


def check_rehearsal(run: Run, action: str) -> None:
    """Enforced liveness, minimal real form: a live actuator with a
    rehearsal quota refuses until an imported sim digest shows enough graded
    attempts under the SAME registry but a DIFFERENT binding, or the owner
    journals a waiver. Inert unless declared."""
    from .registry import action_spec

    registry = run.registry
    spec = action_spec(registry, action) if registry else None
    if not spec or spec.get("liveness") != "live" or not spec.get("rehearsal_quota"):
        return
    if has_waiver(run, action):
        return
    quota = int(spec["rehearsal_quota"])
    imported = read_json(run.paths.state / "imported" / "knowledge.json", None)
    if isinstance(imported, dict):
        own_config = run.config
        same_registry = imported.get("registry_hash") == own_config.get("registry_hash")
        different_binding = imported.get("binding_hash") not in (
            None,
            own_config.get("binding_hash"),
        )
        attempts = int(
            ((imported.get("digest") or {}).get("per_action") or {})
            .get(action.upper(), {})
            .get("graded", 0)
        )
        if same_registry and different_binding and attempts >= quota:
            return
    raise AssayError(
        f"{action} is a LIVE actuator with a rehearsal quota of {quota} that is not met",
        code="REHEARSAL_REQUIRED",
        hint=(
            "import a sim-binding run's knowledge (same registry, different binding) with "
            f">= {quota} graded attempts, or ask the operator to journal "
            f"`assay waive {action} --token ... --because ...` (default-deny)"
        ),
    )


def emergence_meter(run: Run) -> dict[str, int]:
    """Free counters over agent-initiated acts no module demanded."""
    activity = load_jsonl(run.paths.activity)
    verifier_hashes = {
        record.get("hash")
        for record in activity
        if record.get("kind") == "verifier_admitted"
    }
    channels = {
        record.get("channel")
        for record in activity
        if record.get("kind") == "channel_declared"
    }
    proposals = sum(1 for record in activity if record.get("kind") == "goal_proposed")
    model_replays = sum(1 for record in activity if record.get("kind") == "model_replay")
    return {
        "verifiers": len(verifier_hashes - {None}),
        "channels": len(channels - {None}),
        "model_replays": model_replays,
        "goal_proposals": proposals,
    }


def emergence_text(verifiers: int, channels: int, model_replays: int, goal_proposals: int) -> str:
    return (
        f"EMERGENCE | self-authored verifiers {verifiers} | declared "
        f"channels {channels} | model replays {model_replays} | "
        f"goal proposals {goal_proposals}"
    )


def emergence_line(run: Run) -> str:
    return emergence_text(**emergence_meter(run))


def agenda_text(
    goal_text: str, goal_source: str, achieved: bool, pending: Sequence[tuple[int, str]]
) -> list[str]:
    """The AGENDA lines from their facts: the standing goal with its source,
    whether it is achieved, and the pending proposals (id, text), the newest
    last."""
    lines = [
        f"AGENDA | goal ({goal_source}): {goal_text} | "
        f"{'ACHIEVED' if achieved else 'not achieved'}"
    ]
    if pending:
        newest_id, newest_text = pending[-1]
        lines.append(
            f"AGENDA | {len(pending)} goal proposal(s) awaiting the owner; newest "
            f"#{newest_id}: {str(newest_text)[:120]}"
        )
    return lines


def agenda_lines(run: Run) -> list[str]:
    goal = standing_goal(run)
    events = run.events
    achieved = bool(events) and str(events[-1].state) == "WIN"
    pending = [
        (int(item["id"]), str(item["text"]))
        for item in list_proposals(run)
        if item["status"] == "pending"
    ]
    return agenda_text(str(goal["text"]), str(goal["source"]), achieved, pending)
