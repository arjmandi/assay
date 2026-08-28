"""Generates the synthetic fixtures under verify/fixtures/*/ from readable
code rather than hand-edited JSON blobs, so exactly what is broken about
each one is legible in a diff, not archaeology.

One baseline timeline (a tiny "general"/dict-observation run, in the shape
of examples/counter_world.py) is shared by every fixture; each broken
fixture applies exactly one mutation to it. This is a generator, not a test
— it is run once to materialize the committed fixture files; the actual
regression tests in tests/test_assay_verify.py read the already-committed
output and never call this module.

Deliberately stdlib-only, matching verify/assay_verify.py's own constraint,
even though (unlike that file) nothing stops this generator from importing
the kernel — keeping it dependency-free just means a third party can also
read this file to see exactly how the fixtures were built.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CHAIN_SEED = "assay-chain-v1"


def _line(event: dict[str, Any]) -> str:
    return json.dumps(event, separators=(",", ":"), sort_keys=True)


def _advance(head: str, line: str) -> str:
    return hashlib.sha256(head.encode() + line.encode()).hexdigest()


def _chain(lines: list[str]) -> tuple[int, str]:
    head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
    last = -1
    for line in lines:
        head = _advance(head, line)
        last += 1
    return last, head


def _event(
    event_id: int,
    action: str,
    *,
    data: dict[str, Any] | None,
    note: str,
    state: str,
    levels_completed: int,
    level_before: int | None,
    available_actions: list[str],
    counter: int,
    predict: str | None = None,
    predict_ok: bool | None = None,
    grade: list[dict[str, Any]] | None = None,
    mutation_id: int | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": event_id,
        "timestamp": f"2026-01-01T00:00:{event_id:02d}+00:00",
        "action": action,
        "data": data,
        "note": note,
        "state": state,
        "levels_completed": levels_completed,
        "level_before": level_before,
        "win_levels": 1,
        "available_actions": available_actions,
        "counts_action": action != "START",
        "observation": {"counter": counter},
    }
    if predict is not None:
        event["predict"] = predict
        event["predict_ok"] = predict_ok
        event["grade"] = grade
    if mutation_id is not None:
        event["mutation_id"] = mutation_id
    return event


_ACTIONS = ["INC", "NOOP"]


def _baseline_events() -> list[dict[str, Any]]:
    """4 events: START, a predicted-correctly INC, a missed NOOP, a WIN."""
    return [
        _event(
            0,
            "START",
            data=None,
            note="",
            state="NOT_FINISHED",
            levels_completed=0,
            level_before=None,
            available_actions=_ACTIONS,
            counter=0,
        ),
        _event(
            1,
            "INC",
            data={"amount": 1},
            note="probe INC",
            state="NOT_FINISHED",
            levels_completed=0,
            level_before=0,
            available_actions=_ACTIONS,
            counter=1,
            predict="change",
            predict_ok=True,
            grade=[
                {
                    "kind": "change",
                    "text": "change",
                    "ok": True,
                    "actual": "1 keys changed",
                    "bucket": "world_model",
                }
            ],
            mutation_id=0,
        ),
        _event(
            2,
            "NOOP",
            data=None,
            note="probe NOOP",
            state="NOT_FINISHED",
            levels_completed=0,
            level_before=0,
            available_actions=_ACTIONS,
            counter=1,
            predict="change",
            predict_ok=False,
            grade=[
                {
                    "kind": "change",
                    "text": "change",
                    "ok": False,
                    "actual": "no observed change (0 keys)",
                    "bucket": "world_model",
                }
            ],
            mutation_id=1,
        ),
        _event(
            3,
            "INC",
            data={"amount": 2},
            note="going for it",
            state="WIN",
            levels_completed=1,
            level_before=0,
            available_actions=_ACTIONS,
            counter=3,
            predict="win",
            predict_ok=True,
            grade=[
                {
                    "kind": "win",
                    "text": "win",
                    "ok": True,
                    "actual": "state WIN",
                    "bucket": "gamble",
                }
            ],
            mutation_id=2,
        ),
    ]


def _config() -> dict[str, Any]:
    return {
        "game_id": "counterdemo",
        "seed": None,
        "mode": "local",
        "adapter": "examples/counter_world.py:factory",
        "registry": True,
        "created_at": "2026-01-01T00:00:00+00:00",
        "harness": "assay",
    }


def _write_run(
    case_dir: Path,
    events: list[dict[str, Any]],
    *,
    chain_override: tuple[int, str] | None = None,
    anchor: dict[str, Any] | None = None,
) -> None:
    run_dir = case_dir / "run"
    state_dir = run_dir / ".assay"
    state_dir.mkdir(parents=True, exist_ok=True)
    lines = [_line(event) for event in events]
    (state_dir / "events.jsonl").write_text("\n".join(lines) + "\n" if lines else "")
    (state_dir / "config.json").write_text(json.dumps(_config(), indent=2, sort_keys=True) + "\n")
    if chain_override is None:
        last_id, head = _chain(lines)
    else:
        last_id, head = chain_override
    (state_dir / "chain.json").write_text(
        json.dumps({"event_id": last_id, "head": head}, sort_keys=True) + "\n"
    )
    if anchor is not None:
        (case_dir / "anchors.jsonl").write_text(_line(anchor) + "\n")


def _write_expected(
    case_dir: Path,
    *,
    contiguous: bool,
    chain: str,
    anchors: str,
    ungated: list[int],
    predict_grade_mismatches: list[int],
    invalid_for_scoring: bool,
    note: str,
) -> None:
    expected = {
        "note": note,
        "contiguous": contiguous,
        "chain": chain,
        "anchors": anchors,
        "ungated": ungated,
        "predict_grade_mismatches": predict_grade_mismatches,
        "invalid_for_scoring": invalid_for_scoring,
    }
    (case_dir / "expected.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")


def build_all(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)

    # -- passing: the baseline, untouched, plus a matching on-WIN anchor. ---
    events = _baseline_events()
    lines = [_line(event) for event in events]
    _, head_at_3 = _chain(lines)
    passing_dir = root / "passing"
    _write_run(
        passing_dir,
        events,
        anchor={"event_id": 3, "head": head_at_3, "run": "synthetic"},
    )
    _write_expected(
        passing_dir,
        contiguous=True,
        chain="intact",
        anchors="intact",
        ungated=[],
        predict_grade_mismatches=[],
        invalid_for_scoring=False,
        note="a clean run: one correct prediction, one honest miss, one win; "
        "CLEAN does not mean the agent was always right, it means the "
        "artifacts are tamper-evident and every paid action went through "
        "the gate",
    )

    # -- broken-contiguity: event 1's id is wrong; nothing else changes. ----
    events = _baseline_events()
    events[1]["id"] = 2
    case_dir = root / "broken-contiguity"
    _write_run(case_dir, events)
    _write_expected(
        case_dir,
        contiguous=False,
        chain="intact",
        anchors="none",
        ungated=[],
        predict_grade_mismatches=[],
        invalid_for_scoring=True,
        note="event at line 1 carries id 2 instead of 1 — a deletion or "
        "reorder in events.jsonl; the chain still recomputes fine (it "
        "doesn't look at id), which is why contiguity is checked "
        "separately from the chain",
    )

    # -- broken-chain: an event's content changed after chain.json was ------
    #    computed for the original content; chain.json is NOT updated.
    events = _baseline_events()
    original_lines = [_line(event) for event in events]
    _, honest_head = _chain(original_lines)
    events[2]["note"] = "tampered after the fact"
    case_dir = root / "broken-chain"
    _write_run(case_dir, events, chain_override=(3, honest_head))
    _write_expected(
        case_dir,
        contiguous=True,
        chain="DIVERGED",
        anchors="none",
        ungated=[],
        predict_grade_mismatches=[],
        invalid_for_scoring=True,
        note="event 2's note was edited after chain.json was last written; "
        "chain.json still holds the head computed from the ORIGINAL "
        "content, so recomputing from the current events.jsonl diverges "
        "from it",
    )

    # -- broken-anchor: local files are self-consistent (chain.json was -----
    #    regenerated to match the tampered content), but an anchor recorded
    #    before the tamper no longer matches — the scenario anchors exist
    #    to catch: an attacker who controls the run directory can rewrite
    #    events.jsonl AND chain.json together, but not an anchor filed
    #    elsewhere beforehand.
    events = _baseline_events()
    original_lines = [_line(event) for event in events]
    _, anchor_head_at_1 = _chain(original_lines[:2])  # honest head after event 1
    events[1]["data"] = {"amount": 99}  # tamper event 1 after that anchor point
    case_dir = root / "broken-anchor"
    _write_run(
        case_dir,
        events,
        anchor={"event_id": 1, "head": anchor_head_at_1, "run": "synthetic"},
    )
    _write_expected(
        case_dir,
        contiguous=True,
        chain="intact",
        anchors="DIVERGED",
        ungated=[],
        predict_grade_mismatches=[],
        invalid_for_scoring=True,
        note="event 1's data was edited after an anchor recorded its "
        "original head at event_id 1; chain.json was regenerated to match "
        "the new content (so the LOCAL chain looks intact), but the "
        "externally anchored head — outside the run directory — still "
        "reflects the original, unedited line",
    )

    # -- broken-gate: event 2 (a paid, non-RESET action) carries none of ----
    #    predict/predict_ok/grade — a gate bypass.
    events = _baseline_events()
    del events[2]["predict"]
    del events[2]["predict_ok"]
    del events[2]["grade"]
    case_dir = root / "broken-gate"
    _write_run(case_dir, events)
    _write_expected(
        case_dir,
        contiguous=True,
        chain="intact",
        anchors="none",
        ungated=[2],
        predict_grade_mismatches=[],
        invalid_for_scoring=True,
        note="event 2 spent a paid NOOP action with no predict, predict_ok, "
        "or grade at all — as if some code path reached the world without "
        "going through predict-before-act; this is the pre-registered "
        "bypass definition, and it invalidates everything after it too",
    )

    # -- broken-predict-grade: predict_ok contradicts its own grade array. --
    events = _baseline_events()
    events[1]["predict_ok"] = False  # grade[0]["ok"] is still True underneath
    case_dir = root / "broken-predict-grade"
    _write_run(case_dir, events)
    _write_expected(
        case_dir,
        contiguous=True,
        chain="intact",
        anchors="none",
        ungated=[],
        predict_grade_mismatches=[1],
        invalid_for_scoring=True,
        note="event 1's predict_ok was flipped to False, but its own grade "
        "array still shows a passing claim (ok: true) — the two fields "
        "were not produced together by one consistent process",
    )


if __name__ == "__main__":
    import sys

    build_all(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent)
