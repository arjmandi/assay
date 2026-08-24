# ARC-AGI-3 four-system comparison — RHAE, actions, cost, time

## Table 0 — RHAE, the benchmark's own score (read this first)

Games-cleared, levels-cleared and total-actions (Tables 1–3) are **coverage and
cost statistics, not scores**. The benchmark scores with RHAE:

    level  = min(115, 100 · (baseline_actions / actions_spent)²)   [0 if not cleared]
    game   = min(100, Σᵢ i·levelᵢ / Σᵢ i)          if the run reached WIN
           = 100 · Σ(1..levels_done) / Σ(1..levels)  otherwise — progress only
    set    = plain mean of per-game scores

Inefficiency is penalized **quadratically**, surplus efficiency is capped away,
and an unfinished game earns no efficiency credit at all. Formula derived from
published scorecard JSONs; `rhae.py --validate` reproduces 25/25 published game
scores exactly (worst error 0.000000) and a second system's published
partial-game score independently. Ours is computed, not claimed:
`rhae.py <game> <run-dir> …` — **and then confirmed by the benchmark itself**:
our consolidated verification-replay card
[`702ccd4f`](https://arcprize.org/scorecards/702ccd4f-df1f-4118-bc8b-d79d3f4a1a32)
returns 96.54%, identical to the offline figure and matching per game on every
row (`SCORECARDS.md` documents the replay's provenance and limits).

| System | Backbone | Set RHAE | Games at 100.00 | Where the score is lost |
|---|---|---|---|---|
| arc-skill | Opus 5, uncapped | **100.00** (published) | 25 / 25 | nowhere |
| **ASSAY** | Opus 5, hard caps | **96.54** (server-confirmed) | 23 / 25 | lf52 18.18 (unfinished) · bp35 95.27 |
| Prime Agent | Opus 5, median card | 95.24 (published) | 20 / 25 | lf52 27.27 · sk48 77.61 · tn36 79.00 · cd82 98.88 · g50t 98.25 |
| PRO-LONG | Fable 5 | 94.71 (published cards) | 19 / 25 | re86 41.67 · bp35 74.85 · g50t 78.44 · lf52 81.82 · dc22 93.63 · cd82 97.38 |

Reference point: the ARC-reported **human-expert baseline is 95.4** — ASSAY,
Prime Agent (marginally) and arc-skill clear it; PRO-LONG's Fable cohort does not.

Two consequences worth stating plainly:

1. **lf52 is worth more than every efficiency gain combined.** Excluding it,
   ASSAY's RHAE over its 24 wins is **99.80**; including it, 96.54. One
   unfinished game costs 3.27 points, while being 12% behind arc-skill on raw
   actions costs ~0 (both systems sit at the caps almost everywhere).
2. **RHAE and the actions table disagree on purpose.** RHAE only punishes going
   *over* baseline on late levels and not finishing; it gives no credit for
   finishing far under baseline. So arc-skill's 12% raw-action advantage over
   ASSAY (Table 1) is invisible in RHAE, while ASSAY's completed-game
   efficiency advantage over Prime Agent and PRO-LONG *is* visible.

---

# Tables 1–3 — actions, cost, time (coverage and cost, not scores)

**Systems and provenance (2026-08-23).** ASSAY: our journals (best run per game;
wins triple-verified). arc-skill: author's published scorecard (Opus 5,
uncapped). Prime Agent (Prime Intellect): their published median scorecard
`2af780b4` (Opus 5; 24/25 environments, 178/183 levels, 11,245 actions).
**Every scorecard link — ours and each competitor's — is indexed in
`SCORECARDS.md`, including the exact provenance of ours (verification replays
of recorded action sequences; card wall-clock is machine replay time, not
agent time).**
PRO-LONG: their published scorecards (**Fable 5** — stronger backbone;
directional only). Same game instances throughout, verified by id.

## Table 1 — actions per game (ASSAY tax split: probe = paid single-action
discovery actions; batch = paid actions inside verified batches)

