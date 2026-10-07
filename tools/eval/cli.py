"""The command line of the runner.

    python3 -m tools.eval --jobs FILE [--state-dir DIR] [--assay CMD]
        [--dry-run | --summarize] [--max-concurrency N] [--max-sessions N]
        [--max-hours H] [--poll-seconds S] [--pause-minutes M]
        [--bootstrap-seed N] [--bootstrap-resamples N]

One error voice, as the kernel's: `ERROR | message`, exit 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import EvalError
from .jobs import load_design
from .runner import DEFAULT_LAUNCHER, Runner, default_state_dir, launcher_argv


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.eval",
        description="Run the jobs of a design: worlds times arms times seeds from one job file, "
        "the operator's start before each player session, per-seed results and a bootstrap summary.",
    )
    parser.add_argument("--jobs", type=Path, required=True, help="the job file (JSON; tools/eval/README.md)")
    parser.add_argument(
        "--state-dir", type=Path,
        help="where the runs, tokens, anchors, prompts, sessions and results live "
        "(default: ./eval-state/<name>)",
    )
    parser.add_argument(
        "--assay", metavar="CMD",
        help=f"the launcher, split like a shell line (default: {DEFAULT_LAUNCHER})",
    )
    parser.add_argument("--dry-run", action="store_true", help="print every command the queue would run; write nothing")
    parser.add_argument("--summarize", action="store_true", help="recompute results.jsonl and summary.json from the state; launch nothing")
    parser.add_argument("--max-concurrency", type=int, metavar="N", help="players at a time (default: the job file's concurrency)")
    parser.add_argument("--max-sessions", type=int, metavar="N", help="launches per job (default: the job file's max_sessions)")
    parser.add_argument("--max-hours", type=float, metavar="H", help="wall clock per session (default: the job file's max_hours)")
    parser.add_argument("--poll-seconds", type=float, default=10.0, metavar="S")
    parser.add_argument("--pause-minutes", type=float, default=30.0, metavar="M", help="the queue's pause after a usage-limit signal")
    parser.add_argument("--bootstrap-seed", type=int, default=0, metavar="N")
    parser.add_argument("--bootstrap-resamples", type=int, default=2000, metavar="N")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        for name in ("max_concurrency", "max_sessions"):
            value = getattr(args, name)
            if value is not None and value < 1:
                raise EvalError(f"--{name.replace('_', '-')} must be at least 1")
        if args.max_hours is not None and args.max_hours <= 0:
            raise EvalError("--max-hours must be positive")
        if args.bootstrap_resamples < 1:
            raise EvalError("--bootstrap-resamples must be at least 1")
        if args.dry_run and args.summarize:
            raise EvalError("--dry-run and --summarize exclude each other")
        design = load_design(args.jobs)
        runner = Runner(
            design,
            state_dir=args.state_dir or default_state_dir(design),
            launcher=launcher_argv(args.assay),
            concurrency=args.max_concurrency,
            max_sessions=args.max_sessions,
            max_hours=args.max_hours,
            poll_seconds=args.poll_seconds,
            pause_minutes=args.pause_minutes,
            bootstrap_seed=args.bootstrap_seed,
            bootstrap_resamples=args.bootstrap_resamples,
        )
        if args.dry_run:
            return runner.dry_run()
        if args.summarize:
            return runner.summarize_only()
        return runner.run()
    except EvalError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
