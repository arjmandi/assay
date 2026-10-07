# Factorio (FLE) benchmark — results

**M2 calibration, 2026-08-26.** Three lab-play throughput tasks, Opus 5,
Option A (FLE-parity RUN+WAIT actuators), cap 64, hard action budget,
API-billed on the owner's key. Pre-M2 hardening in force (screen +
throughput corroboration + namespace monitor). Not scored numbers for
publication — calibration to price and de-risk the 24-task sweep (M3).

| Task | Result | Paid actions | Cost | Wall-clock | Audit | Refusals |
|---|---|---|---|---|---|---|
| ironplate (iron_plate) | **WIN 4/4** | 5 | $1.99 | ~9 min | CLEAN | 0 |
| irongear (iron_gear_wheel) | **WIN 4/4** | 9 | $4.87 | ~20 min | CLEAN | 0 |
| circuit (electronic_circuit) | **WIN 4/4** | 9 | $4.44 | ~17 min | CLEAN | 0 |

Every win triple-verified (kernel `assay audit` CLEAN, standalone
the independent checker CLEAN on the fresh journal, throughput corroboration enforced
with a real producing entity present). Zero ungated events. **Zero policy
refusals across all three — the safety screen imposed no capability tax on
any legitimate program** (the owner's capability-cost concern, answered by
data).

Also confirmed on a second domain: the referee caught and blocked the exact
exploit class a published agent fell to on FLE. The RCON/host/stat-forge
paths are refused before execution (NAMESPACE_AUDIT.md), and a forged
production statistic cannot be scored as a win (corroboration).

## Calibration findings for M3

1. **`connect_entities` — FIXED 2026-08-26.** It was non-functional (fixed
   180-tick pathfinding allowance too small + a race); the first circuit run
   hand-placed 63 belts/23 poles/15 pipes, costing $19.21/38 actions. The
   adapter now pumps ticks adaptively until the pathfinder answers and records
   the delta for deterministic replay. **Circuit re-run: WIN at $4.44/9
   actions** (4.3x cheaper, under the $15 line), connect_entities routing the
   lines. One residual: a MediumElectricPole route can stop short and needs a
   couple of hand-placed bridge poles — minor, not blocking.
2. **move_to pathfinding-tick allowance** killed two board states until the
   player discovered that priming with a resource query restores it. Needs an
   adapter fix or explicit documentation so the agent is not taxed to
   rediscover it each run.
3. **Cost/kill-line enforcement.** circuit ran to $19.21 because a headless
   run cannot self-kill at the $15 line; the action cap (64) is the only real
   bound. M3 needs a per-run cost ceiling enforced by the runner.
4. **Channel quirk (minor):** `entity_counts.pipe` vanishes when pipes merge
   into a pipe-group, costing one invalid claim on circuit. Document as a
   channel-path caveat.

## M3 cost projection

With `connect_entities` fixed, per-task cost drops (no hand-placement).
Rough single-attempt 24-task estimate: shallow $2–5, mid $5–10, deep
$10–20 even fixed, plus the 7 likely-unsolved deep tasks burning full budget.
Order of magnitude **$150–300** for one attempt — at or above the remaining
Factorio budget ($200 cap minus $32.11 spent = ~$168). A full 24-task sweep
likely needs a larger budget or a depth-stratified subset.
