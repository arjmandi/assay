# 2026-08-24 · issue #736 · FLEET-RULES.md not present in worktree

CLAUDE.md instructs agents to "read `.claude/FLEET-RULES.md` first" and says
"the daemon refreshes it from pado on every run." In this worktree
(`agent/issue-736`, the first real run of the new `agent: assay` route from
arjmandi/me#734), that file did not exist — `.claude/` only contained
`settings.json`. No other FLEET-RULES.md exists anywhere in the repo either.

This didn't block the task (a one-line README edit), but a future agent
relying on fleet-wide rules (scratch dir location, browser policy, etc.)
won't find them here. Worth checking whether the route's daemon config
actually wires up the FLEET-RULES sync step, or whether it's expected to be
absent for this project and CLAUDE.md's instruction is stale/aspirational.
