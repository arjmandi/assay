# Published chain heads: the E2 evidence pack (resumed proof states)

**The commitment:** these are the recomputed `assay-journal-v1` chain heads of the ten E2 journals: five archived campaign journals cut at the event where a session declared a level impossible and resumed by claude-opus-5 on 2026-10-06 in two arms, a module arm (coverage-audit module pinned in advise mode) and a control arm (unmodified registry). Each journal's prefix up to the cut event is byte-identical to the archived journal in `evidence/arcagi/`, whose published head therefore commits the prefix. The design is pre-registered in `E1_E2_PROTOCOL.md` section 5 and amendments 11 and 12. Whenever one of these run directories is shared with anyone, it must verify against its head here:

```bash
gunzip -k <run>/journal.jsonl.gz
python3 ../../verify/assay_verify.py <run>/journal.jsonl --expect-head <head below>
```

Every journal below was verified this way from the published copy before the commit, with `chain.json` beside the decompressed journal so the stored chain is checked too. The `assay-verify` column is the checker's verdict on that copy, and `ungated` is its count of paid non-reset events without a prediction.

| Run | Game | Arm | Cut event | Events | Paid | After cut | Progress | State | Stored chain | ungated | assay-verify | Chain head (SHA-256) | Prefix source head (evidence/arcagi) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s5i5-resume-ctrl | s5i5 | control | e273 | 365 | 364 | 91 | 8/8 | WIN | intact | 0 | CLEAN | `70b0484637c726755f996ea841fd8e1f5a29e4241fcf2ef1fdcc15a34c00fb3d` | `3f0fc13035fa…` |
| s5i5-resume | s5i5 | module | e273 | 380 | 379 | 106 | 8/8 | WIN | intact | 0 | CLEAN | `3f7b3d133a6b502d0df72b41ecbe14bfc82a91163628674274bb550c15786f7b` | `3f0fc13035fa…` |
| wa30-resume-ctrl | wa30 | control | e990 | 1308 | 1307 | 317 | 9/9 | WIN | intact | 0 | CLEAN | `c96142a190b9bef0c13533f34d22ab3bbcfbb1003625e4da3eeebaad458d35dd` | `c2630cbe1251…` |
| wa30-resume | wa30 | module | e990 | 1181 | 1180 | 190 | 9/9 | WIN | intact | 0 | CLEAN | `c0d1dd1377330c9856bb39de3257ee842571d2768049b8d790c07896a23f0201` | `c2630cbe1251…` |
| sk48-resume-ctrl | sk48 | control | e111 | 482 | 481 | 370 | 8/8 | WIN | intact | 0 | CLEAN | `531d0050d0c4f9a645427d7189e5d3d4ce25e2891b2cd8c3b2acca9a59ee599d` | `4ffed0406da9…` |
| sk48-resume | sk48 | module | e111 | 463 | 462 | 351 | 8/8 | WIN | intact | 0 | CLEAN | `6a63efa9db2c19426c6009a87dee23d0646f94c2d8820e29b24470ba50afedcb` | `4ffed0406da9…` |
| dc22-resume-ctrl | dc22 | control | e634 | 795 | 794 | 160 | 6/6 | WIN | intact | 0 | CLEAN | `5459e4492ba54719b7a76b32a6bc7fdb964dbdc344563081dc71f44429245957` | `f7a0991384f6…` |
| dc22-resume | dc22 | module | e634 | 812 | 811 | 177 | 6/6 | WIN | intact | 0 | CLEAN | `c0ad80930fc257cdeb2881ec28668dd299ebc33f02d0675959a79269757b3377` | `f7a0991384f6…` |
| bp35-resume-ctrl | bp35 | control | e368 | 719 | 718 | 350 | 9/9 | WIN | intact | 0 | CLEAN | `c6175498da3c3d5f93576a7e138ac15ec8a287be5ccb45c371e0b0645cbcbacc` | `4235b41aa8d3…` |
| bp35-resume | bp35 | module | e368 | 498 | 497 | 129 | 9/9 | WIN | intact | 0 | CLEAN | `e713aa5a8fa898e513174998a24b941740d60b030bbc433d35ed7abbb4deb825` | `4235b41aa8d3…` |

**Total paid actions: 6993** across 10 runs, 2241 of them after the cut, 10 wins. The paid count of each journal includes its archived prefix (the cut event's id equals the prefix's paid-action count, since e0 is the unpaid START).

Machine-readable copy: `heads.json`.
