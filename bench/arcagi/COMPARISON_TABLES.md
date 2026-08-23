# ARC-AGI-3 four-system comparison — actions, cost, time

**Systems and provenance (2026-08-23).** ASSAY: our journals (best run per game;
wins triple-verified). arc-skill: author's published scorecard (Opus 5,
uncapped). Prime Agent (Prime Intellect): their published median scorecard
`2af780b4` (Opus 5; 24/25 environments, 178/183 levels, 11,245 actions).
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
| **Total** | **8,156** (probe 636 = 7.8%) | **7,645** | **11,245** | 11,156 |

Cheapest-clear count across 25 games: arc-skill 11 · Prime Agent 7 · ASSAY 7 ·
PRO-LONG 0. On the **24 games all three Opus systems won**: ASSAY 7,792 ·
arc-skill 6,858 · Prime Agent 8,734 — ASSAY 11% cheaper than Prime Agent,
arc-skill 12% cheaper than ASSAY (the gap concentrated in ASSAY's five
multi-session conversions; single-session wins are a dead heat with
arc-skill). ASSAY's measured exploration tax: 636 probe actions of 8,156
(7.8%) — every other paid action ran inside a verified batch.

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
