# verify: the journal standard

An open, world-agnostic standard for auditable agent runs. It is MIT
(`LICENSE` in this directory), so anyone can implement it.

An ASSAY journal records an agent run as an append-only sequence of events
under a rolling hash chain: every paid action carries a prediction registered
before the action executed, and every prediction is graded in code against
the world's own response. This directory states those properties precisely.

- **`JOURNAL_SPEC.md`**: the journal format (`assay-journal-v1`): run
  directory layout, the event schema, the chain rule, the ungated-event rule
  and the verdict semantics.
- **`PREDICTION_GRAMMAR.md`**: the prediction grammar and what a graded
  outcome in a journal asserts.
- **`../evidence/`**: the published journals, each with its committed chain
  head and the verdict recorded when it was published (`evidence/README.md`
  is the index, and `evidence/verify_all.py` checks every pack against its
  heads).

## Verify a journal

The harness is open source, so its own audit is the check:

```bash
assay --run-dir <run-directory> audit           # the chain, contiguity, the ungated rule, the anchors
assay --run-dir <run-directory> audit --json
python3 evidence/verify_all.py                  # every published journal against its head
```

The verdict is `CLEAN` or `INVALID FOR SCORING`, by the rules of
`JOURNAL_SPEC.md` section 8. A shared run directory matches a published head
when its `.assay/chain.json` head equals it: the journal you hold is then
byte-identical to the one whose head was published, nothing edited, deleted
or reordered since.

Until 1.3.0 this directory also held `assay_verify.py`, an independent
checker reimplemented from the spec, written so that a third party could
verify a journal while the harness itself was closed. The harness is open
source now, and the checker was retired after 1.3.0; the tag `v1.3.0` holds
its last version.

## What a verdict proves, and what it does not

With a run directory and the audit, a third party can verify:

1. Tamper-evidence: the journal matches its published chain head. Any edit,
   deletion or reordering breaks the chain and the verdict says so.
2. Gate compliance: zero ungated events. Every paid action in the record
   carries a prediction that was registered before the action executed, and
   a machine grade against the world's response.
3. Contiguity and counts: nothing is missing. Paid actions and their
   per-unit attribution recompute exactly, which makes published scores
   recomputable (for ARC-AGI-3, through `evidence/arcagi/rhae.py`).

Deliberate limits, stated plainly: the verdict does not re-execute the
grading (the grammar makes every predict and grade pair in a journal
readable; re-grading is out of scope for v1). It cannot prove anything about
live reasoning; no post-hoc tool can. The truth of a benchmark run's result
rests with the benchmark's own public scorecards, linked in the evidence packs.

## Versioning

The spec is versioned (`assay-journal-v1`, the chain seed string carries the
version). A planned v2 chains a canonical line form in which free-text fields
are represented by salted hashes, so agent prose can be redacted from a
shared journal without breaking chain verification.
