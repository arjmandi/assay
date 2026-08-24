# Laude Slingshots — application draft

For the Laude Institute's Slingshots program (laude.org/slingshots,
slingshots.laude.org) — grants + compute + product/engineering support for
researchers turning eval-focused work into a startup or an open-source
codebase. Prior cohorts funded Terminal-Bench and ARC-AGI itself, which is
the most direct precedent for this project. Text below is a continuous
draft — split across whatever fields the actual application presents.

---

## What I'm building

ASSAY is a referee harness: it sits between an agent and a world you
register, and it never lets the agent touch that world without first
committing to a checkable, typed prediction. Every prediction is graded in
code against what actually happened. Every action lands in a hash-chained,
append-only journal that a separate tool can recompute and verify from the
artifacts alone. The design target is a specific, recurring failure in
agent evaluation: results that can't be trusted because the agent
self-reported them, or because it found a shortcut nobody thought to gate.

Three weeks ago I ran it against the full ARC-AGI-3 public set (25 games,
Opus 5, hard action caps): 24 games won, 177 of 183 levels, computed RHAE
96.54. Every win is verified three independent ways — the journal's
recorded engine state, a replay of the same actions through the official
engine with ASSAY absent, and a live replay against the ARC competition
server itself, which returned WIN with matching level counts. Zero ungated
world-contact events across 8,156 paid actions (656 of them exploration
probes — an 8.0% measured tax). The one unfinished game, lf52, stalled at
4 of 10 levels across three independently-budgeted runs; I know why (a
categorical observation gap, not a resource one) and it's the target for
the next piece of machinery, not something I've papered over.

The result that matters more than the score: six times during that run the
agent constructed an internally-consistent "proof" that some level was
impossible. Five of those six proofs were wrong exactly where no action had
ever gone — and a protocol that audits which rules a proof actually
depended on (versus which it silently assumed) converted five losses into
wins inside the existing action budget. I'm calling this exercised-claim
coverage for now. It isn't ARC-specific — it's a general finding about what
"provably unsolvable" is worth when the proof itself was never checked
against the agent's own evidence, and it applies to any eval where an agent
can report a negative result.

## Why this fits Slingshots specifically

You've funded ARC-AGI itself and Terminal-Bench — both are, at core, bets
that eval infrastructure is worth funding directly rather than waiting for
it as a side effect of capability work. ASSAY is the same bet from the
integrity side: not a new benchmark, but a layer that makes any benchmark's
results checkable rather than asserted. The buyers who already spend money
on this problem are the same people your first cohort's projects serve —
labs buying RL environments, AI safety institutes licensing scaffolds,
environment vendors who need their own environments to be resistant to the
agents being tested in them. A published agent on the Factorio Learning
Environment was caught spawning resources through a raw admin console
despite an explicit "don't cheat" instruction; a well-known eval sandbox was
reward-hacked by the very system it was evaluating. Prompt-level anti-cheat
doesn't hold. A capability boundary — the agent structurally cannot reach
the exploit, and any attempt that bypasses the gate voids the run at audit
time — is a different kind of answer, and it's what ASSAY is.

## The pledge

My final work product under this program would be the open-source track:
the journal format specification, the claim-grammar spec, and a standalone
audit/replay verifier — the part of ASSAY that carries the entire trust
claim and none of the competitive surface. Anyone would be able to take an
ASSAY-produced journal and independently recompute its integrity and replay
its actions without running ASSAY itself. That's a real, usable release,
not a token slice of the repo — but I want to be precise about what it
isn't: I'm not open-sourcing the grading/gating kernel, the caps and
batching discipline, or the doctrine that drives the agent. That part is
what's actually hard to reproduce, and it's going into a paper (targeting
the ARC Prize Paper Prize, due Nov 8, and arXiv regardless) rather than a
repo. If that split doesn't satisfy the letter of "open-source codebase" as
your program means it, I'd rather say that now than overpromise.

## What's next

Two threads, both eval-integrity work, not capability work:

1. **The paper + layer-1 release**, together — the technical report (the
   result tables are most of the way there already), the verifier, and the
   spec, released as one event so the credibility claim and the tooling
   arrive at the same time.
2. **A second domain.** A referee that only works on ARC-AGI-3 is a
   curiosity; Factorio via the Factorio Learning Environment is the
   obvious next test, given the documented cheating incident above. It's
   laptop-runnable — a first sweep is estimated at $50–200 in API spend.

## The ask

Grant + compute credits in whatever range fits your typical Slingshot
award — order of magnitude, this covers the Factorio benchmark build-out
(adapter, determinism check, and an initial sweep across a meaningful task
subset) plus continued probe budget on the coverage protocol and
server-side re-verification of the ARC scorecards, so the public numbers
are computed end to end from the competition's own artifacts. I'm not
raising anything and not incorporating for this — I'm CTO at evolutionID,
a Munich identity-and-access-management company, and this is independent
research on my own time and equipment.
