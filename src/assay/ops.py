"""The wire table (docs/ARCHITECTURE.md section 7.2): the daemon operations
named once, each with its request and result record and the flags the daemon
reads. Nothing that runs: the daemon binds one handler to each name beside
its handlers (`broker.HANDLERS`) and the command line binds a command to the
operations it exposes (`cli.COMMANDS`), so this module imports neither, and
the tool server (section 7.5, #15) reads it alone for its tools.

The owner operations (`install_module`, `approve`, `waive`, `goal_ratify`)
carry the owner's token as `owner_token`, since `token` on the wire is the
daemon's own; the daemon checks it against the hash it holds (section 8.1).

The protocol is versioned (`PROTOCOL_VERSION`, 2 for this package). A daemon
operation crosses the socket as one JSON line, `{"v": 2, "token": ...,
"op": NAME, "args": {...the request record's fields}}`, and is answered with
`{"v": 2, "ok": true, "result": {...the result record's fields}}` or `{"v":
2, "ok": false, "error": {"code", "kind", "message", "hint"}}`, the error
object of section 7.1 (`ERROR_SCHEMA`). Both sides check `v` before anything
else and refuse a line without it, or with another value, with
`PROTOCOL_VERSION`. `broker.call` sends a request record and decodes the
result record; `broker._Daemon.handle` decodes the request record (a body
that does not fit it is refused with `REQUEST_MALFORMED`, kind usage, before
anything spends), calls the name's handler with the daemon and the held run,
and encodes the result.

An action travels as its registered name and its parameters as a JSON
object (`action`, `params`), never as the typed token: the client parses
`NAME k=v ...` against the pinned registry or takes `--params JSON` as given,
and the daemon validates the object against the registry's schema, nested
values included, before anything spends (`registry.validate_action`); a
batch step is `{action, params, predict}`.

The records are small frozen classes with `from_json`, `to_json` and a
hand-written `json_schema()`, the tool server's `inputSchema`. They decode
through the kit of `records.py`: a wrong type, a missing required key and a
key the record does not take raise the kit's `TypeError` or `KeyError`, which
the daemon turns into the `REQUEST_MALFORMED` refusal naming the record's
fields; the daemon spends on nothing it did not read whole.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any, Generic, Protocol, Self, TypeVar

from .core import AssayError
from .errors import KINDS
from .records import (
    Receipt,
    read_bool,
    read_int,
    read_list,
    read_object,
    read_opt_int,
    read_opt_object,
    read_opt_str,
    read_str,
    refuse_unknown,
    wrong_type,
)

PROTOCOL_VERSION = 2
# One request line is at most this many bytes: the daemon reads that many and
# one more and refuses a longer line before parsing it (`broker._read_request`),
# and the command line refuses a `--params` or `--step` document past it
# before the socket sees it. A parameter without `maxLength` or `maxItems` is
# bounded by this; those keywords are the world's own caps below it.
REQUEST_LIMIT_BYTES = 1_000_000


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """The object of a JSON text with a repeated key refused: the last would
    silently win, and nothing on the wire means that."""
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"the key {key!r} is repeated")
        obj[key] = value
    return obj


def decode_json(text: str | bytes) -> Any:
    """One JSON document under the protocol's rules: a repeated key within
    an object is refused (`ValueError`), and a document nested past the
    interpreter's limit raises `RecursionError`; the caller words the
    refusal in its own code (`REQUEST_MALFORMED` on the daemon,
    `COMMAND_ARGS` on the command line)."""
    return json.loads(text, object_pairs_hook=_strict_object)


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


def _params(obj: Mapping[str, Any], record: str) -> dict[str, Any] | None:
    """The parameters object, its values any JSON: the registry's schema is
    the daemon's to check (`registry.validate_action`)."""
    params = read_opt_object(obj, record, "params", nullable=True)
    return None if params is None else dict(params)


_STRING_OR_NULL = {"type": ["string", "null"]}
_INTEGER_OR_NULL = {"type": ["integer", "null"]}
_DECLARES = {"type": ["object", "null"], "additionalProperties": {"type": "string"}}
_PARAMS = {"type": ["object", "null"]}

