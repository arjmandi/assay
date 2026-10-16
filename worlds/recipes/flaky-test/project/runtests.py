"""The project's test runner, standard library only, so the tests run under
any interpreter, pytest or not.

    python3 runtests.py SELECTOR [--junit FILE]

It runs in the tree (its current directory) and collects the test functions
(`test_*`) of the files the selector names: a directory (every
`tests/test_*.py` under it), a file, or `file::name` for one test. Each test
runs once, in collection order; an AssertionError is a failure and any other
exception an error. It writes a JUnit XML report when asked, and exits 0 when
at least one test ran and none failed, 1 otherwise. Every invocation is a new
interpreter, so Python's hash randomization differs from run to run, which is
what makes the project flaky."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import traceback
from pathlib import Path
from xml.sax.saxutils import quoteattr


def collect(selector: str) -> list[tuple[str, str, object]]:
    path_text, _, only = selector.partition("::")
    target = Path(path_text)
    files = sorted(target.rglob("test_*.py")) if target.is_dir() else [target]
    found = []
    for file in files:
        name = ".".join(file.with_suffix("").parts)
        spec = importlib.util.spec_from_file_location(name, file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        for attr, value in vars(module).items():
            if attr.startswith("test_") and callable(value) and (not only or attr == only):
                found.append((name, attr, value))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run the test functions a selector names")
    parser.add_argument("selector")
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path.cwd()))
    cases = []
    try:
        tests = collect(args.selector)
    except Exception:  # noqa: BLE001 - a test file that does not import is an error of the run
        tests = []
        cases.append(("collection", args.selector, "error", traceback.format_exc(limit=3), 0.0))
    for module, name, test in tests:
        began = time.perf_counter()
        try:
            test()  # type: ignore[operator]
            outcome, detail = "passed", ""
        except AssertionError as error:
            outcome, detail = "failure", f"AssertionError: {error}"
        except Exception:  # noqa: BLE001
            outcome, detail = "error", traceback.format_exc(limit=3)
        cases.append((module, name, outcome, detail, time.perf_counter() - began))
    failed = sum(1 for case in cases if case[2] != "passed")
    for module, name, outcome, detail, _ in cases:
        if outcome != "passed":
            print(f"{outcome.upper()} {module}::{name}\n{detail}")
    print(f"{len(cases) - failed} passed, {failed} failed")
    if args.junit is not None:
        lines = [f'<testsuite name="runtests" tests="{len(cases)}" failures="{failed}" errors="0" skipped="0">']
        for module, name, outcome, detail, seconds in cases:
            lines.append(f"  <testcase classname={quoteattr(module)} name={quoteattr(name)} time=\"{seconds:.4f}\">")
            if outcome != "passed":
                lines.append(f"    <{outcome} message={quoteattr(detail.splitlines()[-1] if detail else outcome)}/>")
            lines.append("  </testcase>")
        lines.append("</testsuite>")
        args.junit.write_text("\n".join(lines) + "\n")
    return 0 if cases and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
