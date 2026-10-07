"""The evaluation runner under tools/eval: the job file parses and resolves
its relative paths, the dry run prints every command and writes nothing, the
per-seed rows and the summary are computed from fixture receipts and activity
records, a finished job is not rerun, the bootstrap interval is deterministic
under a seed, the lock refuses a second runner, and one end-to-end queue runs
the fake player against the counter example through the real launcher, CLI
and daemon in the operator-first order. Nothing here starts a live session
against anything under bench/; the shipped job files are only dry-run."""

from __future__ import annotations

import fcntl
import importlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from conftest import stop_run

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
eval_package = importlib.import_module("tools.eval")
jobs = importlib.import_module("tools.eval.jobs")
results = importlib.import_module("tools.eval.results")
summary = importlib.import_module("tools.eval.summary")
cli = importlib.import_module("tools.eval.cli")
EvalError = eval_package.EvalError

COUNTER_WORLD = REPO / "examples" / "counter_world.py"
COUNTER_REGISTRY = REPO / "examples" / "example_registry.json"
FAKE_PLAYER = REPO / "tools" / "eval" / "fake_player"
LAUNCHER = REPO / "bin" / "assay"
JOB_FILES = REPO / "bench" / "arcagi" / "jobs"


def _relative(target: Path, base: Path) -> str:
    return os.path.relpath(target, base)


