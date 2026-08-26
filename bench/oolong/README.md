# OOLONG benchmark

Planned benchmark; runs and results will be documented here. It will follow
the same shape as `bench/arcagi`: a world adapter exposing
`factory(root, config)`, pre-registered action registries, a PROTOCOL.md, and
a RESULTS.md.

Current status: **M1 built** — the query-framed corpus-pack adapter and
registry (`adapter.py`, `registry_40.json`, `registry_200.json`), the sealed
scorer reusing OOLONG's own scoring code (`scorer.py`, `vendor/`), the
extract-and-dedup pack tooling (`packs/`), and the two smoke-test packs
(`spam4k`, `spam8k`). See `PROTOCOL.md` for the pack format, actuators
(`BANK_FACT`, `SUBMIT`), the v1 census, sealed-scoring flow, version pins, and
the determinism argument; `RESULTS.md` records the hand-driven acceptance run
(no LLM). Scored runs with a model, the long-corpus length ladder, and the
goal-framed variant are later milestones.

`RESEARCH.md` is the source dossier — what OOLONG is (synth/real splits,
scoring, kin like BABILong), the published model-only leaderboard, how Prime
Agent benchmarked it (harness vs model axis, their controls), cost and duration
estimates, and the two ASSAY framings: the **query-framed corpus pack** with the
pre-registered three-arm pilot E-C7 (bare model vs plain agentic retrieval vs
ASSAY), and the **goal-framed variant we introduce** (corpus + goal + task
world, span-truth and world-truth graded together).
