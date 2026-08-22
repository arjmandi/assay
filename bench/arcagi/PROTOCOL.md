# ARC-AGI-3 benchmark — protocol

This folder is the ARC-AGI-3 benchmark harness for ASSAY: the world adapter,
the pre-registered action registries, and the results record (`RESULTS.md`).

## Running a game

Requirements beyond the core harness: the `arc-agi` client library must be
importable in the interpreter that serves the broker (install it into the
runtime `bin/assay` selects: `python3 -m pip install arc-agi`). The first run
of a public game needs `ARC_API_KEY` set once to download it into the durable
local cache (`~/.cache/assay/arcade`, override with `ASSAY_CACHE_DIR`);
after that, runs are offline. Older local game caches are adopted
automatically.

```bash
ASSAY=<repo>/bin/assay
mkdir <run-dir> && cd <run-dir>            # one directory = one run
"$ASSAY" start ft09 \
    --adapter <repo>/bench/arcagi/adapter.py:factory \
    --registry <repo>/bench/arcagi/registry_200.json
```

Registries: `registry_200.json` (cap 200), `registry_500.json` (cap 500),
`registry_1500.json` (cap 1500, for games whose published reference cost
exceeds 500). All three register the numbered ARC action vocabulary
(ACTION1–7 plus built-in RESET), `hand_cap: null`, and carry the per-action
`description` hints (up/down/left/right/interact/point/undo) so runs keep
information parity with the hint line the earlier record was earned under.
`zero_prior` stays OFF for comparability; turn it on to withhold the hints.

## Pre-registered protocol (the rc1 batch, 2026-08-22)

Two stages, in order. All runs: Opus (owner law), local simulator, tier-2
starting information (the DOCTRINE manual + "64x64 color grid, level counter
and win state exist" + the registry description hints at parity — zero-prior
OFF so the 500-cap results stay comparable to the 200-cap record), fresh cold
runs (no imports — carryover would confound the cap comparison), sealed
paths, player does its own work.

### Stage 1 — ft09 regression gate (every kernel milestone re-runs ARC)

- Build under test: v1-rc1 (74 tests green; grid/numbered-mode/bypass/export
  smokes).
- Registry: `registry_200.json` — cap 200, `hand_cap: null` (parity: the
  200-cap winners used 10-step hand batches; the standing law demands zero
  added mandatory friction on ARC).
- **Bar (v1 placeholder, already registered): WIN 6/6 within ~1.5x of 75
  actions (<= 113).** Reference points: arc-skill's published run 75, our
  control run of arc-skill 78, ASSAY v1 81.
- Also checked: `assay audit` CLEAN (chain intact, zero ungated), no new gate
  ever fired (the friction check), meters present.
- Fail -> stop, diagnose, fix before any rerun (a milestone that regresses
  ARC does not ship).

### Stage 2 — the 500-cap reruns (su15, ls20, lf52)

- Registry: `registry_500.json` — cap 500, `hand_cap: null`.
- Fresh directories, one per game.
- Reference points (arc-skill's published scorecard, uncapped, same
  instances): su15 117 (2 resets) · ls20 481 (3 resets) · lf52 787 (1 reset).
  Our capped record going in: su15 8/9 @157 · ls20 4/7 @200 · lf52 4/10 @196.
- Pre-registered readings, stated before results:
  - ls20/lf52 wins inside 500 confirm the cap-artifact diagnosis; a win under
    the reference system's own action count is an outperform datum; non-wins
    at 500 weaken the cap story and point back at method.
  - su15 was NOT cap-bound (arc-skill won under 200): a win here says
    persistence/budget slack, not the cap, was the binding factor; a repeat
    8/9 stall at the same mechanic says method gap.
- After each run: journal audit (`assay audit` + the standard
  paid/ungated/contiguity sweep), exploration-tax summary, results entry in
  `RESULTS.md`.
- Then STOP; the owner defines next steps.

Single runs per cell, n=1 discipline applies; direction only.