def _write_design(jobs_dir: Path, **overrides) -> Path:
    """A job file beside the counter example, every path relative to it."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    prompt = jobs_dir / "script.md"
    if not prompt.exists():
        prompt.write_text("Session for {{WORLD}} in {{RUN_DIR}}, budget {{BUDGET}}.\nassay status\n")
    design = {
        "name": "d",
        "adapter": _relative(COUNTER_WORLD, jobs_dir) + ":factory",
        "concurrency": 2,
        "max_sessions": 2,
        "max_hours": 0.5,
        "player": {"model": "fake", "command": ["{python}", "{repo}/tools/eval/fake_player"]},
        "worlds": [
            {"id": "counterdemo", "levels": 1, "budget": 40, "registry": _relative(COUNTER_REGISTRY, jobs_dir)}
        ],
        "arms": [{"name": "a", "prompt": "script.md"}],
        "seeds": [1],
    }
    design.update(overrides)
    path = jobs_dir / "d.json"
    path.write_text(json.dumps(design, indent=2))
    return path


def _journal(run: Path, paid: int, *, final_state: str) -> None:
    state = run / ".assay"
    state.mkdir(parents=True, exist_ok=True)
    lines = [{"id": 0, "action": "START", "counts_action": False, "state": "NOT_FINISHED", "levels_completed": 0, "win_levels": 1}]
    for index in range(1, paid + 1):
        lines.append({
            "id": index, "action": "INC", "counts_action": True, "predict": "change",
            "state": final_state if index == paid else "NOT_FINISHED",
            "levels_completed": 1 if (index == paid and final_state == "WIN") else 0, "win_levels": 1,
        })
    (state / "events.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))


# ---- the job file --------------------------------------------------------------------

def test_job_file_parses_and_resolves_relative_paths(tmp_path):
    root = tmp_path / "design"
    jobs_dir = root / "jobs"
    jobs_dir.mkdir(parents=True)
    (root / "world.py").write_text("def factory(root, config): ...\n")
    (root / "reg.json").write_text(json.dumps({"actions": [], "budget": {"actions": 7}}))
    (jobs_dir / "prompts").mkdir()
    (jobs_dir / "prompts" / "p.md").write_text("{{WORLD}} {{BUDGET}}\n")
    (tmp_path / "MANUAL.md").write_text("manual\n")
    (jobs_dir / "d.json").write_text(json.dumps({
        "name": "e9",
        "adapter": "../world.py:factory",
        "player": {"model": "m", "command": ["player", "--model", "{model}", "{prompt_file}"]},
        "worlds": [
            {"id": "w1", "levels": 3, "budget": 7, "registry": "../reg.json"},
            {"id": "w2", "levels": 1, "budget": 7, "registry": "../reg.json", "pass_seed": True},
        ],
        "arms": [
            {"name": "gated", "prompt": "prompts/p.md", "constitution": "../../MANUAL.md"},
            {"name": "off", "prompt": "prompts/p.md", "registry_overlay": {"gate": "off"}, "model": "other"},
        ],
        "seeds": [1, 2],
    }))
    design = jobs.load_design(jobs_dir / "d.json")
    assert design.name == "e9" and design.concurrency == 1 and design.max_sessions == 3
    assert design.max_hours == 4.0 and design.model == "m"
    # Seed-major, then world, then arm: the protocols' queue order.
    assert [job.id for job in design.jobs] == [
        "e9-w1-gated-s1", "e9-w1-off-s1", "e9-w2-gated-s1", "e9-w2-off-s1",
        "e9-w1-gated-s2", "e9-w1-off-s2", "e9-w2-gated-s2", "e9-w2-off-s2",
    ]
    first = design.jobs[0]
    assert first.registry == (root / "reg.json").resolve()
    assert first.world.adapter == f"{(root / 'world.py').resolve()}:factory"
    assert first.arm.prompt == (jobs_dir / "prompts" / "p.md").resolve()
    assert first.arm.constitution == (tmp_path / "MANUAL.md").resolve()
    assert first.model == "m" and design.jobs[1].model == "other"
    assert design.jobs[1].overlay == {"gate": "off"} and first.overlay is None
    assert design.jobs[2].world.pass_seed is True and first.world.pass_seed is False
    assert jobs.missing_inputs(design) == []
    assert jobs.registry_variant(design.jobs[1]) == {"actions": [], "budget": {"actions": 7}, "gate": "off"}
    assert jobs.registry_variant(first) is None
    assert jobs.variant_name(design.jobs[1]) == "off-reg.json"
    jobs.check_budget(first)
    # The budget must agree with the registry's cap.
    (root / "reg.json").write_text(json.dumps({"actions": [], "budget": {"actions": 9}}))
    try:
        jobs.check_budget(first)
    except EvalError as error:
        assert "the job's budget is 7 but reg.json caps paid actions at 9" in str(error)
    else:
        raise AssertionError("a budget mismatch must be refused")
    # A missing input is reported, labelled, once.
    (tmp_path / "MANUAL.md").unlink()
    assert jobs.missing_inputs(design) == [("e9-w1-gated-s1", "constitution", (tmp_path / "MANUAL.md").resolve())]


def test_job_file_refuses_absolute_paths_and_unknown_placeholders(tmp_path):
    jobs_dir = tmp_path / "jobs"
    base = json.loads(_write_design(jobs_dir).read_text())
    cases = [
        ({"adapter": f"{COUNTER_WORLD}:factory"}, "adapter must be a relative path"),
        ({"worlds": [{**base["worlds"][0], "registry": str(COUNTER_REGISTRY)}]}, "registry must be a relative path"),
        ({"arms": [{"name": "a", "prompt": "~/p.md"}]}, "prompt must be a relative path"),
        ({"player": {"model": "m", "command": ["player", "{nope}"]}}, "unknown placeholder(s) ['nope']"),
        ({"seeds": [1, 1]}, "duplicate seeds"),
        ({"worlds": [{**base["worlds"][0], "budget": 0}]}, "budget must be at least 1"),
    ]
    for override, message in cases:
        path = jobs_dir / "bad.json"
        path.write_text(json.dumps({**base, **override}))
        try:
            jobs.load_design(path)
        except EvalError as error:
            assert message in str(error), (override, str(error))
        else:
            raise AssertionError(f"{override} must be refused")


# ---- the dry run -------------------------------------------------------------------------

def test_dry_run_prints_every_command_and_creates_nothing(tmp_path, capsys):
    jobs_dir = tmp_path / "jobs"
    path = _write_design(
        jobs_dir,
        worlds=[{"id": "counterdemo", "levels": 1, "budget": 40, "registry": _relative(COUNTER_REGISTRY, jobs_dir), "pass_seed": True}],
        arms=[
            {"name": "a", "prompt": "script.md"},
            {"name": "b", "prompt": "script.md", "registry_overlay": {"gate": "off"}},
        ],
        player={"model": "fake", "command": ["claude", "--model", "{model}", "-p", "{prompt}", "--output-format", "json"]},
    )
    state = tmp_path / "state"
    code = cli.main(["--jobs", str(path), "--dry-run", "--state-dir", str(state), "--assay", "/x/bin/assay"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert not state.exists()
    run_a, run_b = state / "runs" / "d-counterdemo-a-s1", state / "runs" / "d-counterdemo-b-s1"
    assert "JOB | d-counterdemo-a-s1 | world counterdemo | arm a | seed 1 | budget 40 | model fake" in out
    assert "JOB | d-counterdemo-b-s1 | world counterdemo | arm b | seed 1 | budget 40 | model fake" in out
    assert (
        f"ASSAY_ANCHOR_DIR={state / 'anchors'} /x/bin/assay --run-dir {run_a} start counterdemo "
        f"--adapter {COUNTER_WORLD}:factory --registry {COUNTER_REGISTRY} "
        f"--owner-token-file {state / 'tokens' / 'd-counterdemo-a-s1.token'} --seed 1"
    ) in out
    variant = state / "registries" / "b-example_registry.json"
    assert f'write {variant} = {COUNTER_REGISTRY} + {{"gate": "off"}}' in out
    assert f"--registry {variant} --owner-token-file {state / 'tokens' / 'd-counterdemo-b-s1.token'} --seed 1" in out
    prompt_a = state / "prompts" / "d-counterdemo-a-s1.s1.md"
    assert f"render {prompt_a} from {jobs_dir / 'script.md'}" in out
    assert (
        f'(cd {run_a} && ASSAY=/x/bin/assay claude --model fake -p "$(cat {prompt_a})" '
        f"--output-format json < {prompt_a})"
    ) in out
    assert f"(cd {run_b} && ASSAY=/x/bin/assay claude --model fake -p" in out
    assert "DRY RUN | d | 2 job(s) | concurrency 2 | max sessions 2 | 0.5h per session" in out
    assert out.rstrip().endswith("nothing written")
    assert "MISSING" not in out and "PROBLEM" not in out


def test_dry_run_reports_missing_inputs_and_exits_one(tmp_path, capsys):
    jobs_dir = tmp_path / "jobs"
    path = _write_design(jobs_dir, arms=[{"name": "a", "prompt": "script.md", "constitution": "manuals/absent.md"}])
    state = tmp_path / "state"
    code = cli.main(["--jobs", str(path), "--dry-run", "--state-dir", str(state), "--assay", "/x/bin/assay"])
    out = capsys.readouterr().out
    assert code == 1
    assert not state.exists()
    assert f"MISSING | d-counterdemo-a-s1 | constitution | {jobs_dir / 'manuals' / 'absent.md'}" in out
    assert "1 missing input(s), 0 problem(s); a live run refuses to launch until they are fixed" in out


def test_shipped_e1_and_e1b_job_files_dry_run(tmp_path, capsys):
    e1 = jobs.load_design(JOB_FILES / "e1.json")
    assert [job.id for job in e1.jobs][:4] == ["e1-ft09-gated-s1", "e1-ft09-ungated-s1", "e1-tr87-gated-s1", "e1-tr87-ungated-s1"]
    assert len(e1.jobs) == 12 and e1.concurrency == 3 and e1.max_sessions == 3 and e1.model == "claude-opus-5"
    assert {job.world.id: job.budget for job in e1.jobs} == {"ft09": 200, "tr87": 1500, "cn04": 1500}
    assert {job.arm.name: job.overlay for job in e1.jobs} == {"gated": None, "ungated": {"gate": "optional"}}
    for job in e1.jobs:
        assert job.registry.is_file() and job.arm.prompt.is_file(), job.id
        assert job.registry.parent == REPO / "bench" / "arcagi"
        jobs.check_budget(job)
    e1b = jobs.load_design(JOB_FILES / "e1b.json")
    assert [job.id for job in e1b.jobs] == [
        "e1b-ft09-off-s1", "e1b-tr87-off-s1", "e1b-cn04-off-s1",
        "e1b-ft09-off-s2", "e1b-tr87-off-s2", "e1b-cn04-off-s2",
    ]
    assert {job.overlay["gate"] for job in e1b.jobs} == {"off"}
    # The control arms' manuals did not ship (owner decision O4): the dry run
    # names them as the one missing input each, prints every command, and
    # writes nothing. Nothing here starts a session.
    for name, missing in (("e1", "CONSTITUTION-ungated.md"), ("e1b", "CONSTITUTION-e1b-off.md")):
        state = tmp_path / name
        code = cli.main(["--jobs", str(JOB_FILES / f"{name}.json"), "--dry-run", "--state-dir", str(state)])
        out = capsys.readouterr().out
        assert code == 1, out
        assert not state.exists()
        missing_lines = [line for line in out.splitlines() if line.startswith("MISSING")]
        assert missing_lines == [f"MISSING | {name}-ft09-{'ungated' if name == 'e1' else 'off'}-s1 | constitution | {JOB_FILES / 'manuals' / missing}"]
        assert "PROBLEM" not in out
        assert out.count("JOB | ") == len(e1.jobs if name == "e1" else e1b.jobs)
        assert f"{LAUNCHER} --run-dir {state / 'runs' / f'{name}-ft09-'}" in out
        assert "claude --model claude-opus-5 -p" in out


# ---- the rows and the summary --------------------------------------------------------------

def test_rows_from_fixture_receipts_and_activity(tmp_path):
    design = jobs.load_design(_write_design(tmp_path / "jobs"))
    job = design.jobs[0]
    run = tmp_path / "run"
    state = run / ".assay"
    (state / "receipts").mkdir(parents=True)
    events = [
        {"id": 0, "action": "START", "counts_action": False, "state": "NOT_FINISHED", "levels_completed": 0, "win_levels": 1},
        {"id": 1, "action": "INC", "counts_action": True, "predict": "change", "state": "NOT_FINISHED", "levels_completed": 0, "win_levels": 1},
        {"id": 2, "action": "NOOP", "counts_action": True, "predict": None, "gate_optional": True, "state": "NOT_FINISHED", "levels_completed": 0, "win_levels": 1},
        {"id": 3, "action": "INC", "counts_action": True, "predict": "change; win", "state": "WIN", "levels_completed": 1, "win_levels": 1},
    ]
    (state / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    for name, outcome, end in (("1-e1", "PREDICTED", 1), ("2-e2", "UNGATED", 2), ("3-e3", "GAME_COMPLETE", 3)):
        (state / "receipts" / f"{name}.json").write_text(json.dumps({"kind": "act", "outcome": outcome, "end_event": end}))
    activity = [
        {"kind": "command_start", "command": "act", "status": "RUNNING"},
        {"kind": "command_end", "command": "act", "status": "ERROR", "error": "AssayError: the prediction gate is off for this run (registry gate: off): `assay act` takes no --predict here"},
        {"kind": "receipt", "outcome": "PREDICTED", "end_event": 1},
        {"kind": "command_end", "command": "act", "status": "FINISHED"},
    ]
    (state / "activity.jsonl").write_text("".join(json.dumps(record) + "\n" for record in activity))
    (state / "chain.json").write_text(json.dumps({"event_id": 3, "head": "f00d" * 16}))
    facts = results.read_run(run)
    assert facts.events == 4 and facts.paid_actions == 3 and facts.win and facts.state == "WIN"
    assert facts.levels_completed == 1 and facts.win_levels == 1 and facts.predicted_actions == 2
    assert facts.receipts == {"GAME_COMPLETE": 1, "PREDICTED": 1, "UNGATED": 1}
    assert facts.refused_predictions == 1 and facts.chain_head == "f00d" * 16
    sessions = [
        {"session": 1, "wall_seconds": 100.5, "report": {"total_cost_usd": 1.25, "num_turns": 9, "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 0}}},
        {"session": 2, "wall_seconds": 50, "report": {"total_cost_usd": 0.75, "num_turns": 4, "usage": {"input_tokens": 500, "output_tokens": 100}}},
    ]
    row = results.row_for(job, facts, sessions, outcome=None, run_dir="runs/x")
    assert row["job"] == "d-counterdemo-a-s1" and row["world"] == "counterdemo" and row["arm"] == "a" and row["seed"] == 1
    assert row["outcome"] == "WIN" and row["win"] is True and row["paid_actions"] == 3 and row["budget"] == 40
    assert row["sessions"] == 2 and row["wall_seconds"] == 150.5
    assert row["tokens"] == {"input": 1500, "output": 300, "cache_read": 300, "cache_creation": 0, "total": 2100}
    assert row["dollars"] == 2.0 and row["dollars_per_action"] == round(2.0 / 3, 6)
    assert row["tokens_per_action"] == 700.0 and row["source"] == "files"
    assert row["receipts"] == {"GAME_COMPLETE": 1, "PREDICTED": 1, "UNGATED": 1} and row["refused_predictions"] == 1
    # A run without a journal yet, and a run at the cap.
    assert results.read_run(tmp_path / "nowhere") is None
    assert results.outcome_of(None, 40) is None
    _journal(tmp_path / "capped", 40, final_state="NOT_FINISHED")
    assert results.outcome_of(results.read_run(tmp_path / "capped"), 40) == "CAP"
    # A session whose player printed no JSON report counts no tokens and no dollars.
    bare = results.row_for(job, facts, [{"session": 1, "wall_seconds": 3, "report": None}], outcome=None, run_dir="runs/x")
    assert bare["tokens"] is None and bare["dollars"] is None and bare["tokens_per_action"] is None
    assert results.player_report("not json\n") is None
    assert results.player_report('{"total_cost_usd": 0.5}\n') == {"total_cost_usd": 0.5}


def test_bootstrap_interval_and_summary_are_deterministic_under_a_seed():
    paid = [80.0, 75.0, 324.0, 216.0, 222.0, 275.0]
    first = summary.bootstrap_interval(paid, seed=11, resamples=500)
    assert first == summary.bootstrap_interval(paid, seed=11, resamples=500)
    assert min(paid) <= first[0] <= first[1] <= max(paid)
    assert summary.bootstrap_interval([80.0], seed=1) == (80.0, 80.0)
    assert summary.bootstrap_interval([], seed=1) is None
    rows = []
    for arm, values in (("gated", [80, 75, 324, 216, 222, 275]), ("ungated", [80, 80, 282, 185, 201, 300])):
        for index, value in enumerate(values):
            rows.append({
                "job": f"e1-{['ft09', 'ft09', 'tr87', 'tr87', 'cn04', 'cn04'][index]}-{arm}-s{index % 2 + 1}",
                "world": ["ft09", "ft09", "tr87", "tr87", "cn04", "cn04"][index], "arm": arm, "seed": index % 2 + 1,
                "win": True, "paid_actions": value, "tokens_per_action": 1000.0 + value, "dollars_per_action": 0.05,
                "dollars": value * 0.05,
            })
    one = summary.summarize(rows, name="e1", seed=3, resamples=400)
    two = summary.summarize(rows, name="e1", seed=3, resamples=400)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    gated = one["arms"]["gated"]
    assert gated["runs"] == 6 and gated["wins"] == 6 and gated["paid_actions"]["mean"] == 198.66666666666666
    assert gated["win"]["interval"] == [1.0, 1.0]
    assert gated["worlds"]["ft09"]["paid_actions"]["values"] == [80.0, 75.0]
    assert 75.0 <= gated["worlds"]["ft09"]["paid_actions"]["interval"][0] <= 80.0
    assert one["bootstrap"] == {"seed": 3, "resamples": 400, "level": 0.95}
    text = summary.render_summary(one)
    assert text.startswith("SUMMARY | e1 | 12 run(s) | bootstrap 400 resamples, 95% percentile intervals of the mean, seed 3")
    assert "ARM | gated | runs 6 | wins 6/6 1.00 [1.00, 1.00] | paid actions 198.7 [" in text
    assert "  ft09 | runs 2 | wins 2/2 | paid actions 80, 75 | mean 77.5 [" in text


# ---- resumability and the lock -----------------------------------------------------------------

def test_finished_jobs_are_skipped_and_never_relaunched(tmp_path, capsys):
    path = _write_design(tmp_path / "jobs", seeds=[1, 2])
    state = tmp_path / "state"
    _journal(state / "runs" / "d-counterdemo-a-s1", 2, final_state="WIN")
    _journal(state / "runs" / "d-counterdemo-a-s2", 40, final_state="NOT_FINISHED")
    # A launcher that cannot exist: any launch would fail loudly.
    code = cli.main(["--jobs", str(path), "--state-dir", str(state), "--assay", str(tmp_path / "no" / "assay"), "--poll-seconds", "0.05"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "SKIP | d-counterdemo-a-s1 | already WIN | paid actions 2" in out
    assert "SKIP | d-counterdemo-a-s2 | already CAP | paid actions 40" in out
    assert "LAUNCH" not in out and "FAIL" not in out
    assert "QUEUE | drained | done 2 | failed 0 | stopped 0" in out
    rows = [json.loads(line) for line in (state / "results.jsonl").read_text().splitlines()]
    assert [(row["job"], row["outcome"], row["win"], row["paid_actions"], row["sessions"]) for row in rows] == [
        ("d-counterdemo-a-s1", "WIN", True, 2, 0), ("d-counterdemo-a-s2", "CAP", False, 40, 0),
    ]
    status = json.loads((state / "status.json").read_text())
    assert status["final"] == "drained" and status["done"] == ["d-counterdemo-a-s1", "d-counterdemo-a-s2"]
    assert status["jobs"]["d-counterdemo-a-s1"]["skipped"] is True
    assert json.loads((state / "summary.json").read_text())["arms"]["a"]["wins"] == 1


def test_the_lock_refuses_a_second_runner(tmp_path, capsys):
    path = _write_design(tmp_path / "jobs")
    state = tmp_path / "state"
    state.mkdir()
    with (state / "runner.lock").open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.write("pid 12345 since earlier\n")
        held.flush()
        for extra in ([], ["--summarize"]):
            code = cli.main(["--jobs", str(path), "--state-dir", str(state), "--assay", "/x/bin/assay", *extra])
            captured = capsys.readouterr()
            assert code == 2
            assert captured.err.strip() == (
                f"ERROR | another runner holds {state / 'runner.lock'} (pid 12345 since earlier); "
                "one runner per state directory"
            )
            assert "LAUNCH" not in captured.out
    assert not (state / "status.json").exists()


# ---- end to end -----------------------------------------------------------------------------------

def test_end_to_end_with_the_fake_player_through_the_real_cli_and_daemon(tmp_path):
    """Two seeds of the counter world, two players at a time, each session a
    scripted `status`, one `act`, and `stop`: the first session leaves the
    counter at 2 (no WIN, below the cap), so the runner resumes the job
    through the operator's `start` (the kernel replays the journal) and the
    second session's act reaches 3, WIN. The token file lies outside the run
    directory and the token never reaches the player; a rerun launches
    nothing."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (jobs_dir / "script.md").write_text(
        "Scripted session for {{WORLD}} in {{RUN_DIR}}, budget {{BUDGET}}, seed {{SEED}}.\n"
        "assay status\n"
        'assay act INC amount=2 --predict "change"\n'
        "assay stop\n"
    )
    path = _write_design(jobs_dir, seeds=[1, 2])
    state = tmp_path / "state"
    env = {**os.environ, "ASSAY_PYTHON": sys.executable}
    command = [sys.executable, "-m", "tools.eval", "--jobs", str(path), "--state-dir", str(state), "--poll-seconds", "0.2"]
    runs = [state / "runs" / f"d-counterdemo-a-s{seed}" for seed in (1, 2)]
    try:
        completed = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, timeout=240)
        out = completed.stdout
        assert completed.returncode == 0, out + completed.stderr
        for job in ("d-counterdemo-a-s1", "d-counterdemo-a-s2"):
            assert f"LAUNCH | {job} | session 1 | pid " in out
            assert f"RELAUNCH | {job} | session 1 ended without WIN or cap (exit 0); another session is queued" in out
            assert f"LAUNCH | {job} | session 2 | pid " in out
            assert f"DONE | {job} | WIN | 2 session(s) | paid actions 2" in out
        assert "QUEUE | drained | done 2 | failed 0 | stopped 0" in out
        assert "ARM | a | runs 2 | wins 2/2 1.00 [1.00, 1.00] | paid actions 2.0 [2.0, 2.0] | tokens/action 330.0 [330.0, 330.0] | USD/action 0.0300 [0.0300, 0.0300] | USD/run 0.06 [0.06, 0.06]" in out
        assert "  counterdemo | runs 2 | wins 2/2 | paid actions 2, 2 | mean 2.0 [2.0, 2.0]" in out
        for run, seed in zip(runs, (1, 2)):
            job = f"d-counterdemo-a-s{seed}"
            # The operator's start: a fresh run, then a resume by replay once
            # the first session's `stop` took the daemon down.
            first = (state / "sessions" / f"{job}.s1.start.log").read_text()
            assert "STARTED | counterdemo | local simulator" in first
            assert f"OWNER TOKEN | written to {state / 'tokens' / f'{job}.token'} (mode 0600)" in first
            second = (state / "sessions" / f"{job}.s2.start.log").read_text()
            assert "RECOVERED | counterdemo | local simulator | replayed 1 paid actions" in second
            token_file = state / "tokens" / f"{job}.token"
            assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
            token = token_file.read_text().strip()
            assert len(token) >= 20
            for session in (1, 2):
                for suffix in ("player.out", "player.err"):
                    assert token not in (state / "sessions" / f"{job}.s{session}.{suffix}").read_text()
                prompt = (state / "prompts" / f"{job}.s{session}.md").read_text()
                assert token not in prompt and f"Scripted session for counterdemo in {run}, budget 40, seed {seed}." in prompt
            report = json.loads((state / "sessions" / f"{job}.s2.player.out").read_text())
            assert report["num_turns"] == 3 and report["is_error"] is False
            transcript = report["transcript"]
            assert transcript[0]["command"] == ["assay", "status"]
            assert "STATUS | counterdemo | event 1 | progress 1/1 | paid actions 1 | NOT_FINISHED" in transcript[0]["stdout"]
            assert "OUTCOME | GAME_COMPLETE" in transcript[1]["stdout"]
            events = [json.loads(line) for line in (run / ".assay" / "events.jsonl").read_text().splitlines()]
            assert [event["id"] for event in events] == [0, 1, 2] and events[-1]["state"] == "WIN"
            assert json.loads((run / ".assay" / "broker.json").read_text())["status"] == "FINISHED"
        rows = [json.loads(line) for line in (state / "results.jsonl").read_text().splitlines()]
        assert [(row["job"], row["outcome"], row["paid_actions"], row["sessions"], row["dollars"], row["tokens"]["total"]) for row in rows] == [
            ("d-counterdemo-a-s1", "WIN", 2, 2, 0.06, 660), ("d-counterdemo-a-s2", "WIN", 2, 2, 0.06, 660),
        ]
        assert all(row["chain_head"] and row["receipts"] == {"GAME_COMPLETE": 1, "PREDICTED": 1} for row in rows)
        ledger = json.loads((state / "sessions" / "d-counterdemo-a-s1.json").read_text())
        assert [record["session"] for record in ledger] == [1, 2] and all(record["counted"] for record in ledger)
        assert all(record["exit_code"] == 0 and not record["timed_out"] for record in ledger)
        status = json.loads((state / "status.json").read_text())
        assert status["final"] == "drained" and status["done"] == ["d-counterdemo-a-s1", "d-counterdemo-a-s2"]
        # A rerun launches nothing: both jobs are finished.
        again = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        assert again.returncode == 0, again.stdout + again.stderr
        assert "SKIP | d-counterdemo-a-s1 | already WIN | paid actions 2" in again.stdout
        assert "SKIP | d-counterdemo-a-s2 | already WIN | paid actions 2" in again.stdout
        assert "LAUNCH" not in again.stdout
        # A summarize pass recomputes the rows and the summary from the state alone.
        summarized = subprocess.run([*command, "--summarize"], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        assert summarized.returncode == 0, summarized.stdout + summarized.stderr
        assert "LAUNCH" not in summarized.stdout and "SKIP" not in summarized.stdout
        assert "ARM | a | runs 2 | wins 2/2 1.00 [1.00, 1.00] | paid actions 2.0 [2.0, 2.0]" in summarized.stdout
        assert [json.loads(line)["outcome"] for line in (state / "results.jsonl").read_text().splitlines()] == ["WIN", "WIN"]
    finally:
        for run in runs:
            if run.exists():
                stop_run(run)
