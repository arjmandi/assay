"""The compare mode of tools/eval (compare.py), behind `/assay:compare`.

How a `/goal` session ended, read from its stream; the designs the packs
carry; the goal arm's bare copy of a world, whose world.py runs the recipe's
own commands and logs every act, and the replay that names a copy changed by
hand; and both arms on the hidden-grammar pack with stand-in players through
the real launcher and daemon: the pre-registration, the rows, a rerun that
skips what ran and refuses a changed design. Nothing here starts a model."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

from conftest import stop_run

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
compare = importlib.import_module("tools.eval.compare")
eval_package = importlib.import_module("tools.eval")

GRAMMAR = REPO / "worlds" / "recipes" / "hidden-grammar"
FLAKY = REPO / "worlds" / "recipes" / "flaky-test"
LAUNCHER = REPO / "bin" / "assay"

STAND_IN_GOAL = f'''import json, subprocess, sys
from pathlib import Path
seed, mode = int(sys.argv[1]), sys.argv[2]
sys.stdin.read()
example = Path("world/examples.txt").read_text().splitlines()[1]
subprocess.run([sys.executable, "world.py", "act", "ASK", json.dumps({{"sentence": example}})], check=True, capture_output=True)
if mode == "edit":
    with open("world/asked.tsv", "a") as handle:
        handle.write("a sentence nobody asked\\tacceptable\\n")
Path("accept.py").write_text("import sys\\nsys.path.insert(0, {str(GRAMMAR)!r})\\nimport grammar\\n\\n"
                             "def accept(s):\\n    return grammar.accepts(%d, s)\\n" % seed)
if mode == "cap":
    print(json.dumps({{"type": "system", "subtype": "notification", "key": "stop-hook-block-cap"}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.03, "num_turns": 3,
                  "usage": {{"input_tokens": 10, "output_tokens": 5}}}}))
'''
ASSAY_SCRIPT = (
    "Scripted.\n"
    "assay state declare last_ok --path last_ok\n"
    "assay act ASK --params '{\"sentence\": \"no such words\"}' --predict \"ch last_ok = false\"\n"
    "note: STOP: the classifier is written\n"
)


def _line(**record: object) -> str:
    return json.dumps(record)


def test_how_a_goal_session_ended():
    met = "\n".join([_line(type="system", subtype="init"),
                     _line(type="user", message={"content": [{"type": "text", "text": "Stop hook feedback:\n[x]: no"}]}),
                     _line(type="result", subtype="success", is_error=False, total_cost_usd=0.12, num_turns=4,
                           usage={"input_tokens": 3, "output_tokens": 2}, result="done")])
    assert compare.goal_ending(met, timed_out=False) == {
        "ended_by": "met", "dollars": 0.12, "tokens": 5, "turns": 4, "goal_feedback": 1, "result": "done",
    }
    capped = met.replace('"subtype": "init"', '"subtype": "notification", "key": "stop-hook-block-cap"')
    assert compare.goal_ending(capped, timed_out=False)["ended_by"] == "cap"
    budget = _line(type="result", subtype="error_max_budget_usd", is_error=True, total_cost_usd=2.0)
    assert compare.goal_ending(budget, timed_out=False)["ended_by"] == "budget"
    assert compare.goal_ending("not json\n", timed_out=False)["ended_by"] == "error"
    assert compare.goal_ending(met, timed_out=True)["ended_by"] == "time"


def test_the_packs_carry_their_designs_and_a_service_or_a_plain_pack_is_refused(tmp_path):
    for pack in (GRAMMAR, FLAKY):
        design = compare.load_design(pack)
        assert design.raw["check"]["assay"][-3:] == ["--run", "{run_dir}", "--json"]
        assert design.mode == ("copy" if pack == GRAMMAR else "worktree")
    for pack, message in ((REPO / "worlds" / "recipes" / "legacy-api", "no compare.json"),):
        try:
            compare.load_design(pack)
        except eval_package.EvalError as error:
            assert message in str(error)
        else:
            raise AssertionError(pack)


def _instance(tmp_path: Path, pack: Path, **options: object) -> object:
    return compare.Compare(
        compare.load_design(pack), state_dir=tmp_path / "state", seeds=[2], model="haiku", max_usd=1.0, hours=0.1,
        max_sessions=1, arms=["assay", "goal"], launcher=[str(LAUNCHER)], **options,
    )


def test_the_goal_arm_gets_a_bare_copy_whose_acts_are_logged_and_replayed(tmp_path):
    instance = _instance(tmp_path, GRAMMAR)
    directory, config = instance.goal_world(2)
    assert sorted(path.name for path in (directory / "world").iterdir()) == ["asked.tsv", "examples.txt", "lexicon.txt"]
    example = (directory / "world" / "examples.txt").read_text().splitlines()[1]
    acted = subprocess.run([sys.executable, "world.py", "act", "ASK", json.dumps({"sentence": example})],
                           cwd=directory, capture_output=True, text=True, timeout=60)
    assert acted.returncode == 0, acted.stderr
    result, observed = (json.loads(line) for line in acted.stdout.splitlines())
    assert result["acceptable"] is True and observed["asked"] == 1
    refused = subprocess.run([sys.executable, "world.py", "act", "TELL", "{}"], cwd=directory, capture_output=True, text=True)
    assert refused.returncode != 0 and "the actions are ASK" in refused.stderr
    assert Path(config["log"]).read_text().count("\n") == 1 and not str(config["log"]).startswith(str(directory))
    assert instance.replayed(2, config) is True
    with (directory / "world" / "asked.tsv").open("a") as handle:
        handle.write("made up\tacceptable\n")
    assert instance.replayed(2, config) is False
    # A worktree is the agent's to edit: no replay, and the project's runner is there.
    flaky = _instance(tmp_path / "flaky", FLAKY)
    tree, flaky_config = flaky.goal_world(1)
    assert (tree / "work" / "runtests.py").is_file() and flaky.replayed(1, flaky_config) is None
    ran = subprocess.run([sys.executable, "world.py", "act", "RUN_TESTS", json.dumps({"selector": "tests", "repeat": 2})],
                         cwd=tree, capture_output=True, text=True, timeout=120)
    assert ran.returncode == 0 and json.loads(ran.stdout.splitlines()[-1])["runs"] == 2, ran.stderr


def _compare(tmp_path: Path, *extra: str, mode: str = "plain") -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    stand_in = tmp_path / "stand_in_goal.py"
    stand_in.write_text(STAND_IN_GOAL)
    prompt = tmp_path / "assay_prompt.md"
    prompt.write_text(ASSAY_SCRIPT)
    argv = [sys.executable, "-m", "tools.eval", "compare", "--pack", str(GRAMMAR), "--seeds", "2",
            "--state-dir", str(tmp_path / "state"), "--assay", str(LAUNCHER),
            "--goal-player", f"{{python}} {stand_in} {{seed}} {mode}",
            "--assay-player", "{python} {repo}/tools/eval/fake_player", "--assay-prompt", str(prompt), *extra]
    env = {**__import__("os").environ, "ASSAY_PYTHON": sys.executable}
    env.pop("ASSAY_OWNER_TOKEN_FILE", None)
    return subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, timeout=300)


def test_both_arms_with_stand_in_players_through_the_real_launcher(tmp_path):
    tmp_path = tmp_path.resolve()
    run = tmp_path / "state" / "runs" / "assay-s2"
    try:
        done = _compare(tmp_path)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "ROW | assay | seed 2 | ended SELF_STOP | claimed True | checker missing | $0.02" in done.stdout
        assert "ROW | goal | seed 2 | ended met | claimed True | checker correct | $0.03" in done.stdout
        assert "ARM | assay | runs 1 | claimed 1 | checker passed 0 | false completions 1 | $0.02" in done.stdout
        rows = [json.loads(line) for line in (tmp_path / "state" / "results.jsonl").read_text().splitlines()]
        assay, goal = rows
        assert (assay["arm"], assay["paid_actions"], assay["host"], assay["sessions"]) == ("assay", 1, "fake_player", 1)
        assert (goal["arm"], goal["acts"], goal["copy_replays"], goal["host"]) == ("goal", 1, True, "stand_in_goal.py")
        assert goal["check"]["coverage"]["asked"] == 1
        registered = json.loads((tmp_path / "state" / "PREREGISTRATION.json").read_text())
        assert registered["seeds"] == [2] and registered["model"] == "haiku" and registered["hypothesis"].startswith("Claude Code")
        summary = (tmp_path / "state" / "COMPARE.md").read_text()
        assert "| assay | 1 | 1 | 0 | 1 | $0.02 |" in summary and "| goal | 1 | 1 | 1 | 0 | $0.03 |" in summary
        report = json.loads(Path(assay["report"]).with_suffix(".json").read_text())
        assert report["check"]["result"]["verdict"] == "missing" and report["integrity"]["audit"]["current"] is True
        assert json.loads((run / ".assay" / "broker.json").read_text())["status"] != "RUNNING"
        # A rerun skips what ran; a changed design is refused.
        again = _compare(tmp_path)
        assert again.returncode == 0 and "SKIP | assay | seed 2" in again.stdout and "SKIP | goal | seed 2" in again.stdout
        changed = _compare(tmp_path, "--model", "sonnet")
        assert changed.returncode == 2 and "pre-registered with another design (model differ)" in changed.stderr
    finally:
        stop_run(run)


def test_a_goal_session_ended_by_the_cap_or_with_an_edited_copy(tmp_path):
    tmp_path = tmp_path.resolve()
    for mode, ended, claimed, replays in (("cap", "cap", False, True), ("edit", "met", True, False)):
        done = _compare(tmp_path / mode, "--arms", "goal", mode=mode)
        assert done.returncode == 0, done.stdout + done.stderr
        row = json.loads((tmp_path / mode / "state" / "results.jsonl").read_text())
        assert (row["ended_by"], row["claimed"], row["copy_replays"]) == (ended, claimed, replays)
    assert "The goal arm changed its copy outside world.py at seed(s) 2." in (
        tmp_path / "edit" / "state" / "COMPARE.md").read_text()


def test_the_dry_run_writes_nothing(tmp_path, capsys):
    assert compare.main(["--pack", str(FLAKY), "--state-dir", str(tmp_path / "state"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "COMPARE | flaky-test | arms assay, goal | seeds [1, 2, 3] | model haiku" in out
    assert "--strict-mcp-config" in out and "PROMPT | goal arm:\n/goal the whole test suite" in out
    assert not (tmp_path / "state").exists()
    assert compare.main(["--pack", str(FLAKY), "--arms", "baseline", "--dry-run"]) == 2
