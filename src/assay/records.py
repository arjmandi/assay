"""The typed records of a run: events, claims, grades, receipts and the
mutation log (docs/ARCHITECTURE.md section 6.2).

Each record is a frozen dataclass with slots. `from_json(obj)` validates the
type of every known key, refuses a wrong type (a `TypeError`) or a missing
required key (a `KeyError`), and carries every unknown key through `extra`
unchanged, so a journal written by a later kernel still reads here.
`to_json()` emits exactly the keys the spec names plus the extras, and the
journal line is `json.dumps(record.to_json(), separators=(",", ":"),
sort_keys=True)`, as it always was: every published journal under evidence/
loads through `Event.from_json` and re-serializes to the same bytes, which
`tests/test_records.py` proves over all 66.

The journal format (`verify/JOURNAL_SPEC.md`) distinguishes an absent key from
a null one: `predict`, `predict_ok` and `grade` are absent on START and on a
recovered event and present, possibly null, on every graded one. `Event`
therefore records which of its optional keys are present (`present`), and
`updated(**fields)` is how the kernel adds a key to a pending event. The other
records never carry a null for an optional key, so there None reads and writes
as absent, and a flag (`invalid`, `verifier`, `machine`, ...) is written only
when it is true, the form every published grade has. A null is accepted only
where the kernel writes one: an event's `data`, `level_before`, `predict` and
`predict_ok`, a mutation's `data` and `reasoning`.

Nothing here imports beyond the standard library: the records are the
boundary between the files and the kernel, and the checker in verify/ keeps
its own independent decoder.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

GAMBLE_KINDS = frozenset({"win", "level_up"})
# The state claims keep the kinds' frozen spelling on the journal; so does the
# `channel` field that names the state a claim is on.
STATE_KINDS = frozenset({"channel_eq", "channel_delta", "channel_cross"})
_MILESTONE = frozenset({"goal", "level"})  # state claims on these gamble; the rest world-model


def claim_bucket(kind: str, state: str | None = None) -> str:
    """Claim taxonomy: goal/milestone claims gamble, the rest world-model."""
    if kind in STATE_KINDS:
        return "gamble" if state in _MILESTONE else "world_model"
    if kind == "aggregate":
        return "aggregate"
    return "gamble" if kind in GAMBLE_KINDS else "world_model"


# --- validation: the decoding kit ---------------------------------------------
#
# Two families: `read_str`, `read_int`, `read_bool`, `read_object`, `read_list`
# read a required, non-null key; the `read_opt_` forms read a key that may be
# absent (and, when `nullable`, null) and return None for either. A missing
# required key is a KeyError, a wrong type (`wrong_type`) or a key the record
# does not take (`refuse_unknown`) a TypeError: the internal-error voice of the
# CLI, since a record that does not decode is a corrupt file, never a refusal.
# The wire records of `ops.py` decode through the same kit.

_MISSING: Any = object()


def _type_name(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def wrong_type(record: str, key: str, expected: str, value: Any) -> TypeError:
    return TypeError(f"{record}.{key} must be {expected}, got {_type_name(value)}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return _is_int(value) or isinstance(value, float)


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, bool, float)) or _is_int(value)


def _read(
    obj: Mapping[str, Any],
    record: str,
    key: str,
    expected: str,
    check: Any,
    *,
    required: bool,
    nullable: bool,
) -> Any:
    if key not in obj:
        if required:
            raise KeyError(key)
        return _MISSING
    value = obj[key]
    if value is None:
        if nullable:
            return None
        raise wrong_type(record, key, expected, value)
    if not check(value):
        raise wrong_type(record, key, expected, value)
    return value


def read_str(obj: Mapping[str, Any], record: str, key: str) -> str:
    return cast(str, _read(obj, record, key, "a string", lambda v: isinstance(v, str),
                           required=True, nullable=False))


def read_int(obj: Mapping[str, Any], record: str, key: str) -> int:
    return cast(int, _read(obj, record, key, "an integer", _is_int, required=True, nullable=False))


def read_bool(obj: Mapping[str, Any], record: str, key: str) -> bool:
    return cast(bool, _read(obj, record, key, "true or false", lambda v: isinstance(v, bool),
                            required=True, nullable=False))


def read_object(obj: Mapping[str, Any], record: str, key: str) -> dict[str, Any]:
    return cast(dict[str, Any], _read(obj, record, key, "a JSON object", lambda v: isinstance(v, dict),
                                      required=True, nullable=False))


def read_list(obj: Mapping[str, Any], record: str, key: str) -> list[Any]:
    return cast(list[Any], _read(obj, record, key, "a list", lambda v: isinstance(v, list),
                                 required=True, nullable=False))


def read_opt_str(
    obj: Mapping[str, Any], record: str, key: str, *, required: bool = False, nullable: bool = False
) -> str | None:
    value = _read(obj, record, key, "a string", lambda v: isinstance(v, str),
                  required=required, nullable=nullable)
    return None if value is _MISSING else cast(str | None, value)


def read_opt_int(
    obj: Mapping[str, Any], record: str, key: str, *, required: bool = False, nullable: bool = False
) -> int | None:
    value = _read(obj, record, key, "an integer", _is_int, required=required, nullable=nullable)
    return None if value is _MISSING else cast(int | None, value)


def read_opt_number(obj: Mapping[str, Any], record: str, key: str) -> float | None:
    """A number kept as written: an integer on the line stays an integer."""
    value = _read(obj, record, key, "a number", _is_number, required=False, nullable=False)
    return None if value is _MISSING else cast(float, value)


def read_opt_bool(
    obj: Mapping[str, Any], record: str, key: str, *, required: bool = False, nullable: bool = False
) -> bool | None:
    value = _read(obj, record, key, "true or false", lambda v: isinstance(v, bool),
                  required=required, nullable=nullable)
    return None if value is _MISSING else cast(bool | None, value)


def read_flag(obj: Mapping[str, Any], record: str, key: str) -> bool:
    return bool(read_opt_bool(obj, record, key))


def read_opt_object(
    obj: Mapping[str, Any], record: str, key: str, *, required: bool = False, nullable: bool = False
) -> dict[str, Any] | None:
    value = _read(obj, record, key, "a JSON object", lambda v: isinstance(v, dict),
                  required=required, nullable=nullable)
    return None if value is _MISSING else cast(dict[str, Any] | None, value)


def read_opt_list(
    obj: Mapping[str, Any], record: str, key: str, *, required: bool = False, nullable: bool = False
) -> list[Any] | None:
    value = _read(obj, record, key, "a list", lambda v: isinstance(v, list),
                  required=required, nullable=nullable)
    return None if value is _MISSING else cast(list[Any] | None, value)


def read_opt_lines(obj: Mapping[str, Any], record: str, key: str) -> tuple[str, ...] | None:
    values = read_opt_list(obj, record, key)
    if values is None:
        return None
    for value in values:
        if not isinstance(value, str):
            raise wrong_type(record, key, "a list of lines", value)
    return tuple(values)


def extras_of(obj: Mapping[str, Any], known: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in obj.items() if key not in known}


def refuse_unknown(obj: Mapping[str, Any], record: str, known: frozenset[str]) -> None:
    """A key the record does not take, refused in the wrong-type voice. The
    journal records carry extras; the wire records (`ops.py`) do not, so a
    request with one is a client's bug, never something the daemon spends
    on."""
    for key in obj:
        if key not in known:
            raise TypeError(f"{record}.{key} is not a field of the record")


def plain(value: Any) -> Any:
    """A record as JSON data, for the result records of the offline commands
    (`--json`, docs/ARCHITECTURE.md section 7.3): a record that writes its
    own form (`to_json`) does so; any other frozen dataclass becomes an
    object of its fields; tuples become lists."""
    to_json = getattr(value, "to_json", None)
    if callable(to_json) and not isinstance(value, type):
        return to_json()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain_fields(value)
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    return value


def plain_fields(record: Any) -> dict[str, Any]:
    """A dataclass as the object of its fields, each as JSON data; what a
    record's own `to_json` calls."""
    return {field.name: plain(getattr(record, field.name)) for field in dataclasses.fields(record)}


