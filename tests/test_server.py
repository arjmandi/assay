"""The tool server (docs/ARCHITECTURE.md section 7.5): one tool per
agent-facing operation over the same daemon, the command line a thin client
beside it. Without mcp installed: the table is the agent-facing operations
by the rule held against `ops.OPERATIONS` and `cli.COMMANDS` (the owner,
daemon-own, lifecycle and operator operations absent), the schemas are the
request records' own, with the surface's `format` and the per-run `predict`
requirement on the listed ones, the offline records decode like the wire
records, a call dispatches like the command line (the same renderers, the
same activity records with `surface: "mcp"` where the command line's say
`cli`, the text the command line prints by default and the `--json` document
under `format: "json"`), an unknown tool and a malformed request are refused
by name, the `python` tool runs the source in a child process that cannot
touch the server and is stopped at the wall clock, nothing an agent causes
leaves the worker, an offline tool runs without a daemon and a paid one
refuses, and `serve-tools` refuses without a run and without the extra. With
mcp installed, one scenario drives a run through the real server over stdio
with the mcp client, beside a step the command line takes through the same
daemon."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ASSAY_CLI, FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
    {"name": "NOOP", "params": {}},
]
TOOL_NAMES = [
    "status", "view", "act", "commit", "reset", "python", "state_declare", "state_list",
    "model_init", "model_replay", "model_solve", "module_list", "goal_propose", "goal_list", "audit",
]
OWNER_OPERATIONS = {"install_module", "approve", "waive", "goal_ratify"}
# The commands of the loaded run that are not tools: the owner's (they take
# the token the agent never holds) and the operator's side effects beyond
# the run (the knowledge export and the spend feed).
NOT_TOOLS = {"module_install", "goal_ratify", "approve", "waive", "export", "spend_report"}
DECLARED = "STATE | declared counter (path); outcomes like `ch counter = V` now parse and grade"
COUNTERS = "[t['after']['counter'] for t in transitions]"


def _prepare(run: Path) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}}))


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory", "--registry", str(run / "reg.json"),
    )


def _records(run: Path, kind: str) -> list[dict]:
    activity = run / ".assay" / "activity.jsonl"
    lines = activity.read_text().splitlines() if activity.exists() else []
    records = [json.loads(line) for line in lines if line.strip()]
    return [record for record in records if record["kind"] == kind]


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _running(token: str) -> bool:
    """Whether a process whose command line carries the token is alive."""
    return subprocess.run(["pgrep", "-f", token], capture_output=True, timeout=10).returncode == 0


def _receipt(run: Path, event: int):
    """The receipt the daemon wrote for the paid action that ended at this
    event, as the records read it."""
    from assay.records import Receipt

    files = sorted((run / ".assay" / "receipts").glob(f"*-e{event}.json"))
    return Receipt.from_json(json.loads(files[-1].read_text()))


# --- the table, without mcp ---------------------------------------------------------


def test_the_tool_table_is_the_agent_facing_operations():
    from assay import cli, server
    from assay.core import AssayError
    from assay.ops import OPERATIONS
    from assay.predictions import prediction_help

    names = [tool.name for tool in server.TOOLS]
    assert names == TOOL_NAMES
    # The rule against the wire table: every daemon operation is a tool or is
    # excluded by it, the owner flag excluding the owner's and the daemon's
    # own excluded by name; a new operation lands here deliberately or fails.
    forwarded = {tool.operation.name for tool in server.TOOLS if tool.operation is not None}
    assert forwarded == {op.name for op in OPERATIONS if server.agent_facing(op)} == {"act", "commit", "reset"}
    excluded = {op.name for op in OPERATIONS if not server.agent_facing(op)}
    assert excluded == OWNER_OPERATIONS | server.DAEMON_OWN
    assert server.DAEMON_OWN == {"ping", "observe"}
    assert {op.name for op in OPERATIONS if op.owner} == OWNER_OPERATIONS
    # The rule against the command line: a tool is what reads or advances the
    # agent's own run, under the command's own name; the commands that are
    # not tools are exactly the owner's and the operator's side effects
    # beyond the run, and no lifecycle command is one.
    commands = {command.name: command for command in cli.COMMANDS}
    assert set(names) <= set(commands)
    assert set(commands) - set(names) == NOT_TOOLS
    owner_commands = {c.name for c in cli.COMMANDS if c.operation is not None and c.operation.owner}
    assert NOT_TOOLS == owner_commands | server.OPERATOR_COMMANDS
    lifecycle = {item.name for item in cli.LIFECYCLE}
    assert "serve_tools" in lifecycle and not set(names) & lifecycle
    for tool in server.TOOLS:
        schema = tool.input_schema()
        assert schema == tool.request.json_schema()
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert "owner_token" not in schema["properties"] and "format" not in schema["properties"]
        assert tool.description and "\n" not in tool.description
        command = commands[tool.name]
        assert (tool.operation is not None) == (command.operation is not None)
        if tool.operation is not None:
            assert tool.request is tool.operation.request and tool.operation is command.operation
        # The listing's schema: the record's with the surface's `format`.
        listed = server.listed_schema(tool, None)
        assert listed["properties"]["format"] == server.FORMAT_PROPERTY
        assert {**listed, "properties": {k: v for k, v in listed["properties"].items() if k != "format"}} == schema
    # The prediction is a nullable string in the table, where the gate is not
    # known; a run whose registry requires it lists it required, as a string,
    # on act and on a commit step, and the relaxed modes list the table's.
    act = server.tool_named("act")
    assert act.input_schema()["required"] == ["action"]
    assert act.input_schema()["properties"]["predict"]["type"] == ["string", "null"]
    assert "PREDICTION_REQUIRED" in act.input_schema()["properties"]["predict"]["description"]
    required = server.listed_schema(act, "required")
    assert required["required"] == ["action", "predict"]
    assert required["properties"]["predict"] == {
        **act.input_schema()["properties"]["predict"], "type": "string",
    }
    step = server.listed_schema(server.tool_named("commit"), "required")["properties"]["steps"]["items"]
    assert step["required"] == ["action", "predict"] and step["properties"]["predict"]["type"] == "string"
    for gate in ("optional", "off", None):
        assert server.listed_schema(act, gate)["required"] == ["action"]
    assert server.listed_schema(server.tool_named("reset"), "required")["required"] == []
    assert act.input_schema()["required"] == ["action"]  # the table is untouched by a listing
    # The prediction grammar rides on the paid tools' descriptions, as on their help.
    assert act.listed_description() == act.description + "\n\n" + prediction_help()
    assert server.tool_named("commit").listed_description().endswith(prediction_help())
    assert server.tool_named("status").listed_description() == server.tool_named("status").description
    with pytest.raises(AssayError, match="^unknown tool 'approve'$") as refused:
        server.tool_named("approve")
    assert refused.value.code == "OPERATION_UNKNOWN" and refused.value.kind == "usage"
    assert refused.value.hint == "the tools are " + ", ".join(TOOL_NAMES)


def test_the_offline_records_decode_like_the_wire_records():
    from assay import server
    from assay.server import StateDeclareRequest, ModelSolveRequest, StatusRequest, ViewRequest

    assert StatusRequest.from_json({}) == StatusRequest(history=8, brief=False)
    assert StatusRequest.from_json({"history": 3, "brief": True}).to_json() == {"history": 3, "brief": True}
    schema = StatusRequest.json_schema()
    assert schema["required"] == [] and list(schema["properties"]) == ["history", "brief"]
    assert schema["properties"]["history"] == {"type": "integer", "description": "how many RECENT lines, 8 by default"}
    with pytest.raises(TypeError, match="^status.history must be an integer, got str$"):
        StatusRequest.from_json({"history": "8"})
    with pytest.raises(TypeError, match="^status.history must be an integer, got bool$"):
        StatusRequest.from_json({"history": True})
    with pytest.raises(TypeError, match="^status.bogus is not a field of the record$"):
        StatusRequest.from_json({"bogus": 1})
    with pytest.raises(KeyError):
        StateDeclareRequest.from_json({"path": "counter"})
    assert StateDeclareRequest.json_schema()["required"] == ["name"]
    assert StateDeclareRequest.from_json({"name": "counter", "path": "counter"}).file is None
    with pytest.raises(TypeError, match="^view.event must be an integer or null, got str$"):
        ViewRequest.from_json({"event": "1"})
    assert ViewRequest.from_json({"event": None, "crop": "0:2,0:2"}) == ViewRequest(crop="0:2,0:2")
    solve = ModelSolveRequest.from_json({"to": "ch counter = 3", "seconds": 2})
    assert solve == ModelSolveRequest(to="ch counter = 3", seconds=2, max_nodes=100_000, max_depth=40)
    assert ModelSolveRequest.from_json({"to": "ch counter = 3", "seconds": 2.5}).seconds == 2.5
    with pytest.raises(TypeError, match="^model_solve.seconds must be a number, got str$"):
        ModelSolveRequest.from_json({"to": "ch counter = 3", "seconds": "2"})
    # Every record round-trips, and one built from no arguments is the defaults.
    for tool in server.TOOLS:
        if not tool.request.json_schema()["required"]:
            request = tool.request.from_json({})
            assert tool.request.from_json(request.to_json()) == request


def test_serve_tools_refuses_without_a_run_and_without_the_extra(tmp_path, monkeypatch):
    from assay import server
    from assay.core import AssayError, RunPaths, atomic_json

    run = tmp_path / "norun"
    run.mkdir()
    # Without a run: the command line's refusal, with the flag after the command.
    refused = run_cli(tmp_path, "serve-tools", "--run-dir", str(run))
    assert refused.returncode == 2 and refused.stdout == ""
    assert refused.stderr == (
        f"ERROR | RUN_MISSING | {run.resolve()} is not initialized\nNEXT | run `assay start WORLD_ID` here\n"
    )
    # Without the extra: the catalogued refusal, before anything is served.
    paths = RunPaths(run)
    paths.state.mkdir()
    atomic_json(paths.config, {"game_id": "x", "mode": "local"})
    for name in ("mcp", "mcp.server", "mcp.server.stdio", "mcp.types"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(AssayError) as missing:
        server.serve(paths)
    assert missing.value.code == "EXTRA_MISSING" and missing.value.kind == "usage"
    assert missing.value.message.startswith("the tool server needs the mcp package, which ")
    assert missing.value.hint == "install the server extra: pip install 'assay-harness[server]'"


# --- the dispatcher, without mcp ----------------------------------------------------


def test_a_call_dispatches_like_the_command_line(tmp_path, monkeypatch):
    from assay import server
    from assay.core import AssayError, RunPaths, error_text
    from assay.inspect import result_text
    from assay.run import Run
    from assay.server import call_tool

    run = tmp_path / "calls"
    _prepare(run)
    paths = RunPaths(run)
    try:
        assert _start(run).returncode == 0
        # An unknown tool, a request that does not fit the record and a form
        # that is neither text nor json are refused by name, in the daemon's
        # words, before anything runs.
        answer = call_tool(paths, "nope", {})
        assert answer.error and answer.structured["code"] == "OPERATION_UNKNOWN" and answer.structured["kind"] == "usage"
        assert answer.structured["hint"] == "the tools are " + ", ".join(TOOL_NAMES)
        assert answer.text == error_text(AssayError.from_json(answer.structured))
        assert answer.text.startswith("ERROR | OPERATION_UNKNOWN | unknown tool 'nope'\nNEXT | the tools are ")
        answer = call_tool(paths, "act", {"action": 7})
        assert answer.error and answer.structured == {
            "code": "REQUEST_MALFORMED",
            "kind": "usage",
            "message": "act.action must be a string, got int",
            "hint": "the act tool takes action, params, predict, because, at_event, declares "
            "(required: action), the fields of its request record, and format (text or json)",
            "detail": None,
        }
        answer = call_tool(paths, "act", {"predict": "noop"})
        assert answer.error and answer.structured["message"] == "act.action is required"
        answer = call_tool(paths, "status", {"bogus": 1})
        assert answer.error and answer.structured["message"] == "status.bogus is not a field of the record"
        assert answer.structured["hint"] == (
            "the status tool takes history, brief, the fields of its request record, and format (text or json)"
        )
        answer = call_tool(paths, "status", {"format": "xml"})
        assert answer.error and answer.structured["message"] == "status.format must be text or json, got 'xml'"
        assert answer.structured["code"] == "REQUEST_MALFORMED"
        assert _records(run, "command_start") == []
        # status, by default: the text of the command line, byte for byte, and
        # nothing structured; under json, the `--json` document as the text
        # and as the structured content.
        answer = call_tool(paths, "status", None)
        assert not answer.error and answer.structured is None
        assert answer.text == run_cli(run, "status").stdout.rstrip("\n")
        machine = json.loads(run_cli(run, "status", "--json").stdout)
        answer = call_tool(paths, "status", {"format": "json"})
        assert not answer.error and answer.structured == machine and json.loads(answer.text) == machine
        assert "text" not in machine and machine["truncated"] == []
        # act: the parameters validated against the registry before the socket
        # (the name case-folded, a bad value refused as the command line refuses
        # it), the receipt rendered as the command line renders it.
        answer = call_tool(paths, "act", {"action": "inc", "params": {"amount": 1}, "predict": "change", "because": "probe"})
        assert not answer.error and answer.structured is None
        assert answer.text == result_text(Run.load(paths, strict=False), _receipt(run, 1))
        assert answer.text.startswith("RESULT | PREDICTED | ") and "EVENT | e1 | " in answer.text
        answer = call_tool(paths, "act", {"action": "INC", "params": {"amount": 9}, "predict": "change"})
        assert answer.error and answer.structured["code"] == "ACTION_PARAMS"
        assert answer.text.startswith("ERROR | ACTION_PARAMS | ")
        answer = call_tool(paths, "act", {"action": "INC", "params": {"amount": 1}, "format": "json"})
        assert answer.error and answer.structured["code"] == "PREDICTION_REQUIRED" and answer.structured["kind"] == "refused"
        assert json.loads(answer.text) == answer.structured
        # The command line takes the same step through the same daemon.
        printed = run_cli(run, "act", "INC", "amount=1", "--predict", "change", "--because", "probe")
        assert printed.returncode == 0 and printed.stdout.startswith("RESULT | PREDICTED | ")
        # commit and reset, the other paid tools, rendered as the command line renders them.
        answer = call_tool(paths, "commit", {"steps": [
            {"action": "set_lamp", "params": {"state": "on"}, "predict": "change"},
            {"action": "NOOP", "predict": "noop"},
        ]})
        assert not answer.error and answer.text == result_text(Run.load(paths, strict=False), _receipt(run, 4))
        assert answer.text.startswith("RESULT | ") and "EVENT | e4 | " in answer.text
        answer = call_tool(paths, "commit", {})
        assert answer.error and answer.structured["code"] == "COMMAND_ARGS"
        answer = call_tool(paths, "reset", {"because": "probe the rewind"})
        assert not answer.error and answer.text == result_text(Run.load(paths, strict=False), _receipt(run, 5))
        assert answer.text.startswith("RESULT | RESET | ") and "EVENT | e5 | " in answer.text
        # The offline tools: the text of a command without a record and of one
        # with a record, as the command line prints them, and their documents.
        answer = call_tool(paths, "state_declare", {"name": "counter", "path": "counter"})
        assert not answer.error and answer.text == DECLARED
        answer = call_tool(paths, "state_list", {})
        assert not answer.error and answer.text == run_cli(run, "state", "list").stdout.rstrip("\n")
        answer = call_tool(paths, "state_list", {"format": "json"})
        assert answer.structured == json.loads(run_cli(run, "state", "list", "--json").stdout)
        # python: the source runs in a child process per call; its output is
        # the command line's, its errors are refusals, and nothing it does
        # reaches the server.
        answer = call_tool(paths, "python", {"source": COUNTERS})
        assert not answer.error and answer.text == run_cli(run, "python", COUNTERS).stdout.rstrip("\n") == "[1, 2, 2, 2, 0]"
        assert call_tool(paths, "python", {"source": COUNTERS, "format": "json"}).structured == {"lines": ["[1, 2, 2, 2, 0]"]}
        answer = call_tool(paths, "python", {"source": "import assay.server; assay.server.TOOLS = (); print(len(assay.server.TOOLS))"})
        assert not answer.error and answer.text == "0"
        assert len(server.TOOLS) == len(TOOL_NAMES)
        assert not call_tool(paths, "status", {}).error
        answer = call_tool(paths, "python", {"source": "import sys; sys.exit(3)"})
        assert answer.error and answer.structured["code"] == "PYTHON_FAILED"
        assert answer.structured["message"] == "analysis failed: SystemExit: 3"
        # The command line says the same of a source that leaves through sys.exit.
        printed = run_cli(run, "python", "import sys; sys.exit(3)")
        assert printed.returncode == 2 and printed.stdout == ""
        assert printed.stderr.startswith("ERROR | PYTHON_FAILED | analysis failed: SystemExit: 3\n")
        answer = call_tool(paths, "python", {"source": "1 / 0"})
        assert answer.error and answer.structured["message"] == "analysis failed: ZeroDivisionError: division by zero"
        # Past the wall clock the child is stopped with everything it spawned:
        # a sleeper the source started dies with the child's process group.
        token = f"assay-sleeper-{run.name}-{os.getpid()}"
        sleeper = (
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', {token!r}])\n"
            "time.sleep(30)\n"
        )
        monkeypatch.setattr(server, "PYTHON_TOOL_SECONDS", 1)
        answer = call_tool(paths, "python", {"source": sleeper})
        assert answer.error and answer.structured["code"] == "PYTHON_FAILED"
        assert answer.structured["message"] == "analysis did not finish within 1 seconds and was stopped"
        monkeypatch.setattr(server, "PYTHON_TOOL_SECONDS", 120)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _running(token):
            time.sleep(0.1)
        assert not _running(token)
        answer = call_tool(paths, "goal_propose", {"text": "count to two", "because": "cheaper"})
        assert not answer.error and answer.text == (
            "GOAL | proposal #1 journaled, awaiting owner ratification (`assay goal ratify ID --token ...`)"
        )
        assert call_tool(paths, "goal_list", {}).text == "  #1 [pending] count to two; cheaper"
        assert call_tool(paths, "model_init", {}).text == f"CREATED | {run / 'model.py'}; declare STATES, define next()"
        answer = call_tool(paths, "view", {"event": 1, "history": 2})
        assert not answer.error and answer.text == run_cli(run, "view", "--event", "1", "--history", "2").stdout.rstrip("\n")
        assert call_tool(paths, "view", {"event": 1, "format": "json"}).structured["event"]["id"] == 1
        # Nothing an agent causes leaves the worker: a BaseException out of a
        # tool is INTERNAL, its traceback in the capped log, never the answer.
        def leaving(paths, status, request):
            raise SystemExit("left through the handler")

        monkeypatch.setattr(server, "tool_named", lambda name: dataclasses.replace(server.TOOLS[0], run=leaving))
        log = run / ".assay" / server.SERVER_LOG
        log.write_bytes(b"x" * 1_200_000 + b"\n")
        answer = call_tool(paths, "status", {})
        monkeypatch.undo()
        monkeypatch.setattr(server, "PYTHON_TOOL_SECONDS", 120)
        assert answer.error and answer.structured["code"] == "INTERNAL" and answer.structured["kind"] == "internal"
        assert answer.structured["message"] == "SystemExit: left through the handler"
        assert answer.structured["hint"] == f"traceback in {log}; report it with the tool call that produced it"
        logged = log.read_bytes()
        assert len(logged) <= server.SERVER_LOG_BYTES and b"x" * 10 not in logged
        assert b"| tool status\n" in logged and logged.endswith(b"SystemExit: left through the handler\n\n")
        # The activity records: the command line's shape, `surface` apart.
        starts = _records(run, "command_start")
        assert [r["command"] for r in starts if r["surface"] == "mcp"] == [
            "status", "status", "act", "act", "act", "commit", "commit", "reset", "state_declare",
            "state_list", "state_list", "python", "python", "python", "status", "python", "python",
            "python", "goal_propose", "goal_list", "model_init", "view", "view", "status",
        ]
        assert [r["command"] for r in starts if r["surface"] == "cli"] == [
            "status", "status", "act", "state", "state", "python", "python", "view",
        ]
        ends = {(r["command"], r["surface"], r["status"]): r for r in _records(run, "command_end")}
        served, typed = ends[("act", "mcp", "FINISHED")], ends[("act", "cli", "FINISHED")]
        assert set(served) == set(typed) and served["risk"] == typed["risk"] == "paid_live_action"
        assert served["event"] == 1 and typed["event"] == 2
        assert ends[("commit", "mcp", "FINISHED")]["event"] == 4 and ends[("reset", "mcp", "FINISHED")]["event"] == 5
        refused = [r for r in _records(run, "command_end") if r["surface"] == "mcp" and r["status"] == "ERROR"]
        assert [r["code"] for r in refused] == [
            "ACTION_PARAMS", "PREDICTION_REQUIRED", "COMMAND_ARGS", "PYTHON_FAILED", "PYTHON_FAILED", "PYTHON_FAILED",
        ]
        # The journal: the step the server took and the step the command line
        # took have one shape.
        events = _events(run)
        assert [e["action"] for e in events] == ["START", "INC", "INC", "SET_LAMP", "NOOP", "RESET"]
        served_event, typed_event = events[1:3]
        assert set(served_event) == set(typed_event)
        assert served_event["data"] == typed_event["data"] == {"amount": 1}
        assert served_event["predict"] == typed_event["predict"] == "change"
        assert served_event["predict_ok"] is True and typed_event["predict_ok"] is True
        assert [g["kind"] for g in served_event["grade"]] == [g["kind"] for g in typed_event["grade"]]
        # Without the daemon an offline tool still answers from disk, a paid one refuses.
        assert run_cli(run, "stop").returncode == 0
        assert _records(run, "command_end")[-1]["command"] == "stop" and _records(run, "command_end")[-1]["surface"] == "cli"
        answer = call_tool(paths, "status", {"brief": True})
        assert not answer.error and answer.text == run_cli(run, "status", "--brief").stdout.rstrip("\n")
        assert call_tool(paths, "audit", {}).text.startswith("AUDIT | CLEAN")
        answer = call_tool(paths, "act", {"action": "NOOP", "predict": "noop"})
        assert answer.error and answer.structured["code"] == "DAEMON_UNAVAILABLE"
        assert answer.structured["hint"] == "run `assay start WORLD_ID` to replay the journal and resume"
    finally:
        stop_run(run)


# --- the server over stdio, with mcp ----------------------------------------------


def test_a_run_driven_through_the_server_over_stdio(tmp_path):
    pytest.importorskip(
        "mcp", reason="the server extra (mcp) is not installed; `uv run --with mcp` runs this scenario"
    )
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    import assay
    from assay import server
    from assay.core import RunPaths
    from assay.inspect import result_text, view_text
    from assay.run import Run

    run = tmp_path / "stdio"
    _prepare(run)
    paths = RunPaths(run)

    def wire(result) -> dict:
        # The wire form of a result, whichever spelling the installed mcp uses.
        return result.model_dump(by_alias=True, mode="json")

    async def scenario() -> dict:
        found: dict = {}
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(ASSAY_CLI), "serve-tools", "--run-dir", str(run)],
            env=dict(os.environ),
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            # Every call waits at most this long for its answer.
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=120) as session:
                found["init"] = wire(await session.initialize())
                found["tools"] = wire(await session.list_tools())
                found["status"] = wire(await session.call_tool("status", {}))
                found["status_json"] = wire(await session.call_tool("status", {"format": "json"}))
                found["act"] = wire(await session.call_tool(
                    "act", {"action": "INC", "params": {"amount": 1}, "predict": "change", "because": "probe"},
                ))
                found["bare"] = wire(await session.call_tool("act", {"action": "INC", "params": {"amount": 1}}))
                # The source leaves through sys.exit in its own process; the
                # server answers it as a refusal and the next call as usual.
                found["exit"] = wire(await session.call_tool("python", {"source": "import sys; sys.exit(3)"}))
                found["after"] = wire(await session.call_tool("status", {}))
                found["view"] = wire(await session.call_tool("view", {}))
                found["declare"] = wire(await session.call_tool(
                    "state_declare", {"name": "counter", "path": "counter"}
                ))
                # The command line takes the next step through the same daemon.
                found["typed"] = await asyncio.to_thread(
                    run_cli, run, "act", "INC", "amount=1", "--predict", "change", "--because", "probe"
                )
                found["win"] = wire(await session.call_tool(
                    "act", {"action": "INC", "params": {"amount": 1}, "predict": "ch counter = 3; win"},
                ))
                found["audit"] = wire(await session.call_tool("audit", {"format": "json"}))
        return found

    try:
        assert _start(run).returncode == 0
        expected_status = run_cli(run, "status").stdout.rstrip("\n")
        expected_document = json.loads(run_cli(run, "status", "--json").stdout)
        found = asyncio.run(scenario())
        # The handshake: the server is named `assay`, versioned as the package.
        assert found["init"]["serverInfo"]["name"] == "assay"
        assert found["init"]["serverInfo"]["version"] == assay.__version__
        assert found["init"]["instructions"] == server.INSTRUCTIONS
        # The listing: the table, each tool with its record's schema, the
        # surface's `format`, and the prediction required on this run, whose
        # registry requires it.
        tools = {tool["name"]: tool for tool in found["tools"]["tools"]}
        assert list(tools) == TOOL_NAMES
        assert tools["act"]["inputSchema"] == server.listed_schema(server.tool_named("act"), "required")
        assert tools["act"]["inputSchema"]["required"] == ["action", "predict"]
        assert tools["act"]["inputSchema"]["properties"]["predict"]["type"] == "string"
        assert tools["commit"]["inputSchema"]["properties"]["steps"]["items"]["required"] == ["action", "predict"]
        assert tools["act"]["description"] == server.tool_named("act").listed_description()
        assert tools["status"]["inputSchema"] == server.listed_schema(server.tool_named("status"), "required")
        assert tools["status"]["inputSchema"]["properties"]["format"]["enum"] == ["text", "json"]
        # status: by default the one text block holds what the command line
        # printed and nothing is structured; under json, the `--json` document
        # as the text and as the structured content.
        status = found["status"]
        assert status["isError"] is False and status["structuredContent"] is None
        assert [block["type"] for block in status["content"]] == ["text"]
        assert status["content"][0]["text"] == expected_status
        status_json = found["status_json"]
        assert status_json["structuredContent"] == expected_document
        assert json.loads(status_json["content"][0]["text"]) == expected_document
        # act: the receipt rendered as the command line renders it, over the run
        # as it stood at that event.
        act = found["act"]
        assert act["isError"] is False and act["structuredContent"] is None
        run_then = Run.load(paths, strict=False)
        run_then.events = run_then.events[:2]
        assert act["content"][0]["text"] == result_text(run_then, _receipt(run, 1))
        # act without a prediction: the gate's refusal, as a tool error carrying
        # the error object, the command line's lines as its text.
        bare = found["bare"]
        assert bare["isError"] is True
        assert bare["structuredContent"]["code"] == "PREDICTION_REQUIRED"
        assert bare["structuredContent"]["kind"] == "refused"
        assert set(bare["structuredContent"]) == {"code", "kind", "message", "hint", "detail"}
        assert bare["content"][0]["text"].startswith("ERROR | PREDICTION_REQUIRED | ")
        assert f"\nNEXT | {bare['structuredContent']['hint']}" in bare["content"][0]["text"]
        # The source's exit stayed in its child: a refusal, and the server went on.
        assert found["exit"]["isError"] is True
        assert found["exit"]["structuredContent"]["code"] == "PYTHON_FAILED"
        assert found["exit"]["content"][0]["text"].startswith("ERROR | PYTHON_FAILED | analysis failed: SystemExit: 3")
        assert found["after"]["isError"] is False and found["after"]["content"][0]["text"].startswith("STATUS | fake1 | event 1 | ")
        # view: the command line's text for the event as it stood.
        assert found["view"]["content"][0]["text"] == view_text(run_then)
        assert found["declare"]["content"][0]["text"] == DECLARED
        assert found["typed"].returncode == 0 and found["typed"].stdout.startswith("RESULT | PREDICTED | ")
        assert found["win"]["content"][0]["text"].startswith("RESULT | GAME_COMPLETE | ")
        assert found["audit"]["structuredContent"]["invalid_for_scoring"] is False
        # The activity log: the server's calls on the mcp surface, the command
        # line's on its own, in order.
        starts = _records(run, "command_start")
        assert [r["command"] for r in starts if r["surface"] == "mcp"] == [
            "status", "status", "act", "act", "python", "status", "view", "state_declare", "act", "audit",
        ]
        assert [r["command"] for r in starts if r["surface"] == "cli"] == ["status", "status", "act"]
        ends = [r for r in _records(run, "command_end") if r["command"] == "act" and r["status"] == "FINISHED"]
        assert [(r["surface"], r["event"]) for r in ends] == [("mcp", 1), ("cli", 2), ("mcp", 3)]
        assert set(ends[0]) == set(ends[1])
        # The journal: the server's step and the command line's have one shape,
        # and the chain is clean.
        events = _events(run)
        assert [event["action"] for event in events] == ["START", "INC", "INC", "INC"]
        assert set(events[1]) == set(events[2]) and events[1]["data"] == events[2]["data"] == {"amount": 1}
        assert events[1]["predict_ok"] is True and events[2]["predict_ok"] is True
        assert events[3]["state"] == "WIN"
        audited = run_cli(run, "audit")
        assert audited.returncode == 0 and audited.stdout.startswith("AUDIT | CLEAN")
    finally:
        stop_run(run)