| Game | ASSAY total (probe + batch) | arc-skill | Prime Agent | PRO-LONG (Fable) |
|---|---|---|---|---|
| ar25 | 264 (22 + 242) | **260** | 264 | 266 |
| bp35 | 597 (73 + 524) | **482** | 602 | 1,038 ✗74.8% |
| cd82 | **88** (12 + 76) | 92 | 363 | 165 ✗97.4% |
| cn04 | 223 (20 + 203) | 227 | **200** | 249 |
| dc22 | 1,042 (57 + 985) | **520** | 985 | 1,392 ✗93.6% |
| ft09 | 82 (16 + 66) | **75** | **75** | 82 |
| g50t | **378** (44 + 334) | 482 | 491 | 722 ✗78.4% |
| ka59 | 344 (18 + 326) | 335 | **306** | 477 |
| lf52 | 364 ✗4/10 (29 + 335) | **787 WIN** | 2,511 ✗5/10 | 1,000 ✗81.8% |
| lp85 | 104 (25 + 79) | **93** | 114 | 112 |
| ls20 | **438** (17 + 421) | 481 | 504 | 544 |
| m0r0 | 218 (20 + 198) | 224 | **212** | 272 |
| r11l | 94 (9 + 85) | **83** | 86 | 162 |
| re86 | **573** (15 + 558) | 601 | 694 | 331 ✗41.7% |
| s5i5 | 367 (43 + 324) | **281** | 298 | 457 |
| sb26 | 128 (12 + 116) | **124** | 133 | 129 |
| sc25 | 192 (15 + 177) | 166 | **149** | 228 |
| sk48 | **404** (34 + 370) | 491 | 1,024 | 650 |
| sp80 | 153 (17 + 136) | 211 | **148** | 170 |
| su15 | 150 (42 + 108) | **117** | 126 | 178 |
| tn36 | 188 (13 + 175) | **160** | 324 | 182 |
| tr87 | 162 (15 + 147) | **153** | 195 | 212 |
| tu93 | 250 (23 + 227) | 205 | **199** | 227 |
| vc33 | **183** (21 + 162) | 193 | 213 | 272 |
| wa30 | 1,171 (44 + 1,127) | **802** | 1,029 | 1,639 |
| **Total** | **8,157** (probe 656 = 8.0%) | **7,645** | **11,245** | 11,156 |

### Table 1a — set totals by system (the actions comparison, one row per system)

| System | Backbone | Games cleared | Levels | Total actions | probe (tax) | batch | Actions / level |
|---|---|---|---|---|---|---|---|
| ASSAY | Opus 5, hard caps | 24 / 25 | 177 / 183 | 8,157 | 656 (8.0%) | 7,501 | 46.1 |
| arc-skill | Opus 5, uncapped | 25 / 25 | 183 / 183 | 7,645 | no data | no data | 41.8 |
| Prime Agent | Opus 5, median card | 24 / 25 | 178 / 183 | 11,245 | no data | no data | 63.2 |
| PRO-LONG | Fable 5 | 19 / 25 | no data | 11,156 | no data | no data | no data |

Totals include actions spent on games a system did not clear (ASSAY lf52,
Prime Agent lf52, PRO-LONG six games). Only ASSAY publishes a tax split.

Cheapest-clear count across 25 games: arc-skill 11 · Prime Agent 7 · ASSAY 7 ·
PRO-LONG 0. On the **24 games all three Opus systems won**: ASSAY 7,793 ·
arc-skill 6,858 · Prime Agent 8,734 — ASSAY 11% cheaper than Prime Agent,
arc-skill 12% cheaper than ASSAY (the gap concentrated in ASSAY's five
multi-session conversions; single-session wins are a dead heat with
arc-skill). ASSAY's measured exploration tax: 656 probe actions of 8,157
(8.0%) — every other paid action ran inside a verified batch.

Games completed: arc-skill 25/25 · ASSAY 24/25 · Prime Agent 24/25 ·
PRO-LONG 19/25. **lf52 defeats every published system except arc-skill**
(ASSAY 4/10 @364 with a located diagnosis; Prime Agent 5/10 @2,511 ending in
GAME_OVER; PRO-LONG 81.8% @1,000).

## Table 2 — cost

| System | Cost data | Figures |
|---|---|---|
| ASSAY | **measured** (API-billed) for 11 games; the other 14 ran on subscription (no per-run $) | 11 games = **$283.68** ($11.61–$53.89/run, median ≈ $22); extrapolated full set ≈ **$550–650** |
| PRO-LONG | **published total** for the 25-game Fable cohort | **$1,750** (~$70/game avg); our own PL×Opus side-by-side runs measured cd82 $11.55, tn36 $30.70 |
| arc-skill | not published | — |
| Prime Agent | graphs only in their blog, no numbers | — |

## Table 3 — wall-clock time

| System | Time data | Figures |
|---|---|---|
| ASSAY | measured per-run for 20 of 25 runs (agent session durations); the 5 pre-sweep runs spanned crash/resume sessions and are not cleanly attributable | measured runs: 32–330 min/game; **≈ 37 h total over those 20** (~1.8 h/game; longest: bp35 330 min across 3 sessions, s5i5 268 min across 3) |
| PRO-LONG | not published; our PL×Opus side-by-side measured two games | cd82 38 min, tn36 82 min |
| arc-skill | not published | — |
| Prime Agent | not published | — |

## Honesty notes

1. PRO-LONG's cohort is Fable 5 — a stronger backbone; their table column is
   context, not controlled comparison. The other three are all Opus 5.
2. arc-skill and Prime Agent ran uncapped and (as far as published) single- or
   best-of-N sessions; ASSAY ran under hard caps with journaled resumes — its
   multi-session conversions carry the full discovery cost of breaking six
   wrong impossibility proofs, visible in the dc22/wa30/bp35 rows.
3. Prime Agent's Best@3 reaches 183/183; the table uses their published
   MEDIAN card (their own choice of representative run). ASSAY numbers are
   single-protocol, no best-of-N.
4. Cost/time blanks are absences of published data, not zeros.
