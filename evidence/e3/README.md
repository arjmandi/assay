# Evidence pack №6: E3, carryover of the agent's own export

The sixth evidence pack under the `assay-journal-v1` standard: the two
journals of experiment E3, fresh runs that started from an import of a prior
run's exported knowledge.

## The design, in three sentences

E3 asks whether knowledge exported from one run and imported into a fresh run
of the same game changes the paid-action count. For ft09 and tr87 the E1
gated seed-1 run (`evidence/e1/<game>-gated-s1`) was exported with the
kernel's export command (the notes file, its Verified lines citing event ids,
up to twenty verifier sources with their graded statistics, model and rules
files if present, hazard tags and a journal digest) and imported at run
creation with `assay start --import`, before the player existed. The player
then received the E1 prompt unchanged and played under the same registry and
constitution as the source.

The pre-registered protocol is `E3_PROTOCOL.md`. The results write-up is
`E1_E2_RESULTS.md` section 5 and the per-run metrics with chain heads are in
`E1_E2_E3_run_table.json`. The export artifacts
(`exports/<game>-from-gated-s1/assay_knowledge.json`) and the per-run
provenance files (`<run>.provenance/PROVENANCE.md` and `provenance.json`,
which record the source snapshot hash before and after the export, the
kernel calls and the file classification of the new run directory) are
archived with the paper materials.

## The imports carried the FOREIGN demotion

Imported knowledge enters a run FOREIGN, by the kernel's carryover rule:
every Verified line of the imported notes is marked Assumed in the
`PRIOR-NOTES.md` the agent reads, imported verifiers are candidates that earn
standing only by being named in a prediction and graded again in the current journal, an
imported model carries no batching rights until it passes replay-fit on the
current journal, and the status block states all of this to the agent at
every status call. In both E3 runs no imported verifier file was named in a prediction. The
journals record the fresh predictions the agent made instead.

One reading note: event e0 (START) was written by the import step about forty
minutes before the player's first action. It is not a paid action and the
gap is a preparation artifact, not a session boundary.

## Contents

- **`HEADS.md` / `heads.json`**: the commitment artifact, one row per run,
  with the import source and the checker's verdict on the published copy.
- **`<run>/`**, one folder per run, named `<game>-import-s1`:
  - `journal.jsonl.gz`: the complete journal.
  - `chain.json`: the writer's stored chain state `{event_id, head}`.

The pinned registries and creation records stay with the archived run
directories (they carry the experiment machine's paths). The import source
of each run is in `heads.json`; the registry match the import rule requires
is recorded in the archived creation records.

## Verify a journal

```bash
gunzip -k tr87-import-s1/journal.jsonl.gz
python3 ../../verify/assay_verify.py tr87-import-s1/journal.jsonl \
  --expect-head bae62c21c55d3e2a1e58e5bcdf86e03e3f41a7241d28cdbe81df5f2896afdfbb
```

The other head is in `heads.json`.

## What the table says

Two runs, two wins. ft09: 80 paid actions (the source run: 80). tr87: 119
paid actions (the source run: 324). The protocol's readings are in
`E1_E2_RESULTS.md` section 5. This pack publishes the record and does not
interpret it.
