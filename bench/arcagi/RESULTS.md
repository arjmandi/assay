# ARC-AGI-3 benchmark — results

**Updated 2026-08-22, after the rc1 batch.** All ASSAY runs: Opus 5, tier-2
starting information, fresh cold starts, local simulator, uniform protocol
(see `PROTOCOL.md`), every journal audit-clean (zero ungated events, chains
intact, no forbidden-path contact).

Competitor reference: **arc-skill**, the strongest published ARC-AGI-3
harness — its author's published scorecard (Opus 5, uncapped, verified to use
the same game instances).

**Win provenance (2026-08-22): every win below is triple-verified** — (1) the
game engine's own state in the journaled run, (2) an ASSAY-free replay of the
recorded actions through the official engine, and (3) a LIVE ARC-server replay
(competition session): the server itself returned WIN with identical level
counts for all 7 wins. Wins are never read from the harness's claims — claims
are graded against the engine state. Server replays are part of every future
win's audit; official scorecard publication happens in one batch at paper time.

## The three-system table — actions per game, all systems' published/recorded runs

ASSAY: our runs, Opus 5, capped, tier-2. arc-skill: the author's published
scorecard, Opus 5, uncapped. PRO-LONG: their published official scorecards —
**Fable 5 backbone** (directional context, not a controlled comparison; scores
below 100% mean they did not fully clear the game). Bold = the cheapest full
clear of that game across the three systems.

| Game | Levels | ASSAY (Opus, capped) | arc-skill (Opus) | PRO-LONG (Fable 5) |
|---|---|---|---|---|
| cd82 | 6 | **WIN — 88** (cap 200) | 92 | 165 (97.4%, not cleared) |
| tn36 | 7 | WIN — 188 (cap 200) | **160** | 182 |
| ft09 | 6 | WIN — 81 (cap 200; rc1 gate 82) | **75** | 82 |
| sp80 | 6 | **WIN — 153** (cap 200) | 211 | 170 |
| su15 | 9 | WIN — 150 (cap 500) | **117** | 178 |
| ls20 | 7 | **WIN — 438** (cap 500) | 481 | 544 |
| lf52 | 10 | 4/10 — 364 (cap 1500, n=3 stall) | **787** | 1,000 (81.8%, not cleared) |
| cn04 | 6 | **WIN — 223** (cap 1500) | 227 | 249 |
| dc22 | 6 | WIN — 1,042 (two sessions: 634 + 408 resume) | **520** | 1,392 (93.6%, not cleared) |
| m0r0 | 6 | **WIN — 218** (cap 1500) | 224 | 272 |
| r11l | 6 | WIN — 94 (cap 1500) | **83** | 162 |
| tr87 | 6 | WIN — 162 (cap 1500) | **153** | 212 |
| sc25 | 6 | WIN — 192 (cap 1500) | **166** | 228 |
| g50t | 7 | **WIN — 378** (cap 1500) | 482 | 722 (78.4%, not cleared) |
| vc33 | 7 | **WIN — 183** (cap 1500) | 193 | 272 |
| ka59 | 7 | WIN — 344 (cap 1500) | **335** | 477 |
| lp85 | 8 | WIN — 104 (cap 1500) | **93** | 112 |
| sb26 | 8 | WIN — 128 (cap 1500) | **124** | 129 |
| ar25 | 8 | WIN — 264 (cap 1500) | **260** | 266 |
| re86 | 8 | **WIN — 573** (cap 1500) | 601 | 331 (41.7%, not cleared) |
| tu93 | 9 | WIN — 250 (cap 1500) | **205** | 227 |
| s5i5 | 8 | WIN — 367 (three sessions: 190+83+94) | **281** | 457 |
| sk48 | 8 | **WIN — 404** (two sessions: 111+293) | 491 | 650 |
| wa30 | 9 | WIN — 1,171 (three sessions) | **802** | 1,639 |
| bp35 | 9 | WIN — 597 (three sessions) | **482** | 1,038 (74.8%, not cleared) |

Reading across the 13 shared games: cheapest full clear — arc-skill 8, ASSAY
5, PRO-LONG 0 (on a stronger backbone). On the 10 games all three fully
cleared, summed actions: **ASSAY 1,899 · arc-skill 1,897 · PRO-LONG 2,279** —
the two Opus systems are separated by 2 actions in 1,900 (a dead heat; ASSAY
ran under hard caps and with less starting information), with PRO-LONG ~20%
above both on the stronger backbone. Per game on those 10, ASSAY was cheaper
than PRO-LONG on 9 of 10 (tn36 the exception).

