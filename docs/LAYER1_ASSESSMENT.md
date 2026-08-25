# Layer-1 separability assessment (open the verification surface?)

2026-08-25 · requested by owner before deciding option (a) "open layer 1 with
the paper" vs (b) "keep everything closed". Assessment only — no split was
started. **Internal planning material: strip this file before any
open-sourcing of the repo itself.**

Layer 1 (candidate to open): journal format spec · standalone audit/replay
verifier · claim-grammar spec. Layers 3/4 (closed): kernel, doctrine,
memory/agency machinery.

## 1. Is layer 1 separable today? — Yes, and the coupling is one file deep.

The audit read-path is already isolated:

- `src/assay/integrity.py` (255 lines) holds the whole verification story:
  chain recompute (`compute_chain`, seed `assay-chain-v1`), anchor
  verification, contiguity, the pre-registered UNGATED definition
  (`ungated_events`: paid non-RESET event with no `predict`/`predict_ok`/
  `grade`), and the CLEAN / INVALID-FOR-SCORING verdict (`audit`).
- Its only internal imports are five file helpers from `core.py`
  (`RunPaths`, `append_jsonl`, `atomic_json`, `load_jsonl`, `read_json`) plus
  `load_events`. It imports **nothing** from broker, live, model, agenda,
  modules, carryover, predictions, or verifiers.
- `assay audit` in `cli.py` is two lines (`cli.py:775-776`) calling
  `integrity.audit`/`audit_lines`.

The verdict is computable from journal fields alone (`counts_action`,
`action`, `predict`, `predict_ok`, `grade`, `id`), which are documented facts
about the format, not kernel behavior. A standalone verifier therefore needs
a **reimplementation of ~150 lines against the spec, stdlib-only** (it even
sheds `core.py`'s numpy import, which only serves grid rendering). Nothing
drags kernel code.

The claim-grammar spec likewise already exists in prose across `GUIDE.md`
and the `predictions.py` docstrings; writing the spec document requires
reading kernel code, not shipping it.

## 2. Would standalone `assay-verify` import layer 3/4? — No.

Scope v1 (integrity verdict: chain, anchors-vs-published-heads, contiguity,
ungated scan, paid-action counts, per-level attribution for RHAE): zero
layer-3/4 imports, zero imports from this repo at all if reimplemented from
the spec — which is the recommended shape (see §3).

One honest boundary: **full claim re-grading** (re-running `ch`/aggregate
predicates and agent-written verifier programs against the journaled frames)
would reimplement grading-engine logic (layer 3, `predictions.py` ~570
lines). It is not needed for the trust story v1 and should be explicitly out
of scope; a third party who wants it has the frames, the channel definitions
(`channels.json`), and the verifier programs (`verifiers/`) inside any shared
run directory and can grade by hand. Ship integrity-verdict + counts first.

## 3. Does the split risk changing benchmark behaviour? — No. Direct answer:

**No behavioural change**, under one rule: the standalone verifier is
**additive** — new code outside `src/`, written against the spec — never an
extraction that the kernel then imports. The only way this work could touch
run behaviour is the anti-pattern of refactoring `integrity.py` into a shared
package and re-pointing kernel imports at it; that is prohibited by this
plan. The spec is the contract; the two implementations stay independent
(and their independent agreement is itself a verification feature).

How it is verified, concretely:

1. `git diff --stat src/` is **empty** across the entire layer-1 work.
2. The new tool runs over all 19 sweep-1500 run directories (plus the earlier
   archived runs): its verdict must equal the stored `.assay/audit.json` and
   a fresh `assay audit` on every run — same CLEAN/INVALID, same ungated
   list, same paid counts, same chain head.
3. Recorded results cannot regress retroactively: the 25-game record is
   already journal-verified and server-confirmed on card `702ccd4f`; opening
   a spec and a reader changes nothing about history.
4. If (against this plan) any `src/` line ever changes, the standing ft09
   regression gate applies before anything ships.

## 4. Work and what a third party actually gets

Roughly **1–2 focused days**: `JOURNAL_SPEC.md` (event fields, run-dir
layout, chain rule + seed, ungated rule, spec version) ~0.5d;
`CLAIM_GRAMMAR.md` ~0.5d; `assay-verify` (stdlib-only, ~150–400 lines +
tests over synthetic journals from the counter-world example) ~0.5–1d.

With layer 1 in hand, an independent party can verify, for any journal we
share: (a) tamper-evidence — the journal matches a published chain head
(add `--expect-head`; we publish per-run heads alongside SCORECARDS.md —
note the operator-side anchor files under `~/.assay/anchors/` are not
third-party visible and contain absolute local paths; publish bare heads,
not anchor files); (b) gate compliance — zero ungated paid actions, the
exact property "every action was predicted before it was taken";
(c) contiguity — nothing deleted or reordered; (d) paid-action counts and
per-level attribution — recomputing RHAE via the already-planned
`rhae.py` + `baselines.json`; (e) that the recorded action sequence
reproduces the outcome, via the ARC server replay path that already exists
publicly (scorecards). That is the full "check it yourself" story the
strategy wants, with no kernel exposure.

## 5. Does DOCTRINE.md leak through layer-1 artifacts? — Not through layer 1.

Checked empirically: ten distinctive verbatim snippets (six spread through
`DOCTRINE.md`, four from the module `DOCTRINE` paragraphs in `modules.py`)
grepped across `events.jsonl`, `activity.jsonl`, `NOTES.md`, `dossier.json`
of **all 19 sweep-1500 runs: zero hits.** `activity.jsonl` is command
telemetry (pids, timings); journal `note`/`predict` fields are agent-authored
strings. No code file outside `modules.py` references DOCTRINE, and no
layer-1 artifact (spec, verifier, synthetic fixtures) needs to quote it.

Two adjacent cautions, distinct from layer 1 itself:
- **Publishing raw run journals is a separate, later decision.** Journals
  contain agent-authored prose (`note`, NOTES.md, verifier code) that is
  doctrine-*shaped* in style even though it never quotes doctrine; any
  journal release gets its own scrub pass. Layer 1 (spec + verifier)
  publishes no journals. Secrets are already redacted at the write boundary.
- **Publishing the spec freezes the format.** Add an explicit spec version
  (the chain seed is already versioned as `assay-chain-v1`) so the format
  can evolve without breaking third-party verifiers.

## Recommendation: **LOW RISK** — proceed with (a), under three conditions.

1. Additive only: new spec docs + a stdlib-only `assay-verify` outside
   `src/`; the kernel tree stays byte-identical (`git diff src/` empty).
2. Scope v1 = integrity verdict + counts (+ published chain heads); claim
   re-grading explicitly out of scope.
3. Acceptance test before release: verdict parity with `assay audit` on all
   historical run directories.

Under those conditions the effect on ASSAY benchmark results and future run
behaviour is zero by construction, and the effect on quality is zero because
no running code changes. The genuine (small, acceptable) cost is a public
format-compatibility commitment, mitigated by the spec version field.
