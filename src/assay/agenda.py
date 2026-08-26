"""Agenda: the host-pinned goal, the goal-proposal lane, owner authority,
and the emergence meter.

The standing goal and its predicate are HOST state: the goal channel (the
environment's win state) is pinned at registration and re-presented in every
status until code says achieved — an agent-invented proxy can never silently
replace it (the eight-of-eight proxy-chasing record). Subgoals live in notes
text, where they lived in every winning run on record.

The goal-proposal lane is vision-1's counterpart: the agent may AT ANY TIME
propose a standing-goal revision (`assay goal propose`) — journaled, surfaced in
status — and the OWNER ratifies (`assay goal ratify --token`). Ratification
changes the displayed standing-goal text; the win predicate itself only
changes at registration. The owner token is minted at `assay start`, printed
once for the launcher to store outside the run, and only its sha256 is kept in
the run state — the agent cannot recover it from artifacts. The same token
authorizes one-shot action approvals (`assay approve`) and liveness waivers
(`assay waive`).

The emergence meter is free journal counters over agent-initiated acts no
module demanded: self-declared channels, self-authored verifiers, self-built
models, self-proposed goals — measuring emergence instead of assuming it.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .core import AssayError, RunPaths, append_jsonl, atomic_json, load_jsonl, read_json

APPROVAL_EXPIRY_SECONDS = 600.0


def owner_path(paths: RunPaths) -> Path:
    return paths.state / "owner.json"


def goal_path(paths: RunPaths) -> Path:
    return paths.state / "goal.json"


def proposals_path(paths: RunPaths) -> Path:
    return paths.state / "proposals.jsonl"


def approvals_path(paths: RunPaths) -> Path:
    return paths.state / "approvals.json"


def waivers_path(paths: RunPaths) -> Path:
    return paths.state / "waivers.json"


def mint_owner_token(paths: RunPaths) -> str:
    """Mint the run's owner token at start; store only its hash. Returns the
    token exactly once — the caller prints it for the owner/launcher."""
    # token_urlsafe uses the URL-safe base64 alphabet, which includes "-", so
    # ~1 in 32 tokens starts with "-" — which argparse then reads as a missing
    # value for `--token`. Re-mint until it does not lead with a dash.
    token = secrets.token_urlsafe(24)
    while token.startswith("-"):
        token = secrets.token_urlsafe(24)
    atomic_json(
        owner_path(paths),
        {"sha256": hashlib.sha256(token.encode()).hexdigest(), "minted_at": time.time()},
    )
    return token


def require_owner(paths: RunPaths, token: str | None) -> None:
    stored = read_json(owner_path(paths), None)
    if not isinstance(stored, dict) or not stored.get("sha256"):
        raise AssayError(
            "no owner token was minted for this run; owner operations are unavailable"
        )
    if not token or hashlib.sha256(token.encode()).hexdigest() != stored["sha256"]:
        raise AssayError(
            "owner authority required: pass --token <the token printed at start>. "
            "The agent proposes; the owner ratifies."
        )


def standing_goal(
    paths: RunPaths, registry: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The goal presented in status: ratified revision > registry text > the
    built-in predicate description."""
    ratified = read_json(goal_path(paths), None)
    if isinstance(ratified, dict) and ratified.get("text"):
        return {"text": str(ratified["text"]), "source": "ratified"}
    registered = ((registry or {}).get("goal") or {}).get("text")
    if registered:
        return {"text": str(registered), "source": "registry"}
    return {
        "text": "reach the environment's WIN state (all levels complete)",
        "source": "host",
    }


def propose_goal(paths: RunPaths, text: str, because: str | None) -> dict[str, Any]:
    if not text or not text.strip():
        raise AssayError("a goal proposal needs non-empty text")
    proposals = load_jsonl(proposals_path(paths))
    record = {
        "kind": "goal_proposed",
        "id": len(proposals) + 1,
        "text": text.strip(),
        "because": (because or "").strip(),
        "status": "pending",
    }
    append_jsonl(proposals_path(paths), record)
    append_jsonl(paths.activity, record)
    return record


def list_proposals(paths: RunPaths) -> list[dict[str, Any]]:
    proposals = load_jsonl(proposals_path(paths))
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


def ratify_goal(paths: RunPaths, proposal_id: int, token: str | None) -> dict[str, Any]:
    require_owner(paths, token)
    proposals = {int(item["id"]): item for item in list_proposals(paths)}
    proposal = proposals.get(int(proposal_id))
    if proposal is None:
        raise AssayError(f"no goal proposal with id {proposal_id}")
    if proposal["status"] != "pending":
        raise AssayError(f"proposal {proposal_id} is already {proposal['status']}")
    append_jsonl(
        proposals_path(paths),
        {"kind": "goal_resolved", "id": int(proposal_id), "status": "ratified"},
    )
    atomic_json(
        goal_path(paths),
        {"text": proposal["text"], "ratified_from": int(proposal_id), "at": time.time()},
    )
    append_jsonl(
        paths.activity,
        {"kind": "goal_ratified", "id": int(proposal_id), "text": proposal["text"]},
    )
    return proposal