## Sweep at cap 1500 (in progress; API-billed, measured $ per run)

| Game | Levels | ASSAY | Actions (cap 1500) | arc-skill (published) | Delta | Run cost |
|---|---|---|---|---|---|---|
| cn04 | 6 | **WIN 6/6** | **223** (0 resets) | 227 | **ASSAY −4** | $22.38, 66 min |
| dc22 | 6 | 5/6, self-stop | 634 (866 unspent, 0 resets) | 520 | — | $46.22, 106 min |
| m0r0 | 6 | **WIN 6/6** ✓server | **218** (0 resets) | 224 | **ASSAY −6** | $16.57, 50 min |
| lf52 | 10 | 4/10 (3rd run), self-stop | 364 (1,136 unspent; 193 on L5) | 787 | — | $53.89, 152 min |
| r11l | 6 | **WIN 6/6** ✓server | 94 (0 resets) | 83 | arc-skill −11 | $16.35, 52 min |
| tr87 | 6 | **WIN 6/6** ✓server | 162 (0 resets; wm miss 0.6%) | 153 | arc-skill −9 | $11.61, 40 min |
| sc25 | 6 | **WIN 6/6** ✓server | 192 (3 resets) | 166 | arc-skill −26 | $23.40, 77 min |
| g50t | 7 | **WIN 7/7** ✓server | **378** (4 resets; wm miss 1.3% over 1,208 claims) | 482 | **ASSAY −104** | $27.26, 75 min |
| vc33 | 7 | **WIN 7/7** ✓server | **183** (0 resets) | 193 | **ASSAY −10** | $15.24, 48 min |
| ka59 | 7 | **WIN 7/7** ✓server | 344 (0 resets) | 335 | arc-skill −9 | $31.12, 95 min |
| lp85 | 8 | **WIN 8/8** ✓server | 104 (0 resets) | 93 | arc-skill −11 | $19.64, 60 min |
| sb26 | 8 | **WIN 8/8** ✓server | 128 (0 resets; wm miss 4.0%) | 124 | arc-skill −4 | subscription, 32 min |
| ar25 | 8 | **WIN 8/8** ✓server | 264 (0 resets; wm miss 1.6%, gambles 9/9) | 260 | arc-skill −4 | subscription, 65 min |
| re86 | 8 | **WIN 8/8** ✓server | **573** (0 resets; wm miss 3.0%; 741 verifiers) | 601 | **ASSAY −28** | subscription, 90 min |

re86 note: the hardest game in the set by competitor record (PRO-LONG 41.7%,
never cleared) — ASSAY cleared it under the competitor's count. First measured
module-efficacy datum: the player credits the wall_spend advisory with moving
its discovery into offline simulators (levels 7–8 solved on paper, executed at
100% step verification).

| tu93 | 9 | **WIN 9/9** ✓server | 250 (6 resets, 5 GAME_OVERs incl. 2 deliberate lethality experiments; gambles 10/10; 315 verifiers, zero vacuous) | 205 | arc-skill −45 | subscription, 100 min |

**Sweep closed 2026-08-23: 11 games · 9 wins · 2 diagnosed self-stops ·
$283.68 of the $320 cap · every win triple-verified (journal, ASSAY-free
engine replay, live ARC-server replay) · every journal audit-clean (zero
ungated events across ~3,400 paid actions).** Measured cost curve: wins
$11.61–$31.12 (median ≈ $22); the two hard partials $46–54. Exploration tax
across the 11 sweep runs: probe share 5–24% (batch share 76–95%), discovery
ramp A1 = 7–28 actions.

cn04 meters: world-model miss 6.2%, gambles 6/6, 0 resets, audit CLEAN.
dc22 meters: world-model miss 23.9% (a hard, gated game), gambles 5/5, audit
CLEAN; stopped by diagnosis (level-6 arming switch unfound; three
live-but-blocked buttons mapped), resumable — the epistemic-wall stop again,
not a budget stop.

m0r0 meters: world-model miss 5.1%, gambles 7/7, 0 resets, audit CLEAN;
win verified by engine replay AND live server replay (WIN 6/6 from the API).

**lf52, closed as a budget question (n=3):** three independent runs now stall
at exactly 4/10 under caps of 200, 500, and 1500 — the last spending 193
actions on level 5 alone and stopping with 1,136 unspent after a
relaxed-constraints planner proved no 1-peg finish exists in the mappable
world. The gap is categorical, not economic: revealing world structure that
sits outside the visible frame when the camera's only lever cannot reach it.
This is the program's primary open research problem. (The run also
self-reported ~70 actions lost to automated loops re-issuing a failing
prediction — flagged as a candidate telemetry module: halt loops on first ✗.)

