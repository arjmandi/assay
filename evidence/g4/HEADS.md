# Published chain head: the G4 evidence pack (the release gate)

**The commitment:** the recomputed `assay-journal-v1` chain head of the one
journal of G4, the live ft09 regression gate of the build the paper calls
1.1.0 (named by its final commit, 6ea56e4), played by claude-opus-5 on
`release/1.1.0` at 82bfd5d on 2026-10-07 (3b7eb19 after the history rewrite
of that day). Whenever this run directory is shared with anyone, it must
verify against its head here:

```bash
gunzip -k ft09-release-s1/journal.jsonl.gz
python3 ../../verify/assay_verify.py ft09-release-s1/journal.jsonl --expect-head <head below>
```

The journal was verified this way from the published copy before the commit,
with `chain.json` beside the decompressed journal so the stored chain is
checked too.

| Run | Game | Gate | Cap | Events | Paid | Per unit | Progress | State | Stored chain | ungated | Checker | Chain head (SHA-256) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ft09-release-s1 | ft09 | required | 200 | 81 | 80 | 4, 7, 14, 21, 21, 13 | 6/6 | WIN | intact | 0 | CLEAN | `cc65462fd7dc52106ed3afe90a51d0e5c2fa7f3e5045b218c496166d7a8e8350` |

The bar (decision 18): WIN 6 of 6 within 113 paid actions. Met at 80.
Machine-readable copy: `heads.json`.
