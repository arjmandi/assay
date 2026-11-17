"""The session mode's stall trigger end to end: a scripted player against
the counter example through the real launcher, CLI and daemon, writing a
flat frontier; the rule fires, the session ends STALL, a scripted analyst's
directions reach the next prompt, and a second runner on the same state
directory continues the rule. Also the flags refused before anything runs,
the note without an analyst, and a host without a dollar flag held to
--max-usd by its own JSON lines. Nothing here starts a model."""

from __future__ import annotations

import importlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from conftest import stop_run

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
session = importlib.import_module("tools.eval.session")
frontier = importlib.import_module("tools.eval.frontier")
supervisor = importlib.import_module("tools.eval.supervisor")
cli = importlib.import_module("tools.eval.cli")

COUNTER_WORLD = REPO / "examples" / "counter_world.py"
COUNTER_REGISTRY = REPO / "examples" / "example_registry.json"
LAUNCHER = REPO / "bin" / "assay"

PLAYER = """
import datetime as dt, json, os, shlex, subprocess, sys, time
from pathlib import Path
frontier = Path(sys.argv[1])
subprocess.run([*shlex.split(os.environ["ASSAY"]), "act", "NOOP", "--predict", "change"], capture_output=True, timeout=120)
for _ in range(300):
    n = len(frontier.read_text().splitlines()) + 1 if frontier.exists() else 1
    at = dt.datetime.now(dt.timezone.utc).isoformat()
    record = {"n": n, "started_at": at, "at": at, "duration_seconds": 0.0, "label": f"try {n}", "code_sha256": None,
              "valid": True, "cycles": 1000, "failure_stage": None, "error": None, "frontier_before": 1000,
              "improved": False, "best_cycles": 1000, "trials": 1}
    with frontier.open("a") as handle:
        handle.write(json.dumps(record) + "\\n")
    time.sleep(0.1)
"""

ANALYST = """
import json, sys
prompt = open(sys.argv[1]).read()
earlier = prompt.count('"call":')
answer = {"directions": [{"rank": 1, "try": f"vectorize the inner loop ({earlier} failed before)",
                          "why": "every evaluation since the anchor read 1000 cycles", "layer": 4}]}
print(json.dumps({"type": "result", "result": "```json\\n" + json.dumps(answer) + "\\n```", "total_cost_usd": 0.25}))
"""


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in ("ASSAY_OWNER_TOKEN_FILE", "ASSAY_STOP_GATE")}
    return {**env, "ASSAY_PYTHON": sys.executable}


def _start(run: Path, token: Path) -> None:
    run.mkdir(parents=True)
    started = subprocess.run(
        [str(LAUNCHER), "--run-dir", str(run), "start", "counterdemo", "--adapter", f"{COUNTER_WORLD}:factory",
         "--registry", str(COUNTER_REGISTRY), "--owner-token-file", str(token)],
        capture_output=True, text=True, env=_env(), timeout=180,
    )
    assert started.returncode == 0, started.stdout + started.stderr
    (run / "PLAYER.md").write_text("Improve the program.\n")


def _session(run: Path, *flags: str, command: list[str]) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-m", "tools.eval", "session", "--run-dir", str(run), "--assay", str(LAUNCHER),
            "--poll-seconds", "0.1", *flags, "--", *command]
    return subprocess.run(argv, cwd=REPO, env=_env(), capture_output=True, text=True, timeout=240)


