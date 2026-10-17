"""The session mode of tools/eval and the Stop gate it installs.

The gate's rules (hooks/stop_gate.py) on fixture run directories: WIN, the
cap, the dollar cap, a pending owner action and a fresh declaration allow
the stop; anything else blocks it, until three blocks with no paid action
between them. As a hook process it is inert without ASSAY_STOP_GATE. The
runner's pieces: the host templates, the player's environment without the
world's secrets, the gate merged into the settings beside the kernel's
hooks, the operator's note. End to end, the fake player against the counter
example through the real launcher, CLI and daemon: relaunch to WIN, a pause
at an approval and the resume after it, a declared stop, the STOP file, a
failing player, a usage-limit pause. Nothing here starts a model."""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import stop_run

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
eval_package = importlib.import_module("tools.eval")
session = importlib.import_module("tools.eval.session")
cli = importlib.import_module("tools.eval.cli")
EvalError = eval_package.EvalError
gate = session.load_gate()

COUNTER_WORLD = REPO / "examples" / "counter_world.py"
COUNTER_REGISTRY = REPO / "examples" / "example_registry.json"
LAUNCHER = REPO / "bin" / "assay"
FAKE_PLAYER = ["{python}", "{repo}/tools/eval/fake_player"]
GATE_SCRIPT = REPO / "hooks" / "stop_gate.py"


def _stamp(seconds_ago: float = 0.0) -> str:
    moment = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)
    return moment.isoformat()


