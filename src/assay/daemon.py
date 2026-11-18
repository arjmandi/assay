"""The daemon: the process that holds a run.

It owns the world session for the life of the run, executes every paid
action behind the gate, and is the only writer of the journal. The command
line is its client over a Unix socket, one JSON line each way. A run resumes
by replaying its own journal through a fresh session, and refuses to go on if
the world no longer reproduces a recorded observation.

The gate is an order of operations. Everything before the world step is a
refusal that costs nothing:

    1. validate the action against the pinned registry
    2. parse the prediction; refuse an empty one
    3. refuse an outcome that names an undeclared state
    4. hash and store every verifier the prediction names
    5. refuse if the run is complete, the budget short or the notes over their cap
    6. collect the modules' advice
    7. step the world once, timing it
    8. grade every outcome against the events before and after
    9. append the event, with its prediction and grades, to the journal

A batch takes every step through 1 to 6 before the first one spends, then
runs 7 to 9 per step and halts at the first miss, invalid outcome, progress
or loss, leaving the rest unspent.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import AssayError, grade, grammar, modules, show, states
from .journal import (
    Event,
    Paths,
    Run,
    advanced,
    append_jsonl,
    make_event,
    observation_of,
    read_json,
    read_notes,
    write_json,
)
from .world import (
    OVER,
    check_available,
    notes_cap,
    observe,
    open_session,
    parse_action,
    registry_hash,
    render_action,
    validate_action,
)

Step = tuple[str, dict[str, Any] | None, str, list[grammar.Outcome]]


class Daemon:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        self.run = Run.load(paths)
        self.run.check_chain()
        if registry_hash(self.run.registry) != self.run.config.get("registry_hash"):
            raise AssayError(
                "the pinned registry does not match the hash recorded at start",
                code="REGISTRY_CHANGED",
                hint="a run cannot change its own contract: restore .assay/registry.json",
            )
        self.registered = sorted(action["name"] for action in self.run.registry["actions"])
        self.modules = modules.load(self.run.registry)
        self.session = open_session(self.run.config["adapter"], paths.root, self.run.config)
        if self.run.events:
            self._replay()
        else:
            self.run.append(make_event(self._world(None, None), "START", None, None, "initial observation"))
        self._render()

    # --- the world ---------------------------------------------------------------

    def _world(self, action: str | None, params: dict[str, Any] | None) -> dict[str, Any]:
        """The session's observation, after one step when an action is given.
        Whatever the adapter raises is the world's error, and nothing is
        journaled for it."""
        try:
            raw = self.session.observation if action is None else self.session.step(action, params)
        except AssayError:
            raise
        except Exception as error:  # noqa: BLE001 - the world's failure, whatever it raised
            raise AssayError(
                f"{type(error).__name__}: {error}",
                code="WORLD_ERROR",
                hint="nothing was journaled for it; choose another action, or read the state with `assay status`",
            ) from error
        observed = observe(raw)
        if not observed["available_actions"]:
            observed["available_actions"] = self.registered  # a world that advertises nothing admits them all
        return observed

    def _replay(self) -> None:
        events = self.run.events
        if not getattr(self.session, "replayable", True):
            raise AssayError(
                "this world cannot be rebuilt by replay, so the run ended with its daemon",
                code="REPLAY_UNAVAILABLE",
                hint="keep this directory as the record and start another run in a fresh one",
            )
        for event in events:
            observed = self._world(None, None) if not event["id"] else self._world(event["action"], event["data"])
            if observed != observation_of(event):
                raise AssayError(
                    f"event {event['id']} no longer reproduces its recorded observation",
                    code="REPLAY_DIVERGED",
                    hint="the world or its seed changed; keep this directory and start another run in a fresh one",
                )

    def _render(self) -> None:
        if "frames" in self.run.last:
            from . import grid

            grid.render(self.paths, self.run.last)

    # --- the gate ----------------------------------------------------------------

    def _prepare(self, step: Mapping[str, Any]) -> Step:
        """Steps 1 to 4 for one action."""
        if step.get("params") is not None:
            name, params = validate_action(self.run.registry, step["action"], step["params"])
        else:
            name, params = parse_action([step["action"], *step.get("tokens", ())], self.run.registry)
        if name == "RESET":
            raise AssayError("a reset is its own command", code="BATCH_FORBIDDEN", hint="use `assay reset`")
        predict = str(step.get("predict") or "")
        outcomes = grammar.parse(predict)
        known = states.known(self.paths)
        for outcome in outcomes:
            if outcome.get("channel") not in (None, *known):
                raise AssayError(
                    f"outcome names undeclared state {outcome['channel']!r}",
                    code="STATE_UNKNOWN",
                    hint=f"declared states: {known}; declare one with `assay state declare "
                    f"{outcome['channel']} --path <dotted.path>` (or --file <extractor.py>)",
                )
        return name, params, predict, grade.admit(self.paths, outcomes)

    def _gates(self, planned: int) -> None:
        """Step 5: the run still open, the budget, the notes cap."""
        run = self.run
        if run.last["state"] == "WIN":
            raise AssayError("the goal is already reached; this run is complete", code="RUN_COMPLETE")
        if run.cap is not None and planned > run.cap - run.paid:
            raise AssayError(
                f"action budget cap={run.cap} spent={run.paid} remaining={max(0, run.cap - run.paid)}; "
                f"this command needs {planned} paid action(s) and was refused",
                code="BUDGET_EXHAUSTED",
            )
        cap = notes_cap(run.registry)
        size = len(read_notes(self.paths) or "")
        if cap is not None and size > 2 * cap:
            raise AssayError(
                f"NOTES.md is {size} chars, more than twice the {cap}-char cap",
                code="NOTES_CAP",
                hint="one page is the contract: move detail into files of your own and trim the notes",
            )

    def _advice(self, pending: Sequence[Mapping[str, Any]]) -> list[str]:
        lines: list[str] = []
        for item in pending:
            lines += modules.advisories(self.modules, self.run.events, item)
        return list(dict.fromkeys(lines))

    def _spend(self, step: Step, note: str = "") -> tuple[Event, list[grade.Grade]]:
        """Steps 7 to 9 for one action."""
        name, params, predict, outcomes = step
        before = self.run.last
        check_available(name, before["available_actions"])
        started = time.monotonic()
        observed = self._world(name, params)
        pending = make_event(observed, name, params, before, note)
        graded = grade.grade(self.run, outcomes, before, pending, time.monotonic() - started)
        event = self.run.append({**pending, "predict": predict, "predict_ok": grade.verdict(graded), "grade": graded})
        self._render()
        return event, graded

    # --- the operations -----------------------------------------------------------

    def act(self, args: Mapping[str, Any]) -> str:
        step = self._prepare(args)
        self._gates(1)
        advice = self._advice([{"kind": "act", "name": step[0], "params": step[1]}])
        before = self.run.last
        event, graded = self._spend(step, str(args.get("because") or ""))
        result, detail = show.result_of(event, graded)
        return show.receipt(
            self.run,
            {
                "result": result,
                "detail": detail,
                "grades": grade.lines(graded),
                "advice": advice,
                "states": states.changes(self.run, before, event),
            },
        )

    def commit(self, args: Mapping[str, Any]) -> str:
        if not args.get("steps"):
            raise AssayError(
                "a batch needs steps",
                code="COMMAND_ARGS",
                hint='pass --step "NAME pname=value :: outcomes", once per step, in order',
            )
        steps = [self._prepare(step) for step in args["steps"]]
        self._gates(len(steps))
        moves = {render_action(step[0], step[1]): {"kind": "commit", "name": step[0], "params": step[1]} for step in steps}
        advice = self._advice(list(moves.values()))
        before = self.run.last
        records = []
        result, detail = "PREDICTED", f"all {len(steps)} steps landed as predicted"
        for index, step in enumerate(steps):
            left = len(steps) - index - 1
            try:
                event, graded = self._spend(step)
            except AssayError as error:
                if not index:
                    raise  # nothing was spent
                self._refused("commit", error)
                result = "HALTED"
                detail = f"step {index + 1} was refused ({error.code}): {error.message}; it and {left} more were not spent"
                break
            marks = grade.lines(graded)
            ok = grade.verdict(graded) is True
            records.append(
                {
                    "event": event["id"],
                    "action": render_action(event["action"], event["data"]),
                    "ok": ok,
                    "problems": [line for line in marks if not line.startswith("✓")],
                }
            )
            if event["state"] in OVER or advanced(event) or not ok:
                result, detail = show.result_of(event, graded)
                detail = f"step {index + 1}: {detail}" + (f"; {left} remaining steps were discarded" if left else "")
                break
        return show.receipt(
            self.run,
            {
                "result": result,
                "detail": detail,
                "advice": advice,
                "states": states.changes(self.run, before, self.run.last),
                "steps": records,
            },
        )

    def reset(self, args: Mapping[str, Any]) -> str:
        self._gates(1)
        before = self.run.last
        reason = str(args.get("because") or "")
        if not reason and before["state"] != "GAME_OVER":
            raise AssayError(
                'reset needs --because "<why this state is worth abandoning>" (GAME_OVER excepted)',
                code="RESET_REASON",
            )
        advice = self._advice([{"kind": "reset", "name": "RESET", "params": None}])
        observed = self._world("RESET", None)
        self.run.append(make_event(observed, "RESET", None, before, reason or "the world reported GAME_OVER"))
        self._render()
        kind = show.unit(before)
        return show.receipt(
            self.run,
            {
                "result": "RESET",
                "detail": f"current {kind} rewound; completed {kind}s and the record are kept",
                "advice": advice,
            },
        )

    def declare(self, args: Mapping[str, Any]) -> str:
        name = str(args.get("name") or "")
        spec = states.declare(self.paths, name, path=args.get("path"), file=args.get("file"))
        return f"STATE | declared {name} ({spec['form']}); outcomes like `ch {name} = V` now parse and grade"

    # --- the socket ----------------------------------------------------------------

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        operation = {"act": self.act, "commit": self.commit, "reset": self.reset, "declare": self.declare}
        name = request.get("op")
        if name in ("ping", "stop"):
            return {"ok": True, "text": ""}
        try:
            if name not in operation:
                raise AssayError(f"unknown operation {name!r}", code="INTERNAL")
            return {"ok": True, "text": operation[str(name)](request.get("args") or {})}
        except AssayError as error:
            self._refused(str(name), error)
            return {"ok": False, "error": error.to_json()}
        except Exception as error:  # noqa: BLE001 - a bug answers as an error and the daemon lives
            traceback.print_exc()
            hint = f"the traceback is in {self.paths.log}"
            return {"ok": False, "error": AssayError(f"{type(error).__name__}: {error}", code="INTERNAL", hint=hint).to_json()}

    def _refused(self, op: str, error: AssayError) -> None:
        """Every refusal is kept beside the journal: what the agent tried and
        could not do is evidence for the diagnosis."""
        append_jsonl(self.paths.refusals, {"op": op, "code": error.code, "message": error.message.split("\n")[0]})

    def _answer(self, connection: socket.socket) -> Any:
        """One request line in, one reply line out; returns the operation asked for."""
        request: Any = {}
        try:
            with connection, connection.makefile("rwb") as stream:
                try:
                    request = json.loads(stream.readline() or b"{}")
                except ValueError:
                    pass
                request = request if isinstance(request, dict) else {}
                stream.write(json.dumps(self.handle(request)).encode() + b"\n")
                stream.flush()
        except OSError:
            pass  # the client gave up waiting; what was done is journaled
        return request.get("op")

    def serve(self) -> None:
        """Answer one request at a time until a stop, or the WIN that ends the run."""
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.paths.socket.parent.mkdir(parents=True, exist_ok=True)
        self.paths.socket.unlink(missing_ok=True)
        server.bind(str(self.paths.socket))
        self.paths.socket.chmod(0o600)
        server.listen(4)
        busy = stopping = False

        def terminate(*_: Any) -> None:
            # A stop signal waits for the request in hand: a paid action is
            # never cut between the world step and the journal.
            nonlocal stopping
            stopping = True
            if not busy:
                sys.exit(0)

        signal.signal(signal.SIGTERM, terminate)
        write_json(self.paths.daemon, {"status": "READY", "pid": os.getpid()})
        try:
            while self.run.last["state"] != "WIN" and not stopping:
                connection, _ = server.accept()
                busy = True
                try:
                    asked = self._answer(connection)
                finally:
                    busy = False
                if asked == "stop":
                    break
        finally:
            server.close()
            self.paths.socket.unlink(missing_ok=True)
            if self.paths.state.is_dir():
                write_json(self.paths.daemon, {"status": "STOPPED", "pid": os.getpid()})


def main() -> None:
    """`python -m assay.daemon RUN_DIR`: what `assay start` spawns."""
    paths = Paths(Path(sys.argv[1]))
    try:
        daemon = Daemon(paths)
    except Exception as error:  # noqa: BLE001 - whatever refused the start is reported by `assay start`
        if not isinstance(error, AssayError):
            error = AssayError(f"{type(error).__name__}: {error}", code="INTERNAL", hint=f"the log is {paths.log}")
        write_json(paths.daemon, {"status": "ERROR", "error": error.to_json()})
        raise
    daemon.serve()


# --- the client ------------------------------------------------------------------


def call(paths: Paths, op: str, args: Mapping[str, Any] | None = None, *, timeout: float = 10.0) -> str:
    """One operation through the daemon; its text, or the refusal it gave."""
    timeout = max(timeout, float(os.getenv("ASSAY_TIMEOUT") or 0))
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(paths.socket))
            with client.makefile("rwb") as stream:
                stream.write(json.dumps({"op": op, "args": dict(args or {})}).encode() + b"\n")
                stream.flush()
                reply = json.loads(stream.readline() or b"null")
    except TimeoutError:
        raise AssayError(
            f"the daemon did not answer within {timeout:g}s; it may still be inside the step",
            code="DAEMON_BUSY",
            hint="run `assay status` and read the last event before acting again (ASSAY_TIMEOUT raises the wait)",
        ) from None
    except (OSError, ValueError):
        reply = None
    if not isinstance(reply, dict):
        raise AssayError(
            "this run's daemon is not running",
            code="DAEMON_UNAVAILABLE",
            hint="the operator resumes the run with `assay start WORLD_ID`",
        )
    if not reply["ok"]:
        error = reply["error"]
        raise AssayError(error["message"], code=error["code"], hint=error.get("hint"))
    return str(reply["text"])


def alive(paths: Paths) -> bool:
    try:
        call(paths, "ping", timeout=2.0)
    except AssayError as error:
        return error.code == "DAEMON_BUSY"
    return True


def spawn(paths: Paths) -> None:
    """Start the daemon for this run directory and wait until it is ready,
    or raise what refused its start."""
    write_json(paths.daemon, {"status": "STARTING"})
    package = str(Path(__file__).resolve().parents[1])
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [package, os.getenv("PYTHONPATH")]))}
    with paths.log.open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 - the run's own daemon
            [sys.executable, "-m", "assay.daemon", str(paths.root)],
            cwd=paths.root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env=environment,
        )
    deadline = time.monotonic() + float(os.getenv("ASSAY_START_TIMEOUT") or 120)
    while time.monotonic() < deadline:
        state = read_json(paths.daemon, {})
        if state.get("status") == "ERROR":
            error = state["error"]
            raise AssayError(error["message"], code=error["code"], hint=error.get("hint"))
        if state.get("status") == "READY" and alive(paths):
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        process.terminate()  # a daemon that missed its deadline must not come up later
        process.wait()
    raise AssayError(
        "the daemon did not start in time",
        code="DAEMON_UNAVAILABLE",
        hint=f"its log is {paths.log}; ASSAY_START_TIMEOUT raises the wait for a world that is slow to open",
    )


def stop(paths: Paths) -> bool:
    """Ask the daemon to stop after the request in hand; whether one was running."""
    try:
        call(paths, "stop", timeout=120.0)
    except AssayError:
        return False
    return True


if __name__ == "__main__":
    main()
