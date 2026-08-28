# 767 — three-domain paper draft

Two things worth knowing before repeating this task.

**OOLONG has no scored LLM run, as of `0b6e6b9`.** `bench/oolong/README.md`
says it outright ("Scored runs with a model... are later milestones"), and
`RESULTS.md`/`PROTOCOL.md` confirm it: M1 is a hand-driven acceptance smoke
test with no model in the loop (the 0.9125 score in `RESULTS.md` is a
plumbing/scorer sanity check with manually supplied answers, not a model
accuracy figure); M2 (committed in the very last commit before this run)
built the 128K/1M/4M length-ladder packs and pre-registered a $100-cap sweep
design, but did not execute it. If a card or memory claims OOLONG "results"
exist and "are strong," check the repo before trusting that framing —
memory/card claims can drift from repo state, and the repo is the source of
truth (per `CLAUDE.md`). This draft reported OOLONG as an
architecturally-validated mechanism with a pending sweep, not as a third
scored result, specifically because of this gap.

**Paper/strategy drafts don't belong in this repo, but the agentd sandbox
can't reach where they do belong.** `~/workspace/assay-archive` is where
Mohsen's own paper/strategy material lives (per prior-session memory, and
consistent with commits `871059c`/`c11ee01` moving planning docs out of this
repo). An agentd worktree is sandboxed to the worktree itself —
`~/workspace/` is unreachable even for reads. There's no clean way to
satisfy "draft the paper, report a path" under that constraint except
staging the draft inside the code repo with an explicit note to move it out
before publication prep. If a future card asks for paper/strategy writing
again under agentd, expect the same conflict — it isn't a one-off.
