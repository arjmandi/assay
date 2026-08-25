#!/usr/bin/env python3
"""assay-verify — a standalone, stdlib-only reader for the ASSAY journal
format (JOURNAL_SPEC.md v1, CLAIM_GRAMMAR.md v1).

This file imports nothing from ASSAY's own kernel (`src/assay/`) or from any
other part of the repo. It is written directly against the two spec
documents at the repo root, on purpose: an independent reader agreeing with
the kernel's own `assay audit` on the same artifacts is the trust claim.

It recomputes an integrity verdict from a run directory's artifacts alone:

  - the rolling hash chain over raw journal lines (JOURNAL_SPEC.md §3)
  - event-id contiguity (§4)
  - gate coverage — the "ungated event" scan (§5)
  - predict/grade consistency (§6) — a check the kernel's own `assay audit`
    does not currently perform; see §8/§6 of the spec for why this is an
    intentional, additive superset, not a parity requirement
  - an externally anchored chain head, if one is supplied (§7)

Usage:
    assay_verify.py RUN_DIR [--anchor-file PATH] [--expect-head ID:HEAD] [--json]

Exit status: 0 = CLEAN, 1 = INVALID FOR SCORING, 2 = usage/read error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

CHAIN_SEED = "assay-chain-v1"


class VerifyError(RuntimeError):
    pass


def _advance(head: str, line: str) -> str:
    return hashlib.sha256(head.encode() + line.encode()).hexdigest()


def read_journal_lines(events_path: Path) -> list[str]:
    """Raw, non-blank journal lines, in order — the exact bytes the chain
    covers. Never reparsed and re-serialized (JOURNAL_SPEC.md §3)."""
    try:
        text = events_path.read_text()
    except FileNotFoundError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def parse_events(lines: list[str]) -> list[dict[str, Any]]:
    events = []
    for line_number, line in enumerate(lines, 1):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise VerifyError(
                f"corrupt JSON at events.jsonl line {line_number}: {error}"
            ) from error
    return events


def compute_chain(lines: list[str]) -> tuple[int, str]:
    """(last event id folded in, head); (-1, seed hash) for an empty journal."""
    head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
    last = -1
    for line in lines:
        head = _advance(head, line)
        last += 1
    return last, head


def check_contiguity(events: list[dict[str, Any]]) -> str | None:
    """None if contiguous, else a problem string."""
    for index, event in enumerate(events):
        if event.get("id") != index:
            return (
                f"contiguity: event at line {index} carries id "
                f"{event.get('id')!r}, expected {index}"
            )
    return None


def ungated_events(events: list[dict[str, Any]]) -> list[int]:
    """Paid, non-RESET events with no predict/predict_ok/grade
    (JOURNAL_SPEC.md §5)."""
    flagged = []
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


def derive_predict_ok(grade: list[dict[str, Any]]) -> bool | None:
    """CLAIM_GRAMMAR.md §4: derive the predict_ok a `grade` array implies."""
    invalid_any = any(item.get("invalid") or item.get("ungradable") for item in grade)
    missed = any(
        not item.get("ok")
        for item in grade
        if not item.get("invalid") and not item.get("ungradable")
    )
    if missed:
        return False
    if invalid_any:
        return None
    return True


def predict_grade_mismatches(events: list[dict[str, Any]]) -> list[int]:
    """Events whose journaled predict_ok disagrees with what their own
    grade array implies (JOURNAL_SPEC.md §6)."""
    mismatches = []
    for event in events:
        grade = event.get("grade")
        if grade is None:
            continue
        if derive_predict_ok(grade) != event.get("predict_ok"):
            mismatches.append(int(event["id"]))
    return mismatches


def load_anchor_entries(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise VerifyError(f"anchor file not found: {path}") from None
    entries = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise VerifyError(
                f"corrupt JSON at {path} line {line_number}: {error}"
            ) from error
    return entries


def check_anchor(
    lines: list[str], event_id: int, expected_head: str
) -> tuple[str, str | None]:
    """(state, problem) for one anchored (event_id, head) pair against the
    journal's raw lines (JOURNAL_SPEC.md §7)."""
    if event_id < 0 or event_id >= len(lines):
        return "DIVERGED", f"anchored event id {event_id} is beyond the journal"
    head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
    for line in lines[: event_id + 1]:
        head = _advance(head, line)
    if head == expected_head:
        return "intact", None
    return "DIVERGED", (
        f"anchor: journal prefix at event {event_id} no longer matches the "
        "externally anchored head — the journal changed after anchoring"
    )