def _fixture_run(
    root: Path,
    *,
    paid: int = 0,
    state: str = "NOT_FINISHED",
    budget: dict | None = None,
    activity: list[dict] | None = None,
    notes: str | None = None,
) -> Path:
    """A run directory as the gate reads it: the journal, the pinned
    registry, the activity log and the notes."""
    run = root.resolve() / "run"
    store = run / ".assay"
    store.mkdir(parents=True, exist_ok=True)
    events = [{"id": 0, "action": "START", "counts_action": False, "state": "NOT_FINISHED"}]
    for index in range(1, paid + 1):
        events.append({"id": index, "action": "INC", "counts_action": True, "state": state if index == paid else "NOT_FINISHED"})
    if paid == 0:
        events[0]["state"] = state
    (store / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    (store / "registry.json").write_text(json.dumps({"actions": [], "budget": budget if budget is not None else {"actions": 10}}))
    (store / "activity.jsonl").write_text("".join(json.dumps(record) + "\n" for record in activity or []))
    if notes is not None:
        (store / "NOTES.md").write_text(notes)
    return run


def _refusal(code: str, error: str) -> dict:
    return {"kind": "command_end", "command": "act", "status": "ERROR", "code": code, "error": error, "error_kind": "refused"}


APPROVAL = _refusal("APPROVAL_REQUIRED", "DRILL is approval-gated (default-deny) and has no fresh approval")
EXPIRED = _refusal("APPROVAL_REQUIRED", "drill's approval expired after 600s (default-deny with timeout)")
REHEARSAL = _refusal("REHEARSAL_REQUIRED", "LAUNCH is a LIVE actuator with a rehearsal quota of 3 that is not met")


# ---- the gate's rules ---------------------------------------------------------------------------

def test_pending_owner_actions_name_the_action_and_follow_the_answers():
    assert gate.pending_owner_actions([APPROVAL]) == [{
        "action": "DRILL", "code": "APPROVAL_REQUIRED", "owner": "approve",
        "error": "DRILL is approval-gated (default-deny) and has no fresh approval",
    }]
    granted = {"kind": "approval_granted", "action": "DRILL"}
    used = {"kind": "approval_used", "action": "DRILL"}
    assert gate.pending_owner_actions([APPROVAL, granted]) == []
    # The next use needs the next grant; an expired grant names the action in lower case.
    assert [item["action"] for item in gate.pending_owner_actions([APPROVAL, granted, used, EXPIRED])] == ["DRILL"]
    # A grant of another action answers nothing; a waiver answers the rehearsal.
    assert len(gate.pending_owner_actions([APPROVAL, {"kind": "approval_granted", "action": "PAY"}])) == 1
    waived = {"kind": "liveness_waived", "action": "LAUNCH", "because": "rehearsed"}
    assert gate.pending_owner_actions([REHEARSAL])[0]["owner"] == "waive"
    assert gate.pending_owner_actions([REHEARSAL, waived]) == []
    # A refusal that does not lead with its action is answered by any record of its kind.
    unnamed = _refusal("APPROVAL_REQUIRED", "(an approval is needed)")
    assert gate.pending_owner_actions([unnamed])[0]["action"] == ""
    assert gate.pending_owner_actions([unnamed, granted]) == []
    # Refusals before `since` belong to an earlier session; another code never counts.
    assert gate.pending_owner_actions([APPROVAL, {"kind": "status"}], since=1) == []
    assert gate.pending_owner_actions([_refusal("BUDGET_EXHAUSTED", "DRILL is refused")]) == []


def test_declarations_are_stop_lines_with_a_reason():
    notes = "# Notes\nSTOP: the door has no code\n  STOP:   the budget cannot reach level 3  \nSTOP:\n- STOP: not at the start\n"
    assert gate.declarations(notes) == ["the door has no code", "the budget cannot reach level 3"]
    assert gate.declarations("") == []


def test_the_gate_allows_only_for_a_reason_and_releases_after_three_blocks(tmp_path):
    def decide(run, state=None, session_id="s1"):
        state = {} if state is None else state
        return gate.decide(gate.load_view(run), state, session_id), state

    assert decide(_fixture_run(tmp_path / "w", paid=3, state="WIN"))[0] == (True, "the run reached WIN")
    assert decide(_fixture_run(tmp_path / "c", paid=10))[0] == (True, "the paid actions reached the registry's cap")
    spend = [{"kind": "spend_report", "id": "a", "usd": 1.5}, {"kind": "spend_report", "id": "a", "usd": 2.5}]
    dollars = _fixture_run(tmp_path / "u", paid=1, budget={"actions": 10, "usd": 2.0}, activity=spend)
    assert gate.load_view(dollars).usd == 2.5
    assert decide(dollars)[0] == (True, "the reported spend reached the registry's dollar cap")
    allow, why = decide(_fixture_run(tmp_path / "o", paid=1, activity=[APPROVAL]))[0]
    assert allow and why == "the owner must act: APPROVAL_REQUIRED for DRILL (the owner runs `assay approve`)"
    # A declaration counts only when it is new since the launch.
    declared = _fixture_run(tmp_path / "d", paid=1, notes="STOP: the counter is broken\n")
    assert decide(declared)[0] == (True, "the player declared a stop: the counter is broken")
    assert decide(declared, {"launch": {"declarations": 1}})[0][0] is False
    # So does a refusal: one before the launch is an earlier session's.
    assert decide(_fixture_run(tmp_path / "e", paid=1, activity=[APPROVAL]), {"launch": {"activity": 1}})[0][0] is False

    run = _fixture_run(tmp_path / "b", paid=2)
    state: dict = {}
    for _ in range(3):
        (allow, why), state = decide(run, state)
        assert allow is False
    assert why == (
        "ASSAY stop gate: the run is not finished. WIN is not reached, 8 of 10 paid actions are left, "
        'and no owner action is pending. Continue from `"$ASSAY" status` and .assay/NOTES.md. If you '
        "judge the goal unreachable with what is left, or you need something only the owner can give, "
        "add one line to .assay/NOTES.md that begins with `STOP:` and says why, then stop."
    )
    assert state["blocks"] == 3 and state["paid_at_last_block"] == 2
    (allow, why), state = decide(run, state)
    assert allow and why == "released after 3 blocks with no paid action between them"
    # A paid action between blocks starts the count again, and so does another session.
    state = {"blocks": 3, "paid_at_last_block": 1, "session_id": "s1"}
    assert decide(run, state)[0][0] is False and state["blocks"] == 1
    state = {"blocks": 3, "paid_at_last_block": 2, "session_id": "s1"}
    assert decide(run, state, "s2")[0][0] is False and state["blocks"] == 1
    uncapped = _fixture_run(tmp_path / "n", paid=2, budget={})
    assert "no action cap is registered" in decide(uncapped)[0][1]


def _hook(run: Path, payload: object, state_file: Path | None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "ASSAY_STOP_GATE"}
    if state_file is not None:
        env["ASSAY_STOP_GATE"] = str(state_file)
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--run-dir", str(run)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60,
    )


