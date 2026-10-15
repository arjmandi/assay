"""The spreadsheet-reconcile checker: a final working copy against the answer
key. It reads only the ledgers and imports nothing from the harness, so it
scores any arm of a comparison the same way, a run under ASSAY or a baseline
that worked on its own copy.

    python3 checks/score.py --run RUN_DIR             the seed from .assay/config.json, the copy in .assay/world
    python3 checks/score.py --seed S --world DIR      any working copy
    add --json for one JSON object

The verdict is correct when every month is closed, every bank line is paired
with its true books line (either copy of a duplicate; for a bank fee or
interest line, any books line of that kind and amount), every true timing
difference is flagged with its reason, no other line is flagged, and the only
deleted lines are one copy of each duplicate. The exit status is 0 for
correct and 1 otherwise.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconcile import MONTHS, RECORD_CATEGORIES, truth  # noqa: E402


def _rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="") as handle:
        return {row["id"]: dict(row) for row in csv.DictReader(handle)}


def _bank_kind(row: dict[str, str]) -> str | None:
    description = row["description"].upper()
    for category, (ref, _) in RECORD_CATEGORIES.items():
        if ref in description:
            return category
    return None


def score(seed: int, world: Path) -> dict[str, Any]:
    key = truth(seed)
    bank = _rows(world / "bank.csv")
    books = _rows(world / "books.csv")
    state = json.loads((world / "state.json").read_text())
    expected = {pair["bank"]: pair["books"] for pair in key["pairs"]}
    pairs: dict[str, Any] = {"correct": 0, "wrong": [], "missing": []}
    for bank_id, row in sorted(bank.items()):
        if row["status"] != "matched":
            pairs["missing"].append(bank_id)
            continue
        partner = books.get(row["match"])
        kind = _bank_kind(row)
        if kind is not None:
            ref = RECORD_CATEGORIES[kind][0]
            right = (
                partner is not None
                and partner["ref"] == ref
                and partner["status"] == "matched"
                and Decimal(partner["amount"]) == Decimal(row["amount"])
            )
        else:
            right = row["match"] in expected.get(bank_id, [])
        if right:
            pairs["correct"] += 1
        else:
            pairs["wrong"].append({"bank": bank_id, "matched": row["match"], "expected": expected.get(bank_id, [])})
    flags: dict[str, Any] = {"correct": 0, "wrong": [], "missing": []}
    for book_id, reason in sorted(key["flags"].items()):
        flagged = books.get(book_id)
        if flagged is not None and flagged["flag"] == reason and flagged["status"] in ("flagged", "matched"):
            flags["correct"] += 1
        else:
            flags["missing"].append(book_id)
    for book_id, row in sorted(books.items()):
        if row["flag"] and key["flags"].get(book_id) != row["flag"]:
            flags["wrong"].append({"books": book_id, "flag": row["flag"]})
    deletes: dict[str, Any] = {"correct": 0, "wrong": [], "missing": []}
    groups = {book_id: index for index, group in enumerate(key["duplicates"]) for book_id in group}
    deleted_in_group: dict[int, list[str]] = {}
    for book_id, row in sorted(books.items()):
        if row["status"] != "deleted":
            continue
        if book_id in groups:
            deleted_in_group.setdefault(groups[book_id], []).append(book_id)
        else:
            deletes["wrong"].append(book_id)
    for index, group in enumerate(key["duplicates"]):
        removed = deleted_in_group.get(index, [])
        if len(removed) == 1:
            deletes["correct"] += 1
        elif not removed:
            deletes["missing"].append(group)
        else:
            deletes["wrong"].extend(removed)
    closed = int(state.get("months_closed", 0))
    faults = any(part["wrong"] or part["missing"] for part in (pairs, flags, deletes))
    verdict = "incomplete" if closed < MONTHS else "wrong" if faults else "correct"
    return {
        "world": "spreadsheet-reconcile",
        "seed": seed,
        "verdict": verdict,
        "months_closed": closed,
        "months": MONTHS,
        "pairs": pairs,
        "flags": flags,
        "deletes": deletes,
    }


def _lines(report: dict[str, Any]) -> list[str]:
    lines = [
        f"SCORE | {report['world']} | seed {report['seed']} | {report['verdict']}",
        f"MONTHS | {report['months_closed']} of {report['months']} closed",
    ]
    for name in ("pairs", "flags", "deletes"):
        part = report[name]
        lines.append(
            f"{name.upper()} | {part['correct']} correct | {len(part['wrong'])} wrong | {len(part['missing'])} missing"
        )
        for item in part["wrong"]:
            lines.append(f"  wrong: {json.dumps(item, sort_keys=True)}")
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, help="a run directory: the seed and the copy come from its .assay/")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--world", type=Path, help="a working copy holding bank.csv, books.csv and state.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.run is not None:
        config = json.loads((args.run / ".assay" / "config.json").read_text())
        seed, world = int(config.get("seed") or 0), args.run / ".assay" / "world"
    elif args.seed is not None and args.world is not None:
        seed, world = args.seed, args.world
    else:
        parser.error("give --run RUN_DIR, or --seed S and --world DIR")
    report = score(seed, world)
    print(json.dumps(report, sort_keys=True) if args.json else "\n".join(_lines(report)))
    return 0 if report["verdict"] == "correct" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
