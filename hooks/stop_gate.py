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

Under the `coverage` demand (the launch record's `demand`, set by a runner
that wants it) a bare `STOP:` line is not enough: a stop with budget left is
a conclusion, and it carries the coverage audit. The declaration is a block
in the notes, a `STOP:` line followed by `RULES:` (the rules the conclusion
rests on, citing at least one event id of this run, `e12`), `UNTESTED:` (what
the record never exercised in the regime the conclusion needs) and `NEXT
PROBE:` (the cheapest probe not bought, and why). An incomplete block is
named in the reason. A cited event must be a paid one (e1 or later): e0 is
the start, which exercises no rule. A block counts when it is new by
content (the digest of its four lines differs from every block the notes
held at the launch, so an edit of an inherited block is new and a copy is
not) and its RULES cite an event of this session (after the launch's last
event). A first stop (the notes held no complete block at the launch) with
more than half of the cap left is held once more unless its NEXT PROBE cites
a bought event or states a cost above what is left. A `STOP:` line whose
reason is a placeholder (`none`, `n/a`, a parenthesis, `not stopped`) is not
a declaration.

Otherwise the stop is blocked, and the reason says what to do next and
states the rule. The stop is released after three blocks in the session
(the count starts again when the notes gain a new block, up to twelve in
all), or once fifty paid actions were bought under the hold, so that a
player that cannot go on is not held in a loop and a held player does not
burn its budget to escape; the session runner then decides whether another
session starts.

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
import hashlib
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
# The paid actions a held player may buy before the hold releases: the
# 2026-10-26 holds were escaped by spending to the cap (s5i5 bought 381).
HOLD_RELEASE = 50
# The blocks one session may see in all, however often a new block restarts
# the count of three.
SESSION_BLOCKS = 12
KEPT_DECISIONS = 50
DECLARATION = "STOP:"
# What a stop declaration must hold: `line`, a `STOP:` line with a reason;
# `coverage`, the block below, the coverage audit a stop with budget left owes.
DEMANDS = ("line", "coverage")
COVERAGE_FIELDS = ("RULES:", "UNTESTED:", "NEXT PROBE:")
# How far below its `STOP:` line a block's fields may sit.
COVERAGE_SPAN = 12
EVENT_ID = re.compile(r"\be0*(\d+)\b")
# A number in NEXT PROBE that is not an event id: a stated cost.
NUMBER = re.compile(r"(?<![\w.])(\d+)(?![\w.])")
# A `STOP:` reason that only holds the place of one ("STOP: none.", "STOP:
# (not applicable)", "STOP: not stopped; play continues").
PLACEHOLDER = re.compile(
    r"^(?:none|n/?a|-+|\(.*|not\s+(?:stopped|applicable|yet)\b.*|no\s+stop\b.*)\.?$",
    re.IGNORECASE,
)
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
    last_event: int = 0


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
        last_event=_count(events[-1].get("id")) or len(events) - 1,
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


def stop_reason(line: str) -> str | None:
    """The reason of a `STOP:` line: the text after `STOP:` when the line
    begins with it; empty for a bare `STOP:`; None for any other line and for
    a placeholder reason, which declares nothing. The conclusion gate module
    (src/assay/modules.py) reads `STOP:` lines by the same rule."""
    stripped = line.strip()
    if not stripped.startswith(DECLARATION):
        return None
    reason = stripped[len(DECLARATION):].strip()
    return None if PLACEHOLDER.match(reason) else reason


def declarations(notes: str) -> list[str]:
    """The stop declarations in the notes: the text after `STOP:` on each
    line that begins with it, when there is text."""
    return [reason for reason in map(stop_reason, notes.splitlines()) if reason]


@dataclasses.dataclass(frozen=True)
class CoverageBlock:
    """One `STOP:` block of the notes under the `coverage` demand: its reason,
    what it still lacks (empty when complete), the digest of its four lines,
    the event ids its RULES cite and its NEXT PROBE text."""

    reason: str
    missing: tuple[str, ...]
    digest: str = ""
    cited: tuple[int, ...] = ()
    probe: str = ""


