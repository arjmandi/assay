"""The flaky-test pack (worlds/recipes/flaky-test): the project's suite fails
under some hash seeds and passes under others, the checker tells the root-cause
fix from the fixes that only quiet the suite, and a fix through the real CLI
and daemon wins after twenty green runs and scores correct. The project's own
runner needs no pytest, so neither does anything here."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "worlds" / "recipes" / "flaky-test"
PROJECT = PACK / "project"
SCORE = PACK / "checks" / "score.py"
UNORDERED = "    categories = {order.category for order in orders}"
ROOT_CAUSE = "    categories = list(dict.fromkeys(order.category for order in orders))"
ALPHABETICAL = "    categories = sorted({order.category for order in orders})"

# Every pack's checker is checks/score.py: load this one by its path, under a
# name of its own, so another pack's checker never answers for it.
_spec = importlib.util.spec_from_file_location("flaky_test_score", SCORE)
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


def _tree(tmp_path: Path, name: str, replacement: str | None = None) -> Path:
    tree = tmp_path / name
    shutil.copytree(PROJECT, tree, ignore=shutil.ignore_patterns("__pycache__"))
    if replacement is not None:
        report = tree / "inventory" / "report.py"
        text = report.read_text()
        assert UNORDERED in text
        report.write_text(text.replace(UNORDERED, replacement))
    return tree


def _suite_passes(tree: Path, seed: int) -> bool:
    env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "runtests.py", "tests"],
                          cwd=tree, env=env, capture_output=True, text=True, timeout=120)
    return done.returncode == 0


def test_the_suite_fails_under_some_hash_seeds_and_passes_under_others(tmp_path):
    tree = _tree(tmp_path, "project")
    passing = sum(_suite_passes(tree, seed) for seed in range(12))
    assert 2 <= passing <= 10, passing
    fixed = _tree(tmp_path, "fixed", ROOT_CAUSE)
    assert all(_suite_passes(fixed, seed) for seed in range(4))


def test_the_checker_tells_the_root_cause_from_the_fixes_that_quiet_the_suite(tmp_path):
    assert score.score(_tree(tmp_path, "unfixed"), 6)["verdict"] in ("wrong", "flaky")
    assert score.score(_tree(tmp_path, "root-cause", ROOT_CAUSE), 6) == {
        "verdict": "correct", "tree": str(tmp_path / "root-cause"), "runs": 6, "passed": 6, "failing": {},
    }
    # The project's own tie is alphabetical, so a sort passes its suite on
    # every run; the checker's tests hold the documented order.
    alphabetical = _tree(tmp_path, "alphabetical", ALPHABETICAL)
    assert all(_suite_passes(alphabetical, seed) for seed in range(3))
    result = score.score(alphabetical, 6)
    assert result["verdict"] == "wrong"
    assert "tests.test_checker_contract::test_ties_keep_first_appearance_when_it_is_not_alphabetical" in result["failing"]
    # A weakened suite and a runner that always passes change nothing: the
    # checker puts the project's tests and runner back.
    quiet = _tree(tmp_path, "quiet")
    (quiet / "tests" / "test_report.py").write_text("def test_nothing():\n    pass\n")
    (quiet / "runtests.py").write_text("raise SystemExit(0)\n")
    assert all(_suite_passes(quiet, seed) for seed in range(3))
    assert score.score(quiet, 6)["verdict"] in ("wrong", "flaky")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert score.score(empty, 6)["verdict"] == "wrong"


def test_a_root_cause_fix_through_the_cli_wins_and_scores_correct(tmp_path):
    run = tmp_path / "flaky"
    run.mkdir()
    try:
        started = run_cli(run, "start", "flaky1", "--adapter", f"{PACK / 'adapter.py'}:factory",
                          "--registry", str(PACK / "registry.json"), "--owner-token-file", str(tmp_path / "flaky.token"))
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "state", "declare", "failed_runs", "--path", "failed_runs").returncode == 0
        first = run_cli(run, "act", "RUN_TESTS", "selector=tests", "repeat=20", "--predict", "ch failed_runs delta sign +")
        assert first.returncode == 0 and "RESULT | PREDICTED" in first.stdout, first.stdout + first.stderr
        events = [json.loads(line) for line in (run / ".assay" / "events.jsonl").read_text().splitlines() if line.strip()]
        data = events[-1]["observation"]
        assert data["runs"] == 20 and 0 < data["failed_runs"] < 20, data
        assert "tests.test_report::test_categories_that_tie_keep_their_first_appearance" in data["failing"]
        report = run / "work" / "inventory" / "report.py"
        report.write_text(report.read_text().replace(UNORDERED, ROOT_CAUSE))
        fixed = run_cli(run, "act", "RUN_TESTS", "selector=tests", "repeat=20", "--predict", "ch failed_runs = 0; win")
        assert fixed.returncode == 0 and "RESULT | GAME_COMPLETE" in fixed.stdout, fixed.stdout + fixed.stderr
        scored = subprocess.run([sys.executable, str(SCORE), "--run", str(run), "--seeds", "6"],
                                capture_output=True, text=True, timeout=300)
        assert scored.returncode == 0 and scored.stdout.startswith("SCORE | correct | 6/6 runs passed"), scored.stdout
    finally:
        stop_run(run)
