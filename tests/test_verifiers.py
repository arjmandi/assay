"""Verifier claims: admission, sandboxed execution, INVALID_CLAIM, telemetry."""

from __future__ import annotations

import json

import pytest

from assay.core import AssayError
from assay.verifiers import (
    admit_verifier,
    grade_verifier_claim,
    load_stats,
    observation_view,
    run_verifier,
    vacuous_hashes,
)

BEFORE = {
    "state": "NOT_FINISHED",
    "levels_completed": 0,
    "win_levels": 1,
    "available_actions": ["INC", "NOOP"],
    "data": {"counter": 0, "lamp": "off"},
}
AFTER = {**BEFORE, "data": {"counter": 1, "lamp": "off"}}

COUNTER_UP = """\
def verify(before, after):
    b = before["data"]["counter"]
    a = after["data"]["counter"]
    return a == b + 1, f"counter {b} -> {a}"
"""

ALWAYS_TRUE = """\
def verify(before, after):
    return True, "always fine"
"""

CRASHER = """\
def verify(before, after):
    raise RuntimeError("boom")
"""

SLEEPER = """\
import time

def verify(before, after):
    time.sleep(30)
    return True, "never reached"
"""

BAD_SHAPE = """\
def verify(before, after):
    return True
"""


def _admitted(paths, body, name="check.py"):
    source = paths.root / name
    source.write_text(body)
    claim = {"kind": "verify", "text": f"verify:{name}", "path": name}
    admit_verifier(paths, claim)
    return claim


def test_admission_stores_hash_copy_and_journals(paths):
    claim = _admitted(paths, COUNTER_UP)
    digest = claim["verifier_hash"]
    stored = paths.verifiers / f"{digest}.py"
    assert stored.read_text() == COUNTER_UP
    activity = paths.activity.read_text()
    assert "verifier_admitted" in activity and digest in activity


def test_admission_refuses_missing_and_escaping_paths(paths):
    with pytest.raises(AssayError, match="not found"):
        admit_verifier(paths, {"kind": "verify", "path": "nope.py"})
    with pytest.raises(AssayError, match="relative"):
        admit_verifier(paths, {"kind": "verify", "path": "/etc/passwd"})
    with pytest.raises(AssayError, match="inside the run directory"):
        admit_verifier(paths, {"kind": "verify", "path": "../outside.py"})


def test_happy_path_real_subprocess(paths):
    claim = _admitted(paths, COUNTER_UP)
    graded = grade_verifier_claim(paths, claim, BEFORE, AFTER)
    assert graded["ok"] is True
    assert graded["verifier"] is True
    assert graded["actual"] == "counter 0 -> 1"
    assert graded["identity_verdict"] is False  # discriminates against identity
    assert "invalid" not in graded
    stats = load_stats(paths)[claim["verifier_hash"]]
    assert stats == {
        "graded": 1,
        "passed": 1,
        "failed": 0,
        "invalid": 0,
        "identity_same_verdict": 0,
    }


def test_miss_reports_counter_fact(paths):
    claim = _admitted(paths, COUNTER_UP)
    graded = grade_verifier_claim(paths, claim, BEFORE, BEFORE)
    assert graded["ok"] is False
    assert graded["actual"] == "counter 0 -> 0"
    stats = load_stats(paths)[claim["verifier_hash"]]
    assert stats["failed"] == 1
    # identity probe returned the same verdict as the grading (both False)
    assert stats["identity_same_verdict"] == 1


def test_crash_is_invalid_claim(paths):
    claim = _admitted(paths, CRASHER)
    graded = grade_verifier_claim(paths, claim, BEFORE, AFTER)
    assert graded["invalid"] is True
    assert graded["ok"] is False
    assert graded["actual"].startswith("INVALID_CLAIM:")
    assert "crashed" in graded["actual"]
    stats = load_stats(paths)[claim["verifier_hash"]]
    assert stats["invalid"] == 1 and stats["graded"] == 0


def test_timeout_is_invalid_claim(paths):
    claim = _admitted(paths, SLEEPER)
    graded = grade_verifier_claim(paths, claim, BEFORE, AFTER, timeout=1.5)
    assert graded["invalid"] is True
    assert "timed out" in graded["actual"]


def test_malformed_output_is_invalid_claim(paths):
    claim = _admitted(paths, BAD_SHAPE)
    graded = grade_verifier_claim(paths, claim, BEFORE, AFTER)
    assert graded["invalid"] is True


def test_vacuous_flagging_after_five_failless_gradings(paths):
    claim = _admitted(paths, ALWAYS_TRUE)
    records = [
        grade_verifier_claim(paths, claim, BEFORE, AFTER) for _ in range(5)
    ]
    assert all(item["ok"] for item in records)
    # identity verdict equals the actual verdict every time: zero discrimination
    stats = load_stats(paths)[claim["verifier_hash"]]
    assert stats["identity_same_verdict"] == 5
    assert vacuous_hashes(load_stats(paths)) == {claim["verifier_hash"]}
    assert records[4].get("excluded_from_meter") is True
    assert "excluded_from_meter" not in records[0]


def test_run_verifier_rejects_missing_store(paths):
    result = run_verifier(paths, "0" * 64, BEFORE, AFTER)
    assert result["status"] == "invalid"


def test_observation_view_shapes():
    general_event = {
        "state": "NOT_FINISHED",
        "levels_completed": 0,
        "win_levels": 1,
        "available_actions": ["NOOP"],
        "observation": {"counter": 2},
    }
    view = observation_view(general_event)
    assert view["data"] == {"counter": 2}
    assert "frames" not in view
    grid_event = {
        "state": "NOT_FINISHED",
        "levels_completed": 0,
        "win_levels": 1,
        "available_actions": [1, 2],
        "frames": [["00", "00"]],
    }
    view = observation_view(grid_event)
    assert view["frames"] == [["00", "00"]]
    assert "data" not in view


def test_stats_file_is_json(paths):
    claim = _admitted(paths, COUNTER_UP)
    grade_verifier_claim(paths, claim, BEFORE, AFTER)
    parsed = json.loads(paths.verifier_stats.read_text())
    assert claim["verifier_hash"] in parsed
