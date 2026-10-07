# OOLONG benchmark

The OOLONG benchmark harness for ASSAY, in the same shape as `bench/arcagi`:
a world adapter exposing `factory(root, config)`, pre-registered action
registries, a `PROTOCOL.md`, and a `RESULTS.md`.

Status: **M2 complete** (2026-08-28). The query-framed corpus-pack adapter and
registries (`adapter.py`, `registry_40.json`, `registry_200.json`), the sealed
scorer reusing OOLONG's own scoring code (`scorer.py`, `vendor/`), the pack
tooling (`packs/`), the smoke packs (`spam4k`, `spam8k`) and the length-ladder
packs (synth 128K, 1M, 4M). `RESULTS.md` records the four-arm comparison (the
raw model, a naive agent, a published agent, and ASSAY de-hinted) on the
length ladder, with the honest confounds. `PROTOCOL.md` has the pack format,
the actuators (`BANK_FACT`, `SUBMIT`), the v1 census, the sealed-scoring flow,
the version pins and the determinism argument. The spam4k pack is also a tenant
of the harness's own test suite (`tests/test_oolong_tenant.py`). Vocabulary:
ASSAY's prose says world and progress unit; here the world id is the pack id,
a progress unit is one question answered (`win_levels` is the question count),
and WIN is every question submitted with the census clean.

`RESEARCH.md` is the source dossier — what OOLONG is (synth/real splits,
scoring, kin like BABILong), the published model-only leaderboard, how Prime
Agent benchmarked it (harness vs model axis, their controls), cost and duration
estimates, and the two ASSAY framings: the **query-framed corpus pack** with the
pre-registered three-arm pilot E-C7 (bare model vs plain agentic retrieval vs
ASSAY), and the **goal-framed variant we introduce** (corpus + goal + task
world, span-truth and world-truth graded together).
