"""tools/night_orchestrator.py driven with tools/fake_player.sh (exits at once):
dry run prints commands only; the live loop writes per-job ledgers and
status.json, relaunches a stalled run up to --max-sessions, pauses the queue on
a rate-limit signal, honours the STOP file, and manages the RUNNING flag."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ORCH = REPO / "tools" / "night_orchestrator.py"
FAKE = REPO / "tools" / "fake_player.sh"


def _job(tmp: Path, job_id: str, kind: str = "e1") -> dict:
    return {"id": job_id, "kind": kind, "game": "ft09", "arm": "gated", "seed": 1,
            "run_dir": str(tmp / "runs" / job_id), "registry": "bench/arcagi/registry_e1_gated_200.json",
            "constitution": "CONSTITUTION.md", "prompt_template": "tools/prompts/e1_player.md",
            "cap": 5, "levels": 6, "anchor_dir": str(tmp / "anchors"), "max_hours": 1}


def _run(tmp: Path, jobs: list[dict], *extra: str, env: dict | None = None):
    jobs_file = tmp / "jobs.json"
    jobs_file.write_text(json.dumps(jobs))
    flag = tmp / "RUNNING"
    cmd = [sys.executable, str(ORCH), "--jobs", str(jobs_file), "--player", str(FAKE), "--no-caffeinate",
           "--running-flag", str(flag), "--poll-seconds", "0.2", "--pause-minutes", "0.02", *extra]
    full_env = {**os.environ, **(env or {})}
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=full_env)
    return done, jobs_file.parent, flag


def test_dry_run_prints_commands_and_launches_nothing(tmp_path):
    done, state_dir, flag = _run(tmp_path, [_job(tmp_path, "j1"), _job(tmp_path, "j2")], "--dry-run")
    assert done.returncode == 0, done.stderr
    assert "j1:" in done.stdout and "fake_player.sh" in done.stdout and "ASSAY_ANCHOR_DIR=" in done.stdout
    assert "2 jobs, max concurrency 3" in done.stdout
    assert (state_dir / "dryrun" / "j1.s1.prompt.md").exists()
    assert "ft09" in (state_dir / "dryrun" / "j1.s1.prompt.md").read_text()
    assert not (state_dir / "status.json").exists()
    assert not (tmp_path / "runs" / "j1" / ".assay").exists()
    assert not flag.exists()


def test_real_dispatch_order_matches_the_jobs_file():
    jobs = json.loads((REPO / "tools" / "jobs_e1_e2.json").read_text())
    assert [j["id"] for j in jobs] == [
        "e1-ft09-gated-s1", "e1-ft09-ungated-s1", "e2-s5i5-resume", "e2-wa30-resume",
        "e1-tr87-gated-s1", "e1-tr87-ungated-s1", "e1-cn04-gated-s1", "e1-cn04-ungated-s1",
        "e2-sk48-resume", "e2-dc22-resume", "e2-bp35-resume",
        "e1-ft09-gated-s2", "e1-ft09-ungated-s2", "e1-tr87-gated-s2", "e1-tr87-ungated-s2",
        "e1-cn04-gated-s2", "e1-cn04-ungated-s2"]
    for job in jobs:
        assert (REPO / job["registry"]).exists() and (REPO / job["constitution"]).exists()
        assert (REPO / job["prompt_template"]).exists()
        if job["kind"] == "e2":
            assert job["run_dir"].endswith(f"/e2/{job['game']}-resume") and job["cap"] == 1500
        else:
            assert job["run_dir"].endswith(f"/e1/{job['game']}-{job['arm']}-s{job['seed']}")


def test_live_loop_done_relaunch_and_ledgers(tmp_path):
    win = _job(tmp_path, "win")
    stall = _job(tmp_path, "stall")
    done, state_dir, flag = _run(tmp_path, [win, stall], "--max-concurrency", "1", "--max-sessions", "2",
                                 env={"FAKE_PLAYER_MODE": "win"})
    assert done.returncode == 0, done.stderr + done.stdout
    status = json.loads((state_dir / "status.json").read_text())
    assert status["final"] == "drained" and not flag.exists()
    assert status["jobs"]["win"]["state"] == "done" and status["jobs"]["win"]["outcome"] == "WIN"
    assert status["jobs"]["win"]["sessions"] == 1
    ledger = json.loads((state_dir / "ledgers" / "win.json").read_text())
    assert ledger["session_count"] == 1 and ledger["models_reported"] == ["fake-model"]
    assert ledger["sessions"][0]["exit_code"] == 0 and "started" in ledger["sessions"][0]


def test_stalled_job_is_relaunched_up_to_max_sessions(tmp_path):
    done, state_dir, flag = _run(tmp_path, [_job(tmp_path, "stall")], "--max-sessions", "3",
                                 env={"FAKE_PLAYER_MODE": "stall"})
    assert done.returncode == 0, done.stderr + done.stdout
    status = json.loads((state_dir / "status.json").read_text())
    job = status["jobs"]["stall"]
    assert job["state"] == "failed" and job["outcome"] == "SESSIONS_EXHAUSTED" and job["sessions"] == 3
    assert job["journal"]["paid"] == 3 and job["journal"]["state"] == "NOT_FINISHED"
    ledger = json.loads((state_dir / "ledgers" / "stall.json").read_text())
    assert ledger["session_count"] == 3
    assert (state_dir / "ledgers" / "stall.s3.prompt.md").exists()


def test_rate_limit_pauses_queue_and_does_not_count_a_session(tmp_path):
    done, state_dir, flag = _run(tmp_path, [_job(tmp_path, "rl")], env={"FAKE_PLAYER_MODE": "ratelimit-once"})
    assert done.returncode == 0, done.stderr + done.stdout
    status = json.loads((state_dir / "status.json").read_text())
    job = status["jobs"]["rl"]
    assert job["state"] == "done" and job["outcome"] == "WIN"
    assert job["sessions"] == 1 and len(job["pauses"]) == 1
    assert status["pauses"][0]["job"] == "rl" and "limit" in status["pauses"][0]["reason"].lower()
    assert "queue paused" in done.stdout


def test_stop_file_prevents_new_launches(tmp_path):
    jobs = [_job(tmp_path, f"j{i}") for i in range(3)]
    (tmp_path / "STOP").write_text("")
    done, state_dir, flag = _run(tmp_path, jobs, env={"FAKE_PLAYER_MODE": "win"})
    assert done.returncode == 0, done.stderr + done.stdout
    status = json.loads((state_dir / "status.json").read_text())
    assert status["final"] == "stopped" and status["stopped"] == ["j0", "j1", "j2"]
    assert all(s["state"] == "stopped" for s in status["jobs"].values())
    assert not flag.exists()
