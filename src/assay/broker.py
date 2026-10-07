from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import os
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
    append_event,
    append_jsonl,
    atomic_json,
    load_events,
    load_jsonl,
    make_event,
    normalize_observation,
    read_json,
    rows_to_grid,
)


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


def _import_factory(spec: str, root: Path) -> Any:
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
    return factory


def _create_session(root: Path, config: Mapping[str, Any]) -> Any:
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
    sim keeps its exact behavior). A slow world — emulated, containerized, or
    otherwise heavy per step — exports it as a floor in seconds so a long but
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
    config = read_json(paths.config, {})
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
    paths: RunPaths,
    action: str,
    data: dict[str, Any] | None,
    reasoning: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], int, str | None]:
    response = _request(
        paths,
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
) -> dict[str, Any]:
    """Send one gated operation to the daemon; returns the receipt. The CLI is
    a stateless display client on registry runs — enforcement happens where
    the session and credentials live."""
    timeout = _client_timeout(60.0 + 30.0 * max(1, steps))
    response = _request(paths, payload, timeout=timeout)
    receipt = response.get("receipt")
    if not isinstance(receipt, dict):
        raise AssayError("environment owner returned no receipt")
    return receipt


def broker_matches_latest_event(paths: RunPaths) -> bool:
    events = load_events(paths)
    if not events:
        return True
    observation, _ = broker_observe(paths)
    return _encode_observation(observation) == _event_observation(events[-1])


