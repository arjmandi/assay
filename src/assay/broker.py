from __future__ import annotations

import contextlib
import dataclasses
import importlib
import importlib.util
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from .core import (
    LOCAL_MODE,
    REMOTE_MODE,
    AssayError,
    RunPaths,
    append_jsonl,
    atomic_json,
    make_event,
    normalize_observation,
    now_iso,
    read_json,
    rows_to_grid,
)
from .sandbox import sandbox_mode
from .adapters import Adapter, Session
from .live import execute_action, execute_model_plan, execute_steps, reset_level
from .modules import active_modules, install_module
from .ops import (
    ACT,
    COMMIT,
    INSTALL_MODULE,
    OBSERVE,
    PING,
    RESET,
    ActRequest,
    CommitRequest,
    InstallModuleRequest,
    InstallModuleResult,
    ObserveRequest,
    ObserveResult,
    Operation,
    PingRequest,
    PingResult,
    ReceiptResult,
    Req,
    Res,
    ResetRequest,
    daemon_operation,
)
from .records import Claim, Event, Mutation, Receipt
from .run import Run


def is_remote_config(config: Mapping[str, Any]) -> bool:
    return str(config.get("mode", LOCAL_MODE)).lower() == REMOTE_MODE


def split_adapter_spec(spec: str) -> tuple[str, str]:
    module_name, separator, attribute = spec.rpartition(":")
    if not separator or not module_name or not attribute:
        raise AssayError(
            f"adapter spec {spec!r} must be module:factory or /path/file.py:factory",
            code="ADAPTER_SPEC",
        )
    return module_name, attribute


def resolve_adapter_spec(spec: str, root: Path, cwd: Path | None = None) -> str:
    """Resolve an adapter spec to its canonical form before anything is
    spawned: a file spec becomes absolute (looked up against the run directory
    first, then the caller's working directory), a module spec stays a module
    spec. Refuses, with the places searched, when the file is nowhere."""
    module_name, attribute = split_adapter_spec(spec)
    candidate = Path(module_name)
    if candidate.suffix != ".py":
        return spec
    searched: list[Path] = []
    for base in ([None] if candidate.is_absolute() else [root, cwd or Path.cwd()]):
        where = candidate if base is None else base / candidate
        searched.append(where)
        if where.is_file():
            return f"{where.resolve()}:{attribute}"
    looked = " and ".join(str(item.parent.resolve()) for item in searched) or str(candidate)
    raise AssayError(
        f"adapter file not found: {module_name} (looked in {looked}); pass the "
        "file's path, absolute or relative to the run directory, as "
        "/path/file.py:factory",
        code="ADAPTER_SPEC",
    )


_DRY_IMPORT = """\
import importlib, importlib.util, sys
spec, root = sys.argv[1], sys.argv[2]
module_name, _, attribute = spec.rpartition(":")
if module_name.endswith(".py"):
    info = importlib.util.spec_from_file_location("_assay_adapter_check", module_name)
    module = importlib.util.module_from_spec(info)
    sys.modules[info.name] = module
    info.loader.exec_module(module)
else:
    module = importlib.import_module(module_name)
factory = getattr(module, attribute, None)
if not callable(factory):
    raise SystemExit(f"no callable {attribute!r} in {module_name}")
"""


def check_adapter_spec(spec: str, root: Path) -> None:
    """Import the adapter once in a throwaway subprocess of the interpreter
    that will serve the daemon, so a missing dependency, a syntax error or a
    missing factory is a plain refusal here, not a daemon that never comes up
    and a pointer to broker.log."""
    module_name, attribute = split_adapter_spec(spec)
    # The daemon script lives beside the package, so an adapter may import
    # `assay` whether or not the package is installed; the dry import sees the
    # same path.
    package_dir = str(Path(__file__).resolve().parents[1])
    inherited = os.environ.get("PYTHONPATH")
    environment = {
        **os.environ,
        "PYTHONPATH": package_dir + (os.pathsep + inherited if inherited else ""),
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _DRY_IMPORT, spec, str(root)],
            capture_output=True,
            text=True,
            cwd=str(root) if root.is_dir() else None,
            env=environment,
            timeout=60.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AssayError(
            f"importing adapter {spec!r} did not finish within 60s; an adapter "
            "module must be importable without starting the world",
            code="ADAPTER_SPEC",
        ) from None
    if completed.returncode == 0:
        return
    tail = (completed.stderr or completed.stdout).strip().splitlines()
    reason = tail[-1][:300] if tail else f"exit {completed.returncode}"
    if "ModuleNotFoundError" in reason or "No module named" in reason:
        raise AssayError(
            f"adapter {spec!r} is not importable by {sys.executable}: {reason}. "
            "Install the adapter's dependencies into that interpreter (or point "
            "ASSAY_PYTHON at one that has them), or pass /path/file.py:factory",
            code="ADAPTER_SPEC",
        )
    raise AssayError(f"adapter {spec!r} failed to import: {reason}", code="ADAPTER_SPEC")


