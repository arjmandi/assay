#!/usr/bin/env python3
"""Verify every published journal in evidence/ against its published head.

For each pack with a heads.json, every run's journal is decompressed into a
scratch directory (beside its chain.json when the pack has one) and handed to
the independent checker with --expect-head. The verdict must be the one the
pack publishes. Standard library only; the checker is ../verify/assay_verify.py.

Usage: python3 evidence/verify_all.py [--pack NAME ...]
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
CHECKER = EVIDENCE.parent / "verify" / "assay_verify.py"


def journal_of(pack: Path, run: dict) -> tuple[Path, Path | None]:
    """(journal.jsonl.gz, chain.json or None) for one heads.json entry."""
    name = run.get("run") or run.get("game") or run.get("task") or run.get("rung")
    folder = pack / str(name)
    if folder.is_dir():
        chain = folder / "chain.json"
        return folder / "journal.jsonl.gz", (chain if chain.is_file() else None)
    return pack / f"journal-{name}.jsonl.gz", None


def check(pack: Path, run: dict, scratch: Path) -> tuple[bool, str]:
    journal, chain = journal_of(pack, run)
    if not journal.is_file():
        return False, f"missing {journal.relative_to(EVIDENCE)}"
    target = scratch / pack.name / journal.parent.name
    target.mkdir(parents=True, exist_ok=True)
    with gzip.open(journal, "rb") as source, (target / "journal.jsonl").open("wb") as sink:
        shutil.copyfileobj(source, sink)
    if chain is not None:
        shutil.copy(chain, target / "chain.json")
    completed = subprocess.run(
        [sys.executable, str(CHECKER), str(target / "journal.jsonl"), "--json",
         "--expect-head", str(run["chain_head"])],
        capture_output=True, text=True, timeout=600,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False, f"checker error: {completed.stderr.strip()[:200]}"
    expected = run.get("verdict", "CLEAN")
    ok = report.get("verdict") == expected and report.get("expect_head") in (None, "match")
    return ok, (
        f"{report.get('verdict')} | events {report.get('events')} paid {report.get('paid')} "
        f"| head {'match' if ok else report.get('expect_head')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", action="append", help="limit to these packs")
    args = parser.parse_args()
    packs = sorted(p for p in EVIDENCE.iterdir() if (p / "heads.json").is_file())
    if args.pack:
        packs = [p for p in packs if p.name in args.pack]
    checked = failures = 0
    with tempfile.TemporaryDirectory(prefix="assay-evidence-") as scratch:
        for pack in packs:
            for run in json.loads((pack / "heads.json").read_text())["runs"]:
                ok, detail = check(pack, run, Path(scratch))
                checked += 1
                failures += 0 if ok else 1
                name = run.get("run") or run.get("game") or run.get("task") or run.get("rung")
                print(f"{'ok' if ok else 'FAIL'} | {pack.name}/{name} | {detail}")
    print(f"evidence | {'PASS' if not failures else 'FAIL'} | {checked} journals, {failures} failed")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
