from __future__ import annotations

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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .core import (
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
from .records import Event, Mutation, Receipt
from .run import Run


LOCAL_MODE = "local"
REMOTE_MODE = "competition"


def is_remote_config(config: Mapping[str, Any]) -> bool:
    return str(config.get("mode", LOCAL_MODE)).lower() == REMOTE_MODE


def split_adapter_spec(spec: str) -> tuple[str, str]:
    module_name, separator, attribute = spec.rpartition(":")
    if not separator or not module_name or not attribute:
        raise AssayError(
            f"adapter spec {spec!r} must be module:factory or /path/file.py:factory"
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
        "/path/file.py:factory"
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
            "module must be importable without starting the world"
        ) from None
    if completed.returncode == 0:
        return
    tail = (completed.stderr or completed.stdout).strip().splitlines()
    reason = tail[-1][:300] if tail else f"exit {completed.returncode}"
    if "ModuleNotFoundError" in reason or "No module named" in reason:
        raise AssayError(
            f"adapter {spec!r} is not importable by {sys.executable}: {reason}. "
            "Install the adapter's dependencies into that interpreter (or point "
            "ASSAY_PYTHON at one that has them), or pass /path/file.py:factory"
        )
    raise AssayError(f"adapter {spec!r} failed to import: {reason}")


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
            raise AssayError(f"cannot import adapter {candidate}")
        module = importlib.util.module_from_spec(info)
        sys.modules[info.name] = module
        info.loader.exec_module(module)
    else:
        module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise AssayError(f"adapter factory {spec!r} is not callable")
    loaded: Adapter = factory
    return loaded


def _create_session(root: Path, config: Mapping[str, Any]) -> Session:
    adapter = config.get("adapter")
    if not adapter:
        raise AssayError(
            "this run's config names no adapter; start runs with "
            "--adapter <module-or-file.py>:factory so the broker knows which world to serve"
        )
    return _import_factory(str(adapter), root)(root, config)


def _encode_observation(value: Any) -> dict[str, Any]:
    if value is None:
        raise AssayError("environment returned no observation")
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
        raise AssayError(f"the environment owner is unavailable; {recovery}")
    token_path = paths.state / "broker.token"
    try:
        token = token_path.read_text().strip()
    except FileNotFoundError as error:
        raise AssayError("environment owner token is missing") from error
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
        raise AssayError(f"the environment owner stopped responding; {recovery}") from error
    if not chunks:
        raise AssayError("empty response from environment owner")
    response = json.loads(b"".join(chunks).splitlines()[0])
    if not isinstance(response, dict):
        raise AssayError("malformed response from environment owner")
    if not response.get("ok"):
        raise AssayError(str(response.get("error", "environment owner error")))
    return response


PING_TIMEOUT_MIN = 0.5
PING_TIMEOUT_MAX = 10.0
STOP_WAIT_SECONDS = 10.0


def _ping_timeout() -> float:
    """The liveness probe's wait. ASSAY_BROKER_TIMEOUT raises it (a slow world
    answers a ping late while it is inside a step) within [0.5, 10] seconds."""
    return min(PING_TIMEOUT_MAX, max(PING_TIMEOUT_MIN, _client_timeout(PING_TIMEOUT_MIN)))


def broker_ping(paths: RunPaths) -> bool:
    try:
        return bool(_request(paths, {"op": "ping"}, timeout=_ping_timeout()).get("pong"))
    except AssayError:
        return False


def broker_observe(paths: RunPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    response = _request(paths, {"op": "observe"})
    return _decode_observation(response["observation"]), dict(
        response.get("public_info") or {}
    )


def broker_step(
    run: Run,
    action: str,
    data: dict[str, Any] | None,
    reasoning: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], int, str | None]:
    response = _request(
        run.paths,
        {"op": "step", "action": action, "data": data, "reasoning": reasoning},
        timeout=_client_timeout(30.0),
    )
    return (
        _decode_observation(response["observation"]),
        int(response["mutation_id"]),
        response.get("finalization_warning"),
    )


def broker_gated(
    paths: RunPaths, payload: Mapping[str, Any], *, steps: int = 1
) -> Receipt:
    """Send one gated operation to the daemon; returns the receipt, decoded
    at the socket boundary. The CLI is a stateless display client on registry
    runs; enforcement happens where the session and credentials live."""
    timeout = _client_timeout(60.0 + 30.0 * max(1, steps))
    response = _request(paths, payload, timeout=timeout)
    receipt = response.get("receipt")
    if not isinstance(receipt, dict):
        raise AssayError("environment owner returned no receipt")
    return Receipt.from_json(receipt)


@dataclasses.dataclass(frozen=True)
class DaemonState:
    """What a live daemon holds, read through `ping`: the chain event and
    head of the run in its memory, and the differences it refused a paid
    action over (section 8.3), or None while nothing changed under it. A
    fresh load from disk must agree with the first two at every quiescent
    point."""

    chain_event: int
    chain_head: str
    tampered: str | None


def broker_state(paths: RunPaths) -> DaemonState:
    response = _request(paths, {"op": "ping"}, timeout=_ping_timeout())
    tampered = response.get("tampered")
    return DaemonState(
        chain_event=int(response["chain_event"]),
        chain_head=str(response["chain_head"]),
        tampered=None if tampered is None else str(tampered),
    )


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
            "is not running; resume it with `assay start WORLD_ID`, then install again"
        )
    # `token` on the wire is the daemon's own; the owner's rides apart.
    response = _request(
        paths,
        {"op": "install_module", "path": str(source.resolve()), "owner_token": token},
        timeout=_client_timeout(60.0),
    )
    record = response.get("record")
    if not isinstance(record, dict):
        raise AssayError("environment owner returned no install record")
    return record


