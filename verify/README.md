# verify: the journal standard and the independent checker

An open, world-agnostic standard for auditable agent runs, and a standalone
checker that needs no harness and no trust in the operator. Both are MIT
(`LICENSE` in this directory), so anyone can verify a journal without a
license to the harness. They moved here from the retired `assay-verify`
repository in 1.2.0; that repository is archived with a pointer to this
directory.

An ASSAY journal records an agent run as an append-only sequence of events
under a rolling hash chain: every paid action carries a prediction registered
before the action executed, and every prediction is graded in code against
the world's own response. This directory lets anyone check those properties
on any journal they are given.

- **`JOURNAL_SPEC.md`**: the journal format (`assay-journal-v1`): run
  directory layout, the event schema, the chain rule, the ungated-event rule
  and the verdict semantics.
- **`CLAIM_GRAMMAR.md`**: the prediction-claim grammar and what a graded
  claim in a journal asserts.
- **`assay_verify.py`**: the checker. One file, Python 3.10 or newer, the
  standard library only, reimplemented from the spec. It shares no code with
  the harness in `src/assay`, and `tests/test_verify_independence.py` keeps
  that true: the checker imports nothing outside the standard library and
  runs with the harness unimportable.
- **`../evidence/`**: the published journals, each with its committed chain
  head and the checker's verdict (`evidence/README.md` is the index, and
  `evidence/verify_all.py` checks every pack against its heads).

## Verify a journal

```bash
python3 verify/assay_verify.py <run-directory>
python3 verify/assay_verify.py <run-directory> --expect-head <sha256>   # against a published head
python3 verify/assay_verify.py <run-directory> --json
```

The verdict is `CLEAN` or `INVALID FOR SCORING`, computed from the artifacts
alone. `--expect-head` checks the journal against a head published in
`evidence/<pack>/heads.json`: if the recomputed chain head matches, the
journal you hold is byte-identical to the one whose head was published,
nothing edited, deleted or reordered since.

To watch the checker reject tampering, decompress
`evidence/arcagi/journal-dc22.jsonl.gz`, flip one byte in it, and run it
again with the published `--expect-head`: the recomputed head no longer
matches and the verdict becomes `INVALID FOR SCORING`.

The checker's own tests use a synthetic non-game world, so no benchmark data
is involved anywhere in the tool or its tests:

```bash
pytest tests/test_verify_checker.py tests/test_verify_independence.py
```

## What this proves, and what it deliberately does not

With a run directory and this checker, a third party can verify:

1. Tamper-evidence: the journal matches its published chain head. Any edit,
   deletion or reordering breaks the chain and the verdict says so.
2. Gate compliance: zero ungated events. Every paid action in the record
   carries a prediction that was registered before the action executed, and
   a machine grade against the world's response.
3. Contiguity and counts: nothing is missing. Paid actions and their
   per-unit attribution recompute exactly, which makes published scores
   recomputable (for ARC-AGI-3, through `evidence/arcagi/rhae.py`).

Deliberate limits, stated plainly: the checker does not re-execute claim
grading (the grammar makes every predict and grade pair in a journal
readable, re-grading is out of scope for v1). It cannot prove anything about
live reasoning, no post-hoc tool can. Outcome truth for benchmark runs rests
with the benchmark's own public scorecards, linked in the evidence packs.

## Versioning

The spec is versioned (`assay-journal-v1`, the chain seed string carries the
version). A planned v2 chains a canonical line form in which free-text fields
are represented by salted hashes, so agent prose can be redacted from a
shared journal without breaking chain verification.
