"""The versioned protocol (docs/ARCHITECTURE.md section 7.2): `v` on every
request and reply, checked by the daemon before anything else and by the
client on every reply; `action` and `params` as JSON, the object validated by
the daemon against the pinned registry before any spend and journaled under
`data`; commit steps as `{action, params, predict}` objects; the error object
raised by the client as the AssayError the daemon raised."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
    {"name": "NOOP", "params": {}},
]
# The structured action of #14, served by the fake adapter: an array of
# objects, each an `inc` with its amount or a `lamp` with its state.
APPLY = {
    "name": "APPLY",
    "params": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["inc", "lamp"]},
                    "amount": {"type": "integer", "minimum": 1, "maximum": 2},
                    "state": {"type": "string", "enum": ["on", "off"]},
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
            "minItems": 1,
            "maxItems": 3,
        }
    },
}
# The pinned copy of a registry is written with its keys sorted, so the form
# lists the properties in that order.
APPLY_FORM = (
    "APPLY --params '{\"ops\": [{\"amount\"?: <integer 1..2>, \"kind\": <inc|lamp>, "
    "\"state\"?: <on|off>}, ... 1..3 items]}'"
)


def _prepare(run: Path, **extra) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}, **extra}))


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory", "--registry", str(run / "reg.json"),
    )


def _events(run: Path) -> list[dict]:
    return [json.loads(line) for line in (run / ".assay" / "events.jsonl").read_text().splitlines() if line.strip()]


def _raw(run: Path, line: dict) -> dict:
    """One line on the socket as given, the reply as JSON: the client's
    transport without its framing, so a request without `v` can be sent."""
    import socket

    from assay.core import RunPaths

    paths = RunPaths(run)
    token = (paths.state / "broker.token").read_text().strip()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10.0)
        client.connect(str(paths.socket))
        client.sendall(json.dumps({"token": token, **line}).encode() + b"\n")
        chunks = b""
        while b"\n" not in chunks:
            chunk = client.recv(1 << 20)
            if not chunk:
                break
            chunks += chunk
    reply = json.loads(chunks.splitlines()[0])
    assert isinstance(reply, dict)
    return reply


def test_the_client_refuses_a_reply_without_the_version_before_reading_it():
    from assay.broker import _decode_reply
    from assay.core import AssayError

    # An old daemon answers in the flat shape, with no `v`: refused with the
    # hint to stop and start it, whatever else the line says.
    with pytest.raises(AssayError) as caught:
        _decode_reply(b'{"ok": true, "pong": true, "chain_event": 1, "chain_head": "a", "tampered": null}\n')
    assert caught.value.code == "PROTOCOL_VERSION" and caught.value.kind == "usage"
    assert str(caught.value) == "the environment owner carries no protocol version; this package speaks 2"
    assert caught.value.hint == "stop the daemon with `assay stop` and start it again with `assay start WORLD_ID`"
    with pytest.raises(AssayError, match="speaks protocol version 3; this package speaks 2") as caught:
        _decode_reply(b'{"v": 3, "ok": false, "error": "x"}\n')
    assert caught.value.code == "PROTOCOL_VERSION"
    # The version is the integer 2: a float or a boolean that compares equal is not.
    for raw in (b'{"v": 2.0, "ok": true, "result": {}}\n', b'{"v": true, "ok": true, "result": {}}\n'):
        with pytest.raises(AssayError) as caught:
            _decode_reply(raw)
        assert caught.value.code == "PROTOCOL_VERSION", raw
    # The error object is raised as the same AssayError.
    with pytest.raises(AssayError, match="^guard failed$") as caught:
        _decode_reply(
            b'{"v": 2, "ok": false, "error": {"code": "EVENT_GUARD", "kind": "refused", '
            b'"message": "guard failed", "hint": "rerun status"}}\n'
        )
    assert (caught.value.code, caught.value.kind, caught.value.hint) == ("EVENT_GUARD", "refused", "rerun status")
    assert _decode_reply(b'{"v": 2, "ok": true, "result": {"pong": true}}\n') == {"pong": True}
    for raw in (b"", b"\n", b"not json\n", b"[1]\n", b'{"v": 2, "ok": true}\n'):
        with pytest.raises(AssayError) as caught:
            _decode_reply(raw)
        assert caught.value.code == "REPLY_MALFORMED" and caught.value.kind == "internal", raw


def test_the_daemon_checks_the_version_before_the_token_and_the_operation(tmp_path):
    run = tmp_path / "version"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        for line in (
            {"op": "ping", "args": {}},
            {"v": 1, "op": "ping", "args": {}},
            {"v": "2", "op": "ping", "args": {}},
            {"v": 2.0, "op": "ping", "args": {}},
            {"v": 1, "op": "nope", "token": "wrong"},
            {"v": 1, "op": "act", "args": {"action": "NOOP", "predict": "noop"}},
        ):
            reply = _raw(run, line)
            assert reply["v"] == 2 and reply["ok"] is False, line
            assert reply["error"]["code"] == "PROTOCOL_VERSION" and reply["error"]["kind"] == "usage", line
            assert reply["error"]["message"].endswith("; this package speaks 2")
        assert reply["error"]["message"] == "the client speaks protocol version 1; this package speaks 2"
        assert _raw(run, {"op": "ping"})["error"]["message"] == (
            "the client carries no protocol version; this package speaks 2"
        )
        # The reply is versioned too, and `ping`'s result is the record as
        # it was, under `result`.
        reply = _raw(run, {"v": 2, "op": "ping", "args": {}})
        assert reply["v"] == 2 and reply["ok"] is True
        assert set(reply["result"]) == {"pong", "chain_event", "chain_head", "tampered"}
        assert reply["result"]["pong"] is True and reply["result"]["chain_event"] == 0
        # No args is an empty record; args of another shape are refused.
        assert _raw(run, {"v": 2, "op": "ping"})["ok"] is True
        malformed = _raw(run, {"v": 2, "op": "ping", "args": [1]})
        assert malformed["error"]["code"] == "REQUEST_MALFORMED" and malformed["error"]["kind"] == "usage"
        # A body that does not fit the record: the kit's message, the
        # record's fields as the next step, no traceback in the log.
        body = _raw(run, {"v": 2, "op": "act", "args": {"predict": "noop"}})
        assert body["error"]["code"] == "REQUEST_MALFORMED" and body["error"]["message"] == "act.action is required"
        assert body["error"]["hint"] == (
            "the act request takes action, params, predict, because, at_event, declares "
            "(required: action), the fields of ops.ActRequest.json_schema()"
        )
        typed = _raw(run, {"v": 2, "op": "act", "args": {"action": 7}})
        assert typed["error"]["message"] == "act.action must be a string, got int"
        assert "Traceback" not in (run / ".assay" / "broker.log").read_text()
        wrong = _raw(run, {"v": 2, "op": "ping", "token": "nope"})
        assert wrong["error"]["code"] == "DAEMON_TOKEN" and wrong["error"]["kind"] == "refused"
        unknown = _raw(run, {"v": 2, "op": "step", "args": {}})
        assert unknown["error"]["code"] == "OPERATION_UNKNOWN"
        assert unknown["error"]["hint"] == (
            "the operations are ping, observe, act, commit, reset, install_module, approve, waive, goal_ratify"
        )
        assert len(_events(run)) == 1
        assert not (run / ".assay" / "mutations.jsonl").exists()
    finally:
        stop_run(run)


def test_the_daemon_validates_the_params_object_against_the_registry(tmp_path):
    """The client sends the name and the object; the daemon checks the
    object against the same schema before any spend, so a client that
    speaks the socket directly cannot slip a wrong type, a bound or a
    stranger past the registry."""
    from assay.broker import _request
    from assay.core import AssayError, RunPaths

    run = tmp_path / "params"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        paths = RunPaths(run)
        refusals = [
            ({"action": "INC", "params": {"amount": "1"}, "predict": "change"}, "ACTION_PARAMS", "INC amount='1' is not an integer"),
            ({"action": "INC", "params": {"amount": True}, "predict": "change"}, "ACTION_PARAMS", "INC amount=True is not an integer"),
            ({"action": "INC", "params": {"amount": 5}, "predict": "change"}, "ACTION_PARAMS", "INC amount=5 is above max 2"),
            ({"action": "INC", "params": {"amount": 1.5}, "predict": "change"}, "ACTION_PARAMS", "INC amount=1.5 is not an integer"),
            ({"action": "INC", "params": None, "predict": "change"}, "ACTION_PARAMS", "INC is missing parameter(s): ['amount']"),
            ({"action": "INC", "params": {"amount": 1, "extra": 1}, "predict": "change"}, "ACTION_PARAMS", "INC has no parameter 'extra'; it takes ['amount']"),
            ({"action": "SET_LAMP", "params": {"state": "blue"}, "predict": "change"}, "ACTION_PARAMS", "SET_LAMP state='blue' is not one of ['on', 'off']"),
            ({"action": "SET_LAMP", "params": {"state": 1}, "predict": "change"}, "ACTION_PARAMS", "SET_LAMP state=1 is not a string"),
            ({"action": "BOGUS", "params": None, "predict": "change"}, "ACTION_UNKNOWN", "unknown action 'BOGUS'; registered actions: ['INC', 'NOOP', 'SET_LAMP'] (plus built-in RESET)"),
            ({"action": "RESET", "params": {"x": 1}, "predict": "change"}, "ACTION_PARAMS", "RESET takes no parameters"),
            ({"action": "RESET", "params": None, "predict": "change"}, "BATCH_FORBIDDEN", "reset cannot hide inside a batch"),
            ({"action": "", "params": None, "predict": "change"}, "ACTION_PARAMS", "empty action name"),
        ]
        for args, code, message in refusals:
            with pytest.raises(AssayError) as caught:
                _request(paths, "act", args, timeout=10.0)
            assert caught.value.code == code, args
            assert str(caught.value) == message, args
        assert len(_events(run)) == 1
        assert not (run / ".assay" / "mutations.jsonl").exists()
        # The name folds like a typed token's, and the validated object is
        # what the journal stores under `data`.
        result = _request(paths, "act", {"action": "inc", "params": {"amount": 2}, "predict": "change"}, timeout=10.0)
        assert result["receipt"]["outcome"] == "PREDICTED" and result["receipt"]["action"] == "INC amount=2"
        event = _events(run)[-1]
        assert event["action"] == "INC" and event["data"] == {"amount": 2}
        # A batch of step objects, validated the same way before the first
        # step spends: the bad second step refuses the whole batch.
        with pytest.raises(AssayError) as caught:
            _request(
                paths, "commit",
                {"steps": [
                    {"action": "NOOP", "params": None, "predict": "noop"},
                    {"action": "SET_LAMP", "params": {"state": "dim"}, "predict": "change"},
                ]},
                timeout=10.0,
            )
        assert caught.value.code == "ACTION_PARAMS" and "state='dim' is not one of" in str(caught.value)
        assert len(_events(run)) == 2
        with pytest.raises(AssayError) as caught:
            _request(paths, "commit", {"steps": [{"action": "NOOP", "params": None, "predict": None}]}, timeout=10.0)
        assert caught.value.code == "PREDICTION_REQUIRED"
        assert str(caught.value) == (
            'each step needs its own prediction: --step "NAME pname=value :: <claims>" '
            'or "predict" in the step object'
        )
        result = _request(
            paths, "commit",
            {"steps": [
                {"action": "set_lamp", "params": {"state": "on"}, "predict": "change"},
                {"action": "NOOP", "predict": "noop"},
            ]},
            timeout=10.0,
        )
        assert result["receipt"]["outcome"] == "PREDICTED"
        assert [step["action"] for step in result["receipt"]["steps"]] == ["SET_LAMP state=on", "NOOP"]
        assert _events(run)[-2]["data"] == {"state": "on"} and _events(run)[-1]["data"] is None
    finally:
        stop_run(run)


def test_the_command_line_parses_the_token_and_the_step_syntax_for_the_wire(tmp_path):
    """`assay act NAME k=v` and `--step "NAME k=v :: claims"` are parsed by
    the client against the pinned registry; the journal is as it was."""
    run = tmp_path / "cli"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        acted = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert acted.returncode == 0, acted.stderr
        assert "OUTCOME | PREDICTED" in acted.stdout
        assert _events(run)[-1]["data"] == {"amount": 1}
        refused = run_cli(run, "act", "INC", "amount=9", "--predict", "change")
        assert refused.returncode == 2
        assert refused.stderr == (
            "ERROR | ACTION_PARAMS | INC amount='9' is above max 2\nNEXT | the form is `INC amount=<int 1..2>`\n"
        )
        unknown = run_cli(run, "act", "BOGUS", "--predict", "change")
        assert unknown.returncode == 2
        assert unknown.stderr.startswith("ERROR | ACTION_UNKNOWN | unknown action 'BOGUS'; registered actions: ")
        assert "NEXT | the REGISTRY block of `assay status` lists the actions and their schemas" in unknown.stderr
        committed = run_cli(run, "commit", "--step", "SET_LAMP state=on :: change", "--step", "NOOP :: noop")
        assert committed.returncode == 0, committed.stderr
        assert "OUTCOME | PREDICTED | all 2 steps landed as predicted" in committed.stdout
        assert _events(run)[-2]["data"] == {"state": "on"}
        bare = run_cli(run, "commit", "--step", "NOOP")
        assert bare.returncode == 2
        assert bare.stderr == (
            'ERROR | PREDICTION_REQUIRED | each step needs its own prediction: --step "NAME pname=value :: <claims>" '
            'or "predict" in the step object\n'
            "NEXT | `assay act --help` lists the claim forms\n"
        )
        empty = run_cli(run, "commit", "--step", ":: noop")
        assert empty.returncode == 2 and empty.stderr.startswith("ERROR | PREDICTION_REQUIRED | each step needs")
        assert len(_events(run)) == 4
    finally:
        stop_run(run)


def test_params_json_carries_structured_values_through_the_cli_and_the_daemon(tmp_path):
    """`assay act NAME --params JSON` and `--params @FILE` (#14): the object
    validated by the client and again by the daemon, nested values refused by
    their path before any spend, the journal holding the value as given and
    the receipt rendering it as JSON."""
    run = tmp_path / "params-json"
    _prepare(run, actions=[*ACTIONS, APPLY])
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        assert "  APPLY ops=<array of 1..3 object>" in started.stdout
        assert f"    form: {APPLY_FORM}" in started.stdout
        # A scalar through --params is coerced like a token's value.
        acted = run_cli(run, "act", "INC", "--params", '{"amount": 1}', "--predict", "change")
        assert acted.returncode == 0, acted.stderr
        assert _events(run)[-1]["data"] == {"amount": 1}
        # Every refusal is free and names the path, the value and the form.
        for arguments, code, message in (
            (["APPLY", "--params", '{"ops": [{"kind": "inc", "amount": 5}]}'], "ACTION_PARAMS",
             "APPLY ops[0].amount=5 is above max 2"),
            (["APPLY", "--params", '{"ops": [{"kind": "inc", "extra": 1}]}'], "ACTION_PARAMS",
             "APPLY ops[0]={\"extra\":1,\"kind\":\"inc\"} has no property 'extra'; it takes ['amount', 'kind', 'state']"),
            (["APPLY", "--params", '{"ops": [{"amount": 1}]}'], "ACTION_PARAMS",
             "APPLY ops[0]={\"amount\":1} is missing property(ies): ['kind']"),
            (["APPLY", "--params", '{"ops": [{"kind": "inc"}, {"kind": "inc"}, {"kind": "inc"}, {"kind": "inc"}]}'],
             "ACTION_PARAMS",
             "APPLY ops=[{\"kind\":\"inc\"},{\"kind\":\"inc\"},{\"kind\":\"inc\"},{\"kind\":\"inc\"}] has 4 item(s), above maxItems 3"),
            (["APPLY", "--params", '{"ops": {"kind": "inc"}}'], "ACTION_PARAMS",
             "APPLY ops={\"kind\":\"inc\"} is not an array"),
            (["APPLY", "--params", '{"ops": [{"kind": "dim"}]}'], "ACTION_PARAMS",
             "APPLY ops[0].kind='dim' is not one of ['inc', 'lamp']"),
            (["APPLY", "ops=x"], "ACTION_PARAMS", "APPLY ops is an array parameter and takes JSON, not a token"),
            (["APPLY", "--params", "{}"], "ACTION_PARAMS", "APPLY is missing parameter(s): ['ops']"),
        ):
            refused = run_cli(run, "act", *arguments, "--predict", "change")
            assert refused.returncode == 2, arguments
            assert refused.stderr == f"ERROR | {code} | {message}\nNEXT | the form is `{APPLY_FORM}`\n", arguments
        for arguments, message, hint in (
            (["INC", "amount=1", "--params", '{"amount": 1}'],
             "act takes the parameters either as pname=value tokens or as --params, not both",
             "pass the parameters as one JSON object, `--params '{\"pname\": value, ...}'`, or as `--params @FILE` holding one"),
            (["INC", "--params", "{amount: 1}"],
             "--params is not valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
             None),
            (["INC", "--params", "[1]"], "--params must be a JSON object, got an array", None),
            (["INC", "--params", "null"], "--params must be a JSON object, got null", None),
            (["INC", "--params", f"@{run / 'nowhere.json'}"],
             f"--params names a file that cannot be read: {run / 'nowhere.json'}: No such file or directory",
             None),
            # The wire's rules, applied here first: a repeated key, an integer
            # past the interpreter's digit limit, a nesting it cannot read (a
            # 20000-deep array on 3.12, 100000 on 3.14, within the size cap).
            (["INC", "--params", '{"amount": 1, "amount": 2}'],
             "--params is not accepted: the key 'amount' is repeated", None),
            (["INC", "--params", '{"amount": ' + "1" * 5000 + "}"],
             "--params is not accepted: Exceeds the limit (4300 digits) for integer string conversion", None),
            (["INC", "--params", '{"amount": ' + "[" * 100_000 + "]" * 100_000 + "}"],
             "--params is nested too deep", None),
        ):
            refused = run_cli(run, "act", *arguments, "--predict", "change")
            assert refused.returncode == 2, arguments
            assert refused.stderr.startswith(f"ERROR | COMMAND_ARGS | {message}"), refused.stderr
            if hint is not None:
                assert refused.stderr == f"ERROR | COMMAND_ARGS | {message}\nNEXT | {hint}\n"
        # A document past the request limit is refused before the socket.
        big = '{"amount": 1, "pad": "' + "a" * 1_000_000 + '"}'
        (run / "big.json").write_text(big)
        too_big = run_cli(run, "act", "INC", "--params", f"@{run / 'big.json'}", "--predict", "change")
        assert too_big.returncode == 2
        assert too_big.stderr.startswith(
            f"ERROR | COMMAND_ARGS | --params is {len(big.encode())} bytes; one request is at most 1000000 bytes\n"
        )
        assert len(_events(run)) == 2
        # A nested value, validated down to each property, journaled as given
        # (keys sorted) and rendered as compact JSON on the receipt.
        applied = run_cli(
            run, "act", "APPLY", "--params",
            '{"ops": [{"kind": "lamp", "state": "on"}, {"amount": 1, "kind": "inc"}]}',
            "--predict", "change", "--json",
        )
        assert applied.returncode == 0, applied.stderr
        receipt = json.loads(applied.stdout)
        assert receipt["outcome"] == "PREDICTED"
        assert receipt["action"] == 'APPLY ops=[{"kind":"lamp","state":"on"},{"amount":1,"kind":"inc"}]'
        event = _events(run)[-1]
        assert event["data"] == {"ops": [{"kind": "lamp", "state": "on"}, {"amount": 1, "kind": "inc"}]}
        assert event["observation"] == {"counter": 2, "lamp": "on"}
        status = run_cli(run, "status")
        assert '  e0002 a0002 L1 APPLY ops=[{"kind":"lamp","state":"on"},{"amount":1,"kind":"inc"}] ✓ |' in status.stdout
        viewed = run_cli(run, "view", "--event", "2")
        assert 'CAUSE | APPLY ops=[{"kind":"lamp","state":"on"},{"amount":1,"kind":"inc"}]' in viewed.stdout
        # The same object from a file.
        (run / "ops.json").write_text('{"ops": [{"kind": "inc", "amount": 1}]}\n')
        from_file = run_cli(run, "act", "APPLY", "--params", f"@{run / 'ops.json'}", "--predict", "win")
        assert from_file.returncode == 0, from_file.stderr
        assert "OUTCOME | GAME_COMPLETE" in from_file.stdout
        assert _events(run)[-1]["data"] == {"ops": [{"amount": 1, "kind": "inc"}]}
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout and "chain intact" in audited.stdout
    finally:
        stop_run(run)


def test_step_json_form_and_a_step_file_beside_the_string_form(tmp_path):
    """`--step '{"action", "params", "predict"}'` and `--step @FILE` holding
    a list of such objects (#14), beside `--step "NAME k=v :: claims"`."""
    run = tmp_path / "steps"
    _prepare(run, actions=[*ACTIONS, APPLY])
    try:
        assert _start(run).returncode == 0
        committed = run_cli(
            run, "commit",
            "--step", '{"action": "set_lamp", "params": {"state": "on"}, "predict": "change"}',
            "--step", "NOOP :: noop",
        )
        assert committed.returncode == 0, committed.stderr
        assert "OUTCOME | PREDICTED | all 2 steps landed as predicted" in committed.stdout
        assert _events(run)[-2]["action"] == "SET_LAMP" and _events(run)[-2]["data"] == {"state": "on"}
        hint = (
            'a step is `--step "NAME pname=value :: claims"`, or `--step \'{"action": "NAME", '
            "\"params\": {...}, \"predict\": \"claims\"}'`, or `--step @FILE` holding a list of such objects"
        )
        for step, code, message, next_step in (
            ('{"action": "NOOP", "bogus": 1, "predict": "noop"}', "COMMAND_ARGS", "step.bogus is not a field of the record", hint),
            ('{"params": {}, "predict": "noop"}', "COMMAND_ARGS", "step.action is required", hint),
            ('{"action": "NOOP", "params": "x"}', "COMMAND_ARGS", "step.params must be a JSON object, got str", hint),
            ('{"action": "NOOP"', "COMMAND_ARGS",
             "--step is not valid JSON: Expecting ',' delimiter: line 1 column 18 (char 17)", hint),
            ('{"action": "APPLY", "params": {"ops": []}, "predict": "noop"}', "ACTION_PARAMS",
             "APPLY ops=[] has 0 item(s), below minItems 1", f"the form is `{APPLY_FORM}`"),
            (f"@{run / 'nowhere.json'}", "COMMAND_ARGS",
             f"--step names a file that cannot be read: {run / 'nowhere.json'}: No such file or directory", hint),
        ):
            refused = run_cli(run, "commit", "--step", step)
            assert refused.returncode == 2, step
            assert refused.stderr == f"ERROR | {code} | {message}\nNEXT | {next_step}\n", refused.stderr
        assert len(_events(run)) == 3
        # An inline JSON list of steps, as a file would hold it.
        listed = run_cli(
            run, "commit", "--step",
            '[{"action": "NOOP", "predict": "noop"}, {"action": "SET_LAMP", "params": {"state": "off"}, "predict": "change"}]',
        )
        assert listed.returncode == 0, listed.stderr
        assert "OUTCOME | PREDICTED | all 2 steps landed as predicted" in listed.stdout
        assert "  e0003 NOOP ✓" in listed.stdout and "  e0004 SET_LAMP state=off ✓" in listed.stdout
        (run / "steps.json").write_text(json.dumps([
            {"action": "APPLY", "params": {"ops": [{"kind": "inc", "amount": 2}]}, "predict": "change"},
            {"action": "INC", "params": {"amount": 1}, "predict": "win"},
        ]))
        from_file = run_cli(run, "commit", "--step", f"@{run / 'steps.json'}")
        assert from_file.returncode == 0, from_file.stderr
        assert "OUTCOME | GAME_COMPLETE" in from_file.stdout
        assert '  e0005 APPLY ops=[{"amount":2,"kind":"inc"}] ✓' in from_file.stdout
        assert "  e0006 INC amount=1 ✓" in from_file.stdout
        assert _events(run)[-2]["data"] == {"ops": [{"amount": 2, "kind": "inc"}]}
    finally:
        stop_run(run)


def _raw_text(run: Path, text: bytes) -> dict:
    """Bytes on the socket as given, one line, the reply as JSON."""
    import socket

    from assay.core import RunPaths

    paths = RunPaths(run)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(60.0)
        client.connect(str(paths.socket))
        client.sendall(text + b"\n")
        chunks = b""
        while b"\n" not in chunks:
            chunk = client.recv(1 << 20)
            if not chunk:
                break
            chunks += chunk
    reply = json.loads(chunks.splitlines()[0])
    assert isinstance(reply, dict)
    return reply


def test_the_daemon_caps_the_request_line_and_refuses_a_repeated_key_and_deep_nesting(tmp_path):
    """The wire's rules (#14): a line past REQUEST_LIMIT_BYTES is refused
    before parsing and the connection answered, a repeated key and a nesting
    the interpreter cannot read are REQUEST_MALFORMED, never INTERNAL, and
    the daemon keeps serving with nothing spent."""
    run = tmp_path / "limits"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        token = (run / ".assay" / "broker.token").read_text().strip()
        head = f'{{"v": 2, "token": "{token}", "op": "act", "args": '
        long_line = (head + '{"action": "NOOP", "predict": "' + "x" * 1_100_000 + '"}}').encode()
        assert len(long_line) > 1_000_000
        reply = _raw_text(run, long_line)
        assert reply["v"] == 2 and reply["ok"] is False
        assert reply["error"]["code"] == "REQUEST_MALFORMED" and reply["error"]["kind"] == "usage"
        assert reply["error"]["message"] == "malformed request: a line of more than 1000000 bytes"
        reply = _raw_text(run, (head + '{"action": "NOOP", "action": "INC", "predict": "noop"}}').encode())
        assert reply["error"]["code"] == "REQUEST_MALFORMED"
        assert reply["error"]["message"] == "malformed request: the key 'action' is repeated"
        reply = _raw_text(run, (head + "[" * 100_000 + "]" * 100_000 + "}").encode())
        assert reply["error"]["code"] == "REQUEST_MALFORMED"
        assert reply["error"]["message"] == "malformed request: nested too deep"
        digits = head + '{"action": "INC", "params": {"amount": ' + "1" * 5000 + '}, "predict": "change"}}'
        reply = _raw_text(run, digits.encode())
        assert reply["error"]["code"] == "REQUEST_MALFORMED"
        assert reply["error"]["message"].startswith("malformed request: Exceeds the limit (4300 digits)")
        assert _raw(run, {"v": 2, "op": "ping", "args": {}})["ok"] is True
        assert len(_events(run)) == 1
        assert not (run / ".assay" / "mutations.jsonl").exists()
        assert "Traceback" not in (run / ".assay" / "broker.log").read_text()
    finally:
        stop_run(run)


def test_split_step_and_validate_action_agree_with_the_token_parser():
    from assay.core import AssayError
    from assay.live import split_step
    from assay.registry import parse_registry_action, validate_action, validate_registry

    registry = validate_registry({"actions": [
        {"name": "GO", "params": {"n": {"type": "int", "min": 0}, "r": {"type": "float", "max": 1.5}, "s": {"type": "str"}}},
    ]})
    assert split_step("GO n=1 r=0.5 s=x :: ch a = 1") == ("GO n=1 r=0.5 s=x", "ch a = 1")
    assert split_step("GO n=1 r=0.5 s=x") == ("GO n=1 r=0.5 s=x", None)
    assert split_step("GO n=1 r=0.5 s=x ::") == ("GO n=1 r=0.5 s=x", None)
    assert split_step("  go n=1 r=1 s=Y  ::  noop ") == ("go n=1 r=1 s=Y", "noop")
    with pytest.raises(AssayError, match="each step needs its own prediction"):
        split_step(":: noop")
    parsed = parse_registry_action("go n=1 r=1 s=Y", registry)
    assert parsed == ("GO", {"n": 1, "r": 1.0, "s": "Y"})
    assert validate_action(registry, "go", {"n": 1, "r": 1, "s": "Y"}) == parsed
    assert isinstance(validate_action(registry, "GO", {"n": 1, "r": 1, "s": "Y"})[1]["r"], float)
    assert validate_action(registry, "reset", None) == ("RESET", None)
    for params, message in (
        ({"n": -1, "r": 1, "s": "Y"}, "GO n=-1 is below min 0"),
        ({"n": 1, "r": 2.0, "s": "Y"}, "GO r=2.0 is above max 1.5"),
        ({"n": 1, "r": float("inf"), "s": "Y"}, "GO r=inf must be finite"),
        ({"n": 1, "r": "1", "s": "Y"}, "GO r='1' is not a number"),
    ):
        with pytest.raises(AssayError, match=f"^{message.replace('(', '[(]').replace(')', '[)]')}$") as caught:
            validate_action(registry, "GO", params)
        assert caught.value.code == "ACTION_PARAMS"
