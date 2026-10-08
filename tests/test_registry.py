"""Registry schema validation, action parsing, refusals, and the budget law."""

from __future__ import annotations

import pytest

from conftest import event_of

from assay.core import AssayError, parse_action
from assay.registry import (
    check_budget,
    check_registry_action,
    load_registry_file,
    parse_registry_action,
    validate_registry,
)

REGISTRY = {
    "actions": [
        {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
        {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
        {"name": "SCALE", "params": {"factor": {"type": "float", "min": 0.5, "max": 2.0}}},
        {"name": "NOOP", "params": {}},
    ],
    "budget": {"actions": 10},
    "mode_note": "test world",
}


def canonical():
    return validate_registry(REGISTRY)


# --- schema validation ------------------------------------------------------


def test_load_registry_file_valid(tmp_path):
    import json

    target = tmp_path / "reg.json"
    target.write_text(json.dumps(REGISTRY))
    spec = load_registry_file(target)
    assert [item["name"] for item in spec["actions"]] == [
        "INC",
        "SET_LAMP",
        "SCALE",
        "NOOP",
    ]
    assert spec["budget"] == {"actions": 10}
    assert spec["mode_note"] == "test world"


@pytest.mark.parametrize(
    "mutant, message",
    [
        ({"actions": []}, "non-empty"),
        ({"actions": [{"name": "A", "params": {"p": {"type": "bool"}}}]}, "type"),
        ({"actions": [{"name": "RESET"}]}, "built-in"),
        ({"actions": [{"name": "A"}, {"name": "a"}]}, "twice"),
        ({"actions": [{"name": "A"}], "budget": {"actions": 0}}, "positive"),
        ({"actions": [{"name": "A"}], "budget": {"actions": "5"}}, "positive"),
        ({"actions": [{"name": "A"}], "extra": 1}, "unknown keys"),
        ({"actions": [{"name": "A", "params": {"p": {"type": "str", "min": 1}}}]}, "does not apply"),
        (
            {"actions": [{"name": "A", "params": {"p": {"type": "int", "enum": ["x"]}}}]},
            "does not match type",
        ),
        (
            {"actions": [{"name": "A", "params": {"p": {"type": "int", "min": 5, "max": 1}}}]},
            "min > max",
        ),
        ({"actions": [{"name": "9BAD"}]}, "name"),
    ],
)
def test_registry_schema_refusals(mutant, message):
    with pytest.raises(AssayError, match=message):
        validate_registry(mutant)


# --- action token parsing ---------------------------------------------------


def test_parse_valid_actions():
    registry = canonical()
    assert parse_registry_action("inc amount=2", registry) == ("INC", {"amount": 2})
    assert parse_registry_action("SET_LAMP state=on", registry) == (
        "SET_LAMP",
        {"state": "on"},
    )
    name, params = parse_registry_action("SCALE factor=1.5", registry)
    assert name == "SCALE" and params == {"factor": 1.5}
    assert parse_registry_action("NOOP", registry) == ("NOOP", None)
    assert parse_registry_action("RESET", registry) == ("RESET", None)


@pytest.mark.parametrize(
    "token, message",
    [
        ("BOGUS amount=1", "unknown action"),
        ("INC", "missing parameter"),
        ("INC amount=abc", "not an integer"),
        ("INC amount=3", "above max"),
        ("INC amount=0", "below min"),
        ("INC amount=1 amount=2", "supplied twice"),
        ("INC volume=1", "no parameter"),
        ("INC amount", "pname=value"),
        ("SET_LAMP state=blue", "not one of"),
        ("SCALE factor=nan", "finite"),
        ("RESET amount=1", "no parameters"),
    ],
)
def test_parse_refusals(token, message):
    with pytest.raises(AssayError, match=message):
        parse_registry_action(token, canonical())


def test_core_parse_action_dispatches_to_registry():
    name, params = parse_action("inc amount=1", canonical())
    assert (name, params) == ("INC", {"amount": 1})


# --- affordance check -------------------------------------------------------


def test_affordance_check_only_when_advertised():
    check_registry_action("INC", ["INC", "NOOP"])
    check_registry_action("INC", [])  # nothing advertised: no check
    check_registry_action("RESET", ["NOOP"])  # RESET always allowed
    with pytest.raises(AssayError, match="unavailable"):
        check_registry_action("SET_LAMP", ["INC", "NOOP"])


# --- budget -----------------------------------------------------------------


def _paid(n):
    return [event_of(id=index, counts_action=True) for index in range(n)]


def test_budget_refusal_embeds_code_and_remaining():
    registry = canonical()  # cap 10
    check_budget(_paid(9), registry, planned=1)  # last affordable spend passes
    with pytest.raises(AssayError, match="^action budget cap=10 spent=10 remaining=0") as error:
        check_budget(_paid(10), registry, planned=1)
    assert error.value.code == "BUDGET_EXHAUSTED" and error.value.kind == "refused"
    assert "remaining=0" in str(error.value)
    with pytest.raises(AssayError, match="remaining=1"):
        check_budget(_paid(9), registry, planned=3)


def test_budget_absent_without_registry_or_cap():
    check_budget(_paid(1000), None, planned=1)
    check_budget(_paid(1000), {"actions": []}, planned=1)
