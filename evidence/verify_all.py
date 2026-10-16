#!/usr/bin/env python3
"""Verify every published journal in evidence/ against its published head.

For each pack with a heads.json, every run's journal is read from its gzip
(with the chain.json beside it when the pack has one) and judged by the
verdict rules of verify/JOURNAL_SPEC.md section 8, with the kernel's own code:
the chain rule (`assay.integrity.chain_over`), the ungated rule
(`assay.integrity.ungated_events`) over the kernel's event records, and the
contiguity of the ids. The verdict must be the one the pack publishes, and
the recomputed head the published one.

Run it with an interpreter that can import the kernel from ../src, as the
test suite does (numpy installed).

Usage: python3 evidence/verify_all.py [--pack NAME ...]
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(EVIDENCE.parent / "src"))

from assay.integrity import chain_over, ungated_events  # noqa: E402
from assay.records import Event  # noqa: E402


def journal_of(pack: Path, run: dict) -> tuple[Path, Path | None]:
    """(journal.jsonl.gz, chain.json or None) for one heads.json entry."""
    name = run.get("run") or run.get("game") or run.get("task") or run.get("rung")
    folder = pack / str(name)
    if folder.is_dir():
        chain = folder / "chain.json"
        return folder / "journal.jsonl.gz", (chain if chain.is_file() else None)
    return pack / f"journal-{name}.jsonl.gz", None


def judge(lines: list[str], chain: Path | None, expect_head: str) -> dict:
    """The spec's verdict over one journal: contiguity, the ungated rule,
    the stored chain, the published head."""
    problems: list[str] = []
    records = []
    for index, line in enumerate(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            problems.append(f"line {index}: not valid JSON ({error})")
            continue
        if value.get("id") != index:
            problems.append(f"line {index}: event id {value.get('id')!r} != position {index}")
        records.append(value)
    try:
        ungated = ungated_events([Event.from_json(record) for record in records])
    except (TypeError, ValueError) as error:
        problems.append(f"event: {error}")
        ungated = []
    head = chain_over(lines)
    stored = "absent"
    if chain is not None:
        held = json.loads(chain.read_text())
        stored = "intact" if held.get("head") == head and held.get("event_id") == len(lines) - 1 else "DIVERGED"
    matched = head == expect_head.lower()
    invalid = bool(problems) or bool(ungated) or stored == "DIVERGED" or not matched
    return {
        "verdict": "INVALID FOR SCORING" if invalid else "CLEAN",
        "events": len(records),
        "paid": sum(1 for record in records if record.get("counts_action")),
        "ungated": ungated,
        "stored_chain": stored,
        "head_matches": matched,
        "problems": problems,
    }


def check(pack: Path, run: dict) -> tuple[bool, str]:
    journal, chain = journal_of(pack, run)
    if not journal.is_file():
        return False, f"missing {journal.relative_to(EVIDENCE)}"
    with gzip.open(journal, "rt", encoding="utf-8") as source:
        lines = [line for line in source.read().splitlines() if line.strip()]
    report = judge(lines, chain, str(run["chain_head"]))
    # A published INVALID verdict (a control arm's ungated events) still has
    # to match its head: the head is what was published.
    expected = run.get("verdict", "CLEAN")
    ok = report["verdict"] == expected and report["head_matches"]
    return ok, (
        f"{report['verdict']} | events {report['events']} paid {report['paid']} "
        f"| head {'match' if report['head_matches'] else 'MISMATCH'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", action="append", help="limit to these packs")
    args = parser.parse_args()
    packs = sorted(p for p in EVIDENCE.iterdir() if (p / "heads.json").is_file())
    if args.pack:
        packs = [p for p in packs if p.name in args.pack]
    checked = failures = 0
    for pack in packs:
        for run in json.loads((pack / "heads.json").read_text())["runs"]:
            ok, detail = check(pack, run)
            checked += 1
            failures += 0 if ok else 1
            name = run.get("run") or run.get("game") or run.get("task") or run.get("rung")
            print(f"{'ok' if ok else 'FAIL'} | {pack.name}/{name} | {detail}")
    print(f"evidence | {'PASS' if not failures else 'FAIL'} | {checked} journals, {failures} failed")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
