# ASSAY architecture

The reference for how a run is put together. Each section is one concept and
names the module that holds it. The kernel is `src/assay/`; it makes no model
calls, so every grade, refusal and meter below is deterministic code.

## 1. The shape of a run

One directory is one run. Two kinds of process touch it.

- The **daemon** (`daemon.py`) holds the world session for the life of the
  run, executes every paid action behind the gate, and is the only writer of
  the journal. It answers one request at a time on a Unix socket and exits on
  `stop` or on the WIN that ends the run.
- The **command line** (`cli.py`) is its client for `act`, `commit`, `reset`
  and `state declare`, and reads the files directly for everything else, so
  `status`, `view`, `python`, `audit` and `diagnose` work with the daemon
  stopped.

```
<run dir>/.assay/
  config.json      world_id, seed, adapter, registry_hash, created_at
  registry.json    the registry as pinned at start
  events.jsonl     the journal (section 3)
  chain.json       {last_id, head}: the chain head after the last line
  states.json      the declared states, and each extractor's last graded reading
  programs/        agent-written verifiers and extractors, each as <sha256>.py
  refusals.jsonl   every refusal the daemon gave: op, code, message
  NOTES.md         the agent's page, the one file here the agent writes
  images/          frame worlds: one PNG per event
  daemon.json, daemon.log
```

`assay start` on a fresh directory validates the registry, pins it, writes
`config.json` and spawns the daemon, which opens the session and writes event
0, the first observation. On a directory that holds a run it resumes: a live
daemon is kept, a dead one is replaced by a new one that replays every
recorded action through a fresh session and refuses (`REPLAY_DIVERGED`) if
the world no longer reproduces a recorded observation. A world that sets
`replayable = False` lives as long as its daemon.

## 2. The world: registry and adapter (`world.py`)

The **registry** is JSON and holds no code:

| Key | Meaning |
|---|---|
| `actions` | required; each `{name, params, description}`. Names are case-folded to upper case; `RESET` is built in. |
| `params` | parameter name to schema; every registered parameter is required |
| `description` | optional; shown to the agent as data, never as instructions |
| `budget.actions` | the hard cap on paid actions |
| `goal.text` | shown to the agent; never graded. The goal the kernel grades is the world's own `WIN`. |
| `notes_cap` | characters of notes (default 16000; null for none). Status warns past it; paid actions are refused past twice it. |
| `modules` | paths of the operator's module files, relative to the registry |

A parameter schema is a JSON Schema subset: `type` (`integer`, `number`,
`string`, `boolean`, `object`, `array`; `int`, `float`, `str` are accepted),
`enum`, `minimum`/`maximum` (or `min`/`max`), `minLength`/`maxLength`,
`properties` with `required`, `items` with `minItems`/`maxItems`. An unknown
key anywhere is refused: a registry says only what the kernel enforces.

The **adapter** is a Python file with `factory(root, config) -> session`. The
session has:

- `observation`: a mapping with `state` (`WIN` and `GAME_OVER` are the two
  the kernel acts on; anything else, by convention `NOT_FINISHED`, means the
  run goes on),
  `levels_completed` and `win_levels` (the progress pair; a world without
  milestones reports 0 of 1), `available_actions` (names, case-folded; when
  a world advertises none, the journal records every registered action) and
  exactly one of `data` (a JSON object, no NaN) or `frames` (a list of grids
  of colors 0..15, the last one settled);
- `step(action, params)`: one registered action with its validated
  parameters, or `RESET`; returns the next observation;
- optionally `replayable = False`.

Whatever the adapter raises is `WORLD_ERROR`, and nothing is journaled for
it. A world enforces its own policy inside `step` and reports a refusal
through the observation, so the spend is journaled as evidence.

## 3. The record (`journal.py`)

`events.jsonl` is append-only, one JSON object per line, keys sorted, no
spaces. The format is `assay-journal-v1`.