def audit(
    run_dir: Path,
    *,
    anchor_file: Path | None = None,
    expect_head: tuple[int, str] | None = None,
) -> dict[str, Any]:
    events_path = run_dir / ".assay" / "events.jsonl"
    chain_path = run_dir / ".assay" / "chain.json"

    lines = read_journal_lines(events_path)
    events = parse_events(lines)

    problems: list[str] = []
    contiguity_problem = check_contiguity(events)
    contiguous = contiguity_problem is None
    if contiguity_problem:
        problems.append(contiguity_problem)

    last_id, head = compute_chain(lines)

    stored = None
    try:
        stored = json.loads(chain_path.read_text())
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as error:
        raise VerifyError(f"corrupt JSON in {chain_path}: {error}") from error

    chain_state = "absent"
    if isinstance(stored, dict):
        if int(stored.get("event_id", -2)) == last_id and stored.get("head") == head:
            chain_state = "intact"
        else:
            chain_state = "DIVERGED"
            problems.append(
                f"chain: stored head at e{stored.get('event_id')} does not "
                f"match the recomputed journal head at e{last_id}"
            )

    anchor_state = "none"
    anchor_count = 0
    if expect_head is not None:
        anchor_count = 1
        anchor_state, anchor_problem = check_anchor(lines, *expect_head)
        if anchor_problem:
            problems.append(anchor_problem)
    elif anchor_file is not None:
        entries = load_anchor_entries(anchor_file)
        anchor_count = len(entries)
        if entries:
            latest = entries[-1]
            anchor_state, anchor_problem = check_anchor(
                lines, int(latest["event_id"]), str(latest["head"])
            )
            if anchor_problem:
                problems.append(anchor_problem)

    ungated = ungated_events(events)
    mismatches = predict_grade_mismatches(events)
    if mismatches:
        problems.append(
            f"predict/grade: events {mismatches[:8]} carry a predict_ok that "
            "disagrees with their own grade array"
        )

    return {
        "spec_version": CHAIN_SEED,
        "events": len(events),
        "paid": sum(1 for event in events if event.get("counts_action")),
        "contiguous": contiguous,
        "chain": chain_state,
        "recomputed_head": head,
        "last_event_id": last_id,
        "anchors": anchor_state,
        "anchor_count": anchor_count,
        "ungated": ungated,
        "predict_grade_mismatches": mismatches,
        "invalid_for_scoring": bool(
            ungated
            or not contiguous
            or chain_state == "DIVERGED"
            or anchor_state == "DIVERGED"
            or mismatches
        ),
        "problems": problems,
    }


def format_report(report: dict[str, Any]) -> list[str]:
    verdict = "INVALID FOR SCORING" if report["invalid_for_scoring"] else "CLEAN"
    lines = [
        f"VERIFY | {verdict} | events {report['events']} (paid {report['paid']}) | "
        f"contiguous {'yes' if report['contiguous'] else 'NO'} | "
        f"chain {report['chain']} | anchors {report['anchors']} ({report['anchor_count']})",
        f"VERIFY | recomputed head e{report['last_event_id']} = {report['recomputed_head']}",
    ]
    if report["ungated"]:
        lines.append(f"VERIFY | UNGATED events {report['ungated'][:8]}")
    if report["predict_grade_mismatches"]:
        lines.append(
            f"VERIFY | PREDICT/GRADE MISMATCH events "
            f"{report['predict_grade_mismatches'][:8]}"
        )
    for problem in report["problems"]:
        lines.append(f"VERIFY | problem: {problem}")
    return lines


def _parse_expect_head(raw: str) -> tuple[int, str]:
    event_id, separator, head = raw.partition(":")
    if not separator:
        raise argparse.ArgumentTypeError("--expect-head needs EVENT_ID:HEAD")
    try:
        return int(event_id), head
    except ValueError:
        raise argparse.ArgumentTypeError("--expect-head's EVENT_ID must be an int") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assay-verify",
        description="Recompute an ASSAY journal's integrity verdict from its "
        "artifacts alone (stdlib only, no ASSAY import).",
    )
    parser.add_argument("run_dir", type=Path, help="a run directory (contains .assay/)")
    parser.add_argument(
        "--anchor-file", type=Path, default=None, help="a bundled anchors.jsonl to check against"
    )
    parser.add_argument(
        "--expect-head",
        type=_parse_expect_head,
        default=None,
        help="a published EVENT_ID:HEAD pair to check against, instead of a file",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    try:
        report = audit(
            args.run_dir, anchor_file=args.anchor_file, expect_head=args.expect_head
        )
    except VerifyError as error:
        print(f"assay-verify: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for line in format_report(report):
            print(line)
    return 1 if report["invalid_for_scoring"] else 0


if __name__ == "__main__":
    sys.exit(main())
