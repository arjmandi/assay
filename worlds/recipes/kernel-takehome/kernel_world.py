"""The kernel-takehome world script: one paid action, EVALUATE, and the
frontier record it keeps.

    python3 kernel_world.py observe --recipe R --workdir W --state S
    python3 kernel_world.py act EVALUATE --recipe R --workdir W --state S    parameters on stdin

EVALUATE copies work/perf_takehome.py into a fresh temporary directory and
runs the evaluator of RSI-top's wrapper over it in two phases: eval_build.py
imports the candidate, which is agent-written code, and dumps the
instructions as JSON; eval_score.py, which never imports the candidate, runs
the frozen simulator on them. Both phases run with an environment cut down to
PATH, HOME, LANG and TMPDIR, in their own process group that is killed on a
timeout. A failed evaluation is a completed evaluation. Each completed one
appends a line to <state>/frontier.jsonl, and the observation is derived from
that record, so it reads the same after the daemon restarts.

kernel.json beside this file holds the pack's constants. checks/score.py
imports `evaluate` from here, so the clean re-score and the world measure
the same way.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EVALUATOR = Path("integrations") / "openevolve-takehome"
CANDIDATE = "perf_takehome.py"
KEPT_ENV = ("PATH", "HOME", "LANG", "TMPDIR")
ERROR_CHARS = 300
FRONTIER = "frontier.jsonl"


def constants(recipe_dir: Path) -> dict[str, Any]:
    return dict(json.loads((recipe_dir / "kernel.json").read_text()))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Outcome:
    """What the two phases made of one candidate."""

    valid: bool
    cycles: int | None
    failure_stage: str | None
    error: str | None
    trials: list[dict[str, Any]]


@dataclass(frozen=True)
class Evaluation:
    """One line of the frontier record, and the source of the observation."""

    n: int
    started_at: str
    at: str
    duration_seconds: float
    label: str
    code_sha256: str
    valid: bool
    cycles: int | None
    failure_stage: str | None
    error: str | None
    frontier_before: int
    improved: bool
    best_cycles: int
    trials: list[dict[str, Any]]


def _clean_env() -> dict[str, str]:
    return {key: os.environ[key] for key in KEPT_ENV if key in os.environ}


def run_isolated(argv: list[str], cwd: Path, timeout: float) -> tuple[int | None, str, str]:
    """Run argv in a process group of its own with the stripped environment.
    Returns the exit code, stdout and stderr; the code is None when the
    timeout killed the group."""
    process = subprocess.Popen(
        argv, cwd=cwd, env=_clean_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, errors="replace", start_new_session=True,
    )
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        return None, "", ""
    return process.returncode, out, err


def _tail(out: str, err: str) -> str:
    return (err.strip() or out.strip())[-ERROR_CHARS:]


def evaluate(candidate: Path, scripts: Path, takehome: Path, consts: dict[str, Any]) -> Outcome:
    """The wrapper's two phases over one candidate file. `scripts` holds
    eval_build.py and eval_score.py, `takehome` holds problem.py and
    frozen_problem.py."""
    with tempfile.TemporaryDirectory(prefix="kernel-eval-") as scratch:
        directory = Path(scratch)
        program = directory / CANDIDATE
        program.write_bytes(candidate.read_bytes())
        instrs = directory / "instrs.json"
        result = directory / "result.json"
        code, out, err = run_isolated(
            [sys.executable, "-B", str(scripts / "eval_build.py"), str(program), str(takehome), str(instrs)],
            directory, float(consts["build_timeout_seconds"]),
        )
        if code is None:
            return Outcome(False, None, "build_timeout", f"the build ran past {consts['build_timeout_seconds']} seconds", [])
        if code != 0 or not instrs.is_file():
            return Outcome(False, None, "build_error", _tail(out, err) or f"the build exited {code}", [])
        code, out, err = run_isolated(
            [sys.executable, "-B", str(scripts / "eval_score.py"), str(instrs), str(takehome),
             str(consts["trials"]), str(result)],
            directory, float(consts["score_timeout_seconds"]),
        )
        if code is None:
            return Outcome(False, None, "simulation_timeout", f"the simulation ran past {consts['score_timeout_seconds']} seconds", [])
        try:
            scored = json.loads(result.read_text())
            trials = list(scored.get("trials", []))
            ok = scored["ok"] is True
            cycles = scored.get("cycles")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return Outcome(False, None, "simulator_error", _tail(out, err) or f"the scorer exited {code} without a result", [])
        if code != 0:
            return Outcome(False, None, "simulator_error", _tail(out, err) or f"the scorer exited {code}", trials)
        if not ok or isinstance(cycles, bool) or not isinstance(cycles, int):
            return Outcome(False, None, "incorrect_output", str(scored.get("error") or "the output is not correct")[:ERROR_CHARS], trials)
        return Outcome(True, cycles, None, None, trials)


def evaluator_dirs(recipe_dir: Path, consts: dict[str, Any]) -> tuple[Path, Path]:
    scripts = recipe_dir / consts["wrapper"]["place"] / EVALUATOR
    return scripts, scripts / "vendor" / "takehome"


def digests(root: Path) -> dict[str, str]:
    """sha256 of problem.py and of every file under tests/, by relative path."""
    found: dict[str, str] = {}
    candidates = [root / "problem.py"]
    tests = root / "tests"
    if tests.is_dir():
        candidates.extend(path for path in sorted(tests.rglob("*")) if path.is_file() and "__pycache__" not in path.parts)
    for path in candidates:
        if path.is_file():
            found[path.relative_to(root).as_posix()] = sha256_bytes(path.read_bytes())
    return found


def rule_differences(task: Path, work: Path) -> list[str]:
    pristine, current = digests(task), digests(work)
    return (
        [f"changed {path}" for path in sorted(pristine) if path in current and current[path] != pristine[path]]
        + [f"missing {path}" for path in sorted(set(pristine) - set(current))]
        + [f"added {path}" for path in sorted(set(current) - set(pristine))]
    )


def read_frontier(state: Path) -> list[Evaluation]:
    path = state / FRONTIER
    if not path.is_file():
        return []
    return [Evaluation(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def append_frontier(state: Path, evaluation: Evaluation) -> None:
    line = (json.dumps(asdict(evaluation), sort_keys=True) + "\n").encode()
    descriptor = os.open(state / FRONTIER, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(descriptor, line)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def observe(recipe_dir: Path, workdir: Path, state: Path) -> dict[str, Any]:
    consts = constants(recipe_dir)
    records = read_frontier(state)
    last = records[-1] if records else None
    program = workdir / CANDIDATE
    return {
        "evaluations": len(records),
        "valid": last.valid if last else None,
        "cycles": last.cycles if last else None,
        "best_cycles": last.best_cycles if last else consts["baseline_cycles"],
        "improved": bool(last and last.improved),
        "gain": last.frontier_before - last.cycles if last and last.valid and last.cycles is not None else None,
        "failure_stage": last.failure_stage if last else None,
        "baseline_cycles": consts["baseline_cycles"],
        "rules_clean": not rule_differences(recipe_dir / consts["task"], workdir),
        "code_sha256": sha256_bytes(program.read_bytes()) if program.is_file() else None,
    }


def run_evaluate(recipe_dir: Path, workdir: Path, state: Path, params: dict[str, Any]) -> dict[str, Any]:
    consts = constants(recipe_dir)
    label = str(params.get("label", "")).strip()
    if not label:
        return {"status": "refused", "detail": "label names the change being measured"}
    differences = rule_differences(recipe_dir / consts["task"], workdir)
    if differences:
        return {
            "status": "refused",
            "detail": "work/tests/ and work/problem.py must stay as they were given; restore them. "
            + "; ".join(differences[:10]),
        }
    program = workdir / CANDIDATE
    if not program.is_file():
        return {"status": "refused", "detail": f"work/{CANDIDATE} is missing; restore it"}
    state.mkdir(parents=True, exist_ok=True)
    code = program.read_bytes()
    records = read_frontier(state)
    frontier = records[-1].best_cycles if records else int(consts["baseline_cycles"])
    started_at, began = now(), time.monotonic()
    scripts, takehome = evaluator_dirs(recipe_dir, consts)
    scratch = state / "evaluating.py"
    scratch.write_bytes(code)
    try:
        outcome = evaluate(scratch, scripts, takehome, consts)
    finally:
        scratch.unlink(missing_ok=True)
    improved = outcome.valid and outcome.cycles is not None and outcome.cycles < frontier
    evaluation = Evaluation(
        n=len(records) + 1, started_at=started_at, at=now(), duration_seconds=round(time.monotonic() - began, 3),
        label=label, code_sha256=sha256_bytes(code), valid=outcome.valid, cycles=outcome.cycles,
        failure_stage=outcome.failure_stage, error=outcome.error, frontier_before=frontier, improved=improved,
        best_cycles=outcome.cycles if improved and outcome.cycles is not None else frontier, trials=outcome.trials,
    )
    (state / "candidates").mkdir(exist_ok=True)
    (state / "candidates" / f"{evaluation.n}.py").write_bytes(code)
    if improved:
        pending = state / "best.py.tmp"
        pending.write_bytes(code)
        os.replace(pending, state / "best.py")
    append_frontier(state, evaluation)
    if not outcome.valid:
        detail = f"evaluation {evaluation.n} failed at {outcome.failure_stage}; the frontier stays {frontier} cycles"
    elif improved:
        detail = f"evaluation {evaluation.n}: {outcome.cycles} cycles, {frontier - (outcome.cycles or 0)} below the frontier of {frontier}"
    else:
        detail = f"evaluation {evaluation.n}: {outcome.cycles} cycles, no better than the frontier of {frontier}"
    return {"status": "ok", "detail": detail, "n": evaluation.n, "label": label, "valid": outcome.valid,
            "cycles": outcome.cycles, "failure_stage": outcome.failure_stage, "improved": improved,
            "best_cycles": evaluation.best_cycles}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=["observe", "act"])
    parser.add_argument("action", nargs="?")
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "observe":
        print(json.dumps(observe(args.recipe, args.workdir, args.state), sort_keys=True))
        return 0
    if args.action != "EVALUATE":
        raise SystemExit(f"unknown action {args.action!r}; this world's action is EVALUATE")
    params = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(run_evaluate(args.recipe, args.workdir, args.state, params), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
