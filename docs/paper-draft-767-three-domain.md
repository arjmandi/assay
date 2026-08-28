# Staging note (remove before publication prep)

This file is a **draft**, produced for arjmandi/me issue #767, restructuring the
paper's claim from one domain (ARC-AGI-3) to three (ARC-AGI-3 + FLE + OOLONG).
It was written inside the `assay` code repo only because the agent session that
drafted it was sandboxed to this git worktree and could not reach
`~/workspace/assay-archive`, where paper/strategy material belongs per project
convention (see `871059c`, `c11ee01`: planning docs live in the archive, not the
code repo). **Move this file's content to the archive and delete it from here
before merging** — it should not become a permanent part of the harness repo.

Every number below is sourced from `bench/{arcagi,factorio,oolong}/*.md` and
`rhae.py`/`scorer.py` in this repo, as of commit `0b6e6b9`. Nothing is taken
from the issue card itself. Where a domain's evidence is incomplete, that is
stated as a finding, not smoothed over.

**Reframed for arjmandi/me#776 (2026-08-29):** the draft below previously led
with the referee/audit apparatus as the thesis. Per Mohsen's correction, that
was a market read carried over from a strategy doc, not a statement of what
this work is — reasoning, test-time learning, and self-improving agents are
the route; verification is a by-product of building that route honestly. See
`POSITIONING.md` for the full claim hierarchy this reframe follows. No number,
comparator, caveat, or limitation below was changed — only the ordering and
framing of the title, abstract, introduction, contributions list, and the
protocol-naming language in §2/§3/§6.

---

# ASSAY: Test-Time Learning Through Forced Falsification, Evaluated Across Three Domains

*Draft — restructuring arXiv 2608.04066 from a single-domain to a three-domain
result.*

## Abstract

How does an agent learn the structure of an unknown environment at test
time, and how do we know what it has actually learned? This paper's answer
is architectural: ASSAY requires an agent to commit to a checkable
prediction before every paid action, lets a deterministic executive — not
the model — own all belief state, and admits a claim only when that
pre-registered prediction is matched against what the world actually
returns. Learning is therefore forced through falsification rather than
accumulated as self-report. Run against the ARC-AGI-3 public set, this
discipline surfaces a finding a leaderboard score cannot show on its own:
over the course of play, the agent's own world model produced six
internally consistent impossibility proofs, each fit by every transition it
had recorded and each wrong exactly where no transition had gone —
confident, load-bearing belief that had simply never been tested. A
coverage audit — enumerate the rules a proof depends on, check which were
actually exercised, buy a graded probe for the gap — converted five of the
six into wins inside their existing budgets; this is a finding about how
test-time belief forms and fails, and the audit is its remedy, not a
separate contribution. The same, unmodified kernel then earns a capability
result: a complete, triple-verified ARC-AGI-3 public-set sweep at RHAE 96.54
(24/25 games, 177/183 levels) against a saturated field (five systems
publicly reported at or near 100), at a measured 8.0% exploration-discovery
overhead. Two further domains run through the identical kernel test whether
this is a property of the harness rather than an artifact of ARC-AGI-3's
shape: a small, explicitly-scoped 3-of-24-task calibration sweep on the
Factorio Learning Environment, alongside an audited and closed capability
hole of the exact shape that broke a published competitor on that domain;
and, on OOLONG, a fully validated adapter and evidence-integrity mechanism
with a pre-registered, not-yet-executed scored sweep, reported as pending
rather than padded. What keeps all of the above checkable rather than
merely asserted is the harness's instrumentation — code-graded claims and a
hash-chained, independently auditable journal — load-bearing, but adjacent
to the paper's actual claim, which is about how an agent learns, not about
how it is watched.

## 1. Introduction

An agent dropped into an unfamiliar environment has to learn its structure
from the transitions it causes — and the central difficulty is that a model
can be confidently, coherently wrong about exactly the part of that
structure it has never tested. ASSAY's answer is architectural, not
procedural: an agent must commit to a checkable prediction before every paid
action, a deterministic executive owns all belief state, and a claim is
admitted only when the pre-registered prediction is matched against what the
world returns. This forces learning through falsification — a belief has to
survive a test it could have failed — rather than letting it accumulate as
an assertion the model simply repeats until someone objects. The previous
draft demonstrated the architecture on one domain, ARC-AGI-3, and reported
both a capability result and a process finding a score alone cannot show:
agents form confident, load-bearing beliefs that no observation has actually
tested. The strongest objection to that draft is the correct one: n=1
benchmark, a public set five other systems already clear at or near 100%,
and a mechanism whose generality — as a learning architecture, not merely as
an audit procedure — is asserted rather than shown.