def _import_factory(spec: str, root: Path) -> Adapter:
    module_name, attribute = split_adapter_spec(spec)
    candidate = Path(module_name)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.suffix == ".py" and candidate.exists():
        info = importlib.util.spec_from_file_location(
            f"_assay_adapter_{time.time_ns()}", candidate
        )
        if info is None or info.loader is None:
            raise AssayError(f"cannot import adapter {candidate}", code="ADAPTER_SPEC")
        module = importlib.util.module_from_spec(info)
        sys.modules[info.name] = module
        info.loader.exec_module(module)
    else:
        module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise AssayError(f"adapter factory {spec!r} is not callable", code="ADAPTER_SPEC")
    loaded: Adapter = factory
    return loaded


def _create_session(root: Path, config: Mapping[str, Any]) -> Session:
    adapter = config.get("adapter")
    if not adapter:
        raise AssayError(
            "this run's config names no adapter; start runs with "
            "--adapter <module-or-file.py>:factory so the broker knows which world to serve",
            code="ADAPTER_SPEC",
        )
    factory = _import_factory(str(adapter), root)
    with world_boundary("factory"):
        return factory(root, config)


def _encode_observation(value: Any) -> dict[str, Any]:
    if value is None:
        raise AssayError("environment returned no observation", code="WORLD_ERROR")
    return normalize_observation(value)


def _decode_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    if "data" in value:
        # General (non-grid) observation: pass the JSON object through.
        return {
            "data": value["data"],
            "state": value["state"],
            "levels_completed": value["levels_completed"],
            "win_levels": value["win_levels"],
            "available_actions": value["available_actions"],
        }
    return {
        "frame": [rows_to_grid(rows) for rows in value["frames"]],
        "state": value["state"],
        "levels_completed": value["levels_completed"],
        "win_levels": value["win_levels"],
        "available_actions": value["available_actions"],
    }


def _client_timeout(computed: float) -> float:
    """The client's socket wait, with an env-set floor for slow worlds.

    Defaults are unchanged when ASSAY_BROKER_TIMEOUT is unset (a fast local
    sim keeps its exact behavior). A slow world (emulated, containerized, or
    otherwise heavy per step) exports it as a floor in seconds so a long but
    legitimate operation does not trip the client's give-up and orphan the
    owner's reply."""
    floor = os.getenv("ASSAY_BROKER_TIMEOUT")
    if not floor:
        return computed
    try:
        return max(computed, float(floor))
    except ValueError:
        return computed


def _request(
    paths: RunPaths, payload: Mapping[str, Any], timeout: float = 10.0
) -> dict[str, Any]:
    descriptor = read_json(paths.broker)
    try:
        config = read_json(paths.config, {})
    except AssayError:
        # The read serves the hint below and nothing else: a configuration
        # the client cannot parse is the daemon's to refuse (section 8.3).
        config = {}
    local = isinstance(config, dict) and not is_remote_config(config)
    recovery = (
        "run `assay start WORLD_ID` to replay the journal and resume"
        if local
        else "a remote competition run cannot be reconstructed"
    )
    if not isinstance(descriptor, dict) or descriptor.get("status") != "READY":
        raise AssayError(f"the environment owner is unavailable; {recovery}", code="DAEMON_UNAVAILABLE")
    token_path = paths.state / "broker.token"
    try:
        token = token_path.read_text().strip()
    except FileNotFoundError as error:
        raise AssayError(
            "environment owner token is missing",
            code="DAEMON_UNAVAILABLE",
            hint="`assay start WORLD_ID` writes a new token with the daemon it starts",
        ) from error
    message = (
        json.dumps({"token": token, **dict(payload)}, separators=(",", ":")).encode()
        + b"\n"
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(paths.socket))
            client.sendall(message)
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
    except (OSError, TimeoutError) as error:
        raise AssayError(f"the environment owner stopped responding; {recovery}", code="DAEMON_UNAVAILABLE") from error
    if not chunks:
        raise AssayError(
            "empty response from environment owner", code="PROTOCOL_MALFORMED", hint=RESTART_HINT
        )
    try:
        response = json.loads(b"".join(chunks).splitlines()[0])
    except ValueError as error:
        raise AssayError(
            f"malformed response from environment owner: {error}",
            code="PROTOCOL_MALFORMED",
            hint=RESTART_HINT,
        ) from error
    if not isinstance(response, dict):
        raise AssayError(
            "malformed response from environment owner", code="PROTOCOL_MALFORMED", hint=RESTART_HINT
        )
    if not response.get("ok"):
        # The error object of section 7.1, raised here as the same AssayError
        # the daemon raised.
        raise AssayError.from_json(response.get("error", "environment owner error"))
    return response


