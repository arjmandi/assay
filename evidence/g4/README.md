# Evidence pack №7: G4, the live regression gate of the build the paper calls 1.1.0

The seventh evidence pack under the `assay-journal-v1` standard: the one
journal behind the G4 gate of `RELEASE_CHECKLIST.md`, published so the
release's live proof is checkable against a committed head like every other
run the harness reports.

## The gate, in two sentences

Before the release is tagged, its kernel plays ft09 once under the standard
protocol (`bench/arcagi/registry_200.json`, the release build's constitution,
the gated prompt template of experiment E1, cap 200, claude-opus-5, one
session) and must WIN 6 of 6 within the bar of 113 paid actions set by
decision 18 of the paper's decision log. It won in 80 paid actions, every one
of them predicted, on `release/1.1.0` at 82bfd5d on 2026-10-07 (3b7eb19 after
the history rewrite of that day). The build the paper calls 1.1.0 is named by
its final commit, 6ea56e4; the kernel commits between the two change no
journal field and no grading rule (`CHANGELOG.md`), so the gate stands for it.

## Contents

- **`HEADS.md` / `heads.json`**: the commitment artifact, one row, with the
  checker's verdict on the published copy, the per-unit paid actions and the
  bar.
- **`ft09-release-s1/`**:
  - `journal.jsonl.gz`: the complete journal (every prediction registered
    before its action, every machine grade, the agent's notes in full).
  - `chain.json`: the writer's stored chain state `{event_id, head}`.

The pinned registry is `bench/arcagi/registry_200.json` of this repository
(its hash is the run's `registry_hash`,
`49a5ed90e1444c08c6bda1b7e1d4dfddace57aeb9e93dcb743bff8f1de9d58dc`). The
creation record stays with the archived run directory (it carries local
paths).

## Verify the journal

```bash
gunzip -k ft09-release-s1/journal.jsonl.gz
python3 ../../verify/assay_verify.py ft09-release-s1/journal.jsonl \
  --expect-head cc65462fd7dc52106ed3afe90a51d0e5c2fa7f3e5045b218c496166d7a8e8350
```

Decompressing beside `chain.json` lets the checker report the stored chain
as `intact` as well as recomputing the head.
