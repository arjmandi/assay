"""The Claude Code baseline on ARC-AGI-3 (no ASSAY): each public game played by
`claude -p` through play.py alone, under the budgets the ASSAY rounds played
under, so only the harness differs.

    python3 bench/arcagi/baseline/run_baseline.py --state DIR \\
        [--models claude-sonnet-5-5,claude-haiku-5-5] [--games all] [--effort medium] \\
        [--sessions 5] [--concurrency 6] [--cap 1500] [--usd 15] [--hours 4]

Per job (model x game): a game server (game_server.py) holds the game for the
whole job; sessions launch in the job's run directory with ARC_PLAY_SOCKET set
and the player's tools pinned as in the ASSAY rounds (Bash, Read, Glob, Grep,
Write, Edit; no web, no agents); a session that ends with the game unwon and
actions left is followed by another, up to `--sessions`; a usage-limit
signal pauses the queue and does not count as a session (the ASSAY runner's
test). Run it from the repository root with the interpreter that has numpy;
the game servers run under ASSAY_PYTHON (the arc_agi environment). Launch it
with every ANTHROPIC_* and CLAUDE* variable unset, so the players use the
operator's claude.ai login.

The state directory: runs/<job>/ (the player's directory), journals/<job>/
(the server's journal, outside the player's reach), sessions/<job>.json (the
ledger), sessions/<job>.s<N>.player.{out,err}, prompts/, runner.log,
results.jsonl, STOP (no new launches).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))
from tools.eval.runner import looks_rate_limited  # noqa: E402

JOBS_FILE = REPO / "bench" / "arcagi" / "jobs" / "sonnet-haiku-5-5.json"
PROMPT = HERE / "baseline_player.md"
PLAY = HERE / "play.py"
SERVER = HERE / "game_server.py"
SHORT = {"claude-sonnet-5-5": "sonnet55", "claude-haiku-5-5": "haiku55"}
PAUSE_MINUTES = 30


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def log(state: Path, line: str) -> None:
    with (state / "runner.log").open("a") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


class Job:
    def __init__(self, model: str, game: str, levels: int, state: Path) -> None:
        self.model, self.game, self.levels = model, game, levels
        self.id = f"ccb-{game}-{SHORT.get(model, model)}"
        self.run_dir = state / "runs" / self.id
        self.journal = state / "journals" / self.id
        self.ledger_path = state / "sessions" / f"{self.id}.json"
        self.socket = Path("/tmp/ccb") / f"{self.id}.sock"
        self.server: subprocess.Popen[bytes] | None = None

    def ledger(self) -> list[dict[str, Any]]:
        return json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else []

    def counted(self) -> int:
        return sum(1 for record in self.ledger() if record.get("counted", True))

    def facts(self) -> dict[str, Any]:
        path = self.journal / ".arc" / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        last = events[-1] if events else {}
        return {
            "paid": sum(1 for event in events if event.get("counts_action")),
            "state": last.get("state"),
            "levels": last.get("levels_completed", 0),
        }


class Baseline:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state = Path(args.state).resolve()
        for sub in ("runs", "journals", "sessions", "prompts"):
            (self.state / sub).mkdir(parents=True, exist_ok=True)
        Path("/tmp/ccb").mkdir(exist_ok=True)
        design = json.loads(JOBS_FILE.read_text())
        levels = {world["id"]: int(world["levels"]) for world in design["worlds"]}
        games = sorted(levels) if args.games == "all" else args.games.split(",")
        # Models alternate in the queue, so both play at once, as in the 2026-10-25 sweep.
        self.jobs = [Job(model, game, levels[game], self.state) for game in games for model in args.models.split(",")]
        self.python = os.environ.get("ASSAY_PYTHON", sys.executable)
        self.paused_until = 0.0
        (self.state / "design.json").write_text(json.dumps({
            "models": args.models.split(","), "games": games, "effort": args.effort, "sessions": args.sessions,
            "concurrency": args.concurrency, "cap": args.cap, "usd": args.usd, "hours": args.hours,
            "prompt": str(PROMPT), "play": str(PLAY), "started": now(),
        }, indent=1) + "\n")

    # ---- one job ----------------------------------------------------------------------------------
    def start_server(self, job: Job) -> None:
        job.run_dir.mkdir(parents=True, exist_ok=True)
        job.journal.mkdir(parents=True, exist_ok=True)
        # A socket left by a server that was killed would read as ready.
        job.socket.unlink(missing_ok=True)
        log_file = (job.journal / "server.log").open("a")
        env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
        job.server = subprocess.Popen(
            [self.python, str(SERVER), "--game", job.game, "--socket", str(job.socket), "--journal", str(job.journal),
             "--run-dir", str(job.run_dir), "--cap", str(self.args.cap)],
            cwd=job.journal, stdout=log_file, stderr=subprocess.STDOUT, env=env,
        )
        for _ in range(120):
            if job.socket.exists():
                return
            if job.server.poll() is not None:
                raise RuntimeError(f"the game server of {job.id} exited ({job.journal / 'server.log'})")
            time.sleep(0.5)
        raise RuntimeError(f"the game server of {job.id} did not open its socket")

    def stop_server(self, job: Job) -> None:
        if job.server is not None and job.server.poll() is None:
            job.server.terminate()
            try:
                job.server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                job.server.kill()
        job.server = None

    def prompt(self, job: Job, session: int) -> str:
        note = (
            "This is the first session of this run."
            if session == 1
            else f"This is session {session} of this run: earlier sessions played this game before you, and the "
            "game continues from where they left it. Read NOTES.md first if it exists."
        )
        play = f"python3 {PLAY}"
        text = PROMPT.read_text()
        for key, value in {"RUN_DIR": str(job.run_dir), "WORLD": job.game, "LEVELS": str(job.levels),
                           "PLAY": play, "BUDGET": str(self.args.cap), "SESSION_NOTE": note}.items():
            text = text.replace("{{" + key + "}}", value)
        (self.state / "prompts" / f"{job.id}.s{session}.md").write_text(text)
        return text

    def command(self, job: Job, prompt: str) -> list[str]:
        return [
            "claude", "--model", job.model, "-p", prompt, "--output-format", "json",
            "--effort", self.args.effort, "--thinking-display", "summarized",
            "--max-budget-usd", str(self.args.usd), "--strict-mcp-config", "--permission-mode", "acceptEdits",
            "--tools", "Bash", "Read", "Glob", "Grep", "Write", "Edit",
            "--allowedTools", "Bash", "Read", "Glob", "Grep", "Write(./**)", "Edit(./**)", "MultiEdit(./**)",
            "--disallowedTools", "WebFetch", "WebSearch", "Agent", "Task", "NotebookEdit", "TodoWrite", "KillShell",
        ]

    def launch(self, job: Job) -> dict[str, Any]:
        session = len(job.ledger()) + 1
        prompt = self.prompt(job, session)
        out = (self.state / "sessions" / f"{job.id}.s{session}.player.out").open("w")
        err = (self.state / "sessions" / f"{job.id}.s{session}.player.err").open("w")
        env = {**os.environ, "ARC_PLAY_SOCKET": str(job.socket)}
        process = subprocess.Popen(self.command(job, prompt), cwd=job.run_dir, stdout=out, stderr=err, env=env,
                                   start_new_session=True)
        log(self.state, f"LAUNCH | {job.id} | session {session} | pid {process.pid} | cwd {job.run_dir}")
        return {"process": process, "session": session, "started": now(), "t0": time.time(),
                "paid_before": job.facts()["paid"], "out": out, "err": err}

    def finish(self, job: Job, info: dict[str, Any], timed_out: bool) -> str | None:
        """Record the session; the job's outcome when it is over, else None."""
        info["out"].close()
        info["err"].close()
        session = info["session"]
        out_path = self.state / "sessions" / f"{job.id}.s{session}.player.out"
        err_path = self.state / "sessions" / f"{job.id}.s{session}.player.err"
        try:
            report = json.loads(out_path.read_text() or "null")
        except ValueError:
            report = None
        stderr = err_path.read_text(errors="replace")[-4000:]
        code = info["process"].returncode
        limited = looks_rate_limited(report if isinstance(report, dict) else None, stderr, code if code is not None else -1)
        facts = job.facts()
        ledger = job.ledger()
        ledger.append({
            "session": session, "counted": not limited, "started": info["started"], "ended": now(),
            "wall_seconds": int(time.time() - info["t0"]), "exit_code": code, "timed_out": timed_out,
            "rate_limited": limited, "paid_before": info["paid_before"], "paid_after": facts["paid"],
            "state_after": facts["state"], "levels_after": facts["levels"],
            "report": report if isinstance(report, dict) else None,
        })
        job.ledger_path.write_text(json.dumps(ledger, indent=1) + "\n")
        if limited:
            self.paused_until = time.time() + PAUSE_MINUTES * 60
            log(self.state, f"PAUSE | {job.id} | usage limit signalled; the queue pauses {PAUSE_MINUTES} min and the session does not count")
            return None
        if facts["state"] == "WIN":
            return "WIN"
        if facts["paid"] >= self.args.cap:
            return "CAP"
        if job.counted() >= self.args.sessions:
            return "SESSIONS_EXHAUSTED"
        log(self.state, f"RELAUNCH | {job.id} | session {session} ended without WIN or cap (exit {code}); another session is queued")
        return None

    def result(self, job: Job, outcome: str) -> None:
        facts = job.facts()
        ledger = job.ledger()
        row = {
            "job": job.id, "model": job.model, "game": job.game, "outcome": outcome, "levels": facts["levels"],
            "of": job.levels, "paid": facts["paid"], "sessions": job.counted(),
            "usd": round(sum(float((r.get("report") or {}).get("total_cost_usd") or 0) for r in ledger), 4),
            "wall_seconds": sum(int(r.get("wall_seconds") or 0) for r in ledger),
        }
        with (self.state / "results.jsonl").open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        verb = "DONE" if outcome in ("WIN", "CAP") else "FAIL"
        log(self.state, f"{verb} | {job.id} | {outcome} | {job.counted()} session(s) | levels {facts['levels']}/{job.levels} | paid actions {facts['paid']}")

    # ---- the queue --------------------------------------------------------------------------------
    def run(self) -> None:
        done = set()
        results = self.state / "results.jsonl"
        if results.exists():
            done = {json.loads(line)["job"] for line in results.read_text().splitlines() if line.strip()}
        queue = [job for job in self.jobs if job.id not in done]
        running: dict[str, tuple[Job, dict[str, Any]]] = {}
        stop = self.state / "STOP"
        while queue or running:
            for job_id, (job, info) in list(running.items()):
                process = info["process"]
                timed_out = False
                if process.poll() is None and time.time() - info["t0"] > self.args.hours * 3600:
                    os.killpg(process.pid, signal.SIGTERM)
                    timed_out = True
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                if process.poll() is None:
                    continue
                del running[job_id]
                outcome = self.finish(job, info, timed_out)
                if outcome is None:
                    queue.insert(0, job)
                else:
                    self.stop_server(job)
                    self.result(job, outcome)
            while (queue and len(running) < self.args.concurrency and time.time() >= self.paused_until
                   and not stop.exists()):
                job = queue.pop(0)
                if job.server is None or job.server.poll() is not None:
                    self.start_server(job)
                facts = job.facts()
                if facts["state"] == "WIN" or facts["paid"] >= self.args.cap:
                    self.stop_server(job)
                    self.result(job, "WIN" if facts["state"] == "WIN" else "CAP")
                    continue
                running[job.id] = (job, self.launch(job))
            if stop.exists() and not running:
                log(self.state, "STOP | the STOP file is present and no session runs")
                break
            time.sleep(5)
        for job in self.jobs:
            self.stop_server(job)
        log(self.state, f"QUEUE | drained | {len(self.jobs)} job(s)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_baseline")
    parser.add_argument("--state", required=True)
    parser.add_argument("--models", default="claude-sonnet-5-5,claude-haiku-5-5")
    parser.add_argument("--games", default="all")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--cap", type=int, default=1500)
    parser.add_argument("--usd", type=float, default=15.0)
    parser.add_argument("--hours", type=float, default=4.0)
    if shutil.which("claude") is None:
        raise SystemExit("claude is not on PATH")
    Baseline(parser.parse_args()).run()


if __name__ == "__main__":
    main()
