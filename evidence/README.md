# evidence: the published journals

Every run the harness reports is published here as its complete journal, a
committed chain head and the verdict recorded at publication, under the `assay-journal-v1`
standard (`../verify/JOURNAL_SPEC.md`). The packs:

| pack | what | journals |
|---|---|---|
| `arcagi/` | the 25-game ARC-AGI-3 public-set record, with the benchmark's scoring function | 25 |
| `factorio/` | the three Factorio (FLE) calibration wins | 3 |
| `oolong/` | the three un-hinted OOLONG runs on the length ladder | 3 |
| `e1/` | experiment E1, gated versus ungated (three games, two seeds, two arms) | 12 |
| `e2/` | experiment E2, resumed proof states with and without the coverage audit | 10 |
| `e3/` | experiment E3, carryover of the agent's own export | 2 |
| `g4/` | the live ft09 regression gate of the build the paper calls 1.1.0 (commit 6ea56e4) | 1 |
| `e1b/` | experiment E1b, the prediction instrument removed (three games, two seeds, gate off) | 6 |
| `factorio-110/` | the two Factorio lab tasks replayed on commit 6ea56e4, and the hand-played smoke | 3 |
| `e5/` | experiment E5, OOLONG 1M in batched bank mode on commit 6ea56e4 | 1 |

Each pack has a `README.md`, a `HEADS.md` with the published heads and
verdicts, and the machine-readable `heads.json`. Journals are gzipped
(`journal-<name>.jsonl.gz`, or `<run>/journal.jsonl.gz` beside the writer's
`chain.json`).

Check every journal against its published head with the kernel's own chain
and ungated rules (run it with the interpreter the test suite uses):

```bash
python3 evidence/verify_all.py
```

A run directory shared later verifies with `assay --run-dir <run> audit`, and
its `.assay/chain.json` head must equal the head published here.

The packs are MIT (`../verify/LICENSE`). They moved here from the retired
`assay-verify` repository in 1.2.0.