# --- claims and grades --------------------------------------------------------

_CLAIM_OPTIONAL = (
    "window_s", "channel", "op", "value", "sign", "direction", "tol", "path",
    "verifier_hash", "stat", "over", "horizon", "on_fail",
)
_CLAIM_KEYS = frozenset({"kind", "text", "coerced", *_CLAIM_OPTIONAL})


def _claim_fields(obj: Mapping[str, Any], record: str) -> dict[str, Any]:
    """The fields a claim and a grade share, validated."""
    value = _read(obj, record, "value", "a number, a string or true/false", _is_scalar,
                  required=False, nullable=False)
    return {
        "kind": read_str(obj, record, "kind"),
        "text": read_str(obj, record, "text"),
        "window_s": read_opt_number(obj, record, "window_s"),
        "channel": read_opt_str(obj, record, "channel"),
        "op": read_opt_str(obj, record, "op"),
        "value": None if value is _MISSING else value,
        "sign": read_opt_str(obj, record, "sign"),
        "direction": read_opt_str(obj, record, "direction"),
        "tol": read_opt_number(obj, record, "tol"),
        "path": read_opt_str(obj, record, "path"),
        "verifier_hash": read_opt_str(obj, record, "verifier_hash"),
        "stat": read_opt_str(obj, record, "stat"),
        "over": read_opt_int(obj, record, "over"),
        "horizon": read_opt_int(obj, record, "horizon"),
        "on_fail": read_opt_str(obj, record, "on_fail"),
        "coerced": read_flag(obj, record, "coerced"),
    }


