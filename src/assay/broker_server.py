"""The environment owner, run as a daemon by `assay start`.

    python -m assay.broker_server --run-dir DIR

One process per run directory. It owns the world session for the life of the
run, executes every paid action behind the gate, journals it before replying,
and is identified by this module name and the --run-dir argument on its
command line (see broker.find_daemon).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .broker import serve
from .core import RunPaths


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    serve(RunPaths(Path(args.run_dir).resolve()))


if __name__ == "__main__":
    main()
