"""Property tests with Hypothesis: the claim parser, the chain rule, the
ungated rule and the registry parameters (#26, part one).

Each property restates a public contract inside the test (the claim forms of
verify/CLAIM_GRAMMAR.md, the two-line chain rule and the ungated rule of
verify/JOURNAL_SPEC.md sections 4 and 6, the parameter schema documented in
src/assay/registry.py) and holds the kernel to it over generated inputs; where
the standalone checker has its own implementation, the kernel is held to the
checker as well. The profile is derandomized, so a run is reproducible, and
the example database is off, so a run writes nothing beside the tree.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from conftest import event_of

from assay.core import AssayError, RunPaths
from assay.integrity import CHAIN_SEED, chain_over, chain_over_bytes, ungated_events
from assay.predictions import parse_claims
from assay.registry import parse_registry_action, validate_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verify"))
import assay_verify  # noqa: E402  the independent checker, as in test_verify_checker.py

settings.register_profile(
    "assay", derandomize=True, max_examples=200, deadline=None, database=None
)
settings.load_profile("assay")


# ------------------------------------------------------------ the claim parser

CLAIM_KINDS = {
    "noop",
    "change",
    "win",
    "level_up",
    "verify",
    "channel_eq",
    "channel_delta",
    "channel_cross",
    "aggregate",
    "note",
}
NOT_MECHANICAL = {"note", "aggregate"}


@dataclasses.dataclass(frozen=True)
class Part:
    """One generated claim: the text as written (with its window suffix, if
    any), the text the parser must echo, the kind it must report, the fields
    it must carry, and the window it must attach."""

    text: str
    core: str
    kind: str
    fields: dict[str, Any]
    window_s: float | None = None


def _cased(word: str) -> st.SearchStrategy[str]:
    """Every keyword is case-insensitive."""
    return st.sampled_from([word, word.upper(), word.capitalize()])


_gap = st.text(alphabet=" \t", min_size=1, max_size=2)  # where the grammar has \s+
_gap0 = st.text(alphabet=" \t", min_size=0, max_size=2)  # where it has \s*
_name = st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,31}", fullmatch=True)
_int_text = st.integers(-1_000_000, 1_000_000).map(str)
_decimal_text = st.tuples(
    st.integers(-1_000_000, 1_000_000), st.integers(0, 1_000_000)
).map(lambda pair: f"{pair[0]}.{pair[1]}")
_number_text = _int_text | _decimal_text
_unsigned_number_text = st.integers(0, 1_000_000).map(str) | st.tuples(
    st.integers(0, 1_000_000), st.integers(0, 1_000_000)
).map(lambda pair: f"{pair[0]}.{pair[1]}")
_positive_number_text = _unsigned_number_text.filter(lambda text: float(text) > 0)
# A bare word reads as itself, except that the value parser tries int, then
# float, then the word: `inf`, `infinity` and `nan` therefore read as floats,
# and `true`/`false` as booleans. Those are generated as their own forms.
_identifier = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]*", fullmatch=True).filter(
    lambda word: word.lower() not in {"true", "false", "inf", "infinity", "nan"}
)
# A quoted value takes anything but its own quote; `;` is left out because the
# splitter runs before the grammar, and `@` so no window hides in a value.
_quoted_inner = st.text(alphabet="abcxyz019 _-+=:.,", max_size=8)


def _number_value(text: str) -> int | float:
    return float(text) if "." in text else int(text)


_numeric_value = _number_text.map(lambda text: (text, _number_value(text)))
_any_value = st.one_of(
    _numeric_value,
    st.sampled_from(["true", "TRUE", "True"]).map(lambda text: (text, True)),
    st.sampled_from(["false", "FALSE", "False"]).map(lambda text: (text, False)),
    _quoted_inner.map(lambda inner: (f'"{inner}"', inner)),
    _quoted_inner.map(lambda inner: (f"'{inner}'", inner)),
    _identifier.map(lambda word: (word, word)),
)


@st.composite
def _simple_part(draw) -> Part:
    kind = draw(st.sampled_from(["noop", "change", "win"]))
    text = draw(_cased(kind))
    return Part(text, text, kind, {})


@st.composite
def _level_part(draw) -> Part:
    text = draw(_cased("level")) + draw(_gap0) + "+" + draw(_gap0) + "1"
    return Part(text, text, "level_up", {})


@st.composite
def _verify_part(draw) -> Part:
    path = draw(st.from_regex(r"[A-Za-z0-9_./-]{1,20}\.py", fullmatch=True))
    text = draw(_cased("verify")) + ":" + path
    return Part(text, text, "verify", {"path": path})


@st.composite
def _channel_eq_part(draw) -> Part:
    name = draw(_name)
    value_text, value = draw(_any_value)
    text = draw(_cased("ch")) + draw(_gap) + name + draw(_gap0) + "=" + draw(_gap0)
    text += value_text
    fields: dict[str, Any] = {"channel": name.lower(), "value": value}
    if draw(st.booleans()):
        tol_text = draw(_unsigned_number_text)
        text += draw(_gap0) + draw(st.sampled_from(["±", "+-"])) + draw(_gap0)
        text += tol_text
        fields["tol"] = float(tol_text)
    return Part(text, text, "channel_eq", fields)


@st.composite
def _channel_delta_part(draw) -> Part:
    name = draw(_name)
    text = draw(_cased("ch")) + draw(_gap) + name + draw(_gap) + draw(_cased("delta"))
    fields: dict[str, Any] = {"channel": name.lower()}
    if draw(st.booleans()):
        sign = draw(st.sampled_from(["+", "-"]))
        text += draw(_gap) + draw(_cased("sign")) + draw(_gap0) + sign
        fields.update(op="sign", sign=sign)
    else:
        op = draw(st.sampled_from(["=", ">=", "<="]))
        value_text, value = draw(_numeric_value)
        text += draw(_gap0) + op + draw(_gap0) + value_text
        fields.update(op=op, value=value)
    return Part(text, text, "channel_delta", fields)


@st.composite
def _channel_cross_part(draw) -> Part:
    name = draw(_name)
    value_text, value = draw(_numeric_value)
    text = draw(_cased("ch")) + draw(_gap) + name + draw(_gap) + draw(_cased("crosses"))
    text += draw(_gap) + value_text
    fields: dict[str, Any] = {"channel": name.lower(), "value": value}
    direction = draw(st.none() | st.sampled_from(["below", "above"]))
    if direction is not None:
        text += draw(_gap) + draw(_cased("from")) + draw(_gap) + draw(_cased(direction))
        fields["direction"] = direction
    return Part(text, text, "channel_cross", fields)


@st.composite
def _aggregate_part(draw) -> Part:
    name = draw(_name)
    stat = draw(st.sampled_from(["mean", "min", "max"]))
    op = draw(st.sampled_from(["=", ">=", "<="]))
    value_text, value = draw(_numeric_value)
    over = draw(st.integers(1, 1000))
    horizon = draw(st.integers(1, 100))
    on_fail = draw(st.sampled_from(["advise", "revoke_batching"]))
    text = draw(_cased("agg")) + draw(_gap) + draw(_cased("ch")) + draw(_gap) + name
    text += draw(_gap) + draw(_cased(stat)) + draw(_gap0) + op + draw(_gap0) + value_text
    text += draw(_gap) + draw(_cased("over")) + draw(_gap) + f"{over}a"
    text += draw(_gap) + draw(_cased("horizon")) + draw(_gap) + f"{horizon}a"
    text += draw(_gap) + draw(_cased("on-fail")) + draw(_gap) + draw(_cased(on_fail))
    fields = {
        "channel": name.lower(),
        "stat": stat,
        "op": op,
        "value": value,
        "over": over,
        "horizon": horizon,
        "on_fail": on_fail,
    }
    return Part(text, text, "aggregate", fields)


@st.composite
def _windowed(draw, inner: st.SearchStrategy[Part]) -> Part:
    """Any claim may end with `@within Ns`."""
    part = draw(inner)
    if not draw(st.booleans()):
        return part
    seconds_text = draw(_positive_number_text)
    suffix = draw(_gap) + draw(_cased("@within")) + draw(_gap) + seconds_text
    suffix += draw(st.sampled_from(["s", "S"]))
    return dataclasses.replace(part, text=part.text + suffix, window_s=float(seconds_text))


mechanical_parts = _windowed(
    st.one_of(
        _simple_part(),
        _level_part(),
        _verify_part(),
        _channel_eq_part(),
        _channel_delta_part(),
        _channel_cross_part(),
    )
)
aggregate_parts = _windowed(_aggregate_part())
claim_parts = mechanical_parts | aggregate_parts


@st.composite
def predictions(draw) -> tuple[str, list[Part]]:
    """Several claims joined with `;`, with the spacing and the empty parts
    the splitter tolerates."""
    parts = draw(st.lists(claim_parts, min_size=1, max_size=5))
    text = draw(_gap0) + parts[0].text
    for part in parts[1:]:
        text += draw(st.sampled_from([";", " ; ", ";;", "; "])) + part.text
    return text + draw(_gap0), parts


@given(prediction=predictions())
def test_grammar_text_parses_one_claim_per_part(prediction):
    text, parts = prediction
    if all(part.kind == "aggregate" for part in parts):
        # Statistical claims are additive, never substitutive.
        with pytest.raises(AssayError, match="additive"):
            parse_claims(text)
        return
    claims = parse_claims(text)
    assert [claim.kind for claim in claims] == [part.kind for part in parts]
    for claim, part in zip(claims, parts, strict=True):
        assert claim.text == part.core
        for key, value in part.fields.items():
            assert getattr(claim, key) == value, key
        assert claim.window_s == part.window_s
        assert not claim.coerced and "coerced" not in claim.to_json()


@given(parts=st.lists(aggregate_parts, min_size=1, max_size=3))
def test_aggregate_claims_alone_are_refused_as_additive(parts):
    with pytest.raises(AssayError, match="additive"):
        parse_claims("; ".join(part.text for part in parts))


_grid_coordinate = st.integers(0, 63).map(str)
_grid_offset = st.integers(-9, 9).map(str) | st.integers(0, 9).map(lambda n: f"+{n}")
frame_forms = st.one_of(
    st.builds(
        "cell {},{}={}".format,
        _grid_coordinate,
        _grid_coordinate,
        st.sampled_from("0123456789abcdefABCDEF"),
    ),
    st.builds(
        "move {},{} {},{}".format,
        _grid_coordinate,
        _grid_coordinate,
        _grid_offset,
        _grid_offset,
    ),
    st.builds("vanish {},{}".format, _grid_coordinate, _grid_coordinate),
    st.builds(
        "region {}:{},{}:{}".format,
        _grid_coordinate,
        _grid_coordinate,
        _grid_coordinate,
        _grid_coordinate,
    ),
)


@given(
    frame=frame_forms,
    before=st.lists(claim_parts, max_size=2),
    after=st.lists(claim_parts, max_size=2),
)
def test_frame_world_forms_are_refused_by_name_on_every_run(frame, before, after):
    # The frame forms (cell, move, vanish, region) parse only under an
    # observation kind, which nothing passes: refused by name, before any
    # spend, however they are mixed with admitted claims (owner decision O1).
    text = ";".join([*(part.text for part in before), frame, *(part.text for part in after)])
    with pytest.raises(AssayError, match="does not admit it") as caught:
        parse_claims(text)
    assert repr(frame) in str(caught.value)


_free_text_body = st.text().filter(
    lambda body: ";" not in body and "@within" not in body.lower()
)


@given(opener=st.sampled_from(["the", "probably", "it", "door", "nothing"]), body=_free_text_body)
def test_free_text_is_a_note_plus_a_coerced_change(opener, body):
    text = f"{opener} {body}"
    assert [claim.to_json() for claim in parse_claims(text)] == [
        {"kind": "note", "text": text.strip()},
        {"kind": "change", "text": "change (implied by free text)", "coerced": True},
    ]


_grammar_tokens = st.sampled_from(
    [
        "noop", "change", "win", "level+1", "level + 1", "verify:", "check.py",
        "ch", "agg", "delta", "sign", "crosses", "from", "below", "above",
        "mean", "min", "max", "over", "horizon", "on-fail", "advise",
        "revoke_batching", "@within", "s", "a", ";", " ", "  ", "=", ">=", "<=",
        "+-", "±", "+", "-", "0", "1", "2.5", "101", ".", "true", "false",
        '"', "'", "cell", "move", "vanish", "region", ",", ":", "_", "x",
        "goal", "level", "temp", "\n", "\t",
    ]
)
near_grammar_text = st.lists(_grammar_tokens | st.text(max_size=3), max_size=12).map("".join)


@given(text=st.text() | near_grammar_text)
def test_any_text_parses_or_is_refused_with_a_reason(text):
    try:
        claims = parse_claims(text)
    except AssayError as error:
        assert str(error).strip()
        return
    assert text.strip()  # the empty prediction is always refused
    assert isinstance(claims, list) and claims
    for claim in claims:
        assert claim.kind in CLAIM_KINDS
        assert isinstance(claim.text, str) and claim.text
    # Whatever was said, the action commits to a mechanical claim.
    assert any(claim.kind not in NOT_MECHANICAL for claim in claims)


@pytest.mark.xfail(
    strict=True,
    raises=ValueError,
    reason="#26 found: an aggregate count of more than 4300 digits escapes as a "
    "ValueError (Python's int digit limit; the int() calls on the over and horizon "
    "groups in predictions.py are unguarded) instead of an AssayError refusal",
)
@pytest.mark.parametrize("field", ["over", "horizon"])
def test_aggregate_count_past_the_int_digit_limit_is_refused(field):
    digits = "1" * 4301
    over, horizon = (digits, "1") if field == "over" else ("1", digits)
    with pytest.raises(AssayError):
        parse_claims(f"noop; agg ch x mean >= 1 over {over}a horizon {horizon}a on-fail advise")


# --------------------------------------------------------------- the chain

SPEC_SEED = "assay-chain-v1"
# Every boundary str.splitlines() honors: a journal line contains none of them.
_LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85  "
_line = st.text(
    st.characters(codec="utf-8", exclude_characters=_LINE_BREAKS), min_size=1
).filter(str.strip)
_blank = st.sampled_from(["", " ", "\t "])


def spec_chain(lines: list[str]) -> str:
    """JOURNAL_SPEC.md section 4, as written:
    head_0 = SHA-256("assay-chain-v1"), head_n = SHA-256(hex(head_{n-1}) || line_n)."""
    head = hashlib.sha256(SPEC_SEED.encode()).hexdigest()
    for line in lines:
        head = hashlib.sha256((head + line).encode()).hexdigest()
    return head


@pytest.fixture(scope="module")
def journal(tmp_path_factory) -> RunPaths:
    run_paths = RunPaths(tmp_path_factory.mktemp("properties"))
    run_paths.state.mkdir(parents=True, exist_ok=True)
    return run_paths


def kernel_chain(rows: list[str]) -> tuple[int, str]:
    """The kernel's chain over these raw journal rows (`integrity.chain_over`,
    the rule the loader applies line by line and `verify_disk` over the
    file): (last event id, head)."""
    return sum(1 for row in rows if row.strip()) - 1, chain_over(rows)


def test_both_implementations_seed_with_the_spec_string():
    assert CHAIN_SEED == SPEC_SEED
    assert assay_verify.CHAIN_SEED == SPEC_SEED


def test_empty_journal_head_is_the_seed_hash():
    assert kernel_chain([]) == (-1, spec_chain([]))
    assert assay_verify.compute_chain([]) == spec_chain([])


@given(rows=st.lists(_line | _blank, max_size=12))
def test_kernel_checker_and_spec_chains_agree(journal, rows):
    lines = [row for row in rows if row.strip()]  # blank lines are skipped
    last_id, head = kernel_chain(rows)
    assert last_id == len(lines) - 1
    assert head == spec_chain(lines)
    assert head == assay_verify.compute_chain(lines)
    journal.events.write_text("".join(row + "\n" for row in rows))
    assert assay_verify.read_lines(journal.events) == lines
    # `verify_disk` hashes the file's bytes without decoding them: the same head.
    assert chain_over_bytes(journal.events.read_bytes()) == head


def _assert_heads_differ(lines: list[str], mutated: list[str]) -> None:
    assert spec_chain(mutated) != spec_chain(lines)
    assert assay_verify.compute_chain(mutated) != assay_verify.compute_chain(lines)
    assert kernel_chain(mutated)[1] != kernel_chain(lines)[1]


@given(lines=st.lists(_line, min_size=1, max_size=12), data=st.data())
def test_changing_one_character_changes_the_head(lines, data):
    # UTF-8 is injective, so a changed character is a changed byte of the
    # line as stored; a flipped byte that still decodes is one of these.
    index = data.draw(st.integers(0, len(lines) - 1), label="line")
    line = lines[index]
    position = data.draw(st.integers(0, len(line) - 1), label="position")
    replacement = data.draw(
        st.characters(codec="utf-8", exclude_characters=_LINE_BREAKS + line[position]),
        label="replacement",
    )
    changed = line[:position] + replacement + line[position + 1 :]
    assume(changed.strip())  # a line blanked out is a line removed, below
    _assert_heads_differ(lines, [*lines[:index], changed, *lines[index + 1 :]])


@given(lines=st.lists(_line, min_size=1, max_size=12), data=st.data())
def test_deleting_one_line_changes_the_head(lines, data):
    index = data.draw(st.integers(0, len(lines) - 1), label="line")
    _assert_heads_differ(lines, [*lines[:index], *lines[index + 1 :]])


@given(lines=st.lists(_line, min_size=2, max_size=12, unique=True), data=st.data())
def test_swapping_two_adjacent_lines_changes_the_head(lines, data):
    index = data.draw(st.integers(0, len(lines) - 2), label="first of the pair")
    mutated = list(lines)
    mutated[index], mutated[index + 1] = mutated[index + 1], mutated[index]
    _assert_heads_differ(lines, mutated)


@given(lines=st.lists(_line, max_size=12), extra=_line)
def test_appending_a_line_changes_the_head(lines, extra):
    _assert_heads_differ(lines, [*lines, extra])
    # and extends the chain by exactly the two-line rule
    head = hashlib.sha256((spec_chain(lines) + extra).encode()).hexdigest()
    assert kernel_chain([*lines, extra]) == (len(lines), head)


# ---------------------------------------------------------- the ungated rule

_event_shapes = st.fixed_dictionaries(
    {},
    optional={
        "counts_action": st.sampled_from([True, False, None]),
        "action": st.sampled_from(["RESET", "GO", "HEAT", "START", "reset"]),
        "predict": st.sampled_from([None, "", "change", "noop; ch temp delta sign +"]),
        "predict_ok": st.sampled_from([None, True, False]),
        "grade": st.sampled_from(
            [None, [], [{"kind": "change", "ok": True, "bucket": "world_model"}]]
        ),
    },
)
journals = st.lists(_event_shapes, max_size=12).map(
    lambda shapes: [{"id": index, **shape} for index, shape in enumerate(shapes)]
)


def _kernel_events(events: list[dict[str, Any]]) -> list[Any]:
    """The generated shapes as the kernel's typed events. The checker reads
    the journal fields as JSON, so a generated shape may lack a key or carry a
    null `counts_action`; the typed record carries every required key and a
    boolean, so an absent or null `counts_action` becomes false, the value the
    checker and the spec read it as, and a generated grade gets the journal's
    required keys around the ones the rule looks at."""
    kernel: list[Any] = []
    for event in events:
        fields: dict[str, Any] = {
            "id": event["id"],
            "action": event.get("action", "GO"),
            "counts_action": bool(event.get("counts_action")),
        }
        for key in ("predict", "predict_ok"):
            if key in event:
                fields[key] = event[key]
        if "grade" in event:
            grade = event["grade"]
            fields["grade"] = (
                None if grade is None
                else [{"text": "change", "actual": "", **item} for item in grade]
            )
        kernel.append(event_of(**fields))
    return kernel


def spec_ungated(events: list[dict[str, Any]]) -> list[int]:
    """JOURNAL_SPEC.md section 6: paid, not RESET, carrying none of predict,
    predict_ok, grade. Both implementations read "carries" as a non-null,
    non-empty value: a null or empty predict or grade is no claim machinery,
    and a predict_ok of false is (the claim was graded and missed)."""
    flagged: list[int] = []
    for event in events:
        paid = bool(event.get("counts_action"))
        exempt = event.get("action") == "RESET"
        carries = (
            bool(event.get("predict"))
            or event.get("predict_ok") is not None
            or bool(event.get("grade"))
        )
        if paid and not exempt and not carries:
            flagged.append(event["id"])
    return flagged


@given(events=journals)
def test_kernel_and_checker_agree_on_ungated_events(events):
    flagged = ungated_events(_kernel_events(events))
    assert flagged == assay_verify.ungated_events(events)
    assert flagged == spec_ungated(events)


@given(events=journals, data=st.data())
def test_an_injected_bare_paid_event_is_always_flagged(events, data):
    # Injecting a paid, claim-free action anywhere adds exactly its id, even
    # when the chain is recomputed around it (test_verify_checker's attack).
    position = data.draw(st.integers(0, len(events)), label="position")
    before = ungated_events(_kernel_events(events))
    bare = {"action": "GO", "counts_action": True, "note": "injected"}
    rewritten = [
        {**event, "id": index}
        for index, event in enumerate([*events[:position], bare, *events[position:]])
    ]
    expected = sorted(
        {index if index < position else index + 1 for index in before} | {position}
    )
    assert ungated_events(_kernel_events(rewritten)) == expected
    assert assay_verify.ungated_events(rewritten) == expected


# ------------------------------------------------------ registry parameters

_action_name = st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,31}", fullmatch=True).filter(
    lambda name: name.upper() != "RESET"  # built in, never registered
)
_param_name = st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,31}", fullmatch=True)
_token = st.text().filter(lambda text: not any(char.isspace() for char in text))
_finite_float = st.floats(allow_nan=False, allow_infinity=False)
_shaped_values = st.sampled_from(
    [
        "0", "-1", "1", "42", "3.5", "-0.0", "1e3", "1E-2", "nan", "NaN", "inf",
        "-inf", "+inf", "Infinity", "1e999", "0x10", "1_000", "+7", "true", "",
        "on", "off", "٣", "１２",  # an Arabic-Indic and two fullwidth digits
    ]
)
_value_text = _token | _shaped_values | st.integers().map(str) | _finite_float.map(repr)


@st.composite
def param_schemas(draw) -> dict[str, Any]:
    """A parameter schema the registry accepts: int or float, optionally
    bounded, str, each optionally with an enum of its own type."""
    kind = draw(st.sampled_from(["int", "float", "str"]))
    schema: dict[str, Any] = {"type": kind}
    bounds = st.integers(-1000, 1000) if kind == "int" else st.integers(-1000, 1000) | _finite_float
    if kind != "str":
        low = draw(st.none() | bounds)
        high = draw(st.none() | bounds)
        if low is not None and high is not None and low > high:
            low, high = high, low
        if low is not None:
            schema["min"] = low
        if high is not None:
            schema["max"] = high
    if draw(st.booleans()):
        members = {"int": st.integers(-1000, 1000), "float": bounds, "str": _token}[kind]
        schema["enum"] = draw(st.lists(members, min_size=1, max_size=5))
    return schema


def registry_with(name: str, pname: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The canonical registry, built the way the kernel builds it."""
    return validate_registry({"actions": [{"name": name, "params": {pname: schema}}]})