# The error object of section 7.1, as every refused reply carries it: the
# code, its kind, the one-line message, the next step and the further lines
# the command line prints after them (the claims table), the last two null
# when the error has none.
ERROR_SCHEMA = _schema(
    {
        "code": {"type": "string"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "message": {"type": "string"},
        "hint": _STRING_OR_NULL,
        "detail": _STRING_OR_NULL,
    },
    ("code", "kind", "message", "hint", "detail"),
)


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


_ACT_KEYS = frozenset({"action", "params", "predict", "because", "at_event", "declares"})


@dataclasses.dataclass(frozen=True, slots=True)
class ActRequest:
    """`act`: one action with its prediction. `action` is the registered
    name and `params` the object of its parameters, any JSON the registry's
    schema admits, or null for an action without any (the client parses
    `NAME pname=value ...` with the pinned registry or takes `--params` as
    given; the daemon validates the object against the schema before any
    spend); `predict` the claims text, null for a bare act under a control
    arm; `because` the reason; `at_event` the event guard; `declares` the
    structural declarations a gate or a module demanded."""

    action: str
    params: dict[str, Any] | None = None
    predict: str | None = None
    because: str | None = None
    at_event: int | None = None
    declares: dict[str, str] | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ActRequest:
        record = "act"
        refuse_unknown(obj, record, _ACT_KEYS)
        return cls(
            action=read_str(obj, record, "action"),
            params=_params(obj, record),
            predict=read_opt_str(obj, record, "predict", nullable=True),
            because=read_opt_str(obj, record, "because", nullable=True),
            at_event=read_opt_int(obj, record, "at_event", nullable=True),
            declares=_declares(obj, record),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "params": self.params,
            "predict": self.predict,
            "because": self.because,
            "at_event": self.at_event,
            "declares": self.declares,
        }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {
                "action": {"type": "string"},
                "params": _PARAMS,
                "predict": _STRING_OR_NULL,
                "because": _STRING_OR_NULL,
                "at_event": _INTEGER_OR_NULL,
                "declares": _DECLARES,
            },
            ("action",),
        )


_STEP_KEYS = frozenset({"action", "params", "predict"})


@dataclasses.dataclass(frozen=True, slots=True)
class Step:
    """One step of a hand-written batch: the registered name, its parameters
    (a JSON object, or null) and its prediction, null for a bare step under
    a control arm (the client splits `NAME pname=value :: claims`, or takes
    the step as this object)."""

    action: str
    params: dict[str, Any] | None = None
    predict: str | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Step:
        record = "step"
        refuse_unknown(obj, record, _STEP_KEYS)
        return cls(
            action=read_str(obj, record, "action"),
            params=_params(obj, record),
            predict=read_opt_str(obj, record, "predict", nullable=True),
        )

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "params": self.params, "predict": self.predict}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {"action": {"type": "string"}, "params": _PARAMS, "predict": _STRING_OR_NULL},
            ("action",),
        )


def _steps(obj: Mapping[str, Any], record: str) -> tuple[Step, ...]:
    if "steps" not in obj:
        return ()
    values = read_list(obj, record, "steps")
    steps: list[Step] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise wrong_type(record, "steps", "a list of steps", value)
        steps.append(Step.from_json(value))
    return tuple(steps)


_COMMIT_KEYS = frozenset({"plan", "steps", "at_event", "declares"})


@dataclasses.dataclass(frozen=True, slots=True)
class CommitRequest:
    """`commit`: a model plan (`plan`, the file reference) or a hand-written
    batch (`steps`, each a `Step`), with the event guard and the
    declarations a module demanded for a step."""

    plan: str | None = None
    steps: tuple[Step, ...] = ()
    at_event: int | None = None
    declares: dict[str, str] | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> CommitRequest:
        record = "commit"
        refuse_unknown(obj, record, _COMMIT_KEYS)
        return cls(
            plan=read_opt_str(obj, record, "plan", nullable=True),
            steps=_steps(obj, record),
            at_event=read_opt_int(obj, record, "at_event", nullable=True),
            declares=_declares(obj, record),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "steps": [step.to_json() for step in self.steps],
            "at_event": self.at_event,
            "declares": self.declares,
        }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {
                "plan": _STRING_OR_NULL,
                "steps": {"type": "array", "items": Step.json_schema()},
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


_APPROVE_KEYS = frozenset({"action", "owner_token"})


@dataclasses.dataclass(frozen=True, slots=True)
class ApproveRequest:
    """`approve`: one use of an approval-gated action, granted by the owner
    and held in the daemon's memory for its 600 seconds (section 7.2)."""

    action: str
    owner_token: str | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ApproveRequest:
        record = "approve"
        refuse_unknown(obj, record, _APPROVE_KEYS)
        return cls(
            action=read_str(obj, record, "action"),
            owner_token=read_opt_str(obj, record, "owner_token", nullable=True),
        )

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "owner_token": self.owner_token}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({"action": {"type": "string"}, "owner_token": _STRING_OR_NULL}, ("action",))


_APPROVE_RESULT_KEYS = frozenset({"action", "expires_seconds"})


