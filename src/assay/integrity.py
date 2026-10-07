"""Journal integrity: rolling hash chain, anchoring, the ungated-event audit,
and secrets redaction at the journal boundary.

The chain makes the journal TAMPER-EVIDENT under stated conditions, not
unforgeable, and says so out loud: head_0 = sha256("assay-chain-v1"),
head_n = sha256(head_{n-1} || line_n). The daemon advances the held head with
every line `run.append` writes, updates `.assay/chain.json` after each append
and anchors the head OUTSIDE the run directory
(`~/.assay/anchors/<run-digest>.jsonl`, override with ASSAY_ANCHOR_DIR) every
ANCHOR_EVERY events and on WIN (`run.Run.append`). `assay audit` recomputes
everything from the journal and reports:

- chain intact / diverged (and against the anchors),
- event-id contiguity,
- UNGATED events: a paid non-RESET event carrying no prediction and no grade
  (the definition the archived bypass audits pre-registered). An ungated event
  INVALIDATES THE RUN FOR SCORING and demotes all trust earned after it: the
  gate refuses model-plan batching once one exists.

Secrets redaction: values of registered secret env vars (plus the kernel's
default list) never enter the journal; every agent-supplied string field is
filtered at the boundary before it is written.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core import RunPaths, atomic_json, load_jsonl
from .records import Event

if TYPE_CHECKING:
    from .run import Run

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


def recorded_anchor_file(config: Mapping[str, Any] | None) -> Path | None:
    """The anchor file pinned in config.json at start, or None for a run that
    predates the key."""
    if isinstance(config, Mapping) and isinstance(config.get("anchor_file"), str):
        return Path(config["anchor_file"])
    return None


def anchor_file(paths: RunPaths, config: Mapping[str, Any] | None) -> Path:
    """Where this run's chain heads go: the file recorded in config.json at
    start, so a later shell with a different ASSAY_ANCHOR_DIR still anchors and
    audits against the same file. Runs without the key use the environment."""
    recorded = recorded_anchor_file(config)
    return recorded if recorded is not None else environment_anchor_file(paths)


def anchor_status(paths: RunPaths, config: Mapping[str, Any] | None) -> dict[str, Any]:
    """The anchor line's facts: file, count, last anchored event, the last
    failed write (if newer than the last anchor), and whether the directory
    can be written now."""
    target = anchor_file(paths, config)
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


def anchor_line(paths: RunPaths, config: Mapping[str, Any] | None) -> str:
    status = anchor_status(paths, config)
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


def ungated_events(events: Sequence[Event]) -> list[int]:
    """Paid non-RESET events with no prediction and no grade: the ungated-event
    rule of verify/JOURNAL_SPEC.md section 6, the one the independent checker
    (verify/assay_verify.py) applies over the same journal fields."""
    return [event.id for event in events if event.ungated]


def first_ungated(events: Sequence[Event]) -> int | None:
    flagged = ungated_events(events)
    return flagged[0] if flagged else None


def ungated_permitted(events: Sequence[Event]) -> dict[int, str]:
    """The ungated events a control arm permitted, keyed by id, with the mode
    whose marker the event carries (`gate_optional` or `gate_off`). The mode
    permits exactly what it marks: an ungated event without a marker was not
    permitted, whatever the registry says. Such events stay ungated and the
    run stays invalid for scoring; they are only counted apart."""
    permitted: dict[int, str] = {}
    for event in events:
        if not event.ungated:
            continue
        if event.gate_optional:
            permitted[event.id] = "optional"
        elif event.gate_off:
            permitted[event.id] = "off"
    return permitted


def audit(run: Run) -> dict[str, Any]:
    """Recompute integrity from the artifacts; write .assay/audit.json; return it."""
    paths = run.paths
    events = run.events
    problems: list[str] = []
    contiguous = run.integrity.contiguous
    if not contiguous:
        problems.append(f"contiguity: {run.integrity.problem}")
    last_id, head = run.chain_event, run.chain_head
    stored = run.stored_chain
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
    anchor_target = anchor_file(paths, run.config)
    anchors = load_jsonl(anchor_target) if anchor_target.exists() else []
    anchor_state = "none"
    recorded = recorded_anchor_file(run.config)
    anchor_env_mismatch = (
        recorded is not None and environment_anchor_file(paths) != recorded
    )
    if anchor_env_mismatch and recorded is not None:
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
                    "externally anchored head; the journal changed after anchoring"
                )
        else:
            anchor_state = "DIVERGED"
            problems.append("anchor: anchored event id beyond the journal")
    ungated = ungated_events(events)
    # The control arms (gate: optional, gate: off) permit bare acts; they are
    # counted apart, with the mode that permitted them, and stay ungated.
    permitted_modes = ungated_permitted(events)
    journaled = {event.mutation_id for event in events if event.mutation_id is not None}
    pending = [
        mutation.mutation_id
        for mutation in run.mutations
        if mutation.mutation_id not in journaled
    ]
    recovered = [
        event.id
        for event in events
        if "recovered from broker mutation journal" in str(event.note or "")
    ]
    report = {
        "computed_at": time.time(),
        "events": len(events),
        "paid": sum(1 for event in events if event.counts_action),
        "mutations": len(run.mutations),
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
            f"AUDIT | UNGATED events {report['ungated'][:8]}; the run is invalid "
            "for scoring and trust earned after the first one is demoted"
        )
    if report.get("ungated_permitted"):
        modes = ", ".join(f"`gate: {mode}`" for mode in report.get("ungated_permitted_by") or ["optional"])
        lines.append(
            f"AUDIT | {len(report['ungated_permitted'])} of them permitted by "
            f"{modes} (control arm), still invalid for scoring"
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
