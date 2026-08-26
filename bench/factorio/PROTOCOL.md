# Factorio benchmark — protocol

This folder is the Factorio Learning Environment (FLE) benchmark harness for
ASSAY: the world adapter, the pre-registered action registries, the namespace
escape audit, and (from M2 on) the results record.

Status: **M1 complete** — adapter, registries, and audit exist and a smoke run
reaches WIN. No scored runs yet; M2 pre-registers bars before any.

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

ASSAY world ids are `[a-z0-9]{2,16}` (`assay.core.normalize_game_id`), so FLE's
task keys cannot be used verbatim. `TASK_ALIASES` in `adapter.py` is the whole
mapping; `assay start ironore` runs FLE's `iron_ore_throughput`.

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
| 3 | one complete 3600-tick window met the quota |
| 4 | the next complete window also met it — the holdout — and the run is `WIN` |

"Automated" is FLE's own accounting (`calculate_achievements`): total new
output minus what the player hand-mined (`harvested`) or hand-crafted
(`crafted`). Hand-mining, hand-crafting, and moving items between chests land
in the `static` bucket and count for nothing. This is the same distinction that
made FLE add its holdout, applied per window.

Levels are a high-water mark: once reached, a level does not drop if throughput
later falls. Level 4 is not a high-water shortcut — it requires two *adjacent*
windows at quota, so a single lucky window cannot produce a win.

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
  finder answers on an in-game event, so `move_to` and `connect_entities`
  cannot complete while the game is paused (M0's one hard tick dependency).
  A program naming `move_to`, `connect_entities`, or `harvest_resource` (which
  walks to an out-of-reach resource) earns a fixed allowance of
  `RUN_PATH_TICKS = 180` ticks, pumped concurrently over the adapter's own
  second RCON socket. The allowance is decided by static analysis of the
  program text, so **the tick cost of a `RUN` is a function of the program
  alone** — the same program always costs 180 ticks or always costs 0.
- The observation reports `tick` as the delta from the session anchor, never
  Factorio's absolute `game.tick`. The absolute value counts from server boot
  and is not reproducible (M0); the delta is.

The journal is therefore a sequence of (program, tick-delta) pairs and replays
exactly. **Verified in M1:** restarting the broker on the finished smoke run
replayed all six paid actions from a fresh world — including two 3600-tick
production windows that reproduced 57 and 63 iron ore exactly — and the CLI
printed `RESUMED | ironore | completed run` rather than `LOCAL_REPLAY_DIVERGED`.

Known limit, stated rather than papered over: during a `RUN` that earns the
pathfinding allowance, the 180 ticks are pumped concurrently with the program's
own RCON calls, so the *interleaving* is wall-clock dependent even though the
tick total is exact. This is inert while nothing time-driven is running (the
build phase) and could in principle shift a production count by a fraction of a
second's worth of output if an agent moves around inside a running factory. If
a run ever reports `LOCAL_REPLAY_DIVERGED` this is the first suspect.

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

Standalone verifier (`assay-verify/assay_verify.py`, spec
`assay-journal-v1`): `verdict CLEAN`, ungated paid events 0.

Cost and duration on this hardware (Apple Silicon, box64-emulated server,
1 CPU / 1 GiB compose limit): session boot ≈ 1 s against a warm server, a
`RUN` 0.2–0.9 s, a 3600-tick window ≈ 6 s of wall time. The whole six-action
run completed in well under a minute. LLM latency, not the simulator, will
dominate a real run.

## Next: M2

A three-task probe (`ironplate`, `irongear`, `circuit`) to calibrate real cost
and duration against RESEARCH.md §6, with pre-registered bars written here
before any scored run.
