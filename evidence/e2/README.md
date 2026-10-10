# Evidence pack №5: E2, resumed proof states with and without the coverage-audit module

The fifth evidence pack under the `assay-journal-v1` standard: the ten
journals of experiment E2, each a continuation of an archived campaign
journal already published in `evidence/arcagi/`.

## The design, in three sentences

E2 asks whether a fresh session, resumed from the archived record at the
event where a campaign session declared a level impossible, moves off that
proof without the operator's historical hint. Five archived campaign
journals (dc22, s5i5, sk48, wa30, bp35) were cut at the proof event (e634,
e273, e111, e990, e368) and resumed by claude-opus-5 under a neutral prompt
in two arms: a module arm whose pinned registry loads the coverage-audit
module in advise mode, and a control arm with the unmodified campaign
registry. Each arm played to WIN or to the cap of 1500.

The pre-registered protocol is `E1_E2_PROTOCOL.md`, section 5 for the design
and amendments 11 and 12 for the cut correction, the module fix and the
control arm. The results write-up is `E1_E2_RESULTS.md` and the per-run
metrics with chain heads are in `E1_E2_E3_run_table.json`. All are archived
with the paper materials.

## These journals are resumed reconstructions

Each E2 journal is not a fresh run. Its pre-cut prefix, events e0 to the cut
event inclusive, is byte-identical to the corresponding archived campaign
journal published as `evidence/arcagi/journal-<game>.jsonl.gz`, so the
archived journal's published head commits the prefix (the `Prefix source
head` column of `HEADS.md`). The two arms of one state share that prefix and
diverge at the first resumed action. The reconstruction of each state is
documented in the provenance files archived with the paper materials, one
set per run under `<run>.provenance/`: `RESUME_PROVENANCE.md` (every removed
or restored file with its hash, the recomputed chain, the registry change
for the module arm), `NOTES.at-cut.md` (the notes file installed at the cut)
and `notes_at.report.json` (how it was reconstructed from the session
transcripts). The sk48 module state also keeps
`NOTES.at-cut.e110-superseded.md` from the cut amended in protocol section
11.1.

Three runs carry a session boundary the agent did not choose: sk48 module,
dc22 control and bp35 control were paused by the usage limit at 21:22Z on
2026-10-06 and resumed from a replayed journal at 21:52Z, as recorded in
`E1_E2_RESULTS.md` section 4.

## Contents

- **`HEADS.md` / `heads.json`**: the commitment artifact, one row per run,
  with the cut event, the paid actions after the cut, the checker's verdict
  on the published copy and the archived prefix's head.
- **`<run>/`**, one folder per run, named `<game>-resume` (module arm) or
  `<game>-resume-ctrl` (control arm):
  - `journal.jsonl.gz`: the complete journal, prefix and continuation.
  - `chain.json`: the writer's stored chain state `{event_id, head}`.

The pinned registries and creation records stay with the archived run
directories (the module arm's registry names the coverage-audit module by a
path on the experiment machine). The arm and the cut of every run are in
`heads.json`.

## Verify a journal

```bash
gunzip -k s5i5-resume/journal.jsonl.gz
python3 ../../verify/assay_verify.py s5i5-resume/journal.jsonl \
  --expect-head 3f7b3d133a6b502d0df72b41ecbe14bfc82a91163628674274bb550c15786f7b
```

To confirm the shared prefix for a state, decompress the archived journal
from `evidence/arcagi/` and compare its first 274 lines (s5i5, cut e273)
with the first 274 lines of either arm. The other heads are in `heads.json`.

## What the table says

Ten runs, ten wins. Control: 1288 paid actions after the cut over five
states. Module: 953. The protocol's interpretation table and the readings
are in `E1_E2_RESULTS.md`. This pack publishes the record and does not
interpret it.