| Key | On | Meaning |
|---|---|---|
| `id` | every event | 0, 1, 2, ... contiguous |
| `timestamp` | every event | UTC, ISO 8601 |
| `action`, `data` | every event | `START` for event 0, else the action and its parameters (null for none) |
| `counts_action` | every event | false for `START`, true for every paid action |
| `state`, `levels_completed`, `win_levels`, `available_actions` | every event | as the world reported them |
| `level_before` | every event | `levels_completed` of the previous event; null on event 0 |
| `note` | every event | the agent's `--because`, or the reason of a reset |
| `observation` | dict worlds | the `data` object |
| `frames`, `n_frames` | frame worlds | each grid as rows of hex digits |
| `predict` | graded actions | the prediction as written |
| `predict_ok` | graded actions | false if any outcome missed; null if none missed but one was invalid or ungradable; else true |
| `grade` | graded actions | one object per outcome: the outcome's fields, `ok`, `actual`, `bucket`, and the flags `coerced`, `invalid`, `ungradable`, `verifier`, `identity_verdict` |

A state is spelled `channel` in a grade, its outcomes are the kinds
`channel_eq`, `channel_delta` and `channel_cross`, and the grammar's keyword
is `ch`: the spelling of the format's first release, kept because the format
is frozen.

**Chain.** `head_0 = sha256("assay-chain-v1")` and
`head_n = sha256(hex(head_{n-1}) + line_n)` over the raw line. The head goes
to `chain.json` after every append, and to the anchor file, outside the run
directory (`~/.assay/anchors/`, or `ASSAY_ANCHOR_DIR`), every 25 events and
on WIN.

**Ungated.** An event is ungated when it is paid, is not a `RESET`, and
carries none of `predict`, `predict_ok`, `grade`. The gate never writes one.

**Audit.** `assay audit` recomputes everything from the files and reports
`CLEAN` only if the ids are contiguous, the chain matches its stored head (a
missing `chain.json` does not), the last anchor matches the journal prefix
it names, the pinned registry matches the hash recorded at start, and no
event is ungated. Otherwise `INVALID FOR SCORING`, with each problem named.
Before it resumes a run the daemon makes the same chain and registry checks
(`CHAIN_DIVERGED`, `REGISTRY_CHANGED`); it tolerates a stored head one line
behind, which is a crash between the two writes.

## 4. Addressable states (`states.py`)

A state is a name bound to a way of reading the observation.

| State | Reading |
|---|---|
| `goal` | whether the world reports `WIN` |
| `level` | `levels_completed` |
| `budget_remaining` | the cap minus the paid actions up to and including the event |
| a path state | a dotted path into the `data` object (list indices allowed) |
| an extractor state | `extract(obs)` of the agent's file, run in the sandbox |

At most 16 are declared per run. A name cannot be bound twice to different
readings (`STATE_REDEFINED`). A reading that cannot be taken (a missing key,
a crashed extractor, a path state on a frame) makes the outcome ungradable.
An outcome that names an undeclared state is refused before any spend
(`STATE_UNKNOWN`) and counted: the mis-reference meter.

## 5. The prediction language (`grammar.py`, `grade.py`)

A prediction is one or more outcomes separated by `;`. Write `o`, `o'` for
the observations before and after the action, `b`, `a` for the readings of
the named state before and after, and `changed` for the number of keys of a
dict observation, or cells of the settled frame, that differ.

| Outcome | Bucket | Holds iff |
|---|---|---|
| `noop` | world model | `changed = 0`, no progress unit was completed and `state` is the same |
| `change` | world model | the complement of `noop` |
| `level+1` | gamble | `levels_completed` rose |
| `win` | gamble | the state is `WIN` |
| `verify:PATH.py` | world model | the stored program returns ok on `(o, o')` |
| `ch N = V` | by N | `a = V` (numbers compare by value, up to float rounding; `true` is not 1) |
| `ch N = V ± T` | by N | `|a - V| <= T` |
| `ch N delta OP V` | by N | `(a - b) OP V` for OP in `=`, `>=`, `<=` |
| `ch N delta sign +` / `-` | by N | `a - b > 0` / `a - b < 0` |
| `ch N crosses V` | by N | `b < V <= a` (from below) or `b > V >= a` (from above); either when unqualified |

