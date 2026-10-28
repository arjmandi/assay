"""A random agent on ARC-AGI-3, with no model: the floor under the Claude Code
and ASSAY numbers, under the same 1,500-action budget, the same local engine
and the same rules (game_server.Game: resets count, a reset on an unplayed
level is not sent, only a reset in GAME_OVER, an unavailable action refused).

Each step takes an available action other than RESET uniformly at random,
and RESET only in GAME_OVER. ACTION6 takes a cell: anywhere on the grid
(`uniform`), or a cell whose color is not the frame's most common one
(`object`). The journal lands in `<out>/<variant>-<game>-r<seed>/.arc/`,
which bench/arcagi/rhae.py scores.

usage: random_agent.py --out DIR [--games all] [--variants uniform,object] [--seeds 1,2,3] [--cap 1500]
Run it with PYTHONPATH=<repo>/src under the arc_agi interpreter.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import game_server  # noqa: E402

JOBS_FILE = HERE.parent / "jobs" / "sonnet-haiku-5-5.json"


def play(game_id: str, variant: str, seed: int, cap: int, out: Path) -> dict[str, object]:
    rng = random.Random(f"{game_id}-{variant}-{seed}")
    folder = out / f"{variant}-{game_id}-r{seed}"
    folder.mkdir(parents=True, exist_ok=True)
    game_server.Game._render = lambda self: None  # no image: nobody looks
    game = game_server.Game(game_id, folder, folder, cap)
    refused = 0
    while game.paid < cap and game_server.state_of(game.observed) != "WIN" and refused < 1000:
        if game_server.state_of(game.observed) == "GAME_OVER":
            ok, _ = game.act("RESET", None, True)
        else:
            names = [name for name in game.available() if name != "RESET"]
            action = rng.choice(names) if names else "RESET"
            data = None
            if action == "ACTION6":
                grid = game_server.settled(game.observed)
                if variant == "object":
                    background = Counter(grid.flatten().tolist()).most_common(1)[0][0]
                    cells = [(x, y) for y in range(grid.shape[0]) for x in range(grid.shape[1]) if grid[y, x] != background]
                    x, y = rng.choice(cells) if cells else (rng.randrange(64), rng.randrange(64))
                else:
                    x, y = rng.randrange(64), rng.randrange(64)
                data = {"x": int(x), "y": int(y)}
            ok, _ = game.act(action, data, True)
        refused += 0 if ok else 1
    return {"game": game_id, "variant": variant, "seed": seed, "state": game_server.state_of(game.observed),
            "levels": int(game.observed.levels_completed), "paid": game.paid}


def main() -> None:
    parser = argparse.ArgumentParser(prog="random_agent.py")
    parser.add_argument("--out", required=True)
    parser.add_argument("--games", default="all")
    parser.add_argument("--variants", default="uniform,object")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--cap", type=int, default=1500)
    args = parser.parse_args()
    games = sorted(world["id"] for world in json.loads(JOBS_FILE.read_text())["worlds"]) if args.games == "all" else args.games.split(",")
    out = Path(args.out).resolve()
    results = out / "results.jsonl"
    done = set()
    if results.exists():
        done = {(r["game"], r["variant"], r["seed"]) for r in map(json.loads, results.read_text().splitlines())}
    for variant in args.variants.split(","):
        for seed in map(int, args.seeds.split(",")):
            for game_id in games:
                if (game_id, variant, seed) in done:
                    continue
                row = play(game_id, variant, seed, args.cap, out)
                with results.open("a") as handle:
                    handle.write(json.dumps(row) + "\n")
                print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