PING_TIMEOUT_MIN = 0.5
PING_TIMEOUT_MAX = 10.0
STOP_WAIT_SECONDS = 10.0
RESTART_HINT = "stop the daemon with `assay stop` and start it again with `assay start WORLD_ID`"


def _ping_timeout() -> float:
    """The liveness probe's wait. ASSAY_BROKER_TIMEOUT raises it (a slow world
    answers a ping late while it is inside a step) within [0.5, 10] seconds."""
    return min(PING_TIMEOUT_MAX, max(PING_TIMEOUT_MIN, _client_timeout(PING_TIMEOUT_MIN)))


def call(
    paths: RunPaths, operation: Operation[Req, Res], request: Req, *, timeout: float = 10.0
) -> Res:
    """One daemon operation over the socket: the request record's fields
    under the operation's name, the reply decoded into the operation's
    result record. The one place the client speaks the wire; #13's version
    check lands here."""
    response = _request(paths, {"op": operation.name, **request.to_json()}, timeout=timeout)
    return operation.result.from_json(
        {key: value for key, value in response.items() if key != "ok"}
    )


def broker_ping(paths: RunPaths) -> bool:
    try:
        return call(paths, PING, PingRequest(), timeout=_ping_timeout()).pong
    except AssayError:
        return False


def broker_observe(paths: RunPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    result = call(paths, OBSERVE, ObserveRequest())
    return _decode_observation(result.observation), dict(result.public_info)


def broker_gated(
    paths: RunPaths, operation: Operation[Req, ReceiptResult], request: Req, *, steps: int = 1
) -> Receipt:
    """One paid operation through the daemon; returns the receipt. The CLI
    is a stateless display client on registry runs; enforcement happens
    where the session and credentials live."""
    timeout = _client_timeout(60.0 + 30.0 * max(1, steps))
    return call(paths, operation, request, timeout=timeout).receipt


def broker_state(paths: RunPaths) -> PingResult:
    """What a live daemon holds, read through `ping` (section 8.3)."""
    return call(paths, PING, PingRequest(), timeout=_ping_timeout())


def broker_install_module(paths: RunPaths, source: Path, token: str | None) -> dict[str, Any]:
    """`assay module install` as the daemon operation it is (section 6.4):
    the daemon checks the owner token against the hash it holds, pins the
    file, updates the manifest it holds and loads the module. Without a live
    daemon (none in the process table) the command refuses, since a manifest
    written behind the daemon's back would be a difference it refuses to
    adopt; a daemon inside a step is waited for like any paid command."""
    if find_daemon(paths) is None:
        raise AssayError(
            "module install is a daemon operation and this run's environment owner "
            "is not running; resume it with `assay start WORLD_ID`, then install again",
            code="DAEMON_UNAVAILABLE",
        )
    # `token` on the wire is the daemon's own; the owner's rides apart.
    request = InstallModuleRequest(path=str(source.resolve()), owner_token=token)
    return call(paths, INSTALL_MODULE, request, timeout=_client_timeout(60.0)).record


def broker_matches_latest_event(run: Run) -> bool:
    if not run.events:
        return True
    observation, _ = broker_observe(run.paths)
    return _encode_observation(observation) == _event_observation(run.events[-1])


def reconcile_mutations(run: Run) -> int:
    """Recover a paid step journaled by the broker before a CLI process died:
    the one append outside a daemon, through the run's own writer. A record
    that carries its claims (an act or a commit step from 1.2.0 on) is
    regraded against its stored response and journaled gated, with its
    prediction (docs/ARCHITECTURE.md section 6.5); a record without them
    (older, or a model-plan step) is journaled UNGATED, as it always was."""
    from .live import recovered_pending

    known = {event.mutation_id for event in run.events if event.mutation_id is not None}
    recovered = 0
    for mutation in run.mutations:
        if mutation.mutation_id in known:
            continue
        previous = run.events[-1] if run.events else None
        observation = _decode_observation(mutation.observation)
        pending = make_event(
            observation,
            mutation.action,
            mutation.data,
            previous,
            note="recovered from broker mutation journal",
        ).updated(mutation_id=mutation.mutation_id)
        if mutation.claims is not None and previous is not None:
            pending = recovered_pending(run, mutation, previous, pending)
        run.append(pending)
        known.add(mutation.mutation_id)
        recovered += 1
    if recovered:
        append_jsonl(
            run.paths.activity,
            {
                "kind": "mutation_recovery",
                "event": len(run.events) - 1,
                "recovered": recovered,
            },
        )
    return recovered


def start_broker(paths: RunPaths) -> None:
    paths.state.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    token_path = paths.state / "broker.token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    descriptor = {
        "status": "STARTING",
        "started_at": time.time(),
        "mode": read_json(paths.config, {}).get("mode", LOCAL_MODE),
    }
    atomic_json(paths.broker, descriptor)
    log = (paths.state / "broker.log").open("ab", buffering=0)
    # The daemon is the package's own module, run by the interpreter that
    # runs this CLI, with the package's parent directory on its path so the
    # spawn works installed (site-packages) and uninstalled (src/) alike.
    package_dir = str(Path(__file__).resolve().parents[1])
    inherited = os.environ.get("PYTHONPATH")
    environment = {
        **os.environ,
        "PYTHONPATH": package_dir + (os.pathsep + inherited if inherited else ""),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "assay.broker_server", "--run-dir", str(paths.root)],
        cwd=str(paths.root),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
        close_fds=True,
        env=environment,
    )
    # From here on the daemon is the only writer of broker.json: it replaces
    # the STARTING descriptor with READY, ERROR or STOPPED and names its own
    # pid there. This side used to rewrite the descriptor with the child's pid
    # right after the spawn, and that write raced the daemon's: on a slow disk
    # its fsync outlasted a warm daemon's whole startup, the rewrite buried
    # READY under STARTING, and the wait below ran to its deadline against a
    # live daemon. Identity is the process table (find_daemon), never a stored
    # pid, so nothing needs the pid before the daemon publishes it.
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        current = read_json(paths.broker, {})
        if current.get("status") == "ERROR":
            # The daemon's refusal before READY, as the daemon raised it
            # (`serve` writes the error object into the descriptor).
            raise AssayError.from_json(current.get("error", "environment initialization failed"))
        if current.get("status") == "READY" and broker_ping(paths):
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise AssayError(
        "environment owner did not start; see .assay/broker.log",
        code="DAEMON_UNAVAILABLE",
        hint="the daemon's log is .assay/broker.log; `assay doctor` checks the interpreter and the adapter",
    )


