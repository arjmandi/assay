"""The queue: launch every job of a design, at most `concurrency` at a time,
each in the operator-first order the protocols describe, and keep the state
of the whole thing under one directory.

Per job, one session is:

1. the operator's start, in the runner's own process: `assay --run-dir RUN
   start WORLD --adapter ... --registry ... --owner-token-file
   STATE/tokens/JOB.token`, with `ASSAY_ANCHOR_DIR=STATE/anchors`; the token
   file lies outside the run directory and the player never sees it; on a
   run that already exists the same command resumes it (the kernel replays
   the journal, or answers RESUMED when its daemon is still up);
2. the player's session, a subprocess with the run directory as its working
   directory, the rendered prompt on stdin and in the `{prompt}` argument, and
   `ASSAY` naming the launcher the start used.

A session that ends without WIN while the paid actions are below the budget
is relaunched (another `start`, another session) up to `max_sessions`; a
usage-limit or 429 signal in the player's report pauses the whole queue and
does not count as a session; a `STOP` file in the state directory stops new
launches and lets the running sessions finish, as do SIGINT and SIGTERM.
When a job reaches WIN or the cap, or runs out of sessions, its daemon is
stopped. A finished job is never launched again: on a rerun the runner reads
every run directory first and skips the ones whose journal says WIN or cap.

The state directory: `runs/JOB/` (the run directories), `tokens/`,
`anchors/`, `registries/` (materialized variants), `prompts/` (rendered),
`sessions/` (the ledger per job and the captured output per session),
`status.json`, `results.jsonl`, `summary.json`, `runner.lock`, `STOP`.
One runner per state directory, held by a file lock.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any

from . import EvalError
from .jobs import Design, Job, check_budget, missing_inputs, registry_variant, variant_name
from .render import render_file
from .results import outcome_of, player_report, read_run, row_for, write_rows
from .summary import render_summary, summarize

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LAUNCHER = REPO / "bin" / "assay"
RATE_LIMIT = re.compile(
    r"rate.?limit|usage limit|hit your limit|limit reached|out of (extra )?usage|"
    r"\b429\b|too many requests|overloaded|resets at|quota",
    re.I,
)
START_TIMEOUT_SECONDS = 900
KILL_GRACE_SECONDS = 10


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def looks_rate_limited(report: dict[str, Any] | None, stderr: str, exit_code: int) -> bool:
    """A usage-limit signal: the words in the player's result or its stderr,
    on a session that errored or barely started."""
    blobs = [stderr]
    turns = 0
    errored = exit_code != 0
    if report:
        blobs.append(str(report.get("result") or ""))
        blobs.append(str(report.get("error") or ""))
        blobs.append(str(report.get("api_error_status") or ""))
        if report.get("is_error"):
            errored = True
            blobs.append(str(report.get("subtype") or ""))
        turns = int(report.get("num_turns") or 0)
    return bool(RATE_LIMIT.search("\n".join(blobs))) and (errored or turns <= 2)


class Runner:
    def __init__(
        self,
        design: Design,
        *,
        state_dir: Path,
        launcher: list[str],
        concurrency: int | None = None,
        max_sessions: int | None = None,
        max_hours: float | None = None,
        poll_seconds: float = 10.0,
        pause_minutes: float = 30.0,
        bootstrap_seed: int = 0,
        bootstrap_resamples: int = 2000,
    ):
        self.design = design
        self.state = Path(state_dir).resolve()
        self.launcher = list(launcher)
        self.launcher_text = shlex.join(self.launcher)
        self.concurrency = concurrency or design.concurrency
        self.max_sessions = max_sessions or design.max_sessions
        self.max_hours = max_hours or design.max_hours
        self.poll_seconds = poll_seconds
        self.pause_minutes = pause_minutes
        self.bootstrap_seed = bootstrap_seed
        self.bootstrap_resamples = bootstrap_resamples
        self.queue: list[str] = []
        self.running: dict[str, dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {
            job.id: {
                "state": "queued", "sessions": 0, "outcome": None, "started": None,
                "ended": None, "last_exit": None, "journal": None, "pauses": [],
            }
            for job in design.jobs
        }
        self.paused_until: float | None = None
        self.pause_log: list[dict[str, Any]] = []
        self.stop_requested = False
        self.started = now()
        self._lock: IO[str] | None = None

    # ---- places -------------------------------------------------------------
    def run_dir(self, job: Job) -> Path:
        return self.state / "runs" / job.id

    def token_file(self, job: Job) -> Path:
        return self.state / "tokens" / f"{job.id}.token"

    @property
    def anchors(self) -> Path:
        return self.state / "anchors"

    def registry_path(self, job: Job) -> Path:
        return self.state / "registries" / variant_name(job) if job.overlay else job.registry

    def prompt_path(self, job: Job, session: int) -> Path:
        return self.state / "prompts" / f"{job.id}.s{session}.md"

    def ledger_path(self, job: Job) -> Path:
        return self.state / "sessions" / f"{job.id}.json"

    def session_file(self, job: Job, session: int, suffix: str) -> Path:
        return self.state / "sessions" / f"{job.id}.s{session}.{suffix}"

    @property
    def status_path(self) -> Path:
        return self.state / "status.json"

    @property
    def stop_file(self) -> Path:
        return self.state / "STOP"

    @property
    def lock_path(self) -> Path:
        return self.state / "runner.lock"

    # ---- the pieces of one session --------------------------------------------
    def environment(self) -> dict[str, str]:
        return {**os.environ, "ASSAY_ANCHOR_DIR": str(self.anchors), "ASSAY": self.launcher_text}

    def start_command(self, job: Job) -> list[str]:
        command = [
            *self.launcher, "--run-dir", str(self.run_dir(job)), "start", job.world.id,
            "--adapter", job.world.adapter, "--registry", str(self.registry_path(job)),
            "--owner-token-file", str(self.token_file(job)),
        ]
        if job.world.pass_seed:
            command += ["--seed", str(job.seed)]
        return command

    def stop_command(self, job: Job) -> list[str]:
        return [*self.launcher, "--run-dir", str(self.run_dir(job)), "stop"]

    def prompt_values(self, job: Job) -> dict[str, str]:
        values = {
            "WORLD": job.world.id, "LEVELS": str(job.world.levels), "BUDGET": str(job.budget),
            "SEED": str(job.seed), "MODEL": job.model, "JOB": job.id,
            "RUN_DIR": str(self.run_dir(job)), "REGISTRY": str(self.registry_path(job)),
            "ASSAY": self.launcher_text, "ADAPTER": job.world.adapter, "REPO": str(REPO),
            "STATE_DIR": str(self.state),
        }
        if job.arm.constitution is not None:
            values["CONSTITUTION"] = str(job.arm.constitution)
            values["CONSTITUTION_NAME"] = job.arm.constitution.name
        return values

    def player_command(self, job: Job, session: int, prompt_text: str) -> list[str]:
        prompt_file = self.prompt_path(job, session)
        fills = {
            "model": job.model, "prompt": prompt_text, "prompt_file": str(prompt_file),
            "run_dir": str(self.run_dir(job)), "world": job.world.id, "job": job.id,
            "seed": str(job.seed), "assay": self.launcher_text, "repo": str(REPO),
            "python": sys.executable,
        }
        return [
            re.sub(r"\{([a-z_]+)\}", lambda match: fills[match.group(1)], argument)
            for argument in self.design.command
        ]

    def player_command_text(self, job: Job, session: int) -> str:
        """The player's command as a shell line, the prompt argument shown as
        the file it is rendered to (its text is the file's content)."""
        prompt_file = self.prompt_path(job, session)
        marker = "\0PROMPT\0"
        parts = []
        for argument in self.player_command(job, session, marker):
            if argument == marker:
                parts.append(f'"$(cat {shlex.quote(str(prompt_file))})"')
            else:
                parts.append(shlex.quote(argument.replace(marker, f"$(cat {prompt_file})")))
        return " ".join(parts)

    def commands_for(self, job: Job, session: int) -> list[str]:
        """What one session of the job runs, as lines a shell could run."""
        lines = [
            f"mkdir -p {shlex.quote(str(self.run_dir(job)))} "
            f"{shlex.quote(str(self.token_file(job).parent))} {shlex.quote(str(self.anchors))}"
        ]
        if job.overlay is not None:
            lines.append(
                f"write {self.registry_path(job)} = {job.registry} + "
                f"{json.dumps(job.overlay, sort_keys=True)}"
            )
        lines.append(f"render {self.prompt_path(job, session)} from {job.arm.prompt}")
        lines.append(
            f"ASSAY_ANCHOR_DIR={shlex.quote(str(self.anchors))} "
            + shlex.join(self.start_command(job))
        )
        lines.append(
            f"(cd {shlex.quote(str(self.run_dir(job)))} && ASSAY={shlex.quote(self.launcher_text)} "
            f"{self.player_command_text(job, session)} < {shlex.quote(str(self.prompt_path(job, session)))})"
        )
        return lines

    # ---- the dry run ------------------------------------------------------------
    def dry_run(self) -> int:
        """Print every command the queue would run and touch nothing."""
        problems: list[str] = []
        for job in self.design.jobs:
            print(
                f"JOB | {job.id} | world {job.world.id} | arm {job.arm.name} | seed {job.seed} | "
                f"budget {job.budget} | model {job.model} | run {self.run_dir(job)}"
            )
            for line in self.commands_for(job, session=1):
                print(f"  {line}")
            try:
                check_budget(job)
                if job.arm.prompt.is_file():
                    render_file(job.arm.prompt, self.prompt_values(job))
            except EvalError as error:
                problems.append(str(error))
        missing = missing_inputs(self.design)
        for job_id, label, path in missing:
            print(f"MISSING | {job_id} | {label} | {path}")
        for problem in problems:
            print(f"PROBLEM | {problem}")
        tail = ""
        if missing or problems:
            tail = (
                f" | {len(missing)} missing input(s), {len(problems)} problem(s); "
                "a live run refuses to launch until they are fixed"
            )
        print(
            f"DRY RUN | {self.design.name} | {len(self.design.jobs)} job(s) | concurrency "
            f"{self.concurrency} | max sessions {self.max_sessions} | {self.max_hours:g}h per "
            f"session | state {self.state} | nothing written{tail}"
        )
        return 1 if missing or problems else 0

    # ---- the lock and the state files ---------------------------------------------
    def acquire_lock(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            holder = handle.read().strip()
            handle.close()
            raise EvalError(
                f"another runner holds {self.lock_path}"
                + (f" ({holder})" if holder else "")
                + "; one runner per state directory"
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid {os.getpid()} since {self.started}\n")
        handle.flush()
        self._lock = handle

    def release_lock(self) -> None:
        if self._lock is not None:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    def load_status(self) -> None:
        """What the previous runner recorded about each job (its outcome in
        particular), so a rerun and a summarize pass start from it."""
        try:
            previous = json.loads(self.status_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        jobs = previous.get("jobs") if isinstance(previous, dict) else None
        if not isinstance(jobs, dict):
            return
        for job_id, record in self.records.items():
            old = jobs.get(job_id)
            if isinstance(old, dict):
                for key in ("outcome", "started", "ended", "pauses"):
                    if old.get(key) is not None:
                        record[key] = old[key]

    def ledger(self, job: Job) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.ledger_path(job).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []

    def counted_sessions(self, job: Job) -> int:
        return sum(1 for record in self.ledger(job) if record.get("counted", True))

    def write_status(self, final: str | None = None) -> None:
        payload = {
            "updated": now(), "started": self.started, "design": self.design.name,
            "jobs_file": str(self.design.path), "state_dir": str(self.state),
            "launcher": self.launcher_text, "concurrency": self.concurrency,
            "max_sessions": self.max_sessions, "max_hours": self.max_hours,
            "paused_until": (
                dt.datetime.fromtimestamp(self.paused_until, dt.timezone.utc).isoformat(timespec="seconds")
                if self.paused_until else None
            ),
            "pauses": self.pause_log, "stop_requested": self.stop_requested,
            "queued": list(self.queue), "running": sorted(self.running),
            "done": [j for j, r in self.records.items() if r["state"] == "done"],
            "failed": [j for j, r in self.records.items() if r["state"] == "failed"],
            "stopped": [j for j, r in self.records.items() if r["state"] == "stopped"],
            "jobs": self.records, "final": final,
        }
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(self.status_path)

    def journal_summary(self, job: Job) -> dict[str, Any] | None:
        facts = read_run(self.run_dir(job))
        if facts is None:
            return None
        return {
            "events": facts.events, "paid": facts.paid_actions, "state": facts.state,
            "levels_completed": facts.levels_completed, "win_levels": facts.win_levels,
        }

    # ---- launching -------------------------------------------------------------------
    def materialize_registry(self, job: Job) -> None:
        variant = registry_variant(job)
        if variant is None:
            return
        target = self.registry_path(job)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(variant, indent=2) + "\n"
        if not target.exists() or target.read_text() != text:
            target.write_text(text)

    def launch(self, job: Job) -> None:
        # The attempt number names the session's files; a rate-limited attempt
        # keeps its number and its output but does not count toward the limit.
        session = len(self.ledger(job)) + 1
        record = self.records[job.id]
        try:
            self._launch(job, session)
        except EvalError as error:
            record.update({"state": "failed", "outcome": "START_FAILED", "ended": now(), "error": str(error)})
            print(f"FAIL | {job.id} | session {session} | {error}", flush=True)
            self.stop_daemon(job, session)
            return
        record.update({
            "state": "running", "sessions": session, "started": record["started"] or now(),
            "pid": self.running[job.id]["proc"].pid, "session_started": now(),
        })
        print(
            f"LAUNCH | {job.id} | session {session} | pid {self.running[job.id]['proc'].pid} | "
            f"cwd {self.run_dir(job)}",
            flush=True,
        )

    def _launch(self, job: Job, session: int) -> None:
        for label, path in job.inputs:
            if not path.is_file():
                raise EvalError(f"{label} {path} does not exist")
        check_budget(job)
        for directory in (self.run_dir(job), self.token_file(job).parent, self.anchors, self.state / "sessions"):
            directory.mkdir(parents=True, exist_ok=True)
        self.materialize_registry(job)
        prompt_text = render_file(job.arm.prompt, self.prompt_values(job))
        prompt_file = self.prompt_path(job, session)
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(prompt_text)
        env = self.environment()
        # The operator's start, before the player exists.
        try:
            started = subprocess.run(
                self.start_command(job), env=env, capture_output=True, text=True,
                timeout=START_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise EvalError(f"`assay start` did not return within {START_TIMEOUT_SECONDS}s") from None
        self.session_file(job, session, "start.log").write_text(
            f"$ {shlex.join(self.start_command(job))}\n{started.stdout}{started.stderr}"
        )
        if started.returncode != 0:
            reason = (started.stderr or started.stdout).strip().splitlines()
            raise EvalError(
                f"`assay start` exited {started.returncode}: "
                + (reason[-1] if reason else "no output")
            )
        # The player's session, in the run directory, with the daemon up.
        out = self.session_file(job, session, "player.out").open("wb")
        err = self.session_file(job, session, "player.err").open("wb")
        prompt_handle = prompt_file.open("rb")
        argv = self.player_command(job, session, prompt_text)
        try:
            proc = subprocess.Popen(
                argv, cwd=self.run_dir(job), env=env, stdin=prompt_handle,
                stdout=out, stderr=err, start_new_session=True,
            )
        except OSError as error:
            out.close()
            err.close()
            prompt_handle.close()
            raise EvalError(f"the player could not start: {error}") from None
        prompt_handle.close()
        self.running[job.id] = {
            "proc": proc, "session": session, "started": now(), "started_monotonic": time.monotonic(),
            "deadline": time.monotonic() + self.max_hours * 3600.0, "out": out, "err": err,
            "command": self.player_command_text(job, session),
        }

    def stop_daemon(self, job: Job, session: int) -> None:
        """`assay stop` on the run; harmless when the daemon already exited."""
        try:
            stopped = subprocess.run(
                self.stop_command(job), env=self.environment(), capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"WARN | {job.id} | `assay stop` failed: {error}", flush=True)
            return
        self.state.joinpath("sessions").mkdir(parents=True, exist_ok=True)
        self.session_file(job, session, "stop.log").write_text(
            f"$ {shlex.join(self.stop_command(job))}\n{stopped.stdout}{stopped.stderr}"
        )

    def _kill(self, info: dict[str, Any]) -> None:
        proc = info["proc"]
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + KILL_GRACE_SECONDS
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()

    def finish(self, job: Job, info: dict[str, Any], *, timed_out: bool) -> None:
        proc = info["proc"]
        info["out"].close()
        info["err"].close()
        session = info["session"]
        stdout = self.session_file(job, session, "player.out").read_text(errors="replace")
        stderr = self.session_file(job, session, "player.err").read_text(errors="replace")
        report = player_report(stdout)
        code = int(proc.returncode)
        rate_limited = looks_rate_limited(report, stderr[-4000:], code)
        wall = round(time.monotonic() - info["started_monotonic"], 1)
        record = self.records[job.id]
        record["last_exit"] = code
        record["journal"] = self.journal_summary(job)
        session_record = {
            "session": session, "counted": not rate_limited, "started": info["started"], "ended": now(),
            "wall_seconds": wall, "wall_limit_seconds": int(self.max_hours * 3600), "exit_code": code,
            "timed_out": timed_out, "rate_limited": rate_limited, "command": info["command"],
            "model": job.model, "prompt_file": str(self.prompt_path(job, session)),
            "report": report, "stdout_tail": stdout[-4000:] if report is None else None,
            "stderr_tail": stderr[-4000:], "journal_at_exit": record["journal"],
        }
        ledger = self.ledger(job)
        ledger.append(session_record)
        self.ledger_path(job).parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path(job).write_text(json.dumps(ledger, indent=2) + "\n")
        facts = read_run(self.run_dir(job))
        outcome = outcome_of(facts, job.budget)
        counted = self.counted_sessions(job)
        record["sessions"] = counted
        if rate_limited:
            self.paused_until = time.time() + self.pause_minutes * 60.0
            entry = {"job": job.id, "at": now(), "minutes": self.pause_minutes}
            self.pause_log.append(entry)
            record["pauses"].append(entry)
            record["state"] = "queued"
            self.queue.insert(0, job.id)
            print(
                f"PAUSE | {job.id} | usage limit signalled; the queue pauses "
                f"{self.pause_minutes:g} min and the session does not count",
                flush=True,
            )
        elif outcome:
            record.update({"state": "done", "outcome": outcome, "ended": now()})
            print(f"DONE | {job.id} | {outcome} | {counted} session(s) | paid actions {facts.paid_actions if facts else 0}", flush=True)
            self.stop_daemon(job, session)
        elif counted < self.max_sessions and not self.stop_requested:
            record["state"] = "queued"
            self.queue.insert(0, job.id)
            print(
                f"RELAUNCH | {job.id} | session {session} ended without WIN or cap (exit {code}"
                f"{', wall limit' if timed_out else ''}); another session is queued",
                flush=True,
            )
        else:
            outcome = "STOPPED" if self.stop_requested else "SESSIONS_EXHAUSTED"
            record.update({"state": "failed", "outcome": outcome, "ended": now()})
            print(f"FAIL | {job.id} | {outcome} | {counted} session(s) (exit {code})", flush=True)
            self.stop_daemon(job, session)

    # ---- the queue -----------------------------------------------------------------
    def classify(self) -> None:
        """Read every run directory first: a job whose journal says WIN or cap
        is done and is not launched; one that ran out of sessions earlier is
        reported as it stands; the rest are queued, the interrupted ones
        resuming through the kernel's own start."""
        for job in self.design.jobs:
            record = self.records[job.id]
            facts = read_run(self.run_dir(job))
            outcome = outcome_of(facts, job.budget)
            counted = self.counted_sessions(job)
            record["sessions"] = counted
            record["journal"] = self.journal_summary(job)
            if outcome:
                record.update({"state": "done", "outcome": outcome, "skipped": True, "ended": now()})
                print(f"SKIP | {job.id} | already {outcome} | paid actions {facts.paid_actions if facts else 0}", flush=True)
            elif counted >= self.max_sessions:
                record.update({"state": "failed", "outcome": "SESSIONS_EXHAUSTED", "skipped": True, "ended": now()})
                print(f"SKIP | {job.id} | {counted} session(s) already, max {self.max_sessions}", flush=True)
            else:
                record.update({"state": "queued", "outcome": None, "ended": None})
                self.queue.append(job.id)
                if facts is not None:
                    print(f"RESUME | {job.id} | {facts.paid_actions} paid action(s) so far | {counted} session(s)", flush=True)

    def run(self) -> int:
        self.acquire_lock()
        previous = {signal.SIGINT: signal.getsignal(signal.SIGINT), signal.SIGTERM: signal.getsignal(signal.SIGTERM)}

        def on_signal(signum: int, _frame: Any) -> None:
            self.stop_requested = True
            print(f"STOP | signal {signum} | no new launches; running sessions finish", flush=True)

        try:
            self.load_status()
            self.classify()
            self.write_status()
            signal.signal(signal.SIGINT, on_signal)
            signal.signal(signal.SIGTERM, on_signal)
            while self.queue or self.running:
                for job_id, info in list(self.running.items()):
                    job = self.design.job(job_id)
                    if info["proc"].poll() is not None:
                        del self.running[job_id]
                        self.finish(job, info, timed_out=False)
                        self.write_status()
                    elif time.monotonic() > info["deadline"]:
                        print(f"TIMEOUT | {job_id} | session {info['session']} past {self.max_hours:g}h; terminated", flush=True)
                        self._kill(info)
                        del self.running[job_id]
                        self.finish(job, info, timed_out=True)
                        self.write_status()
                if self.stop_file.exists() and not self.stop_requested:
                    self.stop_requested = True
                    print(f"STOP | {self.stop_file} seen | no new launches; running sessions finish", flush=True)
                if self.stop_requested and self.queue:
                    for job_id in self.queue:
                        self.records[job_id].update({"state": "stopped", "outcome": "STOPPED"})
                    self.queue.clear()
                    self.write_status()
                paused = self.paused_until is not None and time.time() < self.paused_until
                if self.paused_until is not None and not paused:
                    self.paused_until = None
                while self.queue and len(self.running) < self.concurrency and not paused and not self.stop_requested:
                    self.launch(self.design.job(self.queue.pop(0)))
                    self.write_status()
                if not self.queue and not self.running:
                    break
                time.sleep(self.poll_seconds)
            final = "stopped" if self.stop_requested else "drained"
            self.write_status(final=final)
            print(f"QUEUE | {final} | done {len([r for r in self.records.values() if r['state'] == 'done'])} | failed {len([r for r in self.records.values() if r['state'] == 'failed'])} | stopped {len([r for r in self.records.values() if r['state'] == 'stopped'])}", flush=True)
            self.write_results()
            return 0
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            self.release_lock()

    # ---- results ---------------------------------------------------------------
    def rows(self) -> list[dict[str, Any]]:
        rows = []
        for job in self.design.jobs:
            facts = read_run(self.run_dir(job))
            sessions = self.ledger(job)
            if facts is None and not sessions:
                continue
            counted = [record for record in sessions if record.get("counted", True)]
            outcome = outcome_of(facts, job.budget) or self.records[job.id].get("outcome")
            rows.append(row_for(job, facts, counted, outcome=outcome, run_dir=f"runs/{job.id}"))
        return rows

    def write_results(self) -> int:
        rows = self.rows()
        write_rows(self.state / "results.jsonl", rows)
        summary = summarize(
            rows, name=self.design.name, seed=self.bootstrap_seed, resamples=self.bootstrap_resamples,
        )
        (self.state / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"RESULTS | {self.state / 'results.jsonl'} | {len(rows)} row(s) | {self.state / 'summary.json'}", flush=True)
        print(render_summary(summary), flush=True)
        return 0

    def summarize_only(self) -> int:
        """Recompute the rows and the summary from the state without launching."""
        self.acquire_lock()
        try:
            self.load_status()
            return self.write_results()
        finally:
            self.release_lock()


def launcher_argv(text: str | None) -> list[str]:
    """The launcher as argv: `--assay` split like a shell would, default
    `bin/assay` of this checkout."""
    if not text:
        return [str(DEFAULT_LAUNCHER)]
    argv = shlex.split(text)
    if not argv:
        raise EvalError("--assay must name a command")
    return argv


def default_state_dir(design: Design) -> Path:
    return Path.cwd() / "eval-state" / design.name
