"""Claim parsing and grading: kinds, buckets, coercion, the general grader."""

from __future__ import annotations

import pytest

from assay.core import AssayError
from assay.predictions import (
    claim_bucket,
    grade_action_claims,
    grade_general_claims,
    parse_claims,
)

START = {
    "id": 0,
    "action": "START",
    "state": "NOT_FINISHED",
    "levels_completed": 0,
    "level_before": None,
    "win_levels": 1,
    "available_actions": ["INC", "NOOP"],
    "observation": {"counter": 0, "lamp": "off"},
    "counts_action": False,
}


def _event(observation, state="NOT_FINISHED", levels=0, level_before=0):
    return {
        "id": 1,
        "action": "INC",
        "data": {"amount": 1},
        "state": state,
        "levels_completed": levels,
        "level_before": level_before,
        "win_levels": 1,
        "available_actions": ["INC", "NOOP"],
        "observation": observation,
        "counts_action": True,
    }


# --- parsing ----------------------------------------------------------------


def test_free_text_is_coerced_change():
    claims = parse_claims("the door probably opens")
    assert claims[0] == {"kind": "note", "text": "the door probably opens"}
    assert claims[1]["kind"] == "change" and claims[1]["coerced"] is True


def test_verify_claim_parses_path():
    claims = parse_claims("verify:checks/foo.py; change")
    assert claims[0] == {
        "kind": "verify",
        "text": "verify:checks/foo.py",
        "path": "checks/foo.py",
    }
    assert claims[1]["kind"] == "change"


def test_malformed_verify_refused():
    with pytest.raises(AssayError, match="malformed claim"):
        parse_claims("verify checks/foo.py")


def test_general_mode_refuses_grid_claims():
    with pytest.raises(AssayError, match="needs a grid observation"):
        parse_claims("cell 1,2=3", general=True)
    with pytest.raises(AssayError, match="needs a grid observation"):
        parse_claims("move 1,2 0,1; change", general=True)
    # the general forms still parse
    kinds = [claim["kind"] for claim in parse_claims("noop", general=True)]
    assert kinds == ["noop"]


def test_buckets():
    assert claim_bucket("win") == "gamble"
    assert claim_bucket("level_up") == "gamble"
    assert claim_bucket("change") == "world_model"
    assert claim_bucket("verify") == "world_model"


# --- general grading --------------------------------------------------------


def test_grade_general_noop_and_change():
    changed = _event({"counter": 1, "lamp": "off"})
    unchanged = _event({"counter": 0, "lamp": "off"})
    noop = parse_claims("noop", general=True)
    change = parse_claims("change", general=True)
    assert grade_general_claims(noop, START, unchanged)[0]["ok"] is True
    graded = grade_general_claims(noop, START, changed)[0]
    assert graded["ok"] is False and "1 keys changed" in graded["actual"]
    assert grade_general_claims(change, START, changed)[0]["ok"] is True
    graded = grade_general_claims(change, START, unchanged)[0]
    assert graded["ok"] is False and "no observed change" in graded["actual"]


def test_grade_general_win_and_level():
    won = _event({"counter": 3, "lamp": "off"}, state="WIN", levels=1)
    graded = grade_general_claims(parse_claims("win", general=True), START, won)[0]
    assert graded["ok"] is True and graded["actual"] == "state WIN"
    graded = grade_general_claims(parse_claims("level+1", general=True), START, won)[0]
    assert graded["ok"] is True
    lost = _event({"counter": 1, "lamp": "off"})
    graded = grade_general_claims(parse_claims("win", general=True), START, lost)[0]
    assert graded["ok"] is False and graded["actual"] == "state NOT_FINISHED"


# --- journaled record fields (P4) -------------------------------------------


def test_graded_records_carry_kind_and_bucket(paths):
    after = _event({"counter": 1, "lamp": "off"})
    claims = parse_claims("counter goes up somehow", general=True)  # coerced
    graded = grade_action_claims(paths, claims, START, after)
    assert len(graded) == 1
    record = graded[0]
    assert record["kind"] == "coerced"
    assert record["bucket"] == "world_model"
    assert record["ok"] is True


def test_graded_records_sharp_and_gamble(paths):
    won = _event({"counter": 3, "lamp": "off"}, state="WIN", levels=1)
    graded = grade_action_claims(
        paths, parse_claims("change; win", general=True), START, won
    )
    by_kind = {record["kind"]: record for record in graded}
    assert by_kind["change"]["bucket"] == "world_model"
    assert by_kind["win"]["bucket"] == "gamble"
    assert all(record["ok"] for record in graded)


def test_graded_verify_record_flags(paths):
    body = (
        "def verify(before, after):\n"
        "    return after['data']['counter'] == before['data']['counter'] + 1, "
        "f\"counter {before['data']['counter']} -> {after['data']['counter']}\"\n"
    )
    (paths.root / "check.py").write_text(body)
    from assay.verifiers import admit_verifier

    claims = parse_claims("verify:check.py", general=True)
    admit_verifier(paths, claims[0])
    after = _event({"counter": 1, "lamp": "off"})
    graded = grade_action_claims(paths, claims, START, after)
    record = graded[0]
    assert record["kind"] == "verify"
    assert record["bucket"] == "world_model"
    assert record["verifier"] is True
    assert record["verifier_hash"]
    assert record["ok"] is True
