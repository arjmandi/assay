"""A switchboard: the local archetype's template, the smallest world the
command-world engine runs. Copy this directory, rename the world in
recipe.json and registry.json, and replace the three subcommands with your
own world.

Five switches and four lamps, all off at the start. Each working switch
toggles one lamp; one switch is jammed and the world refuses it. The wiring
comes from the seed and is never written into the working copy, because the
agent can read the copy: hidden state stays out of it.

    python3 world.py setup --workdir W                  writes lamps.json, every lamp off
    python3 world.py observe --workdir W                prints the data object
    python3 world.py act ACTION --workdir W --seed S    prints the result object; parameters on stdin
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

LAMPS = 4
SWITCHES = 5


def wiring(seed: int) -> list[int | None]:
    """Switch n (from 1) toggles lamp wiring[n - 1]; None is the jammed one."""
    lamps: list[int | None] = [*range(LAMPS), None]
    random.Random(f"switchboard-{seed}").shuffle(lamps)
    return lamps


def setup(workdir: Path) -> None:
    (workdir / "lamps.json").write_text(json.dumps({"lamps": [0] * LAMPS}) + "\n")


def observe(workdir: Path) -> dict[str, Any]:
    lamps = json.loads((workdir / "lamps.json").read_text())["lamps"]
    return {"lamps": lamps, "on": sum(lamps)}


def act(workdir: Path, seed: int, action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action != "TOGGLE":
        raise ValueError(f"unknown action {action!r}")
    switch = int(params["switch"])
    target = wiring(seed)[switch - 1]
    if target is None:
        # A refusal goes through the result, so the spend is journaled.
        return {"status": "refused", "detail": f"switch {switch} is jammed"}
    lamps = json.loads((workdir / "lamps.json").read_text())["lamps"]
    lamps[target] = 1 - lamps[target]
    (workdir / "lamps.json").write_text(json.dumps({"lamps": lamps}) + "\n")
    return {"status": "ok", "detail": f"switch {switch} clicked"}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["setup", "observe", "act"])
    parser.add_argument("action", nargs="?")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "setup":
        setup(args.workdir)
        return 0
    if args.command == "observe":
        print(json.dumps(observe(args.workdir), sort_keys=True))
        return 0
    params = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(act(args.workdir, args.seed, args.action, params), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
