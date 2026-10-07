# Evidence pack №4: E1, gated versus ungated (ARC-AGI-3)

The fourth evidence pack under the `assay-journal-v1` standard: the twelve
journals of experiment E1, published so that the paper's gated-versus-ungated
comparison is checkable by anyone, run by run, against a committed head.

## The design, in three sentences

E1 asks whether enforcing the prediction gate, as distinct from making the
prediction instrument available, changes what the agent achieves. Three
ARC-AGI-3 games (ft09 at cap 200, tr87 and cn04 at cap 1500) were each played
twice per arm by claude-opus-5 under the ASSAY harness (branch `exp/2026-10`):
a gated arm with the registry key `gate: required` and the standard
constitution, and a control arm with `gate: optional` and a constitution
stripped of the claim grammar, the verifier contract and the prediction
guidance. Everything else was held fixed: adapter, action descriptions, caps,
prompt template, model, tool permissions.

The pre-registered protocol is `E1_E2_PROTOCOL.md` (sections 1 to 10 fixed
before any run, sections 11 to 13 dated amendments and the launch incident).
The results write-up is `E1_E2_RESULTS.md` and the per-run metrics with chain
heads are in `E1_E2_E3_run_table.json`. All three are archived with the paper
materials.

## The ungated journals

The six `*-ungated-*` journals come from a control arm run with
`gate: optional` (the Gate column of `HEADS.md`). Under that
key the kernel accepts an action without a prediction and journals it as an
UNGATED event, which this checker marks invalid for scoring, as a control arm
should be. The agents attached a prediction to every paid action except
resets (which never carry one), so these six journals contain no UNGATED
events and verify CLEAN. The `ungated` column of `HEADS.md` is the checker's
own count on the published copy, and anyone can recompute it.

## Contents

- **`HEADS.md` / `heads.json`**: the commitment artifact, one row per run,
  with the checker's verdict on the published copy.
- **`<run>/`**, one folder per run, named `<game>-<arm>-s<seed>`:
  - `journal.jsonl.gz`: the complete journal (every prediction registered
    before its action, every machine grade, the agent's notes in full).
  - `chain.json`: the writer's stored chain state `{event_id, head}`.

The pinned registries and creation records stay with the archived run
directories (they carry the experiment machine's paths). The arm, the gate
and the cap of every run are in `heads.json`. A registry's `secrets` list
names the environment variable whose value the writer redacts from every
agent string; the value itself is never stored anywhere in a run.

## Verify a journal

```bash
gunzip -k ft09-gated-s1/journal.jsonl.gz
python3 ../../verify/assay_verify.py ft09-gated-s1/journal.jsonl \
  --expect-head b089902b55c474664afcfc6131fc4750adacc2220f9aaf566ec820c17a9ccfe9
```

Decompressing beside `chain.json` lets the checker report the stored chain
as `intact` as well as recomputing the head. The other heads are in
`heads.json`.

## What the table says

Twelve runs, twelve wins. Gated: 1192 paid actions over six runs. Ungated:
1128 over six runs. The protocol's interpretation table and the readings are
in `E1_E2_RESULTS.md`. This pack publishes the record and does not interpret
it.