def assert_satisfies(schema: dict[str, Any], value: Any) -> None:
    kind = schema["type"]
    if kind == "int":
        assert isinstance(value, int) and not isinstance(value, bool)
    elif kind == "float":
        assert isinstance(value, float) and math.isfinite(value)
    else:
        assert isinstance(value, str)
    if "min" in schema:
        assert value >= schema["min"]
    if "max" in schema:
        assert value <= schema["max"]
    if "enum" in schema:
        assert value in schema["enum"]


@given(name=_action_name, pname=_param_name, schema=param_schemas(), value=_value_text)
def test_registry_value_is_coerced_to_the_schema_or_refused(name, pname, schema, value):
    registry = registry_with(name, pname, schema)
    canonical = registry["actions"][0]["params"][pname]
    try:
        parsed_name, params = parse_registry_action(f"{name} {pname}={value}", registry)
    except AssayError as error:
        assert str(error).strip()
        return
    assert parsed_name == name.upper()
    assert set(params) == {pname}
    assert_satisfies(canonical, params[pname])


@given(
    name=_action_name,
    pname=_param_name,
    low=st.integers(-10**9, 10**9),
    span=st.integers(0, 10**6),
    data=st.data(),
)
def test_registry_int_within_bounds_round_trips_and_outside_is_refused(
    name, pname, low, span, data
):
    high = low + span
    registry = registry_with(name, pname, {"type": "int", "min": low, "max": high})
    value = data.draw(st.integers(low, high), label="within bounds")
    assert parse_registry_action(f"{name} {pname}={value}", registry) == (
        name.upper(),
        {pname: value},
    )
    step = data.draw(st.integers(1, 10**6), label="distance outside")
    with pytest.raises(AssayError, match="below min"):
        parse_registry_action(f"{name} {pname}={low - step}", registry)
    with pytest.raises(AssayError, match="above max"):
        parse_registry_action(f"{name} {pname}={high + step}", registry)


