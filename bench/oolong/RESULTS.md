# OOLONG results

## M1 — corpus-pack adapter + registry (plumbing, hand-driven)

Status: **built and smoke-tested** (2026-08-26). No LLM player, no API keys — the
smoke test drives hand-written actions to exercise the plumbing (adapter,
actuators, gates, sealed scoring, journal replay). Scored runs with a model are
M2+.

Packs used: `spam4k` (5 questions, 4096-token corpus) and `spam8k` (25 questions,
8192-token corpus), both from `oolongbench/oolong-synth` validation at revision
`f0d59eaf0febf130664cfceb710436c8e3216b2b`.

### Acceptance (spam4k unless noted)

- **A — kernel untouched.** `git diff --stat main -- src/` is empty.
- **B — start + status.** `assay status` shows `level 1/5` (win_levels = batch
  size), the current question (id 112010051, timeline, NUMERIC), the corpus path
  `.assay/corpus.txt` (corpus body absent from the observation by design), and
  `BUDGET | paid actions 0/40`.
- **C — BANK_FACT grounding gate.** A real span
  (`Date: Jun 19, 2023 || User: 16544`) → **accepted** (verified at offset 355);
  a fabricated span (`Date: Jan 01, 1999 || User: 00000`) → **refused** ("span is
  not a verbatim substring of the corpus"), the attempt journaled. `refusals`
  increments; `banked_count` does not.
- **D — SUBMIT span+census-gated to WIN.** Five questions banked+submitted,
  `levels_completed` 1→5, ending `GAME_COMPLETE` / state `WIN` at e11 (11 paid
  actions, per-question paid `[3, 2, 2, 2, 2]`). `assay audit` → **CLEAN** (chain
  intact, anchors intact, contiguous). External
  `assay-verify.py <run>` → **verdict CLEAN**, ungated paid events **0**.
- **E — sealed scoring at finalize.** `.assay/oolong_score.{json,md}` written by
  `finalize()` using the reused vendored OOLONG scorer (sha256 `247583a3…`),
  answers fed as `Answer: {x}`, network none. Mean **0.9125** over 5, **4 exact
  hits** + one **numeric-partial** (submitted 15 vs gold 17 → **0.5625** =
  `0.75^2`). Gold literal forms appear **0 times** in `events.jsonl` /
  `mutations.jsonl`; submitted answers are base64 — the answer key never entered
  the run before finalize.
- **F — determinism / replay.** Killing the broker mid-run and resuming
  (`assay start`) replayed 5 paid actions with **no divergence**
  (`RECOVERED | spam4k | local simulator | replayed 5 paid actions`); the run
  then continued to WIN.
- **Length/count independence.** `spam8k` starts under the same adapter with
  `level 1/25`, `question_count 25`, corpus 19 709 chars, `BUDGET 0/200` — the
  pack format and adapter do not care about corpus length (M2's length ladder
  reuses this unchanged).

### Biggest risk for the M2 length-ladder sweep

**Long-corpus extraction, not adapter logic** (M0 "single biggest risk"). The
upper rungs (synth 1M/4M, real 1.3M) live inside multi-GB HF shards (1.44 GB for
the 4M tail; 9.3 GB per real `dnd` split), so building those packs is a one-time
heavy download + single-row extract/dedup — exactly the tail where the
length-invariance claim needs data. Everything downstream (serve a slice, verify
a span, seal, score) is already proven flat and deterministic here. Mitigation:
stage extraction as a separate one-shot `build_pack.py` step, pin the revision,
keep only the deduped packs (<~50 MB).

Secondary: (a) full DATE scoring parity needs python-dateutil in the daemon
runtime (M1 shimmed it; the M1 packs have no DATE questions); (b) the v1 census
is intentionally minimal — the unread-region ledger (RESEARCH.md §6.1) is the M2+
refinement; (c) `0.75^|Δ|` partial credit masks near-miss counting errors, which
is why correctness is sealed and the citation/coverage gate carries the
mid-run signal.