def test_the_stall_ends_the_session_and_the_analyst_steers_the_next(tmp_path):
    tmp_path = tmp_path.resolve()
    run, token = tmp_path / "run", tmp_path / "run.token"
    _start(run, token)
    player, analyst_script, flat = tmp_path / "player.py", tmp_path / "analyst.py", tmp_path / "frontier.jsonl"
    player.write_text(PLAYER)
    analyst_script.write_text(ANALYST)
    state = run.with_name("run.sessions")
    flags = ["--stall", "--frontier", str(flat), "--stall-min-age-minutes", "0", "--stall-flat-minutes", "0.005",
             "--stall-min-attempts", "3",
             "--analyst-command", f"{shlex.quote(sys.executable)} {shlex.quote(str(analyst_script))} {{prompt_file}}"]
    try:
        first = _session(run, *flags, "--max-sessions", "2", command=["{python}", str(player), str(flat)])
        out = first.stdout
        assert first.returncode == 0, out + first.stderr
        assert "TRIGGER | the stall rule: 3 attempts and 0.005 active min with no gain over 0.2 percent, after 0 min | " \
               "analyst analyst.py " in out
        assert "STALL | session 1 | evaluation " in out and "| anchor 1000 cycles | " in out
        assert out.count("| STALL\n") == 2
        assert "ANALYST | call 1 | 1 direction(s) | " in out and "| $0.25" in out
        assert "RELAUNCH | session 1 ended" in out
        assert out.rstrip().endswith("the daemon is left up; the same command resumes the run")
        assert "OUTCOME | SESSIONS_EXHAUSTED | 2 session(s)" in out
        ledger = json.loads((state / "sessions.json").read_text())
        assert [(record["ended_because"], record.get("stalled")) for record in ledger] == [("STALL", True), ("STALL", True)]
        record = json.loads((state / "analyst" / "a1.json").read_text())
        assert record["directions"][0]["try"] == "vectorize the inner loop (0 failed before)"
        assert record["dollars"] == 0.25 and record["reason"] is None
        assert (state / "analyst" / "a1.input.json").is_file() and (state / "analyst" / "a1.prompt.md").is_file()
        assert supervisor.DIRECTIONS_HEADER not in (state / "s1.prompt.md").read_text()
        steered = (state / "s2.prompt.md").read_text()
        assert steered.startswith("Improve the program.\n\n" + supervisor.DIRECTIONS_HEADER + "\n")
        assert "1. Try: vectorize the inner loop (0 failed before)\n" in steered
        assert "   Why: every evaluation since the anchor read 1000 cycles\n   Layer 4: the addressable states\n" in steered
        saved = frontier.load_state(state / "stall.json")
        assert len(saved.interventions) == 2 and saved.interventions[1].advice is None
        assert not frontier.has_open(saved) and len(saved.sessions) == 2

        second = _session(run, *flags, "--max-sessions", "1", command=["{python}", str(player), str(flat)])
        assert second.returncode == 0, second.stdout + second.stderr
        # The second runner answers the intervention the first left, with the first call's directions as failed.
        assert "ANALYST | call 2 | 1 direction(s) | " in second.stdout and "STALL | session 3 | " in second.stdout
        assert "vectorize the inner loop (1 failed before)" in (state / "s3.prompt.md").read_text()
        continued = frontier.load_state(state / "stall.json")
        assert continued.run_started_at == saved.run_started_at and continued.anchor_cycles == 1000
        assert len(continued.interventions) == 3 and len(continued.sessions) == 3
    finally:
        stop_run(run)


def test_the_stall_flags_are_refused_before_anything_runs(tmp_path, capsys):
    tmp_path = tmp_path.resolve()
    run = tmp_path / "run"
    (run / ".assay").mkdir(parents=True)
    (run / ".assay" / "config.json").write_text(json.dumps({"game_id": "counterdemo"}))
    base = ["session", "--run-dir", str(run)]
    refusals = [
        (["--analyst", "claude"], "--analyst needs --stall"),
        (["--stall-flat-minutes", "5"], "--stall-flat-minutes needs --stall"),
        (["--stall", "--analyst", "claude", "--analyst-command", "x {prompt}"], "--analyst and --analyst-command exclude each other"),
        (["--stall", "--analyst-model", "opus"], "--analyst-model needs --analyst or --analyst-command"),
        (["--stall", "--stall-min-attempts", "0"], "--stall-min-attempts must be at least 1"),
        (["--stall", "--stall-gain", "1.5"], "--stall-gain must be at least 0 and below 1"),
        (["--stall", "--analyst-command", "x {run_dir}"], "--analyst-command names {run_dir}; it knows {prompt}, {prompt_file}, {model}"),
    ]
    for flags, said in refusals:
        assert cli.main([*base, *flags]) == 2
        assert capsys.readouterr().err.strip() == f"ERROR | {said}"


