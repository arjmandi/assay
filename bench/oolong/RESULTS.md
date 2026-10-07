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
  `verify/assay_verify.py <run>` → **verdict CLEAN**, ungated paid events **0**.
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

## M2 — four-arm competitor comparison (raw vs naive Claude Code vs Prime Agent vs ASSAY)

Every arm uses the SAME backend (claude-opus-5), the SAME corpora (the 128K/1M/4M
packs), the SAME gold-free question sets, and the SAME grader (the vendored OOLONG
scorer, sha256 247583a3…, real python-dateutil), scored over the full question set.
Crucially, every arm gets the SAME neutral task with **no strategy hint** — the
only thing that differs is the harness. No external or published number is used;
OOLONG supplies only the corpora, questions, and scorer.

The arms:
- **raw** — the whole corpus in the model's context window, no tools, one call.
- **naive Claude Code** — `claude -p`, the full default Claude Code harness, same
  tools ASSAY has, handed "here is a corpus file and these questions" with no
  hint about how to solve it.
- **Prime Agent** — Prime Intellect's open-source RLM harness (MIT), run headless
  on Opus 5 via our API key; it holds the corpus as a variable in a persistent
  IPython REPL and fans out to recursive subagents.
- **ASSAY (de-hinted)** — the referee harness with its strategy hint STRIPPED, so
  its framing matches the competitors: span-cited BANK_FACT/SUBMIT, predict-before-
  act, hash-chained journal, sealed scoring, but no "read-as-file/grep" guidance.

An earlier "plain-agentic" arm (a minimal agent spoon-fed ASSAY's read-as-file/grep
strategy) was **retired as a strawman** — it was handed the winning approach and so
matched ASSAY trivially, proving nothing.

| rung | raw | ASSAY (de-hinted) | naive Claude Code | Prime Agent |
|---|---|---|---|---|
| synth128k | **0.447** (9/25) | 0.764 (16/25) | 0.818 (18/25) | 0.859 (18/25) |
| synth1m | **WALL** (1.2M tok) | 0.740 (17/25) | 0.740 (17/25) | 0.740 (17/25) |
| synth4m | **WALL** (~2.8M+ tok) | 1.000 (20/20) | 1.000 (20/20) | 0.850 (17/20) |

Cost + wall-clock of the completing run (usd / minutes; Prime Agent's CLI does not
itemize cost):

| rung | raw | ASSAY (de-hinted) | naive Claude Code | Prime Agent |
|---|---|---|---|---|
| synth128k | 1.11 / 2 | 12.64 / 33 | 4.11 / 16 | — / 10 |
| synth1m | — | **48.76 / 114** | 8.20 / 30 | — / 5 |
| synth4m | — | 2.40 / 8 | 0.89 / 4 | — / 2 |

(hinted ASSAY, reference only — NOT the fair comparison: 0.870 / 0.701 / 1.000,
cost 10.10 / 5.16 / 2.37.)

### What the table shows

1. **The raw model walls past 128K.** At 128K the corpus is 149,722 tokens and
   fits Opus 5's 1M window; the raw model runs and scores 0.447, reproducing the
   published bare-model floor (~0.46). At 1M (1.2M tokens) and 4M (~2.8M+) the
   corpus exceeds the window; the raw model loads nothing. This is the one robust,
   unambiguous result.

2. **All three harnesses run at every length and cluster tightly on accuracy.**
   Prime Agent, naive Claude Code, and de-hinted ASSAY all run 128K/1M/4M (none
   put the corpus in context) and land in the same band — identical (0.740) at 1M,
   identical (1.000) for two of three at 4M. All beat raw by a wide margin.

3. **On a fair, un-hinted footing, ASSAY does not lead on accuracy — and is far
   less efficient.** At 128K it is the *weakest* harness (0.764 vs naive 0.818 vs
   Prime 0.859); its counting questions collapsed to ~0 without the hint, while
   the competitors found the right approach on their own. On cost it is dramatic:
   de-hinted ASSAY 1M took **$48.76 and 114 minutes** for the same 0.740 that naive
   Claude Code reached in $8.20 / 30 min. The strategy hint had been load-bearing:
   hinted ASSAY 1M cost $5.16, de-hinted $48.76 (~10x). ASSAY's referee structure
   (one-at-a-time, base64 BANK_FACT/SUBMIT, predictions) traps an un-hinted agent
   in an inefficient loop that a plain modern harness avoids.

4. **The "a general harness's machinery trips on long context" thesis did not
   hold.** Naive Claude Code — the full default harness with no hint — handled the
   task fine and aced 4M (1.000, $0.89). Modern harnesses find the corpus-as-file
   approach themselves; they do not trip.

### Conclusion (honest)

Accuracy and efficiency are NOT where ASSAY separates from a good general harness;
on both it is at best tied and at worst behind (weakest at 128K, ~10x the cost at
1M). ASSAY's distinctive value must be the **verification layer** — span-checked
claims, coverage gating, hash-chained journal, sealed scoring — which none of the
competitors provide, not a better or cheaper answer. Two things to separate before
drawing conclusions: (a) the counting collapse and the 114-minute churn may be
**adapter friction** (the heavy BANK_FACT/SUBMIT/base64/predict mechanics), not a
flaw in the referee idea — a candidate for redesign; (b) every cell is n=1, so the
within-band ordering is directional, not precise.

Update 2026-10-07 (E5, `evidence/e5`): the 1M run was repeated on release 1.1.0
with the actuators batched (several spans per BANK_FACT, a plain-text SUBMIT,
`registry_200_batch.json`), same de-hinted framing, cost bands fixed before
launch. Same 0.740, 43.39 USD against 48.76, 76 minutes against 114. The journal
timestamps place the paid phase at seven and a half minutes (single mode: under
six of 114). The cost sits in the offline phase, where both ASSAY agents
hand-classified all 20,321 reviews through the model while the naive agent
labelled about 2,655 and trained a classifier for the rest. Caveat (a) is
therefore retired in the form above: the actuator mechanics are not the seat of
the gap. The candidate cause is the offline strategy chosen under the harness
framing, untested at n=1 per arm.

### Provenance and confounds

Backend claude-opus-5 for all arms. Grader bench/oolong/scorer.py ->
vendor/oolong_eval_helpers.py (sha256 247583a3…), offline, answers as "Answer: {x}".
ASSAY arms re-scored from sealed SUBMIT records through the same standalone path,
matching the daemon exactly. Prime Agent ran under `-p` (128K/4M needed its
completion-gated `--autonomous` mode so its async subagents could return; 1M
completed under one-shot `-p`). Cross-rung source confound unchanged (128K negation,
1M app_reviews, 4M metaphors) — read across a rung, not down a column. Per-arm
answers/scores under ~/workspace/oolong-arms/<rung>/. All arms API-billed on one key.

Friction (all arms): `assay status` truncates long question text (worked around
with `assay python`); agent-written verifiers were flagged VACUOUS in the ASSAY
runs, but the in-code span check is the real gate, so validity is unaffected.
