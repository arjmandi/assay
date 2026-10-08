"""The tool server (docs/ARCHITECTURE.md section 7.5): the harness as tools
over the same daemon, the command line a thin client beside it.

One tool per agent-facing operation, named after the operation. The table
(`TOOLS`) is built here without importing `mcp`, so the plain suite reads it:
the daemon operations of the wire table (`ops.OPERATIONS`) that the rule
below admits, forwarded over the socket through `broker.call` as the command
line sends them, and the offline operations, which run in the server process
from disk, with `Run.load` on every call and no cache (the daemon appends
between calls); `python` alone runs the agent's source in a child process
per call, as `assay python` runs it in a process of its own, under a wall
clock (`PYTHON_TOOL_SECONDS`), so nothing the source does reaches the
server. A tool's `inputSchema` is its request record's `json_schema()` (the
wire record of a daemon operation, a record of this module for an offline
one, since those never cross the socket) with the surface's `format`
property and, on a run whose pinned registry requires the prediction,
`predict` required on `act` and on a commit step (`listed_schema`). The
result is, by default (`format: "text"`), the prose the command line prints,
as one text block; under `format: "json"` it is the document the command
line prints under `--json` (the record's fields and the keys beside them),
as the text block and as the structured content. A refusal is a tool error
(`isError`): the error object of section 7.1 as the structured content, and
as the text block the lines the command line prints on stderr, or the object
under `json`; a failure that is no refusal is `INTERNAL`, with the traceback
appended to `.assay/server.log` (each cut to `SERVER_LOG_ENTRY_CHARS`, the
file kept under `SERVER_LOG_BYTES`) and never sent to the agent. Every call
writes the `command_start` and `command_end` activity records the command
line writes, with `surface: "mcp"` where the command line's say `cli`.

The exclusion rule, in this one place (`agent_facing`, `OPERATOR_COMMANDS`
and the table): a tool is what reads or advances the agent's own run, which
the constitution gives the agent: the paid operations, the readers (`status`,
`view`, `audit`, `state_list`, `module_list`, `python`) and the state,
model and goal operations. Not tools: the owner operations (`owner=True` in
the wire table: `install_module`, `approve`, `waive` and `goal_ratify` take
the owner token the agent must never hold); the daemon's own operations
(`ping` and `observe`, liveness and the raw observation, which the agent
reads through `status` and `view`); the lifecycle commands (`start`, `stop`,
`version`, `doctor` and `serve-tools` itself: the operator's, run before any
run is loaded); and the operator's side effects beyond the run (`export`,
the knowledge file for a later run, and `spend_report`, the operator's feed
of the bill). `tests/test_server.py` holds the rule against `ops.OPERATIONS`
and `cli.COMMANDS`, so a new operation reaches the agent only once it is
classified here.

What the mcp layer decides never reaches the dispatcher: `arguments` that is
not an object is answered by the layer as a JSON-RPC invalid-params error,
not `REQUEST_MALFORMED`; a request line that does not parse, or nests past
the layer's limit, is dropped without a reply, and the server answers the
next call; a repeated key in `arguments` is parsed by the layer with the last
value winning, where the command line's `--params` refuses it.

`mcp` is imported inside `serve()` alone, the `server` extra; nothing else
in the kernel imports it. `assay serve-tools --run-dir DIR` serves one run
over stdio under the server name `assay`, so a Claude Code hook matches its
calls as `mcp__assay__.*` (section 8.4). `python -m assay.server
--run-python DIR` is the `python` tool's child, not a surface.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import dataclasses
import importlib
import io
import json
import os
import signal
import subprocess
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, Self

from . import __version__
from .agenda import list_proposals, proposal_text, proposals_text, propose_goal
from .analysis import run_python
from .broker import broker_gated
from .states import state_declared_text, state_list_of, state_list_text, declare_state
from .core import (
    TOOL_SURFACE,
    AssayError,
    CommandStatus,
    RunPaths,
    command_status,
    error_text,
    now_iso,
    require_run,
    run_lock,
)
from .extras import require_kind
from .inspect import result_text, view_lines_text, view_of
from .integrity import audit, audit_lines
from .model import (
    fit_lines,
    init_model,
    model_created_text,
    replay_model,
    solve_lines,
    solve_model,
)
from .modules import module_list_of, module_list_text
from .ops import (
    ACT,
    COMMIT,
    RESET,
    ActRequest,
    CommitRequest,
    Operation,
    ReceiptResult,
    Req,
    ResetRequest,
    Step,
)
from .predictions import claims_help
from .records import refuse_unknown, wrong_type
from .registry import gate_mode, require_registry, status_budget, validate_action
from .run import Run
from .status import BRIEF_BUDGET, estimated_tokens, status_of, status_within

# The server's name: a Claude Code hook matches its calls as `mcp__assay__.*`.
SERVER_NAME = "assay"
# Where a failure that is no refusal leaves its traceback, under `.assay`:
# each entry's traceback cut to this many characters, the file kept under
# this many bytes by dropping its head.
SERVER_LOG = "server.log"
SERVER_LOG_ENTRY_CHARS = 4_000
SERVER_LOG_BYTES = 1_000_000
# The `python` tool's child runs the agent's source for at most this long.
PYTHON_TOOL_SECONDS = 120
# What the server tells a client at the handshake.
INSTRUCTIONS = (
    "ASSAY referee harness: look, predict, act, compare. Read CONSTITUTION.md completely "
    "first and begin with the status tool. Every paid action (act, commit, reset) needs a "
    "prediction in the claim grammar and is graded in code against what happened; the "
    "receipt comes back as the text the command line prints (format json for the record). "
    "A refusal is a tool error carrying the error object (code, kind, message, hint, detail); "
    "docs/ERRORS.md lists the codes. Keep .assay/NOTES.md to one page; never edit anything "
    "else under .assay by hand."
)
# The surface's one argument beside the record's fields: the result's form.
FORMATS = ("text", "json")
FORMAT_PROPERTY = {
    "type": "string",
    "enum": list(FORMATS),
    "description": "text (default) or json: the lines or the --json document",
}

# The daemon's own operations: liveness and the raw observation, which the
# agent reads through `status` and `view`.
DAEMON_OWN: frozenset[str] = frozenset({"ping", "observe"})
# The operator's side effects beyond the run: the knowledge file for a later
# run and the operator's feed of the bill. They take no token, but neither
# reads nor advances the agent's run, and the constitution gives neither.
OPERATOR_COMMANDS: frozenset[str] = frozenset({"export", "spend_report"})


def agent_facing(operation: Operation[Any, Any]) -> bool:
    """The rule for a daemon operation: a tool unless it needs the owner
    token or is the daemon's own."""
    return not operation.owner and operation.name not in DAEMON_OWN