def _claim_json(record: Claim | Grade) -> dict[str, Any]:
    output: dict[str, Any] = {"kind": record.kind, "text": record.text}
    for key in _CLAIM_OPTIONAL:
        value = getattr(record, key)
        if value is not None:
            output[key] = value
    if record.coerced:
        output["coerced"] = True
    return output


@dataclasses.dataclass(frozen=True, slots=True)
class Claim:
    """One parsed claim of a prediction, whatever its form; a field that does
    not apply to the form is None. The frame-world forms keep their coordinate
    fields in `extra`."""

    kind: str
    text: str
    window_s: float | None = None
    channel: str | None = None
    op: str | None = None
    value: int | float | str | bool | None = None
    sign: str | None = None
    direction: str | None = None
    tol: float | None = None
    path: str | None = None
    verifier_hash: str | None = None
    stat: str | None = None
    over: int | None = None
    horizon: int | None = None
    on_fail: str | None = None
    coerced: bool = False
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Claim:
        if not isinstance(obj, Mapping):
            raise TypeError(f"claim must be a JSON object, got {_type_name(obj)}")
        return cls(**_claim_fields(obj, "claim"), extra=extras_of(obj, _CLAIM_KEYS))

    def to_json(self) -> dict[str, Any]:
        output = _claim_json(self)
        output.update(self.extra)
        return output

    def updated(self, **fields: Any) -> Claim:
        return dataclasses.replace(self, **fields)


_GRADE_FLAGS = ("invalid", "ungradable", "verifier", "excluded_from_meter", "machine")
_GRADE_KEYS = _CLAIM_KEYS | frozenset({"ok", "actual", "bucket", "identity_verdict", *_GRADE_FLAGS})


