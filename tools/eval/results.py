"""What a run directory says about its run, and the per-seed row built from
it and from the runner's own session ledger.

Today the facts come from the files under `.assay/`: the journal
(`events.jsonl`, the public contract: `counts_action`, `state`,
`levels_completed`, `win_levels`, `predict`), the receipts
(`receipts/*.json`, one per paid command, tallied by their frozen result
tokens), the activity log (`activity.jsonl`: command records, among them a
prediction refused under `gate: off`) and `chain.json` (the head). Tokens and
dollars come from the player's own report: the JSON document `claude -p
--output-format json` prints (`usage`, `total_cost_usd`), captured per
session by the runner. Per action means per paid action.

The seam for #13. When the CLI gains `--json`, `assay status --json` returns
the `Status` record (docs/ARCHITECTURE.md section 7.4, the `run` block:
world, event, progress, paid, state) and `assay audit --json` the audit report
(the ungated events, the chain verdict), the same facts this module reads
from the files. The switch is one function: add `read_run_json(run_dir,
launcher)` beside `read_run_files`, map the two records onto `RunFacts`, and
point `read_run` at it. Every row carries `source` (`files` today), so a
results file mixing the two says which rows came from which.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .jobs import Job

SOURCE = "files"
# The refusal of a prediction under `gate: off`: by its code from 1.2.0 on (the
# command record carries `code`), by its text before that, when the daemon's
# flattened error led with the exception's type.
GATE_OFF_CODE = "GATE_OFF"
GATE_OFF_REFUSAL = "the prediction gate is off for this run"
OLD_TYPE_PREFIX = "AssayError: "
TOKEN_FIELDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_creation": "cache_creation_input_tokens",
}


@dataclasses.dataclass(frozen=True)
class RunFacts:
    events: int
    paid_actions: int
    state: str
    levels_completed: int
    win_levels: int
    predicted_actions: int
    receipts: dict[str, int]
    refused_predictions: int
    chain_head: str | None

    @property
    def win(self) -> bool:
        return self.state == "WIN"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _gate_off_refusal(record: Mapping[str, Any]) -> bool:
    if record.get("code") == GATE_OFF_CODE:
        return True
    error = str(record.get("error", ""))
    return error.removeprefix(OLD_TYPE_PREFIX).startswith(GATE_OFF_REFUSAL)


def read_run_files(run_dir: Path) -> RunFacts | None:
    """The facts from the files under `.assay/`, or None when the directory
    holds no journal yet."""
    state = Path(run_dir) / ".assay"
    events = _jsonl(state / "events.jsonl")
    if not events:
        return None
    last = events[-1]
    paid = [event for event in events if event.get("counts_action")]
    predicted = sum(1 for event in paid if event.get("predict") is not None)
    outcomes: Counter[str] = Counter()
    receipts_dir = state / "receipts"
    if receipts_dir.is_dir():
        for file in sorted(receipts_dir.glob("*.json")):
            try:
                receipt = json.loads(file.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(receipt, dict) and isinstance(receipt.get("outcome"), str):
                outcomes[receipt["outcome"]] += 1
    refused = sum(
        1
        for record in _jsonl(state / "activity.jsonl")
        if record.get("kind") == "command_end" and _gate_off_refusal(record)
    )
    head: str | None = None
    try:
        chain = json.loads((state / "chain.json").read_text())
        if isinstance(chain, dict) and isinstance(chain.get("head"), str):
            head = chain["head"]
    except (OSError, json.JSONDecodeError):
        pass
    return RunFacts(
        events=len(events),
        paid_actions=len(paid),
        state=str(last.get("state", "")),
        levels_completed=int(last.get("levels_completed", 0) or 0),
        win_levels=int(last.get("win_levels", 0) or 0),
        predicted_actions=predicted,
        receipts=dict(sorted(outcomes.items())),
        refused_predictions=refused,
        chain_head=head,
    )


def read_run(run_dir: Path) -> RunFacts | None:
    """The one entry point; `SOURCE` says where the facts came from."""
    return read_run_files(run_dir)


def outcome_of(facts: RunFacts | None, budget: int) -> str | None:
    """WIN, or CAP when the paid actions reached the budget, else None (the
    job is not finished by what the run says)."""
    if facts is None:
        return None
    if facts.win:
        return "WIN"
    if facts.paid_actions >= budget:
        return "CAP"
    return None


def player_report(stdout: str) -> dict[str, Any] | None:
    """The player's JSON report when its stdout is one JSON object (the shape
    `claude -p --output-format json` prints), else None."""
    text = stdout.strip()
    if not text.startswith("{"):
        return None
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        return None
    return report if isinstance(report, dict) else None


def session_tokens(report: Mapping[str, Any] | None) -> dict[str, int] | None:
    """The token counts of one session from its report's `usage` block, or
    None when the report carries none."""
    if not report or not isinstance(report.get("usage"), dict):
        return None
    usage = report["usage"]
    counts = {
        name: int(usage.get(field) or 0)
        for name, field in TOKEN_FIELDS.items()
        if isinstance(usage.get(field), (int, float))
    }
    if not counts:
        return None
    counts["total"] = sum(counts.values())
    return counts


def _per_action(value: float | None, paid: int) -> float | None:
    if value is None or paid <= 0:
        return None
    return round(value / paid, 6)


def row_for(
    job: Job,
    facts: RunFacts | None,
    sessions: list[Mapping[str, Any]],
    *,
    outcome: str | None,
    run_dir: str,
) -> dict[str, Any]:
    """One per-seed row: the job, what the run says, what the sessions cost."""
    tokens: dict[str, int] | None = None
    dollars: float | None = None
    for session in sessions:
        counts = session_tokens(session.get("report"))
        if counts is not None:
            tokens = tokens or {}
            for key, value in counts.items():
                tokens[key] = tokens.get(key, 0) + value
        cost = (session.get("report") or {}).get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            dollars = round((dollars or 0.0) + float(cost), 6)
    paid = facts.paid_actions if facts else 0
    return {
        "job": job.id,
        "world": job.world.id,
        "arm": job.arm.name,
        "seed": job.seed,
        "model": job.model,
        "outcome": outcome or (outcome_of(facts, job.budget) if facts else None),
        "win": bool(facts and facts.win),
        "state": facts.state if facts else None,
        "levels_completed": facts.levels_completed if facts else None,
        "win_levels": facts.win_levels if facts else None,
        "paid_actions": paid,
        "budget": job.budget,
        "predicted_actions": facts.predicted_actions if facts else None,
        "receipts": facts.receipts if facts else {},
        "refused_predictions": facts.refused_predictions if facts else None,
        "sessions": len(sessions),
        "wall_seconds": round(sum(float(session.get("wall_seconds") or 0) for session in sessions), 1),
        "tokens": tokens,
        "dollars": dollars,
        "tokens_per_action": _per_action(tokens["total"] if tokens else None, paid),
        "dollars_per_action": _per_action(dollars, paid),
        "chain_head": facts.chain_head if facts else None,
        "run_dir": run_dir,
        "source": SOURCE,
    }


def write_rows(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
