"""Registry schema validation, action parsing, refusals, the budget law, and
the JSON Schema subset of #14: the validator keyword by keyword, nested, with
its refusals, the forms and the rendering of structured values."""

from __future__ import annotations

import json

import pytest

from conftest import event_of

from assay.core import AssayError, parse_action, render_action
from assay.evidence import ACTION_WIDTH, RecentLine, history_text
from assay.registry import (
    action_form,
    check_budget,
    check_registry_action,
    load_registry_file,
    parse_registry_action,
    registry_lines,
    validate_action,
    validate_registry,
    validate_value,
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
        ({"actions": [{"name": "A"}], "status_budget": 0}, "status_budget must be a positive integer"),
        ({"actions": [{"name": "A"}], "status_budget": "1500"}, "status_budget must be a positive integer"),
        ({"actions": [{"name": "A"}], "status_budget": True}, "status_budget must be a positive integer"),
        ({"actions": [{"name": "A"}], "status_budget": None}, "status_budget must be a positive integer"),
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


def test_status_budget_is_a_positive_count_of_tokens_or_absent():
    """The registry key of docs/ARCHITECTURE.md section 7.6: a positive
    integer, carried into the canonical copy and read by the accessor; a
    bad one is refused with the registry code and a hint naming the form;
    absent means no budget."""
    from assay.registry import status_budget

    spec = validate_registry({**REGISTRY, "status_budget": 1200})
    assert spec["status_budget"] == 1200 and status_budget(spec) == 1200
    assert status_budget(canonical()) is None and status_budget(None) is None
    with pytest.raises(AssayError) as caught:
        validate_registry({**REGISTRY, "status_budget": -5})
    assert caught.value.code == "REGISTRY_INVALID"
    assert str(caught.value) == "status_budget must be a positive integer (tokens)"
    assert caught.value.hint == (
        'the form is `"status_budget": N`, N a positive count of tokens (an estimate, characters '
        "over four) every status renders under; leave it out for no budget"
    )


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


def test_module_modes_accept_the_specificity_module_under_its_old_name(paths):
    from conftest import run_of

    from assay.modules import active_modules
    from assay.registry import module_modes

    # The module was named `sharpness` before 1.2.0. A registry written under
    # that name validates to the current one, a registry pinned under it is
    # read the same way, and the mode reaches the module either way.
    spec = validate_registry({**REGISTRY, "module_modes": {"sharpness": "off", "hazard": "block"}})
    assert spec["module_modes"] == {"specificity": "off", "hazard": "block"}
    assert module_modes({"module_modes": {"sharpness": "block", "specificity": "off"}}) == {"specificity": "off"}
    assert module_modes(None) == {} and module_modes({"actions": []}) == {}
    pinned = run_of(paths, registry={"actions": [], "module_modes": {"sharpness": "block"}})
    assert {module.NAME: mode for module, mode in active_modules(pinned)}["specificity"] == "block"
    silenced = run_of(paths, registry={"actions": [], "module_modes": {"sharpness": "off"}})
    assert {module.NAME for module, _ in active_modules(silenced)}.isdisjoint({"sharpness", "specificity"})


# --- the JSON Schema subset (#14) -------------------------------------------

# Every keyword of the subset, nested two deep, with the aliases mixed in.
NESTED = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 20},
            "minItems": 1,
            "maxItems": 3,
        },
        "note": {"type": "str"},
        "level": {"type": "integer", "minimum": 0, "maximum": 2},
        "ratio": {"type": "number", "min": 0.5, "max": 1.5},
        "flag": {"type": "boolean"},
        "mode": {"type": "string", "enum": ["fast", "slow"]},
        "pair": {
            "type": "object",
            "properties": {"x": {"type": "int"}, "y": {"type": "int"}},
            "required": ["x", "y"],
            "additionalProperties": False,
        },
    },
    "required": ["lines", "level"],
}
STRUCTURED = {
    "actions": [
        {"name": "RUN", "params": {"program": NESTED, "fast": {"type": "boolean"}}},
        {
            "name": "SUBMIT",
            "params": {
                "answer": {"type": "string", "minLength": 1, "maxLength": 80},
                "spans": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
        },
        {"name": "PICK", "params": {"choice": {"type": "array", "items": {"type": "str", "enum": ["a", "b"]}, "maxItems": 2}}},
        {"name": "FLAG", "params": {"on": {"type": "boolean"}}},
        {"name": "NOOP", "params": {}},
    ]
}
RUN_FORM = (
    "RUN --params '{\"program\": {\"lines\": [<string 1..20 chars>, ... 1..3 items], \"note\"?: <str>, "
    "\"level\": <integer 0..2>, \"ratio\"?: <number 0.5..1.5>, \"flag\"?: <boolean>, \"mode\"?: <fast|slow>, "
    "\"pair\"?: {\"x\": <int>, \"y\": <int>}}, \"fast\": <boolean>}'"
)


def test_the_canonical_form_keeps_the_spelling_and_is_idempotent():
    canonical = validate_registry(STRUCTURED)
    assert canonical["actions"][0]["params"] == {"program": NESTED, "fast": {"type": "boolean"}}
    assert validate_registry(canonical) == canonical
    legacy = validate_registry({"actions": [{"name": "A", "params": {"p": {"type": "int", "min": 1, "max": 2}}}]})
    assert legacy["actions"][0]["params"]["p"] == {"type": "int", "min": 1, "max": 2}
    modern = validate_registry({"actions": [{"name": "A", "params": {"p": {"type": "integer", "minimum": 1}}}]})
    assert modern["actions"][0]["params"]["p"] == {"type": "integer", "minimum": 1}


def test_validate_value_accepts_the_subset_and_canonicalizes():
    value = validate_value(
        NESTED, {"level": 1, "lines": ["a", "b"], "ratio": 1, "flag": True, "pair": {"y": 2, "x": 1}}
    )
    assert value == {"flag": True, "level": 1, "lines": ["a", "b"], "pair": {"x": 1, "y": 2}, "ratio": 1.0}
    assert isinstance(value["ratio"], float)
    assert list(value) == sorted(value) and list(value["pair"]) == ["x", "y"]
    assert validate_value({"type": "boolean"}, False) is False
    assert validate_value({"type": "array", "items": {"type": "number"}, "minItems": 0}, []) == []
    assert validate_value({"type": "string", "enum": ["a"]}, "a") == "a"
    # An enum on a structured type is an exact-value set, checked by equality.
    assert validate_value({"type": "array", "items": {"type": "int"}, "enum": [[1, 2]]}, [1, 2]) == [1, 2]
    with pytest.raises(AssayError, match=r"^value=\[2,1\] is not one of \[\[1, 2\]\]$"):
        validate_value({"type": "array", "items": {"type": "int"}, "enum": [[1, 2]]}, [2, 1])


LONG = ["x" * 20] * 4
LONG_SHOWN = json.dumps(LONG, separators=(",", ":"))[:77] + "..."


@pytest.mark.parametrize(
    "value, message",
    [
        (7, "RUN program=7 is not an object"),
        ({"level": 1}, "RUN program={\"level\":1} is missing property(ies): ['lines']"),
        ({"level": 1, "lines": []}, "RUN program.lines=[] has 0 item(s), below minItems 1"),
        ({"level": 1, "lines": LONG}, f"RUN program.lines={LONG_SHOWN} has 4 item(s), above maxItems 3"),
        ({"level": 1, "lines": ["a", 2]}, "RUN program.lines[1]=2 is not a string"),
        ({"level": 1, "lines": [""]}, "RUN program.lines[0]='' is 0 characters long, below minLength 1"),
        (
            {"level": 1, "lines": ["x" * 21]},
            "RUN program.lines[0]='xxxxxxxxxxxxxxxxxxxxx' is 21 characters long, above maxLength 20",
        ),
        ({"level": 3, "lines": ["a"]}, "RUN program.level=3 is above max 2"),
        ({"level": -1, "lines": ["a"]}, "RUN program.level=-1 is below min 0"),
        ({"level": 1.0, "lines": ["a"]}, "RUN program.level=1.0 is not an integer"),
        ({"level": True, "lines": ["a"]}, "RUN program.level=True is not an integer"),
        ({"level": 1, "lines": ["a"], "ratio": 0.1}, "RUN program.ratio=0.1 is below min 0.5"),
        ({"level": 1, "lines": ["a"], "ratio": "1"}, "RUN program.ratio='1' is not a number"),
        ({"level": 1, "lines": ["a"], "flag": 1}, "RUN program.flag=1 is not true or false"),
        ({"level": 1, "lines": ["a"], "mode": "medium"}, "RUN program.mode='medium' is not one of ['fast', 'slow']"),
        ({"level": 1, "lines": ["a"], "pair": {"x": 1}}, "RUN program.pair={\"x\":1} is missing property(ies): ['y']"),
        (
            {"level": 1, "lines": ["a"], "pair": {"x": 1, "y": 2, "z": 3}},
            "RUN program.pair={\"x\":1,\"y\":2,\"z\":3} has no property 'z'; it takes ['x', 'y']",
        ),
        (
            {"level": 1, "lines": ["a"], "extra": 1},
            "RUN program={\"extra\":1,\"level\":1,\"lines\":[\"a\"]} has no property 'extra'; "
            "it takes ['flag', 'level', 'lines', 'mode', 'note', 'pair', 'ratio']",
        ),
        ({"level": 1, "lines": "a"}, "RUN program.lines='a' is not an array"),
        ({"level": 1, "lines": ["a"], "note": None}, "RUN program.note=None is not a string"),
    ],
)
def test_validate_value_refusals_name_the_path_the_code_and_the_form(value, message):
    with pytest.raises(AssayError) as caught:
        validate_value(NESTED, value, where="RUN program", form="RUN --params '{...}'")
    assert str(caught.value) == message
    assert caught.value.code == "ACTION_PARAMS"
    assert caught.value.hint == "the form is `RUN --params '{...}'`"
    with pytest.raises(AssayError) as bare:
        validate_value(NESTED, value)
    assert str(bare.value) == message.replace("RUN program", "value", 1) and bare.value.hint is None


@pytest.mark.parametrize(
    "schema, message",
    [
        ({"type": "array"}, "A.p is an array and needs 'items'"),
        ({"type": "array", "items": {"type": "str"}, "minItems": -1}, "A.p minItems must be a non-negative integer"),
        ({"type": "array", "items": {"type": "str"}, "minItems": 3, "maxItems": 1}, "A.p has minItems > maxItems"),
        ({"type": "array", "items": 7}, r"parameter schema A\.p\[\] must be an object"),
        ({"type": "object"}, "A.p is an object and needs a non-empty 'properties' object"),
        ({"type": "object", "properties": {}}, "needs a non-empty 'properties'"),
        ({"type": "object", "properties": {"a": {"type": "int"}}, "required": ["b"]}, "A.p requires properties it does not declare: \\['b'\\]"),
        ({"type": "object", "properties": {"a": {"type": "int"}}, "required": ["a", "a"]}, "names a property twice in required"),
        ({"type": "object", "properties": {"a": {"type": "int"}}, "required": "a"}, "required must be a list of property names"),
        ({"type": "object", "properties": {"a": {"type": "int"}}, "additionalProperties": True}, "additionalProperties must be false"),
        ({"type": "object", "properties": {"9x": {"type": "int"}}}, "A.p has an invalid property name '9x'"),
        ({"type": "object", "properties": {"a": {"type": "nope"}}}, "parameter A.p.a type must be one of"),
        ({"type": "integer", "min": 1, "minimum": 2}, "A.p gives both min and minimum"),
        ({"type": "number", "max": 1, "maximum": 2}, "A.p gives both max and maximum"),
        ({"type": "string", "minLength": 2, "maxLength": 1}, "A.p has minLength > maxLength"),
        ({"type": "string", "minLength": 1.5}, "A.p minLength must be a non-negative integer"),
        ({"type": "string", "minimum": 1}, "A.p is a string; minimum does not apply"),
        ({"type": "boolean", "items": {}}, "A.p is a boolean; items does not apply"),
        ({"type": "number", "properties": {}}, "A.p is a number; properties does not apply"),
        ({"type": "int", "bogus": 1}, "has unknown keys \\['bogus'\\]"),
        ({"type": "array", "items": {"type": "str"}, "enum": [["a"], "b"]}, "A.p enum value 'b' does not match type array"),
        ({"type": "boolean", "enum": [1]}, "A.p enum value 1 does not match type boolean"),
        ({"type": "number", "minimum": "1"}, "A.p minimum must be a number"),
        ({"type": "integer", "enum": []}, "A.p enum must be a non-empty list"),
        ({"type": ["string", "null"]}, "parameter A.p type must be one of"),
    ],
)
def test_registry_refuses_a_schema_outside_the_subset(schema, message):
    with pytest.raises(AssayError, match=message) as caught:
        validate_registry({"actions": [{"name": "A", "params": {"p": schema}}]})
    assert caught.value.code == "REGISTRY_INVALID"


def test_registry_lines_and_the_form_render_structured_schemas():
    registry = validate_registry(STRUCTURED)
    lines = registry_lines(registry)
    assert lines[1] == "  RUN program=<object: lines[] note? level ratio? flag? mode? pair?{}> fast=<boolean>"
    assert lines[2] == f"    form: {RUN_FORM}"
    assert lines[3] == "  SUBMIT answer=<string 1..80 chars> spans=<array of >=1 string>"
    assert lines[4] == "    form: SUBMIT --params '{\"answer\": <string 1..80 chars>, \"spans\": [<string>, ... >=1 items]}'"
    assert lines[5] == "  PICK choice=<array of <=2 a|b>"
    assert lines[6] == "    form: PICK --params '{\"choice\": [<a|b>, ... <=2 items]}'"
    assert lines[7:9] == ["  FLAG on=<boolean>", "  NOOP"]
    schemas = {item["name"]: item["params"] for item in registry["actions"]}
    assert action_form("RUN", schemas["RUN"]) == RUN_FORM
    assert action_form("FLAG", schemas["FLAG"]) == "FLAG on=<boolean>"
    assert action_form("NOOP", {}) == "NOOP"
    # The scalar forms are what they were.
    assert action_form("INC", {"amount": {"type": "int", "min": 1, "max": 2}}) == "INC amount=<int 1..2>"
    assert registry_lines(canonical())[1:5] == [
        "  INC amount=<int 1..2>",
        "  SET_LAMP state=<on|off>",
        "  SCALE factor=<float 0.5..2.0>",
        "  NOOP",
    ]


def test_tokens_coerce_booleans_and_refuse_structured_parameters():
    registry = validate_registry(STRUCTURED)
    assert parse_registry_action("flag on=true", registry) == ("FLAG", {"on": True})
    assert parse_registry_action("FLAG on=false", registry) == ("FLAG", {"on": False})
    with pytest.raises(AssayError, match="^FLAG on='yes' is not true or false$") as caught:
        parse_registry_action("FLAG on=yes", registry)
    assert caught.value.code == "ACTION_PARAMS" and caught.value.hint == "the form is `FLAG on=<boolean>`"
    with pytest.raises(AssayError, match="^SUBMIT spans is an array parameter and takes JSON, not a token$") as caught:
        parse_registry_action("SUBMIT answer=x spans=y", registry)
    assert caught.value.hint == (
        "the form is `SUBMIT --params '{\"answer\": <string 1..80 chars>, \"spans\": [<string>, ... >=1 items]}'`"
    )
    with pytest.raises(AssayError, match="^RUN program is an object parameter and takes JSON, not a token$"):
        parse_registry_action("RUN program=x fast=true", registry)
    assert validate_action(registry, "flag", {"on": True}) == ("FLAG", {"on": True})
    with pytest.raises(AssayError, match="^FLAG on='true' is not true or false$"):
        validate_action(registry, "FLAG", {"on": "true"})


def test_validate_action_checks_nested_values_with_the_form_as_the_hint():
    registry = validate_registry(STRUCTURED)
    name, params = validate_action(
        registry, "run", {"program": {"lines": ["a"], "level": 2, "pair": {"y": 1, "x": 0}}, "fast": False}
    )
    assert name == "RUN"
    assert params == {"fast": False, "program": {"level": 2, "lines": ["a"], "pair": {"x": 0, "y": 1}}}
    with pytest.raises(AssayError, match=r"^RUN program.lines\[1\]=2 is not a string$") as caught:
        validate_action(registry, "RUN", {"program": {"lines": ["a", 2], "level": 1}, "fast": True})
    assert caught.value.code == "ACTION_PARAMS" and caught.value.hint == f"the form is `{RUN_FORM}`"
    with pytest.raises(AssayError, match="^RUN is missing parameter\\(s\\): \\['fast'\\]$"):
        validate_action(registry, "RUN", {"program": {"lines": ["a"], "level": 1}})
    assert validate_action(registry, "SUBMIT", {"answer": "a b", "spans": ["x"]}) == (
        "SUBMIT",
        {"answer": "a b", "spans": ["x"]},
    )


def test_render_action_keeps_scalars_and_prints_nested_values_as_json():
    assert render_action("INC", {"amount": 1}) == "INC amount=1"
    assert render_action("ACTION6", {"y": 3, "x": 12}) == "ACTION6 x=12 y=3"
    assert render_action("SCALE", {"factor": 1.5}) == "SCALE factor=1.5"
    assert render_action("SET_LAMP", {"state": "on"}) == "SET_LAMP state=on"
    assert render_action("NOOP", None) == "NOOP" and render_action("NOOP", {}) == "NOOP"
    assert render_action("BANK", {"span": "aGVsbG8="}) == "BANK span=aGVsbG8="
    assert render_action("FLAG", {"on": True}) == "FLAG on=true"
    assert render_action("SUBMIT", {"answer": "Answer: 1", "spans": ["a b", "c"]}) == (
        'SUBMIT answer="Answer: 1" spans=["a b","c"]'
    )
    assert render_action("RUN", {"program": "x = 1\nprint(x)"}) == 'RUN program="x = 1\\nprint(x)"'
    assert render_action("RUN", {"program": {"lines": ["a"], "level": 1}}) == (
        'RUN program={"level":1,"lines":["a"]}'
    )
    assert render_action("NAME", {"empty": ""}) == "NAME empty="


def test_history_lines_clip_a_long_action_and_keep_a_short_one():
    def line(action: str) -> RecentLine:
        return RecentLine(
            event=3, paid=3, unit=1, action=action, predict_ok=True, changed=2,
            changed_unit="keys", frames=None, state="NOT_FINISHED",
        )

    assert history_text([line("ACTION6 x=12 y=34")]) == [
        "  e0003 a0003 L1 ACTION6 x=12 y=34 ✓ | 2 keys | NOT_FINISHED"
    ]
    long = "RUN program=" + json.dumps({"lines": ["x" * 50, "y" * 50]}, separators=(",", ":"))
    assert len(long) > ACTION_WIDTH
    [rendered] = history_text([line(long)])
    assert rendered == f"  e0003 a0003 L1 {long[:ACTION_WIDTH - 3]}... ✓ | 2 keys | NOT_FINISHED"
    exact = "x" * ACTION_WIDTH
    assert history_text([line(exact)]) == [f"  e0003 a0003 L1 {exact} ✓ | 2 keys | NOT_FINISHED"]
