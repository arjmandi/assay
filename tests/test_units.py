"""The parts on their own: the grammar, the registry and the adapter
boundary, the grades of state outcomes, the chain and the audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assay import AssayError, grade, grammar, journal, meters, world
from assay.journal import Paths, Run

REGISTRY = {
    "actions": [
        {"name": "inc", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
        {"name": "SET", "params": {"lamp": {"type": "string", "enum": ["on", "off"]}, "dim": {"type": "boolean"}}},
        {
            "name": "SUBMIT",
            "params": {
                "spans": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1},
                "meta": {"type": "object", "properties": {"n": {"type": "number"}, "tag": {"type": "string"}}, "required": ["n"]},
            },
        },
    ],
    "budget": {"actions": 5},
}


def code(function: Any, *args: Any) -> str:
    with pytest.raises(AssayError) as caught:
        function(*args)
    return caught.value.code


# --- the grammar -------------------------------------------------------------------


def test_every_outcome_form_parses() -> None:
    outcomes = grammar.parse(
        "noop; CHANGE; level + 1; win; verify:checks/a.py; ch tick = 360; ch rate = 20 ± 5; "
        "ch name = 'red'; ch on = True; ch ents delta sign +; ch gears delta >= 16; "
        "ch n crosses 16 from below; ch n crosses 2.5; change @within 3s"
    )
    assert [item["kind"] for item in outcomes] == [
        "noop", "change", "level_up", "win", "verify", "channel_eq", "channel_eq", "channel_eq", "channel_eq",
        "channel_delta", "channel_delta", "channel_cross", "channel_cross", "change",
    ]
    assert outcomes[4] == {"kind": "verify", "text": "verify:checks/a.py", "path": "checks/a.py"}
    assert outcomes[5] == {"kind": "channel_eq", "text": "ch tick = 360", "channel": "tick", "value": 360}
    assert outcomes[6]["tol"] == 5.0 and outcomes[7]["value"] == "red" and outcomes[8]["value"] is True
    assert outcomes[9] == {"kind": "channel_delta", "text": "ch ents delta sign +", "channel": "ents", "op": "sign", "sign": "+"}
    assert outcomes[10]["op"] == ">=" and outcomes[10]["value"] == 16
    assert outcomes[11]["direction"] == "below" and "direction" not in outcomes[12] and outcomes[12]["value"] == 2.5
    assert outcomes[13] == {"kind": "change", "text": "change", "window_s": 3.0}


def test_free_text_is_commentary_and_an_empty_or_malformed_prediction_is_refused() -> None:
    assert grammar.parse("it should move; change") == [
        {"kind": "note", "text": "it should move"},
        {"kind": "change", "text": "change"},
    ]
    assert grammar.parse("the lamp turns on")[-1] == {
        "kind": "change", "text": "change (implied by free text)", "coerced": True,
    }
    assert code(grammar.parse, "  ") == "PREDICTION_REQUIRED" and code(grammar.parse, None) == "PREDICTION_REQUIRED"
    for malformed in ("ch counter", "level+2", "verify: a.py", "ch n delta = up", "change @within 0s", "win big"):
        assert code(grammar.parse, malformed) == "PREDICTION_SYNTAX", malformed


def test_progress_outcomes_are_gambles_and_the_rest_meter_the_world_model() -> None:
    buckets = [grammar.bucket(item) for item in grammar.parse("win; level+1; ch level = 2; ch goal = true; ch x = 1; noop")]
    assert buckets == ["gamble", "gamble", "gamble", "gamble", "world_model", "world_model"]


# --- the registry ------------------------------------------------------------------


def test_a_registry_is_canonical_and_strict() -> None:
    registry = world.validate_registry(REGISTRY)
    assert registry["actions"][0] == {"name": "INC", "params": {"amount": {"type": "integer", "minimum": 1, "maximum": 2}}}
    assert world.notes_cap(registry) == 16_000 and world.notes_cap({**registry, "notes_cap": None}) is None
    lines = world.registry_lines(registry)
    assert lines[1:4] == [
        "  INC amount=<integer 1..2>",
        "  SET lamp=<on|off> dim=<boolean>",
        "  SUBMIT --params '{\"spans\": [<string >=1 chars>, ...], \"meta\": {\"n\": <number>, \"tag\"?: <string>}}'",
    ]
    for broken in (
        [],
        {"actions": []},
        {"actions": [{"name": "RESET"}]},
        {"actions": [{"name": "A"}, {"name": "a"}]},
        {"actions": [{"name": "A", "params": {"x": {"type": "date"}}}]},
        {"actions": [{"name": "A", "params": {"x": {"type": "string", "minimum": 1}}}]},
        {"actions": [{"name": "A", "params": {"x": {"type": "array"}}}]},
        {"actions": [{"name": "A"}], "budget": {"actions": 0}},
        {"actions": [{"name": "A"}], "goal": {"text": " "}},
        {"actions": [{"name": "A"}], "gate": "off"},
    ):
        assert code(world.validate_registry, broken) == "REGISTRY_INVALID", broken


def test_actions_are_validated_against_their_schemas() -> None:
    registry = world.validate_registry(REGISTRY)
    assert world.parse_action(["inc", "amount=2"], registry) == ("INC", {"amount": 2})
    assert world.parse_action(["SET", "lamp=on", "dim=true"], registry) == ("SET", {"lamp": "on", "dim": True})
    assert world.parse_action(["RESET"], registry) == ("RESET", None)
    submit = {"spans": ["a b"], "meta": {"n": 1.5}}
    assert world.validate_action(registry, "submit", submit) == ("SUBMIT", submit)
    assert world.render_action("SUBMIT", submit) == 'SUBMIT meta={"n":1.5} spans=["a b"]'
    for tokens in (["INC"], ["INC", "amount=3"], ["INC", "amount=x"], ["INC", "amount=1", "amount=1"],
                   ["INC", "size=1"], ["SET", "lamp=red", "dim=true"], ["SUBMIT", "spans=a"], ["RESET", "x=1"], []):
        assert code(world.parse_action, tokens, registry) == "ACTION_PARAMS", tokens
    assert code(world.parse_action, ["FLY"], registry) == "ACTION_UNKNOWN"
    for params in ({"spans": [], "meta": {"n": 1}}, {"spans": ["a"], "meta": {}}, {"spans": ["a"], "meta": {"n": 1, "x": 2}},
                   {"spans": [""], "meta": {"n": 1}}, {"spans": "a", "meta": {"n": 1}}):
        assert code(world.validate_action, registry, "SUBMIT", params) == "ACTION_PARAMS", params
    world.check_available("INC", [])
    world.check_available("RESET", ["SET"])
    assert code(world.check_available, "INC", ["SET"]) == "ACTION_UNAVAILABLE"


def test_an_observation_is_normalized_or_refused() -> None:
    observed = world.observe({"state": "WIN", "levels_completed": 1, "available_actions": ["B", "A"], "data": {"b": 1, "a": [2]}})
    assert observed == {"state": "WIN", "levels_completed": 1, "win_levels": 1, "available_actions": ["A", "B"], "data": {"a": [2], "b": 1}}
    assert world.observe({"frames": [[[0, 15], [10, 1]]]})["frames"] == [["0f", "a1"]]
    assert world.observe({"data": {}, "state": "NOT_PLAYED"})["state"] == "NOT_PLAYED"
    for broken in (None, {}, {"frames": []}, {"frames": [[[0, 16]]]}, {"frames": [[[0], [0, 1]]]},
                   {"data": {"x": object()}}):
        assert code(world.observe, broken) == "OBSERVATION_INVALID", broken


# --- grading ------------------------------------------------------------------------


GATED = {"predict": "change", "predict_ok": True, "grade": [{"kind": "change", "text": "change", "ok": True}]}


def _run(tmp_path: Path, observations: list[dict[str, Any]], *, gated: bool = True) -> Run:
    """A run whose events carry these observations, with states declared on their keys."""
    paths = Paths(tmp_path)
    run = Run(paths, {"world_id": "t"}, world.validate_registry(REGISTRY), [], journal.seed())
    previous = None
    for data in observations:
        event = journal.make_event(world.observe({"data": data}), "INC" if previous else "START", None, previous)
        previous = run.append({**event, **(GATED if previous and gated else {})})
    journal.write_json(paths.config, run.config)
    journal.write_json(paths.registry, run.registry)
    journal.write_json(paths.states, {key: {"form": "path", "path": key} for key in observations[0]})
    return run


def _marks(run: Run, prediction: str, elapsed: float = 0.0) -> list[str]:
    before, after = run.events[-2], run.events[-1]
    return grade.lines(grade.grade(run, grammar.parse(prediction), before, after, elapsed))


def test_state_outcomes_grade_against_the_readings(tmp_path: Path) -> None:
    run = _run(tmp_path, [{"n": 2, "rate": 18.0, "name": "red", "on": False}, {"n": 5, "rate": 22.5, "name": "blue", "on": True}])
    assert _marks(run, "ch n = 5; ch n = 5.0; ch name = blue; ch on = true; ch rate = 20 ± 3") == [
        "✓ ch n = 5", "✓ ch n = 5.0", "✓ ch name = blue", "✓ ch on = true", "✓ ch rate = 20 ± 3",
    ]
    assert _marks(run, "ch n = 4; ch rate = 20 ± 2; ch on = 1; ch name = 5") == [
        "✗ ch n = 4 | ch n = 5", "✗ ch rate = 20 ± 2 | ch rate = 22.5", "✗ ch on = 1 | ch on = true",
        '✗ ch name = 5 | ch name = "blue"',
    ]
    assert _marks(run, "ch n delta = 3; ch n delta >= 3; ch n delta <= 2; ch n delta sign -; ch rate delta sign +") == [
        "✓ ch n delta = 3", "✓ ch n delta >= 3", "✗ ch n delta <= 2 | ch n moved +3 (2 -> 5)",
        "✗ ch n delta sign - | ch n moved +3 (2 -> 5)", "✓ ch rate delta sign +",
    ]
    assert _marks(run, "ch n crosses 4; ch n crosses 5 from below; ch n crosses 4 from above; ch n crosses 9") == [
        "✓ ch n crosses 4", "✓ ch n crosses 5 from below",
        "✗ ch n crosses 4 from above | ch n went 2 -> 5 (threshold 4)", "✗ ch n crosses 9 | ch n went 2 -> 5 (threshold 9)",
    ]
    # What cannot be read or compared is ungradable, its own verdict, never a miss.
    assert _marks(run, "ch name delta = 1; ch name = red ± 1; change @within 1s", elapsed=2.0) == [
        '! ch name delta = 1 | UNGRADABLE: a delta or a crossing needs numbers, got "red" -> "blue"',
        '! ch name = red ± 1 | UNGRADABLE: a tolerance needs numbers, got "blue"',
        "! change | UNGRADABLE: settled after 2.00s, outside the declared 1s window",
    ]


def test_the_verdict_of_an_action(tmp_path: Path) -> None:
    run = _run(tmp_path, [{"n": 1, "s": "a"}, {"n": 2, "s": "a"}])
    before, after = run.events

    def verdict(prediction: str) -> bool | None:
        return grade.verdict(grade.grade(run, grammar.parse(prediction), before, after, 0.0))

    assert verdict("change; ch n = 2") is True
    assert verdict("change; ch n = 3") is False
    assert verdict("change; ch s delta = 1") is None  # nothing missed, one outcome ungradable
    assert verdict("noop; ch s delta = 1") is False  # a miss outranks it
    assert _marks(run, "ch budget_remaining = 4; ch level = 0; ch goal = false") == [
        "✓ ch budget_remaining = 4", "✓ ch level = 0", "✓ ch goal = false",
    ]


def test_the_meters_are_functions_of_the_record(tmp_path: Path) -> None:
    run = _run(tmp_path, [{"n": 1}])
    journal.write_json(run.paths.states, {"n": {"form": "path", "path": "n"}})
    for prediction in ("ch n = 1", "ch n = 9; win", "it does something", "ch n delta = 1; change"):
        before = run.last
        after = journal.make_event(world.observe({"data": {"n": 1}}), "INC", None, before)
        graded = grade.grade(run, grammar.parse(prediction), before, after, 0.0)
        run.append({**after, "predict": prediction, "predict_ok": grade.verdict(graded), "grade": graded})
    counts = meters.predictions(run.events)
    # The coerced free text is graded and earns nothing in either rate.
    assert (counts.world_model, counts.gamble, counts.graded, counts.coerced, counts.void) == ((4, 3), (1, 1), 6, 1, 0)
    assert meters.recent(run.events) == (1, 4)
    ledger = meters.coverage(run.events)
    assert (ledger.paid, ledger.untried, ledger.dead, ledger.stalled) == (4, [], [], False)


# --- the chain and the audit ---------------------------------------------------------


def test_an_edited_journal_does_not_audit_clean(tmp_path: Path) -> None:
    run = _run(tmp_path, [{"n": index} for index in range(27)])
    paths = run.paths
    assert journal.audit(paths) == (
        True, ["AUDIT | CLEAN | events 27 (paid 26) | contiguous yes | chain intact | anchors intact (1)"]
    )
    Run.load(paths).check_chain()
    original = paths.events.read_text()
    # An edit inside the journal breaks the chain and the anchor.
    paths.events.write_text(original.replace('"n":3', '"n":4', 1))
    clean, report = journal.audit(paths)
    assert not clean and "chain DIVERGED | anchors DIVERGED (1)" in report[0]
    assert code(Run.load(paths).check_chain) == "CHAIN_DIVERGED"
    # A truncation is caught by the stored head, and a reordering by the ids.
    lines = original.splitlines()
    paths.events.write_text("\n".join(lines[:20]) + "\n")
    assert "chain DIVERGED" in journal.audit(paths)[1][0]
    paths.events.write_text("\n".join([lines[1], lines[0], *lines[2:]]) + "\n")
    assert "contiguous NO" in journal.audit(paths)[1][0]
    # A head one line behind is a crash between two writes, which a start tolerates.
    paths.events.write_text(original)
    heads = [journal.seed()]
    for line in lines:
        heads.append(journal.advance(heads[-1], line))
    journal.write_json(paths.chain, {"last_id": 25, "head": heads[26]})
    Run.load(paths).check_chain()


def test_the_ungated_rule(tmp_path: Path) -> None:
    run = _run(tmp_path, [{"n": 0}, {"n": 1}], gated=False)
    start, bare = run.events
    assert not journal.is_ungated(start) and journal.is_ungated(bare)
    assert not journal.is_ungated({**bare, "action": "RESET"})
    assert not journal.is_ungated({**bare, "predict": "change", "predict_ok": True, "grade": [{}]})
    assert not journal.is_ungated({**bare, "predict": "x", "predict_ok": None, "grade": []})
    clean, report = journal.audit(run.paths)
    assert not clean and report[1] == "AUDIT | UNGATED events [1]: paid actions that carry no prediction"


def test_the_journal_line_format_is_frozen(tmp_path: Path) -> None:
    run = _run(tmp_path, [{"n": 0}])
    line = run.paths.events.read_text().splitlines()[0]
    event = json.loads(line)
    assert line == json.dumps(event, separators=(",", ":"), sort_keys=True)
    assert sorted(event) == [
        "action", "available_actions", "counts_action", "data", "id", "level_before", "levels_completed", "note",
        "observation", "state", "timestamp", "win_levels",
    ]
    # sha256("assay-chain-v1"): the chain of every journal ever published starts here.
    assert journal.seed() == "29ad34c5e516ded764e331f3cf386d88c238559c72f2282145c66863ccb53ef6"
    assert journal.read_json(run.paths.chain) == {"last_id": 0, "head": journal.advance(journal.seed(), line)}