def test_without_an_analyst_the_prompt_says_the_frontier_is_flat(tmp_path):
    rule = frontier.StallRule(min_run_age_seconds=0, flat_seconds=60, min_attempts=3)
    clock = iter([1000.0, 1200.0, 1300.0])
    watcher = supervisor.Supervisor(run_dir=tmp_path, state_dir=tmp_path / "state", frontier=tmp_path / "f.jsonl",
                                    rule=rule, clock=lambda: next(clock))
    watcher.launched()
    rows = [{"n": n, "at": frontier.iso(1000.0 + 50 * n), "valid": True, "cycles": 500, "best_cycles": 500} for n in range(1, 5)]
    (tmp_path / "f.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert watcher.note() is None
    assert watcher.poll(1) == "STALL | session 1 | evaluation 4 | anchor 500 cycles | 2.5 active min | 4 attempts since the anchor"
    assert watcher.awaiting() is None  # no analyst: nothing waits for advice
    assert watcher.note() == (
        "A note from the session runner (it is not from the world): the best score has not improved by more than "
        "0.2 percent over the last 4 evaluations and 2 active minutes."
    )
    assert frontier.load_state(tmp_path / "state" / "stall.json").phase == "intervening"


def test_a_killed_runners_open_session_is_closed_at_the_state_files_last_write(tmp_path):
    path = tmp_path / "stall.json"
    frontier.save_state(path, frontier.begin(1000.0, []))
    os.utime(path, (1500.0, 1500.0))
    watcher = supervisor.Supervisor(run_dir=tmp_path, state_dir=tmp_path, frontier=tmp_path / "f.jsonl",
                                    rule=frontier.StallRule())
    watcher.load()
    assert watcher.state.sessions == ((1000.0, 1500.0),)
    assert frontier.load_state(path).sessions == ((1000.0, 1500.0),)


def test_a_host_without_a_dollar_flag_is_held_to_max_usd_by_its_json_lines(tmp_path, monkeypatch, capsys):
    tmp_path = tmp_path.resolve()
    run, token = tmp_path / "run", tmp_path / "run.token"
    _start(run, token)
    step = {"type": "step_finish", "part": {"type": "step-finish", "cost": 0.03,
                                            "tokens": {"input": 100, "output": 10, "reasoning": 5, "cache": {"read": 50, "write": 0}}}}
    code = f"import time\nfor _ in range(600):\n    print({json.dumps(step)!r}, flush=True)\n    time.sleep(0.05)\n"
    monkeypatch.setattr(session, "SPEND_CHECK_SECONDS", 0.3)
    for key, value in _env().items():
        monkeypatch.setenv(key, value)
    runner = session.SessionRunner(run, host="claude", command=[sys.executable, "-c", code], launcher=[str(LAUNCHER)],
                                   max_usd=0.1, poll_seconds=0.05)
    try:
        assert runner.run() == 0
    finally:
        stop_run(run)
    out = capsys.readouterr().out
    assert "| USD_EXHAUSTED\n" in out and "OUTCOME | USD_EXHAUSTED | 1 session(s)" in out
    (record,) = json.loads(run.with_name("run.sessions").joinpath("sessions.json").read_text())
    assert record["usd_exhausted"] is True and record["dollars"] >= 0.1
    steps = round(record["dollars"] / 0.03)
    assert record["tokens"] == {"input": 100 * steps, "output": 10 * steps, "reasoning": 5 * steps,
                                "cache_read": 50 * steps, "cache_creation": 0, "total": 165 * steps}
