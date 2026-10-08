from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
import unicodedata
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import numpy as np

from .errors import INTERNAL, UNSPECIFIED, kind_of
from .records import Event

if TYPE_CHECKING:
    from .run import Run


class AssayError(RuntimeError):
    """A refusal (docs/ARCHITECTURE.md section 7.1): the message, one line,
    a code from the catalogue (`errors.py`), the kind the code has there
    (`usage`, `refused`, `world`, `internal` or `invalid`, which decides the
    exit status), the next step in one sentence, or None, and `detail`, the
    further lines the command line prints after the first two (the claims
    table), or None. `str(error)` is the message alone; the code rides
    beside it. A raise without a code, an adapter's or a module's, is
    `UNSPECIFIED`, outside the table, with the kind it asks for."""

    def __init__(
        self,
        message: str,
        *,
        code: str = UNSPECIFIED,
        kind: str = "refused",
        hint: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.code = str(code)
        self.kind = kind_of(self.code, str(kind))
        self.hint = hint
        self.detail = detail

    def __str__(self) -> str:
        return self.message

    def to_json(self) -> dict[str, Any]:
        """The error object of the socket and of `--json`."""
        return {
            "code": self.code,
            "kind": self.kind,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, obj: Any) -> AssayError:
        """The error a reply carried, as the same AssayError. A string is an
        error without a code; anything else is a malformed reply."""
        if isinstance(obj, str):
            return cls(obj)
        if not isinstance(obj, Mapping) or not isinstance(obj.get("message"), str):
            return cls(
                "malformed error object from environment owner",
                code="REPLY_MALFORMED",
                hint="stop the daemon with `assay stop` and start it again with `assay start WORLD_ID`",
            )
        hint = obj.get("hint")
        detail = obj.get("detail")
        return cls(
            obj["message"],
            code=str(obj.get("code") or UNSPECIFIED),
            kind=str(obj.get("kind") or "refused"),
            hint=hint if isinstance(hint, str) else None,
            detail=detail if isinstance(detail, str) else None,
        )

    @classmethod
    def wrap(cls, error: BaseException, *, hint: str | None = None) -> AssayError:
        """An AssayError as it is; anything else as `INTERNAL`, carrying the
        exception's type and text."""
        if isinstance(error, AssayError):
            return error
        return cls(f"{type(error).__name__}: {error}", code=INTERNAL, hint=hint)


@dataclasses.dataclass(frozen=True)
class RunPaths:
    root: Path

    @property
    def state(self) -> Path:
        return self.root / ".assay"

    @property
    def config(self) -> Path:
        return self.state / "config.json"

    @property
    def events(self) -> Path:
        return self.state / "events.jsonl"

    @property
    def activity(self) -> Path:
        return self.state / "activity.jsonl"

    @property
    def notes(self) -> Path:
        return self.state / "NOTES.md"

    @property
    def dossier(self) -> Path:
        return self.state / "dossier.json"

    @property
    def receipts(self) -> Path:
        return self.state / "receipts"

    @property
    def mutations(self) -> Path:
        return self.state / "mutations.jsonl"

    @property
    def broker(self) -> Path:
        return self.state / "broker.json"

    @property
    def socket(self) -> Path:
        digest = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:24]
        tail = Path(f"assay-{os.getuid()}") / "sockets" / f"{digest}.sock"
        candidate = Path(tempfile.gettempdir()) / tail
        if len(str(candidate).encode()) > 90:
            # AF_UNIX paths are capped near 104 bytes; a long TMPDIR breaks bind().
            return Path("/tmp") / tail
        return candidate

    @property
    def lock(self) -> Path:
        return self.state / "run.lock"

    @property
    def registry(self) -> Path:
        return self.state / "registry.json"

    @property
    def verifiers(self) -> Path:
        return self.state / "verifiers"

    @property
    def verifier_stats(self) -> Path:
        return self.state / "verifiers" / "stats.json"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# The hint of every observation the kernel cannot read (`OBSERVATION_INVALID`,
