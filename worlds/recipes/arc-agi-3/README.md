# arc-agi-3

An ARC-AGI-3 public game as an ASSAY world: a 64 by 64 grid of colours, seven
actions, a level counter, and no instructions. The agent works out what each
level wants from the responses alone. This is the world the published
ARC-AGI-3 runs played (`bench/arcagi/`): the pack wraps the same adapter,
with the key read from a file, and adds the goal, a cap of 200 paid actions
and the interpreter the game client needs.

```
WORLD CARD    arc-agi-3                      code M-E-C-0   family Explore   fit yes   clock s
acts          this machine: a local copy of a public game   bench/arcagi adapter, replayable
answers       exactly: the frame after every action          states: the agent declares its own
done when     every level completed                          WIN from the game, a unit per level
worst case    nothing: a copy of the game                    no flags; ACTION1..7 are paid
proxy         none needed                    the public game, downloaded once
```

## In Claude Code

1. Save your ARC API key once (from https://arcprize.org), so the daemon can
   download a game the first time it plays it. Paste the line into your own
   terminal, not into Claude Code; it asks for the key without echoing it:

   ```bash
   mkdir -p -m 700 ~/assay/tokens && (umask 077; printf 'ARC API key: '; read -rs k; echo; printf '%s\n' "$k" > ~/assay/tokens/arc_api_key)
   ```

2. Attach the pack with the game you want, then play it:

   ```
   /assay:attach arc-agi-3 "win the game ft09"
   /assay:run
   ```

`/assay:attach` builds the interpreter `pack.json` names
(`~/assay/envs/arc-agi-3`, Python 3.14.5 with `requirements.txt`), starts the
run with the game id as the world id, and installs the hooks with the pack's
deny patterns, so the player reaches the game through `assay act` alone.

## By hand

```bash
uv venv --python 3.14.5 ~/assay/envs/arc-agi-3
uv pip install --python ~/assay/envs/arc-agi-3/bin/python -r <this directory>/requirements.txt
mkdir -p ~/assay/runs/ft09 && cd ~/assay/runs/ft09
ASSAY_PYTHON=~/assay/envs/arc-agi-3/bin/python assay start ft09 \
    --adapter <this directory>/adapter.py:factory \
    --registry <this directory>/registry.json \
    --owner-token-file ~/assay/tokens/ft09.token
```

The 25 public games are listed in `pack.json` under `world_id`. The game's
WIN is the world's: a goal you give at attach becomes the goal the player is
told, and the run still ends when the game reports every level completed.
The published ft09 run won 6 of 6 levels in 82 paid actions
(`evidence/arcagi/journal-ft09.jsonl.gz`).
