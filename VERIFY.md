# VERIFY — check the claim yourself, in about five minutes

You don't have to trust that an ASSAY run was gated, predicted-before-acting,
and unaltered. Every run produces a journal in a documented format
(`JOURNAL_SPEC.md`, `CLAIM_GRAMMAR.md`), and `verify/assay_verify.py` is a
from-scratch, stdlib-only reader that recomputes the integrity verdict from
a run directory's artifacts alone. It imports nothing from this repo's
kernel (`src/assay/`) — an independent reader agreeing with ASSAY's own
`assay audit` on the same artifacts is the actual trust claim, not our say-so.

**Prerequisite:** a `python3` (3.9+) on your `PATH`. Nothing to `pip install`
— the verifier is one file, standard library only.

## Step 1 — a clean verdict

```
python3 verify/assay_verify.py verify/fixtures/passing/run
```

Output:

```
VERIFY | CLEAN | events 4 (paid 3) | contiguous yes | chain intact | anchors none (0)
VERIFY | recomputed head e3 = f498fcf95a2533d6f894dd8e2b1769949a7b645cb736006708437533bec023b1
```

Exit code `0`. This is a synthetic run (see "What this does not prove"
below) — three paid actions, one honest miss, one win. `CLEAN` doesn't mean
the agent was always right; it means every paid action went through
predict-before-act and the journal wasn't altered after the fact.

## Step 2 — watch it fail

This fixture is identical except one paid action (event `2`, a `NOOP`) was
journaled with no `predict`, `predict_ok`, or `grade` at all — as if some
code path reached the world without going through the gate:

```
python3 verify/assay_verify.py verify/fixtures/broken-gate/run
```

Output:

```
VERIFY | INVALID FOR SCORING | events 4 (paid 3) | contiguous yes | chain intact | anchors none (0)
VERIFY | recomputed head e3 = 56334dc45d447d04e8105b93040010d56cbc1423544f0ed0866786ae3afa88ab
VERIFY | UNGATED events [2]
```

Exit code `1`. A verifier nobody has seen reject anything is not evidence —
this is the reject.

## Five more, one per failure mode

`verify/fixtures/` has one fixture per way the tool detects tampering or a
gate bypass. Each directory's `expected.json` states its verdict and, in
`note`, exactly what's broken and why:

| fixture | what's wrong |
|---|---|
| `broken-contiguity` | an event's `id` doesn't match its line number — a deletion or reorder in `events.jsonl`. The hash chain alone wouldn't catch this (it doesn't look at `id`), which is why contiguity is a separate check. |
| `broken-chain` | a journal line was edited after `chain.json` was last written, so recomputing the chain from the current `events.jsonl` no longer matches the stored head. |
| `broken-anchor` | trickier: the edit also regenerated `chain.json` to match, so the *local* chain looks intact — but the head anchored *outside* the run directory before the edit still reflects the original content. Run with `--anchor-file verify/fixtures/broken-anchor/anchors.jsonl` to see it caught. |
| `broken-gate` | (above) a paid action with no sign of ever going through predict-before-act. |
| `broken-predict-grade` | `predict_ok` was flipped to `false`, but the event's own `grade` array still shows a passing claim — the two fields disagree, so they weren't produced by one consistent process. |

Run any of them the same way: `python3 verify/assay_verify.py verify/fixtures/<name>/run`.
`passing` is the only one that comes back `CLEAN`.

## Checking against a published anchor

Anchors are how a third party catches an edit that a run's own directory has
been made internally consistent to hide (see `broken-anchor` above, and
`JOURNAL_SPEC.md` §7). When we publish a run, we publish the bare
`event_id:head` pair, not our local anchor file:

```
python3 verify/assay_verify.py verify/fixtures/passing/run --expect-head 3:f498fcf95a2533d6f894dd8e2b1769949a7b645cb736006708437533bec023b1
```

`--anchor-file PATH` checks against a bundled `anchors.jsonl` instead, if one
ships with the run. `--json` emits the same report as machine-readable JSON.
Exit codes: `0` = CLEAN, `1` = INVALID FOR SCORING, `2` = usage/read error
(e.g. the run directory doesn't exist).

## The scorecard batch

`bench/arcagi/SCORECARDS.md` indexes every ASSAY and competitor scorecard
this repo cites — each one a link to `arcprize.org`, scored by ARC's own
server, not by us. It's the closest thing to a real, third-party-verified
record currently public; read that file's provenance note before citing any
number from it.

## What this proves, and what it does not

**Proves**, for any journal that verifies `CLEAN` against this tool:

- Every paid action in the run went through predict-before-act — no
  ungated world contact exists in the journal (§5 of `JOURNAL_SPEC.md`).
- The journaled `predict_ok` for every graded action is exactly what its
  own `grade` array implies — the two weren't edited independently (§6).
- The journal is unaltered since it was written — the hash chain, and, if
  an anchor is checked, unaltered since a point in time attested to
  *outside* the run directory (§3, §7).

**Does not prove:**

- **It is not a re-run.** The verifier recomputes hashes and checks
  bookkeeping consistency; it does not re-execute the world or re-derive a
  `cell`/`move`/`region`/`verify:` claim's truth value from the journaled
  frames. Full claim re-grading needs the grading engine, which is closed
  (`JOURNAL_SPEC.md` §9) — the raw material to do it by hand is in the
  journal (frames/observations, channel declarations, verifier source and
  its SHA-256), but this tool doesn't do it for you.
- **It does not reveal the doctrine or the kernel.** `verify/assay_verify.py`
  is a clean-room implementation against the published spec, not an export
  of `src/assay/`. Layers 3 and 4 (the grading engine, the agent-facing
  manual) ship nothing here.
- **It does not vouch for the world.** The journal records what the
  adapter reported; this tool checks that the record wasn't tampered with
  after the fact, not that the adapter itself was honest about what
  happened in the world.
- **This bundle ships no real run journal.** `verify/fixtures/` is
  synthetic, built for exactly this walkthrough — not a benchmark result.
  The v1 hash chain (§3) covers raw journal lines, so a redacted line no
  longer reproduces its anchored hash: **v1 cannot verify a scrubbed
  journal**, only an unredacted one. A journal that needs scrubbing before
  release doesn't ship in this bundle; `JOURNAL_SPEC.md` §10 reserves a v2
  chain form designed to make redaction and verification coexist, but v2 is
  not implemented. Until a real run journal is cleared for release under
  v1's all-or-nothing terms, `bench/arcagi/SCORECARDS.md` above is the
  closest thing to a public, third-party-checkable ASSAY result.
