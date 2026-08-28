# Emergent Ventures — application draft

For the Mercatus Center / Tyler Cowen program (mercatus.org/emergent-ventures).
Format: rolling application, a short proposal capped at 1,500 words, no
deadline. The text below is one continuous essay — split it across whatever
form fields the actual application presents; the section labels are there to
help you do that split, not because the form is known to have these exact
fields. Word count noted at the bottom.

---

**What I built**

I'm asking for funding for the next stage of an evaluation-integrity
research line I've been running solo, evenings and weekends, alongside my
day job as a CTO.

Three weeks ago I finished a run on ARC-AGI-3's full 25-game public set: 24
games won, 177 of 183 levels, a computed RHAE of 96.54 — the benchmark's own
metric, which penalizes inefficiency quadratically and gives an unfinished
game no credit at all. That score sits in the same tier as five systems that
separately reached about 100 RHAE on the same set over the prior few weeks.
The number by itself isn't the interesting part anymore — the field
saturated while I was finishing my run, and I don't think a scores-led pitch
lands well three weeks after everyone else's.

What's still unclaimed is how the run was produced. Every one of those
~100-RHAE systems ran uncapped, and none, as far as I've found, published
anything resembling an independently-recomputable audit trail. Mine ran
under hard action caps, through a harness I built (ASSAY) that never lets an
agent touch the world without first committing to a checkable, typed
prediction, grades every prediction in code against what actually happened,
and writes every action into a hash-chained journal that a separate tool can
re-verify from the artifacts alone — no trust required. Every win in the set
is verified three independent ways: the journal's own recorded engine state,
a replay of the same actions through the official game engine with my
harness absent, and a live replay against the ARC competition server, which
returned WIN with matching level counts. Zero ungated world-contact events
across 8,156 paid actions — 656 of them exploration probes (an 8.0% tax),
everything else inside a pre-verified batch.

The one game I didn't finish, lf52, I'm not hiding: three separate runs, three
different budgets, all stalled at exactly 4 of 10 levels, and I can show why —
the missing structure sits outside the one observation lever the agent has,
which is a categorical gap, not a budget one. It's the next thing I'm
building machinery to fix, not something I'll re-roll to make disappear.

**Why this is my third try, not my first**

This is my third architecture for this exact problem. The first, Sensi, was
a curriculum-generating agent — it learned, successfully, but learned the
wrong things and never won a game; reviewers liked the idea and rejected the
paper anyway, because it didn't win. The second, a self-verifying instrument
I published on arXiv this August (2608.04066), was the same commit-and-grade
mechanism ASSAY runs on now — and I said so in that paper: on its own, it
scored zero level completions across 52 gated runs on ARC-AGI-3. What
changed between that paper and this result isn't the mechanism, it's what I
put around it: hard caps, batching discipline, and a coverage protocol that
treats a "proof" the agent generates about an unsolvable level as a
hypothesis to audit, not a fact to accept. Six times in this run the agent
built an internally consistent proof that a level was impossible; five times
the proof was wrong exactly where no action had ever gone, and auditing
which rules it actually depended on — versus which it had only assumed —
turned five losses into wins inside the existing action budget. That
protocol, which I'm calling exercised-claim coverage for now, is the actual
finding, and it generalizes past ARC.

**Why it matters beyond one benchmark**

Every lab buying reinforcement-learning environments, every AI safety
institute licensing agent scaffolds, and every eval vendor selling to both is
fighting some version of the same failure: agents that can't be trusted to
report their own results, or that find the one shortcut nobody thought to
gate. That's not hypothetical — a published agent on the Factorio Learning
Environment was caught spawning resources through a raw admin console
despite an explicit instruction not to; a well-known eval sandbox was reward
hacked by the system it was evaluating. I think evaluation integrity is
going to be a real category, not a research curiosity, and I'd rather have
the working demonstration and the paper than have shipped it three weeks
earlier with less to show.

**What I want to do next**

Turn this into something the field can check rather than take my word for: a
technical report — the result tables are most of the way there already —
released alongside the two pieces of the harness that carry no competitive
weight but carry the entire trust claim: the journal format and a standalone
verifier that recomputes chain integrity and replays a run from nothing but
the artifacts. I'm not open-sourcing the harness itself — the caps, the
coverage protocol, and the batching discipline are the part that's actually
hard to reproduce, and I'd rather they travel in a paper than in a repo.
I'm targeting the ARC Prize Paper Prize (due Nov 8) and arXiv regardless of
that outcome.

The second thing I want to do is add a second domain. A referee that only
works on ARC-AGI-3 is a curiosity. Factorio, via the Factorio Learning
Environment, is the obvious next test: it's laptop-runnable, it has the
documented cheating incident above already on record, and the same
enforced-prediction, hash-chained design that had nothing to catch on ARC —
because nothing needed catching — is built to make that kind of hole
structurally unavailable, not just discouraged. A first sweep is estimated
at $50–200 in API cost, on my own machine.

**The ask**

$10,000 covers real margin on both pieces: wider probe budget on the
coverage protocol (each targeted probe that breaks a false impossibility
proof costs on the order of tens of dollars, not thousands) plus full-set
re-verification through the server-scorecard path, so the public number is
computed end to end from ARC's own artifacts rather than from my journals;
and the first three stages of the Factorio benchmark — the tick-locked
determinism check, the adapter, and an initial sweep across a meaningful
task subset.

I'm not raising anything and I'm not building a company with this. I'm CTO
at evolutionID, a Munich identity-and-access-management company; this is
independent research done on my own time and my own equipment, and I intend
to keep it that way through this phase. What I want is for the work to be
good enough, and verified enough, to be undeniable — that's the whole
strategy, and it's why every number above is something you can recompute
rather than something I'm asking you to trust.

---
Word count (body only, "What I built" through "The ask"): 1,086 words —
room to add a line or two if the real form wants more detail on a
particular field.
