# Published chain heads: the E1 evidence pack (gated versus ungated)

**The commitment:** these are the recomputed `assay-journal-v1` chain heads of the twelve E1 journals: three ARC-AGI-3 games, two seeds, a gated arm (`gate: required`) and an ungated control arm (`gate: optional`), played by claude-opus-5 under the ASSAY harness on 2026-10-06. The design is pre-registered in `E1_E2_PROTOCOL.md` sections 1 to 10. Whenever one of these run directories is shared with anyone, it must verify against its head here:

```bash
gunzip -k <run>/journal.jsonl.gz
python3 ../../verify/assay_verify.py <run>/journal.jsonl --expect-head <head below>
```

Every journal below was verified this way from the published copy before the commit, with `chain.json` beside the decompressed journal so the stored chain is checked too. The `assay-verify` column is the checker's verdict on that copy, and `ungated` is its count of paid non-reset events without a prediction.

| Run | Game | Arm | Seed | Gate | Events | Paid | Progress | State | Stored chain | ungated | assay-verify | Chain head (SHA-256) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ft09-gated-s1 | ft09 | gated | 1 | required | 81 | 80 | 6/6 | WIN | intact | 0 | CLEAN | `b089902b55c474664afcfc6131fc4750adacc2220f9aaf566ec820c17a9ccfe9` |
| ft09-gated-s2 | ft09 | gated | 2 | required | 76 | 75 | 6/6 | WIN | intact | 0 | CLEAN | `7efb30f0d5242c5c10d0b4e09c84566aaab515fa5d078ff557c22c3d5f498c1d` |
| ft09-ungated-s1 | ft09 | ungated | 1 | optional | 81 | 80 | 6/6 | WIN | intact | 0 | CLEAN | `aeae3ac07c7a11ecf47509e02b6d7e166c1d7851e2326a26693e77a73cbc32b0` |
| ft09-ungated-s2 | ft09 | ungated | 2 | optional | 81 | 80 | 6/6 | WIN | intact | 0 | CLEAN | `e79887550b475b4a680b405700de151343b1d78e30bff9f664e0e8d2638cad0a` |
| tr87-gated-s1 | tr87 | gated | 1 | required | 325 | 324 | 6/6 | WIN | intact | 0 | CLEAN | `e8f6b941a065b8b1ec781cf2e860dfa0b8d3e37f9a5edb35e87b94c3c861317d` |
| tr87-gated-s2 | tr87 | gated | 2 | required | 217 | 216 | 6/6 | WIN | intact | 0 | CLEAN | `debf30e47668e854999610b142fcd16ee2b67afc7f8eb9550ac228c8e89bf32a` |
| tr87-ungated-s1 | tr87 | ungated | 1 | optional | 283 | 282 | 6/6 | WIN | intact | 0 | CLEAN | `50aff132d7671cd8a78995b885a9dde5caa000a89edb60931eb4e3663a2f4ee0` |
| tr87-ungated-s2 | tr87 | ungated | 2 | optional | 186 | 185 | 6/6 | WIN | intact | 0 | CLEAN | `883ab0d29e06399b7de057e7f4d62832a6a80ecd838a277a0061f744afd6fe2f` |
| cn04-gated-s1 | cn04 | gated | 1 | required | 223 | 222 | 6/6 | WIN | intact | 0 | CLEAN | `2d7449627a227b6bb7e8de45e9ef43511caba724a642e3f4450d4470f40b3545` |
| cn04-gated-s2 | cn04 | gated | 2 | required | 276 | 275 | 6/6 | WIN | intact | 0 | CLEAN | `8d5d4b4bdd515d64a483238715dedf240e1e2b896e3971b017f3972d56b3f3c5` |
| cn04-ungated-s1 | cn04 | ungated | 1 | optional | 202 | 201 | 6/6 | WIN | intact | 0 | CLEAN | `b5f1d53d5d50a7f8e04cd97ebd160bba3a0a3c22cac9db9823927ceb4c7e4bc6` |
| cn04-ungated-s2 | cn04 | ungated | 2 | optional | 301 | 300 | 6/6 | WIN | intact | 0 | CLEAN | `ff715f95c9fb8ed89cc0b80a4a43ac70e360c20a3fca467b16d9d646fed93281` |

**Total paid actions: 2320** across 12 runs, 12 wins.

Machine-readable copy: `heads.json`.
