"""The wire table (docs/ARCHITECTURE.md section 7.2): the six daemon
operations named once with their request and result records; each record
round-trips through the wire shape, describes itself with a schema naming
its fields, and refuses a wrong type, a missing required key and a key it
does not take; the daemon binds a handler to every name and the command line
a command to the ones it exposes; an unknown operation is refused by name."""

from __future__ import annotations

import argparse

import pytest


def test_the_table_and_its_flags():
    from assay.ops import OPERATIONS

    assert [operation.name for operation in OPERATIONS] == [
        "ping", "observe", "act", "commit", "reset", "install_module",
    ]
    assert {operation.name for operation in OPERATIONS if operation.paid} == {"act", "commit", "reset"}
    assert {operation.name for operation in OPERATIONS if operation.owner} == {"install_module"}


def test_every_operation_has_a_handler_and_the_paid_ones_a_command():
    from assay import broker, cli
    from assay.ops import OPERATIONS

    assert set(broker.HANDLERS) == {operation.name for operation in OPERATIONS}
    for operation in OPERATIONS:
        assert broker.HANDLERS[operation.name].__name__ == f"serve_{operation.name}"
    exposed = {command.operation.name for command in cli.COMMANDS if command.operation is not None}
    assert exposed == {"act", "commit", "reset", "install_module"}


def test_commands_are_identifiers_with_unique_paths():
    from assay import cli
    from assay.core import AssayError

    names = [command.name for command in cli.COMMANDS] + [item.name for item in cli.LIFECYCLE]
    assert all(name.isidentifier() for name in names) and len(names) == len(set(names))
    paths = [command.path for command in cli.COMMANDS] + [item.name for item in cli.LIFECYCLE]
    assert len(paths) == len(set(paths))
    for command in cli.COMMANDS:
        words = command.path.split()
        assert len(words) in (1, 2) and (len(words) == 1 or words[0] in cli.GROUPS)
    assert cli.command_of(argparse.Namespace(command="channel", channel_command="declare")).name == "channel_declare"
    assert cli.command_of(argparse.Namespace(command="goal", goal_command="ratify")).name == "goal_ratify"
    assert cli.command_of(argparse.Namespace(command="status")).name == "status"
    with pytest.raises(AssayError, match="^unsupported command nope$"):
        cli.command_of(argparse.Namespace(command="nope"))


def test_an_unknown_operation_is_refused_by_name():
    from assay.core import AssayError
    from assay.ops import daemon_operation

    with pytest.raises(AssayError, match="^unknown broker operation 'step'$") as refused:
        daemon_operation("step")
    assert refused.value.code == "UNKNOWN_OPERATION" and refused.value.kind == "usage"
    with pytest.raises(AssayError, match="^unknown broker operation None$"):
        daemon_operation(None)
    assert daemon_operation("act").name == "act"


def _samples():
    from assay.ops import (
        ActRequest,
        CommitRequest,
        InstallModuleRequest,
        InstallModuleResult,
        ObserveRequest,
        ObserveResult,
        PingRequest,
        PingResult,
        ReceiptResult,
        ResetRequest,
        Step,
    )
    from assay.records import Receipt

    receipt = Receipt(kind="act", outcome="PREDICTED", detail="result matched the prediction",
                      start_event=1, end_event=2, grade=("✓ noop",), modules=("MODULE probe | fired",))
    return [
        PingRequest(),
        PingResult(pong=True, chain_event=3, chain_head="ab" * 32, tampered=None),
        PingResult(pong=True, chain_event=3, chain_head="ab" * 32, tampered="registry.json (held a, on disk b)"),
        ObserveRequest(),
        ObserveResult(observation={"state": "NOT_FINISHED", "data": {"counter": 1}}, public_info={"rooms": 2}),
        ActRequest(action="INC", params={"amount": 1}, predict="change", because="why", at_event=4, declares={"worst_case": "x"}),
        ActRequest(action="NOOP"),
        ActRequest(action="SET", params={"ratio": 0.5, "name": "x", "flag": True}),
        Step(action="INC", params={"amount": 1}, predict="change"),
        Step(action="NOOP"),
        CommitRequest(plan="@.assay/model_plan.json", at_event=4),
        CommitRequest(steps=(Step(action="INC", params={"amount": 1}, predict="change"), Step(action="NOOP", predict="noop")), declares={}),
        ResetRequest(because="stuck", at_event=4, declares=None),
        ReceiptResult(receipt=receipt),
        InstallModuleRequest(path="/tmp/probe.py", owner_token="t"),
        InstallModuleRequest(path="/tmp/probe.py"),
        InstallModuleResult(record={"name": "probe", "sha256": "0" * 64}),
    ]