@dataclasses.dataclass(frozen=True)
class DaemonInfo:
    """A live process identified as this run's environment owner."""

    pid: int
    command: str


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def find_daemon(paths: RunPaths) -> DaemonInfo | None:
    """Identify this run's daemon by process, never by a stored pid alone.

    broker.json names a pid, but after a reboot (or a long-lived run directory)
    that pid can belong to another process of the same user, and after a
    hand-deleted `.assay` there is no broker.json at all while the daemon still
    serves the socket. So the identity is the process table: a live process
    whose command line runs the `broker_server` module with `--run-dir` naming
    this directory. Portable across macOS and Linux through `ps`; `-ww` lifts
    the column limit procps applies when no terminal is attached (under pytest
    on Linux the listing was cut at 80 columns and `--run-dir` fell off the
    line), and BSD ps reads it as the same unlimited width."""
    roots = {str(paths.root), str(paths.root.resolve())}
    try:
        listing = subprocess.run(
            ["ps", "-ww", "-eo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        listing = ""
    own = os.getpid()
    for line in listing.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, command = int(parts[0]), parts[1]
        if pid == own or "broker_server" not in command:
            continue
        if any(f"--run-dir {root}" in command for root in roots) and _alive(pid):
            return DaemonInfo(pid=pid, command=command)
    return None


def stop_broker(paths: RunPaths, wait: float = STOP_WAIT_SECONDS) -> dict[str, Any]:
    """Stop this run's daemon if, and only if, a live process is identified as
    ours. Sends SIGTERM (the daemon finishes any in-flight step, writes STOPPED
    and exits), then waits up to `wait` seconds. Never sends SIGKILL: a kill
    inside a step could leave a paid action applied in the world and absent
    from the journal. Returns what happened."""
    daemon = find_daemon(paths)
    if daemon is None:
        paths.socket.unlink(missing_ok=True)
        descriptor = read_json(paths.broker, {})
        if isinstance(descriptor, dict) and descriptor.get("status") in {"READY", "STARTING"}:
            # A stale descriptor: the process it names is gone or is not ours.
            atomic_json(paths.broker, {**descriptor, "status": "STOPPED", "stale": True})
        return {"stopped": False, "pid": None, "reason": "no live environment owner"}
    started = time.monotonic()
    try:
        os.kill(daemon.pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"stopped": True, "pid": daemon.pid, "elapsed": 0.0}
    while time.monotonic() - started < wait:
        if not _alive(daemon.pid):
            paths.socket.unlink(missing_ok=True)
            return {"stopped": True, "pid": daemon.pid, "elapsed": time.monotonic() - started}
        time.sleep(0.05)
    return {
        "stopped": False,
        "pid": daemon.pid,
        "reason": f"still inside a step after {wait:g}s; it exits when the step ends",
    }


def _event_observation(event: Event) -> dict[str, Any]:
    if event.frames is None:
        return {
            "state": str(event.state),
            "levels_completed": int(event.levels_completed),
            "win_levels": int(event.win_levels),
            "available_actions": [str(value) for value in event.available_actions],
            "data": event.observation,
        }
    return {
        "state": str(event.state),
        "levels_completed": int(event.levels_completed),
        "win_levels": int(event.win_levels),
        "available_actions": [int(value) for value in event.available_actions],
        "frames": list(event.frames),
    }


def _competition_step(
    session: Any,
    action: str,
    data: dict[str, Any] | None,
    reasoning: Mapping[str, Any] | None,
    *,
    fresh_level: bool,
) -> tuple[Any, bool]:
    """Apply one paid action while preventing a local opener reset from rewinding the world."""
    before = _encode_observation(_session_observation(session))
    if action == "RESET" and fresh_level:
        observed = _session_observation(session)
    else:
        observed = _session_step(session, action, data, reasoning)
    encoded = _encode_observation(observed)
    level_advanced = encoded["levels_completed"] != before["levels_completed"]
    return observed, action == "RESET" or level_advanced


def _replay_local_session(session: Any, run: Run) -> tuple[list[Mutation], bool]:
    """Reconstruct an exact local session from the append-only paid-action journal."""
    events = run.events
    current = _encode_observation(_session_observation(session))
    if events and current != _event_observation(events[0]):
        raise AssayError(
            "fresh simulator state differs from event 0; cached world or seed changed",
            code="LOCAL_REPLAY_DIVERGED",
            hint=REPLAY_HINT,
        )
    mutations = run.mutations
    fresh_level = True
    for mutation in mutations:
        observed, fresh_level = _competition_step(
            session,
            str(mutation.action),
            mutation.data,
            mutation.reasoning,
            fresh_level=fresh_level,
        )
        actual = _encode_observation(observed)
        expected = dict(mutation.observation)
        if actual != expected:
            raise AssayError(
                f"mutation {mutation.mutation_id} no longer reproduces its recorded observation",
                code="LOCAL_REPLAY_DIVERGED",
                hint=REPLAY_HINT,
            )
    return mutations, fresh_level


def _open_run(run: Run, session: Session) -> None:
    """Event 0 is the daemon's (docs/ARCHITECTURE.md section 6.3): on a fresh
    run, the first observation, the session's `public_info` into config.json,
    START through the one writer, and the observation kind's after-record
    work, all before the socket binds and READY is reported."""
    from .extras import kind_for

    public_info = json.loads(json.dumps(_session_public_info(session)))
    run.config = {**run.config, "public_info": dict(public_info or {})}
    atomic_json(run.paths.config, run.config)
    with world_boundary("observation"):
        pending = make_event(
            _session_observation(session), "START", None, None, note="initial observation"
        )
    event = run.append(pending)
    kind = kind_for(event)
    if kind is not None:
        kind.after_record(run, event)


def _short(value: str) -> str:
    """A difference as the refusal line shows it: every 64-hex digest cut to
    twelve characters. The activity record keeps every value whole."""
    return re.sub(r"[0-9a-f]{64}", lambda match: match.group(0)[:12], value)


def _tamper_error(detail: str) -> AssayError:
    return AssayError(
        f"{detail}: the run's files changed under the daemon, "
        "which keeps the record it holds and refuses every paid action until it "
        "is stopped and the record is examined (`assay audit`)",
        code="TAMPER_DETECTED",
        hint="run `assay stop`, then `assay audit`",
    )


REPLAY_HINT = (
    "the world no longer reproduces this journal; preserve the directory and "
    "start another run in a fresh one"
)


@contextlib.contextmanager
def world_boundary(what: str) -> Iterator[None]:
    """The adapter boundary (docs/ARCHITECTURE.md section 7.1): anything the
    session raises inside the daemon's call into it, an AssayError or not,
    becomes `WORLD_ERROR` carrying the text. A finalize failure is not
    wrapped here: it stays the warning on the receipt."""
    try:
        yield
    except AssayError as error:
        if error.code == "WORLD_ERROR":
            raise
        raise AssayError(error.message, code="WORLD_ERROR", hint=error.hint or _WORLD_HINTS[what]) from error
    except Exception as error:  # noqa: BLE001 - the world's failure, whatever it raised
        raise AssayError(
            f"{type(error).__name__}: {error}", code="WORLD_ERROR", hint=_WORLD_HINTS[what]
        ) from error


_WORLD_HINTS = {
    "factory": "the adapter's factory raised; the daemon's log is .assay/broker.log",
    "observation": "the adapter's observation raised or has a shape the kernel does not take",
    "step": "the world refused or failed this action; no event was journaled for it",
}


def _session_observation(session: Any) -> Any:
    with world_boundary("observation"):
        return session.observation


def _session_public_info(session: Any) -> Any:
    with world_boundary("observation"):
        return getattr(session, "public_info", {}) or {}


def _session_step(
    session: Any, action: str, data: dict[str, Any] | None, reasoning: Mapping[str, Any] | None
) -> Any:
    with world_boundary("step"):
        return session.step(action, data, reasoning)


def _error_reply(error: BaseException) -> dict[str, Any]:
    """The error object a reply or the descriptor carries. A failure that is
    no refusal is `INTERNAL`, its traceback printed to the daemon's log."""
    if not isinstance(error, AssayError):
        print(traceback.format_exc(), flush=True)
    return AssayError.wrap(error, hint="the daemon's log is .assay/broker.log").to_json()


class _StopRequested(Exception):
    """Raised into the accept loop by the stop signal while the daemon is
    idle."""


def _read_request(connection: socket.socket) -> dict[str, Any]:
    """One request line from the connection, as a JSON object."""
    raw = b""
    while b"\n" not in raw:
        chunk = connection.recv(1 << 20)
        if not chunk:
            break
        raw += chunk
    lines = raw.splitlines()
    if not lines or not lines[0].strip():
        raise AssayError("malformed request", code="MALFORMED_REQUEST")
    request = json.loads(lines[0])
    if not isinstance(request, dict):
        raise AssayError("malformed request", code="MALFORMED_REQUEST")
    return request


class _Daemon:
    """What the daemon holds for its life and the operations over it: the one
    run (docs/ARCHITECTURE.md section 6.3), the world session, the local
    replay's flag for a freshly entered progress unit, the refusal once a
    file changed under it (section 8.3), held as what differed, or None, the
    socket it serves with its token and its sandbox mode, and the two flags
    of a clean stop (inside a request; stop requested). `handle` is the
    dispatcher over the wire table (`ops`, section 7.2); the handlers are
    the `serve_*` functions below, one per operation, bound to the table's
    names in `HANDLERS`."""

    def __init__(self, paths: RunPaths, run: Run, session: Session, *, fresh_level: bool) -> None:
        self.paths = paths
        self.run = run
        self.session = session
        self.fresh_level = fresh_level
        self.tampered: str | None = None
        self.token = ""
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sandbox = ""
        self.in_request = False
        self.stop_requested = False

    def listen(self) -> None:
        """Bind the socket and report READY. The daemon's own sandbox mode is
        decided (one probe) before it reports and recorded beside the pid, so
        the mode every grade of this life runs under is on disk and `assay
        doctor` can hold it against the one config.json recorded at the run's
        creation."""
        paths = self.paths
        self.token = (paths.state / "broker.token").read_text().strip()
        try:
            paths.socket.unlink()
        except FileNotFoundError:
            pass
        paths.socket.parent.mkdir(parents=True, exist_ok=True)
        self.server.bind(str(paths.socket))
        paths.socket.chmod(0o600)
        self.server.listen(4)
        self.sandbox = sandbox_mode()
        self.publish("READY", replayed_mutations=len(self.run.mutations), started_at=time.time())

    def publish(self, status: str, **stamp: Any) -> None:
        """The descriptor, `broker.json`, as the daemon's own: its status,
        pid, mode and sandbox mode, and the stamp of this transition."""
        atomic_json(
            self.paths.broker,
            {
                "status": status,
                "pid": os.getpid(),
                "mode": self.run.config.get("mode", LOCAL_MODE),
                "sandbox": self.sandbox,
                **stamp,
            },
        )

    def close(self, status: str) -> None:
        """Leave: STOPPED on the stop signal, FINISHED on the WIN that ends
        the run; the socket closed and unlinked."""
        stamp = "stopped_at" if status == "STOPPED" else "finished_at"
        self.publish(status, **{stamp: time.time()})
        self.server.close()
        self.paths.socket.unlink(missing_ok=True)

    def on_terminate(self, signum: int, frame: Any) -> None:
        # Idle: leave now. Inside a request: finish it, reply, then leave. A
        # paid action is never cut between spend and record by a plain stop.
        self.stop_requested = True
        if not self.in_request:
            raise _StopRequested

    def serve_one(self, connection: socket.socket) -> bool:
        """One connection: the line read, handled and answered; whether the
        run ended with it. A client that hung up before the reply (its socket
        timeout fired on a slow operation) loses the reply and nothing else:
        the action is already journaled, and the daemon keeps serving so the
        client can re-read state on its next call."""
        self.in_request = True
        terminal = False
        with connection:
            try:
                response, terminal = self.handle(_read_request(connection))
            except Exception as error:  # noqa: BLE001 - every failure answers as the error object
                response = {"ok": False, "error": _error_reply(error)}
            try:
                connection.sendall(
                    json.dumps(response, separators=(",", ":")).encode() + b"\n"
                )
            except OSError as error:
                print(f"broker: client gone before reply ({error}); continuing", flush=True)
        self.in_request = False
        return terminal

    def refuse_if_tampered(self) -> None:
        if self.tampered is not None:
            raise _tamper_error(self.tampered)

    def verify_before_spend(self) -> None:
        """The files on disk against the held copies, before every paid
        action. A difference refuses this action and every later one, is
        recorded once in the activity log with every difference whole, and
        changes nothing the daemon holds: it never adopts the disk state and
        never appends to a changed file."""
        self.refuse_if_tampered()
        found = self.run.verify_disk()
        if not found:
            return
        detail = "; ".join(
            f"{item.what} (held {_short(item.expected)}, on disk {_short(item.found)})"
            for item in found
        )
        append_jsonl(
            self.paths.activity,
            {
                "kind": "tamper_detected",
                "event": self.run.chain_event,
                "head": self.run.chain_head,
                "differences": [dataclasses.asdict(item) for item in found],
            },
        )
        self.tampered = detail
        raise _tamper_error(detail)

    def spend(
        self,
        run: Run,
        action: str,
        data: dict[str, Any] | None,
        reasoning: Mapping[str, Any] | None,
        *,
        claims: Sequence[Claim] | None = None,
    ) -> tuple[dict[str, Any], int, str | None]:
        """The daemon-side `Stepper`: the disk verified, the world stepped,
        the mutation recorded through the run with the next id of the held
        log and the step's parsed claims (section 6.5), all before the reply;
        no socket hop."""
        if run is not self.run:
            raise AssayError("the daemon spends only on the run it holds", code="INTERNAL")
        self.verify_before_spend()
        if is_remote_config(run.config):
            observed = _session_step(self.session, action, data, reasoning)
            if observed is None:
                raise AssayError(
                    "the competition server returned no observation. This remote run "
                    "cannot be reconstructed; preserve its artifacts and use a fresh "
                    "directory for another run",
                    code="REMOTE_SESSION_EXPIRED_OR_UNAVAILABLE",
                )
        else:
            observed, self.fresh_level = _competition_step(
                self.session, action, data, reasoning, fresh_level=self.fresh_level
            )
        encoded = _encode_observation(observed)
        mutation_id = (run.mutations[-1].mutation_id if run.mutations else 0) + 1
        run.record_mutation(
            Mutation(
                mutation_id=mutation_id,
                action=action,
                data=data,
                reasoning=None if reasoning is None else dict(reasoning),
                observation=encoded,
                timestamp=now_iso(),
                claims=None if claims is None else tuple(claims),
            )
        )
        warning: str | None = None
        finalize = getattr(self.session, "finalize", None)
        if encoded["state"] == "WIN" and callable(finalize):
            try:
                finalize()
            except Exception as error:  # noqa: BLE001 - action is already journaled
                warning = f"{type(error).__name__}: {error}"
        return _decode_observation(encoded), mutation_id, warning

    def handle(self, request: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """One request line to its reply, and whether the run ended with it:
        the token checked, the operation looked up in the table, the request
        record decoded (a field the record does not take is refused before
        anything spends), the name's handler called with the daemon and the
        held run, the result encoded. An unknown operation is refused by
        name."""
        if not secrets.compare_digest(str(request.get("token", "")), self.token):
            raise AssayError("invalid environment owner token", code="BROKER_TOKEN")
        operation = daemon_operation(request.get("op"))
        if operation.paid or operation.owner:
            # A tampered daemon refuses before any pre-spend work; the
            # verification itself runs in `spend`, before the world step of
            # every act, commit step, model-plan step and reset.
            self.refuse_if_tampered()
        fields = {key: value for key, value in request.items() if key not in {"token", "op"}}
        result = HANDLERS[operation.name](self, self.run, operation.request.from_json(fields))
        terminal = operation.paid and bool(self.run.events) and self.run.events[-1].state == "WIN"
        return {"ok": True, **result.to_json()}, terminal


def serve_ping(daemon: _Daemon, run: Run, request: PingRequest) -> PingResult:
    """Liveness, and the held state for whoever compares the disk with the
    daemon's view (`broker_state`)."""
    return PingResult(
        pong=True, chain_event=run.chain_event, chain_head=run.chain_head, tampered=daemon.tampered
    )


def serve_observe(daemon: _Daemon, run: Run, request: ObserveRequest) -> ObserveResult:
    return ObserveResult(
        observation=_encode_observation(_session_observation(daemon.session)),
        public_info=dict(_session_public_info(daemon.session)),
    )


def serve_act(daemon: _Daemon, run: Run, request: ActRequest) -> ReceiptResult:
    return ReceiptResult(
        execute_action(
            run,
            request.action_token,
            predict=request.predict or "",
            because=request.because,
            at_event=request.at_event,
            declares=request.declares,
            stepper=daemon.spend,
        )
    )


def serve_commit(daemon: _Daemon, run: Run, request: CommitRequest) -> ReceiptResult:
    if request.plan:
        return ReceiptResult(
            execute_model_plan(run, request.plan, at_event=request.at_event, stepper=daemon.spend)
        )
    return ReceiptResult(
        execute_steps(
            run,
            list(request.steps),
            at_event=request.at_event,
            declares=request.declares,
            stepper=daemon.spend,
        )
    )


def serve_reset(daemon: _Daemon, run: Run, request: ResetRequest) -> ReceiptResult:
    return ReceiptResult(
        reset_level(
            run,
            because=request.because,
            at_event=request.at_event,
            declares=request.declares,
            stepper=daemon.spend,
        )
    )


def serve_install_module(
    daemon: _Daemon, run: Run, request: InstallModuleRequest
) -> InstallModuleResult:
    """The owner's install, against the held hash (section 6.4): the file
    pinned, the held manifest updated and written, `module_installed`
    recorded, and the held set reloaded so the module runs from the next
    action. Refused once a file changed under the daemon (the dispatcher's
    check), so the install never rewrites a changed manifest."""
    record = install_module(run, Path(request.path), request.owner_token)
    active_modules(run)
    return InstallModuleResult(record=record)


Handler = Callable[["_Daemon", Run, Any], Any]

# One function per operation (section 7.2), bound to the wire table's names
# here and looked up by `handle`; `tests/test_ops.py` holds the two in step.
HANDLERS: Mapping[str, Handler] = {
    PING.name: serve_ping,
    OBSERVE.name: serve_observe,
    ACT.name: serve_act,
    COMMIT.name: serve_commit,
    RESET.name: serve_reset,
    INSTALL_MODULE.name: serve_install_module,
}


def _open(paths: RunPaths) -> _Daemon:
    """Everything before READY. The one strict load of the daemon's life: a
    contiguity problem or a diverged chain refuses the start with
    CHAIN_DIVERGED and rewrites nothing, and the run is held from here on,
    appended and chained in memory, and checked against the disk before
    every paid action (docs/ARCHITECTURE.md sections 6.3 and 8.3). Then the
    world session, the local replay, event 0 on a fresh run, the modules
    from the manifest the run holds (loaded once; the consults and the
    outcome observations use the held objects), the socket and READY."""
    run = Run.load(paths, strict=True)
    session = _create_session(paths.root, run.config)
    if is_remote_config(run.config):
        fresh_level = False
    else:
        _, fresh_level = _replay_local_session(session, run)
    if not run.events:
        _open_run(run, session)
    active_modules(run)
    daemon = _Daemon(paths, run, session, fresh_level=fresh_level)
    daemon.listen()
    return daemon


def serve(paths: RunPaths) -> None:
    """The daemon's life: the setup (`_open`), then the accept loop until the
    stop signal, or the WIN that ends the run. A failure before READY goes
    into the descriptor for `assay start` to report."""
    config = read_json(paths.config)
    if not isinstance(config, dict):
        return
    try:
        daemon = _open(paths)
    except Exception as error:  # noqa: BLE001 - the descriptor carries whatever refused the start
        atomic_json(
            paths.broker,
            {"status": "ERROR", "pid": os.getpid(), "error": _error_reply(error)},
        )
        return
    signal.signal(signal.SIGTERM, daemon.on_terminate)
    while True:
        if daemon.stop_requested:
            daemon.close("STOPPED")
            return
        try:
            connection, _ = daemon.server.accept()
        except _StopRequested:
            daemon.close("STOPPED")
            return
        if daemon.serve_one(connection):
            daemon.close("FINISHED")
            return
