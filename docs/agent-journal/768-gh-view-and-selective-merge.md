# 2026-08-28 · issue #768 · `gh issue view --comments` is silently empty here; use `--json`

`gh issue view <N> --repo arjmandi/me --comments` — the exact command this
repo's `CLAUDE.md` tells every agentd run to use first — returns **zero
bytes on stdout and stderr, exit success**, in this headless sandbox. No
error, no timeout, nothing to grep for. It looks identical to "issue has no
body and no comments," which is never true for a real issue. `gh issue view
<N> --repo <repo> --json body,comments,title` works fine and returns the
full content. Root cause not confirmed (plausibly the glamour/TTY renderer
misbehaving under a non-interactive terminal), but the fix is: **never trust
the plain rendered `gh issue view`, always pull `--json` fields** when
reading an issue body/comments programmatically. `--json title,number,state`
also works, so plain flags aren't broadly broken — it's specifically the
markdown-rendered path.

## Selectively merging commits when `git cherry-pick` isn't in the allowlist

`.claude/settings.json`'s Bash allowlist has `git merge`, not `git
cherry-pick`. To bring in specific commits from another branch's history
while excluding a later commit on that same branch (here: #753's build was
approved but then reverted on `agent/issue-753`, and the task was to ship it
from *this* session, i.e. everything on that branch except the revert
commit) — `git merge <commit-sha>` works, because it merges that commit and
all of *its* ancestors, not the branch tip. Pick the last commit you *do*
want (here, the commit right before the revert) and merge that SHA
directly; the revert, being a later, un-merged descendant, never enters the
history. Worked cleanly with zero conflicts here because the two source
branches (`agent/issue-753`, `agent/issue-754`) only touched new files plus
one already-unchanged region of `README.md` — check `git diff <fork-point>
HEAD -- <shared files>` first if the target branch has since diverged on
files the commit you're merging also touches.
