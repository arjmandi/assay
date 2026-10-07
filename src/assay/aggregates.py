"""Statistical (aggregate) claims: the minimal deployment-hardening form.

Three rules govern aggregate claims and this module implements exactly them:

1. ADDITIVE, never substitutive: the parser refuses an action whose only
   gradable claims are aggregates (every action still carries a mechanical
   claim of its own).
2. DECLARED CONSEQUENCE, executed by the kernel: `on-fail advise` surfaces an
   advisory; `on-fail revoke_batching` revokes batching rights for the rest of
   the run (hand batches drop to single steps; model plans refuse until a
   fresh replay-fit passes).
3. AUTO-RESOLVE AT HORIZON: an open aggregate that reaches its horizon is
   resolved then, pass or FAIL, never left dangling; superseding an open
   aggregate (same channel + stat) closes the old one as an ABANDONMENT with
   miss-equivalent weight in the aggregate meter.

Aggregates live in their own meter bucket; they never enter the world-model or
gamble miss rates. Honest scope note: this machinery has no tenant on frame worlds;
it ships because the deployment-hardening order includes it, and it is inert
unless an agent opens one.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from .core import RunPaths, append_jsonl, atomic_json, read_json

MAX_OPEN = 4


def _path(paths: RunPaths):
    return paths.state / "aggregates.json"


def load_state(paths: RunPaths) -> dict[str, Any]:
    value = read_json(_path(paths), {"open": [], "resolved": [], "batching_revoked": False})
    if not isinstance(value, dict):
        return {"open": [], "resolved": [], "batching_revoked": False}
    value.setdefault("open", [])
    value.setdefault("resolved", [])
    value.setdefault("batching_revoked", False)
    return value


def batching_revoked(paths: RunPaths) -> bool:
    return bool(load_state(paths).get("batching_revoked"))


def _paid_ids(events: Sequence[Mapping[str, Any]]) -> list[int]:
    return [int(event["id"]) for event in events if event.get("counts_action")]


def open_aggregates(
    paths: RunPaths,
    claims: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Register this action's aggregate claims as open; supersede duplicates.

    Returns display lines. live.py calls it after the graded event is recorded
    (the action's mechanical claims validated before the spend), so an
    aggregate opens at the paid event that carried it."""
    aggregate_claims = [claim for claim in claims if claim.get("kind") == "aggregate"]
    if not aggregate_claims:
        return []
    state = load_state(paths)
    lines: list[str] = []
    opened_at = _paid_ids(events)[-1] if _paid_ids(events) else 0
    for claim in aggregate_claims:
        key = (claim["channel"], claim["stat"])
        survivors = []
        for entry in state["open"]:
            if (entry["channel"], entry["stat"]) == key:
                entry.update(status="abandoned", resolved_at_event=opened_at)
                state["resolved"].append(entry)
                append_jsonl(
                    paths.activity,
                    {"kind": "aggregate_abandoned", **{k: entry[k] for k in ("channel", "stat")}},
                )
                lines.append(
                    f"AGGREGATE | superseded open {entry['channel']}/{entry['stat']}; "
                    "journaled as an abandonment (miss-equivalent)"
                )
            else:
                survivors.append(entry)
        state["open"] = survivors
        if len(state["open"]) >= MAX_OPEN:
            lines.append(
                f"AGGREGATE | refused: at most {MAX_OPEN} open aggregates"
            )
            continue
        entry = {
            "channel": claim["channel"],
            "stat": claim["stat"],
            "op": claim["op"],
            "value": claim["value"],
            "over": int(claim["over"]),
            "horizon": int(claim["horizon"]),
            "on_fail": claim["on_fail"],
            "opened_at_event": opened_at,
            "opened_ts": time.time(),
            "status": "open",
        }
        state["open"].append(entry)
        append_jsonl(paths.activity, {"kind": "aggregate_opened", **{
            k: entry[k] for k in ("channel", "stat", "op", "value", "over", "horizon", "on_fail")
        }})
        lines.append(
            f"AGGREGATE | open: {claim['stat']}(ch {claim['channel']}) {claim['op']} "
            f"{claim['value']} over {claim['over']}a, resolves at horizon {claim['horizon']}a"
        )
    atomic_json(_path(paths), state)
    return lines


def resolve_due(
    paths: RunPaths, events: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Resolve every open aggregate whose horizon has passed. live.py calls it
    after each paid event is recorded, right after `open_aggregates`. A failed
    reading resolves as FAILED (rule 3), never skips."""
    state = load_state(paths)
    if not state["open"]:
        return []
    from .channels import channel_value

    paid = _paid_ids(events)
    by_id = {int(event["id"]): event for event in events}
    lines: list[str] = []
    still_open: list[dict[str, Any]] = []
    for entry in state["open"]:
        since = [pid for pid in paid if pid > int(entry["opened_at_event"])]
        if len(since) < int(entry["horizon"]):
            still_open.append(entry)
            continue
        window = since[-int(entry["over"]):]
        readings: list[float] = []
        failure: str | None = None
        for pid in window:
            ok, value = channel_value(paths, entry["channel"], by_id[pid])
            if not ok or isinstance(value, bool) or not isinstance(value, (int, float)):
                failure = f"reading at e{pid} not numeric/gradable: {value}"
                break
            readings.append(float(value))
        if failure is None and len(readings) < int(entry["over"]):
            failure = f"only {len(readings)} readings for over={entry['over']}"
        if failure is None:
            stat = {
                "mean": sum(readings) / len(readings),
                "min": min(readings),
                "max": max(readings),
            }[entry["stat"]]
            target = float(entry["value"])
            ok = {
                "=": stat == target,
                ">=": stat >= target,
                "<=": stat <= target,
            }[entry["op"]]
            actual = f"{entry['stat']}={stat:g} over {len(readings)} readings"
        else:
            ok = False
            actual = f"auto-resolved FAILED at horizon: {failure}"
        entry.update(status="held" if ok else "failed", actual=actual,
                     resolved_at_event=paid[-1] if paid else 0)
        state["resolved"].append(entry)
        append_jsonl(
            paths.activity,
            {
                "kind": "aggregate_resolved",
                "channel": entry["channel"],
                "stat": entry["stat"],
                "ok": ok,
                "actual": actual,
            },
        )
        lines.append(
            f"AGGREGATE | {'HELD' if ok else 'FAILED'}: "
            f"{entry['stat']}(ch {entry['channel']}) {entry['op']} {entry['value']} | {actual}"
        )
        if not ok and entry["on_fail"] == "revoke_batching":
            state["batching_revoked"] = True
            lines.append(
                "AGGREGATE | consequence executed: batching rights revoked for this "
                "run (hand batches single-step; model plans need a fresh replay-fit)"
            )
    state["open"] = still_open
    atomic_json(_path(paths), state)
    return lines


def meter(paths: RunPaths) -> dict[str, int]:
    state = load_state(paths)
    resolved = state["resolved"]
    return {
        "open": len(state["open"]),
        "held": sum(1 for entry in resolved if entry.get("status") == "held"),
        "failed": sum(1 for entry in resolved if entry.get("status") == "failed"),
        "abandoned": sum(1 for entry in resolved if entry.get("status") == "abandoned"),
    }
