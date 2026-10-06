#!/usr/bin/env python3
"""night_orchestrator.py: run a queue of ASSAY player jobs unattended.

    tools/night_orchestrator.py --jobs tools/jobs_e1_e2.json [--max-concurrency 3]
        [--dry-run] [--player tools/run_player.sh] [--no-caffeinate]
        [--pause-minutes 30] [--max-sessions 3] [--poll-seconds 30]

Jobs file: a JSON list of {id, kind: e1|e2, game, arm: gated|ungated|null, seed,
run_dir, registry, constitution, prompt_template, cap, levels, anchor_dir?,
max_hours?}. Jobs launch in file order, at most --max-concurrency at a time,
each through the player script (tools/run_player.sh PROMPT RUN_DIR LEDGER) under
`caffeinate -dimsu` so the machine stays awake while anything runs.

Per job: <jobs dir>/ledgers/<id>.json (job, sessions as reported by claude -p:
model, usage, cost, exit code, start/end, plus session_count, outcome, pauses).
Queue: <jobs dir>/status.json (queued, running, done, failed, stopped, with
timestamps and the pause state), rewritten on every change.

Crash safety: when a session ends without WIN and the journal shows paid
actions below the cap, the same run dir is relaunched with the same prompt as a
new session, up to --max-sessions per job (resume is the kernel's `start`).
Rate limits: if the player's result signals a usage limit or 429, the whole
queue pauses for --pause-minutes, the session does not count, and the pause is
recorded. Stop file: <jobs dir>/STOP stops new launches and lets running jobs
finish. The flag file <archive>/runs/E1_E2_RUNNING exists while the queue runs.
--dry-run prints the exact launch commands and renders the prompts, nothing else.
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_FLAG = Path("/Users/mohsenarjmandi/workspace/assay-archive/runs/E1_E2_RUNNING")
RATE_LIMIT = re.compile(
    r"rate.?limit|usage limit|hit your limit|limit reached|out of (extra )?usage|"
    r"\b429\b|too many requests|overloaded|resets at|quota", re.I)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def journal_summary(run_dir: Path) -> dict | None:
    events = run_dir / ".assay" / "events.jsonl"
    if not events.exists():
        return None
    lines = [line for line in events.read_text().splitlines() if line.strip()]
    if not lines:
        return None
    paid = 0
    for line in lines:
        try:
            if json.loads(line).get("counts_action"):
                paid += 1
        except json.JSONDecodeError:
            continue
    last = json.loads(lines[-1])
    return {"events": len(lines), "paid": paid, "state": last.get("state"),
            "levels_completed": last.get("levels_completed"), "win_levels": last.get("win_levels")}


def outcome_of(job: dict, summary: dict | None) -> str | None:
    if not summary:
        return None
    if summary.get("state") == "WIN":
        return "WIN"
    if int(summary.get("paid") or 0) >= int(job["cap"]):
        return "CAP"
    return None


def render_prompt(job: dict, out: Path) -> Path:
    cmd = [sys.executable, str(REPO / "tools" / "render_prompt.py"), str(REPO / job["prompt_template"]),
           "--game", job["game"], "--cap", str(job["cap"]), "--levels", str(job["levels"]),
           "--run-dir", job["run_dir"], "--registry", str(REPO / job["registry"]),
           "--constitution", str(REPO / job["constitution"]), "--out", str(out)]
    subprocess.run(cmd, check=True)
    return out


def session_records(sessions_file: Path) -> list[dict]:
    if not sessions_file.exists():
        return []
    try:
        data = json.loads(sessions_file.read_text())
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def looks_rate_limited(record: dict | None) -> bool:
    if not record:
        return False
    blobs = [str(record.get("result_text") or ""), str(record.get("stderr_tail") or "")]
    claude = record.get("claude_json") or {}
    if isinstance(claude, dict):
        blobs.append(str(claude.get("api_error_status") or ""))
        blobs.append(str(claude.get("error") or ""))
        if claude.get("is_error") and claude.get("subtype"):
            blobs.append(str(claude.get("subtype")))
    text = "\n".join(blobs)
    return bool(RATE_LIMIT.search(text)) and (record.get("is_error") or record.get("exit_code") not in (0, None)
                                               or (record.get("num_turns") or 0) <= 2)


class Orchestrator:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.jobs_file = args.jobs.resolve()
        self.jobs_dir = self.jobs_file.parent if args.state_dir is None else args.state_dir.resolve()
        self.jobs = json.loads(self.jobs_file.read_text())
        ids = [job["id"] for job in self.jobs]
        if len(ids) != len(set(ids)):
            raise SystemExit("duplicate job ids")
        self.ledgers = self.jobs_dir / "ledgers"
        self.status_path = self.jobs_dir / "status.json"
        self.stop_file = self.jobs_dir / "STOP"
        self.flag = args.running_flag
        self.state = {job["id"]: {"state": "queued", "sessions": 0, "started": None, "ended": None,
                                  "outcome": None, "last_exit": None, "pauses": [], "journal": None}
                      for job in self.jobs}
        self.queue = [job["id"] for job in self.jobs]
        self.running: dict[str, dict] = {}
        self.paused_until: float | None = None
        self.pause_log: list[dict] = []
        self.stop_requested = False
        self.started = now()

    # ---- bookkeeping ------------------------------------------------------------------
    def job(self, job_id: str) -> dict:
        return next(job for job in self.jobs if job["id"] == job_id)

    def write_status(self, final: str | None = None) -> None:
        payload = {
            "updated": now(), "started": self.started, "jobs_file": str(self.jobs_file),
            "max_concurrency": self.args.max_concurrency, "max_sessions": self.args.max_sessions,
            "paused_until": dt.datetime.fromtimestamp(self.paused_until, dt.timezone.utc).isoformat(timespec="seconds")
            if self.paused_until else None,
            "pauses": self.pause_log, "stop_requested": self.stop_requested,
            "queued": [job_id for job_id in self.queue], "running": sorted(self.running),
            "done": [j for j, s in self.state.items() if s["state"] == "done"],
            "failed": [j for j, s in self.state.items() if s["state"] == "failed"],
            "stopped": [j for j, s in self.state.items() if s["state"] == "stopped"],
            "jobs": self.state, "final": final,
        }
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(self.status_path)

    def write_ledger(self, job_id: str) -> None:
        job = self.job(job_id)
        sessions = session_records(self.ledgers / f"{job_id}.sessions.json")
        record = {
            "job": job, "state": self.state[job_id],
            "session_count": len(sessions),
            "models_reported": sorted({str(s.get("model_reported")) for s in sessions if s.get("model_reported")}),
            "total_cost_usd_list_price": sum(float(s.get("total_cost_usd") or 0) for s in sessions),
            "sessions": sessions,
        }
        self.ledgers.mkdir(parents=True, exist_ok=True)
        (self.ledgers / f"{job_id}.json").write_text(json.dumps(record, indent=2) + "\n")

    # ---- launching ---------------------------------------------------------------------
    def command_for(self, job: dict, prompt: Path, sessions_file: Path) -> tuple[list[str], dict]:
        player = str(Path(self.args.player).resolve())
        cmd = [player, str(prompt), job["run_dir"], str(sessions_file), "--max-hours", str(job.get("max_hours", 4))]
        if self.args.caffeinate:
            cmd = ["caffeinate", "-dimsu"] + cmd
        env = dict(os.environ)
        if job.get("anchor_dir"):
            env["ASSAY_ANCHOR_DIR"] = job["anchor_dir"]
        env.setdefault("CLAUDE_MODEL", self.args.model)
        return cmd, env

    def launch(self, job_id: str) -> None:
        job = self.job(job_id)
        session = self.state[job_id]["sessions"] + 1
        self.ledgers.mkdir(parents=True, exist_ok=True)
        prompt = render_prompt(job, self.ledgers / f"{job_id}.s{session}.prompt.md")
        sessions_file = self.ledgers / f"{job_id}.sessions.json"
        cmd, env = self.command_for(job, prompt, sessions_file)
        if job.get("anchor_dir"):
            Path(job["anchor_dir"]).mkdir(parents=True, exist_ok=True)
        Path(job["run_dir"]).mkdir(parents=True, exist_ok=True)
        log = (self.ledgers / f"{job_id}.s{session}.orchestrator.log").open("ab")
        proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=log, start_new_session=True)
        self.running[job_id] = {"proc": proc, "session": session, "started": now(), "log": log}
        state = self.state[job_id]
        state.update({"state": "running", "sessions": session, "started": state["started"] or now(),
                      "pid": proc.pid, "session_started": now()})
        print(f"[{now()}] launched {job_id} session {session} pid {proc.pid}", flush=True)

    def finish(self, job_id: str, proc_info: dict) -> None:
        proc = proc_info["proc"]
        proc_info["log"].close()
        code = proc.returncode
        job = self.job(job_id)
        state = self.state[job_id]
        state["last_exit"] = code
        sessions = session_records(self.ledgers / f"{job_id}.sessions.json")
        last = sessions[-1] if sessions else None
        summary = journal_summary(Path(job["run_dir"]))
        state["journal"] = summary
        if looks_rate_limited(last):
            resume_at = time.time() + self.args.pause_minutes * 60
            self.paused_until = resume_at
            entry = {"job": job_id, "at": now(), "minutes": self.args.pause_minutes,
                     "reason": (last or {}).get("result_text", "")[:200] if last else "rate limit"}
            self.pause_log.append(entry)
            state["pauses"].append(entry)
            state["sessions"] -= 1  # a rate-limited launch is not a session
            state["state"] = "queued"
            self.queue.insert(0, job_id)
            print(f"[{now()}] {job_id}: rate limit signalled; queue paused {self.args.pause_minutes} min", flush=True)
        else:
            result = outcome_of(job, summary)
            if result:
                state.update({"state": "done", "outcome": result, "ended": now()})
                print(f"[{now()}] {job_id}: done ({result}) after {state['sessions']} session(s)", flush=True)
            elif state["sessions"] < self.args.max_sessions and not self.stop_requested:
                state["state"] = "queued"
                self.queue.insert(0, job_id)
                print(f"[{now()}] {job_id}: session {state['sessions']} ended without WIN/cap "
                      f"(exit {code}); relaunch queued", flush=True)
            else:
                state.update({"state": "failed", "outcome": "SESSIONS_EXHAUSTED" if not self.stop_requested else "STOPPED",
                              "ended": now()})
                print(f"[{now()}] {job_id}: failed after {state['sessions']} session(s) (exit {code})", flush=True)
        self.write_ledger(job_id)

    # ---- main loop ---------------------------------------------------------------------
    def dry_run(self) -> int:
        out_dir = self.jobs_dir / "dryrun"
        out_dir.mkdir(parents=True, exist_ok=True)
        for job in self.jobs:
            prompt = render_prompt(job, out_dir / f"{job['id']}.s1.prompt.md")
            cmd, env = self.command_for(job, prompt, out_dir / f"{job['id']}.sessions.json")
            extra = {k: env[k] for k in ("ASSAY_ANCHOR_DIR", "CLAUDE_MODEL") if k in env}
            print(f"{job['id']}: " + " ".join(f"{k}={v}" for k, v in extra.items()))
            print("    " + " ".join(cmd))
        print(f"{len(self.jobs)} jobs, max concurrency {self.args.max_concurrency}, "
              f"prompts rendered under {out_dir}")
        return 0

    def run(self) -> int:
        if self.args.dry_run:
            return self.dry_run()
        self.flag.parent.mkdir(parents=True, exist_ok=True)
        self.flag.write_text(json.dumps({"pid": os.getpid(), "started": self.started,
                                         "jobs": str(self.jobs_file), "status": str(self.status_path)}) + "\n")
        self.write_status()

        def on_signal(signum, _frame):
            self.stop_requested = True
            self.write_status(final=f"signal {signum}: no new launches")
        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
        try:
            while self.queue or self.running:
                for job_id, info in list(self.running.items()):
                    if info["proc"].poll() is not None:
                        del self.running[job_id]
                        self.finish(job_id, info)
                        self.write_status()
                if self.stop_file.exists() and not self.stop_requested:
                    self.stop_requested = True
                    print(f"[{now()}] STOP file seen: no new launches", flush=True)
                if self.stop_requested:
                    for job_id in list(self.queue):
                        self.state[job_id]["state"] = "stopped"
                    self.queue.clear()
                paused = self.paused_until is not None and time.time() < self.paused_until
                if self.paused_until is not None and not paused:
                    self.paused_until = None
                while (self.queue and len(self.running) < self.args.max_concurrency
                       and not paused and not self.stop_requested):
                    self.launch(self.queue.pop(0))
                    self.write_status()
                if not self.queue and not self.running:
                    break
                time.sleep(self.args.poll_seconds)
        finally:
            final = "stopped" if self.stop_requested else "drained"
            self.write_status(final=final)
            if self.flag.exists():
                self.flag.unlink()
        print(f"[{now()}] queue {final}", flush=True)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=Path, default=REPO / "tools" / "jobs_e1_e2.json")
    parser.add_argument("--max-concurrency", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--player", default=str(REPO / "tools" / "run_player.sh"))
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--no-caffeinate", dest="caffeinate", action="store_false")
    parser.add_argument("--pause-minutes", type=float, default=30.0)
    parser.add_argument("--max-sessions", type=int, default=3)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--state-dir", type=Path, help="where ledgers/, status.json and STOP live (default: the jobs file's dir)")
    parser.add_argument("--running-flag", type=Path, default=DEFAULT_FLAG)
    args = parser.parse_args(argv)
    if shutil.which("caffeinate") is None and args.caffeinate and not args.dry_run:
        print("caffeinate not found; pass --no-caffeinate", file=sys.stderr)
        return 2
    return Orchestrator(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
