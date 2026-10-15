"""The legacy-api checker: a customer hub against the legacy CRM it was meant
to receive. It reads only the hub's file and the seed and imports nothing from
the harness, so a run under ASSAY and a baseline that drove its own copy of
the service are scored alike.

    python3 checks/score.py --run RUN_DIR            the seed from .assay/config.json, the hub in .assay/world_state/hub.json
    python3 checks/score.py --seed S --hub FILE      any hub file
    add --json for one JSON object

The verdict is correct when the hub holds every active customer, with its name
and its email lowercase and trimmed, and nothing else. It is incomplete when
customers are missing and nothing is wrong, and wrong otherwise. The exit
status is 0 for correct and 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legacy_crm import customers, normalized  # noqa: E402


def score(seed: int, hub_file: Path) -> dict[str, Any]:
    legacy = {record["id"]: record for record in customers(seed)}
    active = {ident for ident, record in legacy.items() if record["status"] == "active"}
    hub = {row["id"]: row for row in json.loads(hub_file.read_text()).get("customers", [])}
    archived = sorted(ident for ident in hub if ident in legacy and legacy[ident]["status"] == "archived")
    unknown = sorted(ident for ident in hub if ident not in legacy)
    malformed = sorted(
        ident for ident in hub
        if ident in active and (
            hub[ident].get("name") != legacy[ident]["name"]
            or hub[ident].get("email") != normalized(legacy[ident]["email"])
        )
    )
    missing = sorted(active - set(hub))
    wrong = bool(archived or unknown or malformed)
    verdict = "wrong" if wrong else "incomplete" if missing else "correct"
    return {
        "world": "legacy-api",
        "seed": seed,
        "verdict": verdict,
        "active": len(active),
        "in_hub": len(hub),
        "correct": len(active & set(hub)) - len(malformed),
        "missing": missing,
        "archived": archived,
        "unknown": unknown,
        "malformed": malformed,
    }


def _lines(report: dict[str, Any]) -> list[str]:
    lines = [
        f"SCORE | {report['world']} | seed {report['seed']} | {report['verdict']}",
        f"HUB | {report['in_hub']} customers | {report['correct']} of {report['active']} active customers correct",
    ]
    for key in ("missing", "archived", "unknown", "malformed"):
        if report[key]:
            shown = ", ".join(report[key][:12]) + (" ..." if len(report[key]) > 12 else "")
            lines.append(f"{key.upper()} | {len(report[key])} | {shown}")
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, help="a run directory: the seed and the hub come from its .assay/")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--hub", type=Path, help="a hub file, {\"customers\": [...]}")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.run is not None:
        config = json.loads((args.run / ".assay" / "config.json").read_text())
        seed, hub_file = int(config.get("seed") or 0), args.run / ".assay" / "world_state" / "hub.json"
    elif args.seed is not None and args.hub is not None:
        seed, hub_file = args.seed, args.hub
    else:
        parser.error("give --run RUN_DIR, or --seed S and --hub FILE")
    report = score(seed, hub_file)
    print(json.dumps(report, sort_keys=True) if args.json else "\n".join(_lines(report)))
    return 0 if report["verdict"] == "correct" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
