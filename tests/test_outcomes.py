"""Outcome parsing and grading: kinds, buckets, coercion, the general grader."""

from __future__ import annotations

import pytest

from conftest import event_of, run_of

from assay.core import AssayError
from assay.predictions import (
    outcome_bucket,
    grade_general_outcomes,
    grade_pending,
    parse_prediction,
)
from assay.records import Outcome

START = event_of(
    id=0,
    action="START",
    data=None,
    state="NOT_FINISHED",
    levels_completed=0,
    level_before=None,
    win_levels=1,
    available_actions=["INC", "NOOP"],
    observation={"counter": 0, "lamp": "off"},
    counts_action=False,
)


def _event(observation, state="NOT_FINISHED", levels=0, level_before=0):
    return event_of(
        id=1,
        action="INC",
        data={"amount": 1},
        state=state,
        levels_completed=levels,
        level_before=level_before,
        win_levels=1,
        available_actions=["INC", "NOOP"],
        observation=observation,
        counts_action=True,
    )


# --- parsing ----------------------------------------------------------------


def test_free_text_is_coerced_change():
    outcomes = parse_prediction("the door probably opens")
    assert outcomes[0] == Outcome(kind="note", text="the door probably opens")
    assert outcomes[1].kind == "change" and outcomes[1].coerced is True


def test_verify_outcome_parses_path():
    outcomes = parse_prediction("verify:checks/foo.py; change")
    assert outcomes[0] == Outcome(kind="verify", text="verify:checks/foo.py", path="checks/foo.py")
    assert outcomes[1].kind == "change"


def test_malformed_verify_refused():
    with pytest.raises(AssayError, match="malformed outcome"):
        parse_prediction("verify checks/foo.py")


def test_general_mode_refuses_grid_outcomes():
    # Without an observation kind the frame forms are named and refused.
    with pytest.raises(AssayError, match="does not admit it"):
        parse_prediction("cell 1,2=3")
    with pytest.raises(AssayError, match="does not admit it"):
        parse_prediction("move 1,2 0,1; change")
    # the general forms still parse
    kinds = [outcome.kind for outcome in parse_prediction("noop")]
    assert kinds == ["noop"]


def test_buckets():
    assert outcome_bucket("win") == "gamble"
    assert outcome_bucket("level_up") == "gamble"
    assert outcome_bucket("change") == "world_model"
    assert outcome_bucket("verify") == "world_model"


# --- general grading --------------------------------------------------------


def test_grade_general_noop_and_change():
    changed = _event({"counter": 1, "lamp": "off"})
    unchanged = _event({"counter": 0, "lamp": "off"})
    noop = parse_prediction("noop")
    change = parse_prediction("change")
    assert grade_general_outcomes(noop, START, unchanged)[0].ok is True
    graded = grade_general_outcomes(noop, START, changed)[0]
    assert graded.ok is False and "1 keys changed" in graded.actual
    assert grade_general_outcomes(change, START, changed)[0].ok is True
    graded = grade_general_outcomes(change, START, unchanged)[0]
    assert graded.ok is False and "no observed change" in graded.actual


def test_grade_general_win_and_level():
    won = _event({"counter": 3, "lamp": "off"}, state="WIN", levels=1)
    graded = grade_general_outcomes(parse_prediction("win"), START, won)[0]
    assert graded.ok is True and graded.actual == "state WIN"
    graded = grade_general_outcomes(parse_prediction("level+1"), START, won)[0]
    assert graded.ok is True
    lost = _event({"counter": 1, "lamp": "off"})
    graded = grade_general_outcomes(parse_prediction("win"), START, lost)[0]
    assert graded.ok is False and graded.actual == "state NOT_FINISHED"


# --- journaled record fields (P4) -------------------------------------------


def test_graded_records_carry_kind_and_bucket(paths):
    after = _event({"counter": 1, "lamp": "off"})
    outcomes = parse_prediction("counter goes up somehow")  # coerced
    graded = grade_pending(run_of(paths, [START, after]), outcomes, START, after, elapsed_s=0.0)
    assert len(graded) == 1
    record = graded[0]
    assert record.kind == "coerced"
    assert record.bucket == "world_model"
    assert record.ok is True
    assert record.to_json()["kind"] == "coerced" and record.to_json()["coerced"] is True


def test_graded_records_world_model_and_gamble(paths):
    won = _event({"counter": 3, "lamp": "off"}, state="WIN", levels=1)
    graded = grade_pending(
        run_of(paths, [START, won]), parse_prediction("change; win"), START, won, elapsed_s=0.0
    )
    by_kind = {record.kind: record for record in graded}
    assert by_kind["change"].bucket == "world_model"
    assert by_kind["win"].bucket == "gamble"
    assert all(record.ok for record in graded)


def test_graded_verify_record_flags(paths):
    body = (
        "def verify(before, after):\n"
        "    return after['data']['counter'] == before['data']['counter'] + 1, "
        "f\"counter {before['data']['counter']} -> {after['data']['counter']}\"\n"
    )
    (paths.root / "check.py").write_text(body)
    from assay.verifiers import admit_verifier

    outcomes = [admit_verifier(paths, parse_prediction("verify:check.py")[0])]
    after = _event({"counter": 1, "lamp": "off"})
    graded = grade_pending(run_of(paths, [START, after]), outcomes, START, after, elapsed_s=0.0)
    record = graded[0]
    assert record.kind == "verify"
    assert record.bucket == "world_model"
    assert record.verifier is True
    assert record.verifier_hash
    assert record.ok is True


def test_a_windowed_outcome_is_ungradable_without_a_measured_duration(paths):
    """On recovery the step's duration died with the process (`elapsed_s`
    None): every windowed outcome is UNGRADABLE with the recovery actual, its
    own verdict, while the outcomes without a window grade as usual. A
    measured duration grades the outcome inside its window and marks it late
    outside."""
    after = _event({"counter": 1, "lamp": "off"})
    outcomes = parse_prediction("change @within 2s; level+1")
    run = run_of(paths, [START, after])
    recovered = {grade.kind: grade for grade in grade_pending(run, outcomes, START, after, elapsed_s=None)}
    assert recovered["change"].ungradable is True and recovered["change"].ok is False
    assert recovered["change"].actual == "UNGRADABLE: recovered, step duration unknown"
    assert recovered["level_up"].ungradable is False and recovered["level_up"].ok is False
    timely = {grade.kind: grade for grade in grade_pending(run, outcomes, START, after, elapsed_s=0.5)}
    assert timely["change"].ok is True and timely["change"].ungradable is False
    late = {grade.kind: grade for grade in grade_pending(run, outcomes, START, after, elapsed_s=3.0)}
    assert late["change"].actual == "UNGRADABLE: settled after 3.00s, outside the declared 2s window"
