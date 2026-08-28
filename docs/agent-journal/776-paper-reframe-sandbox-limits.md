# 776 — reframing "the paper" across unmerged sibling branches, and two hard walls

Three things worth knowing before repeating a task like this.

**"The paper" is not one file, and it is not on `main`.** As of this run,
`main` has no paper draft at all. Two candidates exist only on unmerged
sibling agent branches, at different staleness: `origin/agent/issue-740`
has a single-domain (ARC-only) `docs/PREPRINT-DRAFT-2026-08-24.md`, branched
before the Factorio/OOLONG bench work landed; `origin/agent/issue-767` has
`docs/paper-draft-767-three-domain.md`, branched directly off the commit
this worktree also started from, restructuring the claim to three domains
and explicitly superseding the single-domain version. The 767 draft is the
one that matches this issue's own description (small FLE sweep, OOLONG
pending) — the 740 draft does not mention Factorio or OOLONG at all. Don't
assume the file named in a card or a stale memory is still the current one;
check which sibling branch's content actually matches the caveats the task
describes. Pulling a specific file out of a sibling branch via
`git show <branch>:<path> > <path>` (rather than merging the whole branch)
avoids dragging in that branch's unrelated, possibly-stale changes (in this
case a `CONSTITUTION.md` → `DOCTRINE.md` rename neither branch has actually
landed on `main`).

**The real, currently-published paper is not in this repo at all.**
Per `docs/agent-journal/767-three-domain-paper-draft.md`, paper/strategy
material belongs in `~/workspace/assay-archive`, and an agentd worktree
cannot reach it — confirmed again this run (`ls`/`cat` outside the worktree
is blocked outright, not just discouraged). Cross-referencing
`arjmandi/me#738`'s later comments shows the actual paper has since been
rewritten further, published to Zenodo, and resubmitted to arXiv — entirely
outside this repo's git history. So "reframe the paper" from inside this
sandbox necessarily means reframing the best reachable in-repo draft (here,
the 767 draft) as a reference implementation of the new framing, not editing
the actual version of record. Say this explicitly in the report; don't imply
the live, published copy was touched.

**A `github.com/user-attachments/...` link on a private repo's issue is
unreachable from a headless run.** The task asked to reconcile a doc against
an attachment linked on `arjmandi/me#738` (a private repo). `WebFetch`
returned a plain 404 (not a permissions error) — consistent with GitHub
serving 404 instead of 403 for attachments on private repos when the
requester isn't authenticated in-browser. `curl`/`gh api` aren't in the
allowlist and silently require approval that never arrives unattended. There
is no in-sandbox workaround; the only path is pasting the content into the
issue thread or a repo the agent can read, as a prior run on this same issue
chain already noted.
