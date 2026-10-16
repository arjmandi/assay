"""The published journals under evidence/ verify against their committed heads
with the kernel's own chain and ungated rules (evidence/verify_all.py, gate
G3), and the gate notices an edited, a deleted or a reordered line, and a
bare paid event injected with its chain recomputed."""

from __future__ import annotations

import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "evidence"

_spec = importlib.util.spec_from_file_location("evidence_verify_all", EVIDENCE / "verify_all.py")
verify_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_all)


def test_every_published_journal_verifies_against_its_head():
    completed = subprocess.run(
        [sys.executable, str(EVIDENCE / "verify_all.py")], capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "| 66 journals, 0 failed" in completed.stdout


def _published(name: str) -> tuple[list[str], str]:
    heads = json.loads((EVIDENCE / "factorio" / "heads.json").read_text())
    head = {entry["task"]: entry for entry in heads["runs"]}[name]["chain_head"]
    with gzip.open(EVIDENCE / "factorio" / f"journal-{name}.jsonl.gz", "rt") as source:
        return [line for line in source.read().splitlines() if line.strip()], head


def test_the_gate_notices_every_change_to_a_published_journal():
    lines, head = _published("circuit")
    clean = verify_all.judge(lines, None, head)
    assert (clean["verdict"], clean["head_matches"], clean["ungated"]) == ("CLEAN", True, [])
    edited = [*lines]
    edited[3] = edited[3].replace('"note":', '"note": "x",  "was":', 1)
    deleted = lines[:4] + lines[5:]
    reordered = [lines[0], lines[2], lines[1], *lines[3:]]
    for changed in (edited, deleted, reordered):
        assert verify_all.judge(changed, None, head)["verdict"] == "INVALID FOR SCORING"
    assert verify_all.judge(deleted, None, head)["problems"]
    # A bare paid event, its own head recomputed: the head no longer matches
    # what was published, and the ungated rule names the event on its own.
    bare = json.dumps({"id": len(lines), "timestamp": "2026-08-26T00:00:00+00:00", "action": "RUN", "data": None,
                       "counts_action": True, "state": "WIN", "levels_completed": 4, "level_before": 4,
                       "win_levels": 4, "available_actions": [], "note": "injected", "observation": {}})
    injected = verify_all.judge([*lines, bare], None, verify_all.chain_over([*lines, bare]))
    assert injected["head_matches"] is True and injected["ungated"] == [len(lines)]
    assert injected["verdict"] == "INVALID FOR SCORING"
