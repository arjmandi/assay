# Published chain head: the E5 evidence pack (OOLONG 1M in batched bank mode)

**The commitment:** the recomputed `assay-journal-v1` chain head of the one E5 journal: OOLONG synth1m answered in batched bank mode by claude-opus-5 on 2026-10-07 under the release build (`release/1.1.0` at d99c0a6), the de-hinted prompt and `bench/oolong/registry_200_batch.json`. The design is pre-registered in `E5_PROTOCOL.md`. Whenever this run directory is shared with anyone, it must verify against its head here:

```bash
gunzip -k synth1m-batch/journal.jsonl.gz
python3 ../../verify/assay_verify.py synth1m-batch/journal.jsonl --expect-head <head below>
```

The journal was verified this way from the published copy before the commit, with `chain.json` beside the decompressed journal so the stored chain is checked too.

| Run | Rung | Arm | Budget | Events | Paid | Progress | State | Stored chain | ungated | assay-verify | Chain head (SHA-256) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| synth1m-batch | synth1m | de-hinted, batch mode | 200 | 51 | 50 | 25/25 | WIN | intact | 0 | CLEAN | `eda4c0bd5f8d315268b604e7f2946dc599eacbeb613852039612ae1ac7e89f8e` |

**Total paid actions: 50** across 1 run.

Accuracy 0.740 (17 of 25 exact, the sealed score and the standalone re-score agree), 43.39 USD at list price, 76 min 24 s, 82 spans banked, zero refusals, 50 of 50 predictions held. The single-mode row it is compared with is `evidence/oolong` (synth1m: 48.76 USD, 114 min). The reading is in `E5_RESULTS.md`; this pack publishes the record and does not interpret it.

Machine-readable copy: `heads.json`.
