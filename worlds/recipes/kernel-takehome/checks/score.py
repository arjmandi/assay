"""The kernel-takehome checker: one candidate perf_takehome.py, re-scored in a
clean copy of the pinned take-home, so every arm of a comparison is scored the
same way whatever its run did to its own tree.

    python3 checks/score.py CANDIDATE.py [--json]
    python3 checks/score.py --run RUN_DIR [--json]     the run's best.py, else its work/perf_takehome.py

It copies the pinned take-home from upstream/ into a temporary directory,
records the hashes of problem.py and tests/, places only the candidate as
perf_takehome.py, scores it with the wrapper's two-phase evaluator (the one
the world uses), then runs the official tests/submission_tests.py there and
reports which thresholds passed. The hashes are checked again at the end,
because that step runs the candidate's code beside the tests. The exit status
is 0 when the candidate is valid and the rules hold, 1 when not, 2 for a usage
or setup error (prepare.py has not run, the candidate is missing).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

PACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACK))
import kernel_world  # noqa: E402

BEST_KNOWN_THRESHOLD = 1363
SUBMISSION_TIMEOUT_SECONDS = 600
TEST_LINE = re.compile(r"^(test\w+) \(.*\) \.\.\. (\w+)", re.MULTILINE)
N_CORES = re.compile(r"^N_CORES\s*=\s*1\s*$", re.MULTILINE)
IGNORED = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache")


class SetupError(Exception):
    """A refusal, printed as `ERROR | message`, exit 2."""


def thresholds(output: str) -> dict[str, bool]:
    return {name: result == "ok" for name, result in TEST_LINE.findall(output)}


def score(candidate: Path) -> dict[str, Any]:
    consts = kernel_world.constants(PACK)
    source = PACK / consts["takehome"]["place"]
    scripts, _ = kernel_world.evaluator_dirs(PACK, consts)
    if not candidate.is_file():
        raise SetupError(f"{candidate} is not a file")
    if not source.is_dir() or not (scripts / "eval_build.py").is_file():
        raise SetupError("upstream/ is missing; run prepare.py once first")
    with tempfile.TemporaryDirectory(prefix="kernel-score-") as scratch:
        copy = Path(scratch) / "takehome"
        shutil.copytree(source, copy, ignore=IGNORED)
        before = kernel_world.digests(copy)
        flat = Path(scratch) / "frozen"
        flat.mkdir()
        shutil.copy(copy / "problem.py", flat / "problem.py")
        shutil.copy(copy / "tests" / "frozen_problem.py", flat / "frozen_problem.py")
        (copy / kernel_world.CANDIDATE).write_bytes(candidate.read_bytes())
        outcome = kernel_world.evaluate(candidate, scripts, flat, consts)
        code, output = _submission_tests(copy)
        after = kernel_world.digests(copy)
        frozen = (copy / "tests" / "frozen_problem.py").read_text()
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    rules = {
        "tests_unchanged": not any(path.startswith("tests/") for path in changed),
        "problem_unchanged": "problem.py" not in changed,
        "n_cores_is_1": bool(N_CORES.search(frozen)),
    }
    return {
        "valid": outcome.valid,
        "cycles": outcome.cycles,
        "normalized": round(BEST_KNOWN_THRESHOLD / outcome.cycles, 4) if outcome.valid and outcome.cycles else None,
        "failure_stage": outcome.failure_stage,
        "error": outcome.error,
        "rules": rules,
        "submission_tests": {"exit": code, "thresholds": thresholds(output)},
    }


def run_candidate(run: Path) -> Path:
    best = run / ".assay" / "world_state" / "best.py"
    return best if best.is_file() else run / "work" / kernel_world.CANDIDATE


def _submission_tests(copy: Path) -> tuple[int | None, str]:
    # unittest reports each test on stderr; -v names every one, so a threshold that failed is told from one that passed.
    code, _, err = kernel_world.run_isolated(
        [sys.executable, "-E", "-s", "-B", "tests/submission_tests.py", "-v"], copy, SUBMISSION_TIMEOUT_SECONDS
    )
    return code, err


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("candidate", type=Path, nargs="?")
    parser.add_argument("--run", type=Path, help="a run directory: score its best.py, else work/perf_takehome.py")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    if (args.candidate is None) == (args.run is None):
        print("ERROR | name a candidate file or --run RUN_DIR", file=sys.stderr, flush=True)
        return 2
    candidate = args.candidate or run_candidate(args.run)
    try:
        result = score(candidate)
    except SetupError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
    good = bool(result["valid"]) and all(result["rules"].values())
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        passed = [name for name, ok in result["submission_tests"]["thresholds"].items() if ok]
        failed = [name for name, ok in result["submission_tests"]["thresholds"].items() if not ok]
        verdict = "valid" if good else "invalid"
        print(f"SCORE | {verdict} | cycles {result['cycles']} | normalized {result['normalized']} | rules {result['rules']}")
        print(f"THRESHOLDS | passed {', '.join(passed) or 'none'} | failed {', '.join(failed) or 'none'}")
        if result["error"]:
            print(f"DETAIL | {result['failure_stage']} | {result['error']}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
