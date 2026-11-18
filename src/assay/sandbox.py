"""The one place agent-written code runs.

A verifier or an extractor is a pure function of the observations it is
given, and the sandbox is what keeps it one: a copy of the stored program runs
in a fresh process (`python -I`, an empty environment, an empty scratch
directory as its working directory) with the arguments as JSON on stdin and
one JSON line on stdout. It gets a CPU limit, a wall clock, a 1 MB cap on any
file it writes (its output included), no child processes and, on Linux, 512 MB
of memory. Nothing it receives names the run directory. A program that
crashes, runs out of time or prints something else is reported as such and
never raises here.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

TIMEOUT_SECONDS = 5.0

_RUNNER = """\
import os, resource, sys
for name, limit in {limits!r}:
    resource.setrlimit(getattr(resource, name), (limit, limit))
os.environ.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
import importlib.util, json
call, args = json.loads(sys.stdin.read())
spec = importlib.util.spec_from_file_location("program", "program.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = getattr(module, call)(*args)
if call == "verify":
    result = {{"ok": bool(result[0]), "actual": str(result[1])}}
print("\\n" + json.dumps(result))
"""


def _limits(timeout: float) -> list[tuple[str, int]]:
    limits = [("RLIMIT_CPU", max(1, math.ceil(timeout))), ("RLIMIT_FSIZE", 1 << 20), ("RLIMIT_NPROC", 1)]
    if sys.platform.startswith("linux"):
        limits.append(("RLIMIT_AS", 512 << 20))
    return limits


def run(program: Path, call: str, args: list[Any], timeout: float = TIMEOUT_SECONDS) -> tuple[bool, Any]:
    """Call `call(*args)` of the stored program. `(True, result)` with the
    JSON value it returned, or `(False, reason)`."""
    with tempfile.TemporaryDirectory(prefix="assay-sandbox-") as raw:
        scratch = Path(raw)
        try:
            shutil.copyfile(program, scratch / "program.py")
        except OSError:
            return False, f"stored program {program.stem[:12]} missing"
        (scratch / "runner.py").write_text(_RUNNER.format(limits=_limits(timeout)))
        with (scratch / "out").open("wb") as out, (scratch / "err").open("wb") as err:
            try:
                completed = subprocess.run(  # noqa: S603 - the sandboxed run itself
                    [sys.executable, "-I", "runner.py"],
                    input=json.dumps([call, args]).encode(),
                    stdout=out,
                    stderr=err,
                    cwd=scratch,
                    env={},
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return False, f"timed out after {timeout:g}s"
        output = (scratch / "out").read_text(errors="replace").strip().splitlines()
        errors = (scratch / "err").read_text(errors="replace").strip().splitlines()
    if completed.returncode != 0:
        return False, f"crashed (exit {completed.returncode}): {errors[-1][:200] if errors else 'no stderr'}"
    if not output:
        return False, "produced no output"
    try:
        return True, json.loads(output[-1])
    except json.JSONDecodeError:
        return False, f"malformed output: {output[-1][:120]!r}"
