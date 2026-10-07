# Factorio benchmark — protocol

This folder is the Factorio Learning Environment (FLE) benchmark harness for
ASSAY: the world adapter, the pre-registered action registries, the namespace
escape audit, and (from M2 on) the results record.

Status: **M2 calibration complete** (2026-08-26): three tasks played, three
won, journals CLEAN, zero policy refusals (`RESULTS.md`). Those are
calibration numbers, not the sweep. Vocabulary: ASSAY's prose says world and
progress unit; here the world id is the task alias from the table below, a
progress unit is one milestone of the four (`win_levels` is 4), and WIN is the
throughput bar held through the holdout window.

## Version pins

Every pin below is load-bearing. They come from the M0 determinism spike and
are the configuration the M1 smoke run was played on.

| Component | Pin |
|---|---|
| Python | 3.12.12 (`uv venv --python 3.12`) |
| factorio-learning-environment | 0.4.3 |
| a2a-sdk | **`<1` — must be pinned by hand** (0.4.3 resolves 1.1.2, which fails on a `TextPart` import from `a2a.types`) |
| factorio-rcon-py | 1.2.1 |
| Docker image | `factoriotools/factorio:2.0.73` |
| Map seed | 44340 (hardcoded in FLE's generated compose file; `fle/cluster/run_envs.sh`) |
| Scenario | `default_lab_scenario` |
| Starting inventory | FLE's `LAB_PLAY_POPULATED_STARTING_INVENTORY`, mirrored in `adapter.py` |

## Run recipe

The adapter needs FLE importable in the interpreter that serves the broker.
`bin/assay` selects the first `python3` on PATH that is ≥ 3.12 with numpy 2.x
and pillow 10–12; the FLE virtualenv satisfies all three, so putting it first
on PATH is the whole of the wiring. The broker daemon inherits that interpreter
(`sys.executable`), so nothing else needs configuring.

```bash
# one-time: an FLE environment that also satisfies bin/assay's fingerprint
uv venv --python 3.12 .venv
uv pip install 'factorio-learning-environment==0.4.3' 'a2a-sdk<1' 'factorio-rcon-py==1.2.1'

# the game server (Docker must be running); the adapter will also start this
# itself if the port is closed
python -m fle cluster start -n 1

# a run
export ASSAY_BROKER_TIMEOUT=600   # slow box64 ops: client waits longer; the broker also survives a client hangup (kernel fix 2026-08-26)
export PATH="$PWD/.venv/bin:$PATH"
ASSAY=<repo>/bin/assay
mkdir <run-dir> && cd <run-dir>            # one directory = one run
"$ASSAY" start ironore \
    --adapter <repo>/bench/factorio/adapter.py:factory \
    --registry <repo>/bench/factorio/registry_lab64.json
```

If FLE is not importable the adapter fails at `assay start` with a named error
pointing back here. If no server is listening and autostart cannot bring one
up, it names the `fle cluster start` command.

### World ids

The M2 runs were recorded when ASSAY world ids were `[a-z0-9]{2,16}`, so FLE's
task keys could not be used verbatim. `TASK_ALIASES` in `adapter.py` is the
whole mapping and stays as the published ids; `assay start ironore` runs FLE's
`iron_ore_throughput`. (Since 1.2.0 a world id is any string up to 64
characters without whitespace or path separators, so the aliases are a
convenience, not a necessity.)

| id | FLE task | id | FLE task |
|---|---|---|---|
| `ironore` | iron_ore | `sulfur` | sulfur |
| `crudeoil` | crude_oil | `sulfacid` | sufuric_acid *(FLE's spelling)* |
| `ironplate` | iron_plate | `logisci` | logistics_science_pack |
| `steelplate` | steel_plate | `battery` | battery |
| `irongear` | iron_gear_wheel | `plastic` | plastic_bar |
| `stonewall` | stone_wall | `engineunit` | engine_unit |
| `inserter` | inserter | `milsci` | military_science_pack |
| `petrogas` | petroleum_gas | `advcircuit` | advanced_circuit |
| `circuit` | electronic_circuit | `lds` | low_density_structure |
| `autosci` | automation_science_pack | `chemsci` | chemical_science_pack |
| `piercing` | piercing_round | `procunit` | processing_unit |
| `prodsci` | production_science_pack | `utilsci` | utility_science_pack |

All 24 are FLE 0.4.3 lab-play throughput tasks. Quota is 16 per window for
solids and 250 for fluids (`crudeoil`, `petrogas`, `sulfacid`), read from FLE's
own task definitions rather than restated here.

## Registries — Option A (FLE parity)

`registry_lab64.json` (budget 64, the v0.3 evaluation cap) and
`registry_lab128.json` (budget 128, the paper cap). Identical otherwise:
`hand_cap: null`, `zero_prior: false`, and two paid actuators.

```
RUN  program=<str>            one program against the FLE API namespace
WAIT ticks=<int 1..3600>      advance the simulation by an exact tick count
```

**`program` is base64-encoded UTF-8 Python source.** ASSAY action tokens are
parsed by whitespace splitting (`assay.registry.parse_registry_action`), so
source containing a space or a newline cannot be passed inline. Base64 is the
encoding that survives that parser and lands the exact program text in the
journal, which is what replay and audit need. A program that is not valid
base64 is a free refusal with an error that says so — a zero-prior agent
discovers the encoding without being told it.

Information parity: published FLE agents receive the full API reference in
their prompt. `FLE_API.md` documents how to obtain and inject the identical
reference, and both registries point at it from their action descriptions
(tier-2 starting information, like the ARC hint line). `zero_prior: false`
keeps parity for comparable runs; flipping it to `true` withholds the
descriptions and is the ablation.

Secrets: the registries list `ASSAY_FLE_RCON_PASSWORD`, `FLE_RCON_PASSWORD`,
`RCON_PASSWORD`, and `FACTORIO_RCON_PASSWORD`. The adapter reads the first two
as overrides and otherwise uses FLE's own constant. None of them ever appear in
an observation: the agent is given no address, no port, and no password, and
has no action that could carry one.

## The milestone ladder

`win_levels = 4`. Every level is computed inside the adapter from Factorio's
own production statistics and entity list — server state the agent has no
action that writes to. **Nothing here is agent-reported.**

| level | meaning |
|---|---|
| 1 | the first unit of the target item has been produced, by any means |
| 2 | an automated chain producing it exists — non-zero automated production with entities standing |
| 3 | one complete 3600-tick window met the quota **and a real entity is producing the target** |
| 4 | the next complete window also met it — the holdout — and the run is `WIN` (same corroboration) |

"Automated" is FLE's own accounting (`calculate_achievements`): total new
output minus what the player hand-mined (`harvested`) or hand-crafted
(`crafted`). Hand-mining, hand-crafting, and moving items between chests land
in the `static` bucket and count for nothing. This is the same distinction that
made FLE add its holdout, applied per window.

Levels are a high-water mark: once reached, a level does not drop if throughput
later falls. Level 4 is not a high-water shortcut — it requires two *adjacent*
windows at quota, so a single lucky window cannot produce a win.

Levels 3 and 4 additionally require **throughput corroboration**: the throughput
statistic is only credited when a real entity is actually producing the target.
A rate with no producing entity is incoherent — a forged or injected statistic —
and caps the milestone below 3. See "Throughput corroboration" under Anti-cheat.

### Holdout, in ticks

`WINDOW_TICKS = 3600` = 60 in-game seconds at 60 UPS. Windows are a fixed grid
anchored at session start: `[0, 3600)`, `[3600, 7200)`, and so on. The clock
only moves through metered advances, and every advance is chopped at window
boundaries so a production snapshot is taken at exactly tick 0, 3600, 7200, ….

The FLE protocol is a 60-second pre-holdout wait followed by a 60-second
holdout measurement. Here that is two adjacent complete windows: the first is
the pre-holdout, the second is the holdout, and **both** must meet the quota.
Wall-clock never enters — a window is 3600 ticks whether the server simulates
them in six seconds or six minutes.

## Determinism

The design rule from M0: never let "time passes" be an implicit side effect.

- The game is `tick_paused` at all times except inside a metered advance.
  Pause state is set over raw RCON at session start, because FLE's
  `GameControl` caches pause state per process and goes stale across processes
  (M0 finding 4).
- `WAIT ticks=N` advances exactly N ticks via `game.ticks_to_run`. The adapter
  polls `game.tick` to detect completion; that poll is wall-clock but the tick
  count is not — the server delivers exactly N.
- `RUN` executes against a frozen world, with one exception. Factorio's path
  finder answers only on an in-game event, so `move_to` and `connect_entities`
  cannot complete while the game is paused (M0's one hard tick dependency), and
  a real route issues many path queries in sequence — ticks must keep flowing
  for as long as the program runs. A program naming `move_to`,
  `connect_entities`, or `harvest_resource` (which walks to an out-of-reach
  resource) earns an **adaptive** pathfinding allowance: the adapter pumps ticks
  in `RUN_PATH_TICK_STEP = 60`-tick increments over its own second RCON socket,
  alongside the program's worker thread, until the worker returns (every path
  answered) or the hard cap `RUN_PATH_TICK_CAP = 6000` ticks is reached. The
  exact tick delta the pump spends is recorded — implicitly, as the `tick`
  cursor the action carries into its observation, which the broker journals.
  (A single fixed 180-tick allowance was used before; it drained before a long
  route's later queries, which then hit a frozen world — `connect_entities`
  returned "No path found" and built nothing, forcing agents to hand-place
  belts. `RUN_PATH_TICKS = 180` is retained only as the backward-compatible
  value a pre-adaptive journal recorded, reused verbatim on replay.)
- The observation reports `tick` as the delta from the session anchor, never
  Factorio's absolute `game.tick`. The absolute value counts from server boot
  and is not reproducible (M0); the delta is.

The journal is therefore a sequence of (program, tick-delta) pairs and replays
exactly. For a `WAIT` the delta is the requested `ticks`; for a pathfinding
`RUN` it is the delta the adaptive pump recorded (above). **Verified in M1:**
restarting the broker on the finished smoke run replayed all six paid actions
from a fresh world — including two 3600-tick production windows that reproduced
57 and 63 iron ore exactly — and the CLI printed `RESUMED | ironore | completed
run` rather than `LOCAL_REPLAY_DIVERGED`.

Determinism of the adaptive allowance — the design rule that keeps it exact.
A pathfinding `RUN`'s live tick cost is *variable*: the pump runs until the
worker returns, so wall-clock timing (RCON latency, the path finder's own
polling) decides how many increments elapse. Live variability is fine because
**replay never re-derives the cost — it reuses the recorded one.** At session
start the adapter reads `.assay/mutations.jsonl` and recovers, in journal order,
the tick delta every already-recorded pathfinding `RUN` spent (a `RUN`'s delta
is the rise in its cursor over the previous action's). On the local resume the
broker replays each recorded action through a fresh session; a replayed
pathfinding `RUN` pumps *exactly* its recorded delta (still in increments
alongside the worker, so the re-issued path queries still see ticks flowing,
then topped up to the recorded total if the worker returns early) instead of
pumping adaptively. The cursor — and thus the whole observation — therefore
reconstructs identically, and the broker's own `LOCAL_REPLAY_DIVERGED` check
enforces it action by action. Empirically: the same sequence (a `move_to`, a
`connect_entities` belt, a `WAIT`, a `connect_entities` pipe) run live recorded
deltas `[120, 480, 300, 600]`; replaying its journal reused the three path
deltas `[120, 480, 600]` and reproduced the identical state fingerprint and
final tick, as did an independent fresh live run. A pre-adaptive journal's flat
`+180` path deltas are recovered and reused the same way (backward compatible).

Residual, stated rather than papered over: the pump's *interleaving* with the
program's own RCON calls is wall-clock dependent even though the recorded tick
total is reused exactly, so a `RUN` that both routes a path and drives a running
factory could in principle read a production count a fraction of a second early.
This is inert while nothing time-driven is running (the build phase). Two other
edge cases: if a route genuinely needs more than `RUN_PATH_TICK_CAP` ticks the
pump stops early and the program's remaining queries fail (raise the cap), and a
mutation journal hand-edited to a tick the world cannot reproduce will, rightly,
`LOCAL_REPLAY_DIVERGED`. If a run ever reports that, the pathfinding allowance is
the first suspect.

**Not the REMOTE fallback.** RESEARCH.md §7.4 held open the option of treating
Factorio runs like ARC's competition mode — non-reconstructible, artifacts
preserved, fresh directory per run. M0 and this milestone make that
unnecessary: runs are `local` mode with exact replay recovery.

## Anti-cheat

Stock FLE hands every agent program the live RCON client, and through it the
Lua console, the host filesystem, and the production statistics the throughput
verifier reads. `NAMESPACE_AUDIT.md` documents the proof and the filter added
in response. Two consequences for anyone reading a Factorio run:

- Every `RUN` program is screened before execution. A refused program **spends
  its action and is journaled** with `POLICY_REFUSED | <reason>` in `stderr`
  and a `policy_refusals` counter in the observation. The record of what was
  reached for is the finding, so it is kept.
- The agent's only contact with the world is `assay act` / `assay commit`.
  Anything else is ungated and voids the run at `assay audit`.

Two further mechanisms were added as pre-M2 hardening. They live entirely in the
bench-layer adapter; the kernel is untouched. Both were named as M2 mitigations
in `NAMESPACE_AUDIT.md` (residual risks 1 and 3).

### Unknown-global monitor

A watch, not a wall. It never refuses a program, changes any state, or voids a
run; its only job is to notice if a future FLE version quietly exposes a new
global that a submitted program could reach for.

At session start the adapter enumerates the names a program would actually see
as globals — the same union FLE builds in `FactorioNamespace.eval_with_timeout`:
non-underscore `dir(builtins)`, non-underscore `dir(namespace)`, and the
non-underscore `persistent_vars` keys. It compares that live set against
`_EXPECTED_FLE_GLOBALS` in `adapter.py`, a frozenset of the 356 names the pinned
FLE 0.4.3 (Python 3.12.12) exposes, enumerated from a live instance so the
expected set is grounded in reality rather than guessed. Any live name not in
the expected set is reported.

The report is written two ways: into the observation body under `namespace_watch`
(the authoritative, replay-safe, audit-visible copy — `{expected_globals,
observed_globals, unexpected_globals, clean}`), and, best-effort, as a
`namespace_watch` line in the run's `activity.jsonl` for a reader tailing the
journal. The activity write is wrapped so a missing or locked journal can never
affect the run. On the pinned version `unexpected_globals` is empty and `clean`
is true. A non-empty report is a signal to re-audit the AST screen before that
FLE version is trusted — it means a new handle appeared that the screen has not
been reasoned about.

If the version pin changes, regenerate `_EXPECTED_FLE_GLOBALS` from a live
instance with the same three-way union.

### Throughput corroboration

Prevention, in the win predicate. Levels 3 and 4 read Factorio's production
statistics, and any program that reaches RCON can write to those statistics
directly (`NAMESPACE_AUDIT.md` probe R forged 4321 iron-ore with no drill, no
ore, no factory — filed under `dynamic`, enough to satisfy the ladder). The
holdout does not touch this: it constrains *when* production counts, not *who*
may write the counter.

So before crediting throughput, the adapter requires a **real entity that is
actually producing the target**. A `dynamic` rate with no producing entity is
incoherent, so it is not credited and the milestone is capped below 3.

The target's producing-entity kind is mapped in `_TARGET_PRODUCER_KIND` for all
24 lab-play targets (recipe categories come back null from
`get_prototype_recipe` on 0.4.3, so the kind is mapped, not derived from
category). The kind selects which public entity-model field must corroborate the
rate — all three confirmed populated by `get_entities()` on the pinned version:

- **mined** (`iron-ore`, `crude-oil`): a `MiningDrill`/`PumpJack` whose
  `.resources` lists the target.
- **smelted** (`iron-plate`, `steel-plate`): a `Furnace` whose `.furnace_result`
  holds the target, or whose `.furnace_source` holds the target's input ore
  (furnaces carry no settable recipe; `_SMELTING_INPUT` maps the input).
- **assembled** (the other 20, incl. chemical-plant and refinery products): an
  `AssemblingMachine`/`ChemicalPlant`/`OilRefinery` whose `.recipe` makes the
  target — `recipe.name` matches, or a `recipe.products` name matches (the
  latter is needed for refinery outputs like `petroleum-gas`, whose recipe name
  is the process, not the product).

A target absent from the map is **unmapped**: corroboration is not enforced
(fail-open, so an untested future target can never wrongly block a legitimate
win), and the observation marks it. All 24 shipped tasks are mapped. The
observation always carries `throughput_corroboration = {enforced, producer_kind,
producer_present}` so a reader can see the check and its result on every event.

The corroboration is a *coherence* check, not a full defence: it makes a forge
have to be physically consistent (build the right entity) as well as numerically
large. It does not make the statistic unforgeable — the AST screen remains the
capability boundary. Its value is that the single-line RCON forge that the screen
already refuses is, even if it ever slipped through, no longer sufficient on its
own to manufacture a win.

## M1 acceptance record (2026-08-26)

Smoke run on `ironore` (`iron_ore_throughput`), `registry_lab64.json`, hand-
written programs, no LLM. Run directory
`~/workspace/assay-runs/factorio-m1-smoke`.

Six paid actions to WIN: walk to the ore patch, place three fuelled burner
drills each emptying into a chest, one deliberate RCON-escape attempt (refused
and journaled at event 3), one status query, then two 3600-tick windows
producing 57 and 63 iron ore against a quota of 16.

```
STATUS | ironore | event 6 | level 4/4 | paid actions 6 | WIN
CLAIMS | world-model misses 0/12 (0.0%) | gamble misses 0/1 (0.0%) | sharpness 13/13 (100%) | invalid 0
AUDIT  | CLEAN | events 7 (paid 6) | contiguous yes | chain intact | anchors intact (1)
```

Standalone verifier (`verify/assay_verify.py`, spec
`assay-journal-v1`): `verdict CLEAN`, ungated paid events 0.

Cost and duration on this hardware (Apple Silicon, box64-emulated server,
1 CPU / 1 GiB compose limit): session boot ≈ 1 s against a warm server, a
`RUN` 0.2–0.9 s, a 3600-tick window ≈ 6 s of wall time. The whole six-action
run completed in well under a minute. LLM latency, not the simulator, will
dominate a real run.

## Pre-M2 hardening acceptance record (2026-08-26)

Both hardening mechanisms above were added bench-layer (kernel untouched:
`git diff --stat main -- src/` empty) and verified against the pinned FLE 0.4.3
on the running server, no LLM.

- **Legit win unaffected.** The M1 six-action `ironore` sequence was replayed
  through `assay act` (run directory `~/workspace/assay-runs/factorio-m2-legit`)
  and still reached `level 4/4 | WIN`, `AUDIT | CLEAN`, all six predictions
  held. `throughput_corroboration` reported `producer_present` false before the
  drills were placed and true from the drill-placement event on; window rates
  57 and 63 were credited because three `burner-mining-drill` on the iron-ore
  patch corroborate them.
- **Monitor clean.** Every observation and the `activity.jsonl` carried
  `namespace_watch = {expected_globals 356, observed_globals 356,
  unexpected_globals [], clean true}` — no new handle on this version.
- **Forge rejected.** A test harness injected `on_flow("iron-ore", 5000)` over
  RCON into each of two windows with nothing built (bypassing the screen in the
  harness only; the shipped screen still refuses this via `assay act`). The
  forged statistic was present — `production.output` 10000, window rates
  `[5000, 5000]`, both far past the quota of 16 — and the pre-corroboration
  ladder would have scored it `level 4` (a full WIN). With corroboration the
  same flows capped at `level 1`: `producer_present` false, `entity_counts`
  empty, state `NOT_FINISHED`.

## Next: M2

A three-task probe (`ironplate`, `irongear`, `circuit`) to calibrate real cost
and duration against RESEARCH.md §6, with pre-registered bars written here
before any scored run.
