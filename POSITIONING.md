# POSITIONING

This file exists so that no future paper draft, website copy, or funding
document drifts back to a framing this project has already moved past.
Read it before writing anything that describes what ASSAY *is*, as opposed
to how it works.

## The correction this document encodes

An earlier strategy document argued "sell trust, not capability" — a market
read about a saturated ARC-AGI-3 leaderboard, not a statement of what this
work is. That framing leaked into the paper, making it read referee-first:
auditability as the headline, capability and the learning result as
supporting evidence underneath it. Per Mohsen's correction
(arjmandi/me#776, 2026-08-29):

> "reasoning · test-time learning · self-improving agents are very
> important for me and assay is the best work in these field so far that
> i've done. verifications and other trust angles ... were by-products of
> this main goal. I want to be 100% sure that paper, assay website later,
> and other artifacts stays on this, make this the main route and other
> areas adjacent."

**Reasoning, test-time learning, and self-improving agents are the route.
Verification is a by-product of building that route honestly, not the
destination.** Every paper, the website when it exists, and any other
public-facing artifact should lead with the claim hierarchy below, in
order.

## The claim hierarchy, in order

1. **The question.** How does an agent learn the structure of an unknown
   environment at test time, and how do you know what it has actually
   learned?
2. **The mechanism.** The agent must commit to a prediction before it acts;
   a deterministic executive owns belief; a claim is admitted only when a
   pre-registered prediction matches observation. Learning is forced
   through falsification instead of accumulated as assertion. This is a
   test-time learning architecture, and that framing is the paper's spine.
3. **The finding.** Agents form confident, load-bearing beliefs that no
   observation ever tested. Six impossibility proofs, each consistent with
   every recorded transition, each wrong exactly where no transition had
   gone. The coverage audit protocol (enumerate load-bearing rules → audit
   which transitions exercised each → buy graded probes for the gaps)
   converted five of six into wins inside existing budgets. This is a
   result about agent epistemics under test-time learning, not an audit
   procedure that happens to help.
4. **The capability result.** 24/25 games, RHAE 96.54, ARC-AGI-3 public
   set, under hard caps, 8.0% overhead. Earned by 2 and 3, reported after
   them.
5. **Self-improvement as the roadmap.** Journals as the substrate for
   auto-deriving doctrine (the machinery phase). Name it as the direction;
   don't overclaim it as done.
6. **Verification, adjacent and load-bearing.** Gating, code-grading, and
   hash-chained journals are what make the learning signal honest and the
   result checkable. Instrumentation, not thesis. It belongs in the paper;
   it does not belong in the abstract's first sentence.

## What this does and doesn't change

- **No numbers move.** This is a framing and ordering correction, not a
  results correction. Every figure, caveat, and limitation stands exactly
  as measured and published, including partial or pending evidence (e.g. a
  small calibration sweep, or a domain with a result still pending) —
  reported at its true maturity, not rounded up or down to fit the frame.
- **The published protocol name stays.** Where a protocol name is already
  public (e.g. under a DOI), keep it. Change what work the name is
  described as doing — a remedy for a finding about belief formation, not
  a freestanding auditing contribution — not the name itself.
- **Verification content stays in the paper.** This hierarchy reorders
  emphasis; it does not delete the verification material. Gating,
  code-grading, and the hash chain are real, load-bearing engineering and
  belong in the method section — they are just not the opening claim.

## Where this applies

- Every paper draft, present and future (title, abstract, introduction,
  and contributions list in that order).
- The ASSAY website, when it is built.
- Funding and grant applications, to the extent they describe what ASSAY
  is rather than what it needs.
- Any future agent run that touches one of the above should read this file
  first.