@dataclasses.dataclass(frozen=True, slots=True)
class ApproveResult:
    """The action as held (upper case) and the seconds the grant lives."""

    action: str
    expires_seconds: int

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ApproveResult:
        record = "approve"
        refuse_unknown(obj, record, _APPROVE_RESULT_KEYS)
        return cls(
            action=read_str(obj, record, "action"),
            expires_seconds=read_int(obj, record, "expires_seconds"),
        )

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "expires_seconds": self.expires_seconds}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {"action": {"type": "string"}, "expires_seconds": {"type": "integer"}},
            ("action", "expires_seconds"),
        )


_WAIVE_KEYS = frozenset({"action", "owner_token", "because"})


@dataclasses.dataclass(frozen=True, slots=True)
class WaiveRequest:
    """`waive`: the owner's waiver of a live actuator's rehearsal quota, with
    the reason it is safe now; the activity record it leaves is the waiver,
    rebuilt into the held set at every load (section 7.2)."""

    action: str
    owner_token: str | None = None
    because: str | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> WaiveRequest:
        record = "waive"
        refuse_unknown(obj, record, _WAIVE_KEYS)
        return cls(
            action=read_str(obj, record, "action"),
            owner_token=read_opt_str(obj, record, "owner_token", nullable=True),
            because=read_opt_str(obj, record, "because", nullable=True),
        )

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "owner_token": self.owner_token, "because": self.because}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {
                "action": {"type": "string"},
                "owner_token": _STRING_OR_NULL,
                "because": _STRING_OR_NULL,
            },
            ("action",),
        )


_WAIVE_RESULT_KEYS = frozenset({"action", "because"})


@dataclasses.dataclass(frozen=True, slots=True)
class WaiveResult:
    """The action as held (upper case) and the reason as journaled."""

    action: str
    because: str

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> WaiveResult:
        record = "waive"
        refuse_unknown(obj, record, _WAIVE_RESULT_KEYS)
        return cls(
            action=read_str(obj, record, "action"),
            because=read_str(obj, record, "because"),
        )

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "because": self.because}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema(
            {"action": {"type": "string"}, "because": {"type": "string"}}, ("action", "because")
        )


_GOAL_RATIFY_KEYS = frozenset({"id", "owner_token"})


@dataclasses.dataclass(frozen=True, slots=True)
class GoalRatifyRequest:
    """`goal_ratify`: the owner's ratification of a goal proposal by id; the
    daemon writes `goal.json` (section 7.2)."""

    id: int
    owner_token: str | None = None

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> GoalRatifyRequest:
        record = "goal_ratify"
        refuse_unknown(obj, record, _GOAL_RATIFY_KEYS)
        return cls(
            id=read_int(obj, record, "id"),
            owner_token=read_opt_str(obj, record, "owner_token", nullable=True),
        )

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "owner_token": self.owner_token}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({"id": {"type": "integer"}, "owner_token": _STRING_OR_NULL}, ("id",))


_GOAL_RATIFY_RESULT_KEYS = frozenset({"id", "text"})


@dataclasses.dataclass(frozen=True, slots=True)
class GoalRatifyResult:
    """The ratified proposal: its id and the text now presented as the
    standing goal."""

    id: int
    text: str

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> GoalRatifyResult:
        record = "goal_ratify"
        refuse_unknown(obj, record, _GOAL_RATIFY_RESULT_KEYS)
        return cls(id=read_int(obj, record, "id"), text=read_str(obj, record, "text"))

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text}

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return _schema({"id": {"type": "integer"}, "text": {"type": "string"}}, ("id", "text"))


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
APPROVE: Operation[ApproveRequest, ApproveResult] = Operation(
    "approve", ApproveRequest, ApproveResult, owner=True
)
WAIVE: Operation[WaiveRequest, WaiveResult] = Operation("waive", WaiveRequest, WaiveResult, owner=True)
GOAL_RATIFY: Operation[GoalRatifyRequest, GoalRatifyResult] = Operation(
    "goal_ratify", GoalRatifyRequest, GoalRatifyResult, owner=True
)

OPERATIONS: tuple[Operation[Any, Any], ...] = (
    PING, OBSERVE, ACT, COMMIT, RESET, INSTALL_MODULE, APPROVE, WAIVE, GOAL_RATIFY,
)


def daemon_operation(name: Any) -> Operation[Any, Any]:
    """The operation of this name; anything else, the retired `step`
    included, is refused by name with `OPERATION_UNKNOWN`."""
    for operation in OPERATIONS:
        if operation.name == name:
            return operation
    raise AssayError(
        f"unknown broker operation {name!r}",
        code="OPERATION_UNKNOWN",
        hint="the operations are " + ", ".join(operation.name for operation in OPERATIONS),
    )
