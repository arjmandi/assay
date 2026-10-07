# Published chain heads: the E3 evidence pack (carryover import)

**The commitment:** these are the recomputed `assay-journal-v1` chain heads of the two E3 journals: fresh runs of ft09 and tr87 that started from an import of the E1 gated seed-1 run's exported knowledge, carried FOREIGN (demoted until re-earned), played by claude-opus-5 on 2026-10-07. The design is pre-registered in `E3_PROTOCOL.md`. Whenever one of these run directories is shared with anyone, it must verify against its head here:

```bash
gunzip -k <run>/journal.jsonl.gz
python3 ../../verify/assay_verify.py <run>/journal.jsonl --expect-head <head below>
```

Every journal below was verified this way from the published copy before the commit, with `chain.json` beside the decompressed journal so the stored chain is checked too. The `assay-verify` column is the checker's verdict on that copy, and `ungated` is its count of paid non-reset events without a prediction.

| Run | Game | Import source | Events | Paid | Progress | State | Stored chain | ungated | assay-verify | Chain head (SHA-256) |
|---|---|---|---|---|---|---|---|---|---|---|
| ft09-import-s1 | ft09 | evidence/e1/ft09-gated-s1 | 81 | 80 | 6/6 | WIN | intact | 0 | CLEAN | `5b5ad05569fb80362e3e0f125a86d5c16a95dbbf4514b07156c19faab22a357c` |
| tr87-import-s1 | tr87 | evidence/e1/tr87-gated-s1 | 120 | 119 | 6/6 | WIN | intact | 0 | CLEAN | `bae62c21c55d3e2a1e58e5bcdf86e03e3f41a7241d28cdbe81df5f2896afdfbb` |

**Total paid actions: 199** across 2 runs, 2 wins.

Machine-readable copy: `heads.json`.