@given(
    name=_action_name,
    pname=_param_name,
    value=st.floats(-1e300, 1e300, allow_nan=False, allow_infinity=False),
    below=st.floats(0, 1e300, allow_nan=False, allow_infinity=False),
    above=st.floats(0, 1e300, allow_nan=False, allow_infinity=False),
)
def test_registry_finite_float_within_bounds_round_trips(name, pname, value, below, above):
    schema = {"type": "float", "min": value - below, "max": value + above}
    registry = registry_with(name, pname, schema)
    parsed_name, params = parse_registry_action(f"{name} {pname}={value!r}", registry)
    assert parsed_name == name.upper()
    assert params == {pname: value} and isinstance(params[pname], float)


@given(
    name=_action_name,
    pname=_param_name,
    schema=st.sampled_from(
        [{"type": "float"}, {"type": "float", "min": -1.0}, {"type": "float", "max": 1.0}]
    ),
    text=st.sampled_from(
        ["nan", "NaN", "NAN", "inf", "-inf", "+inf", "Inf", "infinity", "-Infinity"]
        + ["1e999", "-1e999"]  # float() overflows these to infinity without complaint
    ),
)
def test_registry_refuses_nan_and_inf_floats(name, pname, schema, text):
    with pytest.raises(AssayError, match="finite"):
        parse_registry_action(f"{name} {pname}={text}", registry_with(name, pname, schema))