def test_the_gate_as_a_hook_process(tmp_path):
    run = _fixture_run(tmp_path, paid=1)
    event = {"session_id": "abc", "hook_event_name": "Stop", "stop_hook_active": False}
    # Inert without the variable: a person's own session is never held.
    inert = _hook(run, event, None)
    assert (inert.returncode, inert.stdout, inert.stderr) == (0, "", "")
    state_file = tmp_path / "gate" / "stop_gate.json"
    blocked = _hook(run, event, state_file)
    assert blocked.returncode == 0 and blocked.stderr == ""
    decision = json.loads(blocked.stdout)
    assert decision["decision"] == "block" and decision["reason"].startswith("ASSAY stop gate: the run is not finished.")
    recorded = json.loads(state_file.read_text())
    assert recorded["session_id"] == "abc" and recorded["blocks"] == 1
    assert [(item["allow"], item["paid"]) for item in recorded["decisions"]] == [(False, 1)]
    (run / ".assay" / "NOTES.md").write_text("STOP: nothing left to try\n")
    allowed = _hook(run, event, state_file)
    assert (allowed.returncode, allowed.stdout) == (0, "")
    assert json.loads(state_file.read_text())["decisions"][-1]["why"] == "the player declared a stop: nothing left to try"
    # A run it cannot read, or input that is not JSON, is never held.
    assert _hook(tmp_path / "nowhere", event, state_file).stdout == ""
    garbled = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--run-dir", str(_fixture_run(tmp_path / "g", paid=1))],
        input="not json", capture_output=True, text=True, timeout=60,
        env={**os.environ, "ASSAY_STOP_GATE": str(tmp_path / "g.json")},
    )
    assert garbled.returncode == 0 and json.loads(garbled.stdout)["decision"] == "block"


# ---- the runner's pieces --------------------------------------------------------------------------

def _runner(run: Path, **options) -> object:
    options.setdefault("host", "claude")
    options.setdefault("launcher", [str(LAUNCHER)])
    return session.SessionRunner(run, **options)


def test_the_host_templates(tmp_path):
    run = _fixture_run(tmp_path, paid=0)
    claude = _runner(run, model="haiku")
    argv = claude.player_argv(2, "PROMPT", 1.5)
    # The prompt follows -p; the options come before the variadic tool lists.
    assert argv[:7] == ["claude", "-p", "PROMPT", "--model", "haiku", "--max-budget-usd", "1.50"]
    assert argv[-5:] == ["--disallowedTools", "WebFetch", "WebSearch", "Agent", "--strict-mcp-config"]
    assert _runner(run).player_argv(1, "PROMPT", None)[:4] == ["claude", "-p", "PROMPT", "--output-format"]
    assert claude.player_text(2, None).startswith(f'claude -p "$(cat {run.parent / "run.sessions" / "s2.prompt.md"})" --model haiku ')
    codex = _runner(run, host="codex", model="gpt-x").player_argv(1, "PROMPT", 3.0)
    assert codex[:2] == ["codex", "exec"] and codex[-3:] == ["--model", "gpt-x", "PROMPT"]
    opencode = _runner(run, host="opencode").player_argv(1, "PROMPT", None)
    assert opencode == ["opencode", "run", "--format", "json", "--auto", "PROMPT"]
    custom = _runner(run, command=[*FAKE_PLAYER, "{run_dir}", "{session}"])
    assert custom.host_name == "fake_player" and custom.gate_wanted is False
    assert custom.player_argv(4, "PROMPT", 2.0) == [sys.executable, f"{REPO}/tools/eval/fake_player", str(run), "4"]
    assert custom.player_text(4, None).endswith(f"< {run.parent / 'run.sessions' / 's4.prompt.md'}")
    assert _runner(run, command=["{python}", "-c", "pass"]).host_name == Path(sys.executable).name
    with pytest.raises(EvalError, match="--player gemini is not one of claude, codex, opencode"):
        _runner(run, host="gemini")


