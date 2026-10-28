#!/usr/bin/env python3
"""The Stop hook of the Claude Code + Stop gate control: the operator layer of
the ASSAY rounds with no ASSAY under it.

It applies the gate of `hooks/stop_gate.py` (loaded by path, so the rules are
the same code): the `coverage` demand, a block new by content whose RULES
cite an event of this session, the first-stop hold, the release after three
blocks or fifty paid actions under a hold. The facts come from the baseline's
files instead of an ASSAY run directory: the game server's journal (the
event ids `play.py` prints) and `NOTES.md` in the player's directory. The
message's pointers to ASSAY (`"$ASSAY" status`, `.assay/NOTES.md`) are
rewritten to the player's own (`play.py status`, `NOTES.md`).

usage (as the hook command): stop_gate_baseline.py --run-dir DIR --journal DIR --cap N
Inert without ASSAY_STOP_GATE, like the gate itself.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GATE = HERE.parents[2] / "hooks" / "stop_gate.py"


def load_gate() -> Any:
    name = "assay_stop_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, GATE)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {GATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_view(gate: Any, run_dir: Path, journal: Path, cap: int) -> Any:
    """The baseline run as the gate reads it, or None before the first event."""
    path = journal / ".arc" / "events.jsonl"
    try:
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except OSError:
        return None
    if not events:
        return None
    try:
        notes = (run_dir / "NOTES.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        notes = ""
    return gate.RunView(
        state=str(events[-1].get("state", "")),
        paid=sum(1 for event in events if event.get("counts_action")),
        cap=cap,
        usd=0.0,
        usd_cap=None,
        activity=(),
        notes=notes,
        last_event=int(events[-1].get("id", len(events) - 1)),
    )


def plain(reason: str) -> str:
    """The gate's message with the baseline's pointers in place of ASSAY's."""
    return (
        reason.replace('`"$ASSAY" status` and .assay/NOTES.md', "`play.py status` and NOTES.md")
        .replace(".assay/NOTES.md", "NOTES.md")
        .replace('`"$ASSAY" status`', "`play.py status`")
        .replace("ASSAY stop gate:", "Stop gate:")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stop_gate_baseline.py")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--cap", type=int, default=1500)
    args = parser.parse_args(argv)
    state_file = os.environ.get("ASSAY_STOP_GATE")
    if not state_file:
        return 0
    gate = load_gate()
    try:
        payload = json.loads(sys.stdin.read() or "null")
    except ValueError:
        payload = None
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    view = load_view(gate, Path(args.run_dir), Path(args.journal), args.cap)
    if view is None:
        return 0
    path = Path(state_file)
    state = gate.load_state(path)
    allow, reason = gate.decide(view, state, session_id if isinstance(session_id, str) else None)
    reason = plain(reason)
    decisions = state.get("decisions") if isinstance(state.get("decisions"), list) else []
    decisions.append({
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "session_id": state.get("session_id"), "allow": allow, "why": reason, "paid": view.paid,
    })
    state["decisions"] = decisions[-gate.KEPT_DECISIONS:]
    gate.save_state(path, state)
    if not allow:
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