@pytest.mark.parametrize("record", _samples(), ids=lambda record: type(record).__name__)
def test_records_round_trip_and_describe_themselves(record):
    wire = record.to_json()
    assert type(record).from_json(wire) == record
    schema = type(record).json_schema()
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(wire)
    assert set(schema["required"]) <= set(schema["properties"])
    for key in schema["required"]:
        assert key in wire
    # The schema says no other property, and the record means it.
    with pytest.raises(TypeError, match="\\.extra is not a field of the record$"):
        type(record).from_json({**wire, "extra": 1})


def test_request_records_read_the_wire_fields():
    from assay.ops import ActRequest, CommitRequest, ResetRequest, Step

    act = ActRequest.from_json(
        {"action": "NOOP", "params": None, "predict": None, "because": None, "at_event": None, "declares": {}}
    )
    assert act == ActRequest(action="NOOP", declares={})
    assert ActRequest.from_json({"action": "INC", "params": {"amount": 1}}).params == {"amount": 1}
    commit = CommitRequest.from_json(
        {"plan": None, "steps": [{"action": "NOOP", "params": None, "predict": "noop"}], "at_event": 2, "declares": {}}
    )
    assert commit.steps == (Step(action="NOOP", predict="noop"),) and commit.plan is None and commit.at_event == 2
    assert CommitRequest.from_json({}).steps == ()
    assert CommitRequest.from_json({"steps": [{"action": "NOOP"}]}).steps == (Step(action="NOOP"),)
    assert ResetRequest.from_json({"because": "x"}) == ResetRequest(because="x")
    with pytest.raises(KeyError):
        ActRequest.from_json({"predict": "noop"})
    with pytest.raises(TypeError, match="^act.action must be a string, got int$"):
        ActRequest.from_json({"action": 7})
    with pytest.raises(TypeError, match="^act.params must be a JSON object, got str$"):
        ActRequest.from_json({"action": "INC", "params": "amount=1"})
    with pytest.raises(TypeError, match="^act.params.amount must be a string, a number or true/false, got list$"):
        ActRequest.from_json({"action": "INC", "params": {"amount": [1]}})
    with pytest.raises(TypeError, match="^act.params.amount must be a string, a number or true/false, got null$"):
        ActRequest.from_json({"action": "INC", "params": {"amount": None}})
    with pytest.raises(TypeError, match="^act.declares.k must be a string, got int$"):
        ActRequest.from_json({"action": "NOOP", "declares": {"k": 1}})
    with pytest.raises(TypeError, match="^commit.steps must be a list of steps, got int$"):
        CommitRequest.from_json({"steps": [1]})
    with pytest.raises(TypeError, match="^step.predict must be a string, got int$"):
        CommitRequest.from_json({"steps": [{"action": "NOOP", "predict": 1}]})
    with pytest.raises(TypeError, match="^act.bogus is not a field of the record$"):
        ActRequest.from_json({"action": "NOOP", "bogus": 1})
    assert ActRequest.json_schema()["properties"]["params"]["type"] == ["object", "null"]
    assert CommitRequest.json_schema()["properties"]["steps"]["items"] == Step.json_schema()


def test_the_error_object_schema_names_the_kinds():
    from assay.core import AssayError
    from assay.errors import KINDS
    from assay.ops import ERROR_SCHEMA, PROTOCOL_VERSION

    assert PROTOCOL_VERSION == 2
    assert set(ERROR_SCHEMA["properties"]) == set(AssayError("x").to_json()) == {"code", "kind", "message", "hint"}
    assert ERROR_SCHEMA["properties"]["kind"]["enum"] == list(KINDS)


def test_the_wire_table_imports_neither_the_daemon_nor_the_command_line():
    import subprocess
    import sys

    from conftest import SRC_DIR

    probe = (
        "import sys, assay.ops; "
        "print(sorted(name for name in sys.modules if name in ('assay.broker', 'assay.cli', 'argparse')))"
    )
    completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=str(SRC_DIR), timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"
