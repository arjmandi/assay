# 2026-08-24 · issue #742 · RESULTS.md and COMPARISON_TABLES.md disagree on total actions

`bench/arcagi/RESULTS.md` line ~155 says "zero ungated events across ~7,800
paid actions" for the completed 25-game public set. `COMPARISON_TABLES.md`
(Table 1a, same date) sums a full per-game action table to **8,156** actions,
656 of them probes (8.0% exploration tax) — this is the number commit
`0428437` corrected the probe count for (636→656), but it never touched
RESULTS.md's older, rounded "~7,800".

Anything citing ASSAY's total-action or exploration-tax figures externally
should use COMPARISON_TABLES.md Table 1a (8,156 / 656 / 8.0%), not RESULTS.md's
approximation — and should not trust the issue card's own numbers either: the
arjmandi/me#738 strategy doc quotes "~7.8%" overhead, which is stale relative
to the corrected 8.0% now in the repo. Used in `docs/funding/*.md` (arjmandi/me#742).
