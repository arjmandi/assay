# OOLONG benchmark

The OOLONG benchmark harness for ASSAY, in the same shape as `bench/arcagi`:
a world adapter exposing `factory(root, config)`, pre-registered action
registries, a `PROTOCOL.md`, and a `RESULTS.md`.

Status: **M2 complete** (2026-08-28). The query-framed corpus-pack adapter and
registries (`adapter.py`, `registry_40.json`, `registry_200.json`, and
`registry_200_batch.json` for the E5 batch-banking variant), the sealed
scorer reusing OOLONG's own scoring code (`scorer.py`, `vendor/`), the pack
tooling (`packs/`), the smoke packs (`spam4k`, `spam8k`) and the manifests of
the length-ladder packs (synth 128K, 1M, 4M), rebuilt on first use (see Packs
below). `RESULTS.md` records the four-arm comparison (the
raw model, a naive agent, a published agent, and ASSAY de-hinted) on the
length ladder, with the honest confounds. `PROTOCOL.md` has the pack format,
the actuators (`BANK_FACT`, `SUBMIT`), the v1 census, the sealed-scoring flow,
the version pins and the determinism argument. The spam4k pack is also a tenant
of the harness's own test suite (`tests/test_oolong_tenant.py`). Vocabulary:
ASSAY's prose says world and progress unit; here the world id is the pack id,
a progress unit is one question answered (`win_levels` is the question count),
and WIN is every question submitted with the census clean.

## Packs

The repository ships the two smoke packs whole and every other pack as its
manifest only. The OOLONG dataset text is not published here: a pack beyond
the smoke packs is rebuilt on first use from the pinned dataset revision and
checked against the sha256 values in its manifest.

    pip install -e '.[oolong]'                        # pyarrow and pandas, once
    python bench/oolong/packs/build_pack.py fetch synth128k

`fetch` takes the shard from, in order, a `--parquet` path, ASSAY's own cache
(`$XDG_CACHE_HOME/assay/oolong`, default `~/.cache/assay/oolong`), the Hugging
Face hub cache when the pinned revision is already there, else one download
from the hub (2.3 MB for synth128k, 1.4 GB for synth4m). The rebuilt files must
hash to the manifest's values or they are deleted again. `build_pack.py verify
<id>` checks a built pack with the standard library alone, and the adapter
names the fetch command when a pack with a manifest is not built.

| pack | questions | corpus | in the repository |
|---|---|---|---|
| spam4k | 5 | 4K tokens, 10 115 chars | whole (the test suite's tenant) |
| spam8k | 25 | 8K tokens, 19 709 chars | whole |
| synth128k | 25 | 128K tokens, 384 019 chars | manifest, fetch on first use |
| synth1m | 25 | 1M tokens, 2 930 641 chars | manifest, fetch on first use |
| synth4m | 20 | 4M tokens, 11 303 606 chars | manifest, fetch on first use |

The dataset's own terms apply to the text a fetch produces (`NOTICE`). The
length-ladder packs were taken out of the repository and its history before
publication (1.1.0, owner decision O3).

`RESEARCH.md` is the source dossier — what OOLONG is (synth/real splits,
scoring, kin like BABILong), the published model-only leaderboard, how Prime
Agent benchmarked it (harness vs model axis, their controls), cost and duration
estimates, and the two ASSAY framings: the **query-framed corpus pack** with the
pre-registered three-arm pilot E-C7 (bare model vs plain agentic retrieval vs
ASSAY), and the **goal-framed variant we introduce** (corpus + goal + task
world, span-truth and world-truth graded together).
