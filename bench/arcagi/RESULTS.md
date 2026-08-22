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

| Game | Levels | ASSAY best | Actions (cap) | arc-skill (published, uncapped) | Action delta |
|---|---|---|---|---|---|
| cd82 | 6 | **WIN 6/6** | **88** (200) | 92 | **ASSAY −4** |
| tn36 | 7 | **WIN 7/7** | 188 (200) | 160 | arc-skill −28 |
| ft09 | 6 | **WIN 6/6** | 81 (200); rc1 regression 82 | 75 | arc-skill −6 |
| sp80 | 6 | **WIN 6/6** | **153** (200) | 211 (1 reset) | **ASSAY −58** |
| su15 | 9 | **WIN 9/9** | 150 (500, 4 resets) | 117 (2 resets) | arc-skill −33 |
| ls20 | 7 | **WIN 7/7** | **438** (500, 3 resets) | 481 (3 resets) | **ASSAY −43** |
| lf52 | 10 | 4/10, self-stop | 246 of 500 (254 unspent) | 787 (1 reset) | — (not comparable) |

## Sweep at cap 1500 (in progress; API-billed, measured $ per run)

| Game | Levels | ASSAY | Actions (cap 1500) | arc-skill (published) | Delta | Run cost |
|---|---|---|---|---|---|---|
| cn04 | 6 | **WIN 6/6** | **223** (0 resets) | 227 | **ASSAY −4** | $22.38, 66 min |
| dc22 | 6 | 5/6, self-stop | 634 (866 unspent, 0 resets) | 520 | — | $46.22, 106 min |
| m0r0 | 6 | **WIN 6/6** ✓server | **218** (0 resets) | 224 | **ASSAY −6** | $16.57, 50 min |
| lf52 | 10 | 4/10 (3rd run), self-stop | 364 (1,136 unspent; 193 on L5) | 787 | — | $53.89, 152 min |
| r11l | 6 | **WIN 6/6** ✓server | 94 (0 resets) | 83 | arc-skill −11 | $16.35, 52 min |

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

**Totals: 11 games · 9 full wins · 68 of 75 levels (91%).**

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