def _digest(reason: str, found: Mapping[str, str]) -> str:
    """The block's content, whitespace-normalized: the same four lines give
    the same digest wherever they sit in the notes."""
    parts = [reason, *(found.get(field, "") for field in COVERAGE_FIELDS)]
    text = "\n".join(" ".join(part.split()).lower() for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def coverage_blocks(notes: str, last_event: int) -> list[CoverageBlock]:
    """Every `STOP:` block in the notes, read as the coverage audit a stop
    owes: the fields within `COVERAGE_SPAN` lines below the `STOP:` line (up to
    the next `STOP:` line), each needing text, and `RULES:` citing at least
    one paid event of this run (`e1` to the last event; `e0` is the start)."""
    lines = notes.splitlines()
    blocks: list[CoverageBlock] = []
    for index, line in enumerate(lines):
        reason = stop_reason(line)
        if reason is None:
            continue
        found: dict[str, str] = {}
        for following in lines[index + 1:index + 1 + COVERAGE_SPAN]:
            text = following.strip().lstrip("-* ").strip()
            if text.startswith(DECLARATION):
                break
            for field in COVERAGE_FIELDS:
                if text.upper().startswith(field) and field not in found:
                    found[field] = text[len(field):].strip()
        missing = [] if reason else ["a reason after STOP:"]
        for field in COVERAGE_FIELDS:
            if not found.get(field):
                missing.append(field[:-1])
        rules = found.get("RULES:", "")
        cited = tuple(int(match) for match in EVENT_ID.findall(rules) if 1 <= int(match) <= last_event)
        if rules and not cited:
            missing.append(
                f"a paid event of this run in RULES (e1 to e{last_event})"
                if last_event >= 1
                else "a paid event in RULES (none is journaled yet: probe before you conclude)"
            )
        blocks.append(CoverageBlock(
            reason=reason,
            missing=tuple(missing),
            digest=_digest(reason, found),
            cited=cited,
            probe=found.get("NEXT PROBE:", ""),
        ))
    return blocks


def launch_snapshot(view: RunView | None, demand: str = "line") -> dict[str, Any]:
    """What the gate needs from a session's launch: the activity records, the
    declarations that count under the demand, the paid actions, the last
    event, the digests of every STOP block the notes held and how many of
    them were complete. Both runners write it before each session."""
    blocks = coverage_blocks(view.notes, view.last_event) if view else []
    return {
        "demand": demand,
        "activity": len(view.activity) if view else 0,
        "declarations": count_declarations(view, demand) if view else 0,
        "paid": view.paid if view else 0,
        "last_event": view.last_event if view else 0,
        "digests": sorted({block.digest for block in blocks}),
        "complete": sum(1 for block in blocks if not block.missing),
    }


def fresh_state(launch: Mapping[str, Any]) -> dict[str, Any]:
    """The gate's state file at a session's launch: no blocks yet."""
    return {
        "version": STATE_VERSION, "launch": dict(launch), "blocks": 0, "session_blocks": 0,
        "hold_paid": None, "digests_at_last_block": None, "first_stop_held": False, "decisions": [],
    }


def count_declarations(view: RunView, demand: str = "line") -> int:
    """The declarations that count under the demand: every `STOP:` line with
    a reason, or every complete coverage block."""
    if demand == "coverage":
        return sum(1 for block in coverage_blocks(view.notes, view.last_event) if not block.missing)
    return len(declarations(view.notes))


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


def _left(view: RunView) -> str:
    if view.cap is None:
        return "no action cap is registered"
    return f"{max(0, view.cap - view.paid)} of {view.cap} paid actions are left"


def coverage_reason(view: RunView, launch: Mapping[str, Any], blocks: int, lacking: str | None) -> str:
    """The block message under the coverage demand: what the gate accepts,
    stated as the rule it applies, and the form of the block."""
    held = _count(launch.get("complete")) or 0
    after = _count(launch.get("last_event")) or 0
    rule = (
        f"The gate accepts a STOP block that is new by content (the notes held {held} complete "
        f"block{'' if held == 1 else 's'} at this session's launch; append a new one, or rewrite one "
        f"in your own words) and whose RULES cite an event of this session (after e{after}). "
        f"Blocks this session: {blocks} of {MAX_BLOCKS}."
    )
    return (
        "ASSAY stop gate: the run is not finished. WIN is not reached, "
        f"{_left(view)}, and no owner action is pending. A stop with budget left is a "
        "conclusion, and it carries the coverage audit. Continue from "
        '`"$ASSAY" status` and .assay/NOTES.md; if your notes or the audit name '
        "an untested rule, object or probe, buy the cheapest one before you stop. "
        f"{rule}{f' Your newest STOP block lacks: {lacking}.' if lacking else ''} "
        "To stop anyway, add this block to .assay/NOTES.md, then stop:\n"
        "STOP: <the conclusion, in one line>\n"
        "RULES: <each rule the conclusion rests on, with the graded event ids that "
        "exercised it, e.g. e41, e57>\n"
        "UNTESTED: <every rule or object the record never exercised in the regime "
        "the conclusion needs>\n"
        "NEXT PROBE: <the cheapest probe you have not bought, and why you will not buy it>"
    )


def first_stop_reason(view: RunView, remaining: int) -> str:
    return (
        "ASSAY stop gate: this is the run's first conclusion and "
        f"{_left(view)}, more than half the cap. A first stop stands when its NEXT PROBE "
        "names an event you bought to test it (an event id of this run) or states a "
        f"cost above the {remaining} actions left. Buy the probe your block names, then "
        "rewrite the block with what it showed; this hold is not repeated in this session."
    )


def _probe_answers(block: CoverageBlock, last_event: int, remaining: int) -> bool:
    """A first stop's NEXT PROBE stands when it cites a bought event or a
    cost above the actions left."""
    if any(1 <= int(match) <= last_event for match in EVENT_ID.findall(block.probe)):
        return True
    costs = [int(match) for match in NUMBER.findall(EVENT_ID.sub(" ", block.probe))]
    return any(cost > remaining for cost in costs)


def _accepted(view: RunView, launch: Mapping[str, Any]) -> tuple[CoverageBlock | None, str | None]:
    """The newest block the coverage demand accepts, or None with what the
    newest block lacks."""
    found = coverage_blocks(view.notes, view.last_event)
    if not found:
        return None, None
    known = launch.get("digests")
    after = _count(launch.get("last_event")) or 0
    if not isinstance(known, list):
        # A launch record from before the digests: the count rule.
        complete = [block for block in found if not block.missing]
        if len(complete) > (_count(launch.get("declarations")) or 0):
            return complete[-1], None
        newest = next((block for block in reversed(found) if block.missing), None)
        return None, ", ".join(newest.missing) if newest else None
    for block in reversed(found):
        if not block.missing and block.digest not in known and any(event > after for event in block.cited):
            return block, None
    newest = found[-1]
    lacking = list(newest.missing)
    if not newest.missing and newest.digest in known:
        lacking.append("newness (the notes held this block at the launch)")
    elif not any(event > after for event in newest.cited) and not any("RULES" in item for item in newest.missing):
        lacking.append(f"an event of this session in RULES (after e{after})")
    return None, ", ".join(lacking) or None


def _release(state: dict[str, Any], why: str) -> tuple[bool, str]:
    state["blocks"] = 0
    state["hold_paid"] = None
    return True, why


def decide(view: RunView, state: dict[str, Any], session_id: str | None) -> tuple[bool, str]:
    """Allow the stop or block it, with the reason. `state` is the gate's
    state file, updated in place: the session it holds, its blocks and its
    hold."""
    launch = state.get("launch")
    if not isinstance(launch, dict):
        launch = {}
    if session_id and state.get("session_id") != session_id:
        state["session_id"] = session_id
        state.update({
            "blocks": 0, "session_blocks": 0, "hold_paid": None,
            "digests_at_last_block": None, "first_stop_held": False,
        })
    verdict = finished(view)
    if verdict is not None:
        return True, FINISHED[verdict]
    pending = pending_owner_actions(view.activity, _count(launch.get("activity")) or 0)
    if pending:
        return True, "the owner must act: " + "; ".join(owner_text(item) for item in pending)
    demand = launch.get("demand") if launch.get("demand") in DEMANDS else "line"
    lacking: str | None = None
    held_first = False
    if demand == "coverage":
        block, lacking = _accepted(view, launch)
        if block is not None:
            remaining = max(0, view.cap - view.paid) if view.cap is not None else 0
            first = isinstance(launch.get("digests"), list) and not (_count(launch.get("complete")) or 0)
            if (
                first
                and view.cap is not None
                and remaining * 2 > view.cap
                and not state.get("first_stop_held")
                and not _probe_answers(block, view.last_event, remaining)
            ):
                state["first_stop_held"] = True
                held_first = True
            else:
                return True, f"the player declared a stop with its coverage audit: {block.reason}"
    else:
        said = declarations(view.notes)
        if len(said) > (_count(launch.get("declarations")) or 0):
            return True, f"the player declared a stop: {said[-1]}"
    blocks = _count(state.get("blocks")) or 0
    digests = sorted({block.digest for block in coverage_blocks(view.notes, view.last_event)})
    before = state.get("digests_at_last_block")
    if isinstance(before, list) and set(digests) - set(before):
        # The notes gained a block since the last block: the count of three
        # starts again (the session's total still bounds it).
        blocks = 0
    hold = _count(state.get("hold_paid"))
    if hold is not None and view.paid - hold >= HOLD_RELEASE:
        return _release(state, f"released after {HOLD_RELEASE} paid actions bought under the hold")
    session_blocks = _count(state.get("session_blocks")) or 0
    if blocks >= MAX_BLOCKS:
        return _release(state, f"released after {MAX_BLOCKS} blocks in this session")
    if session_blocks >= SESSION_BLOCKS:
        return _release(state, f"released after {SESSION_BLOCKS} blocks in this session in all")
    state["blocks"] = blocks + 1
    state["session_blocks"] = session_blocks + 1
    state["digests_at_last_block"] = digests
    if hold is None:
        state["hold_paid"] = view.paid
    if held_first:
        return False, first_stop_reason(view, max(0, (view.cap or 0) - view.paid))
    if demand == "coverage":
        return False, coverage_reason(view, launch, blocks + 1, lacking)
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
