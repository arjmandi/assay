"""The repo archetype (worlds/archetypes/repo): a repository copied into work/,
edited freely by the agent, with RUN_TESTS as the one paid action. Its
template runs through the real CLI and daemon: the failing tests in the
observation, a selector the world refuses, a fix in the tree, the repeated
passing run that wins, and the tree's hash on every result. The world script's
parts are checked in-process: the JUnit totals and the tree hash."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "worlds" / "archetypes" / "repo" / "template"
REPO_WORLD = REPO / "worlds" / "archetypes" / "repo" / "repo_world.py"


def _repo_world():
    spec = importlib.util.spec_from_file_location("repo_world", REPO_WORLD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data(run: Path) -> dict:
    lines = [line for line in (run / ".assay" / "events.jsonl").read_text().splitlines() if line.strip()]
    return json.loads(lines[-1])["observation"]


def test_the_repo_template_end_to_end(tmp_path):
    run = tmp_path / "repo"
    run.mkdir()
    try:
        started = run_cli(
            run, "start", "repo1", "--adapter", f"{TEMPLATE / 'adapter.py'}:factory",
            "--registry", str(TEMPLATE / "registry.json"), "--owner-token-file", str(tmp_path / "repo.token"),
        )
        assert started.returncode == 0, started.stderr
        # The tree changes outside the journal: no replay, one daemon long.
        assert "STARTED | repo1 | local simulator | single run | no action-idle lease | no replay recovery" in started.stdout
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["session"]["replayable"] is False
        assert config["public_info"]["archetype"] == "repo"
        assert sorted(path.name for path in (run / "work").iterdir()) == ["calc.py", "check_calc.py"]
        first = _data(run)
        assert first["workdir"] == "work" and first["runs"] == 0 and first["selector"] is None
        for name in ("failed", "failed_runs", "refusals", "tree"):
            assert run_cli(run, "state", "declare", name, "--path", name).returncode == 0
        failing = run_cli(
            run, "act", "RUN_TESTS", "--params", '{"selector": "check_calc.py", "repeat": 1}',
            "--predict", "ch failed = 2; ch failed_runs = 1",
        )
        assert failing.returncode == 0 and "RESULT | PREDICTED" in failing.stdout, failing.stdout
        observed = _data(run)
        assert observed["failing"] == ["check_calc::test_clamp_above", "check_calc::test_clamp_inside"]
        assert observed["tests"] == 5 and observed["exit_codes"] == [1] and observed["test_runs_total"] == 1
        assert observed["last_result"]["detail"] == "1 run(s) of check_calc.py: 0 passed, 1 failed"
        tree_before = observed["tree"]
        # A selector that would pass a flag to the runner is the world's refusal.
        refused = run_cli(
            run, "act", "RUN_TESTS", "--params", '{"selector": "-p evil", "repeat": 1}',
            "--predict", "ch refusals delta = 1",
        )
        assert refused.returncode == 0 and "RESULT | PREDICTED" in refused.stdout, refused.stdout
        assert _data(run)["last_result"]["status"] == "refused"
        # Editing is free: the agent fixes the tree, then pays for the run.
        calc = run / "work" / "calc.py"
        calc.write_text(calc.read_text().replace("min(value, low)", "min(value, high)"))
        won = run_cli(
            run, "act", "RUN_TESTS", "--params", '{"selector": "check_calc.py", "repeat": 2}',
            "--predict", "win; ch failed = 0; ch failed_runs = 0",
        )
        assert won.returncode == 0 and "RESULT | GAME_COMPLETE" in won.stdout, won.stdout
        final = _data(run)
        assert final["runs"] == 2 and final["pass_rate"] == 1 and final["failing"] == []
        assert final["tree"] != tree_before and final["test_runs_total"] == 3
        # No cache or bytecode lands in the tree the agent reads.
        assert sorted(path.name for path in (run / "work").iterdir()) == ["calc.py", "check_calc.py"]
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)


def test_junit_totals_and_failing_ids(tmp_path):
    module = _repo_world()
    report = tmp_path / "report.xml"
    report.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="3" failures="1" errors="1" skipped="0">'
        '<testcase classname="tests.test_a" name="test_ok"/>'
        '<testcase classname="tests.test_a" name="test_bad"><failure message="no"/></testcase>'
        '<testcase classname="tests.test_b" name="test_broken"><error message="boom"/></testcase>'
        "</testsuite></testsuites>"
    )
    assert module.junit_counts(report) == {
        "tests": 3, "failed": 2, "skipped": 0, "failing": ["tests.test_a::test_bad", "tests.test_b::test_broken"],
    }
    report.write_text("not xml")
    assert module.junit_counts(report) is None


def test_the_tree_hash_reads_content_and_skips_caches(tmp_path):
    module = _repo_world()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n")
    first = module.tree_hash(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"\0")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    assert module.tree_hash(tmp_path) == first
    (tmp_path / "pkg" / "a.py").write_text("x = 2\n")
    assert module.tree_hash(tmp_path) != first


def test_selectors_the_world_refuses(tmp_path):
    module = _repo_world()
    work, state = tmp_path / "work", tmp_path / "state"
    work.mkdir()
    for selector in ("-k x", "--co", "../outside.py", "tests; rm -rf /", ""):
        result = module.run_tests(TEMPLATE, work, state, {"selector": selector, "repeat": 1})
        assert result["status"] == "refused", selector
    result = module.run_tests(TEMPLATE, work, state, {"selector": "tests", "repeat": 6})
    assert result == {"status": "refused", "detail": "repeat runs from 1 to 5"}