## THE PUBLIC SET IS COMPLETE (2026-08-23)

**All 25 public games attempted · 24 fully won · 177 of 183 levels (96.7%).**
The one non-win is lf52 (4/10 across three runs — the located exploration
gap, parked by owner decision for the machinery phase). Every win
triple-verified (journal engine state, ASSAY-free engine replay, live
ARC-server replay); every journal audit-clean — zero ungated events across
~7,800 paid actions; the entire set played under hard caps with one uniform,
domain-neutral protocol.

Aggregate actions vs the competitors:
- On the 24 games both Opus systems won: ASSAY 7,792 · arc-skill 6,858
  (arc-skill −12%; per-game split 15–9 in arc-skill's favor). The honest
  decomposition: on games ASSAY won in a single session the two systems are a
  statistical dead heat (earlier measured: 1,899 vs 1,897 over ten games);
  the aggregate gap comes from the five multi-session conversions
  (dc22, s5i5, sk48, wa30, bp35), whose totals carry the full discovery cost
  of breaking six wrong impossibility proofs across handoffs.
- PRO-LONG (published, Fable 5) fully cleared 19 of 25; ASSAY cleared 5 of
  the 6 games PRO-LONG could not (bp35, cd82, dc22, g50t, re86 — lf52 the
  exception for both).

**The campaign's central process finding — six impossibility proofs broken by
one protocol:** dc22 ("isolated blocks" — connected), s5i5 (off-board
rotation clips, not refuses), sk48 (freed blocks re-hook positionally; the
"no-op" ACTION6 was SELECT), wa30 (all three hostile rules false), bp35 L8
(switch above the drifted map), bp35 L9 (nine switches hidden above a map
CERTIFIED pixel-complete, visible only from inside the "dead-end" shaft).
Each proof fit every recorded transition and was wrong precisely where no
transition had gone. The exercised-claim coverage protocol — enumerate the
rules a proof depends on, audit the journal for what actually exercised each,
buy graded probes for the gaps — converted five of the six into wins within
their existing budgets. The sixth lesson (bp35 L9) is the deepest: the
weakest link is often the unstated frame ("rendered" standing in for
"exists"), which is exactly lf52's problem class. Machinery-phase candidates,
in order of evidence: (1) the exercised-claim coverage meter, (2) the
halt-loops-on-first-✗ rule, (3) the frontier/occupancy probe regime for
absence claims.

**wa30 converted (2026-08-23) — the fourth broken impossibility, the most
complete collapse:** every hostile rule in the throughput proof was false and
unexercised. ACTION5 kills any adjacent hostile (no "idle" requirement — the
prior session had SEVEN free kills in front of it and moved away each time);
hostiles move 1 cell/action orthogonally (the displacement histogram over 812
observations shows zero diagonal moves — "2/tick diagonal dodger" was a
two-event misread); the helper "ceiling" was a treadmill equilibrium that
vanished once the hostiles were dead. A free determinism probe (two attempts
sharing an 89-action prefix produced pixel-identical frames) let the resume
replay a recorded prefix straight into a kill.

**sk48 converted (2026-08-23) — the exercised-claim protocol's third broken
"proof":** an exhaustive 8.2M-state search had shown level 3 unsolvable — under
one UNGRADED companion rule (freed blocks re-hook positionally, not
bottom-only) that fit all 103 recorded transitions while being wrong in
untested territory. The same session found ACTION6 (written off as a no-op)
was SELECT, unlocking the final three levels. Beat the competitor's count
(404 < 491) despite the two-session handoff.

**s5i5 converted across three sessions (2026-08-23) — the exercised-claim
finding, proven:** session 2 "proved" level 7 unwinnable; session 3 audited
WHICH rules the proof depended on, found the load-bearing one had been
exercised only in a different failure mode (occupied vs off-board rotation),
bought one graded probe — and the off-board case CLIPS instead of refusing,
un-trapping the arm and also breaking level 8's length cap. Doctrine now
standing: a replay-validated model can be confidently wrong where no
transition ever went; "provably unsolvable" is a signal to probe the proof's
unexercised rules. Directly applicable to lf52's impossibility proofs.

**dc22 converted by resume (2026-08-23):** the paused 5/6 run finished level 6
in 408 further actions (1,042 total, server-verified). The handoff evidence is
the stale-plan doctrine's n=6 and its sharpest case: every inherited GRADED
mechanic was immediately actionable; the inherited prose map was wrong in four
specific ways (mislabeled the arming switch, called reachable blocks isolated,
called walkable cells unwalkable, asserted an escape that never existed). The
level-6 gate was geometric arming — found by mining the prior journal offline
(74 prior inert probes were all taken in one world-state; one coordinate had
been inert-then-live on level 5, reframing "refuses to act" as conditional).

## Second competitor reference: PRO-LONG (published)

PRO-LONG (paper arxiv.org/abs/2607.20064) published official arcprize.org
scorecards for all 25 games — **backbone caveat: that cohort ran on Fable 5,
not Opus**, so it is directional context, not a controlled comparison.
Their published actions (score) on our played games: ft09 82 · cd82 165
(97.4%) · tn36 182 · sp80 170 · su15 178 · ls20 544 · cn04 249 · m0r0 272 ·
r11l 162 · tr87 212 · dc22 1,392 (93.6%) · lf52 1,000 (81.8%). ASSAY's Opus
runs used fewer actions on 8 of the 12 shared games. Notable: PRO-LONG also
failed to fully clear lf52 (81.8% at 1,000 actions) — the game is hard for
every published system except arc-skill's 787.

## Exploration tax per run (measured from the same journals)

The tax is what discovery cost before exploitation began — reportable as a
generality indicator (these runs start with schemas but no semantics). A1 =
paid actions until the first level cleared; probe share = paid actions issued
as single probes (vs batches); waste = batch steps that missed (halt-on-miss
discards the rest unpaid); V% = share of sharp claims carried by
self-authored verifiers.

| Game | A1 (ramp) | Probe share | Batch share | Waste | V% self-authored |
|---|---|---|---|---|---|
| cd82 | 22 | 14% | 86% | 3 | 91% |
| tn36 | 12 | 7% | 93% | 9 | 94% |
| ft09 (rc1) | 4 | 20% | 80% | 0 | 39% |
| sp80 | 9 | 11% | 89% | 5 | 96% |
| su15 (rc1) | 19 | 28% | 72% | 5 | 72% |
| ls20 (rc1) | 15 | 4% | 96% | 9 | 0%* |
| lf52 (rc1) | 10 | 9% | 91% | 10 | 83% |

*Instrument note: V% counts only verifier-carried claims and predates the
channel tier. ls20's 0% is not "given vocabulary" — its 1,937 sharp claims
ran on 14 self-declared channels (it dropped verifiers after one use because
in-kernel channel grading was cheaper). Channels are self-authored grammar
too; the tax instrument needs a channel-share column (queued, telemetry-only
change).

Reading: every run converted to exploitation fast (batch share 72–96%, waste
≤10 paid steps per run); the discovery ramp varies with the game's opening
opacity (ft09's 4-action ramp vs cd82's 22). Reference point from our one
traced control run of arc-skill (cd82, tier-3 information): A1 58 and probe
share 42% — tier-2 ASSAY paid A1 22 and 14% on the same instance, i.e.
converted discovery into exploitation faster despite starting with less.

On the six games both systems won, the per-game action ledger splits **3–3**
(ASSAY cheaper: cd82, sp80, ls20; arc-skill cheaper: ft09, tn36, su15), and
the summed cost of those six wins is ASSAY 1,099 vs arc-skill 1,136 — a ~3%
aggregate edge for ASSAY, under caps, from less starting information
(domain-neutral doctrine vs arc-skill's ARC-specific instructions). Standard
caveat: one run per cell, direction only.

**Cap history on the reruns (what the 500 cap did and didn't test):**

- ls20 — cap was the binding factor at 200 (4/7); at 500: WIN in 438, under
  arc-skill's own 481. Cap-artifact diagnosis confirmed.
- su15 — never cap-bound (arc-skill won under 200); at 500: WIN in 150. The
  old 8/9 was persistence variance; arc-skill keeps a 28% cost edge here.
- lf52 — **the 500 cap was set below arc-skill's own 787**, so it could not
  test the cap story; the run refuted it anyway by stopping at 246 with 254
  unspent on a proof that the reachable geometry is unsolvable. Two runs now
  stall at exactly 4/10: a located exploration gap (hidden off-frame
  geometry; the camera's only lever is a loaded shuttle that cannot reach
  it). Next attempt belongs above 787 — `registry_1500.json` — fresh or
  resumed; both prior runs wrote the successor's probe plan into notes.

**Open items:** lf52 at the 1500 cap; 18 of the 25 public games unattempted;
the carryover experiment (lf52 paid for level 4 twice — 61 then 112 actions —
the measured case for export/import warm starts).
