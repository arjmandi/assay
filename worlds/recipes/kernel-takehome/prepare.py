"""The operator's one-time step: fetch the two upstream repositories at their
pinned commits into upstream/ and build task/, the tree the agent gets.

    python3 prepare.py                                     clone both from GitHub
    python3 prepare.py --takehome DIR --wrapper DIR        copy from local clones, no network
    add --allow-unpinned to skip the check that a clone is at its pinned commit

Nothing from either repository is part of this pack: the take-home has no
licence and its terms forbid redistributing solutions, so upstream/ and
task/ are ignored by git. The evaluator must score the same way for every
arm of a comparison, so the wrapper's vendored problem.py and
frozen_problem.py have to match the take-home's byte for byte, and prepare
refuses otherwise. Run it twice and the second run changes nothing.
Standard library only; an error is `ERROR | message`, exit 2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK_FILES = ("perf_takehome.py", "problem.py", "Readme.md")
EVALUATOR_VENDOR = Path("integrations") / "openevolve-takehome" / "vendor" / "takehome"
IGNORED = shutil.ignore_patterns("__pycache__", ".pytest_cache")


class PrepareError(Exception):
    """A refusal, printed as `ERROR | message`, exit 2."""


def git(directory: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, check=False)
    except OSError as error:
        raise PrepareError(f"git could not start: {error}") from error
    if done.returncode != 0:
        raise PrepareError(f"git {' '.join(args)} failed in {directory.name}: {done.stderr.strip()[-300:]}")
    return done.stdout.strip()


def head(directory: Path) -> str | None:
    try:
        return git(directory, "rev-parse", "HEAD")
    except PrepareError:
        return None


def fetch(url: str, commit: str, place: Path, source: Path | None, allow_unpinned: bool) -> str | None:
    """Put the repository at `place` on its pinned commit and return that
    commit, or None for an unpinned copy that is not a git tree."""
    if place.exists() and head(place) != commit:
        shutil.rmtree(place)
    if not place.exists():
        place.parent.mkdir(parents=True, exist_ok=True)
        if source is not None:
            if not source.is_dir():
                raise PrepareError(f"{source} is not a directory")
            shutil.copytree(source, place, symlinks=True, ignore=IGNORED)
        else:
            try:
                done = subprocess.run(["git", "clone", "--quiet", url, str(place)], capture_output=True, text=True, check=False)
            except OSError as error:
                raise PrepareError(f"git could not start: {error}") from error
            if done.returncode != 0:
                raise PrepareError(f"cloning {url} failed: {done.stderr.strip()[-300:]}")
    actual = head(place)
    if actual is not None and actual != commit and source is None:
        git(place, "checkout", "--quiet", "--detach", commit)
        actual = head(place)
    if actual != commit and not allow_unpinned:
        raise PrepareError(f"{place.name} is at {actual or 'no git commit'}, not the pinned {commit}; pass --allow-unpinned to accept it")
    return actual


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_evaluator(takehome: Path, wrapper: Path) -> None:
    pairs = (("problem.py", "problem.py"), ("frozen_problem.py", "tests/frozen_problem.py"))
    for vendored, official in pairs:
        left, right = wrapper / EVALUATOR_VENDOR / vendored, takehome / official
        for path in (left, right):
            if not path.is_file():
                raise PrepareError(f"{path} does not exist")
        if sha256(left) != sha256(right):
            raise PrepareError(
                f"the wrapper's vendored {vendored} differs from the take-home's {official}; "
                "the evaluator would score a different machine"
            )


def build_task(takehome: Path, task: Path) -> dict[str, str]:
    if task.exists():
        shutil.rmtree(task)
    task.mkdir(parents=True)
    for name in TASK_FILES:
        if not (takehome / name).is_file():
            raise PrepareError(f"{takehome / name} does not exist")
        shutil.copy2(takehome / name, task / name)
    if not (takehome / "tests").is_dir():
        raise PrepareError(f"{takehome / 'tests'} does not exist")
    shutil.copytree(takehome / "tests", task / "tests", ignore=IGNORED)
    return {path.relative_to(task).as_posix(): sha256(path) for path in sorted(task.rglob("*")) if path.is_file()}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--takehome", type=Path, help="a local clone of the take-home to copy instead of cloning")
    parser.add_argument("--wrapper", type=Path, help="a local clone of the wrapper to copy instead of cloning")
    parser.add_argument("--allow-unpinned", action="store_true", help="accept a clone that is not at its pinned commit")
    args = parser.parse_args(argv)
    try:
        consts = json.loads((HERE / "kernel.json").read_text())
        places = {}
        commits = {}
        for key, source in (("takehome", args.takehome), ("wrapper", args.wrapper)):
            entry = consts[key]
            places[key] = HERE / entry["place"]
            commits[key] = fetch(entry["url"], entry["commit"], places[key], source, args.allow_unpinned)
        check_evaluator(places["takehome"], places["wrapper"])
        files = build_task(places["takehome"], HERE / consts["task"])
        manifest = {"takehome_commit": commits["takehome"], "wrapper_commit": commits["wrapper"], "task": files}
        (HERE / "upstream" / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    except PrepareError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
    print(f"PREPARED | {len(files)} files in {consts['task']}/ | take-home {commits['takehome']} | wrapper {commits['wrapper']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
