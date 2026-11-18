# ASSAY

ASSAY is a reasoning harness. It sits between an agent and a world, and it
makes the agent's knowledge of that world something code has checked.

- **Grounding by prediction.** Before every action the agent writes what the
  action will do. After it, code grades each outcome against what the world
  reported: held or missed, with the actual value. A fact enters the agent's
  world model only through a prediction that held.
- **An addressable world state.** The agent names the parts of the world it
  cares about as states, readings that code extracts from the observation,
  and predicts over those names. What it knows is a compact, structured
  record it looks up by address instead of a long context it has to recall.
- **Five layers of reasoning.** What an agent needs to reason is placed at
  five layers, each a place to steer. `assay diagnose` reads a run and says
  at which layer its reasoning broke, with the evidence.

The harness makes no model calls. Any agent that can drive a shell can play,
and so can a person.

## Sixty seconds

```bash
REPO=$PWD; ASSAY=$REPO/bin/assay
mkdir /tmp/demo && cd /tmp/demo
"$ASSAY" start demo --adapter "$REPO/worlds/counter/adapter.py" --registry "$REPO/worlds/counter/registry.json"

"$ASSAY" act INC amount=1 --predict "change"          # ✓ held
"$ASSAY" act NOOP --predict "change"                  # ✗ missed, with what happened instead
"$ASSAY" state declare counter --path counter         # name a part of the world
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"
"$ASSAY" audit                                        # the record's integrity
"$ASSAY" diagnose                                     # the record against the five layers
```

`bin/assay` runs from the checkout with any Python 3.12+ that has numpy
(`ASSAY_PYTHON` pins the interpreter). `pip install -e '.[grid]'` installs
the `assay` command instead; `[grid]` adds pillow, which a world with grid
observations needs. macOS and Linux.

## The concepts

| Concept | What it is | Where |
|---|---|---|
| **World** | Anything that takes an action and returns an observation. | yours |
| **Adapter** | The one piece of code that touches the world: `factory(root, config)` returns a session with `observation` and `step(action, params)`. | `world.py` |
| **Registry** | The world's contract, no code: the actions with typed parameters, the action budget, the goal text. Pinned at start. | `world.py` |
| **Journal** | The record: one line per event, each paid action with its observation, its prediction and its grades. Hash-chained. Everything else is a view over it. | `journal.py` |
| **State** | A named reading of the observation: a dotted path or an extractor program. Declared once, never rebound. | `states.py` |
| **Prediction** | One or more outcomes in a fixed grammar, each a predicate over one transition. | `grammar.py` |
| **Verifier** | The agent's own program for what the grammar cannot say; hashed before the action, run in the sandbox. | `grade.py`, `sandbox.py` |
| **Gate** | No paid action without a prediction. Everything refused before the world is stepped costs nothing. | `daemon.py` |
| **Grade** | Held, missed, invalid or ungradable, always with the actual value. A held outcome is a fact. | `grade.py` |
| **Batch** | Steps with a prediction each; halts at the first miss. | `daemon.py` |
| **Meters** | World-model and gamble miss rates, specificity, coverage: pure functions of the journal. | `meters.py` |
| **Module** | Reasoning in code over the record: it advises, never refuses. | `modules.py` |
| **Notes** | The agent's one page: Verified (with event ids), Assumed, Plan. Its summary, not the record. | `.assay/NOTES.md` |

`CONSTITUTION.md` is the manual the agent reads. `docs/ARCHITECTURE.md` is
the reference: the file formats, the gate, the grading rules, the contracts.

## The five layers

From the most general to the most specific. A failure points at the layer to
change, and `assay diagnose` does the pointing.

| Layer | Holds | Written by | Change it when |
|---|---|---|---|
| 1. **The constitution** | how to learn any world | the operator, once | the agent lacks a way of working: free-text predictions, vacuous verifiers |
| 2. **Modules** | reasoning in code over the record | the operator | a pattern repeats across a long trace and the agent does not see it |
| 3. **Registry and adapter** | one world and the only way to touch it | the world's operator | the failures come from the interaction: unreadable states, refused parameters |
| 4. **Addressable states** | the world model the agent builds | the agent, by checked prediction | reasoning breaks from forgetting or drifting referents: rising world-model misses |
| 5. **The model** | everything behind the model's API | not the harness | progress predictions miss while the world model holds |