def test_a_host_that_does_not_run_is_refused(tmp_path, monkeypatch):
    run = _fixture_run(tmp_path, paid=0)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    broken = bin_dir / "codex"
    broken.write_text("#!/bin/sh\necho 'Error: spawn codex ENOENT' >&2\nexit 1\n")
    broken.chmod(0o755)
    working = bin_dir / "claude"
    working.write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n")
    working.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(EvalError, match=r"the host does not run: `codex --version` exited 1: Error: spawn codex ENOENT"):
        _runner(run, host="codex").host_version()
    with pytest.raises(EvalError, match="the host `opencode` is not on PATH"):
        _runner(run, host="opencode").host_version()
    assert _runner(run).host_version() == "9.9.9 (Claude Code)"


def test_the_player_environment_keeps_the_world_secrets_out(tmp_path, monkeypatch):
    run = _fixture_run(tmp_path, paid=0)
    registry = json.loads((run / ".assay" / "registry.json").read_text())
    (run / ".assay" / "registry.json").write_text(json.dumps({**registry, "secrets": ["CRM_TOKEN", "UNSET_ONE"]}))
    monkeypatch.setenv("CRM_TOKEN", "hush")
    monkeypatch.setenv("ASSAY_OWNER_TOKEN_FILE", "/tokens/run.token")
    monkeypatch.setenv("EXTRA_KEY", "x")
    monkeypatch.setenv("ASSAY_STOP_GATE", "/elsewhere.json")
    runner = _runner(run, strip=["EXTRA_KEY"])
    env, stripped = runner.player_environment()
    assert stripped == ["ASSAY_OWNER_TOKEN_FILE", "CRM_TOKEN", "EXTRA_KEY"]
    assert not {"CRM_TOKEN", "ASSAY_OWNER_TOKEN_FILE", "EXTRA_KEY"} & set(env)
    assert env["ASSAY"] == str(LAUNCHER) and env["ASSAY_STOP_GATE"] == str(runner.gate_state)
    # Without the gate the variable is not passed on, whatever the runner's own environment holds.
    env, _ = _runner(run, stop_gate=False).player_environment()
    assert "ASSAY_STOP_GATE" not in env


def test_the_world_runs_under_the_interpreter_the_run_was_started_with(tmp_path, monkeypatch):
    run = _fixture_run(tmp_path, paid=0)
    (run / ".assay" / "config.json").write_text(json.dumps({"game_id": "w", "python": sys.executable}))
    monkeypatch.delenv("ASSAY_PYTHON", raising=False)
    runner = _runner(run)
    assert runner.world_environment()["ASSAY_PYTHON"] == sys.executable
    assert runner.player_environment()[0]["ASSAY_PYTHON"] == sys.executable
    # A shell that names one keeps it, and a recorded interpreter that is gone is not passed on.
    monkeypatch.setenv("ASSAY_PYTHON", "/chosen/python")
    assert runner.world_environment()["ASSAY_PYTHON"] == "/chosen/python"
    monkeypatch.delenv("ASSAY_PYTHON")
    (run / ".assay" / "config.json").write_text(json.dumps({"game_id": "w", "python": str(tmp_path / "gone")}))
    assert "ASSAY_PYTHON" not in runner.world_environment()