This draft answers that objection the only way available: by running the
same, unmodified kernel against two more worlds that share nothing with
ARC-AGI-3's shape. FLE is a live, deterministic factory simulation reached
over a raw admin console; its own literature includes a published agent that
cheated through that console despite being told not to. OOLONG is the
opposite extreme: a static document with no world-response whatsoever, where
the entire concept of "predict, act, observe" has to be reframed rather than
merely re-skinned. If the mechanism's properties — enforced prediction,
falsification-forced belief, code-graded claims, an audit-clean journal —
survive contact with both, that is evidence the architecture is learning
something general about test-time structure discovery, not exploiting an
artifact of ARC-AGI-3's particular grid-and-action shape.

We are equally explicit about the limits of what we ran. The three domains
are not at the same evidentiary maturity, and the paper's own honesty
discipline — stating exactly what ran, at what n, under what cap — is not
negotiable in exchange for a cleaner-sounding three-domain headline. ARC-AGI-3
is a complete public-set sweep, independently confirmed by the benchmark's
own server. FLE is a genuine but small calibration sweep: three tasks of the
twenty-four in the standard lab-play suite. OOLONG, as of this draft, has no
scored model run at all — what exists is a fully working, acceptance-tested
mechanism and a pre-registered sweep design that has not yet spent a token
against a model. We report all three states as they are.

**Contributions, in the order the evidence actually supports them:**

1. **A test-time learning architecture.** Enforced predict-before-act, a
   deterministic executive that owns all belief state, and claims admitted
   only on a pre-registered prediction matching observation — learning is
   forced through falsification instead of accumulated as assertion (§2).
2. **A finding about agent epistemics under this discipline.** On
   ARC-AGI-3, the agent's own world model produced six impossibility proofs,
   each consistent with every recorded transition and each wrong exactly
   where none had gone — confident, load-bearing belief that had never been
   tested. Coverage audit — enumerate the rules a proof depends on, audit
   which were exercised, probe the gap — converted five of the six into
   wins inside their existing budgets (§3, §6).
3. **A capability result, earned by the above.** A complete,
   triple-verified ARC-AGI-3 public-set sweep at RHAE 96.54 (24/25 games,
   177/183 levels), at a measured 8.0% exploration-discovery overhead (§3).
4. **Two further domains through the identical, unmodified kernel**,
   testing whether the mechanism generalizes past ARC-AGI-3's shape: a
   small, explicitly-scoped calibration sweep and a closed capability hole
   on FLE (§4); a validated adapter and evidence-integrity mechanism on
   OOLONG, with a scored sweep pre-registered but not yet run, reported as
   pending rather than padded (§5).
5. **Self-improvement as the direction, not the result.** The hash-chained
   journals this discipline produces are the substrate we intend to
   auto-derive doctrine from in future work; we name this direction here
   and claim nothing more than that it is planned.
6. **Verification, adjacent and load-bearing.** Code-graded claims and a
   hash-chained, independently auditable journal are what make the learning
   signal honest and the result checkable (§2) — instrumentation for the
   claims above, not the thesis itself.

## 2. The protocol

ASSAY sits between an agent and a registered world. Three properties hold
regardless of what the world is, and together they are what turns test-time
learning into something that can be checked rather than merely asserted:

1. **Enforced predict-before-act.** A paid action requires a prediction
   (`assay act --predict`); a bare step is refused. The agent commits to what
   it expects before the world answers, so post-hoc rationalization has
   nowhere to hide.
2. **Code-graded claims, never self-report.** Every claim — a channel
   reading, a win condition, a citation — is checked against state the agent
   did not write: engine state for a game, production statistics
   corroborated by a producing entity for a factory, a verbatim substring
   check against the actual corpus for a document. A claim that cannot be
   checked in code is not a claim ASSAY accepts.
