"""The typed records (docs/ARCHITECTURE.md section 6.2): every published
journal loads through `Event.from_json` and re-serializes to the same bytes,
a wrong type on a known key is refused, a missing required key is a KeyError,
an unknown key rides through `extra`, and the kernel-side constructors keep
the written form of every record."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from conftest import event_of

from assay.records import (
    Outcome,
    Event,
    Grade,
    Mutation,
    Receipt,
    ReceiptStep,
    outcome_bucket,
)

EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"


def _line(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def _published_journals() -> list[Path]:
    journals: list[Path] = []
    for pack in sorted(path for path in EVIDENCE.iterdir() if (path / "heads.json").is_file()):
        for run in json.loads((pack / "heads.json").read_text())["runs"]:
            name = run.get("run") or run.get("game") or run.get("task") or run.get("rung")
            folder = pack / str(name)
            journals.append(
                folder / "journal.jsonl.gz" if folder.is_dir() else pack / f"journal-{name}.jsonl.gz"
            )
    return journals


def test_every_published_journal_round_trips_through_the_event_record():
    journals = _published_journals()
    assert len(journals) == 66
    events = 0
    for journal in journals:
        index = 0
        with gzip.open(journal, "rt") as handle:
            for number, line in enumerate(handle, 1):
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                event = Event.from_json(json.loads(line))
                assert _line(event.to_json()) == line, f"{journal.name} line {number}"
                assert not event.extra, f"{journal.name} line {number} carries an unknown key"
                assert event.id == index, f"{journal.name} line {number}"
                index += 1
        events += index
    assert events == 19251


def test_event_distinguishes_absent_from_null():
    start = event_of(id=0, action="START", counts_action=False, level_before=None)
    assert "predict" not in start.present and start.predict is None and start.grade == ()
    assert "predict" not in start.to_json() and "grade" not in start.to_json()
    bare = start.updated(predict=None, predict_ok=None, grade=())
    assert bare.to_json()["predict"] is None and bare.to_json()["grade"] == []
    assert _line(Event.from_json(bare.to_json()).to_json()) == _line(bare.to_json())
    assert bare.predict is None and start == Event.from_json(start.to_json())
    with pytest.raises(TypeError, match="grade must be a list"):
        Event.from_json({**start.to_json(), "grade": None})


def test_constructor_replace_and_updated_write_the_same_line():
    """An optional field given a value is on the line whichever way the
    event was built (the reviewer's break attempt: a constructed graded
    event whose line read UNGATED)."""
    import dataclasses

    base = {key: value for key, value in event_of(id=3).to_json().items() if key != "observation"}
    graded = Grade(kind="change", text="change", ok=True, actual="1 keys changed", bucket="world_model")
    built = Event(**base, observation={"counter": 1}, predict="change", predict_ok=True,
                  grade=(graded,), mutation_id=3, declares={"revised": "yes"}, gate_optional=True)
    updated = (
        Event(**base)
        .updated(observation={"counter": 1}, predict="change", predict_ok=True, grade=(graded,),
                 mutation_id=3, declares={"revised": "yes"}, gate_optional=True)
    )
    assert _line(built.to_json()) == _line(updated.to_json())
    assert built.present == updated.present
    assert built.ungated is False and Event.from_json(built.to_json()).ungated is False
    replaced = dataclasses.replace(Event(**base), predict_ok=False, grade=(graded,))
    assert replaced.to_json()["predict_ok"] is False and replaced.to_json()["grade"] == [graded.to_json()]
    # An explicit null is not a value: the constructor leaves it off the line,
    # `updated` puts it there.
    assert "predict" not in Event(**base, predict=None).to_json()
    assert Event(**base).updated(predict=None).to_json()["predict"] is None
    # A line the constructor wrote reads back as the same object.
    assert Event.from_json(built.to_json()) == built


def test_event_refuses_a_wrong_type_and_names_the_key():
    good = event_of().to_json()
    for key, bad, expected in [
        ("id", "1", "an integer"),
        ("id", True, "an integer"),
        ("counts_action", 1, "true or false"),
        ("available_actions", "INC", "a list"),
        ("available_actions", [True], "a list of strings or of integers"),
        ("frames", ["00"], "a list of grids"),
        ("declares", {"a": 1}, "a string"),
        ("predict_ok", "yes", "true or false"),
        ("action", None, "a string"),
        ("grade", [{"kind": "noop"}], None),
    ]:
        with pytest.raises((TypeError, KeyError)) as caught:
            Event.from_json({**good, key: bad})
        if expected is not None:
            assert key.split(".")[0] in str(caught.value) and expected in str(caught.value)


def test_event_missing_required_key_is_a_key_error():
    with pytest.raises(KeyError, match="timestamp"):
        Event.from_json({"id": 1})
    without_action = {key: value for key, value in event_of().to_json().items() if key != "action"}
    with pytest.raises(KeyError, match="action"):
        Event.from_json(without_action)
    with pytest.raises(TypeError):
        Event.from_json([1])  # type: ignore[arg-type]


def test_unknown_keys_ride_through_extra_unchanged():
    raw = {**event_of().to_json(), "progress": 0.5, "zz": {"nested": [1, 2.0, "three"]}}
    event = Event.from_json(raw)
    assert event.extra == {"progress": 0.5, "zz": {"nested": [1, 2.0, "three"]}}
    assert _line(event.to_json()) == _line(raw)
    grade = Grade.from_json({"kind": "cell", "text": "cell 1,1=5", "ok": True, "actual": "", "bucket": "world_model", "x": 1, "y": 1})
    assert grade.extra == {"x": 1, "y": 1} and grade.to_json()["x"] == 1


def test_updated_marks_the_key_present_and_refuses_strangers():
    event = event_of()
    assert "mutation_id" not in event.present
    with_id = event.updated(mutation_id=3)
    assert with_id.mutation_id == 3 and "mutation_id" in with_id.present
    assert with_id.to_json()["mutation_id"] == 3
    with pytest.raises(TypeError, match="not event fields"):
        event.updated(mutation=3)


def test_ungated_rule_and_level_advanced():
    assert event_of(counts_action=True).ungated is True
    assert event_of(counts_action=True, action="RESET").ungated is False
    assert event_of(counts_action=False).ungated is False
    assert event_of(counts_action=True, predict="change").ungated is False
    assert event_of(counts_action=True, predict_ok=False).ungated is False
    assert event_of(counts_action=True, predict=None, predict_ok=None, grade=()).ungated is True
    assert event_of(level_before=0, levels_completed=1).level_advanced is True
    assert event_of(level_before=None, levels_completed=1).level_advanced is False


def test_grade_of_an_outcome_keeps_the_written_form():
    outcome = Outcome(kind="change", text="change (implied by free text)", coerced=True)
    graded = Grade.of(outcome, ok=True, actual="1 keys changed")
    assert graded.kind == "coerced" and graded.bucket == "world_model" and graded.coerced
    assert graded.to_json() == {
        "kind": "coerced", "text": "change (implied by free text)", "coerced": True,
        "ok": True, "actual": "1 keys changed", "bucket": "world_model",
    }
    goal = Outcome(kind="channel_eq", text="ch goal = true", channel="goal", value=True)
    assert Grade.of(goal, ok=False, actual="ch goal = false").bucket == "gamble"
    verified = Grade.of(
        Outcome(kind="verify", text="verify:x.py", path="x.py", verifier_hash="a" * 64),
        ok=True, actual="fine", verifier=True, identity_verdict=False,
    )
    assert verified.to_json()["verifier"] is True and verified.to_json()["identity_verdict"] is False
    assert "invalid" not in verified.to_json()
    assert outcome_bucket("win") == "gamble" and outcome_bucket("aggregate") == "aggregate"
    assert Grade.from_json(verified.to_json()) == verified
    assert Outcome.from_json(goal.to_json()) == goal


def test_grade_keeps_numbers_as_written():
    tolerant = Grade.from_json({"kind": "channel_eq", "text": "ch x = 2 +- 1", "ok": True,
                                "actual": "", "bucket": "world_model", "channel": "x", "value": 2, "tol": 1.0})
    assert isinstance(tolerant.value, int) and isinstance(tolerant.tol, float)
    assert _line(tolerant.to_json()) == '{"actual":"","bucket":"world_model","channel":"x","kind":"channel_eq","ok":true,"text":"ch x = 2 +- 1","tol":1.0,"value":2}'
    base = {"kind": "verify", "text": "v", "ok": True, "actual": "", "bucket": "b"}
    for bad in (3, "maybe", None):
        with pytest.raises(TypeError, match="identity_verdict"):
            Grade.from_json({**base, "identity_verdict": bad})
    assert Grade.from_json({**base, "identity_verdict": "invalid"}).identity_verdict == "invalid"
    assert Grade.from_json({**base, "identity_verdict": False}).identity_verdict is False


def test_act_receipt_keeps_null_predict_and_because():
    receipt = Receipt(kind="act", outcome="UNGATED", detail="d", start_event=0, end_event=1,
                      level=1, action="INC amount=1", grade=())
    rendered = receipt.to_json()
    assert rendered["predict"] is None and rendered["because"] is None and rendered["grade"] == []
    assert "modules" not in rendered and "steps" not in rendered
    assert Receipt.from_json(rendered) == receipt
    batch = Receipt(kind="commit", outcome="PREDICTED", detail="d", start_event=0, end_event=2,
                    steps=(ReceiptStep(event=1, action="NOOP", ok=True, failed=()),
                           ReceiptStep(event=2, action="NOOP", ok=True, machine=True, kind="plan_step")))
    rendered = batch.to_json()
    assert "predict" not in rendered and "because" not in rendered
    assert rendered["steps"] == [
        {"event": 1, "action": "NOOP", "ok": True, "failed": []},
        {"event": 2, "action": "NOOP", "ok": True, "machine": True, "kind": "plan_step"},
    ]
    assert Receipt.from_json(rendered) == batch


def test_mutation_round_trips_and_outcomes_are_optional():
    raw = {"mutation_id": 1, "action": "INC", "data": {"amount": 1}, "reasoning": None,
           "observation": {"state": "NOT_FINISHED"}, "timestamp": "t"}
    mutation = Mutation.from_json(raw)
    assert mutation.outcomes is None and _line(mutation.to_json()) == _line(raw)
    with_outcomes = Mutation.from_json({**raw, "claims": [{"kind": "noop", "text": "noop"}]})
    assert with_outcomes.outcomes == (Outcome(kind="noop", text="noop"),)
    with pytest.raises(KeyError, match="observation"):
        Mutation.from_json({"mutation_id": 1, "action": "INC", "data": None, "reasoning": None, "timestamp": "t"})