def test_the_gate_merges_into_the_settings_beside_the_kernels_hooks(tmp_path):
    from assay.hooks import merge_hook_entries

    run = _fixture_run(tmp_path, paid=0)
    settings = run / ".claude" / "settings.json"
    settings.parent.mkdir()
    foreign = {"hooks": [{"type": "command", "command": "notify-me"}]}
    settings.write_text(json.dumps({
        "model": "kept",
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x/pre_tool_use.py --policy /p.json"}]}],
                  "Stop": [foreign]},
    }))
    runner = _runner(run)
    runner.install_gate()
    runner.install_gate()
    merged = json.loads(settings.read_text())
    assert merged["model"] == "kept" and merged["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert merged["hooks"]["Stop"] == [
        foreign, {"hooks": [{"type": "command", "command": runner.gate_command(), "timeout": 60}]},
    ]
    assert runner.gate_command().endswith(f"hooks/stop_gate.py --run-dir {run}")
    # The kernel's own install keeps the gate.
    kernel, _ = merge_hook_entries(merged, "pre", "post")
    assert kernel["hooks"]["Stop"] == merged["hooks"]["Stop"]
    # The policy the PreToolUse entry names pins the launcher.
    policy = tmp_path / "p.json"
    policy.write_text(json.dumps({"assay_value": "/opt/assay/bin/assay", "launcher": "/other"}))
    merged["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = f"python3 x/pre_tool_use.py --policy {policy}"
    settings.write_text(json.dumps(merged))
    assert session.SessionRunner(run, host="claude").launcher == ["/opt/assay/bin/assay"]
    # No write through a symlink.
    settings.unlink()
    settings.symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(EvalError, match="is a symlink, and the gate writes through none"):
        runner.install_gate()


def test_the_owner_note():
    granted = {"kind": "approval_granted", "action": "DRILL", "timestamp": _stamp(30)}
    old = {"kind": "approval_granted", "action": "PAY", "timestamp": _stamp(900)}
    used = {"kind": "approval_granted", "action": "SELL", "timestamp": _stamp(20)}
    activity = [
        old, used, {"kind": "approval_used", "action": "SELL"},
        {"kind": "goal_ratified", "id": 1, "text": "an early ratification"},
        granted,
        {"kind": "liveness_waived", "action": "LAUNCH", "because": "rehearsed on Tuesday"},
        {"kind": "goal_ratified", "id": 2, "text": "open only the first door"},
    ]
    lines = session.owner_note(activity, 4, daemon_kept=True)
    assert len(lines) == 3
    assert lines[0].startswith("the owner granted one use of DRILL at ") and lines[0].endswith(
        " UTC; an approval is used up by one act and expires 600 seconds after it is granted"
    )
    assert lines[1:] == [
        "the owner waived the rehearsal quota of LAUNCH, because: rehearsed on Tuesday",
        "the owner ratified goal proposal #2: open only the first door",
    ]
    # A daemon the start replaced holds no approval.
    assert session.owner_note(activity, 4, daemon_kept=False) == lines[1:]


def test_the_cli_routes_the_session_mode(tmp_path, capsys):
    tmp_path = tmp_path.resolve()
    assert cli.main(["session", "--run-dir", str(tmp_path / "none")]) == 2
    assert capsys.readouterr().err.strip() == (
        f"ERROR | {tmp_path / 'none'} holds no started run; start one first (/assay:attach, or `assay start`)"
    )
    run = _fixture_run(tmp_path, paid=0)
    (run / ".assay" / "config.json").write_text(json.dumps({"game_id": "counterdemo"}))
    assert cli.main(["session", "--run-dir", str(run), "--", ]) == 2
    assert capsys.readouterr().err.strip() == "ERROR | `--` must be followed by the player's command"
    assert cli.main(["session", "--run-dir", str(run), "--dry-run", "--", *FAKE_PLAYER]) == 2
    assert capsys.readouterr().err.strip() == (
        f"ERROR | no prompt at {run / 'PLAYER.md'}: write the player's prompt (skills/attach/start.md), or pass --prompt FILE"
    )


# ---- end to end ----------------------------------------------------------------------------------

def _env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in ("ASSAY_OWNER_TOKEN_FILE", "ASSAY_STOP_GATE")}
    return {**env, "ASSAY_PYTHON": sys.executable, **extra}


def _start(run: Path, token: Path, registry: Path = COUNTER_REGISTRY) -> None:
    run.mkdir(parents=True)
    started = subprocess.run(
        [str(LAUNCHER), "--run-dir", str(run), "start", "counterdemo", "--adapter", f"{COUNTER_WORLD}:factory",
         "--registry", str(registry), "--owner-token-file", str(token)],
        capture_output=True, text=True, env=_env(), timeout=180,
    )
    assert started.returncode == 0, started.stdout + started.stderr


def _session(run: Path, *flags: str, env: dict[str, str] | None = None, command: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-m", "tools.eval", "session", "--run-dir", str(run), "--assay", str(LAUNCHER),
            "--poll-seconds", "0.1", *flags, "--", *(command or FAKE_PLAYER)]
    return subprocess.run(argv, cwd=REPO, env=env or _env(), capture_output=True, text=True, timeout=240)


def _ledger(run: Path) -> list[dict]:
    return json.loads(run.with_name(run.name + ".sessions").joinpath("sessions.json").read_text())


