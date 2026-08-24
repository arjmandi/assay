# Agent journal

One file per entry, in this directory — `<issue>-<short-slug>.md` — so
concurrent PRs never touch the same file and never conflict. This is how a
fresh session inherits what previous sessions learned; sessions are per-issue
and get retired, these files do not.

Write an entry only when you learned something a future agent would otherwise
rediscover the hard way — a dead end, a non-obvious constraint, a decision and
why the alternative was rejected. Do not log routine work; git history already
has that. **Most runs need no entry.**

Entry format (inside your `<issue>-<slug>.md` file):

```
# <date> · issue #<N> · <one-line topic>

<what you learned, why it matters, what to do / avoid>
```

If a repo has a legacy single-file `docs/agent-journal.md`, it is frozen
history — read it, never edit it.
