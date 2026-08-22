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
import json
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
    configured = os.getenv("ASSAY_ANCHOR_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".assay" / "anchors"


def anchor_file(paths: RunPaths) -> Path:
    digest = hashlib.sha256(str(paths.root.resolve()).encode()).hexdigest()[:24]
    return anchor_dir() / f"{digest}.jsonl"


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
        try:
            anchor_dir().mkdir(parents=True, exist_ok=True)
            append_jsonl(
                anchor_file(paths),
                {"event_id": event_id, "head": head, "run": str(paths.root)},
            )
        except OSError:
            # An unanchorable filesystem degrades to chain-only integrity;
            # the audit reports the missing anchor rather than failing spends.
            pass


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
    anchors = load_jsonl(anchor_file(paths)) if anchor_file(paths).exists() else []
    anchor_state = "none"
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
    mutations = load_jsonl(paths.mutations)
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
        "ungated": ungated,
        "recovered_orphans": recovered,
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
    if report["recovered_orphans"]:
        lines.append(
            f"AUDIT | recovered orphan events {report['recovered_orphans'][:8]} "
            "(spend journaled by the broker; CLI died before recording)"
        )
    for problem in report["problems"]:
        lines.append(f"AUDIT | problem: {problem}")
    return lines