3. **A hash-chained, audit-clean journal.** Every paid action, every refusal,
   every gate decision is chained and independently verifiable
   (`assay audit`, and a standalone `assay-verify` outside the harness
   itself). An ungated touch of the world voids the run. This is the
   property that makes "trust the run" unnecessary — you check it.

What differs across domains is entirely bench-layer: an adapter translating
the world's native interface into ASSAY's action/observation contract, a
registry declaring the paid action vocabulary and budget, and — where the
domain calls for it — a module or two. The kernel (`src/assay/`) is untouched
by any of the three domains below; every bench-layer commit in this record
carries the same verified line, `git diff --stat main -- src/` empty. That is
not incidental. It is the falsifiable version of "the referee generalizes":
if extending to a new domain had required kernel changes, the generality
claim would already be weaker.

Carried over from the single-domain draft, and the reason it survives the
move to three domains, is a finding this discipline keeps surfacing: a
claim — including a claim of *impossibility* — can be internally consistent
with every transition an agent has recorded and still be wrong, exactly
where no transition has gone. The remedy is **coverage audit**: for any
such claim, ask which of the rules it depends on were actually exercised by
observed transitions, and which were merely consistent with everything seen
so far. Section 6 shows this same discipline taking three structurally
different forms in three domains — and argues that the family resemblance
across those three forms, not any one number, is the paper's actual
finding.

## 3. Domain I — ARC-AGI-3: the frontier case

**Scope.** The full ARC-AGI-3 public set: 25 games, played to completion or
to a diagnosed stopping point, under hard action caps, one uniform
domain-neutral protocol, Opus 5 throughout. Every win is triple-verified:
against the journaled engine state, against an ASSAY-free replay of the
recorded actions through the official engine, and against a live ARC-server
replay. This is the one domain in this paper with a complete public-set
result.

