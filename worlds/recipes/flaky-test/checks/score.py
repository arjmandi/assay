"""The flaky-test checker: a final tree against the project's contract. It
imports nothing from the harness, so it scores any arm of a comparison the same
way, a run under ASSAY or a baseline that edited its own copy of the project.

    python3 checks/score.py --run RUN_DIR             the tree in RUN_DIR/work
    python3 checks/score.py --tree DIR                any copy of the project
    add --seeds N (default 30) and --json for one JSON object

It copies the tree to a scratch directory, puts the project's original tests
and test runner back in place of whatever the tree's became, adds the
checker's own tests (hidden_test_report.py beside this file), and runs them
all once under each of PYTHONHASHSEED 0 to N-1, with the standard library
alone. The hash seed is what decides the order of a
set of strings, so a fix that only made the failure rarer, a retry, a skip,
an edited expectation or a pinned seed in the tree does not pass. The verdict
is correct when every run passes, flaky when some do, and wrong when none do.
The exit status is 0 for correct and 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PRISTINE_TESTS = HERE.parent / "project" / "tests"
PRISTINE_RUNNER = HERE.parent / "project" / "runtests.py"
HIDDEN = HERE / "hidden_test_report.py"
IGNORED = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git")


def score(tree: Path, seeds: int = 30) -> dict[str, Any]:
    if not (tree / "inventory").is_dir():
        return {"verdict": "wrong", "tree": str(tree), "detail": "the tree holds no inventory package", "runs": 0, "passed": 0}
    with tempfile.TemporaryDirectory(prefix="flaky-score-") as scratch:
        copy = Path(scratch) / "tree"
        shutil.copytree(tree, copy, ignore=IGNORED, symlinks=False)
        if (copy / "tests").exists():
            shutil.rmtree(copy / "tests")
        shutil.copytree(PRISTINE_TESTS, copy / "tests", ignore=IGNORED)
        shutil.copy(HIDDEN, copy / "tests" / "test_checker_contract.py")
        shutil.copy(PRISTINE_RUNNER, copy / "runtests.py")
        failing: dict[str, int] = {}
        passed = 0
        for seed in range(seeds):
            env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONDONTWRITEBYTECODE": "1"}
            junit = Path(scratch) / f"junit-{seed}.xml"
            done = subprocess.run(
                [sys.executable, "runtests.py", "tests", "--junit", str(junit)],
                cwd=copy, env=env, capture_output=True, text=True, timeout=120, check=False,
            )
            if done.returncode == 0:
                passed += 1
                continue
            for test in _failed(junit):
                failing[test] = failing.get(test, 0) + 1
    verdict = "correct" if passed == seeds else "wrong" if passed == 0 else "flaky"
    return {
        "verdict": verdict,
        "tree": str(tree),
        "runs": seeds,
        "passed": passed,
        "failing": dict(sorted(failing.items())),
    }


def _failed(junit: Path) -> list[str]:
    import xml.etree.ElementTree as ElementTree

    try:
        root = ElementTree.parse(junit).getroot()
    except (OSError, ElementTree.ParseError):
        return ["(the run wrote no report)"]
    found = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            found.append(f"{case.get('classname')}::{case.get('name')}")
    return found or ["(the run failed outside the tests)"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="score a flaky-test tree against the project's contract")
    parser.add_argument("--run", type=Path, help="a run directory: the tree is its work/")
    parser.add_argument("--tree", type=Path, help="any copy of the project")
    parser.add_argument("--seeds", type=int, default=30, help="the hash seeds to run under, 0 to N-1")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if (args.run is None) == (args.tree is None):
        parser.error("give exactly one of --run and --tree")
    tree = args.tree if args.tree is not None else args.run / "work"
    result = score(tree.resolve(), args.seeds)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"SCORE | {result['verdict']} | {result['passed']}/{result['runs']} runs passed")
        for test, count in result.get("failing", {}).items():
            print(f"FAIL | {test} | {count} run(s)")
    return 0 if result["verdict"] == "correct" else 1


if __name__ == "__main__":
    raise SystemExit(main())