# kind world): the adapter is broken, not the action.
OBSERVATION_HINT = (
    "the adapter returned an observation the kernel cannot read; fix the adapter "
    "(docs/ARCHITECTURE.md section 2.2 gives the shape) and resume with `assay start WORLD_ID`"
)

# The run modes `config.json` records and `--mode` takes: a local simulator
# or a remote world. The mode is the operator's word to the adapter, which
# reads it from the config; the session rules the kernel applies (the lease,
# replay, a reset on a fresh unit) come from the adapter's declaration
# (`adapters.SessionCapability`), not from the mode.
LOCAL_MODE = "local"
REMOTE_MODE = "remote"
# The value config.json carried for a remote run before 1.2.0, read as
# REMOTE_MODE by `run_mode`; the one line of the kernel the conformance
# test's word rule allows by name.
LEGACY_REMOTE_MODE = "competition"


def run_mode(config: Mapping[str, Any]) -> str:
    """The run's mode as the kernel reads it, `LOCAL_MODE` or `REMOTE_MODE`;
    the value runs before 1.2.0 recorded is read as remote."""
    mode = str(config.get("mode", LOCAL_MODE)).lower()
    return REMOTE_MODE if mode in {REMOTE_MODE, LEGACY_REMOTE_MODE} else LOCAL_MODE

WORLD_ID_MAX = 64
WORLD_ID_RULE = (
    f"a world id is any non-empty string up to {WORLD_ID_MAX} characters with no "
    "whitespace, control characters or path separators, kept as given"
)


def _world_id_problem(world_id: str) -> str | None:
    if not world_id:
        return "it is empty"
    if len(world_id) > WORLD_ID_MAX:
        return f"it is {len(world_id)} characters long"
    for character in world_id:
        if character.isspace():
            return "it contains whitespace"
        if unicodedata.category(character) == "Cc":
            return "it contains a control character"
        if character in "/\\":
            return "it contains a path separator"
    return None


def normalize_game_id(value: str) -> str:
    """The world id, stored and shown as given. It is a label: the socket and
    anchor paths hash the run directory, never the id. A benchmark adapter may
    read it to pick the instance to load."""
    world_id = str(value)
    problem = _world_id_problem(world_id)
    if problem is not None:
        raise AssayError(
            f"invalid world id {value!r}: {problem}. {WORLD_ID_RULE[0].upper()}{WORLD_ID_RULE[1:]}",
            code="WORLD_ID_INVALID",
        )
    return world_id


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as error:
        raise AssayError(f"corrupt JSON in {path}: {error}", code="RECORD_CORRUPT") from error