An outcome on `goal` or `level` is a gamble; any other state is world model.
`@within Ns` after an outcome makes it ungradable when the step took longer.
Free text is kept as commentary; if no gradable outcome remains, `change` is
appended and marked `coerced`. A part that starts with a keyword and matches
no form is refused (`PREDICTION_SYNTAX`), with the grammar printed.

Each outcome gets one of four verdicts and an `actual` string:

| Verdict | Meaning | Counts as |
|---|---|---|
| held | the predicate is true | a fact |
| missed | it is false; `actual` is the counter-fact | a miss |
| invalid | a verifier crashed, timed out or printed something else | neither; halts a batch |
| ungradable | a reading could not be taken, or the result settled late | neither; halts a batch |

**Verifiers.** `def verify(before, after) -> (ok, actual)`, where both
arguments are the observation mappings of section 2 with `frames` as rows of
hex digits. The file is hashed and copied to `programs/` before the action
it grades, and the copy is what runs. A program that does not return a pair
is invalid. After each grading of a transition that changed something, the
same program runs on `(before, before)`: the identity verdict. A verifier
whose identity verdict equalled its real verdict on every one of five or more
such gradings does not use the transition; it is flagged vacuous and its
passes stop counting.

**Sandbox** (`sandbox.py`). Agent-written code runs as `python -I` in a fresh
process with an empty environment and an empty scratch directory: 5 seconds
of CPU and of wall clock, 1 MB per file written, no child processes and, on
Linux, 512 MB of memory. The arguments arrive as JSON on stdin and the result
leaves as one JSON line.

## 6. The gate and the batch (`daemon.py`)

One paid action, in order; every step before the world step is a free
refusal:

1. validate the action against the pinned registry (`ACTION_UNKNOWN`,
   `ACTION_PARAMS`);
2. parse the prediction (`PREDICTION_REQUIRED`, `PREDICTION_SYNTAX`);
3. refuse an outcome on an undeclared state (`STATE_UNKNOWN`);
4. hash and store every verifier named (`FILE_NOT_FOUND`, `PATH_INVALID`);
5. refuse if the run is complete (`RUN_COMPLETE`), the budget is short
   (`BUDGET_EXHAUSTED`) or the notes are past twice their cap (`NOTES_CAP`);
6. collect the modules' advice for the receipt;
7. refuse an action the last observation does not list as available
   (`ACTION_UNAVAILABLE`; `RESET` always is), then step the world once and
   time it;
8. grade every outcome against the events before and after;
9. append the event with its prediction and grades; extend the chain.

The receipt's result token is, in order of precedence: `GAME_COMPLETE` (WIN),
`LEVEL_COMPLETE` (a unit completed), `GAME_OVER`, `SURPRISE` (an outcome
missed), `INVALID_CLAIM` (none missed, one did not grade), `PREDICTED`.
Progress overrides the grade, and says so when the prediction also missed.

A **batch** (`commit`) runs steps 1 to 6 for every step before the first one
spends, with the budget checked for all of them, then steps 7 to 9 per step,
each graded against the event before it. It halts after the first step whose
result is anything but `PREDICTED`; the remaining steps are not spent and not
journaled. If a later step is refused at step 7, the batch reports `HALTED`
with the steps already taken. A **reset** is a paid event with a reason and
no prediction; its token is `RESET`.

## 7. Meters (`meters.py`)

Pure functions of the journal; nothing is stored.

- **Miss rates** per bucket: missed over held plus missed, skipping invalid
  and ungradable outcomes, coerced outcomes and the passes of a vacuous
  verifier.