@dataclasses.dataclass(frozen=True, slots=True)
class Grade:
    """One graded claim as the journal carries it: the claim's fields plus the
    verdict. On the journal a coerced claim's kind is the string `coerced`;
    `identity_verdict` is a bool, or the string `invalid` when the identity
    probe crashed, as the journals carry it."""

    kind: str
    text: str
    ok: bool
    actual: str
    bucket: str
    window_s: float | None = None
    channel: str | None = None
    op: str | None = None
    value: int | float | str | bool | None = None
    sign: str | None = None
    direction: str | None = None
    tol: float | None = None
    path: str | None = None
    verifier_hash: str | None = None
    stat: str | None = None
    over: int | None = None
    horizon: int | None = None
    on_fail: str | None = None
    coerced: bool = False
    invalid: bool = False
    ungradable: bool = False
    verifier: bool = False
    identity_verdict: bool | Literal["invalid"] | None = None
    excluded_from_meter: bool = False
    machine: bool = False
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Grade:
        if not isinstance(obj, Mapping):
            raise TypeError(f"grade must be a JSON object, got {_type_name(obj)}")
        record = "grade"
        fields = _claim_fields(obj, record)
        fields["ok"] = read_bool(obj, record, "ok")
        fields["actual"] = read_str(obj, record, "actual")
        fields["bucket"] = read_str(obj, record, "bucket")
        for key in _GRADE_FLAGS:
            fields[key] = read_flag(obj, record, key)
        verdict = _read(obj, record, "identity_verdict", 'true, false or the string "invalid"',
                        lambda v: isinstance(v, bool) or v == "invalid", required=False, nullable=False)
        fields["identity_verdict"] = None if verdict is _MISSING else verdict
        return cls(**fields, extra=extras_of(obj, _GRADE_KEYS))

    @classmethod
    def of(
        cls,
        claim: Claim,
        *,
        ok: bool,
        actual: str,
        invalid: bool = False,
        ungradable: bool = False,
        verifier: bool = False,
        identity_verdict: bool | Literal["invalid"] | None = None,
        excluded_from_meter: bool = False,
    ) -> Grade:
        """The grade of one claim: the claim's fields, the verdict, the meter
        bucket of the claim's kind, and the journal's `coerced` kind."""
        fields = {key: getattr(claim, key) for key in _CLAIM_OPTIONAL}
        return cls(
            kind="coerced" if claim.coerced else claim.kind,
            text=claim.text,
            ok=bool(ok),
            actual=actual,
            bucket=claim_bucket(claim.kind, claim.channel),
            coerced=claim.coerced,
            invalid=invalid,
            ungradable=ungradable,
            verifier=verifier,
            identity_verdict=identity_verdict,
            excluded_from_meter=excluded_from_meter,
            extra=dict(claim.extra),
            **fields,
        )

    def to_json(self) -> dict[str, Any]:
        output = _claim_json(self)
        output["ok"] = self.ok
        output["actual"] = self.actual
        output["bucket"] = self.bucket
        for key in _GRADE_FLAGS:
            if getattr(self, key):
                output[key] = True
        if self.identity_verdict is not None:
            output["identity_verdict"] = self.identity_verdict
        output.update(self.extra)
        return output

    def updated(self, **fields: Any) -> Grade:
        return dataclasses.replace(self, **fields)


def grades_json(grades: Sequence[Grade]) -> list[dict[str, Any]]:
    return [grade.to_json() for grade in grades]


# --- events -------------------------------------------------------------------

_EVENT_REQUIRED = (
    "id", "timestamp", "action", "data", "counts_action", "state", "levels_completed",
    "level_before", "win_levels", "available_actions", "note",
)
_EVENT_OPTIONAL = (
    "observation", "frames", "n_frames", "predict", "predict_ok", "grade", "mutation_id",
    "declares", "gate_optional", "gate_off",
)
_EVENT_KEYS = frozenset((*_EVENT_REQUIRED, *_EVENT_OPTIONAL))


def _available_actions(obj: Mapping[str, Any]) -> list[str | int]:
    values = read_list(obj, "event", "available_actions")
    for value in values:
        if not (isinstance(value, str) or _is_int(value)):
            raise wrong_type("event", "available_actions", "a list of strings or of integers", value)
    return values


def _frames(obj: Mapping[str, Any]) -> list[list[str]] | None:
    frames = read_opt_list(obj, "event", "frames")
    if frames is None:
        return None
    for frame in frames:
        if not isinstance(frame, list) or not all(isinstance(row, str) for row in frame):
            raise wrong_type("event", "frames", "a list of grids, each a list of hex rows", frame)
    return frames


def _declares(obj: Mapping[str, Any]) -> dict[str, str]:
    declares = read_opt_object(obj, "event", "declares")
    if declares is None:
        return {}
    for key, value in declares.items():
        if not isinstance(value, str):
            raise wrong_type("event", f"declares.{key}", "a string", value)
    return declares


