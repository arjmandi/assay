# OOLONG benchmark — research dossier

Compiled 2026-08-23. Status: research phase — no adapter, registry, or
PROTOCOL.md exists yet; this document is the source material for designing
them. Two framings are in scope: the **traditional query-framed** benchmark
(OOLONG as published, plus kin like BABILong) and the **goal-framed
variant we introduce** (corpus + goal + task world). External facts are
sourced from the OOLONG paper/repo/dataset cards and the Prime Intellect
blog (links at the bottom); the design position in sections 6–7 is the
owner's (long-context memo, 2.1/2.2), grounded here against the researched
numbers.

## 1. The benchmark

**OOLONG** ("Evaluating Long Context Reasoning and Aggregation
Capabilities" — Bertsch, Pratapa, Mitamura, Neubig, Gormley; CMU; arXiv
2511.02817, Nov 2025) is the anti-needle long-context benchmark: instead of
retrieving one planted fact, the model must analyze **every** chunk of a
long context at the atomic level and then **aggregate** — count, compare
distributions, split by user or by time. Nothing is skippable; the answer
is a property of the whole corpus. Code: `github.com/abertsch72/oolong`;
data: HuggingFace org `oolongbench`.

### 1.1 OOLONG-synth

Built from **10 text-classification datasets** (Spam, TREC-QC, AGNews, App
Reviews, Formality, IMDB, Negation, Yahoo Topics, MultiNLI, Metaphors;
2–10 labels each). The context is a long stream of labeled-task instances
(with user IDs and dates attached); questions come in three groups:

- **counting** — occurrence counts, most-frequent label, label comparisons;
- **user** — cross-referencing the user-ID field ("which user posted the
  most X?");
- **timeline** — distribution changes before/after a date.

**400 questions per context length** (50 per source dataset), context
ladder from **1K to 4M tokens**. By design the same context is reused for
25 questions, so plain-model evaluation amortizes via prompt caching — the
benchmark itself ships a multi-question amortization axis. HF card:
~6,500 rows (1.3K validation / 5.2K test), ~12 GB.

### 1.2 OOLONG-real

Transcripts of **Critical Role** (a Dungeons & Dragons actual-play show),
campaign 1, 115 episodes, from the CRD3 dataset — with answers validated
against the fan-maintained CritRoleStats statistics. Questions are
counting/enumeration/indexing over dice rolls, spells cast, and character
actions, over contexts of **1–24 episodes ≈ 55K–1.3M tokens**. This split
is messy, natural, multi-speaker data — the "downstream" complement to
synth's ablatable cleanliness.

### 1.3 Scoring

Exact match for labels/dates/comparisons; **exponential partial credit
`0.75^|y−ŷ|` for numeric answers**; set overlap for list answers. Scoring
scripts ship in the repo (`src/eval/eval_script_batched.py` runs the
standard in-window protocol via LiteLLM). For any ASSAY run we consume the
HF datasets directly and reuse their scoring code verbatim for parity.

### 1.4 Kin

- **BABILong** (arXiv 2406.10149, NeurIPS 2024 D&B): bAbI reasoning facts
  scattered through PG19 book filler, variants to 10M+ tokens. It is the
  needle/multi-hop complement to OOLONG's aggregation: headline finding is
  that popular LLMs effectively exploit only ~10–20% of their stated
  windows, and retrieval helps single-fact questions but not multi-hop
  chains. Long-length leaderboard entries are dominated by fine-tuned
  recurrent-memory approaches rather than stock frontier models.
- Prime Intellect's suite also lists **LongBenchPro**, **LongBenchv2**
  (comprehension) and **ManyIH** (many instructions in haystack) — same
  family, softer than OOLONG.

## 2. Published results — the model-only leaderboard

Paper leaderboard, **128K tokens: every frontier model under 50% on both
splits.** Best is GPT-5 at **46.4% (synth) / 36.5% (real)**; Claude
Sonnet 4 and Gemini 2.5 Pro also land under 50%. DeepSeek-R1 collapses
(11.9% synth) with documented pathologies — 60% of failures give no
answer, 64% end mid-sentence, 17% declare the task intractable.

Analysis findings that matter for us:

- **Aggregation is the bottleneck, not classification**: giving models the
  gold per-instance labels improves scores by only 0.8–10.9 points. The
  models can label a chunk; they cannot count over 128K tokens of chunks.
- **Timeline/temporal questions are hardest.**
- **More thinking doesn't fix it**: "high" reasoning effort *underperforms*
  at 256K — attention over tokens is the failing resource, and spending
  more of it doesn't help.
- Performance degrades with length on both splits; the real split runs to
  1.3M tokens where in-window evaluation is barely feasible at all.

No independently maintained public leaderboard beyond the paper yet (the
benchmark is from Nov 2025); numbers circulating in 2026 are self-reported
harness results — next section.

## 3. How Prime Agent benchmarked it

Prime Intellect evaluated their **Prime Agent** harness (persistent
IPython kernel as the only tool; the model manages its own prompts,
skills, memory, sub-agents) on OOLONG at **128K** — their table describes
the task as a "yahoo" slice, i.e. apparently the Yahoo Topics subset of
oolong-synth, so scores are **not** comparable to the paper's full-benchmark
numbers. They also report **"OOLONG-Pairs"**, their own long-*output*
companion task (emit many classified pairs; spec not published in detail).

Their reported scores (accuracy, higher is better):

| harness + model | OOLONG @128K | OOLONG-Pairs |
|---|---|---|
| Prime Agent + GPT-5.6 Sol | **0.940** | 0.911 |
| Prime Agent + Opus 5 | 0.920 | **0.929** |
| Prime Agent + GLM-5.2 | 0.700 | 0.874 |
| Claude Code + Opus 5 (control) | 0.900 | 0.922 |
| Codex + GPT-5.6 Sol (control) | 0.500 | 0.895 |
| Pi-mono + GLM-5.2 (control) | 0.420 | — |

Three readings, all load-bearing for our design:

1. **The harness axis dwarfs the model axis.** GPT-5.6 Sol goes 0.500 →
   0.940 by swapping Codex for Prime Agent — while the paper's best bare
   in-window number is 0.46. Programmatic reading of the corpus (kernel +
   code) is what wins; this is the mechanism our record already attributes
   a controlled causal win to.
2. **Plain agentic retrieval is already a strong control.** Claude Code +
   Opus 5 scores 0.900 with no referee at all — within 0.02 of Prime
   Agent. On this slice, arm (b) of E-C7 nearly saturates accuracy; a
   referee cannot show its value on headline accuracy at 128K synth.
3. **Where the residual lives**: the unclosed 6–10%, citation integrity
   (none of these harnesses verify evidence — nothing in their protocol
   catches a confabulated count that happens to be right-ish under
   0.75^|Δ| partial credit), the 55K–1.3M real split and the 4M synth tail
   (unreported by every harness), and multi-question cost. That is
   exactly the territory E-C7's arm (c) predictions claim.

No cheating incidents were reported on OOLONG (unlike Factorio) — a static
corpus offers little to hack except the grader.

## 4. Why isn't it saturated?

Two different questions, because there are two games:

**The in-window game (what the paper measures)** is unsaturated because it
is deliberately attention-hostile: every token matters, aggregation demands
O(corpus) exact bookkeeping that attention approximates lossily,
lost-in-the-middle compounds, and added thinking budget doesn't substitute
(the 256K finding). Context windows growing to 1M+ doesn't fix it — the
real split's 1.3M and synth's 4M tails stay out of practical reach, and
partial credit masks systematic near-miss counting errors.

**The harness game** is nearly saturated at 128K-synth (0.90–0.94) but
unclaimed above it: nobody has published harness results on the 1.3M-token
real ladder or the 4M synth tail, nobody grades evidence integrity, and
all reported numbers are self-scored single-arm runs without controls.
Also worth saying plainly: harness entries answer a different question
than the leaderboard ("what can a system do") — model-only comparisons are
out of scope for us by declaration, which is why our results will always
be labeled system entries.

## 5. Cost and duration estimates

Assumptions marked as estimates; Opus 5 $5/$25 per MTok, cache reads
≈0.1× input; Sonnet 5 ≈0.6×, Haiku 4.5 ≈0.2× of Opus prices.

**Arm (a) — bare model in-window.** Cost is corpus-bound: 128K input ≈
$0.64/question uncached; with the benchmark's designed 25-questions-per-
context caching, ≈$2.20 per context block → a full 400-question synth
sweep at 128K ≈ **$30–60**, ~0.5–2 min/question. The real split at 1.3M
requires a 1M+ window and ≈$6.5/question even before output; the 4M synth
tail is not runnable in-window on anything.

**Arms (b)/(c) — agentic / ASSAY.** Cost is action-bound and **flat in
corpus length**: the corpus sits on disk; per question the agent spends
5–30 small-context turns ≈ **$0.5–3/question** (Opus 5) uncorrelated with
whether the corpus is 128K or 4M. First question per corpus is the
expensive one (building the map/counts); with note carryover, follow-up
questions on the same corpus drop toward **$0.1–0.5** — the multi-question
amortization E-C7 measures. Wall-clock 3–20 min/question first-touch,
falling after. Crossover logic, honestly stated: at 128K single-question,
the cached bare model is cheaper; the agentic arms win on long corpora,
question batches, and everywhere the bare model simply cannot go.

**A full E-C7 pilot** (three arms × a sample set of ~60 questions spanning
synth 128K + synth long-tail + real ladder): roughly **$100–600 total**,
a few days of wall-clock, dominated by arms (b)/(c). Small enough to not
need staging; pre-registration is the expensive part.

## 6. Modeling for ASSAY

### 6.1 Query-framed (the traditional way): the corpus pack

Design position (owner's memo, 2.1): **ASSAY refuses to play the
long-context game.** The corpus registers as an observer; the agent reads
slices, searches, and counts through offline compute; notes hold the map;
the journal holds everything touched. The model's window stays small; the
system's context is unbounded. This is the one mechanism in our record
with a controlled causal result behind it — programmatic reading of a
lossless record is what that win was attributed to — applied here to its
home turf. Section 3's external data independently confirms the mechanism
(0.50 → 0.94 by harness swap) *and* sharpens the burden: a referee-free
harness already gets 0.90, so the referee must earn its place on something
other than 128K headline accuracy.

Failure-mode map, against OOLONG's documented failures:

| documented failure (paper §2) | ASSAY mechanism |
|---|---|
| lost-in-the-middle / effective-window limits | gone by construction — no long window exists |
| aggregation errors (the core: counting over 128K tokens; gold labels barely help) | counting becomes **code over the corpus** instead of attention over tokens; per-chunk judgment (classification) stays model-bound, tallying does not |
| confabulated answers/citations (R-1's 60% no-answer, 64% truncation) | die at a **re-aimed gate**: in a static corpus there is no world-response to grade predictions against, so the referee grades **evidence integrity** — a fact may only be banked, and an answer only submitted, carrying a span-verifiable citation checked **verbatim in code** (deterministic, consistent with the no-LLM-in-kernel law) |
| multi-hop over aggregates (timeline/user questions — the hardest group) | chains build only on verified facts; executable entity-state timelines replayed against extracted events check the whole chain |
| premature answering / unread regions | a **coverage census** (unread-region ledger) gates submission the way the hazard gate demands a recovery plan |

Pack contract (kernel-free — everything below is adapter/registry/module
level): the document observer serving slices; search/read/count REPL
helpers as offline compute (**thinking is free, probing is paid — reading
is thinking**); paid actions reduced to `BANK_FACT {text, span}` and
`SUBMIT {question, answer, spans[]}`, both span-gated, SUBMIT additionally
coverage-gated; a citation-verifier claim shorthand (deterministic span
check); coverage meters. Claims gain an optional source-span field.

Honest limits (owner's memo, kept intact): **synthesis stays model-bound**
— navigation locates, but holding two distant passages together in one
reasoning pass is still the backbone's job; **query invention in
distractor seas is judgment** and will show tier-sensitivity;
**global-judgment tasks** (theme, tone) get coverage support but no
referee; and these are **system entries** — model-only leaderboards are
out of scope. Expected effectiveness by class: OOLONG-type aggregation —
**high**; BABILong-type needles + multi-hop — **moderate-to-high**;
holistic judgment — **low-to-moderate**. Tier note: aggregation-by-code
and span-checking are tier-independent, so a smaller model should close
more of the frontier gap here than on ARC — Prime Agent's GLM-5.2 gap
(0.700 vs 0.920/0.940) is the datum this prediction gets tested against.

### 6.2 Experiment E-C7 — the three-arm pilot (pre-registered)

Arms, on an OOLONG (or BABILong) sample set:

- **(a) bare model** — full context in-window (the paper's protocol);
- **(b) plain agentic retrieval, no referee** — the design-honesty control
  ("would something simpler do" applied to ourselves); implementation
  should match the published control shape (Claude Code-style file tools);
- **(c) ASSAY with the corpus pack.**

Metrics: accuracy vs context length; hallucinated-citation rate;
aggregation accuracy; coverage at submission; multi-question cost
(carryover amortization) — for which the benchmark's own 25-questions-per-
context design provides the natural unit.

Pre-registered predictions, as stated in the memo: (b) beats (a) heavily
on length degradation; (c) beats (b) specifically on citation integrity,
aggregation accuracy, and multi-question cost, roughly tying on simple
needles. **If (c) does not separate from (b), the referee does not earn
its place on this benchmark family, and we say so.**

Calibration against section 3 (written before our runs, so recording it
here is part of the pre-registration): at 128K-synth, (b)≈0.90 leaves no
headroom for an accuracy separation — the arms must be separated where the
axes actually differ: the real split's 55K→1.3M ladder and synth's ≥1M
tail (where (a) is impossible and (b) has no published footing),
hallucinated-citation rate (no existing harness measures it at all), and
cost-per-question over question batches. Sample set should therefore span:
synth @128K (comparability anchor), synth @1M–4M (length axis), real @3
lengths up to 1.3M (natural-data axis), with all three question groups
represented and n per cell fixed in PROTOCOL.md before any scored run.

Grading discipline: SUBMIT is accepted and journaled but scored **sealed**
— correctness computed only at finalize, from the paper's own scoring
code. Mid-run the referee grades only evidence integrity (spans, coverage);
the answer key never enters the run. This keeps parity with the
no-feedback protocol the bare model faces.

### 6.3 Goal-framed (the new way we introduce)

Design position (owner's memo, 2.2): give the corpus **plus a goal** — "we
want to do X with this knowledge" — instead of a question about the
content. Goal-framing changes what retrieval is graded against, and it
repairs the structural weakness of 6.1: with a goal plus an environment,
the full predict-act-grade loop returns. A passage read from the book is a
hypothesis — "the manual says valve A before pump B" becomes a claim the
world grades when acted on. **Two truths get graded instead of one**:
span-truth (the book says it — mechanical check) and world-truth (it holds
here — receipts). Existence proof at small scale: the knowledge-import win
— a 34KB foreign document plus a goal, treated as untrusted claims,
verified by acting, 6/6.

What changes relative to query-framing:

- **Precision** — the live impasse supplies the query as literal strings
  (observation field names, error signatures) matched against the corpus,
  mechanically and tier-independently;
- **Recall** — retrieval becomes iterative and self-correcting (a plan
  built on a bad retrieval fails visibly; a query system never learns its
  retrieval was wrong), and coverage gets the meaningful definition "no
  open subgoal with an unconsulted matching section";
- **Utility grading** — the journal links banked fact → citing plan steps
  → receipts, so a knowledge item's measured value is the record of the
  actions it licensed (no analogue exists under query-framing);
- **Book auditing** — an outdated or wrong passage surfaces as a conflict
  receipt (book span vs refuting action), which query systems structurally
  cannot produce;
- **Internalization as the deliverable** — the export is the book
  compiled: verified operational rules carrying both source spans and
  action receipts.

Honest costs: relevance judgment moves onto the agent (what knowledge does
the goal need — hypothesis famine with a library card; partially mitigated
by an impasse-consultation nudge); credit assignment over long chains is
approximate (per-step knowledge provenance is bookkeeping only, never
plan-voiding); and **evaluation needs a task world, not an answer key** —
a thin benchmark space, and an opportunity to define it.

No public benchmark exists in this shape — that is the point. Candidate
instantiations, cheapest first:

1. **Factorio manual + FLE lab task** (reuses `bench/factorio` infra):
   corpus = wiki/recipe manual for the target chain; goal = the throughput
   task; the book's ratios and recipes are claims the factory grades.
   Directly measurable: task success with/without corpus, span-truth
   violations, world-refuted passages found (seed some deliberately wrong
   passages to measure book-auditing yield).
2. **ARC + imported strategy notes** — the existing knowledge-import
   mechanism scaled up (a large foreign playbook instead of 34KB).
3. **Tool/API documentation + sandboxed task** — man-pages corpus, goal is
   an operational outcome in a shell world.

Design deltas remain pack/module level, kernel-free: reading as offline
compute; the optional source-span field on claims; the notes Verified tier
split into world-earned vs book-sourced-and-confirmed; the impasse
consultation nudge.

## 7. Fit with the kernel (what exists vs what the packs add)

Nothing in sections 6.1–6.3 touches the kernel: the corpus is an adapter
(`factory(root, config)` serving a document observer, deterministic — a
static corpus trivially satisfies journal-replay resume, unlike Factorio);
BANK_FACT/SUBMIT are registry rows; the span verifier is a claim grader in
code; coverage is a channel/meter; the consultation nudge is a behavior
module. Registries per arm-(c) run: cap sized to the question batch (e.g.
actions ≈ 4–8× questions), `hand_cap` per ARC parity conventions, budget
in USD fed via `assay spend report`. One run directory = one corpus + its
question set; `levels_completed` = questions submitted through the gate,
`win_levels` = batch size; WIN = all submitted with the census clean —
correctness lands at finalize, sealed.

## 8. Open questions for the owner

1. **E-C7 sample set**: confirm the axes in 6.2 (synth 128K + synth ≥1M +
   real ladder; n per cell) — or narrower first pilot?
2. **Arm (b) implementation**: Claude Code with file tools (matches the
   published control shape) vs ASSAY-with-gate-disabled (cleaner ablation,
   weaker external comparability). Recommend Claude Code shape.
3. **Sealed scoring** for SUBMIT (recommended, 6.2) — confirm.
4. **Tier arm**: add a small-model pass (Haiku/Sonnet) to test the
   tier-independence prediction directly against Prime Agent's GLM gap?
5. **OOLONG-Pairs**: Prime Intellect's long-output variant is theirs and
   under-specified — ignore, or reproduce from their materials if they
   publish it?
6. **Goal-framed first world**: Factorio-manual instance (recommended —
   reuses the other bench) vs ARC playbook vs shell world; and whether
   seeded-wrong-passage book-auditing is in scope for v1.

## Sources

- OOLONG paper: https://arxiv.org/abs/2511.02817 (OpenReview lrDr6dmXOX)
- OOLONG code: https://github.com/abertsch72/oolong
- OOLONG data: https://huggingface.co/oolongbench
  (`oolong-synth`: ~6.5K rows, 12 GB; `oolong-real` sibling)
- BABILong: https://arxiv.org/abs/2406.10149
- Prime Intellect, "Prime Agent" (OOLONG/OOLONG-Pairs tables, harness
  controls): https://www.primeintellect.ai/blog/prime-agent
- Owner's long-context memo (sections 2.1/2.2, E-C7) — reproduced in
  substance in sections 6–7 above.
