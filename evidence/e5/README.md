# Evidence pack №10: E5, OOLONG 1M in batched bank mode

The tenth evidence pack under the `assay-journal-v1` standard: the one journal
of experiment E5, the OOLONG synth1m run (the 1M rung, app_reviews, 25
questions) answered in batched bank mode on the release build, published so
that the paper's cost comparison at that rung is checkable against a
committed head.

## The design, in three sentences

The de-hinted ASSAY run of `../oolong` answered the 1M rung at 0.740 for
48.76 USD and 114 minutes where a plain Claude Code agent reached the same
0.740 for 8.20 USD and 30 minutes. E5 asked whether that gap belongs to the
adapter's actuator shape (one fact per `BANK_FACT`, base64 arguments, a
base64 `SUBMIT`) or to the kernel's per-action discipline, by keeping the
kernel unchanged and running the same de-hinted prompt against
`bench/oolong/registry_200_batch.json` (`control.bank_mode: batch`, several
spans per `BANK_FACT`, a plain-text `SUBMIT`). One session of claude-opus-5
on commit 6ea56e4, the build the paper calls 1.1.0 (the checkout was
d99c0a6, which has the same kernel and adapters), budget 200, cost bands
fixed before launch.

The pre-registered protocol is `E5_PROTOCOL.md` and the results write-up is
`E5_RESULTS.md`, both archived with the paper materials.

## Contents

- **`HEADS.md` / `heads.json`**: the commitment artifact, one row, with the
  checker's verdict on the published copy, the accuracy, the cost at list
  price and the wall time.
- **`synth1m-batch/`**:
  - `journal.jsonl.gz`: the complete journal (every prediction registered
    before its action, every machine grade, the agent's notes in full).
  - `chain.json`: the writer's stored chain state `{event_id, head}`.

The corpus is not in the journal by design: each observation carries
`corpus_path` and `corpus_sha256` (d14a6c74..., the pack the comparison row
read, rebuilt with `bench/oolong/packs/build_pack.py fetch synth1m` from the
public `oolongbench/oolong-synth` dataset at the pinned revision). The sealed
score and the agent's offline work stay with the archived run directory.

## Verify the journal

```bash
gunzip -k synth1m-batch/journal.jsonl.gz
python3 ../../verify/assay_verify.py synth1m-batch/journal.jsonl \
  --expect-head eda4c0bd5f8d315268b604e7f2946dc599eacbeb613852039612ae1ac7e89f8e
```

Decompressing beside `chain.json` lets the checker report the stored chain
as `intact` as well as recomputing the head.

## What the table says

WIN 25 of 25 at 0.740 (the per-question vector every arm produced at this
rung), 50 paid actions, 82 spans banked, zero refusals, 50 of 50 predictions
held, 43.39 USD and 76 minutes against 48.76 USD and 114 minutes in single
mode. The journal's timestamps put the first paid action 68 minutes after
the start and all fifty paid actions inside seven and a half minutes. The
reading is in `E5_RESULTS.md`; this pack publishes the record and does not
interpret it.
