"""`python3 -m tools.eval ...` from the repository root, or this file by path
from anywhere (`python3 <repo>/tools/eval/__main__.py ...`)."""

from __future__ import annotations

import sys
from pathlib import Path


def _run() -> int:
    if not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.eval.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_run())