def broker_matches_latest_event(run: Run) -> bool:
    if not run.events:
        return True
    observation, _ = broker_observe(run.paths)
    return _encode_observation(observation) == _event_observation(run.events[-1])


def reconcile_mutations(run: Run) -> int:
    """Recover a paid step journaled by the broker before a CLI process died:
    the one append outside a daemon, through the run's own writer."""
    known = {event.mutation_id for event in run.events if event.mutation_id is not None}
    recovered = 0
    for mutation in run.mutations:
        if mutation.mutation_id in known:
            continue
        previous = run.events[-1] if run.events else None
        observation = _decode_observation(mutation.observation)
        event = make_event(
            observation,
            mutation.action,
            mutation.data,
            previous,
            note="recovered from broker mutation journal",
        )
        run.append(event.updated(mutation_id=mutation.mutation_id))
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
            raise AssayError(
                str(current.get("error", "environment initialization failed"))
            )
        if current.get("status") == "READY" and broker_ping(paths):
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise AssayError("environment owner did not start; see .assay/broker.log")


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
    before = _encode_observation(session.observation)
    if action == "RESET" and fresh_level:
        observed = session.observation
    else:
        observed = session.step(action, data, reasoning)
    encoded = _encode_observation(observed)
    level_advanced = encoded["levels_completed"] != before["levels_completed"]
    return observed, action == "RESET" or level_advanced


def _replay_local_session(session: Any, run: Run) -> tuple[list[Mutation], bool]:
    """Reconstruct an exact local session from the append-only paid-action journal."""
    events = run.events
    current = _encode_observation(session.observation)
    if events and current != _event_observation(events[0]):
        raise AssayError(
            "LOCAL_REPLAY_DIVERGED | fresh simulator state differs from event 0; cached world or seed changed"
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
                "LOCAL_REPLAY_DIVERGED | mutation "
                f"{mutation.mutation_id} no longer reproduces its recorded observation"
            )
    return mutations, fresh_level


def _open_run(run: Run, session: Session) -> None:
    """Event 0 is the daemon's (docs/ARCHITECTURE.md section 6.3): on a fresh
    run, the first observation, the session's `public_info` into config.json,
    START through the one writer, and the observation kind's after-record
    work, all before the socket binds and READY is reported."""
    from .extras import kind_for

    public_info = json.loads(json.dumps(getattr(session, "public_info", {})))
    run.config = {**run.config, "public_info": dict(public_info or {})}
    atomic_json(run.paths.config, run.config)
    event = run.append(
        make_event(session.observation, "START", None, None, note="initial observation")
    )
    kind = kind_for(event)
    if kind is not None:
        kind.after_record(run, event)


def _short(value: str) -> str:
    """A difference as the refusal line shows it: every 64-hex digest cut to
    twelve characters. The activity record keeps every value whole."""
    return re.sub(r"[0-9a-f]{64}", lambda match: match.group(0)[:12], value)


def _tamper_message(detail: str) -> str:
    return (
        f"TAMPER_DETECTED | {detail}: the run's files changed under the daemon, "
        "which keeps the record it holds and refuses every paid action until it "
        "is stopped and the record is examined (`assay audit`)"
    )