# --- the offline request records ---------------------------------------------
#
# The daemon operations' records are the wire table's. The offline operations
# never cross the socket, so their records live here: frozen classes whose
# fields carry their JSON Schema and their description (`_field`), a field
# without a default required, decoded by the rules of the records kit and
# described by the same fields, so the schema and the check cannot drift
# apart.

_STRING = {"type": "string"}
_STRING_OR_NULL = {"type": ["string", "null"]}
_INTEGER = {"type": "integer"}
_INTEGER_OR_NULL = {"type": ["integer", "null"]}
_NUMBER = {"type": "number"}
_BOOLEAN = {"type": "boolean"}
_WORDS = {"string": "a string", "integer": "an integer", "number": "a number", "boolean": "true or false"}


def _field(schema: Mapping[str, Any], description: str, default: Any = dataclasses.MISSING) -> Any:
    """One field of an offline request record: its JSON Schema, the one-line
    description the agent reads, and its default (none: the field is
    required)."""
    return dataclasses.field(
        default=default, metadata={"schema": dict(schema), "description": description}
    )


def _expected(types: Sequence[str]) -> str:
    words = " or ".join(_WORDS[name] for name in types if name != "null")
    return words + (" or null" if "null" in types else "")


def _checked(record: str, key: str, schema: Mapping[str, Any], value: Any) -> Any:
    """The value when it has one of the schema's types, else the kit's
    refusal naming the field."""
    declared = schema["type"]
    types = [str(name) for name in declared] if isinstance(declared, list) else [str(declared)]
    if value is None:
        if "null" in types:
            return None
    elif isinstance(value, bool):
        if "boolean" in types:
            return value
    elif isinstance(value, int):
        if "integer" in types or "number" in types:
            return value
    elif isinstance(value, float):
        if "number" in types:
            return value
    elif isinstance(value, str):
        if "string" in types:
            return value
    raise wrong_type(record, key, _expected(types), value)


