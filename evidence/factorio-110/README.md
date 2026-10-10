# Evidence pack №9: Factorio (FLE) on the release build

The ninth evidence pack under the `assay-journal-v1` standard: the two
Factorio Learning Environment lab tasks replayed on commit 6ea56e4, the build
the paper calls 1.1.0 (`ironplate` and `circuit`, action cap 64,
claude-opus-5, one session each), and the hand-played six-action smoke that
ran on the same build before any model spend. They reproduce the August wins
of `../factorio` under the same registry (`bench/factorio/registry_lab64.json`,
whose hash the runs carry) and the same strategy hints, with the result bands
fixed before launch. The sessions' checkout was d99c0a6, which has the same
kernel and adapters as 6ea56e4. The pack keeps the name `factorio-110`, from
the version the build was then called, because the paper and `heads.json`
cite it.

## Contents

- **`HEADS.md` / `heads.json`**: the commitment artifact, one row per run,
  with the checker's verdict on the published copy, the cost at list price
  and the count of channel outcomes on paid actions.
- **`<run>/`**, one folder per run:
  - `journal.jsonl.gz`: the complete journal (every prediction registered
    before its action, every machine grade, the agent's notes in full).
  - `chain.json`: the writer's stored chain state `{event_id, head}`.

Each paid event's `data.program` field is the exact screened FLE program that
executed, base64-encoded. The world is deterministic under the pinned setup
(fle 0.4.3, server image 2.0.73, map seed 44340), so the recorded programs
replay without any model in the loop.

The pre-registered protocol is `FACTORIO_1_1_0_PROTOCOL.md` and the results
write-up is `FACTORIO_1_1_0_RESULTS.md`, both archived with the paper
materials.

## Verify a journal

```bash
gunzip -k circuit/journal.jsonl.gz
python3 ../../verify/assay_verify.py circuit/journal.jsonl \
  --expect-head cb67fea573f4cb667b807f0ccfffe3f704ff5e7df9eedab909d2d5d0b3e19188
```

Decompressing beside `chain.json` lets the checker report the stored chain
as `intact` as well as recomputing the head. The other heads are in
`heads.json`.

## What the table says

Iron plate won in 5 paid actions (August: 5) and electronic circuit in 15
(August: 9), both inside the pre-registered bands of twice the August count,
zero programs refused by the screen, and every paid action carrying at least
one channel outcome. This pack publishes the record and does not interpret it.