class _Daemon:
    """What the daemon holds for its life and the operations over it: the one
    run (docs/ARCHITECTURE.md section 6.3), the world session, the local
    replay's flag for a freshly entered progress unit, and the refusal once a
    file changed under it (section 8.3), held as what differed, or None."""

    def __init__(self, paths: RunPaths, run: Run, session: Session, *, fresh_level: bool) -> None:
        self.paths = paths
        self.run = run
        self.session = session
        self.fresh_level = fresh_level
        self.tampered: str | None = None

    def refuse_if_tampered(self) -> None:
        if self.tampered is not None:
            raise AssayError(_tamper_message(self.tampered))

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
        raise AssayError(_tamper_message(detail))

    def spend(
        self,
        run: Run,
        action: str,
        data: dict[str, Any] | None,
        reasoning: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], int, str | None]:
        """The daemon-side `Stepper`: the disk verified, the world stepped,
        the mutation recorded through the run with the next id of the held
        log, all before the reply; no socket hop."""
        if run is not self.run:
            raise AssayError("the daemon spends only on the run it holds")
        self.verify_before_spend()
        if is_remote_config(run.config):
            observed = self.session.step(action, data, reasoning)
            if observed is None:
                raise AssayError(
                    "REMOTE_SESSION_EXPIRED_OR_UNAVAILABLE | the competition server returned no observation. This remote run cannot be reconstructed; preserve its artifacts and use a fresh directory for another run"
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
        """One authenticated request to its reply, and whether the run ended
        with it."""
        operation = request.get("op")
        run = self.run
        if operation == "ping":
            # Liveness, and the held state for whoever compares the disk
            # with the daemon's view (`broker_state`).
            return {
                "ok": True,
                "pong": True,
                "chain_event": run.chain_event,
                "chain_head": run.chain_head,
                "tampered": self.tampered,
            }, False
        if operation == "observe":
            return {
                "ok": True,
                "observation": _encode_observation(self.session.observation),
                "public_info": getattr(self.session, "public_info", {}),
            }, False
        if operation in {"gated_act", "gated_commit", "gated_reset"}:
            # A tampered daemon refuses before any pre-spend work; the
            # verification itself runs in the stepper, before the world step
            # of every act, commit step, model-plan step and reset.
            self.refuse_if_tampered()
            from .live import execute_action, execute_model_plan, execute_steps, reset_level

            if operation == "gated_act":
                receipt = execute_action(
                    run,
                    str(request["action_token"]),
                    predict=str(request.get("predict") or ""),
                    because=request.get("because"),
                    at_event=request.get("at_event"),
                    declares=request.get("declares"),
                    stepper=self.spend,
                )
            elif operation == "gated_commit":
                if request.get("plan"):
                    receipt = execute_model_plan(
                        run,
                        str(request["plan"]),
                        at_event=request.get("at_event"),
                        stepper=self.spend,
                    )
                else:
                    receipt = execute_steps(
                        run,
                        [str(item) for item in request.get("steps") or ()],
                        at_event=request.get("at_event"),
                        declares=request.get("declares"),
                        stepper=self.spend,
                    )
            else:
                receipt = reset_level(
                    run,
                    because=request.get("because"),
                    at_event=request.get("at_event"),
                    declares=request.get("declares"),
                    stepper=self.spend,
                )
            terminal = bool(run.events) and run.events[-1].state == "WIN"
            return {"ok": True, "receipt": receipt.to_json()}, terminal
        if operation == "install_module":
            # The owner's install, against the held hash (section 6.4): the
            # file pinned, the held manifest updated and written,
            # `module_installed` recorded, and the held set reloaded so the
            # module runs from the next action. Refused once a file changed
            # under the daemon, so the install never rewrites a changed
            # manifest.
            self.refuse_if_tampered()
            from .modules import active_modules, install_module

            owner = request.get("owner_token")
            record = install_module(
                run,
                Path(str(request.get("path") or "")),
                None if owner is None else str(owner),
            )
            active_modules(run)
            return {"ok": True, "record": record}, False
        if operation == "step":
            # Every run the daemon serves has a registry, so the bare step is
            # always a gate bypass; #24 retires the operation.
            raise AssayError(
                "UNGATED_STEP_REFUSED | this registry run is daemon-gated: "
                "paid actions go through `assay act/commit/reset` (which "
                "carry graded predictions); a bare step is a gate bypass "
                "and is refused"
            )
        raise AssayError(f"unknown broker operation {operation!r}")


def serve(paths: RunPaths) -> None:
    config = read_json(paths.config)
    if not isinstance(config, dict):
        return
    try:
        # Strict: a contiguity problem or a diverged chain refuses the start
        # with CHAIN_DIVERGED and rewrites nothing. This is the one load of
        # the daemon's life: the run is held from here on, appended and
        # chained in memory, and checked against the disk before every
        # paid action (docs/ARCHITECTURE.md sections 6.3 and 8.3).
        run = Run.load(paths, strict=True)
        config = run.config
        session = _create_session(paths.root, config)
        if is_remote_config(config):
            fresh_level = False
        else:
            _, fresh_level = _replay_local_session(session, run)
        if not run.events:
            _open_run(run, session)
            config = run.config
        # The modules load once, here, from the manifest the run holds; the
        # consults and the outcome observations use the held objects.
        from .modules import active_modules

        active_modules(run)
        daemon = _Daemon(paths, run, session, fresh_level=fresh_level)
        token = (paths.state / "broker.token").read_text().strip()
        try:
            paths.socket.unlink()
        except FileNotFoundError:
            pass
        paths.socket.parent.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(paths.socket))
        paths.socket.chmod(0o600)
        server.listen(4)
        # The daemon's own sandbox mode, decided (one probe) before it
        # reports READY and recorded beside the pid, so the mode every grade
        # of this life runs under is on disk and `assay doctor` can hold it
        # against the one config.json recorded at the run's creation.
        sandbox = sandbox_mode()
        atomic_json(
            paths.broker,
            {
                "status": "READY",
                "pid": os.getpid(),
                "mode": config.get("mode", LOCAL_MODE),
                "sandbox": sandbox,
                "replayed_mutations": len(run.mutations),
                "started_at": time.time(),
            },
        )
    except Exception as error:  # noqa: BLE001 - broker persists arbitrary adapter failures
        atomic_json(
            paths.broker,
            {
                "status": "ERROR",
                "pid": os.getpid(),
                "error": f"{type(error).__name__}: {error}",
            },
        )
        return

    class _StopRequested(Exception):
        pass

    lifecycle = {"in_request": False, "stop": False}

    def _on_terminate(signum: int, frame: Any) -> None:
        # Idle: leave now. Inside a request: finish it, reply, then leave. A
        # paid action is never cut between spend and record by a plain stop.
        lifecycle["stop"] = True
        if not lifecycle["in_request"]:
            raise _StopRequested

    signal.signal(signal.SIGTERM, _on_terminate)

    def _stopped() -> None:
        atomic_json(
            paths.broker,
            {
                "status": "STOPPED",
                "pid": os.getpid(),
                "mode": config.get("mode", LOCAL_MODE),
                "sandbox": sandbox,
                "stopped_at": time.time(),
            },
        )
        server.close()
        paths.socket.unlink(missing_ok=True)

    while True:
        if lifecycle["stop"]:
            _stopped()
            return
        try:
            connection, _ = server.accept()
        except _StopRequested:
            _stopped()
            return
        lifecycle["in_request"] = True
        terminal = False
        with connection:
            try:
                raw = b""
                while b"\n" not in raw:
                    chunk = connection.recv(1 << 20)
                    if not chunk:
                        break
                    raw += chunk
                request = json.loads(raw.splitlines()[0])
                if not isinstance(request, dict):
                    raise AssayError("malformed request")
                if not secrets.compare_digest(str(request.get("token", "")), token):
                    raise AssayError("invalid environment owner token")
                response, terminal = daemon.handle(request)
            except Exception as error:  # noqa: BLE001 - isolate arbitrary adapter failures
                response = {"ok": False, "error": f"{type(error).__name__}: {error}"}
            try:
                connection.sendall(
                    json.dumps(response, separators=(",", ":")).encode() + b"\n"
                )
            except OSError as error:
                # The client hung up (e.g. its socket timeout fired on a slow
                # operation) before we could reply. The action is already
                # journaled; a lost response must never take down the owner.
                # Keep serving so the client can re-read state on its next call.
                print(f"broker: client gone before reply ({error}); continuing", flush=True)
        lifecycle["in_request"] = False
        if terminal:
            atomic_json(
                paths.broker,
                {
                    "status": "FINISHED",
                    "pid": os.getpid(),
                    "mode": config.get("mode", LOCAL_MODE),
                    "sandbox": sandbox,
                    "finished_at": time.time(),
                },
            )
            server.close()
            paths.socket.unlink(missing_ok=True)
            return
