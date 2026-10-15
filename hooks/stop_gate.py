#!/usr/bin/env python3
"""The Stop hook of a player's session on an ASSAY run directory: the
session ends when the run says it may, not when the player believes it is
done.

`/goal` asks a small model whether the conversation looks finished. This
gate asks the run's own files instead. The player may stop when:

- the last event of the journal is WIN;
- the paid actions have reached the registry's cap (`budget.actions`), or
  the reported spend its dollar cap (`budget.usd`);
- an owner action is pending: a refusal coded APPROVAL_REQUIRED or
  REHEARSAL_REQUIRED, since the session's launch, that no later
  `approval_granted` or `liveness_waived` record answers. The player cannot
  give either to itself;
- the player declared why it stops: a line beginning `STOP:` in
  `.assay/NOTES.md` that was not there when the session was launched. In
  this version the declaration lives in the notes and is not journaled.

Otherwise the stop is blocked, and the reason says what to do next. After
three blocks with no paid action between them the stop is allowed, so that a
player that cannot go on is not held in a loop; the session runner then
decides whether another session starts.

The session runner (`python3 -m tools.eval session`, behind the `/assay:run`
skill) installs this hook into `<run dir>/.claude/settings.json`, where the
PreToolUse hook already refuses the player's writes. The runner also
exports `ASSAY_STOP_GATE` to each player, naming the gate's state file
outside the run directory. Before each session the runner writes the launch's
counts there: the activity records, the declarations and the paid actions so
far. The gate keeps its blocks and its decisions in the same file. Without
the variable the gate is inert, so a person's own session in the run
directory is never held.

The facts come from the run's files: the journal (`.assay/events.jsonl`,
the public contract), the pinned registry (`.assay/registry.json`), the
activity log (`.assay/activity.jsonl`) and the notes. Standard library only;
it imports nothing from `assay`. The session runner loads this file by path,
so the runner and the gate apply the same rules. A block is the JSON
`{"decision": "block", "reason": ...}` on stdout with exit status 0; an
allowed stop is exit status 0 with no output. A run this script cannot read
is never held.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

STATE_VARIABLE = "ASSAY_STOP_GATE"
STATE_VERSION = 1
MAX_BLOCKS = 3
KEPT_DECISIONS = 50
DECLARATION = "STOP:"
# The refusals only the owner answers, with the owner's command for each.
OWNER_CODES = {"APPROVAL_REQUIRED": "approve", "REHEARSAL_REQUIRED": "waive"}
# The activity records that answer them.
ANSWERS = {"approval_granted": "APPROVAL_REQUIRED", "liveness_waived": "REHEARSAL_REQUIRED"}
# The action a refusal names, at the start of its message: "DRILL is
# approval-gated ...", "DRILL's approval expired ...", "DRILL is a LIVE
# actuator ...".
ACTION_IN_ERROR = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)(?:'s| is)\b")
FINISHED = {
    "WIN": "the run reached WIN",
    "CAP": "the paid actions reached the registry's cap",
    "USD_CAP": "the reported spend reached the registry's dollar cap",
}


@dataclasses.dataclass(frozen=True)
class RunView:
    """What the gate reads of a run."""

    state: str
    paid: int
    cap: int | None
    usd: float
    usd_cap: float | None
    activity: tuple[Mapping[str, Any], ...]
    notes: str


# --- reading ---------------------------------------------------------------------


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    amount = float(value)
    return amount if math.isfinite(amount) else None


def reported_spend(activity: Sequence[Mapping[str, Any]]) -> float:
    """The reported spend in dollars, as the kernel sums it: the last
    `spend_report` record per id."""
    latest: dict[str, Mapping[str, Any]] = {}
    for record in activity:
        if record.get("kind") == "spend_report" and record.get("id"):
            latest[str(record["id"])] = record
    total = 0.0
    for record in latest.values():
        amount = _amount(record.get("usd"))
        if amount is not None:
            total += amount
    return round(total, 6)


def load_view(run_dir: Path) -> RunView | None:
    """The run as the gate reads it, or None when it has no journal."""
    state = run_dir / ".assay"
    events = read_jsonl(state / "events.jsonl")
    if not events:
        return None
    registry = read_json(state / "registry.json")
    budget = registry.get("budget") if isinstance(registry, dict) else None
    if not isinstance(budget, dict):
        budget = {}
    activity = read_jsonl(state / "activity.jsonl")
    try:
        notes = (state / "NOTES.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        notes = ""
    return RunView(
        state=str(events[-1].get("state", "")),
        paid=sum(1 for event in events if event.get("counts_action")),
        cap=_count(budget.get("actions")),
        usd=reported_spend(activity),
        usd_cap=_amount(budget.get("usd")),
        activity=tuple(activity),
        notes=notes,
    )


# --- the rules ---------------------------------------------------------------------


def finished(view: RunView) -> str | None:
    """WIN, CAP or USD_CAP when the run is over, else None."""
    if view.state == "WIN":
        return "WIN"
    if view.cap is not None and view.paid >= view.cap:
        return "CAP"
    if view.usd_cap is not None and view.usd >= view.usd_cap:
        return "USD_CAP"
    return None


def pending_owner_actions(activity: Sequence[Mapping[str, Any]], since: int = 0) -> list[dict[str, str]]:
    """The owner actions a refusal at or after record `since` asked for and
    no later record answered: one entry per code and action, with the
    action's name (empty when the message does not lead with it), the code,
    the owner's command (`approve` or `waive`) and the refusal's message."""
    pending: dict[tuple[str, str], dict[str, str]] = {}
    for index, record in enumerate(activity):
        kind = record.get("kind")
        code = record.get("code")
        if kind == "command_end" and index >= since and isinstance(code, str) and code in OWNER_CODES:
            error = str(record.get("error") or "")
            match = ACTION_IN_ERROR.match(error)
            action = match.group(1).upper() if match else ""
            pending[(code, action)] = {"action": action, "code": code, "owner": OWNER_CODES[code], "error": error}
        elif isinstance(kind, str) and kind in ANSWERS and isinstance(record.get("action"), str):
            answered = ANSWERS[kind]
            pending.pop((answered, str(record["action"]).upper()), None)
            # A refusal whose message named no action is answered by any
            # record of its kind.
            pending.pop((answered, ""), None)
    return list(pending.values())


def declarations(notes: str) -> list[str]:
    """The stop declarations in the notes: the text after `STOP:` on each
    line that begins with it, when there is text."""
    found: list[str] = []
    for line in notes.splitlines():
        stripped = line.strip()
        if stripped.startswith(DECLARATION):
            reason = stripped[len(DECLARATION):].strip()
            if reason:
                found.append(reason)
    return found


def owner_text(item: Mapping[str, str]) -> str:
    action = item.get("action") or "an action"
    return f"{item.get('code')} for {action} (the owner runs `assay {item.get('owner')}`)"


def block_reason(view: RunView) -> str:
    if view.cap is None:
        left = "no action cap is registered"
    else:
        left = f"{max(0, view.cap - view.paid)} of {view.cap} paid actions are left"
    return (
        "ASSAY stop gate: the run is not finished. WIN is not reached, "
        f"{left}, and no owner action is pending. Continue from "
        '`"$ASSAY" status` and .assay/NOTES.md. If you judge the goal '
        "unreachable with what is left, or you need something only the owner "
        "can give, add one line to .assay/NOTES.md that begins with `STOP:` and "
        "says why, then stop."
    )


def decide(view: RunView, state: dict[str, Any], session_id: str | None) -> tuple[bool, str]:
    """Allow the stop or block it, with the reason. `state` is the gate's
    state file, updated in place: the session it holds and its blocks."""
    launch = state.get("launch")
    if not isinstance(launch, dict):
        launch = {}
    if session_id and state.get("session_id") != session_id:
        state["session_id"] = session_id
        state["blocks"] = 0
        state["paid_at_last_block"] = None
    verdict = finished(view)
    if verdict is not None:
        return True, FINISHED[verdict]
    pending = pending_owner_actions(view.activity, _count(launch.get("activity")) or 0)
    if pending:
        return True, "the owner must act: " + "; ".join(owner_text(item) for item in pending)
    said = declarations(view.notes)
    if len(said) > (_count(launch.get("declarations")) or 0):
        return True, f"the player declared a stop: {said[-1]}"
    blocks = _count(state.get("blocks")) or 0
    last = _count(state.get("paid_at_last_block"))
    if last is None or view.paid > last:
        blocks = 0
    if blocks >= MAX_BLOCKS:
        state["blocks"] = 0
        state["paid_at_last_block"] = None
        return True, f"released after {MAX_BLOCKS} blocks with no paid action between them"
    state["blocks"] = blocks + 1
    state["paid_at_last_block"] = view.paid
    return False, block_reason(view)


# --- the state file ------------------------------------------------------------------


def load_state(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION}
    return value


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    """Written whole and renamed into place; a failure is not the
    session's problem, so it is swallowed."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stop_gate.py",
        description="the Stop hook of an ASSAY player's session; the hook JSON on stdin",
    )
    parser.add_argument("--run-dir", required=True, metavar="DIR", help="the run directory the session plays")
    args = parser.parse_args(argv)
    state_file = os.environ.get(STATE_VARIABLE)
    if not state_file:
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "null")
    except ValueError:
        payload = None
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    view = load_view(Path(args.run_dir))
    if view is None:
        return 0
    path = Path(state_file)
    state = load_state(path)
    allow, reason = decide(view, state, session_id if isinstance(session_id, str) else None)
    decisions = state.get("decisions")
    if not isinstance(decisions, list):
        decisions = []
    decisions.append({
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "session_id": state.get("session_id"),
        "allow": allow,
        "why": reason,
        "paid": view.paid,
    })
    state["decisions"] = decisions[-KEPT_DECISIONS:]
    save_state(path, state)
    if allow:
        return 0
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