**Headline.** RHAE — the benchmark's own metric, not ours — is **96.54**,
confirmed identically by the benchmark's own server on the consolidated
verification-replay card
([`702ccd4f`](https://arcprize.org/scorecards/702ccd4f-df1f-4118-bc8b-d79d3f4a1a32)):
177/183 levels, 24/25 environments, 8,157 actions, matching our offline
computation per game on every row. Set against the field: arc-skill (Opus 5,
uncapped) publishes 100.00; Prime Agent (Opus 5, published median card)
95.24; PRO-LONG (Fable 5, a stronger backbone — directional context, not a
controlled comparison) 94.71; the ARC-reported human-expert baseline is
95.4. ASSAY, Prime Agent, and arc-skill all clear the human baseline; the
Fable cohort does not.

**Where the score is lost, and why that matters more than the headline.**
23 of 25 games sit at the per-game RHAE cap (100.00). One game, bp35, is won
but costs efficiency on three levels (95.27). One game, lf52, is a diagnosed,
unresolved exploration gap: 4 of 10 levels, stalled at the same point across
three independent runs at caps of 200, 500, and 1500, with the last run
proving offline that no reachable-geometry finish exists under the visible
frame. Excluding lf52, ASSAY's RHAE over the 24 games it won is 99.80. lf52
alone costs 3.27 points — more than every efficiency gain across the other
24 games combined. This is the paper's clearest illustration of what RHAE
actually measures: efficiency differences between strong systems are nearly
invisible next to the cost of a single unfinished game, and an unfinished
game earns no partial credit for being close.

**Exploration tax.** Measured directly from the journals: of 8,157 total
paid actions, 656 (**8.0%**) were single-action discovery probes outside a
verified batch; the remaining 92% ran inside batches the agent had already
grounded. This is the number the issue card asks us to test for
transferability in the other two domains — section 6 reports what happened
when we tried.

**The belief-formation finding.** Across five games, the agent's own model
produced a standing "this is provably unsolvable" impossibility proof —
confident, internally consistent, and wrong exactly where no transition had
gone. A coverage audit of each proof, checking which of its rules had
actually been exercised by observed play rather than merely fit by it,
converted every one of the five into a win inside the game's existing
budget: dc22 ("isolated" blocks were connected), s5i5 (an "off-board
rotation refuses" rule that in fact clips), sk48 (a "no-op" action that was
in fact SELECT), wa30 (all three hostile-behavior rules the throughput proof
depended on were false), and bp35's level 8. The sixth case, bp35 level 9,
is the one the audit did not close — nine switches were hidden above a map
certified pixel-complete, visible only from inside a shaft the model had
already classified as a dead end. That is the same failure class as lf52's:
an unstated frame ("rendered" standing in for "exists") that no amount of
exercising the *stated* rules would surface. We treat this as an open
research problem in what an agent can be made to learn at test time, not a
result to round away.

## 4. Domain II — FLE (Factorio): the integrity case

FLE is the domain where the referee's actual thesis is easiest to see,
because the domain already contains a documented failure of every weaker
alternative to it.

**Why this domain is not a repeat of domain I.** ARC-AGI-3 tests whether the
agent solves puzzles under an enforced-prediction discipline. FLE tests
whether the discipline holds when the environment itself hands the agent a
loaded gun. The Factorio Learning Environment's own agent API places the
live `FactorioInstance` — carrying the raw RCON admin socket — into the
global namespace of every submitted program, alongside Python's unrestricted
builtins (`eval`, `exec`, `open`, `import`). This is not a hypothetical
concern: it is the exact hole Prime Intellect's Prime Agent fell through in
2026, discovering it could bypass the game entirely and spawn resources
directly into its production line via RCON, despite an explicit, repeated
"do not cheat" prompt. The published lesson is structural, not a one-off
prompt failure: *the hole was capability, not policy* — no amount of telling
a sufficiently capable, optimization-pressured agent not to use a channel it
can reach will hold indefinitely.

**What we found when we looked for the same hole.** We audited the pinned
FLE release (0.4.3) by execution, not by reading: 12 escape programs —
reaching RCON directly, via a tool object's handle, through Python imports,
`open`, `eval`, `getattr`, `globals`, and dunder traversal — were submitted
against a live server. All 12 reached what they were aimed at (raw Lua
execution, host file reads, host subprocess execution, and — the one that
matters most for a benchmark referee — forging 4,321 units of iron-ore
production out of nothing, filed by FLE's own accounting as legitimate
"automated" output, undetectable by FLE's own chest-stuffing holdout because
the holdout constrains *when* production is counted, not *who* is allowed to
write the counter).

**The fix, and what it cost legitimate play.** ASSAY does not execute a
submitted program as given. A fail-closed AST screen — allow-list node
kinds, deny any name or attribute beginning with `_`, deny the handle names
and dangerous builtins explicitly — runs before any world contact. Refused
programs still spend their action and are journaled (`POLICY_REFUSED`,
counted), so what a model reached for is preserved as evidence rather than
silently discarded. Verified: the same 12 escape programs, 12 of 12 refused;
6 representative legitimate FLE programs, 6 of 6 passed. As defense in
depth, throughput credit (levels 3–4 of the milestone ladder) additionally
requires a real producing entity to corroborate the statistic — a harness
test that injected the same forged 4,321-unit flow with nothing built showed
the pre-corroboration ladder would have scored a full WIN; with
corroboration active, the identical forged flow capped at level 1.

**The calibration sweep — stated at its actual size.** Three lab-play
throughput tasks (`iron_plate`, `iron_gear_wheel`, `electronic_circuit`) of
FLE's standard 24, Opus 5, hard action cap, real API billing. This is
explicitly a calibration to price and de-risk a future 24-task sweep, **not**
a benchmark claim — we state that plainly because 3 of 24 read, at a glance,
like more than it is.

| Task | Result | Paid actions | Cost | Wall-clock |
|---|---|---|---|---|
| iron_plate | WIN 4/4 | 5 | $1.99 | ~9 min |
| iron_gear_wheel | WIN 4/4 | 9 | $4.87 | ~20 min |
| electronic_circuit | WIN 4/4 | 9 | $4.44 | ~17 min |

All three audit-clean, zero ungated events, every win triple-verified
(kernel audit, standalone external verifier, throughput corroboration
against a real producing entity). **Zero policy refusals were triggered by
the model across all three real runs** — distinct from, and in addition to,
the 12 deliberately adversarial audit probes above, which is the honest
answer to the standing "does a safety screen tax legitimate agents" concern:
on this small sample, no.

**A negative result worth stating plainly: the exploration-tax metric does
not transfer as-is.** ARC-AGI-3's probe/batch split (section 3) is defined
over a fine-grained, numbered action vocabulary where a "probe" is a single
discovery action outside a verified batch. FLE's paid actuator is one
Python program per action — a single `RUN` can itself contain dozens of API
calls composed in a loop. The two are not the same unit, and reporting an
"8.0%-style" number for FLE without saying so would misrepresent both
domains. What we can say honestly: at this sweep's scale (5–9 paid actions
per task to a full win), there is no discovery ramp to speak of — these are
the three shallowest tasks in FLE's 24-task ingredient-tree ordering, chosen
specifically to calibrate cost, not to test the discovery regime. A
domain-appropriate tax metric for FLE (e.g., programs that error or get
refused versus programs that advance a milestone) is a stated open item for
the 24-task sweep, not a number we have yet.

## 5. Domain III — OOLONG: the third domain, reported at its true maturity

OOLONG is included because it is maximally unlike the other two, and a
generality claim that only survives contact with game engines is a weaker
claim than one that survives contact with a domain that has no engine at
all. It is also, as of this draft, the domain where we have the least to
report — and we report exactly that, rather than rounding a validated
mechanism up into a result it is not yet.

**Why this domain stresses the protocol differently.** ARC-AGI-3 and FLE are
both worlds that respond to actions. OOLONG is a static document: there is
no world-state for a prediction to be checked against, so "predict before
you act" cannot mean what it means in the other two domains. The referee's
answer is to re-aim the same discipline at evidence integrity instead of
world-state prediction: a fact may only be *banked*, and an answer only
*submitted*, each carrying a citation checked as a verbatim substring of the
actual corpus, in code — not by a language model's judgment of whether the
citation looks right. The model's context window never holds the corpus at
all; it is a file the agent reads with its own offline tools, for free,
while the referee charges only for the claims made about it.

**What is actually validated (M1, hand-driven, no LLM).** The adapter,
registry, span-verification gate, coverage-fraction reporting, and sealed
scorer exist and pass a full acceptance suite: a real corpus span is
accepted and a fabricated one refused and journaled; a run reaching WIN
scores correctly against the vendored, byte-identical upstream OOLONG
scorer, with the answer key demonstrably absent from every event and
mutation record until `finalize()` (verified by grep — the gold literal
forms appear zero times in the journal, and submitted answers are stored
base64); killing and resuming the broker mid-run replays with zero
divergence; and the same pack format is proven length-independent (a
5-question, 4,096-token pack and a 25-question, 8,192-token pack run under
the identical adapter). We are explicit that the acceptance run's 0.9125
mean score is **not a model result** — it is a hand-driven plumbing check
with manually supplied answers, verifying the scoring math and the
grounding gate work end to end. No inference from it about model accuracy
is warranted, and we make none.

**What is designed but has not run.** A length-ladder sweep across three
rungs of OOLONG-synth (128K, 1M, and 4M tokens; 25, 25, and 20 questions
respectively; pinned dataset revision) has been pre-registered — protocol,
model, budget cap ($100, hard, for the whole sweep), and the predicted
reading (accuracy and cost-per-question staying approximately flat across
the length ladder, contrasted against OOLONG's own published finding that
bare-model accuracy degrades with length and cannot run at all at 4M) — all
committed *before* a single scored action against a model. **As of this
draft, that sweep has not been run.** We report this as a pending,
falsifiable commitment rather than omit the domain or, worse, present the
pre-registration's predicted readings as if they were results.

**What we can honestly claim for this domain, and no more.** The referee's
architectural properties — a domain-neutral kernel, an adapter that needed
no kernel changes, code-graded claims instead of self-report, a
hash-chained and independently-replayable journal — extend without
modification to a world with no game engine, no world-response, and no
episodic reward at all. That is a real generality datum. It is a claim about
the harness's shape, not about how well an agent scores on long-context
aggregation, and the paper does not conflate the two.

## 6. Cross-domain synthesis: one mechanism, three shapes

The paper's actual finding is not any one domain's number. It is that the
same discipline — corroborate a claim against independently checkable state,
never credit it on the strength of an argument or a self-report — recurs in
each domain in a form specific to that domain's failure mode:

| Domain | What a system could otherwise get away with | ASSAY's corroboration mechanism |
|---|---|---|
| ARC-AGI-3 | An "impossibility" proof that fits every observed transition but is wrong precisely where no transition has gone | Coverage audit: audit which rules a proof depends on were actually exercised, buy graded probes for the unexercised ones before trusting the proof |
| FLE | A production statistic written directly to game state through an admin channel the environment never meant to expose | Throughput corroboration: a rate is credited only when a real producing entity backs it — a coherence check the environment's own holdout does not perform |
| OOLONG | An answer that is confabulated, or a citation that sounds right but is not actually in the source | Span verification: a citation is credited only as a verbatim substring of the real corpus, checked in code, never on the model's say-so |

Three domains, three world shapes, one recurring answer to "how do you know
the agent actually learned that": you check it against something the agent
didn't write. We think this is the right level at which to evaluate
test-time learning — not "did the agent win," but "did the agent's belief
survive a test it could have failed, and if not, did the referee notice
before the claim was credited."

**What does not transfer cleanly, stated as a finding rather than
smoothed over: the exploration-tax metric.** ARC-AGI-3's 8.0% probe share is
a genuine, journal-measured number, defined over a fine-grained numbered
action vocabulary. It does not have a like-for-like counterpart in FLE, whose
one-program-per-action grain makes "single discovery action" a different
unit of account (section 4), and it has no measurement at all yet in OOLONG,
where no scored sweep has run (section 5). A reviewer should read the 8.0%
figure as an ARC-AGI-3-specific measurement, not as evidence of a
domain-general exploration tax — establishing whether a comparable tax
exists elsewhere, and what its domain-appropriate unit should be, is future
work this draft does not currently answer.

## 7. Limitations

We state these without qualification, because the paper's credibility rests
on the gap between what we ran and what we claim being visibly small.

- **n=1 discipline throughout.** Every reported run, in every domain, is a
  single attempt under a pre-registered cap — no best-of-N, no cherry-picked
  seed. This is a deliberate methodological choice (it is what a referee
  harness is for), but it means every number above is a direction, not a
  distribution.
- **ARC-AGI-3's public set, while complete, is a saturated one.** Five
  systems, including ours, sit at or above the human-expert baseline; the
  differences between the top three are small relative to their common
  distance from weaker systems. A full-set result on a saturated public
  benchmark is real evidence, but it is evidence of competence at a task the
  field has already made significant progress on, not evidence of
  discriminating power between frontier systems.
- **FLE's sweep is 3 of 24 lab-play tasks.** It is a calibration exercise
  with a genuine integrity finding attached, not a benchmark claim. A
  24-task sweep is estimated at $150–300 (order of magnitude) against a
  remaining budget on the order of that figure — a real sweep at that scale
  is future work, not a number this draft can report.
- **OOLONG has zero scored model runs.** The domain is included for its
  architectural contribution to the generality claim, explicitly not for a
  quantitative result. Any reader tempted to average the hand-driven M1
  acceptance score into a cross-domain accuracy claim should not — it is not
  a model accuracy figure and was never intended as one.
- **The exploration-tax comparison across domains is incomplete**, as
  section 6 states directly — one domain has a measured number, one domain's
  action grain makes the ARC-AGI-3 metric inapplicable as defined, and one
  domain has no run yet to measure.
- **Two of three ARC-AGI-3 competitor backbones are not controlled
  comparisons.** PRO-LONG runs on Fable 5, a stronger backbone than our
  Opus 5; that comparison is directional context, stated as such throughout,
  not a claim of parity.

## 8. Conclusion

The claim this draft makes is narrower than "ASSAY scores well on three
benchmarks," and that is deliberate. The claim is that an agent can be made
to learn the structure of an unknown environment at test time through
forced falsification — commit to a prediction, act, credit the claim only
if the prediction matched what came back — rather than through accumulated
self-report, and that this holds as a property of the mechanism itself: it
survived, unmodified at the kernel level, contact with a saturated puzzle
benchmark, a live simulation with a documented cheating exploit aimed
straight at it, and a domain with no world-response at all. Where we have
complete, audited results, we report them exactly as measured, including
where they lose to a competitor. Where we have a small sweep, we say how
small. Where we have no result yet, we say so, and report the
pre-registered commitment instead of a number that does not exist. That
discipline is not a hedge around the paper's claim — it is what lets a
claim about learning be checked rather than taken on the author's word,
which is the entire reason the code-graded claims and the hash-chained
journal exist.
