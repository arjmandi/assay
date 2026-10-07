# Published chain heads: the E1b evidence pack (the instrument removed)

**The commitment:** these are the recomputed `assay-journal-v1` chain heads of the six E1b journals: three ARC-AGI-3 games, two seeds, played by claude-opus-5 on 2026-10-07 under the release build, commit 6ea56e4, the build the paper calls 1.1.0 (the checkout was `release/1.1.0` at a4e1d90, renamed 063d3c4 by the history rewrite of that day, which is how `heads.json` still records it) with the registry key `gate: off`, a manual that carries no prediction discipline and the ungated prompt template. The design is pre-registered in `E1B_PROTOCOL.md`. Whenever one of these run directories is shared with anyone, it must verify against its head here:

```bash
gunzip -k <run>/journal.jsonl.gz
python3 ../../verify/assay_verify.py <run>/journal.jsonl --expect-head <head below>
```

Every journal below was verified this way from the published copy before the commit, with `chain.json` beside the decompressed journal so the stored chain is checked too. The `assay-verify` column is the checker's verdict on that copy and `ungated` is its count of paid non-reset events without a prediction. In this arm every paid action is ungated, so every verdict is INVALID FOR SCORING: that is the arm's design, not a defect, and the heads commit to it.

| Run | Game | Arm | Seed | Gate | Cap | Events | Paid | Progress | State | Stored chain | ungated | assay-verify | Chain head (SHA-256) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ft09-off-s1 | ft09 | instrument removed | 1 | off | 200 | 81 | 80 | 6/6 | WIN | intact | 80 | INVALID FOR SCORING | `96b5583fb5cc8232af2d6919a7dd39ab39e2436bddc56fb74130b6b38e2db421` |
| ft09-off-s2 | ft09 | instrument removed | 2 | off | 200 | 81 | 80 | 6/6 | WIN | intact | 80 | INVALID FOR SCORING | `c9fb9f2e25e94c36ef80f33581013bce07f338b8bbb383da5ba2d5e072a291af` |
| tr87-off-s1 | tr87 | instrument removed | 1 | off | 1500 | 450 | 449 | 6/6 | WIN | intact | 449 | INVALID FOR SCORING | `1813278064f502b4aa79055df48e2e54090d9bac743d5096da48df30bf78e318` |
| tr87-off-s2 | tr87 | instrument removed | 2 | off | 1500 | 157 | 156 | 6/6 | WIN | intact | 156 | INVALID FOR SCORING | `12fe40d307f2bf413008de224a8633225da987bfd4432bfec41716e7a329ad5e` |
| cn04-off-s1 | cn04 | instrument removed | 1 | off | 1500 | 213 | 212 | 6/6 | WIN | intact | 212 | INVALID FOR SCORING | `68d106283475e421ebb0ed215383aff9c5cd5472f049d0fb07a8d41e8c728e07` |
| cn04-off-s2 | cn04 | instrument removed | 2 | off | 1500 | 221 | 220 | 6/6 | WIN | intact | 220 | INVALID FOR SCORING | `a15c5448e99f0f3863540b6d58de75c1144203c2bede9b13d60b52e84ae30134` |

**Total paid actions: 1197** across 6 runs.

The E1 comparison these runs extend (gated 1,192, ungated 1,128, instrument removed 1,197 paid actions over the same three games and two seeds) is in `E1_E2_RESULTS.md` section 10. This pack publishes the record and does not interpret it.

Machine-readable copy: `heads.json`.
