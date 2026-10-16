"""The evaluation runner: worlds times arms times seeds from one job file,
run the way the benchmark protocols describe (the operator starts each run
and holds the owner token, the player's session begins in the run directory
after that), with per-seed results and a bootstrap summary. It launches
players and reads run directories; it is not part of the kernel, which makes
no LLM calls, and it imports nothing from `assay`.

    python3 -m tools.eval --jobs bench/arcagi/jobs/e1.json --dry-run
    python3 -m tools.eval --jobs bench/arcagi/jobs/e1.json --state-dir <dir>
    python3 -m tools.eval session --run-dir <run>    (one started run, session.py)
    python3 -m tools.eval report --run-dir <run>     (the report of one run, report.py)
    python3 -m tools.eval compare --pack <pack>      (ASSAY against /goal on one pack, compare.py)
    python3 -m tools.eval watch --run-dir <run>      (one line per change of a run, watch.py)
    python3 -m tools.eval diagnose --run-dir <run>   (a run read against the five layers, diagnose.py)

`tools/eval/README.md` is the manual.
"""

from __future__ import annotations


class EvalError(Exception):
    """A refusal of the runner, printed as `ERROR | message`, exit 2."""