The more the model already knows the world, the less the layers above it
need to say.

## Run an agent

The operator starts the run; the agent's session begins after it, in the run
directory, at `status`. With Claude Code:

```bash
cd <run dir> && ASSAY=<repo>/bin/assay claude "$(cat PLAYER.md)"
```

where `PLAYER.md` is the whole prompt:

```
You are playing a world through ASSAY, a harness that grades a prediction before every paid action.

1. Read this file completely before anything else: <repo>/CONSTITUTION.md
2. The launcher is exported as ASSAY. You are in your run directory and the run is started. Begin with:
   "$ASSAY" status
   If .assay/NOTES.md has content, it is your record from an earlier session of this run.

The task: <the registry's goal text>

- Touch the world only through "$ASSAY". Read nothing outside this directory except the constitution.
- Never edit anything inside .assay/ except .assay/NOTES.md. Never run start or stop.
- Stop at WIN or when the budget is spent. Do the work yourself.
```

A session that ends early is started again with the same command: the agent
resumes from `status` and its notes. If the daemon is gone, `assay start
WORLD_ID` replays the journal first. Afterwards, `assay audit` checks the
record and `assay diagnose` reads it against the layers.

## Bring a world

A world is a directory with two files (`worlds/counter/` is the whole
contract in forty lines):

- `adapter.py`: `factory(root, config)` returns a session. `observation` is
  a mapping with `state` (`NOT_FINISHED`, `WIN`, `GAME_OVER`),
  `levels_completed`, `win_levels`, `available_actions` and either `data` (a
  JSON object) or `frames` (grids of colors 0..15). `step(action, params)`
  takes one registered action, or `RESET`, and returns the next observation.
  World policy lives here; the kernel names no world.
- `registry.json`: `actions` (name, `params` as a JSON Schema subset,
  optional `description`), `budget.actions`, `goal.text`, and optionally
  `notes_cap` and `modules`.

`worlds/arc-agi-3/` plays an ARC-AGI-3 public game: create the interpreter
its `requirements.txt` describes, save the ARC API key once, and start with
the game id as the world id:

```bash
ASSAY_PYTHON=<env>/bin/python "$ASSAY" start ft09 \
    --adapter <repo>/worlds/arc-agi-3/adapter.py --registry <repo>/worlds/arc-agi-3/registry.json
```

Its registry caps a run at 200 paid actions; for a longer game, start from a
copy with another `budget.actions`.

## Commands

| Command | Who | Does |
|---|---|---|
| `start WORLD_ID --adapter F.py --registry F.json` | operator | start a run in this directory, or resume it |
| `stop` | operator | stop the run's daemon; `start` resumes by replay |
| `status` | agent | the whole picture: world, registry, budget, states, meters, advice, notes |
| `act NAME k=v --predict "..."` | agent | one paid action, graded |
| `commit --step "NAME k=v :: ..."` | agent | a batch, graded step by step |
| `reset --because "..."` | agent | rewind the current progress unit, for one action |
| `state declare NAME --path a.b` / `--file f.py`, `state list` | agent | addressable states |
| `view --event N` | agent | one event: cause, prediction, grades, delta, observation |
| `python 'source'` | agent | offline analysis with the record preloaded |
| `audit` | anyone | ids contiguous, chain intact, anchors intact, no ungated event |
| `diagnose` | operator | the findings, by layer, with what to change |

A refusal is `ERROR | CODE | message`, then `NEXT | hint`, exit status 2.

## Trust

The record is tamper-evident, not tamper-proof. Every journal line extends a
hash chain whose head is anchored outside the run directory, and `assay
audit` recomputes all of it and checks that every paid action carries a
prediction. Nothing the agent asserts is load-bearing: every grade is
computed by the kernel from the world's own response, and agent-written code
(verifiers, extractors) runs only in a sandboxed process. The agent and the
daemon run as the same user, so where the stakes warrant it, run the daemon
as its own user and keep the anchor directory out of the agent's reach.

## License

Apache License 2.0 (`LICENSE`, `NOTICE`).
