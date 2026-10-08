"""Verifier claims: admission, sandboxed execution, INVALID_CLAIM, telemetry."""

from __future__ import annotations

import json

import pytest

from conftest import event_of, run_of

from assay.core import AssayError, atomic_json
from assay.records import Claim, Grade
from assay.verifiers import (
    RULE_IDENTITY,
    RULE_NEVER_FAILED,
    admit_verifier,
    grade_verifier_claim,
    load_stats,
    never_failed_hashes,
    observation_view,
    run_verifier,
    stats_entries,
    stats_rule,
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

BEFORE_ONLY = """\
def verify(before, after):
    return before["data"]["counter"] == 0, "reads before only"
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
    return admit_verifier(paths, Claim(kind="verify", text=f"verify:{name}", path=name))


def test_admission_stores_hash_copy_and_journals(paths):
    claim = _admitted(paths, COUNTER_UP)
    digest = claim.verifier_hash
    stored = paths.verifiers / f"{digest}.py"
    assert stored.read_text() == COUNTER_UP
    activity = paths.activity.read_text()
    assert "verifier_admitted" in activity and digest in activity


def test_admission_refuses_missing_and_escaping_paths(paths):
    with pytest.raises(AssayError, match="not found"):
        admit_verifier(paths, Claim(kind="verify", text="verify:nope.py", path="nope.py"))
    with pytest.raises(AssayError, match="relative"):
        admit_verifier(paths, Claim(kind="verify", text="verify:/etc/passwd", path="/etc/passwd"))
    with pytest.raises(AssayError, match="inside the run directory"):
        admit_verifier(paths, Claim(kind="verify", text="verify:../outside.py", path="../outside.py"))


def test_happy_path_real_subprocess(paths):
    claim = _admitted(paths, COUNTER_UP)
    graded = grade_verifier_claim(paths, claim, BEFORE, AFTER)
    assert graded.ok is True
    assert graded.verifier is True
    assert graded.actual == "counter 0 -> 1"
    assert graded.identity_verdict is False  # discriminates against identity
    assert graded.invalid is False and "invalid" not in graded.to_json()
    stats = load_stats(paths)
    assert stats["rule"] == RULE_IDENTITY
    assert stats["verifiers"][claim.verifier_hash] == {
        "graded": 1,
        "passed": 1,
        "failed": 0,
        "invalid": 0,
        "identity_same_verdict": 0,
    }


def test_miss_reports_counter_fact(paths):
    claim = _admitted(paths, COUNTER_UP)
    graded = grade_verifier_claim(paths, claim, BEFORE, BEFORE)
    assert graded.ok is False
    assert graded.actual == "counter 0 -> 0"
    stats = stats_entries(load_stats(paths))[claim.verifier_hash]
    assert stats["failed"] == 1
    # identity probe returned the same verdict as the grading (both False)
    assert stats["identity_same_verdict"] == 1


def test_crash_is_invalid_claim(paths):
    claim = _admitted(paths, CRASHER)
    graded = grade_verifier_claim(paths, claim, BEFORE, AFTER)
    assert graded.invalid is True
    assert graded.ok is False
    assert graded.actual.startswith("INVALID_CLAIM:")
    assert "crashed" in graded.actual
    stats = stats_entries(load_stats(paths))[claim.verifier_hash]
    assert stats["invalid"] == 1 and stats["graded"] == 0


def test_timeout_is_invalid_claim(paths):
    claim = _admitted(paths, SLEEPER)
    graded = grade_verifier_claim(paths, claim, BEFORE, AFTER, timeout=1.5)
    assert graded.invalid is True
    assert "timed out" in graded.actual


def test_malformed_output_is_invalid_claim(paths):
    claim = _admitted(paths, BAD_SHAPE)
    graded = grade_verifier_claim(paths, claim, BEFORE, AFTER)
    assert graded.invalid is True


def test_constant_verifier_is_vacuous_after_five_gradings(paths):
    claim = _admitted(paths, ALWAYS_TRUE)
    records = [
        grade_verifier_claim(paths, claim, BEFORE, AFTER) for _ in range(5)
    ]
    assert all(item.ok for item in records)
    # identity verdict equals the real verdict every time: zero discrimination
    stats = load_stats(paths)
    entry = stats_entries(stats)[claim.verifier_hash]
    assert entry["graded"] == 5 and entry["identity_same_verdict"] == 5
    assert vacuous_hashes(stats) == {claim.verifier_hash}
    assert never_failed_hashes(stats) == set()  # flagged, so not an advisory
    assert records[4].excluded_from_meter is True
    assert records[0].excluded_from_meter is False
    assert "excluded_from_meter" not in records[0].to_json()


def test_verifier_that_ignores_the_transition_is_vacuous_despite_a_failure(paths):
    claim = _admitted(paths, BEFORE_ONLY)
    missed = grade_verifier_claim(paths, claim, AFTER, AFTER)
    assert missed.ok is False and missed.identity_verdict is False
    records = [
        grade_verifier_claim(paths, claim, BEFORE, AFTER) for _ in range(4)
    ]
    assert all(item.ok and item.identity_verdict is True for item in records)
    stats = load_stats(paths)
    entry = stats_entries(stats)[claim.verifier_hash]
    assert entry["graded"] == 5 and entry["failed"] == 1
    assert entry["identity_same_verdict"] == 5
    # one deliberate failure no longer buys a way out of the flag
    assert vacuous_hashes(stats) == {claim.verifier_hash}
    assert records[3].excluded_from_meter is True
    assert records[2].excluded_from_meter is False


def test_discriminating_verifier_that_holds_is_an_advisory_not_a_flag(paths):
    claim = _admitted(paths, COUNTER_UP)
    records = [
        grade_verifier_claim(paths, claim, BEFORE, AFTER) for _ in range(5)
    ]
    assert all(item.ok and item.identity_verdict is False for item in records)
    assert not any(item.excluded_from_meter for item in records)
    stats = load_stats(paths)
    entry = stats_entries(stats)[claim.verifier_hash]
    assert entry["graded"] == 5 and entry["failed"] == 0
    assert entry["identity_same_verdict"] == 0
    assert vacuous_hashes(stats) == set()
    assert never_failed_hashes(stats) == {claim.verifier_hash}


OLD_RULE_STATS = {
    "a" * 64: {
        "graded": 5, "passed": 5, "failed": 0, "invalid": 0, "identity_same_verdict": 0
    },
    "b" * 64: {
        "graded": 6, "passed": 5, "failed": 1, "invalid": 0, "identity_same_verdict": 6
    },
}


def test_stats_file_recorded_before_the_marker_keeps_the_never_failed_rule(paths):
    atomic_json(paths.verifier_stats, OLD_RULE_STATS)
    stats = load_stats(paths)
    assert stats_rule(stats) == RULE_NEVER_FAILED
    assert stats_entries(stats) == OLD_RULE_STATS
    assert vacuous_hashes(stats) == {"a" * 64}  # never failed: the old flag
    assert never_failed_hashes(stats) == set()  # the advisory is the new rule's
    # a grading on such a run keeps the flat file and the old rule
    claim = _admitted(paths, BEFORE_ONLY)
    grade_verifier_claim(paths, claim, AFTER, AFTER)
    records = [
        grade_verifier_claim(paths, claim, BEFORE, AFTER) for _ in range(4)
    ]
    stats = load_stats(paths)
    assert "rule" not in stats and "verifiers" not in stats
    assert stats_rule(stats) == RULE_NEVER_FAILED
    entry = stats[claim.verifier_hash]
    assert entry["graded"] == 5 and entry["failed"] == 1
    assert entry["identity_same_verdict"] == 5
    assert claim.verifier_hash not in vacuous_hashes(stats)  # it failed once
    assert not any(item.excluded_from_meter for item in records)


def test_status_lines_follow_the_rule_of_the_stats_file(paths):
    from assay.inspect import _claim_meter_lines

    vacuous, held = "a" * 64, "b" * 64
    events = [
        event_of(
            grade=[
                Grade(kind="verify", text="verify:a.py", ok=True, actual="", bucket="world_model",
                      verifier=True, verifier_hash=vacuous),
                Grade(kind="verify", text="verify:b.py", ok=True, actual="", bucket="world_model",
                      verifier=True, verifier_hash=held),
            ]
        )
    ]
    run = run_of(paths, events)
    counters = {
        vacuous: {
            "graded": 5, "passed": 5, "failed": 0, "invalid": 0, "identity_same_verdict": 5
        },
        held: {
            "graded": 5, "passed": 5, "failed": 0, "invalid": 0, "identity_same_verdict": 0
        },
    }
    atomic_json(paths.verifier_stats, {"rule": "identity", "verifiers": counters})
    assert _claim_meter_lines(run) == [
        "CLAIMS | world-model misses 0/1 (0.0%) | gamble misses 0/0 | "
        "specificity 2/2 (100%) | invalid 0",
        f"VACUOUS | verifier {vacuous[:12]} graded 5, identity verdict matched the "
        "real verdict every time; it does not use the transition, its passes are "
        "excluded from the meter",
        f"VERIFIER | {held[:12]} graded 5, never failed (advisory, not a flag)",
    ]
    # The same counters in a file recorded under the never-failed rule: both
    # flagged by the old line, both excluded, no advisory. The replay gate
    # holds that line against the campaign kernel byte for byte, through the
    # vocabulary map's one entry for it (the colon where the em dash was).
    atomic_json(paths.verifier_stats, counters)
    lines = _claim_meter_lines(run)
    assert lines == [
        "CLAIMS | world-model misses 0/0 | gamble misses 0/0 | "
        "specificity 2/2 (100%) | invalid 0",
        f"VACUOUS | verifier {vacuous[:12]} graded 5 failed 0: a verifier that "
        "never fails proves nothing; its passes are excluded from the meter",
        f"VACUOUS | verifier {held[:12]} graded 5 failed 0: a verifier that "
        "never fails proves nothing; its passes are excluded from the meter",
    ]


def test_run_verifier_rejects_missing_store(paths):
    result = run_verifier(paths, "0" * 64, BEFORE, AFTER)
    assert result["status"] == "invalid"


def test_observation_view_shapes():
    general_event = event_of(available_actions=["NOOP"], observation={"counter": 2})
    view = observation_view(general_event)
    assert view["data"] == {"counter": 2}
    assert "frames" not in view
    grid_event = event_of(available_actions=[1, 2], frames=[["00", "00"]])
    view = observation_view(grid_event)
    assert view["frames"] == [["00", "00"]]
    assert "data" not in view


def test_first_grading_writes_the_identity_marker(paths):
    assert stats_rule(load_stats(paths)) == RULE_IDENTITY  # nothing graded yet
    claim = _admitted(paths, CRASHER)
    grade_verifier_claim(paths, claim, BEFORE, AFTER)  # an invalid one counts
    parsed = json.loads(paths.verifier_stats.read_text())
    assert parsed == {
        "rule": "identity",
        "verifiers": {
            claim.verifier_hash: {
                "graded": 0,
                "passed": 0,
                "failed": 0,
                "invalid": 1,
                "identity_same_verdict": 0,
            }
        },
    }