def reconcile_mutations(paths: RunPaths) -> int:
    """Recover a paid step journaled by the broker before a CLI process died."""
    events = load_events(paths)
    known = {
        int(event["mutation_id"])
        for event in events
        if event.get("mutation_id") is not None
    }
    recovered = 0
    for mutation in load_jsonl(paths.mutations):
        mutation_id = int(mutation["mutation_id"])
        if mutation_id in known:
            continue
        previous = events[-1] if events else None
        observation = _decode_observation(mutation["observation"])
        event = make_event(
            observation,
            mutation["action"],
            mutation.get("data"),
            previous,
            note="recovered from broker mutation journal",
        )
        event["mutation_id"] = mutation_id
        appended = append_event(paths, event)
        events.append(appended)
        known.add(mutation_id)
        recovered += 1
    if recovered:
        append_jsonl(
            paths.activity,
            {
                "kind": "mutation_recovery",
                "event": len(events) - 1,
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
    script = Path(__file__).resolve().parents[1] / "broker_server.py"
    process = subprocess.Popen(
        [sys.executable, str(script), "--run-dir", str(paths.root)],
        cwd=str(paths.root),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
        close_fds=True,
    )
    current = read_json(paths.broker, {})
    if current.get("status") == "STARTING":
        atomic_json(paths.broker, {**current, "pid": process.pid})
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
    whose command line runs `broker_server.py` with `--run-dir` naming this
    directory. Portable across macOS and Linux through `ps`."""
    roots = {str(paths.root), str(paths.root.resolve())}
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,command="],
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
        if pid == own or "broker_server.py" not in command:
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


def _event_observation(event: Mapping[str, Any]) -> dict[str, Any]:
    if "frames" not in event:
        return {
            "state": str(event["state"]),
            "levels_completed": int(event["levels_completed"]),
            "win_levels": int(event["win_levels"]),
            "available_actions": [str(value) for value in event["available_actions"]],
            "data": event["observation"],
        }
    return {
        "state": str(event["state"]),
        "levels_completed": int(event["levels_completed"]),
        "win_levels": int(event["win_levels"]),
        "available_actions": [int(value) for value in event["available_actions"]],
        "frames": list(event["frames"]),
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


def _replay_local_session(
    session: Any, paths: RunPaths
) -> tuple[list[dict[str, Any]], bool]:
    """Reconstruct an exact local session from the append-only paid-action journal."""
    events = load_events(paths)
    current = _encode_observation(session.observation)
    if events and current != _event_observation(events[0]):
        raise AssayError(
            "LOCAL_REPLAY_DIVERGED | fresh simulator state differs from event 0; cached world or seed changed"
        )
    mutations = load_jsonl(paths.mutations)
    fresh_level = True
    for mutation in mutations:
        observed, fresh_level = _competition_step(
            session,
            str(mutation["action"]),
            mutation.get("data"),
            mutation.get("reasoning"),
            fresh_level=fresh_level,
        )
        actual = _encode_observation(observed)
        expected = dict(mutation["observation"])
        if actual != expected:
            raise AssayError(
                "LOCAL_REPLAY_DIVERGED | mutation "
                f"{mutation.get('mutation_id')} no longer reproduces its recorded observation"
            )
    return mutations, fresh_level


def serve(paths: RunPaths) -> None:
    config = read_json(paths.config)
    if not isinstance(config, dict):
        return
    try:
        session = _create_session(paths.root, config)
        if is_remote_config(config):
            mutations = load_jsonl(paths.mutations)
            fresh_level = False
        else:
            mutations, fresh_level = _replay_local_session(session, paths)
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
        atomic_json(
            paths.broker,
            {
                "status": "READY",
                "pid": os.getpid(),
                "mode": config.get("mode", LOCAL_MODE),
                "replayed_mutations": len(mutations),
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
                "stopped_at": time.time(),
            },
        )
        server.close()
        paths.socket.unlink(missing_ok=True)

    sequence = max((int(item.get("mutation_id", 0)) for item in mutations), default=0)
    # Daemon-side gate: on a registry run, enforcement lives HERE,
    # where the session and the credentials live. The bare `step` op is refused
    # — a client speaking this socket directly cannot bypass the gate invisibly.
    gated = read_json(paths.registry, None) is not None
    shared = {"sequence": sequence, "fresh_level": fresh_level}

    def direct_stepper(
        paths_arg: RunPaths,
        action: str,
        data: dict[str, Any] | None,
        reasoning: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], int, str | None]:
        """In-daemon spend: journal-before-respond preserved, no socket hop."""
        if is_remote_config(config):
            observed = session.step(action, data, reasoning)
            if observed is None:
                raise AssayError(
                    "REMOTE_SESSION_EXPIRED_OR_UNAVAILABLE | the competition server returned no observation. This remote run cannot be reconstructed; preserve its artifacts and use a fresh directory for another run"
                )
        else:
            observed, shared["fresh_level"] = _competition_step(
                session, action, data, reasoning, fresh_level=shared["fresh_level"]
            )
        encoded = _encode_observation(observed)
        shared["sequence"] += 1
        append_jsonl(
            paths.mutations,
            {
                "mutation_id": shared["sequence"],
                "action": action,
                "data": data,
                "reasoning": reasoning,
                "observation": encoded,
            },
        )
        warning: str | None = None
        if encoded["state"] == "WIN" and callable(getattr(session, "finalize", None)):
            try:
                session.finalize()
            except Exception as error:  # noqa: BLE001 - action is already journaled
                warning = f"{type(error).__name__}: {error}"
        return _decode_observation(encoded), shared["sequence"], warning

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
                if not secrets.compare_digest(str(request.get("token", "")), token):
                    raise AssayError("invalid environment owner token")
                operation = request.get("op")
                if operation == "ping":
                    response = {"ok": True, "pong": True}
                elif operation == "observe":
                    response = {
                        "ok": True,
                        "observation": _encode_observation(session.observation),
                        "public_info": getattr(session, "public_info", {}),
                    }
                elif operation in {"gated_act", "gated_commit", "gated_reset"}:
                    if not gated:
                        raise AssayError(
                            "this run has no registry; gated operations need one"
                        )
                    from .live import (
                        execute_action,
                        execute_model_plan,
                        execute_steps,
                        reset_level,
                    )

                    if operation == "gated_act":
                        receipt = execute_action(
                            paths,
                            str(request["action_token"]),
                            predict=str(request.get("predict") or ""),
                            because=request.get("because"),
                            at_event=request.get("at_event"),
                            declares=request.get("declares"),
                            stepper=direct_stepper,
                        )
                    elif operation == "gated_commit":
                        if request.get("plan"):
                            receipt = execute_model_plan(
                                paths,
                                str(request["plan"]),
                                at_event=request.get("at_event"),
                                stepper=direct_stepper,
                            )
                        else:
                            receipt = execute_steps(
                                paths,
                                [str(item) for item in request.get("steps") or ()],
                                at_event=request.get("at_event"),
                                declares=request.get("declares"),
                                stepper=direct_stepper,
                            )
                    else:
                        receipt = reset_level(
                            paths,
                            because=request.get("because"),
                            at_event=request.get("at_event"),
                            declares=request.get("declares"),
                            stepper=direct_stepper,
                        )
                    events = load_events(paths)
                    terminal = bool(events) and events[-1]["state"] == "WIN"
                    response = {"ok": True, "receipt": receipt}
                elif operation == "step":
                    if gated:
                        raise AssayError(
                            "UNGATED_STEP_REFUSED | this registry run is daemon-gated: "
                            "paid actions go through `assay act/commit/reset` (which "
                            "carry graded predictions); a bare step is a gate bypass "
                            "and is refused"
                        )
                    if is_remote_config(config):
                        observed = session.step(
                            request["action"],
                            request.get("data"),
                            request.get("reasoning"),
                        )
                        if observed is None:
                            raise AssayError(
                                "REMOTE_SESSION_EXPIRED_OR_UNAVAILABLE | the competition server returned no observation. This remote run cannot be reconstructed; preserve its artifacts and use a fresh directory for another run"
                            )
                    else:
                        observed, shared["fresh_level"] = _competition_step(
                            session,
                            request["action"],
                            request.get("data"),
                            request.get("reasoning"),
                            fresh_level=shared["fresh_level"],
                        )
                    encoded = _encode_observation(observed)
                    shared["sequence"] += 1
                    append_jsonl(
                        paths.mutations,
                        {
                            "mutation_id": shared["sequence"],
                            "action": request["action"],
                            "data": request.get("data"),
                            "reasoning": request.get("reasoning"),
                            "observation": encoded,
                        },
                    )
                    response = {
                        "ok": True,
                        "observation": encoded,
                        "mutation_id": shared["sequence"],
                    }
                    terminal = encoded["state"] == "WIN"
                    if terminal and callable(getattr(session, "finalize", None)):
                        try:
                            session.finalize()
                        except Exception as error:  # noqa: BLE001 - action is already journaled
                            response["finalization_warning"] = (
                                f"{type(error).__name__}: {error}"
                            )
                else:
                    raise AssayError(f"unknown broker operation {operation!r}")
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
                    "finished_at": time.time(),
                },
            )
            server.close()
            paths.socket.unlink(missing_ok=True)
            return
