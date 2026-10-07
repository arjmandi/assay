# Published chain heads: the Factorio 1.1.0 evidence pack (the release-build reproduction)

**The commitment:** the recomputed `assay-journal-v1` chain heads of the two Factorio lab-task wins replayed on the release build (`release/1.1.0` at d99c0a6, 2026-10-07, claude-opus-5, cap 64, the M2 registry and strategy hints) and of the hand-played smoke that preceded them on the same build. Any shared copy of these runs must verify against its head here:

```bash
gunzip -k <run>/journal.jsonl.gz
python3 ../../verify/assay_verify.py <run>/journal.jsonl --expect-head <head below>
```

Every journal below was verified this way from the published copy before the commit, with `chain.json` beside the decompressed journal so the stored chain is checked too.

| Task | Player | Cap | Events | Paid | Progress | State | Stored chain | ungated | assay-verify | Chain head (SHA-256) |
|---|---|---|---|---|---|---|---|---|---|---|
| ironplate | claude-opus-5 | 64 | 6 | 5 | 4/4 | WIN | intact | 0 | CLEAN | `fcfbebc17442ac966a80b6aaaf8d31ed57f2be535000499c9429d24e2b395363` |
| circuit | claude-opus-5 | 64 | 16 | 15 | 4/4 | WIN | intact | 0 | CLEAN | `cb67fea573f4cb667b807f0ccfffe3f704ff5e7df9eedab909d2d5d0b3e19188` |
| smoke | hand-played, no model | 64 | 7 | 6 | 4/4 | WIN | intact | 0 | CLEAN | `57828a5a89e5e80dbd7765bc2c599b1c8b644797ce3674197e20dfa41a1cca3d` |

**Total paid actions: 26** across 3 runs.

The August wins these reproduce are `evidence/factorio` (ironplate 5 paid, circuit 9 paid). The pre-registered bands were twice those counts. The reading is in `FACTORIO_1_1_0_RESULTS.md`; this pack publishes the record and does not interpret it.

Machine-readable copy: `heads.json`.