@dataclasses.dataclass(frozen=True, slots=True)
class Event:
    """One journal line (verify/JOURNAL_SPEC.md section 2). `frames` and
    `n_frames` belong to a frame world, `observation` to a dict world; the
    frame rows stay the hex strings the line carries and are decoded on
    demand by `core.frame_at`. `present` names the optional keys this event
    carries, which is what `to_json` emits: every optional field given a value
    is present whichever way the event was built, and an explicit null
    (`predict: null` on a gate-off event) is present only through `updated`
    or `from_json`; `grade` reads as an empty tuple and `declares` as an
    empty mapping when absent."""

    id: int
    timestamp: str
    action: str
    data: dict[str, Any] | None
    counts_action: bool
    state: str
    levels_completed: int
    level_before: int | None
    win_levels: int
    available_actions: list[str | int]
    note: str = ""
    observation: dict[str, Any] | None = None
    frames: list[list[str]] | None = None
    n_frames: int | None = None
    predict: str | None = None
    predict_ok: bool | None = None
    grade: tuple[Grade, ...] = ()
    mutation_id: int | None = None
    declares: dict[str, str] = dataclasses.field(default_factory=dict)
    gate_optional: bool = False
    gate_off: bool = False
    present: frozenset[str] = frozenset()
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        # An optional field that carries a value is on the line, however the
        # event was built: the constructor, `dataclasses.replace`, `updated`
        # and `from_json` all agree. An explicit null (`predict: null` on a
        # gate-off event) is reachable through `updated` and `from_json` only.
        carried = frozenset(
            key
            for key, value in (
                ("observation", self.observation),
                ("frames", self.frames),
                ("n_frames", self.n_frames),
                ("predict", self.predict),
                ("predict_ok", self.predict_ok),
                ("mutation_id", self.mutation_id),
            )
            if value is not None
        )
        if self.grade:
            carried |= {"grade"}
        if self.declares:
            carried |= {"declares"}
        if self.gate_optional:
            carried |= {"gate_optional"}
        if self.gate_off:
            carried |= {"gate_off"}
        if not carried <= self.present:
            object.__setattr__(self, "present", self.present | carried)

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Event:
        if not isinstance(obj, Mapping):
            raise TypeError(f"event must be a JSON object, got {_type_name(obj)}")
        record = "event"
        grade_raw = read_opt_list(obj, record, "grade")
        return cls(
            id=read_int(obj, record, "id"),
            timestamp=read_str(obj, record, "timestamp"),
            action=read_str(obj, record, "action"),
            data=read_opt_object(obj, record, "data", required=True, nullable=True),
            counts_action=read_bool(obj, record, "counts_action"),
            state=read_str(obj, record, "state"),
            levels_completed=read_int(obj, record, "levels_completed"),
            level_before=read_opt_int(obj, record, "level_before", required=True, nullable=True),
            win_levels=read_int(obj, record, "win_levels"),
            available_actions=_available_actions(obj),
            note=read_str(obj, record, "note"),
            observation=read_opt_object(obj, record, "observation"),
            frames=_frames(obj),
            n_frames=read_opt_int(obj, record, "n_frames"),
            predict=read_opt_str(obj, record, "predict", nullable=True),
            predict_ok=read_opt_bool(obj, record, "predict_ok", nullable=True),
            grade=tuple(Grade.from_json(item) for item in grade_raw) if grade_raw else (),
            mutation_id=read_opt_int(obj, record, "mutation_id"),
            declares=_declares(obj),
            gate_optional=read_flag(obj, record, "gate_optional"),
            gate_off=read_flag(obj, record, "gate_off"),
            present=frozenset(key for key in _EVENT_OPTIONAL if key in obj),
            extra=extras_of(obj, _EVENT_KEYS),
        )

    def to_json(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "id": self.id,
            "timestamp": self.timestamp,
            "action": self.action,
            "data": self.data,
            "counts_action": self.counts_action,
            "state": self.state,
            "levels_completed": self.levels_completed,
            "level_before": self.level_before,
            "win_levels": self.win_levels,
            "available_actions": self.available_actions,
            "note": self.note,
        }
        present = self.present
        if "observation" in present:
            output["observation"] = self.observation
        if "frames" in present:
            output["frames"] = self.frames
        if "n_frames" in present:
            output["n_frames"] = self.n_frames
        if "predict" in present:
            output["predict"] = self.predict
        if "predict_ok" in present:
            output["predict_ok"] = self.predict_ok
        if "grade" in present:
            output["grade"] = [grade.to_json() for grade in self.grade]
        if "mutation_id" in present:
            output["mutation_id"] = self.mutation_id
        if "declares" in present:
            output["declares"] = self.declares
        if "gate_optional" in present:
            output["gate_optional"] = self.gate_optional
        if "gate_off" in present:
            output["gate_off"] = self.gate_off
        output.update(self.extra)
        return output

    def updated(self, **fields: Any) -> Event:
        """A copy carrying these fields, each optional one marked present on
        the line."""
        unknown = set(fields) - _EVENT_KEYS
        if unknown:
            raise TypeError(f"not event fields: {sorted(unknown)}")
        present = self.present | frozenset(key for key in fields if key in _EVENT_OPTIONAL)
        return dataclasses.replace(self, present=present, **fields)

    @property
    def ungated(self) -> bool:
        """The ungated-event rule of the spec (section 6): a paid non-RESET
        event carrying none of `predict`, `predict_ok`, `grade`."""
        if not self.counts_action or self.action == "RESET":
            return False
        return not (self.predict or self.predict_ok is not None or self.grade)

    @property
    def level_advanced(self) -> bool:
        return self.level_before is not None and self.levels_completed > self.level_before