@given(
    name=_action_name,
    pname=_param_name,
    low=_finite_float,
    span=st.floats(0, 1e300, allow_nan=False, allow_infinity=False),
    step=st.floats(min_value=1e-300, max_value=1e300, allow_nan=False, allow_infinity=False),
)
def test_registry_float_outside_bounds_is_refused(name, pname, low, span, step):
    high = low + span
    assume(math.isfinite(high) and math.isfinite(low - step) and math.isfinite(high + step))
    assume(low - step < low and high + step > high)  # a step too small to register is no step
    registry = registry_with(name, pname, {"type": "float", "min": low, "max": high})
    with pytest.raises(AssayError, match="below min"):
        parse_registry_action(f"{name} {pname}={low - step!r}", registry)
    with pytest.raises(AssayError, match="above max"):
        parse_registry_action(f"{name} {pname}={high + step!r}", registry)


@given(name=_action_name, pname=_param_name, data=st.data())
def test_registry_enum_members_round_trip_and_strangers_are_refused(name, pname, data):
    kind = data.draw(st.sampled_from(["int", "float", "str"]), label="type")
    members_of = {
        "int": st.integers(-1000, 1000),
        "float": st.integers(-1000, 1000) | _finite_float,
        "str": _token,
    }
    members = data.draw(st.lists(members_of[kind], min_size=1, max_size=5), label="enum")
    registry = registry_with(name, pname, {"type": kind, "enum": members})
    member = data.draw(st.sampled_from(members), label="member")
    text = repr(member) if isinstance(member, float) else str(member)
    parsed_name, params = parse_registry_action(f"{name} {pname}={text}", registry)
    assert parsed_name == name.upper()
    assert params[pname] == member
    assert_satisfies(registry["actions"][0]["params"][pname], params[pname])
    stranger = data.draw(_value_text, label="stranger")
    try:
        _, params = parse_registry_action(f"{name} {pname}={stranger}", registry)
    except AssayError:
        return
    assert params[pname] in members


@given(name=_action_name, pname=_param_name, value=_token)
def test_registry_str_without_enum_round_trips_any_token(name, pname, value):
    registry = registry_with(name, pname, {"type": "str"})
    assert parse_registry_action(f"{name} {pname}={value}", registry) == (
        name.upper(),
        {pname: value},
    )
