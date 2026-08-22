# ARC-AGI-3 benchmark — results

**Updated 2026-08-22, after the rc1 batch.** All ASSAY runs: Opus 5, tier-2
starting information, fresh cold starts, local simulator, uniform protocol
(see `PROTOCOL.md`), every journal audit-clean (zero ungated events, chains
intact, no forbidden-path contact).

Competitor reference: **arc-skill**, the strongest published ARC-AGI-3
harness — its author's published scorecard (Opus 5, uncapped, verified to use
the same game instances).

| Game | Levels | ASSAY best | Actions (cap) | arc-skill (published, uncapped) | Action delta |
|---|---|---|---|---|---|
| cd82 | 6 | **WIN 6/6** | **88** (200) | 92 | **ASSAY −4** |
| tn36 | 7 | **WIN 7/7** | 188 (200) | 160 | arc-skill −28 |
| ft09 | 6 | **WIN 6/6** | 81 (200); rc1 regression 82 | 75 | arc-skill −6 |
| sp80 | 6 | **WIN 6/6** | **153** (200) | 211 (1 reset) | **ASSAY −58** |
| su15 | 9 | **WIN 9/9** | 150 (500, 4 resets) | 117 (2 resets) | arc-skill −33 |
| ls20 | 7 | **WIN 7/7** | **438** (500, 3 resets) | 481 (3 resets) | **ASSAY −43** |
| lf52 | 10 | 4/10, self-stop | 246 of 500 (254 unspent) | 787 (1 reset) | — (not comparable) |

**Totals: 7 games · 6 full wins · 45 of 51 levels (88%).**

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
