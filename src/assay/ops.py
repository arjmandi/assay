"""The wire table (docs/ARCHITECTURE.md section 7.2): the daemon operations
named once, each with its request and result record and the flags the daemon
reads. Nothing that runs: the daemon binds one handler to each name beside
its handlers (`broker.HANDLERS`) and the command line binds a command to the
operations it exposes (`cli.COMMANDS`), so this module imports neither, and
the tool server (section 7.5, #15) reads it alone for its six tools.

A daemon operation crosses the socket as one JSON line, `{"token": ...,
"op": NAME, ...the request record's fields}`, and is answered with `{"ok":
true, ...the result record's fields}` or `{"ok": false, "error": ...}`; the
fields are today's, and #13 versions the wire, names the error object and
changes the shapes (`action` and `params` as JSON). `broker.call` sends a
request record and decodes the result record; `broker._Daemon.handle`
decodes the request record, calls the name's handler with the daemon and the
held run, and encodes the result.

The records are small frozen classes with `from_json`, `to_json` and a
hand-written `json_schema()`, the tool server's `inputSchema`. They decode
through the kit of `records.py`: a wrong type, a missing required key and a
key the record does not take are refused in the internal-error voice, since
a malformed line is a client's bug, never a refusal, and the daemon spends
on nothing it did not read whole.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, Generic, Protocol, Self, TypeVar

from .core import AssayError
from .records import (
    Receipt,
    read_bool,
    read_int,
    read_object,
    read_opt_int,
    read_opt_lines,
    read_opt_object,
    read_opt_str,
    read_str,
    refuse_unknown,
    wrong_type,
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
    declares = read_opt_object(obj, record, "declares", nullable=True)
    if declares is None:
        return None
    for key, value in declares.items():
        if not isinstance(value, str):
            raise wrong_type(record, f"declares.{key}", "a string", value)
    return dict(declares)


_STRING_OR_NULL = {"type": ["string", "null"]}
_INTEGER_OR_NULL = {"type": ["integer", "null"]}
_DECLARES = {"type": ["object", "null"], "additionalProperties": {"type": "string"}}


# --- the records ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PingRequest:
    """`ping` takes nothing."""

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> PingRequest:
        refuse_unknown(obj, "ping", frozenset())
        return cls()

    def to_json(self) -> dict[str, Any]:
        return {}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({})


_PING_RESULT_KEYS = frozenset({"pong", "chain_event", "chain_head", "tampered"})


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
        refuse_unknown(obj, record, _PING_RESULT_KEYS)
        return cls(
            pong=read_bool(obj, record, "pong"),
            chain_event=read_int(obj, record, "chain_event"),
            chain_head=read_str(obj, record, "chain_head"),
            tampered=read_opt_str(obj, record, "tampered", required=True, nullable=True),
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
        refuse_unknown(obj, "observe", frozenset())
        return cls()

    def to_json(self) -> dict[str, Any]:
        return {}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({})


_OBSERVE_RESULT_KEYS = frozenset({"observation", "public_info"})


@dataclasses.dataclass(frozen=True, slots=True)
class ObserveResult:
    """The session's current observation as the wire carries it
    (`core.normalize_observation`) and its `public_info`."""

    observation: dict[str, Any]
    public_info: dict[str, Any]

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ObserveResult:
        record = "observe"
        refuse_unknown(obj, record, _OBSERVE_RESULT_KEYS)
        return cls(
            observation=read_object(obj, record, "observation"),
            public_info=read_object(obj, record, "public_info"),
        )

    def to_json(self) -> dict[str, Any]:
        return {"observation": self.observation, "public_info": self.public_info}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {"observation": {"type": "object"}, "public_info": {"type": "object"}},
            ("observation", "public_info"),
        )


_ACT_KEYS = frozenset({"action_token", "predict", "because", "at_event", "declares"})


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
        refuse_unknown(obj, record, _ACT_KEYS)
        return cls(
            action_token=read_str(obj, record, "action_token"),
            predict=read_opt_str(obj, record, "predict", nullable=True),
            because=read_opt_str(obj, record, "because", nullable=True),
            at_event=read_opt_int(obj, record, "at_event", nullable=True),
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


_COMMIT_KEYS = frozenset({"plan", "steps", "at_event", "declares"})


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
        refuse_unknown(obj, record, _COMMIT_KEYS)
        return cls(
            plan=read_opt_str(obj, record, "plan", nullable=True),
            steps=read_opt_lines(obj, record, "steps") or (),
            at_event=read_opt_int(obj, record, "at_event", nullable=True),
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


_RESET_KEYS = frozenset({"because", "at_event", "declares"})


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
        refuse_unknown(obj, record, _RESET_KEYS)
        return cls(
            because=read_opt_str(obj, record, "because", nullable=True),
            at_event=read_opt_int(obj, record, "at_event", nullable=True),
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
        refuse_unknown(obj, "result", frozenset({"receipt"}))
        return cls(receipt=Receipt.from_json(read_object(obj, "result", "receipt")))

    def to_json(self) -> dict[str, Any]:
        return {"receipt": self.receipt.to_json()}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {"receipt": {"type": "object", "description": "the Receipt record"}}, ("receipt",)
        )


_INSTALL_KEYS = frozenset({"path", "owner_token"})


@dataclasses.dataclass(frozen=True, slots=True)
class InstallModuleRequest:
    """`install_module`: the module file's path and the owner token, which
    the daemon checks against the hash it holds (section 6.4)."""

    path: str
    owner_token: str | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> InstallModuleRequest:
        record = "install_module"
        refuse_unknown(obj, record, _INSTALL_KEYS)
        return cls(
            path=read_str(obj, record, "path"),
            owner_token=read_opt_str(obj, record, "owner_token", nullable=True),
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
        refuse_unknown(obj, "install_module", frozenset({"record"}))
        return cls(record=read_object(obj, "install_module", "record"))

    def to_json(self) -> dict[str, Any]:
        return {"record": self.record}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({"record": {"type": "object"}}, ("record",))


# --- the table ----------------------------------------------------------------

Req = TypeVar("Req", bound=Record)
Res = TypeVar("Res", bound=Record)


@dataclasses.dataclass(frozen=True)
class Operation(Generic[Req, Res]):
    """One daemon operation: its name on the wire, its request and result
    records, and whether it spends (`paid`) or needs the owner token
    (`owner`, checked by the daemon against the held hash)."""

    name: str
    request: type[Req]
    result: type[Res]
    paid: bool = False
    owner: bool = False


PING: Operation[PingRequest, PingResult] = Operation("ping", PingRequest, PingResult)
OBSERVE: Operation[ObserveRequest, ObserveResult] = Operation(
    "observe", ObserveRequest, ObserveResult
)
ACT: Operation[ActRequest, ReceiptResult] = Operation("act", ActRequest, ReceiptResult, paid=True)
COMMIT: Operation[CommitRequest, ReceiptResult] = Operation(
    "commit", CommitRequest, ReceiptResult, paid=True
)
RESET: Operation[ResetRequest, ReceiptResult] = Operation(
    "reset", ResetRequest, ReceiptResult, paid=True
)
INSTALL_MODULE: Operation[InstallModuleRequest, InstallModuleResult] = Operation(
    "install_module", InstallModuleRequest, InstallModuleResult, owner=True
)

OPERATIONS: tuple[Operation[Any, Any], ...] = (PING, OBSERVE, ACT, COMMIT, RESET, INSTALL_MODULE)


def daemon_operation(name: Any) -> Operation[Any, Any]:
    """The operation of this name; anything else, the retired `step`
    included, is refused by name (#13 names the code)."""
    for operation in OPERATIONS:
        if operation.name == name:
            return operation
    raise AssayError(f"unknown broker operation {name!r}")