- **Specificity**: one minus coerced outcomes over all graded outcomes.
- **Recent**: held actions among the last ten graded actions.
- **Coverage** of the current progress unit (the tail of the journal with
  the last event's `levels_completed`): the advertised actions never tried
  on it, those tried six times or more that never changed the world, and a
  stall, eight paid actions in a row that changed nothing.
- **Hazards**: the actions whose use took the world into `GAME_OVER` or lost
  a completed unit, with the first event that showed it.

## 8. Modules (`modules.py`)

A module is a function `advise(events, pending)` that returns one line of
advice or nothing. `pending` is the action about to be taken, `{kind, name,
params}`, or None when the status asks. A module never refuses an action.

| Built-in | Advises when |
|---|---|
| `wall_spend` | 25 paid actions on the current progress unit |
| `miss_streak` | 3 of the last 10 graded actions missed |
| `null_forensics` | the last graded action missed a `change` |
| `park_with_test` | a reset is about to be taken |
| `specificity` | over half of 20 or more graded outcomes are coerced |
| `hazard` | the pending action once entered a loss state |
| `coverage_audit` | at status: the untried and no-op-only actions, and a stall; before an action: the same move just missed, or missed three times in a row |

The operator adds a module by listing a file under `modules` in the
registry. The file defines `NAME` and `advise(events, pending)`; `events`
are the journal's events as dicts. Module files run in the daemon, with the
operator's trust.

## 9. The five layers and the diagnosis (`diagnose.py`)

The layers, with who writes each: 1 the constitution (the operator, once per
domain), 2 the modules (the operator), 3 the registry and the adapter (the
world's operator), 4 the addressable states (the agent, during the run), 5
the model (not the harness). `assay diagnose` reads the journal, the
refusals, the declared states and the notes, and files each finding under
the layer where that failure starts:

| Layer | Finding | Rule |
|---|---|---|
| 1 | predictions written as free text | coerced outcomes are 20% or more of the graded ones |
| 1 | predictions refused for their form | 3 or more `PREDICTION_SYNTAX` or `PREDICTION_REQUIRED` refusals |
| 1 | vacuous verifiers | any (section 5) |
| 2 | a move that had just missed was re-issued and missed again | two consecutive paid actions, same action and parameters, both missed |
| 2 | an outcome form that never held | the same form after the same action missed 3 times and never held |
| 2 | a stall; no-op-only actions | the coverage meter (section 7) |
| 3 | outcomes the code could not grade | any ungradable outcome |
| 3 | actions refused at the world's boundary | 3 or more of `ACTION_PARAMS`, `ACTION_UNKNOWN`, `ACTION_UNAVAILABLE`, `WORLD_ERROR`, `OBSERVATION_INVALID` |
| 3 | the budget ran out before the goal | a `BUDGET_EXHAUSTED` refusal on a run that is not won |
| 4 | world-model misses | 30% or more of 5 or more graded world-model outcomes |
| 4 | predictions named undeclared states | any `STATE_UNKNOWN` refusal |
| 4 | parts of the world the model does not address | a declared state no outcome ever named; an action only ever predicted as `change` or `noop` |
| 4 | verifiers that crashed | any invalid outcome |
| 4 | Verified notes that cite no held outcome | a line under a Verified heading with no `eN` that names an event with a held outcome |
| 5 | progress predicted and not made | half or more of 2 or more gamble outcomes missed while no world-model finding stands |
| 5 | nothing above, and not won | the budget is spent and no other finding stands |

A finding is a place to look, with counts and event ids as its evidence; the
mapping from a finding to a layer is a reading of the record, not a proof.

## 10. The frame-world extra (`grid/`)

Loaded only when an event carries frames, so a dict world needs neither it
nor pillow. It renders every settled frame as an image, words the transition
between two frames as moved, vanished, appeared, recolored and resized
components (`grid/perception.py`), prints the board, a diff and a scene
summary for `assay view`, adds the grid regions a point action never probed
to the coverage audit, and gives `assay python` the grids as arrays with the
perception and search helpers.

## 11. Laws

The tests in `tests/test_laws.py` hold these:

- the kernel names no world and imports no model client;
- a dict world loads neither the grid extra nor pillow;
- the manual teaches only commands, flags and outcome forms that exist;
- no em dash, no arrow and no machine path in the source or the docs;
- every world under `worlds/` is a valid registry and an adapter that
  imports nothing from the kernel.
