# Anthropic External Researcher Access Program — application draft

For Anthropic's External Researcher Access Program — free API credits for
researchers on AI safety/alignment topics, reviewed monthly (first Monday).
Standard award is $1,000 in credits; the form asks for details on the
researcher and the research topic. Draft below covers both.

---

## Research topic

I work on evaluation integrity: making it structurally impossible for an
agent to fabricate or shortcut its way to a benchmark result, rather than
relying on the agent's own honesty or a reviewer noticing after the fact.
This is a live, documented problem — a published agent on the Factorio
Learning Environment was caught spawning resources through a raw admin
console despite an explicit instruction not to cheat; a well-known eval
sandbox was reward-hacked by the system it was evaluating. Prompt-level
instructions not to cheat don't hold once a capability exists to route
around them.

My harness, ASSAY, answers this by construction rather than by instruction:
an agent can never touch the registered world without first committing to
a checkable, typed prediction, every prediction is graded in code against
what actually happened, and every action lands in a hash-chained journal
that a separate tool recomputes and verifies from the artifacts alone —
any world-contact that bypassed the gate voids the run at audit time. It's
a capability boundary, not a policy.

I validated this on the ARC-AGI-3 public set (Opus 5, hard action caps): 24
of 25 games won, 177 of 183 levels, computed RHAE 96.54. Every win is
verified three independent ways — journal-recorded engine state, a replay
through the official engine with ASSAY absent, and a live replay against
the ARC competition server itself (WIN, matching level counts). Zero
ungated world-contact events across 8,156 paid actions, 656 of them
exploration probes (an 8.0% measured tax). The one unfinished game, lf52,
stalled at 4 of 10 levels across three separately-budgeted runs — a
categorical gap I can locate precisely, not a hidden one.

The finding I think is most safety-relevant: six times in that run the
agent built an internally-consistent "proof" that a level was unsolvable.
Five were wrong exactly where no action had ever gone. Auditing which
rules a proof actually depended on — versus which it silently assumed —
turned five false negatives into wins inside the existing budget. That's a
general result about trusting an agent's negative self-reports in any eval
setting, not an ARC-specific trick.

## What the credits are for

Two extensions, both on Claude models specifically: (1) probing the
exercised-claim coverage protocol further against the unfinished ARC game,
and (2) starting a cross-domain test of the same design on the Factorio
Learning Environment — the environment with the documented raw-console
exploit above, which is exactly the case this architecture is meant to
close off structurally rather than by instruction. A first Factorio sweep
is estimated at $50–200 in API spend; $1,000 covers a real first
attempt plus continued ARC verification runs.

## About me

I'm CTO at evolutionID; this is independent research, done on my own time
and equipment. This is my third architecture aimed at the same underlying
problem — an earlier self-verifying instrument I published on arXiv this
August (2608.04066) used the same commit-and-grade mechanism ASSAY runs on
now, and on its own it scored zero level completions across 52 gated runs
on ARC-AGI-3. The mechanism didn't work until I put hard caps, batching
discipline, and the coverage protocol around it — which is the part I'd
want feedback on from people who spend their time thinking about where
agent evaluations actually break.
