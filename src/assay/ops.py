"""The operation table (docs/ARCHITECTURE.md section 7.2): every operation
the harness performs, named here once, with its request and result records,
its handler and the flags the daemon and the command line read.

A daemon operation crosses the socket as one JSON line, `{"token": ...,
"op": NAME, ...the request record's fields}`, and is answered with `{"ok":
true, ...the result record's fields}` or `{"ok": false, "error": ...}`; the
fields are today's, and #13 versions the wire, names the error object and
changes the shapes (`action` and `params` as JSON). `broker._Daemon.handle`
looks the operation up here, decodes the request record, calls the handler
with the daemon, the run it holds and the record, and encodes the result:
one function per operation, the `serve_*` functions of `broker`. An offline
operation runs in the client from disk: its handler is the command-line
function, and the parser (`cli._parser`) is assembled from the `Command` of
each operation, so a command, its flags and its help text exist in one
place and the command line dispatches over the same entries.

The table binds the functions of `broker` and `cli`, and both import this
module, so it is built on first read (`operations()`); the records and the
`Operation` shape are plain module constants.

The request and result records are small frozen classes with `from_json`,
`to_json` and a hand-written `json_schema()`, the `inputSchema` of the tool
server (section 7.5, #15). Like the journal records they read the type of
every known key and refuse a wrong one, the internal-error voice, since a
malformed line is a bug of the client, never a refusal.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, Self

from .core import AssayError
from .predictions import claims_help
from .records import (
    Receipt,
    _bool,
    _int,
    _object,
    _opt_int,
    _opt_list,
    _opt_object,
    _opt_str,
    _str,
)


class Record(Protocol):
    """What a request or result record provides: the boundary between the
    wire and the kernel."""

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Self: ...

    def to_json(self) -> dict[str, Any]: ...

    @classmethod
    def json_schema(cls) -> dict[str, Any]: ...


def _schema(properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _declares(obj: Mapping[str, Any], record: str) -> dict[str, str] | None:
    declares = _opt_object(obj, record, "declares", nullable=True)
    if declares is None:
        return None
    for key, value in declares.items():
        if not isinstance(value, str):
            raise TypeError(f"{record}.declares.{key} must be a string, got {type(value).__name__}")
    return dict(declares)


_STRING_OR_NULL = {"type": ["string", "null"]}
_INTEGER_OR_NULL = {"type": ["integer", "null"]}
_DECLARES = {"type": ["object", "null"], "additionalProperties": {"type": "string"}}


# --- the daemon operations' records ------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PingRequest:
    """`ping` takes nothing."""

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> PingRequest:
        return cls()

    def to_json(self) -> dict[str, Any]:
        return {}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({})


@dataclasses.dataclass(frozen=True, slots=True)
class PingResult:
    """Liveness, and the held state for whoever compares the disk with the
    daemon's view (`broker.broker_state`, section 8.3): the chain event and
    head of the run in its memory, and the differences it refused a paid
    action over, or null while nothing changed under it. A fresh load from
    disk must agree with the first two at every quiescent point."""

    pong: bool
    chain_event: int
    chain_head: str
    tampered: str | None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> PingResult:
        record = "ping"
        return cls(
            pong=_bool(obj, record, "pong"),
            chain_event=_int(obj, record, "chain_event"),
            chain_head=_str(obj, record, "chain_head"),
            tampered=_opt_str(obj, record, "tampered", required=True, nullable=True),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "pong": self.pong,
            "chain_event": self.chain_event,
            "chain_head": self.chain_head,
            "tampered": self.tampered,
        }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {
                "pong": {"type": "boolean"},
                "chain_event": {"type": "integer"},
                "chain_head": {"type": "string"},
                "tampered": _STRING_OR_NULL,
            },
            ("pong", "chain_event", "chain_head", "tampered"),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class ObserveRequest:
    """`observe` takes nothing."""

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ObserveRequest:
        return cls()

    def to_json(self) -> dict[str, Any]:
        return {}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({})


@dataclasses.dataclass(frozen=True, slots=True)
class ObserveResult:
    """The session's current observation as the wire carries it
    (`core.normalize_observation`) and its `public_info`."""

    observation: dict[str, Any]
    public_info: dict[str, Any]

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ObserveResult:
        record = "observe"
        return cls(
            observation=_object(obj, record, "observation"),
            public_info=_object(obj, record, "public_info"),
        )

    def to_json(self) -> dict[str, Any]:
        return {"observation": self.observation, "public_info": self.public_info}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {"observation": {"type": "object"}, "public_info": {"type": "object"}},
            ("observation", "public_info"),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class ActRequest:
    """`act`: one action with its prediction. `action_token` is the
    `NAME pname=value ...` line as typed (#13 splits it into `action` and
    `params`); `predict` the claims text, null for a bare act under a
    control arm; `because` the reason; `at_event` the event guard;
    `declares` the structural declarations a gate or a module demanded."""

    action_token: str
    predict: str | None = None
    because: str | None = None
    at_event: int | None = None
    declares: dict[str, str] | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ActRequest:
        record = "act"
        return cls(
            action_token=_str(obj, record, "action_token"),
            predict=_opt_str(obj, record, "predict", nullable=True),
            because=_opt_str(obj, record, "because", nullable=True),
            at_event=_opt_int(obj, record, "at_event", nullable=True),
            declares=_declares(obj, record),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "action_token": self.action_token,
            "predict": self.predict,
            "because": self.because,
            "at_event": self.at_event,
            "declares": self.declares,
        }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {
                "action_token": {"type": "string"},
                "predict": _STRING_OR_NULL,
                "because": _STRING_OR_NULL,
                "at_event": _INTEGER_OR_NULL,
                "declares": _DECLARES,
            },
            ("action_token",),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class CommitRequest:
    """`commit`: a model plan (`plan`, the file reference) or a hand-written
    batch (`steps`, each `NAME pname=value :: claims`), with the event guard
    and the declarations a module demanded for a step."""

    plan: str | None = None
    steps: tuple[str, ...] = ()
    at_event: int | None = None
    declares: dict[str, str] | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> CommitRequest:
        record = "commit"
        steps = _opt_list(obj, record, "steps", nullable=True)
        return cls(
            plan=_opt_str(obj, record, "plan", nullable=True),
            steps=tuple(str(item) for item in steps or ()),
            at_event=_opt_int(obj, record, "at_event", nullable=True),
            declares=_declares(obj, record),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "steps": list(self.steps),
            "at_event": self.at_event,
            "declares": self.declares,
        }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {
                "plan": _STRING_OR_NULL,
                "steps": {"type": "array", "items": {"type": "string"}},
                "at_event": _INTEGER_OR_NULL,
                "declares": _DECLARES,
            }
        )


@dataclasses.dataclass(frozen=True, slots=True)
class ResetRequest:
    """`reset`: the reason (required unless the state is GAME_OVER), the
    event guard and the declarations a module demanded."""

    because: str | None = None
    at_event: int | None = None
    declares: dict[str, str] | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ResetRequest:
        record = "reset"
        return cls(
            because=_opt_str(obj, record, "because", nullable=True),
            at_event=_opt_int(obj, record, "at_event", nullable=True),
            declares=_declares(obj, record),
        )

    def to_json(self) -> dict[str, Any]:
        return {"because": self.because, "at_event": self.at_event, "declares": self.declares}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {"because": _STRING_OR_NULL, "at_event": _INTEGER_OR_NULL, "declares": _DECLARES}
        )


@dataclasses.dataclass(frozen=True, slots=True)
class ReceiptResult:
    """What a paid operation returns: the `Receipt` record (`records.py`),
    under `receipt` on the wire."""

    receipt: Receipt

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ReceiptResult:
        return cls(receipt=Receipt.from_json(_object(obj, "result", "receipt")))

    def to_json(self) -> dict[str, Any]:
        return {"receipt": self.receipt.to_json()}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {"receipt": {"type": "object", "description": "the Receipt record"}}, ("receipt",)
        )


@dataclasses.dataclass(frozen=True, slots=True)
class InstallModuleRequest:
    """`install_module`: the module file's path and the owner token, which
    the daemon checks against the hash it holds (section 6.4)."""

    path: str
    owner_token: str | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> InstallModuleRequest:
        record = "install_module"
        return cls(
            path=_str(obj, record, "path"),
            owner_token=_opt_str(obj, record, "owner_token", nullable=True),
        )

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "owner_token": self.owner_token}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({"path": {"type": "string"}, "owner_token": _STRING_OR_NULL}, ("path",))


@dataclasses.dataclass(frozen=True, slots=True)
class InstallModuleResult:
    """The manifest entry of the installed module, as journaled."""

    record: dict[str, Any]

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> InstallModuleResult:
        return cls(record=_object(obj, "install_module", "record"))

    def to_json(self) -> dict[str, Any]:
        return {"record": self.record}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({"record": {"type": "object"}}, ("record",))


# --- the command-line surface -------------------------------------------------

Handler = Callable[..., Any]


@dataclasses.dataclass(frozen=True)
class Argument:
    """One `add_argument` call of a command: the flags, then the keyword
    arguments exactly as the parser spells them."""

    flags: tuple[str, ...]
    options: Mapping[str, Any]


def arg(*flags: str, **options: Any) -> Argument:
    return Argument(flags, options)


@dataclasses.dataclass(frozen=True)
class Group:
    """A command that holds sub-commands (`assay channel declare`): its
    name, its help and the namespace field the chosen sub-command lands
    in."""

    name: str
    help: str
    dest: str


@dataclasses.dataclass(frozen=True)
class Command:
    """The command-line surface of an operation, from which the parser is
    built. `epilog` renders only when help does (the claims table imports
    the observation kinds). `lifecycle` marks the commands that run before
    any run is loaded (start, stop, version, doctor) and return the exit
    status. `run` is what the command line executes for a daemon operation,
    its client; for an offline operation the handler is the function."""

    name: str
    help: str
    arguments: tuple[Argument, ...] = ()
    epilog: Callable[[], str] | None = None
    group: Group | None = None
    lifecycle: bool = False
    run: Handler | None = None


CHANNEL = Group("channel", "declare and list registered channels (named readings)", "channel_command")
MODEL = Group(
    "model",
    "the general world-model tier: replay-fit is trust; fit models earn batching",
    "model_command",
)
MODULE = Group("module", "behavior modules: list the active set, install one (owner)", "module_command")
GOAL = Group("goal", "the standing goal: propose revisions (agent), ratify (owner)", "goal_command")
SPEND = Group("spend", "the external spend feed (the kernel cannot see the LLM bill)", "spend_command")
GROUPS = (CHANNEL, MODEL, MODULE, GOAL, SPEND)


# --- the table ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Operation:
    """One operation (section 7.2). `request` and `result` are the record
    classes of a daemon operation, None for an offline one until #13 gives
    the offline results their records; `handler` is the daemon's function,
    `(daemon, run, request) -> result`, or the command-line function of an
    offline operation; `paid` marks the operations that spend, `owner` the
    ones that need the owner token, `offline` the ones the client runs from
    disk without the daemon; `command` is the command-line surface, None
    for the operations the client uses on its own (`ping`, `observe`)."""

    name: str
    request: type[Record] | None
    result: type[Record] | None
    handler: Handler
    paid: bool = False
    owner: bool = False
    offline: bool = False
    command: Command | None = None

    @property
    def path(self) -> str | None:
        """The command path (`channel declare`), None without a command."""
        command = self.command
        if command is None:
            return None
        return command.name if command.group is None else f"{command.group.name} {command.name}"

    def decode(self, obj: Mapping[str, Any]) -> Any:
        """The request record from the wire fields, None for an operation
        without one."""
        return None if self.request is None else self.request.from_json(obj)


def _offline(name: str, handler: Handler, command: Command, *, owner: bool = False) -> Operation:
    return Operation(name, None, None, handler, owner=owner, offline=True, command=command)


_DECLARE = ("--declare",)
_AT = arg("--at", type=int, dest="at_event")


@functools.cache
def operations() -> tuple[Operation, ...]:
    """The table, in the order the command line lists its commands. Built on
    first read, since it binds the daemon's handlers and the command line's
    functions, and both modules import this one. The owner operations
    `approve`, `waive` and `goal ratify` are offline here and move into the
    daemon with #13."""
    from . import broker, cli

    return (
        Operation("ping", PingRequest, PingResult, broker.serve_ping),
        Operation("observe", ObserveRequest, ObserveResult, broker.serve_observe),
        _offline(
            "start",
            cli.start_command,
            Command(
                "start",
                "start or resume the one persistent run",
                arguments=(
                    arg(
                        "game_id",
                        metavar="world_id",
                        help="a label for this run, kept as given: up to 64 characters with no whitespace, "
                        "control characters or path separators. A benchmark adapter may read it to pick the instance",
                    ),
                    arg("--seed", type=int, default=0, help=argparse.SUPPRESS),
                    arg(
                        "--adapter",
                        help="world adapter factory: module:factory or /path/file.py:factory",
                    ),
                    arg(
                        "--registry",
                        type=Path,
                        help="JSON file registering the actions, their parameter schemas and the action "
                        "budget; required for a fresh run, optional on resume (the pinned one is used)",
                    ),
                    arg(
                        "--mode",
                        choices=(broker.LOCAL_MODE, broker.REMOTE_MODE),
                        help="local simulator (default), or expiring remote competition validation",
                    ),
                    arg(
                        "--import",
                        dest="import_knowledge",
                        type=Path,
                        metavar="KNOWLEDGE.json",
                        help="import a prior run's exported knowledge (lands FOREIGN, demoted)",
                    ),
                    arg(
                        "--owner-token-file",
                        type=Path,
                        metavar="PATH",
                        help="write the owner token to this file (mode 0600, outside the run "
                        "directory) instead of printing it; ASSAY_OWNER_TOKEN_FILE does the same",
                    ),
                ),
                lifecycle=True,
            ),
        ),
        _offline(
            "stop",
            cli.stop_command,
            Command(
                "stop",
                "stop this run's environment owner (the daemon) cleanly; "
                "`assay start` resumes the run later",
                lifecycle=True,
            ),
        ),
        _offline(
            "version",
            cli.version_command,
            Command(
                "version",
                "the harness version, the journal spec it writes, the interpreter",
                lifecycle=True,
            ),
        ),
        _offline(
            "doctor",
            cli.doctor_command,
            Command(
                "doctor",
                "check the interpreter, dependencies, anchors, socket path, run "
                "state, daemon, adapter and registry; works with or without a run here",
                lifecycle=True,
            ),
        ),
        _offline(
            "status",
            cli.status_command,
            Command(
                "status",
                "full picture: progress, image, actions, recent results, notes",
                arguments=(arg("--history", type=int, default=8),),
            ),
        ),
        _offline(
            "view",
            cli.view_command,
            Command(
                "view",
                "inspect one event: the observation, the delta since the previous one, history",
                arguments=(
                    arg("--event", type=int),
                    arg("--history", type=int, default=0),
                    # Frame worlds only; inert on a dict run, which says so.
                    arg(
                        "--grid",
                        action="store_true",
                        help="frame worlds: print the complete exact 0-f grid",
                    ),
                    arg(
                        "--frames",
                        action="store_true",
                        help="frame worlds: show causal animation frames",
                    ),
                    arg(
                        "--crop",
                        metavar="R0:R1,C0:C1",
                        help="frame worlds: print an exact half-open crop",
                    ),
                    arg(
                        "--export",
                        type=Path,
                        metavar="FILE.npz",
                        help="frame worlds: export the grid history",
                    ),
                ),
            ),
        ),
        Operation(
            "act",
            ActRequest,
            ReceiptResult,
            broker.serve_act,
            paid=True,
            command=Command(
                "act",
                "take one action with a prediction; the result is graded against it",
                arguments=(
                    arg("action"),
                    arg("params", nargs="*", metavar="pname=value", help=argparse.SUPPRESS),
                    arg(
                        "--predict",
                        required=False,
                        help='what this action does, e.g. "change; ch counter delta = 1" (see below); required '
                        "unless the registry sets gate: optional",
                    ),
                    arg("--because", help="short reason for choosing this action"),
                    _AT,
                    arg(
                        *_DECLARE,
                        action="append",
                        default=[],
                        metavar='"field=value"',
                        help="structural declaration a gate or module demanded "
                        '(e.g. --declare "worst_case=..." --declare "recovery=...")',
                    ),
                ),
                epilog=claims_help,
                run=cli.act_command,
            ),
        ),
        Operation(
            "commit",
            CommitRequest,
            ReceiptResult,
            broker.serve_commit,
            paid=True,
            command=Command(
                "commit",
                "run a prediction-checked batch or a model plan; halts on the first miss",
                arguments=(
                    arg(
                        "plan",
                        nargs="?",
                        help="a plan file, e.g. @.assay/model_plan.json from `assay model solve`",
                    ),
                    arg(
                        "--step",
                        action="append",
                        default=[],
                        metavar='"ACTION :: CLAIMS"',
                        help="one action with its own prediction; repeat in execution order",
                    ),
                    _AT,
                    arg(
                        *_DECLARE,
                        action="append",
                        default=[],
                        metavar='"field=value"',
                        help="structural declaration a module demanded for a step in this batch",
                    ),
                ),
                epilog=claims_help,
                run=cli.commit_command,
            ),
        ),
        Operation(
            "reset",
            ResetRequest,
            ReceiptResult,
            broker.serve_reset,
            paid=True,
            command=Command(
                "reset",
                "pay one action to rewind the current progress unit",
                arguments=(
                    arg(
                        "--because",
                        help="why the current state is worth abandoning (required unless GAME_OVER)",
                    ),
                    _AT,
                    arg(
                        *_DECLARE,
                        action="append",
                        default=[],
                        metavar='"field=value"',
                        help="structural declaration a module demanded for this reset "
                        '(e.g. --declare "impossible=..." --declare "coverage_audit=...")',
                    ),
                ),
                run=cli.reset_command,
            ),
        ),
        _offline(
            "python",
            cli.python_command,
            Command(
                "python",
                "run offline Python with the history, deltas, BFS and A* preloaded",
                arguments=(arg("source", nargs="?"), arg("--file", type=Path)),
            ),
        ),
        _offline(
            "channel declare",
            cli.channel_declare,
            Command(
                "declare",
                "register a named reading of the observation",
                arguments=(
                    arg("name"),
                    arg("--path", help="dotted keys into the dict observation, e.g. counters.red"),
                    arg("--file", help="extractor file: def extract(obs) -> value (sandboxed)"),
                ),
                group=CHANNEL,
            ),
        ),
        _offline(
            "channel list",
            cli.channel_list,
            Command(
                "list",
                "list registered channels with their current readings",
                arguments=(
                    arg(
                        "--read",
                        action="store_true",
                        help="compute extractor channels fresh (runs each extractor sandboxed) "
                        "instead of showing the last graded reading",
                    ),
                ),
                group=CHANNEL,
            ),
        ),
        _offline(
            "model init",
            cli.model_init,
            Command("init", "create a model.py template", group=MODEL),
        ),
        _offline(
            "model replay",
            cli.model_replay,
            Command(
                "replay",
                "grade model.py's declared channels over every recorded transition",
                group=MODEL,
            ),
        ),
        _offline(
            "model solve",
            cli.model_solve,
            Command(
                "solve",
                "search the model for a plan to a channel target",
                arguments=(
                    arg("--to", required=True, metavar='"ch NAME = V"', help="the goal reading"),
                    arg("--seconds", type=float, default=15.0),
                    arg("--max-nodes", type=int, default=100_000),
                    arg("--max-depth", type=int, default=40),
                ),
                group=MODEL,
            ),
        ),
        _offline(
            "module list",
            cli.module_list,
            Command(
                "list",
                "active modules with mode and origin, plus ignored files",
                group=MODULE,
            ),
        ),
        Operation(
            "install_module",
            InstallModuleRequest,
            InstallModuleResult,
            broker.serve_install_module,
            owner=True,
            command=Command(
                "install",
                "owner: install a module file mid-run (journaled, manifest-pinned)",
                arguments=(arg("path", type=Path), arg("--token")),
                group=MODULE,
                run=cli.module_install,
            ),
        ),
        _offline(
            "goal propose",
            cli.goal_propose,
            Command(
                "propose",
                "propose a standing-goal revision (journaled, owner ratifies)",
                arguments=(arg("text"), arg("--because")),
                group=GOAL,
            ),
        ),
        _offline(
            "goal list",
            cli.goal_list,
            Command("list", "list goal proposals and their status", group=GOAL),
        ),
        _offline(
            "goal ratify",
            cli.goal_ratify,
            Command(
                "ratify",
                "owner: ratify a proposal by id (requires the owner token)",
                arguments=(arg("id", type=int), arg("--token")),
                group=GOAL,
            ),
            owner=True,
        ),
        _offline(
            "export",
            cli.export_command,
            Command(
                "export",
                "export this run's earned knowledge for a future import",
                arguments=(arg("--out", type=Path),),
            ),
        ),
        _offline(
            "spend report",
            cli.spend_report,
            Command(
                "report",
                "post cumulative usage (idempotent by --id; last entry wins)",
                arguments=(
                    arg("--usd", type=float, required=True),
                    arg("--tokens", type=int, default=0),
                    arg("--id", dest="report_id", required=True),
                ),
                group=SPEND,
            ),
        ),
        _offline(
            "audit",
            cli.audit_command,
            Command("audit", "recompute journal integrity: chain, anchors, ungated events"),
        ),
        _offline(
            "approve",
            cli.approve_command,
            Command(
                "approve",
                "owner: grant one use of an approval-gated action",
                arguments=(arg("action"), arg("--token")),
            ),
            owner=True,
        ),
        _offline(
            "waive",
            cli.waive_command,
            Command(
                "waive",
                "owner: waive a live actuator's rehearsal quota (journaled)",
                arguments=(arg("action"), arg("--token"), arg("--because")),
            ),
            owner=True,
        ),
    )


def command_operation(args: argparse.Namespace) -> Operation:
    """The operation a parsed command line names: for a group, the
    sub-command's (`channel declare`)."""
    path = str(args.command)
    for group in GROUPS:
        if path == group.name:
            path = f"{group.name} {getattr(args, group.dest)}"
            break
    for operation in operations():
        if operation.command is not None and operation.path == path:
            return operation
    raise AssayError(f"unsupported command {args.command}")


def daemon_operation(name: Any) -> Operation:
    """The daemon operation of this name; anything else, the retired `step`
    included, is refused by name (#13 names the code)."""
    for operation in operations():
        if operation.name == name and not operation.offline:
            return operation
    raise AssayError(f"unknown broker operation {name!r}")