# --- receipts -----------------------------------------------------------------

_STEP_KEYS = frozenset({"event", "action", "ok", "failed", "invalid", "ungated", "machine", "kind", "problem"})


@dataclasses.dataclass(frozen=True, slots=True)
class ReceiptStep:
    """One step of a batch or model-plan receipt."""

    event: int
    action: str
    ok: bool
    failed: tuple[str, ...] | None = None
    invalid: tuple[str, ...] | None = None
    ungated: bool = False
    machine: bool = False
    kind: str | None = None
    problem: str | None = None
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> ReceiptStep:
        if not isinstance(obj, Mapping):
            raise TypeError(f"receipt step must be a JSON object, got {_type_name(obj)}")
        record = "step"
        return cls(
            event=read_int(obj, record, "event"),
            action=read_str(obj, record, "action"),
            ok=read_bool(obj, record, "ok"),
            failed=read_opt_lines(obj, record, "failed"),
            invalid=read_opt_lines(obj, record, "invalid"),
            ungated=read_flag(obj, record, "ungated"),
            machine=read_flag(obj, record, "machine"),
            kind=read_opt_str(obj, record, "kind"),
            problem=read_opt_str(obj, record, "problem"),
            extra=extras_of(obj, _STEP_KEYS),
        )

    def to_json(self) -> dict[str, Any]:
        output: dict[str, Any] = {"event": self.event, "action": self.action, "ok": self.ok}
        if self.failed is not None:
            output["failed"] = list(self.failed)
        if self.invalid is not None:
            output["invalid"] = list(self.invalid)
        if self.ungated:
            output["ungated"] = True
        if self.machine:
            output["machine"] = True
        if self.kind is not None:
            output["kind"] = self.kind
        if self.problem is not None:
            output["problem"] = self.problem
        output.update(self.extra)
        return output


