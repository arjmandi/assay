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

import dataclasses
import functools
from collections.abc import Callable, Mapping
from typing import Any, Protocol, Self

from .core import AssayError
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


# --- the table ----------------------------------------------------------------

Handler = Callable[..., Any]


@dataclasses.dataclass(frozen=True)
class Operation:
    """One operation (section 7.2). `request` and `result` are the record
    classes of a daemon operation, None for an offline one until #13 gives
    the offline results their records; `handler` is the daemon's function,
    `(daemon, run, request) -> result`, or the command-line function of an
    offline operation; `paid` marks the operations that spend, `owner` the
    ones that need the owner token, `offline` the ones the client runs from
    disk without the daemon."""

    name: str
    request: type[Record] | None
    result: type[Record] | None
    handler: Handler
    paid: bool = False
    owner: bool = False
    offline: bool = False

    def decode(self, obj: Mapping[str, Any]) -> Any:
        """The request record from the wire fields, None for an operation
        without one."""
        return None if self.request is None else self.request.from_json(obj)


@functools.cache
def operations() -> tuple[Operation, ...]:
    """The table. Built on first read, since it binds the daemon's handlers
    and the daemon imports this module."""
    from . import broker

    return (
        Operation("ping", PingRequest, PingResult, broker.serve_ping),
        Operation("observe", ObserveRequest, ObserveResult, broker.serve_observe),
        Operation("act", ActRequest, ReceiptResult, broker.serve_act, paid=True),
        Operation("commit", CommitRequest, ReceiptResult, broker.serve_commit, paid=True),
        Operation("reset", ResetRequest, ReceiptResult, broker.serve_reset, paid=True),
        Operation(
            "install_module",
            InstallModuleRequest,
            InstallModuleResult,
            broker.serve_install_module,
            owner=True,
        ),
    )


def daemon_operation(name: Any) -> Operation:
    """The daemon operation of this name; anything else, the retired `step`
    included, is refused by name (#13 names the code)."""
    for operation in operations():
        if operation.name == name and not operation.offline:
            return operation
    raise AssayError(f"unknown broker operation {name!r}")