def append_jsonl(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    record = {"timestamp": now_iso(), **dict(value)}
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
    with path.open("a") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return record


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    output: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            output.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise AssayError(
                f"corrupt JSONL in {path} line {line_number}: {error}",
                code="RECORD_CORRUPT",
            ) from error
    return output


@contextlib.contextmanager
def run_lock(paths: RunPaths) -> Iterator[None]:
    paths.state.mkdir(parents=True, exist_ok=True)
    with paths.lock.open("a+") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield


@dataclasses.dataclass
class CommandStatus:
    """The run a command works on. A paid command replaces it with the run
    reloaded after the daemon's receipt, so the activity record of the
    command's end names the head the daemon left."""

    run: Run


@contextlib.contextmanager
def command_status(run: Run, command: str) -> Iterator[CommandStatus]:
    started_monotonic = time.monotonic()
    current = CommandStatus(run)
    record: dict[str, Any] = {
        "status": "RUNNING",
        "command": command,
        "event": run.events[-1].id if run.events else None,
        "risk": "paid_live_action"
        if command in {"act", "commit", "reset"}
        else "offline",
        "started_at": now_iso(),
        "pid": os.getpid(),
    }
    append_jsonl(run.paths.activity, {"kind": "command_start", **record})
    try:
        yield current
    except Exception as error:
        events = current.run.events
        failure = AssayError.wrap(error)
        record.update(
            status="ERROR",
            finished_at=now_iso(),
            elapsed_seconds=time.monotonic() - started_monotonic,
            event=events[-1].id if events else record["event"],
            error=failure.message[:500],
            code=failure.code,
            error_kind=failure.kind,
        )
        append_jsonl(run.paths.activity, {"kind": "command_end", **record})
        raise
    else:
        events = current.run.events
        record.update(
            status="FINISHED",
            finished_at=now_iso(),
            elapsed_seconds=time.monotonic() - started_monotonic,
            event=events[-1].id if events else record["event"],
        )
        append_jsonl(run.paths.activity, {"kind": "command_end", **record})


def require_run(paths: RunPaths) -> dict[str, Any]:
    config = read_json(paths.config)
    if not isinstance(config, dict):
        raise AssayError(
            f"{paths.root} is not initialized",
            code="RUN_MISSING",
            hint="run `assay start WORLD_ID` here",
        )
    return config


def grid_to_rows(grid: Any) -> list[str]:
    array = np.asarray(grid, dtype=np.int16)
    if array.ndim != 2:
        raise AssayError(
            f"frame must be 2-D, got {array.shape}", code="OBSERVATION_INVALID", hint=OBSERVATION_HINT
        )
    if array.size and (int(array.min()) < 0 or int(array.max()) > 15):
        raise AssayError(
            "grid colors must be in 0..15", code="OBSERVATION_INVALID", hint=OBSERVATION_HINT
        )
    return ["".join(format(int(cell), "x") for cell in row) for row in array]


def rows_to_grid(rows: Sequence[str]) -> np.ndarray[Any, Any]:
    if not rows:
        raise AssayError("empty frame", code="RECORD_CORRUPT")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise AssayError("ragged frame encoding", code="RECORD_CORRUPT")
    try:
        return np.asarray(
            [[int(cell, 16) for cell in row] for row in rows], dtype=np.int16
        )
    except ValueError as error:
        raise AssayError("invalid frame encoding", code="RECORD_CORRUPT") from error


def normalize_state(value: Any) -> str:
    return str(getattr(value, "value", value))


def normalize_available(values: Any) -> list[int]:
    output: set[int] = set()
    for value in values or ():
        raw = getattr(value, "value", value)
        if isinstance(raw, tuple):
            raw = raw[0]
        output.add(int(raw))
    return sorted(output)


def general_event(event: Event) -> bool:
    """True for a dict-shaped (non-grid) observation event."""
    return event.frames is None


def normalize_observation(response: Any) -> dict[str, Any]:
    if (
        isinstance(response, Mapping)
        and "data" in response
        and response.get("frame", response.get("frames")) is None
    ):
        # General (non-grid) adapter observation: a JSON object under "data".
        data = response["data"]
        if not isinstance(data, Mapping):
            raise AssayError(
                "adapter observation 'data' must be a JSON object",
                code="OBSERVATION_INVALID",
                hint=OBSERVATION_HINT,
            )
        try:
            payload = json.loads(json.dumps(data, sort_keys=True))
        except (TypeError, ValueError) as error:
            raise AssayError(
                f"adapter observation is not JSON-serializable: {error}",
                code="OBSERVATION_INVALID",
                hint=OBSERVATION_HINT,
            ) from error
        available = sorted(
            {
                str(getattr(value, "value", value))
                for value in response.get("available_actions") or ()
            }
        )
        return {
            "state": normalize_state(response.get("state", "NOT_FINISHED")),
            "levels_completed": int(response.get("levels_completed", 0)),
            "win_levels": int(response.get("win_levels", 1)),
            "available_actions": available,
            "data": payload,
        }
    if isinstance(response, Mapping):
        raw_frames = response.get("frame", response.get("frames"))
        state = response.get("state", "NOT_FINISHED")
        completed = response.get("levels_completed", 0)
        total = response.get("win_levels", 1)
        available = response.get("available_actions", ())
    else:
        raw_frames = response.frame
        state = response.state
        completed = response.levels_completed
        total = response.win_levels
        available = response.available_actions
    frames = [np.asarray(frame, dtype=np.int16) for frame in raw_frames]
    if not frames:
        raise AssayError(
            "environment returned no frames", code="OBSERVATION_INVALID", hint=OBSERVATION_HINT
        )
    return {
        "state": normalize_state(state),
        "levels_completed": int(completed),
        "win_levels": int(total),
        "available_actions": normalize_available(available),
        "frames": [grid_to_rows(frame) for frame in frames],
    }


def make_event(
    response: Any,
    action: str,
    data: Mapping[str, Any] | None,
    previous: Event | None,
    note: str = "",
) -> Event:
    """The pending event of one observation: id -1 until `run.append` assigns
    the held count, the observation under `observation` (a dict world) or
    `frames` and `n_frames` (a frame world)."""
    observed = normalize_observation(response)
    event = Event(
        id=-1,
        timestamp=now_iso(),
        action=action,
        data=dict(data) if data else None,
        counts_action=action != "START",
        state=str(observed["state"]),
        levels_completed=int(observed["levels_completed"]),
        level_before=None if previous is None else int(previous.levels_completed),
        win_levels=int(observed["win_levels"]),
        available_actions=list(observed["available_actions"]),
        note=note,
    )
    if "data" in observed:
        return event.updated(observation=observed["data"])
    frames = list(observed["frames"])
    return event.updated(n_frames=len(frames), frames=frames)


def frame_at(event: Event, frame: int = -1) -> np.ndarray[Any, Any]:
    if event.frames is None:
        raise AssayError(
            "this event has dict observations; there is no frame to decode",
            code="COMMAND_ARGS",
        )
    return rows_to_grid(event.frames[frame])


def canonical_action(event: Event) -> str:
    """One line for an event's action: the name, then `k=v` parameters in key
    order. An observation kind may render its own form (a frame world prints
    its point action as NAME:x,y, the form the published journals' receipts
    carry)."""
    from .extras import kind_for

    kind = kind_for(event)
    if kind is not None:
        rendered = kind.canonical_action(event)
        if rendered is not None:
            return rendered
    data = event.data
    if data:
        rendered = " ".join(f"{key}={data[key]}" for key in sorted(data))
        return f"{event.action} {rendered}"
    return str(event.action)


def parse_action(
    token: str, registry: Mapping[str, Any]
) -> tuple[str, dict[str, Any] | None]:
    """Parse an action token against the run's registry."""
    from .registry import parse_registry_action

    return parse_registry_action(token, registry)


@contextlib.contextmanager
def import_path(path: Path, prefix: str) -> Iterator[ModuleType]:
    before_path = list(sys.path)
    before_modules = set(sys.modules)
    unique = f"_{prefix}_{time.time_ns()}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(unique, path)
    if spec is None or spec.loader is None:
        raise AssayError(f"cannot import {path}", code="MODULE_CONTRACT")
    module = importlib.util.module_from_spec(spec)
    try:
        sys.path.insert(0, str(path.parent))
        sys.modules[unique] = module
        spec.loader.exec_module(module)
        yield module
    except AssayError:
        raise
    except Exception as error:
        raise AssayError(
            f"failed to load {path.name}: {type(error).__name__}: {error}",
            code="MODULE_CONTRACT",
        ) from error
    finally:
        sys.path[:] = before_path
        for name in set(sys.modules) - before_modules:
            loaded = sys.modules.get(name)
            source = getattr(loaded, "__file__", None)
            package_paths = [
                str(item) for item in (getattr(loaded, "__path__", ()) or ())
            ]
            if (
                name == unique
                or (source and str(source).startswith(str(path.parent)))
                or any(item.startswith(str(path.parent)) for item in package_paths)
            ):
                sys.modules.pop(name, None)