@dataclasses.dataclass(frozen=True, slots=True)
class _OfflineRequest:
    """What the offline request records share: `from_json` by the rules of
    the records kit (a wrong type, a missing required field and a field the
    record does not take raise `TypeError` or `KeyError`, which
    `decode_arguments` words as `REQUEST_MALFORMED`, as the daemon words a
    wire record's), `to_json`, and `json_schema()` from the same fields."""

    NAME: ClassVar[str] = ""

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Self:
        fields = dataclasses.fields(cls)
        refuse_unknown(obj, cls.NAME, frozenset(field.name for field in fields))
        values: dict[str, Any] = {}
        for field in fields:
            if field.name not in obj:
                if field.default is dataclasses.MISSING:
                    raise KeyError(field.name)
                continue
            values[field.name] = _checked(cls.NAME, field.name, field.metadata["schema"], obj[field.name])
        return cls(**values)

    def to_json(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in dataclasses.fields(self)}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        fields = dataclasses.fields(cls)
        return {
            "type": "object",
            "properties": {
                field.name: {**field.metadata["schema"], "description": field.metadata["description"]}
                for field in fields
            },
            "required": [field.name for field in fields if field.default is dataclasses.MISSING],
            "additionalProperties": False,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class StatusRequest(_OfflineRequest):
    """`status`: the whole picture, as `assay status` prints it."""

    NAME: ClassVar[str] = "status"

    history: int = _field(_INTEGER, "how many RECENT lines, 8 by default", 8)
    brief: bool = _field(
        _BOOLEAN,
        "drop the lowest-value blocks to fit 1500 tokens, or the registry's status_budget "
        "when that is smaller; a TRUNCATED line names them",
        False,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ViewRequest(_OfflineRequest):
    """`view`: one event, the delta since the previous one, the history."""

    NAME: ClassVar[str] = "view"

    event: int | None = _field(_INTEGER_OR_NULL, "the event to inspect; the latest when null", None)
    history: int = _field(_INTEGER, "how many history lines after it, none by default", 0)
    grid: bool = _field(_BOOLEAN, "frame worlds: print the complete exact 0-f grid", False)
    frames: bool = _field(_BOOLEAN, "frame worlds: show the causal animation frames", False)
    crop: str | None = _field(
        _STRING_OR_NULL, "frame worlds: print an exact half-open crop, as R0:R1,C0:C1", None
    )
    export: str | None = _field(
        _STRING_OR_NULL,
        "frame worlds: export the grid history to this .npz file, relative to the run directory",
        None,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class AuditRequest(_OfflineRequest):
    """`audit` takes nothing."""

    NAME: ClassVar[str] = "audit"


@dataclasses.dataclass(frozen=True, slots=True)
class PythonRequest(_OfflineRequest):
    """`python`: offline analysis with the history preloaded, in a child
    process per call."""

    NAME: ClassVar[str] = "python"

    source: str = _field(
        _STRING,
        "the Python source: one expression prints its value; a program runs with observations, "
        "transitions, actions, key_delta, delta_lines, bfs, astar, np and json preloaded",
    )


@dataclasses.dataclass(frozen=True, slots=True)
class StateDeclareRequest(_OfflineRequest):
    """`state_declare`: an addressable state, a named reading by a path or an extractor."""

    NAME: ClassVar[str] = "state_declare"

    name: str = _field(_STRING, "the state's name, lowercase")
    path: str | None = _field(
        _STRING_OR_NULL, "dotted keys into the dict observation, e.g. counters.red", None
    )
    file: str | None = _field(
        _STRING_OR_NULL,
        "an extractor file relative to the run directory, def extract(obs) -> value, sandboxed; "
        "instead of path",
        None,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class StateListRequest(_OfflineRequest):
    """`state_list`: the registered states with their readings."""

    NAME: ClassVar[str] = "state_list"

    read: bool = _field(
        _BOOLEAN,
        "compute the extractor states fresh (each extractor runs sandboxed) instead of showing "
        "the last graded reading",
        False,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ModelInitRequest(_OfflineRequest):
    """`model_init` takes nothing."""

    NAME: ClassVar[str] = "model_init"


@dataclasses.dataclass(frozen=True, slots=True)
class ModelReplayRequest(_OfflineRequest):
    """`model_replay` takes nothing."""

    NAME: ClassVar[str] = "model_replay"


@dataclasses.dataclass(frozen=True, slots=True)
class ModelSolveRequest(_OfflineRequest):
    """`model_solve`: a plan to a state target, searched inside the model."""

    NAME: ClassVar[str] = "model_solve"

    to: str = _field(_STRING, 'the goal reading, as "ch NAME = V"')
    seconds: float = _field(_NUMBER, "the search's time limit, 15 by default", 15.0)
    max_nodes: int = _field(_INTEGER, "the search's node limit, 100000 by default", 100_000)
    max_depth: int = _field(_INTEGER, "the plan's depth limit, 40 by default", 40)


@dataclasses.dataclass(frozen=True, slots=True)
class ModuleListRequest(_OfflineRequest):
    """`module_list` takes nothing."""

    NAME: ClassVar[str] = "module_list"


@dataclasses.dataclass(frozen=True, slots=True)
class GoalProposeRequest(_OfflineRequest):
    """`goal_propose`: a revision of the standing goal, for the owner to
    ratify."""

    NAME: ClassVar[str] = "goal_propose"

    text: str = _field(_STRING, "the proposed goal text")
    because: str | None = _field(_STRING_OR_NULL, "why the registered goal no longer fits", None)


@dataclasses.dataclass(frozen=True, slots=True)
class GoalListRequest(_OfflineRequest):
    """`goal_list` takes nothing."""

    NAME: ClassVar[str] = "goal_list"


# --- the tools --------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Outcome:
    """What a tool call produced: the record's fields (or `{"lines": [...]}`
    for an operation without a record, as under `--json`), the prose the
    command line prints, and the keys that ride beside the record's fields
    in the command line's document (`estimated_tokens`, `truncated`)."""

    record: Mapping[str, Any]
    text: str
    beside: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def document(self) -> dict[str, Any]:
        """The document the command line prints under `--json`: the record's
        fields and the keys beside them."""
        return {**self.record, **self.beside}


Runner = Callable[[RunPaths, CommandStatus, Any], Outcome]


@dataclasses.dataclass(frozen=True)
class Tool:
    """One tool: the operation's name, its description, its request record
    (the `inputSchema` is the record's `json_schema()`), what runs it, the
    daemon operation it forwards to, if any, and the epilog a listing
    appends to the description (the claim grammar, as `assay act --help`
    shows it)."""

    name: str
    description: str
    request: type[Any]
    run: Runner
    operation: Operation[Any, Any] | None = None
    epilog: Callable[[], str] | None = None

    def input_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = self.request.json_schema()
        return schema

    def listed_description(self) -> str:
        if self.epilog is None:
            return self.description
        return f"{self.description}\n\n{self.epilog()}"


def listed_schema(tool: Tool, gate: str | None) -> dict[str, Any]:
    """The `inputSchema` a listing carries for one run: the record's schema
    with the surface's `format` property and, when the run's pinned registry
    requires the prediction (`gate: required`, the default, fixed for the
    run's life), `predict` required on `act` and on a commit step, as a
    string. The table's own schema is untouched."""
    schema = copy.deepcopy(tool.input_schema())
    schema["properties"]["format"] = dict(FORMAT_PROPERTY)
    if gate == "required" and tool.name == "act":
        _require_predict(schema)
    elif gate == "required" and tool.name == "commit":
        _require_predict(schema["properties"]["steps"]["items"])
    return schema


def _require_predict(schema: dict[str, Any]) -> None:
    schema["properties"]["predict"] = {**schema["properties"]["predict"], "type": "string"}
    schema["required"] = [*schema["required"], "predict"]


def _lines(lines: Sequence[str]) -> Outcome:
    """The outcome of an operation without a result record: its lines, as
    `--json` prints them."""
    return Outcome({"lines": list(lines)}, "\n".join(lines))


def _paid(
    paths: RunPaths,
    status: CommandStatus,
    operation: Operation[Req, ReceiptResult],
    request: Req,
    *,
    steps: int = 1,
) -> Outcome:
    """A paid operation forwarded to the daemon, the gate enforced where the
    session lives; the run is reloaded for the receipt, since the daemon
    appended what this process does not hold (as the command line does)."""
    receipt = broker_gated(paths, operation, request, steps=steps)
    status.run = Run.load(paths, strict=False)
    text = result_text(status.run, receipt)
    return Outcome(receipt.to_json(), text, {"estimated_tokens": estimated_tokens(text)})


def _act(paths: RunPaths, status: CommandStatus, request: ActRequest) -> Outcome:
    # The parameters validated against the pinned registry here, before the
    # socket, as the command line validates `--params`; the daemon validates
    # them again before any spend.
    name, params = validate_action(require_registry(status.run), request.action, request.params)
    return _paid(paths, status, ACT, dataclasses.replace(request, action=name, params=params))


def _commit(paths: RunPaths, status: CommandStatus, request: CommitRequest) -> Outcome:
    if bool(request.plan) == bool(request.steps):
        raise AssayError(
            "commit takes either a plan (from model_solve) or one or more steps, not both",
            code="COMMAND_ARGS",
        )
    registry = require_registry(status.run)
    steps: list[Step] = []
    for step in request.steps:
        name, params = validate_action(registry, step.action, step.params)
        steps.append(Step(action=name, params=params, predict=step.predict))
    checked = dataclasses.replace(request, steps=tuple(steps))
    return _paid(paths, status, COMMIT, checked, steps=max(1, len(steps)))


def _reset(paths: RunPaths, status: CommandStatus, request: ResetRequest) -> Outcome:
    return _paid(paths, status, RESET, request)


def _status(paths: RunPaths, status: CommandStatus, request: StatusRequest) -> Outcome:
    # The registry's budget on every status, `brief` under the smaller of it
    # and BRIEF_BUDGET (section 7.6), the whole record either way, as the
    # command line's document.
    run = status.run
    record = status_of(run, history=request.history)
    budget = status_budget(run.registry)
    if request.brief:
        budget = BRIEF_BUDGET if budget is None else min(budget, BRIEF_BUDGET)
    text, dropped = status_within(record, budget)
    return Outcome(
        record.to_json(), text, {"estimated_tokens": estimated_tokens(text), "truncated": list(dropped)}
    )


def _view(paths: RunPaths, status: CommandStatus, request: ViewRequest) -> Outcome:
    run = status.run
    flags = {"grid": request.grid, "frames": request.frames, "crop": request.crop}
    view = view_of(run, event_id=request.event, history=request.history, flags=flags)
    if request.export:
        export = Path(request.export)
        destination = export if export.is_absolute() else paths.root / export
        events = run.events
        exported = require_kind(events[-1] if events else None, "export").export_history(run, destination)
        view = dataclasses.replace(view, exported=str(exported))
    return Outcome(view.to_json(), view_lines_text(view))


def _audit(paths: RunPaths, status: CommandStatus, request: AuditRequest) -> Outcome:
    report = audit(status.run)
    return Outcome(report.to_json(), "\n".join(audit_lines(report)))


def _python(paths: RunPaths, status: CommandStatus, request: PythonRequest) -> Outcome:
    # The source runs in a child process per call, as `assay python` runs it
    # in a process of its own: whatever it does to its interpreter (a module
    # it rewrites, a SystemExit it raises, a loop that never ends) stays
    # there, and the child is stopped at the wall clock. The child leads a
    # session of its own, so at the limit its whole process group is killed
    # and nothing the source spawned outlives it. The run lock is held
    # meanwhile, as the command line holds it for `assay python`. The child
    # answers with one JSON line, the lines the source printed or the error
    # object.
    package_dir = str(Path(__file__).resolve().parents[1])
    inherited = os.environ.get("PYTHONPATH")
    environment = {
        **os.environ,
        "PYTHONPATH": package_dir + (os.pathsep + inherited if inherited else ""),
    }
    child = subprocess.Popen(
        [sys.executable, "-m", "assay.server", "--run-python", str(paths.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(paths.root),
        env=environment,
        start_new_session=True,
    )
    try:
        stdout, stderr = child.communicate(request.source, timeout=PYTHON_TOOL_SECONDS)
    except subprocess.TimeoutExpired:
        _stop_group(child)
        raise AssayError(
            f"analysis did not finish within {PYTHON_TOOL_SECONDS} seconds and was stopped",
            code="PYTHON_FAILED",
            hint="the python tool runs the source in a child process under that wall clock; narrow the computation",
        ) from None
    return _lines(_child_lines(child.returncode, stdout, stderr))


def _stop_group(child: subprocess.Popen[str]) -> None:
    """The child and everything it spawned, killed as one process group (the
    child leads its session), and the child reaped."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(child.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        child.communicate(timeout=5)


def _child_lines(returncode: int, stdout: str, stderr: str) -> list[str]:
    """The lines the child's answer carries, or the refusal it carries, or
    the refusal of a child that answered nothing (one that left through
    `os._exit`, or closed its output)."""
    lines = stdout.splitlines()
    answer: Any = None
    if lines:
        try:
            answer = json.loads(lines[-1])
        except ValueError:
            answer = None
    if isinstance(answer, dict) and isinstance(answer.get("lines"), list):
        return [str(line) for line in answer["lines"]]
    if isinstance(answer, dict) and isinstance(answer.get("error"), dict):
        raise AssayError.from_json(answer["error"])
    tail = stderr.strip()[-300:]
    raise AssayError(
        f"analysis exited with status {returncode} without an answer",
        code="PYTHON_FAILED",
        hint=(
            f"the child's last words: {tail}"
            if tail
            else "the child wrote nothing; the source may have closed its output or left through os._exit"
        ),
    )


def run_python_child(root: str) -> int:
    """The `python` tool's child (`python -m assay.server --run-python DIR`):
    the source on stdin, the run loaded from `root`, `run_python` over it
    with its output captured, and one JSON line on stdout at the end,
    `{"lines": [...]}` or `{"error": {...}}`: a refusal as its object,
    anything else the source raised, SystemExit included, as
    `PYTHON_FAILED`."""
    source = sys.stdin.read()
    buffer = io.StringIO()
    answer: dict[str, Any]
    try:
        run = Run.load(RunPaths(Path(root)), strict=False)
        with contextlib.redirect_stdout(buffer):
            run_python(run, source)
    except AssayError as error:
        answer = {"error": error.to_json()}
    except BaseException as error:  # noqa: BLE001 - the source's own exit, interrupt or anything else
        failure = AssayError(f"analysis failed: {type(error).__name__}: {error}", code="PYTHON_FAILED")
        answer = {"error": failure.to_json()}
    else:
        answer = {"lines": buffer.getvalue().splitlines()}
    sys.stdout.write(_document(answer) + "\n")
    sys.stdout.flush()
    return 0


def _state_declare(paths: RunPaths, status: CommandStatus, request: StateDeclareRequest) -> Outcome:
    spec = declare_state(status.run, request.name, path=request.path, file=request.file)
    return _lines([state_declared_text(request.name, spec)])


def _state_list(paths: RunPaths, status: CommandStatus, request: StateListRequest) -> Outcome:
    listing = state_list_of(status.run, fresh=request.read)
    return Outcome(listing.to_json(), "\n".join(state_list_text(listing)))


def _model_init(paths: RunPaths, status: CommandStatus, request: ModelInitRequest) -> Outcome:
    return _lines([model_created_text(init_model(paths))])


def _model_replay(paths: RunPaths, status: CommandStatus, request: ModelReplayRequest) -> Outcome:
    return _lines(fit_lines(replay_model(status.run)))


def _model_solve(paths: RunPaths, status: CommandStatus, request: ModelSolveRequest) -> Outcome:
    result = solve_model(
        status.run,
        request.to,
        seconds=request.seconds,
        max_nodes=request.max_nodes,
        max_depth=request.max_depth,
    )
    return _lines(solve_lines(result))


def _module_list(paths: RunPaths, status: CommandStatus, request: ModuleListRequest) -> Outcome:
    listing = module_list_of(status.run)
    return Outcome(listing.to_json(), "\n".join(module_list_text(listing)))


def _goal_propose(paths: RunPaths, status: CommandStatus, request: GoalProposeRequest) -> Outcome:
    return _lines([proposal_text(propose_goal(status.run, request.text, request.because))])


def _goal_list(paths: RunPaths, status: CommandStatus, request: GoalListRequest) -> Outcome:
    return _lines(proposals_text(list_proposals(status.run)))


# The table, in the command line's order: every agent-facing operation, the
# daemon ones with the operation they forward to.
TOOLS: tuple[Tool, ...] = (
    Tool(
        "status",
        "The full picture: progress, the observation, the registered actions, the meters, the "
        "recent results, the notes. The first call of a session, and the one after any context loss.",
        StatusRequest,
        _status,
    ),
    Tool(
        "view",
        "Inspect one event: the observation, the delta since the previous one, the history.",
        ViewRequest,
        _view,
    ),
    Tool(
        "act",
        "Take one registered action with a prediction; the result is graded against it and the "
        "receipt comes back as the text the command line prints (format json for the record). The "
        "prediction is required unless the registry sets gate: optional. The claim grammar follows.",
        ActRequest,
        _act,
        operation=ACT,
        epilog=claims_help,
    ),
    Tool(
        "commit",
        "Run a prediction-checked batch (steps, each with its own prediction) or a model plan "
        "written by model_solve; halts on the first miss. The claim grammar follows.",
        CommitRequest,
        _commit,
        operation=COMMIT,
        epilog=claims_help,
    ),
    Tool(
        "reset",
        "Pay one action to rewind the current progress unit; completed units and the journal "
        "are never lost.",
        ResetRequest,
        _reset,
        operation=RESET,
    ),
    Tool(
        "python",
        "Run offline Python with the history preloaded (observations, transitions, actions, "
        "key_delta, delta_lines, bfs, astar, np, json), in a fresh process per call, 120 seconds "
        "at most; thinking is free, probing is paid.",
        PythonRequest,
        _python,
    ),
    Tool(
        "state_declare",
        "Declare an addressable state, a named reading of the observation, by a dotted path "
        "into it or by an extractor file; claims like `ch NAME = V` then parse and grade.",
        StateDeclareRequest,
        _state_declare,
    ),
    Tool(
        "state_list",
        "List the registered states with their current readings.",
        StateListRequest,
        _state_list,
    ),
    Tool(
        "model_init",
        "Create the model.py template: declare STATES, define next().",
        ModelInitRequest,
        _model_init,
    ),
    Tool(
        "model_replay",
        "Grade model.py's declared states over every recorded transition; replay-fit is trust.",
        ModelReplayRequest,
        _model_replay,
    ),
    Tool(
        "model_solve",
        "Search the model for a plan to a state target; the plan is written to "
        ".assay/model_plan.json for commit.",
        ModelSolveRequest,
        _model_solve,
    ),
    Tool(
        "module_list",
        "The active behavior modules with their mode and origin, plus the ignored files.",
        ModuleListRequest,
        _module_list,
    ),
    Tool(
        "goal_propose",
        "Propose a revision of the standing goal; journaled, and only the owner ratifies it.",
        GoalProposeRequest,
        _goal_propose,
    ),
    Tool("goal_list", "The goal proposals and their status.", GoalListRequest, _goal_list),
    Tool(
        "audit",
        "Recompute the journal's integrity: the chain, the anchors, the ungated events.",
        AuditRequest,
        _audit,
    ),
)


def tool_named(name: str) -> Tool:
    """The tool of this name; anything else is refused by name, as the daemon
    refuses an unknown operation."""
    for tool in TOOLS:
        if tool.name == name:
            return tool
    raise AssayError(
        f"unknown tool {name!r}",
        code="OPERATION_UNKNOWN",
        hint="the tools are " + ", ".join(tool.name for tool in TOOLS),
    )


def _format_of(tool: Tool, arguments: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """The surface's `format` argument taken off the arguments, the rest
    being the record's."""
    given = dict(arguments or {})
    chosen = given.pop("format", "text")
    if chosen not in FORMATS:
        raise AssayError(
            f"{tool.name}.format must be text or json, got {chosen!r}",
            code="REQUEST_MALFORMED",
            hint=str(FORMAT_PROPERTY["description"]),
        )
    return str(chosen), given


def decode_arguments(tool: Tool, arguments: Mapping[str, Any] | None) -> Any:
    """The request record of a call, from its arguments: checked against the
    record here, so the agent reads the refusal before the socket does (the
    daemon checks a wire record again), in the daemon's words."""
    try:
        return tool.request.from_json({} if arguments is None else arguments)
    except (TypeError, KeyError) as error:
        if isinstance(error, KeyError):
            message = f"{tool.name}.{error.args[0]} is required"
        else:
            message = str(error)
        schema = tool.input_schema()
        fields = ", ".join(schema["properties"]) or "no fields"
        required = ", ".join(schema["required"])
        raise AssayError(
            message,
            code="REQUEST_MALFORMED",
            hint=(
                f"the {tool.name} tool takes {fields}"
                + (f" (required: {required})" if required else "")
                + ", the fields of its request record, and format (text or json)"
            ),
        ) from error


@dataclasses.dataclass(frozen=True)
class Answer:
    """What a call answers (section 7.5): the text block (the prose, or the
    document as JSON under `json`), the structured content (the document
    under `json`, the error object on a refusal, None otherwise), and
    whether it is an error."""

    text: str
    structured: dict[str, Any] | None
    error: bool


def _answered(outcome: Outcome, chosen: str) -> Answer:
    if chosen == "json":
        document = outcome.document()
        return Answer(_document(document), document, False)
    return Answer(outcome.text, None, False)


def _refused(error: AssayError, chosen: str) -> Answer:
    obj = error.to_json()
    return Answer(_document(obj) if chosen == "json" else error_text(error), obj, True)


def call_tool(paths: RunPaths, name: str, arguments: Mapping[str, Any] | None) -> Answer:
    """One tool call to its answer: the tool looked up by name, the surface's
    `format` taken off the arguments, the rest decoded into the record, the
    run loaded from disk under the lock with its activity record (the
    command line's path, `surface: "mcp"`), the tool run. A refusal is the
    error object; anything else, whatever it is, is `INTERNAL` with the
    traceback in `.assay/server.log`, never in the answer, so nothing an
    agent causes leaves the worker."""
    chosen = "text"
    try:
        tool = tool_named(name)
        chosen, given = _format_of(tool, arguments)
        request = decode_arguments(tool, given)
        require_run(paths)
        with run_lock(paths):
            run = Run.load(paths, strict=False)
            with command_status(run, tool.name, surface=TOOL_SURFACE) as status:
                outcome = tool.run(paths, status, request)
        return _answered(outcome, chosen)
    except AssayError as error:
        return _refused(error, chosen)
    except BaseException as error:  # noqa: BLE001 - one error voice, never a traceback to the agent
        return _refused(_internal(paths, name, error), chosen)


def _internal(paths: RunPaths, name: str, error: BaseException) -> AssayError:
    saved = _log_failure(paths, name)
    return AssayError(
        f"{type(error).__name__}: {str(error)[:300]}",
        code="INTERNAL",
        hint=f"{saved}; report it with the tool call that produced it",
    )


def _log_failure(paths: RunPaths, name: str) -> str:
    """The traceback of the failure being handled, appended to the server's
    log under its caps: the entry cut to SERVER_LOG_ENTRY_CHARS, the file
    kept under SERVER_LOG_BYTES by dropping its head. Returns the words the
    hint says about where it went."""
    log = paths.state / SERVER_LOG
    entry = f"{now_iso()} | tool {name}\n{traceback.format_exc()[-SERVER_LOG_ENTRY_CHARS:]}\n"
    try:
        existing = log.read_bytes() if log.exists() else b""
        data = existing + entry.encode()
        if len(data) > SERVER_LOG_BYTES:
            data = data[-SERVER_LOG_BYTES:]
            data = data[data.find(b"\n") + 1 :]
        log.write_bytes(data)
    except OSError:
        return "the traceback could not be saved"
    return f"traceback in {log}"


def _document(value: Any) -> str:
    """A document as the command line prints one: compact, with the
    journal's separators and no NaN."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


# --- the server ---------------------------------------------------------------


def _import_mcp() -> tuple[Any, Any, Any]:
    """The `mcp` package (the `server` extra), imported here and nowhere
    else in the kernel: its `Server` class, its stdio transport and its
    wire types. Through `importlib`, so the kernel's strict typing holds
    with or without the extra installed."""
    try:
        return (
            importlib.import_module("mcp.server"),
            importlib.import_module("mcp.server.stdio"),
            importlib.import_module("mcp.types"),
        )
    except ImportError as error:
        raise AssayError(
            f"the tool server needs the mcp package, which {sys.executable} cannot import ({error})",
            code="EXTRA_MISSING",
            hint="install the server extra: pip install 'assay-harness[server]'",
        ) from error


def serve(paths: RunPaths) -> int:
    """`assay serve-tools`: the tools of `TOOLS` over this process's stdin
    and stdout, for the one run in `paths`, until the client closes its end.
    The run must exist; the daemon need not (an offline tool runs from
    disk, a paid one refuses with `DAEMON_UNAVAILABLE` until `assay start`
    resumes it). The schemas are listed once, for this run: its pinned
    registry cannot change for the run's life."""
    require_run(paths)
    lowlevel, stdio, types = _import_mcp()
    registry = Run.load(paths, strict=False).registry
    gate = gate_mode(registry) if registry is not None else None
    listed = [
        types.Tool(name=tool.name, description=tool.listed_description(), inputSchema=listed_schema(tool, gate))
        for tool in TOOLS
    ]

    async def list_tools(context: Any, params: Any) -> Any:
        return types.ListToolsResult(tools=listed)

    async def call(context: Any, params: Any) -> Any:
        # The call runs in a thread: the socket wait and the file lock block,
        # and the server keeps answering the client's other messages meanwhile.
        answer = await asyncio.to_thread(call_tool, paths, str(params.name), params.arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=answer.text)],
            structuredContent=answer.structured,
            isError=answer.error,
        )

    server = lowlevel.Server(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call,
    )

    async def main() -> None:
        async with stdio.stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())
    return 0


def main(argv: list[str] | None = None) -> int:
    """`python -m assay.server --run-python DIR`: the `python` tool's child.
    The server itself is `assay serve-tools`."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 2 and arguments[0] == "--run-python":
        return run_python_child(arguments[1])
    sys.stderr.write(
        "usage: python -m assay.server --run-python RUN_DIR "
        "(the python tool's child; the server is `assay serve-tools`)\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