def test_sessions_relaunch_to_a_win_through_the_real_cli_and_daemon(tmp_path):
    tmp_path = tmp_path.resolve()
    run, token = tmp_path / "run", tmp_path / "run.token"
    _start(run, token)
    (run / "PLAYER.md").write_text('Scripted.\nassay status\nassay act INC amount=2 --predict "change"\n')
    try:
        done = _session(run)
        out = done.stdout
        assert done.returncode == 0, out + done.stderr
        assert f"SESSIONS | {run} | world counterdemo | host fake_player Python " in out
        assert "LAUNCH | session 1 | pid " in out and "| RESUMED | paid actions 0 so far" in out
        assert "| paid actions 0 to 1 | $0.02 | UNFINISHED" in out
        assert "RELAUNCH | session 1 ended without WIN, the cap, an owner action or a stop declaration" in out
        assert "| paid actions 1 to 2 | $0.02 | WIN" in out
        assert out.rstrip().endswith(
            "OUTCOME | WIN | 2 session(s) | paid actions 2/40 | $0.04 reported by the host | the daemon has finished"
        )
        ledger = _ledger(run)
        assert [(record["session"], record["ended_because"], record["launch"]["paid"], record["paid_at_exit"]) for record in ledger] == [
            (1, "UNFINISHED", 0, 1), (2, "WIN", 1, 2),
        ]
        first = ledger[0]
        assert first["host"] == "fake_player" and first["host_version"].startswith("Python ")
        assert first["start"].startswith("RESUMED | counterdemo") and first["stripped"] == []
        assert first["spend"] == "SPEND | recorded | cumulative $0.02 | 220 tokens"
        assert ledger[1]["spend"] == "SPEND | recorded | cumulative $0.04 | 440 tokens"
        assert first["gate"] is None and first["report"]["transcript"][0]["command"] == ["assay", "status"]
        reports = [line for line in (run / ".assay" / "activity.jsonl").read_text().splitlines() if '"spend_report"' in line]
        assert len(reports) == 2
        status = json.loads(run.with_name("run.sessions").joinpath("status.json").read_text())
        assert (status["state"], status["outcome"], status["sessions"], status["paid"]) == ("ended", "WIN", 2, 2)
        assert json.loads((run / ".assay" / "broker.json").read_text())["status"] == "FINISHED"
        # A finished run launches nothing.
        again = _session(run)
        assert "LAUNCH" not in again.stdout and "OUTCOME | WIN | 0 session(s)" in again.stdout
    finally:
        stop_run(run)


def test_an_owner_action_pauses_the_sessions_and_the_resume_carries_the_note(tmp_path):
    tmp_path = tmp_path.resolve()
    run, token = tmp_path / "run", tmp_path / "run.token"
    registry = json.loads(COUNTER_REGISTRY.read_text())
    registry["actions"][0]["approval"] = True
    registry["secrets"] = ["FAKE_WORLD_KEY"]
    variant = tmp_path / "approval.json"
    variant.write_text(json.dumps(registry))
    _start(run, token, variant)
    (run / "PLAYER.md").write_text('Scripted.\nassay status\nassay act INC amount=2 --predict "change"\n')
    env = _env(FAKE_WORLD_KEY="hush", ASSAY_OWNER_TOKEN_FILE=str(token))
    try:
        paused = _session(run, "--owner-token-file", str(token), env=env)
        assert paused.returncode == 0, paused.stdout + paused.stderr
        assert "| paid actions 0 to 0 | $0.02 | OWNER_NEEDED" in paused.stdout
        assert "OWNER | INC is approval-gated (default-deny) and has no fresh approval" in paused.stdout
        assert f"OWNER | to grant one use: {LAUNCHER} --run-dir {run} approve INC --token-file {token}" in paused.stdout
        assert paused.stdout.rstrip().endswith(
            "OUTCOME | OWNER_NEEDED | 1 session(s) | paid actions 0/40 | $0.02 reported by the host | "
            "the daemon is left up; the same command resumes the run"
        )
        first = _ledger(run)[0]
        assert first["stripped"] == ["ASSAY_OWNER_TOKEN_FILE", "FAKE_WORLD_KEY"]
        names = first["report"]["environment"]
        assert "ASSAY" in names and "FAKE_WORLD_KEY" not in names and "ASSAY_OWNER_TOKEN_FILE" not in names
        assert first["owner_pending"][0]["action"] == "INC"
        assert "hush" not in json.dumps(_ledger(run)) and token.read_text().strip() not in json.dumps(_ledger(run))
        approved = subprocess.run(
            [str(LAUNCHER), "--run-dir", str(run), "approve", "INC", "--token-file", str(token)],
            capture_output=True, text=True, env=_env(), timeout=120,
        )
        assert approved.returncode == 0 and approved.stdout.startswith("APPROVED | one use of INC")
        resumed = _session(run, "--owner-token-file", str(token), env=env)
        assert "| paid actions 0 to 1 | $0.02 | UNFINISHED" in resumed.stdout
        assert "| paid actions 1 to 1 | $0.02 | OWNER_NEEDED" in resumed.stdout
        assert "OUTCOME | OWNER_NEEDED | 2 session(s) | paid actions 1/40" in resumed.stdout
        prompt = run.with_name("run.sessions").joinpath("s2.prompt.md").read_text()
        assert session.NOTE_HEADER in prompt and "- the owner granted one use of INC at " in prompt
        assert session.NOTE_HEADER not in run.with_name("run.sessions").joinpath("s3.prompt.md").read_text()
    finally:
        stop_run(run)


