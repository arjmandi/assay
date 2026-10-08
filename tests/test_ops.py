"""The operation table (docs/ARCHITECTURE.md section 7.2): the operations
named once, the daemon's six with their request and result records, each
record round-tripping through the wire shape and describing itself with a
schema that names its fields, and an unknown operation refused by name."""

from __future__ import annotations

import pytest


def _daemon_operations():
    from assay.ops import operations

    return [operation for operation in operations() if not operation.offline]


def test_the_daemon_operations_and_their_flags():
    from assay.ops import operations

    names = [operation.name for operation in operations()]
    assert len(names) == len(set(names))
    daemon = {operation.name: operation for operation in _daemon_operations()}
    assert sorted(daemon) == ["act", "commit", "install_module", "observe", "ping", "reset"]
    assert {name for name, operation in daemon.items() if operation.paid} == {"act", "commit", "reset"}
    assert {name for name, operation in daemon.items() if operation.owner} == {"install_module"}
    for operation in daemon.values():
        assert operation.request is not None and operation.result is not None
        assert callable(operation.handler)


def test_an_unknown_operation_is_refused_by_name():
    from assay.core import AssayError
    from assay.ops import daemon_operation

    with pytest.raises(AssayError, match="^unknown broker operation 'step'$"):
        daemon_operation("step")
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
        ActRequest(action_token="INC amount=1", predict="change", because="why", at_event=4, declares={"worst_case": "x"}),
        ActRequest(action_token="NOOP"),
        CommitRequest(plan="@.assay/model_plan.json", at_event=4),
        CommitRequest(steps=("INC amount=1 :: change", "NOOP :: noop"), declares={}),
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


def test_request_records_read_the_wire_fields_of_today():
    from assay.ops import ActRequest, CommitRequest, ResetRequest

    act = ActRequest.from_json(
        {"action_token": "NOOP", "predict": None, "because": None, "at_event": None, "declares": {}}
    )
    assert act == ActRequest(action_token="NOOP", declares={})
    commit = CommitRequest.from_json({"plan": None, "steps": ["NOOP :: noop"], "at_event": 2, "declares": {}})
    assert commit.steps == ("NOOP :: noop",) and commit.plan is None and commit.at_event == 2
    assert CommitRequest.from_json({}).steps == ()
    assert ResetRequest.from_json({"because": "x"}) == ResetRequest(because="x")
    with pytest.raises(KeyError):
        ActRequest.from_json({"predict": "noop"})
    with pytest.raises(TypeError, match="act.action_token must be a string, got int"):
        ActRequest.from_json({"action_token": 7})
    with pytest.raises(TypeError, match="act.declares.k must be a string, got int"):
        ActRequest.from_json({"action_token": "NOOP", "declares": {"k": 1}})


def test_the_table_imports_lazily_and_binds_the_broker():
    from assay import broker
    from assay.ops import daemon_operation

    assert daemon_operation("act").handler is broker.serve_act
    assert daemon_operation("install_module").handler is broker.serve_install_module
