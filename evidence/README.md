# evidence: the published journals

Every run the harness reports is published here as its complete journal, a
committed chain head and the checker's verdict, under the `assay-journal-v1`
standard (`../verify/JOURNAL_SPEC.md`). The packs:

| pack | what | journals |
|---|---|---|
| `arcagi/` | the 25-game ARC-AGI-3 public-set record, with the benchmark's scoring function | 25 |
| `factorio/` | the three Factorio (FLE) calibration wins | 3 |
| `oolong/` | the three un-hinted OOLONG runs on the length ladder | 3 |
| `e1/` | experiment E1, gated versus ungated (three games, two seeds, two arms) | 12 |
| `e2/` | experiment E2, resumed proof states with and without the coverage audit | 10 |
| `e3/` | experiment E3, carryover of the agent's own export | 2 |
| `g4/` | the live ft09 regression gate of release 1.1.0 | 1 |

Each pack has a `README.md`, a `HEADS.md` with the published heads and
verdicts, and the machine-readable `heads.json`. Journals are gzipped
(`journal-<name>.jsonl.gz`, or `<run>/journal.jsonl.gz` beside the writer's
`chain.json`).

Check every journal against its published head with the standard library
alone:

```bash
python3 evidence/verify_all.py
```

The packs are MIT (`../verify/LICENSE`). They moved here from the retired
`assay-verify` repository in 1.1.0.