def grant_approval(paths: RunPaths, action: str, token: str | None) -> None:
    """One-shot, expiring owner approval for an approval-flagged action."""
    require_owner(paths, token)
    state = read_json(approvals_path(paths), {})
    if not isinstance(state, dict):
        state = {}
    state[action.upper()] = {"granted_at": time.time(), "used": False}
    atomic_json(approvals_path(paths), state)
    append_jsonl(paths.activity, {"kind": "approval_granted", "action": action.upper()})


def consume_approval(paths: RunPaths, action: str) -> None:
    """Default-deny: refuse unless a fresh, unused approval exists; use it up."""
    state = read_json(approvals_path(paths), {})
    entry = state.get(action.upper()) if isinstance(state, dict) else None
    if not isinstance(entry, dict) or entry.get("used"):
        raise AssayError(
            f"{action} is approval-gated (default-deny): the owner grants one use "
            f"with `assay approve {action} --token ...`"
        )
    age = time.time() - float(entry.get("granted_at", 0))
    if age > APPROVAL_EXPIRY_SECONDS:
        raise AssayError(
            f"{action}'s approval expired after {int(APPROVAL_EXPIRY_SECONDS)}s "
            "(default-deny with timeout); ask the owner to approve again"
        )
    entry["used"] = True
    entry["used_at"] = time.time()
    atomic_json(approvals_path(paths), state)
    append_jsonl(paths.activity, {"kind": "approval_used", "action": action.upper()})


def grant_waiver(paths: RunPaths, action: str, token: str | None, because: str) -> None:
    """Owner waiver for a liveness rehearsal quota — explicit and journaled."""
    require_owner(paths, token)
    if not because or not because.strip():
        raise AssayError("a liveness waiver needs --because <why it is safe now>")
    state = read_json(waivers_path(paths), {})
    if not isinstance(state, dict):
        state = {}
    state[action.upper()] = {"at": time.time(), "because": because.strip()}
    atomic_json(waivers_path(paths), state)
    append_jsonl(
        paths.activity,
        {"kind": "liveness_waived", "action": action.upper(), "because": because.strip()},
    )


def has_waiver(paths: RunPaths, action: str) -> bool:
    state = read_json(waivers_path(paths), {})
    return isinstance(state, dict) and action.upper() in state


def check_rehearsal(
    paths: RunPaths, registry: Mapping[str, Any] | None, action: str
) -> None:
    """Enforced liveness, minimal real form: a live actuator with a
    rehearsal quota refuses until an imported sim digest shows enough graded
    attempts under the SAME registry but a DIFFERENT binding, or the owner
    journals a waiver. Inert unless declared."""
    from .registry import action_spec

    spec = action_spec(registry or {}, action) if registry else None
    if not spec or spec.get("liveness") != "live" or not spec.get("rehearsal_quota"):
        return
    if has_waiver(paths, action):
        return
    quota = int(spec["rehearsal_quota"])
    imported = read_json(paths.state / "imported" / "knowledge.json", None)
    if isinstance(imported, dict):
        own_config = read_json(paths.config, {})
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
        f"{action} is a LIVE actuator with a rehearsal quota of {quota}: import a "
        "sim-binding run's knowledge (same registry, different binding) with "
        f">= {quota} graded attempts, or the owner journals "
        f"`assay waive {action} --token ... --because ...` (default-deny)"
    )


def emergence_meter(paths: RunPaths) -> dict[str, int]:
    """Free counters over agent-initiated acts no module demanded."""
    activity = load_jsonl(paths.activity)
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


def emergence_line(paths: RunPaths) -> str:
    meter = emergence_meter(paths)
    return (
        f"EMERGENCE | self-authored verifiers {meter['verifiers']} | declared "
        f"channels {meter['channels']} | model replays {meter['model_replays']} | "
        f"goal proposals {meter['goal_proposals']}"
    )


def agenda_lines(
    paths: RunPaths,
    registry: Mapping[str, Any] | None,
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    goal = standing_goal(paths, registry)
    achieved = bool(events) and str(events[-1]["state"]) == "WIN"
    lines = [
        f"AGENDA | goal ({goal['source']}): {goal['text']} | "
        f"{'ACHIEVED' if achieved else 'not achieved'}"
    ]
    pending = [item for item in list_proposals(paths) if item["status"] == "pending"]
    if pending:
        newest = pending[-1]
        lines.append(
            f"AGENDA | {len(pending)} goal proposal(s) awaiting the owner — newest "
            f"#{newest['id']}: {str(newest['text'])[:120]}"
        )
    return lines