def test_a_declared_stop_ends_the_sessions_and_a_rerun_needs_a_new_one(tmp_path):
    tmp_path = tmp_path.resolve()
    run, token = tmp_path / "run", tmp_path / "run.token"
    _start(run, token)
    (run / "PLAYER.md").write_text("Scripted.\nassay status\nnote: STOP: the counter cannot be driven, said the script\n")
    try:
        declared = _session(run)
        assert "| UNFINISHED" not in declared.stdout and "| $0.01 | SELF_STOP" in declared.stdout
        assert "DECLARED | the counter cannot be driven, said the script" in declared.stdout
        assert "OUTCOME | SELF_STOP | 1 session(s) | paid actions 0/40" in declared.stdout
        assert _ledger(run)[0]["declaration"] == "the counter cannot be driven, said the script"
        (run / "PLAYER.md").write_text("Scripted.\nassay status\n")
        rerun = _session(run, "--max-sessions", "2")
        assert rerun.stdout.count("| UNFINISHED") == 2 and rerun.stdout.count("RELAUNCH |") == 1
        assert "OUTCOME | SESSIONS_EXHAUSTED | 2 session(s) | paid actions 0/40" in rerun.stdout
    finally:
        stop_run(run)


def test_the_stop_file_a_failing_player_and_a_usage_limit(tmp_path):
    tmp_path = tmp_path.resolve()
    run, token = tmp_path / "run", tmp_path / "run.token"
    _start(run, token)
    (run / "PLAYER.md").write_text("Scripted.\n")
    state = run.with_name("run.sessions")
    try:
        state.mkdir()
        (state / "STOP").write_text("")
        stopped = _session(run)
        assert "LAUNCH" not in stopped.stdout and "OUTCOME | STOPPED | 0 session(s)" in stopped.stdout
        (state / "STOP").unlink()
        failing = _session(run, command=["{python}", "-c", "import sys; print('no key', file=sys.stderr); sys.exit(3)"])
        assert failing.stdout.count("| exit 3 |") == 2
        assert f"DETAIL | exit 3 twice with no paid action; read {state / 's2.player.err'}" in failing.stdout
        assert "OUTCOME | PLAYER_FAILING | 2 session(s)" in failing.stdout
        limited = json.dumps({"type": "result", "is_error": True, "result": "You've hit your usage limit", "num_turns": 1})
        paused = _session(
            run, "--hours", "0.001", "--pause-minutes", "0.002",
            command=["{python}", "-c", f"import sys; print({limited!r}); sys.exit(1)"],
        )
        assert "| RATE_LIMITED" in paused.stdout and "PAUSE | a usage limit was signalled" in paused.stdout
        # A limited session does not count; one the wall clock cut short does.
        later = _ledger(run)[2:]
        counted = [record for record in later if not record["rate_limited"]]
        assert all(record["ended_because"] == "TIME_EXHAUSTED" for record in counted) and len(counted) <= 1
        assert f"OUTCOME | TIME_EXHAUSTED | {len(counted)} session(s)" in paused.stdout
    finally:
        stop_run(run)