_RECEIPT_KEYS = frozenset(
    {
        "kind", "outcome", "detail", "start_event", "end_event", "level", "action", "predict",
        "grade", "because", "modules", "aggregates", "states", "steps", "plan",
        "timestamp",
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class Receipt:
    """What a paid command returns and what `.assay/receipts/` and the activity
    log keep: the outcome, the rendered grade lines, the module, aggregate
    and state lines, and the steps of a batch. An `act` receipt always
    carries `predict` and `because`, null when there is none, as it always
    has; every other optional key is written when it is set."""

    kind: str
    outcome: str
    detail: str
    start_event: int
    end_event: int
    level: int | None = None
    action: str | None = None
    predict: str | None = None
    grade: tuple[str, ...] | None = None
    because: str | None = None
    modules: tuple[str, ...] | None = None
    aggregates: tuple[str, ...] | None = None
    states: tuple[str, ...] | None = None
    steps: tuple[ReceiptStep, ...] | None = None
    plan: str | None = None
    timestamp: str | None = None
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Receipt:
        if not isinstance(obj, Mapping):
            raise TypeError(f"receipt must be a JSON object, got {_type_name(obj)}")
        record = "receipt"
        steps_raw = read_opt_list(obj, record, "steps")
        return cls(
            kind=read_str(obj, record, "kind"),
            outcome=read_str(obj, record, "outcome"),
            detail=read_str(obj, record, "detail"),
            start_event=read_int(obj, record, "start_event"),
            end_event=read_int(obj, record, "end_event"),
            level=read_opt_int(obj, record, "level"),
            action=read_opt_str(obj, record, "action"),
            predict=read_opt_str(obj, record, "predict", nullable=True),
            grade=read_opt_lines(obj, record, "grade"),
            because=read_opt_str(obj, record, "because", nullable=True),
            modules=read_opt_lines(obj, record, "modules"),
            aggregates=read_opt_lines(obj, record, "aggregates"),
            states=read_opt_lines(obj, record, "states"),
            steps=None if steps_raw is None else tuple(ReceiptStep.from_json(item) for item in steps_raw),
            plan=read_opt_str(obj, record, "plan"),
            timestamp=read_opt_str(obj, record, "timestamp"),
            extra=extras_of(obj, _RECEIPT_KEYS),
        )

    def to_json(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "kind": self.kind,
            "outcome": self.outcome,
            "detail": self.detail,
            "start_event": self.start_event,
            "end_event": self.end_event,
        }
        for key in ("level", "action", "plan", "timestamp"):
            value = getattr(self, key)
            if value is not None:
                output[key] = value
        for key in ("predict", "because"):
            value = getattr(self, key)
            if value is not None or self.kind == "act":
                output[key] = value
        for key in ("grade", "modules", "aggregates", "states"):
            lines = getattr(self, key)
            if lines is not None:
                output[key] = list(lines)
        if self.steps is not None:
            output["steps"] = [step.to_json() for step in self.steps]
        output.update(self.extra)
        return output

    def updated(self, **fields: Any) -> Receipt:
        return dataclasses.replace(self, **fields)


# --- the mutation log -----------------------------------------------------------

_MUTATION_KEYS = frozenset({"mutation_id", "action", "data", "reasoning", "observation", "claims", "timestamp"})


@dataclasses.dataclass(frozen=True, slots=True)
class Mutation:
    """One write-ahead spend record of `.assay/mutations.jsonl`: written by the
    daemon before the event line, read back by the replay and by recovery.
    `claims` is written from #16 on and absent on older records."""

    mutation_id: int
    action: str
    data: dict[str, Any] | None
    reasoning: dict[str, Any] | None
    observation: dict[str, Any]
    timestamp: str
    claims: tuple[Claim, ...] | None = None
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Mutation:
        if not isinstance(obj, Mapping):
            raise TypeError(f"mutation must be a JSON object, got {_type_name(obj)}")
        record = "mutation"
        claims_raw = read_opt_list(obj, record, "claims")
        return cls(
            mutation_id=read_int(obj, record, "mutation_id"),
            action=read_str(obj, record, "action"),
            data=read_opt_object(obj, record, "data", required=True, nullable=True),
            reasoning=read_opt_object(obj, record, "reasoning", required=True, nullable=True),
            observation=read_object(obj, record, "observation"),
            timestamp=read_str(obj, record, "timestamp"),
            claims=None if claims_raw is None else tuple(Claim.from_json(item) for item in claims_raw),
            extra=extras_of(obj, _MUTATION_KEYS),
        )

    def to_json(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "mutation_id": self.mutation_id,
            "action": self.action,
            "data": self.data,
            "reasoning": self.reasoning,
            "observation": self.observation,
            "timestamp": self.timestamp,
        }
        if self.claims is not None:
            output["claims"] = [claim.to_json() for claim in self.claims]
        output.update(self.extra)
        return output
