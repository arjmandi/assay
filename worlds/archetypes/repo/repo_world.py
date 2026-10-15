"""The repo archetype's world script: the tests of a working tree as a world.

The agent edits the tree at work/ freely; reading and editing are free. A paid
action, RUN_TESTS, runs the recipe's test command over the tree as it stands,
`repeat` times, and the observation reports what the runs showed and a hash of
the tree they ran on, so every result in the journal names its input.

    python3 repo_world.py observe --recipe R --workdir W --state S
    python3 repo_world.py act RUN_TESTS --recipe R --workdir W --state S    parameters on stdin

The recipe's `tests` block (worlds/README.md):

    "tests": {"command": [...], "timeout_seconds": 120, "max_repeat": 5}

`command` is an argv list whose words may carry {python} (this interpreter),
{selector} (the RUN_TESTS parameter), {junit} (a JUnit XML report path the
command should write, as `pytest --junitxml` does) and {workdir}. A run passes
when the command exits 0 and, when it wrote a report, the report holds at
least one test. A command that writes no report still counts by exit code.

The tests run code the agent wrote, as the daemon's user, with the daemon's
environment: keep secrets out of that environment, and run the daemon in a
container or a throwaway checkout where that matters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

SELECTOR = re.compile(r"^[A-Za-z0-9_./:\[\]\-]+$")
PLACEHOLDER = re.compile(r"\{([a-z]+)\}")
PLACEHOLDERS = frozenset({"python", "selector", "junit", "workdir"})
UNHASHED = frozenset(
    {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis", ".venv", "node_modules"}
)
LAST_RUN = "last_run.json"
FAILING_SHOWN = 20
DEFAULTS: dict[str, Any] = {
    "selector": None,
    "runs": 0,
    "passed_runs": 0,
    "failed_runs": 0,
    "pass_rate": None,
    "tests": None,
    "failed": None,
    "skipped": None,
    "failing": [],
    "timed_out_runs": 0,
    "exit_codes": [],
    "test_runs_total": 0,
}


def tests_block(recipe_dir: Path) -> dict[str, Any]:
    recipe = json.loads((recipe_dir / "recipe.json").read_text())
    block = recipe.get("tests")
    if not isinstance(block, dict):
        raise SystemExit(f"{recipe_dir / 'recipe.json'}: the repo archetype needs a tests block")
    command = block.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(word, str) and word for word in command):
        raise SystemExit("tests.command: an argv list of non-empty strings")
    for word in command:
        for name in PLACEHOLDER.findall(word):
            if name not in PLACEHOLDERS:
                raise SystemExit(f"tests.command: {{{name}}} is not one of {sorted(PLACEHOLDERS)}")
    timeout = block.get("timeout_seconds", 120)
    repeat = block.get("max_repeat", 5)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise SystemExit("tests.timeout_seconds: a positive number")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise SystemExit("tests.max_repeat: a positive integer")
    return {"command": command, "timeout_seconds": float(timeout), "max_repeat": repeat}


def tree_hash(workdir: Path) -> str:
    """A content hash of the working tree: every file's path and bytes, in
    path order, version control and caches left out."""
    digest = hashlib.sha256()
    for path in sorted(_files(workdir)):
        relative = path.relative_to(workdir).as_posix()
        digest.update(relative.encode() + b"\0")
        if path.is_symlink():
            digest.update(b"link:" + os.readlink(path).encode())
        else:
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


def _files(directory: Path) -> list[Path]:
    found: list[Path] = []
    for entry in directory.iterdir():
        if entry.name in UNHASHED:
            continue
        if entry.is_dir() and not entry.is_symlink():
            found.extend(_files(entry))
        else:
            found.append(entry)
    return found


def junit_counts(report: Path) -> dict[str, Any] | None:
    """The totals of a JUnit XML report and its failing test ids, or None when
    the file cannot be read as one."""
    try:
        root = ElementTree.parse(report).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = failures = skipped = 0
    for suite in suites:
        tests += int(suite.get("tests", "0") or 0)
        failures += int(suite.get("failures", "0") or 0) + int(suite.get("errors", "0") or 0)
        skipped += int(suite.get("skipped", "0") or 0)
    failing = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failing.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return {"tests": tests, "failed": failures, "skipped": skipped, "failing": failing}


def observe(workdir: Path, state: Path) -> dict[str, Any]:
    data = dict(DEFAULTS)
    last = state / LAST_RUN
    if last.is_file():
        data.update(json.loads(last.read_text()))
    data["tree"] = tree_hash(workdir)
    return data


def run_tests(recipe_dir: Path, workdir: Path, state: Path, params: dict[str, Any]) -> dict[str, Any]:
    block = tests_block(recipe_dir)
    selector = str(params.get("selector", ""))
    repeat = int(params.get("repeat", 1))
    if not SELECTOR.fullmatch(selector) or selector.startswith("-") or ".." in Path(selector).parts:
        return {
            "status": "refused",
            "detail": "a selector names tests inside the tree, such as tests or tests/test_x.py::test_y, "
            "and never starts with a dash",
        }
    if not 1 <= repeat <= block["max_repeat"]:
        return {"status": "refused", "detail": f"repeat runs from 1 to {block['max_repeat']}"}
    state.mkdir(parents=True, exist_ok=True)
    exits: list[int | None] = []
    passes: list[bool] = []
    reports: list[dict[str, Any]] = []
    for index in range(repeat):
        report = state / f"junit-{index}.xml"
        report.unlink(missing_ok=True)
        values = {"python": sys.executable, "selector": selector, "junit": str(report), "workdir": str(workdir)}
        argv = [PLACEHOLDER.sub(lambda match: values[match.group(1)], word) for word in block["command"]]
        try:
            completed = subprocess.run(
                argv, cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=block["timeout_seconds"], env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, check=False,
            )
            exit_code: int | None = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = None
        except OSError as error:
            raise SystemExit(f"the test command could not start: {error}") from error
        counts = junit_counts(report) if report.is_file() else None
        exits.append(exit_code)
        passes.append(exit_code == 0 and (counts is None or counts["tests"] > 0))
        reports.append(counts or {})
    previous = json.loads((state / LAST_RUN).read_text()) if (state / LAST_RUN).is_file() else {}
    last_counts = reports[-1]
    failing = sorted({test for counts in reports for test in counts.get("failing", [])})
    passed_runs = sum(passes)
    summary = {
        "selector": selector,
        "runs": repeat,
        "passed_runs": passed_runs,
        "failed_runs": repeat - passed_runs,
        "pass_rate": round(passed_runs / repeat, 4),
        "tests": last_counts.get("tests"),
        "failed": last_counts.get("failed"),
        "skipped": last_counts.get("skipped"),
        "failing": failing[:FAILING_SHOWN],
        "timed_out_runs": sum(1 for exit_code in exits if exit_code is None),
        "exit_codes": exits,
        "test_runs_total": int(previous.get("test_runs_total", 0)) + repeat,
    }
    (state / LAST_RUN).write_text(json.dumps(summary, sort_keys=True) + "\n")
    return {"status": "ok", "detail": f"{repeat} run(s) of {selector}: {passed_runs} passed, {repeat - passed_runs} failed"}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["observe", "act"])
    parser.add_argument("action", nargs="?")
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "observe":
        print(json.dumps(observe(args.workdir, args.state), sort_keys=True))
        return 0
    if args.action != "RUN_TESTS":
        raise SystemExit(f"unknown action {args.action!r}; the repo archetype's action is RUN_TESTS")
    params = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(run_tests(args.recipe, args.workdir, args.state, params), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
