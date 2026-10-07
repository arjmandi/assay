# Factorio benchmark

The Factorio Learning Environment (FLE) benchmark harness for ASSAY: a world
adapter exposing `factory(root, config)`, pre-registered action registries, and
the protocol record.

Status: **M2 calibration complete** (2026-08-26): three lab-play throughput
tasks played, three won (ironplate 5 actions, irongear 9, circuit 9), every
journal CLEAN under `assay audit` and the independent checker, zero policy refusals. See
`RESULTS.md`. The 24-task sweep (M3) has not run.

| file | what it is |
|---|---|
| `PROTOCOL.md` | run recipe, version pins, world-id table, milestone ladder, holdout in ticks, determinism, M1 acceptance record |
| `NAMESPACE_AUDIT.md` | proof that stock FLE hands agent programs the raw RCON client, and the filter added in response |
| `FLE_API.md` | how a run obtains the FLE API reference at parity with published agents, and what this adapter changes about it |
| `adapter.py` | the world adapter: owns the server connection, screens programs, computes the throughput verifier |
| `registry_lab64.json` | Option A registry, action budget 64 (FLE v0.3 evaluation cap) |
| `registry_lab128.json` | the same, budget 128 (FLE paper cap) |
| `RESEARCH.md` | the source dossier: the game, FLE, published results, the Prime Agent incident, cost estimates, the M0–M4 rollout |
