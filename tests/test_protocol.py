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
        assert caught.value.code == "PROTOCOL_MALFORMED" and caught.value.kind == "internal", raw


def test_the_daemon_checks_the_version_before_the_token_and_the_operation(tmp_path):
    run = tmp_path / "version"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        for line in (
            {"op": "ping", "args": {}},
            {"v": 1, "op": "ping", "args": {}},
            {"v": "2", "op": "ping", "args": {}},
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
        assert malformed["error"]["code"] == "MALFORMED_REQUEST"
        wrong = _raw(run, {"v": 2, "op": "ping", "token": "nope"})
        assert wrong["error"]["code"] == "BROKER_TOKEN" and wrong["error"]["kind"] == "usage"
        unknown = _raw(run, {"v": 2, "op": "step", "args": {}})
        assert unknown["error"]["code"] == "UNKNOWN_OPERATION"
        assert unknown["error"]["hint"] == "the operations are ping, observe, act, commit, reset, install_module"
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
            ({"action": "RESET", "params": None, "predict": "change"}, "BATCH_FORBIDDEN", "use `assay reset`; reset cannot hide inside a batch"),
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
        assert str(caught.value) == 'each step needs its own prediction: --step "NAME pname=value :: <claims>"'
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
        assert refused.stderr == "ERROR | ACTION_PARAMS | INC amount='9' is above max 2\n"
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
            'ERROR | PREDICTION_REQUIRED | each step needs its own prediction: --step "NAME pname=value :: <claims>"\n'
        )
        empty = run_cli(run, "commit", "--step", ":: noop")
        assert empty.returncode == 2 and empty.stderr.startswith("ERROR | PREDICTION_REQUIRED | each step needs")
        assert len(_events(run)) == 4
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
