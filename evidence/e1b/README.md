# Evidence pack №8: E1b, the prediction instrument removed (ARC-AGI-3)

The eighth evidence pack under the `assay-journal-v1` standard: the six
journals of experiment E1b, published so that the paper's third E1 arm is
checkable by anyone, run by run, against a committed head.

## The design, in three sentences

E1 compared a gated arm (`gate: required`) with an ungated control
(`gate: optional`) in which the prediction instrument was still available and
the agents kept using it. E1b removes the instrument: the registry key is
`gate: off`, the agent manual carries no prediction discipline and no mention
of the flag, and the ungated prompt template of E1 is used unchanged. The same
three ARC-AGI-3 games (ft09 at cap 200, tr87 and cn04 at cap 1500) were each
played twice by claude-opus-5 on the release build (`release/1.1.0` at
a4e1d90), one session per run, with the built-in coverage audit active in
advise mode as it is in every 1.1.0 run.

The pre-registered protocol is `E1B_PROTOCOL.md`. The results write-up is
`E1_E2_RESULTS.md` section 10 and the per-run metrics with chain heads are in
`E1B_run_table.json`. All three are archived with the paper materials.

## Why every verdict reads INVALID FOR SCORING

Under `gate: off` the kernel journals every paid action as UNGATED, because
no prediction was registered before it. The checker marks a journal with any
UNGATED event invalid for scoring, which is the property the gated runs are
scored on. In this arm every paid action is ungated, so the verdict on all six
journals is INVALID FOR SCORING, and the `ungated` column of `HEADS.md` equals
the paid count. That is the arm's design: the chains are intact, the heads
match, the wins are the world's, and what the verdict says is that none of
these actions was predicted. The pack commits to that record exactly as it
commits to the clean ones.

## Contents

- **`HEADS.md` / `heads.json`**: the commitment artifact, one row per run,
  with the checker's verdict on the published copy.
- **`<run>/`**, one folder per run, named `<game>-off-s<seed>`:
  - `journal.jsonl.gz`: the complete journal (every action, every machine
    grade, the agent's notes in full).
  - `chain.json`: the writer's stored chain state `{event_id, head}`.

The pinned registries (`registry_e1b_off_200.json`,
`registry_e1b_off_1500.json`), the E1b manual and its diff against the 1.1.0
constitution, and the creation records stay with the archived run directories
(they carry the experiment machine's paths).

## Verify a journal

```bash
gunzip -k ft09-off-s1/journal.jsonl.gz
python3 ../../verify/assay_verify.py ft09-off-s1/journal.jsonl \
  --expect-head 96b5583fb5cc8232af2d6919a7dd39ab39e2436bddc56fb74130b6b38e2db421
```

The checker exits 1 on this pack, because the verdict is INVALID FOR SCORING,
while reporting the head as a match. The other heads are in `heads.json`.

## What the table says

Six runs, six wins, 1,197 paid actions, against 1,192 for the gated E1 arm
and 1,128 for the ungated one over the same games and seeds. The protocol's
interpretation table and the reading are in `E1_E2_RESULTS.md`. This pack
publishes the record and does not interpret it.
