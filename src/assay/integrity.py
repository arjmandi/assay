"""Journal integrity: rolling hash chain, anchoring, the ungated-event audit,
and secrets redaction at the journal boundary.

The chain makes the journal TAMPER-EVIDENT under stated conditions, not
unforgeable, and says so out loud: head_0 = sha256("assay-chain-v1"),
head_n = sha256(head_{n-1} || line_n). The daemon updates `.assay/chain.json` on
every event append and anchors the head OUTSIDE the run directory
(`~/.assay/anchors/<run-digest>.jsonl`, override with ASSAY_ANCHOR_DIR) every
ANCHOR_EVERY events and on WIN. `assay audit` recomputes everything from the
journal and reports:

- chain intact / diverged (and against the anchors),
- event-id contiguity,
- UNGATED events — a paid non-RESET event carrying no prediction and no grade
  (the definition the archived bypass audits pre-registered). An ungated event
  INVALIDATES THE RUN FOR SCORING and demotes all trust earned after it: the
  gate refuses model-plan batching once one exists.

Secrets redaction: values of registered secret env vars (plus the kernel's
default list) never enter the journal — every agent-supplied string field is
filtered at the boundary before it is written.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .core import RunPaths, append_jsonl, atomic_json, load_jsonl, read_json

ANCHOR_EVERY = 25
CHAIN_SEED = "assay-chain-v1"
# Env names whose values are always redacted, registered or not.
DEFAULT_SECRET_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
_MIN_SECRET_LENGTH = 8  # never redact trivially short values ("1", "true", ...)


def chain_path(paths: RunPaths) -> Path:
    return paths.state / "chain.json"


def anchor_dir() -> Path:
    """The anchor directory the environment names right now."""
    configured = os.getenv("ASSAY_ANCHOR_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".assay" / "anchors"


def environment_anchor_file(paths: RunPaths) -> Path:
    digest = hashlib.sha256(str(paths.root.resolve()).encode()).hexdigest()[:24]
    return anchor_dir() / f"{digest}.jsonl"


def recorded_anchor_file(paths: RunPaths) -> Path | None:
    """The anchor file pinned in config.json at start, or None for a run that
    predates the key."""
    config = read_json(paths.config, None)
    if isinstance(config, dict) and isinstance(config.get("anchor_file"), str):
        return Path(config["anchor_file"])
    return None


def anchor_file(paths: RunPaths) -> Path:
    """Where this run's chain heads go: the file recorded in config.json at
    start, so a later shell with a different ASSAY_ANCHOR_DIR still anchors and
    audits against the same file. Runs without the key use the environment."""
    recorded = recorded_anchor_file(paths)
    return recorded if recorded is not None else environment_anchor_file(paths)


def anchor_status(paths: RunPaths) -> dict[str, Any]:
    """The anchor line's facts: file, count, last anchored event, the last
    failed write (if newer than the last anchor), and whether the directory
    can be written now."""
    target = anchor_file(paths)
    anchors = load_jsonl(target) if target.exists() else []
    last_event = int(anchors[-1]["event_id"]) if anchors else None
    failed: str | None = None
    for record in load_jsonl(paths.activity):
        if record.get("kind") != "anchor_failed":
            continue
        event = record.get("event")
        if last_event is None or (isinstance(event, int) and event > last_event):
            failed = f"e{event}: {record.get('error')}"
    # Writability without side effects: the nearest existing ancestor must be
    # a writable directory (a file in the way is the common failure).
    ancestor = target.parent
    while not ancestor.exists() and ancestor.parent != ancestor:
        ancestor = ancestor.parent
    writable = ancestor.is_dir() and os.access(ancestor, os.W_OK)
    return {
        "file": target,
        "count": len(anchors),
        "last_event": last_event,
        "failed": failed,
        "writable": writable,
    }


def anchor_line(paths: RunPaths) -> str:
    status = anchor_status(paths)
    line = f"ANCHORS | {status['file']} | "
    line += (
        f"{status['count']} anchor(s), last e{status['last_event']}"
        if status["count"]
        else "none yet (every 25 events and on WIN)"
    )
    if status["failed"]:
        line += f" | last write FAILED at {status['failed']}"
    elif not status["writable"]:
        line += " | directory NOT WRITABLE, heads stay chain-only until fixed"
    return line


def _advance(head: str, line: str) -> str:
    return hashlib.sha256(head.encode() + line.encode()).hexdigest()


def compute_chain(paths: RunPaths) -> tuple[int, str]:
    """(last event id, head) recomputed from the whole journal; (-1, seed) when empty."""
    head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
    last = -1
    try:
        lines = paths.events.read_text().splitlines()
    except FileNotFoundError:
        return last, head
    for line in lines:
        if not line.strip():
            continue
        head = _advance(head, line)
        last += 1
    return last, head


def extend_chain(paths: RunPaths, appended_line: str, event_id: int, win: bool) -> None:
    """Advance the stored chain by one appended journal line; anchor when due.

    The daemon calls this immediately after each event append. If the stored
    state is behind (older appends, recovery), it recomputes from the journal —
    correctness over speed at this file size."""
    state = read_json(chain_path(paths), None)
    if (
        isinstance(state, dict)
        and int(state.get("event_id", -2)) == event_id - 1
        and isinstance(state.get("head"), str)
    ):
        head = _advance(state["head"], appended_line)
    else:
        _, head = compute_chain(paths)
    atomic_json(chain_path(paths), {"event_id": event_id, "head": head})
    if win or (event_id > 0 and event_id % ANCHOR_EVERY == 0):
        target = anchor_file(paths)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            append_jsonl(
                target,
                {"event_id": event_id, "head": head, "run": str(paths.root)},
            )
        except OSError as error:
            # An unanchorable filesystem degrades to chain-only integrity. The
            # spend is never failed over it, but the failure is journaled to
            # activity and shown in status, never swallowed.
            append_jsonl(
                paths.activity,
                {
                    "kind": "anchor_failed",
                    "event": event_id,
                    "file": str(target),
                    "error": f"{type(error).__name__}: {error}",
                },
            )


def redact(text: str | None, extra_names: Sequence[str] = ()) -> str | None:
    """Replace secret env values with [REDACTED:<NAME>] before journaling."""
    if not text:
        return text
    for name in (*DEFAULT_SECRET_ENV, *extra_names):
        value = os.getenv(name)
        if value and len(value) >= _MIN_SECRET_LENGTH and value in text:
            text = text.replace(value, f"[REDACTED:{name}]")
    return text


def redact_mapping(
    value: Mapping[str, Any] | None, extra_names: Sequence[str] = ()
) -> dict[str, Any] | None:
    """Redact every string leaf of an agent-supplied mapping (reasoning etc.)."""
    if value is None:
        return None

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return redact(node, extra_names)
        if isinstance(node, Mapping):
            return {key: _walk(item) for key, item in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(dict(value))


def ungated_events(events: Sequence[Mapping[str, Any]]) -> list[int]:
    """Paid non-RESET events with no prediction and no grade — the
    pre-registered bypass definition (evidence/bypass_audit.py)."""
    flagged: list[int] = []
    for event in events:
        if not event.get("counts_action") or event.get("action") == "RESET":
            continue
        gated = (
            event.get("predict")
            or event.get("predict_ok") is not None
            or event.get("grade")
        )
        if not gated:
            flagged.append(int(event["id"]))
    return flagged


def first_ungated(events: Sequence[Mapping[str, Any]]) -> int | None:
    flagged = ungated_events(events)
    return flagged[0] if flagged else None


_PERMIT_MARKERS = (("gate_optional", "optional"), ("gate_off", "off"))


def ungated_permitted(events: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """The ungated events a control arm permitted, keyed by id, with the mode
    whose marker the event carries (`gate_optional` or `gate_off`). The mode
    permits exactly what it marks: an ungated event without a marker was not
    permitted, whatever the registry says. Such events stay ungated and the
    run stays invalid for scoring; they are only counted apart."""
    by_id = {int(event["id"]): event for event in events if event.get("id") is not None}
    permitted: dict[int, str] = {}
    for event_id in ungated_events(events):
        event = by_id.get(event_id, {})
        for marker, mode in _PERMIT_MARKERS:
            if event.get(marker):
                permitted[event_id] = mode
                break
    return permitted


def audit(paths: RunPaths) -> dict[str, Any]:
    """Recompute integrity from the artifacts; write .assay/audit.json; return it."""
    from .core import load_events

    problems: list[str] = []
    try:
        events = load_events(paths)  # raises on non-contiguous ids
        contiguous = True
    except Exception as error:  # noqa: BLE001 - the audit reports, never crashes
        events = load_jsonl(paths.events)
        contiguous = False
        problems.append(f"contiguity: {error}")
    last_id, head = compute_chain(paths)
    stored = read_json(chain_path(paths), None)
    chain_state = "absent"
    if isinstance(stored, dict):
        if int(stored.get("event_id", -2)) == last_id and stored.get("head") == head:
            chain_state = "intact"
        else:
            chain_state = "DIVERGED"
            problems.append(
                f"chain: stored head at e{stored.get('event_id')} does not match "
                f"the recomputed journal head at e{last_id}"
            )
    anchor_target = anchor_file(paths)
    anchors = load_jsonl(anchor_target) if anchor_target.exists() else []
    anchor_state = "none"
    recorded = recorded_anchor_file(paths)
    anchor_env_mismatch = (
        recorded is not None and environment_anchor_file(paths) != recorded
    )
    if anchor_env_mismatch:
        problems.append(
            f"anchor_env_mismatch: ASSAY_ANCHOR_DIR names {environment_anchor_file(paths).parent} "
            f"but this run anchors to {recorded.parent} (recorded at start); the "
            "recorded file was audited"
        )
    if anchors:
        latest = anchors[-1]
        replay_head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
        try:
            lines = [
                line for line in paths.events.read_text().splitlines() if line.strip()
            ]
        except FileNotFoundError:
            lines = []
        target = int(latest["event_id"])
        if target < len(lines):
            for line in lines[: target + 1]:
                replay_head = _advance(replay_head, line)
            anchor_state = (
                "intact" if replay_head == latest["head"] else "DIVERGED"
            )
            if anchor_state == "DIVERGED":
                problems.append(
                    f"anchor: journal prefix at e{target} no longer matches the "
                    "externally anchored head — the journal changed after anchoring"
                )
        else:
            anchor_state = "DIVERGED"
            problems.append("anchor: anchored event id beyond the journal")
    ungated = ungated_events(events)
    # The control arms (gate: optional, gate: off) permit bare acts; they are
    # counted apart, with the mode that permitted them, and stay ungated.
    permitted_modes = ungated_permitted(events)
    mutations = load_jsonl(paths.mutations)
    journaled = {
        int(event["mutation_id"]) for event in events if event.get("mutation_id") is not None
    }
    pending = [
        int(item["mutation_id"])
        for item in mutations
        if item.get("mutation_id") is not None and int(item["mutation_id"]) not in journaled
    ]
    recovered = [
        int(event["id"])
        for event in events
        if "recovered from broker mutation journal" in str(event.get("note") or "")
    ]
    report = {
        "computed_at": time.time(),
        "events": len(events),
        "paid": sum(1 for event in events if event.get("counts_action")),
        "mutations": len(mutations),
        "contiguous": contiguous,
        "chain": chain_state,
        "anchors": anchor_state,
        "anchor_count": len(anchors),
        "anchor_file": str(anchor_target),
        "anchor_env_mismatch": anchor_env_mismatch,
        "ungated": ungated,
        "ungated_permitted": sorted(permitted_modes),
        "ungated_permitted_by": sorted(set(permitted_modes.values())),
        "recovered_orphans": recovered,
        "mutations_pending": pending,
        "invalid_for_scoring": bool(ungated) or not contiguous
        or chain_state == "DIVERGED" or anchor_state == "DIVERGED",
        "problems": problems,
    }
    atomic_json(paths.state / "audit.json", report)
    return report


def audit_lines(report: Mapping[str, Any]) -> list[str]:
    verdict = "INVALID FOR SCORING" if report["invalid_for_scoring"] else "CLEAN"
    lines = [
        f"AUDIT | {verdict} | events {report['events']} (paid {report['paid']}) | "
        f"contiguous {'yes' if report['contiguous'] else 'NO'} | "
        f"chain {report['chain']} | anchors {report['anchors']} ({report['anchor_count']})",
    ]
    if report["ungated"]:
        lines.append(
            f"AUDIT | UNGATED events {report['ungated'][:8]} — the run is invalid "
            "for scoring and trust earned after the first one is demoted"
        )
    if report.get("ungated_permitted"):
        modes = ", ".join(f"`gate: {mode}`" for mode in report.get("ungated_permitted_by") or ["optional"])
        lines.append(
            f"AUDIT | {len(report['ungated_permitted'])} of them permitted by "
            f"{modes} (control arm) — still invalid for scoring"
        )
    if report["recovered_orphans"]:
        lines.append(
            f"AUDIT | recovered orphan events {report['recovered_orphans'][:8]} "
            "(spend journaled by the broker; CLI died before recording)"
        )
    if report.get("mutations_pending"):
        pending = report["mutations_pending"]
        lines.append(
            f"AUDIT | {len(pending)} spend(s) in the mutation journal not yet in the "
            f"timeline {pending[:8]} (a step in flight, or a crash between spend and "
            "record; `assay start` recovers them once the daemon is gone)"
        )
    for problem in report["problems"]:
        lines.append(f"AUDIT | problem: {problem}")
    return lines
