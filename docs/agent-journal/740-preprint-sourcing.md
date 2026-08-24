# 2026-08-24 · issue #740 · preprint sourcing: two artifact discrepancies, one genealogy fact

Drafting the claim-stake preprint (`docs/PREPRINT-DRAFT-2026-08-24.md`) surfaced two
things worth knowing before the full Nov 8 tech report is written, plus one external fact
that took a live arXiv fetch to establish and isn't recoverable from this repo alone.

**1. The exploration tax is 8.0%, not 7.8%.** Both `arjmandi/me#738` (the strategy doc)
and `#740` (this task's card) state "~7.8% exploration tax." Neither `RESULTS.md`,
`COMPARISON_TABLES.md`, nor `rhae.py`/`baselines.json` contain 7.8% anywhere.
`COMPARISON_TABLES.md` Table 1a computes 656 probe actions of 8,156 total = 8.04% → 8.0%.
The 7.8% figure looks like it predates the final 25-game tally (possibly computed from an
earlier subset) and was never corrected in the two issue threads that repeat it. If you
see 7.8% quoted again, it's the stale secondhand number — recompute from Table 1a.

**2. `COMPARISON_TABLES.md`'s own prose is internally inconsistent on the human-expert
reference.** It says "ASSAY, Prime Agent (marginally) and arc-skill clear it" against a
95.4 reference — but its own Table 0 lists Prime Agent's RHAE as 95.24, which is *below*
95.4, not a marginal clear. Numerically, only ASSAY (96.54) and arc-skill (100.00) clear
95.4; Prime Agent and PRO-LONG (94.71) both fall short. I didn't edit the artifact
(RESULTS.md/COMPARISON_TABLES.md are treated as historical record per CLAUDE.md), just
reported the correct numeric relationship in the preprint and flagged the discrepancy
inline. Whoever writes the full tech report should either fix this line in
`COMPARISON_TABLES.md` or confirm it's intentional (it doesn't look like it is).

**3. arXiv:2608.04066 (the direct prior work) reported *zero* ARC-AGI-3 task efficacy.**
This isn't documented anywhere in this repo — I only learned it by fetching the live
arXiv abstract page. The paper's contribution was a verification methodology (a
deterministic Executive + typed-proposal LLM + code-gated predict-before-act claims, with
its own self-invalidating instrumentation) and a commitment/binding drift dissociation;
it explicitly pre-registered "zero level completions across 52 gated runs on ARC-AGI-3"
as a structural defeater, not a hidden result. This is the redemption-arc fact that makes
the preprint's lineage claim work: ASSAY's kernel is that architecture's *first*
capability result, three weeks after 52 runs of it scored nothing. Worth keeping in the
tech report's framing — it's a stronger claim than "prior work exists," and it isn't
derivable from anything in this repo without the live fetch.

Two citations in the preprint draft are marked `[[TBD]]` because no linkable source for
them exists in this repo: arc-skill's scorecard (referenced by name only throughout
`bench/arcagi/`) and the canonical ARC-AGI-3 benchmark citation. Whoever finalizes the
paper needs to supply both rather than have an agent guess a URL.
