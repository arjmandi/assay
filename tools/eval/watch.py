"""The watch mode: one line per change of a run, read from its files.

    python3 -m tools.eval watch --run-dir DIR [--every S] [--once] [--until-end]

Each line says where the run stands: the session runner's state, the paid
actions of the cap, the outcomes held and missed (counted as `assay status`
counts them), the last paid action and how it graded, the spend reported to
the run and the Stop gate's blocks. A line is printed only when something on
it changed, so a quiet run prints nothing. `--once` prints one line and
exits; `--until-end` exits when the session runner says it ended, or the run
reached WIN or a cap. `/assay:watch` runs it.

It reads the run's files as `report.py` does and writes nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path
from typing import Any

from . import EvalError
from .report import build, grades
from .session import load_gate

FINISHED = ("WIN", "CAP", "USD_CAP")


def line(record: dict[str, Any]) -> str:
    """The run as one line, without the time of day."""
    end = record["ending"]
    counts = record["predictions"]["meter"]
    held = sum(counts[bucket]["graded"] - counts[bucket]["missed"] for bucket in ("world_model", "gamble", "other"))
    missed = sum(counts[bucket]["missed"] for bucket in ("world_model", "gamble", "other"))
    runner = end.get("runner") or {}
    state = runner.get("state") or "no runner"
    if state == "ended":
        state = f"ended {runner.get('outcome')}"
    cap = end["cap"] if end["cap"] is not None else "no cap"
    parts = [state, f"{end['outcome']}", f"paid {end['paid']}/{cap}", f"held {held} missed {missed}"]
    last = record.get("last")
    if last:
        parts.append(f"last {last}")
    spend = record["activity"]["spend"]
    if spend["reports"]:
        parts.append(f"${spend['usd']:.2f}")
    sessions = record["sessions"]
    if sessions and sessions["totals"]["blocks"]:
        parts.append(f"stop blocks {sessions['totals']['blocks']}")
    return " | ".join(parts)


def last_action(run_dir: Path) -> str | None:
    """The last paid action and how it graded: `e14 ASK held` or `e15 ASK
    missed: ch last_ok = true (ch last_ok = false)`."""
    gate = load_gate()
    for name in (".assay", ".arc"):
        events = gate.read_jsonl(run_dir / name / "events.jsonl")
        if events:
            break
    paid = [event for event in events if event.get("counts_action")]
    if not paid:
        return None
    event = paid[-1]
    head = f"e{event.get('id')} {event.get('action')}"
    wrong = [grade for grade in grades(event) if grade.get("kind") != "note" and not grade.get("ok")]
    if not grades(event):
        return head
    if not wrong:
        return f"{head} held"
    first = wrong[0]
    return f"{head} missed: {first.get('text')} ({first.get('actual')})"


def snapshot(run_dir: Path) -> dict[str, Any]:
    record = build(run_dir)
    record["last"] = last_action(run_dir)
    return record


def ended(record: dict[str, Any]) -> bool:
    runner = record["ending"].get("runner") or {}
    return runner.get("state") == "ended" or record["ending"]["outcome"] in FINISHED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m tools.eval watch", description="One line per change of a run.")
    parser.add_argument("--run-dir", type=Path, required=True, metavar="DIR")
    parser.add_argument("--every", type=float, default=10.0, metavar="S", help="seconds between looks (default 10)")
    parser.add_argument("--once", action="store_true", help="print one line and exit")
    parser.add_argument("--until-end", action="store_true", help="exit when the run or its session runner ends")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.every <= 0:
            raise EvalError("--every must be positive")
        run_dir = args.run_dir.resolve()
        previous = None
        while True:
            record = snapshot(run_dir)
            text = line(record)
            if text != previous:
                stamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
                print(f"WATCH | {stamp} | {text}", flush=True)
                previous = text
            if args.once or (args.until_end and ended(record)):
                return 0
            time.sleep(args.every)
    except EvalError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 0
