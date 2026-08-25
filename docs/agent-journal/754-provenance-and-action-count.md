# 2026-08-25 · issue #754 · provenance validate-path gap; action-count/tax fully reconciled on main; three stale copies live only on unmerged branches

**Action count: settled.** `RESULTS.md` and `COMPARISON_TABLES.md` on `main` already
agree at **8,157** (fixed by `f8f6b47`, ahead of this issue). Independently re-verified
two ways: summing `consolidated_replay_702ccd4f.json`'s 25 per-game `sent` values by
hand gives 8,157, and summing `COMPARISON_TABLES.md` Table 1's `ASSAY total` column
(and its probe sub-column) also gives 8,157 / 656 — both match the server's own
`TOTAL ACTIONS 8,157` on card `702ccd4f`. The `~7,800` in `RESULTS.md` was a stale
rounded figure that predated the final tally and was never reconciled; 8,157 is right
because three independent sources agree (journals, per-game table, server card), not
by preference. Exploration tax 656/8,157 = 8.04% → **8.0%**, consistent everywhere in
this repo. Also fixed a leftover in `SCORECARDS.md` (line 26 still said "8,156" three
paragraphs above the sentence explaining the correction to 8,157) and a real math
bug in `COMPARISON_TABLES.md` (prose called Prime Agent's 95.24 a "marginal clear" of
the 95.4 human-expert baseline — 95.24 < 95.4, so it doesn't clear it at all; this
exact bug was already flagged, unfixed, in the agent/issue-740 journal entry
`740-preprint-sourcing.md`).

**The "7.8% vs 8.0%" split is real but not where #754 pointed.** No file in this
repo's current tree or history has ever said 7.8% *and* been the corrected artifact —
`COMPARISON_TABLES.md` itself briefly said 7.8% (probe miscounted as 636) and was
self-corrected to 656/8.0% in `0428437`, before any of the downstream docs existed.
7.8% survives today only in **`docs/STRATEGY-2026-08-23.md`** (three occurrences),
which was committed to this repo on `agent/issue-740` (`d34896b`) then correctly
removed to the archive per Mohsen's decision (`b5e101c`, "belongs in
workspace/assay-archive, not here"). This agent's worktree cannot read
`~/workspace/assay-archive` (sandboxed to the repo worktree), so **whoever edits the
archived strategy doc next should fix its three "7.8%" mentions to 8.0%** — they were
never corrected when the file left this repo.

**Cross-branch fragmentation — read carefully before trusting `main`'s state.**
`agent/issue-754` forked from `main` at `871059c`, which has never had the preprint or
funding drafts merged in. Both exist only on sibling branches that are not ancestors
of `main`:
- `agent/issue-740`: `docs/PREPRINT-DRAFT-2026-08-24.md` — already uses 8.0% correctly
  and already flags the 7.8%-doesn't-reproduce and Prime-Agent-95.24 issues inline
  (see its own `740-preprint-sourcing.md`). Nothing to fix there.
- `agent/issue-742`: `docs/funding/{anthropic-era,emergent-ventures,laude-slingshots}.md`
  — cite **8,156** actions (correct at the time they were written, now one shy of the
  server-confirmed 8,157). The 8.0% tax figure in these files is still fine (656/8,156
  also rounds to 8.0%); only the raw total needs the one-digit bump when this branch
  eventually merges.
Neither branch was in scope for this worktree ("work only in this worktree, do not
touch other branches"), so these two stale digits are flagged, not fixed. Whoever
merges `agent/issue-742` should grep it for `8,156` first.

**Provenance upgrade (item 1): genuinely incomplete, and the blocker is structural.**
"ARC's own scoring returns 96.54" is currently established by reading the live card's
rendered SCORE/per-game rows and comparing by eye to `rhae.py`'s offline output — not
by running the raw scorecard JSON through `rhae.py --validate` (that function expects
a directory of `scorecard.json` files; none has ever been committed to this repo, and
no run journals live in this worktree either — `bench/` here is checked-in prose
records, not the actual multi-hour game sessions). Closing this needs
`GET https://three.arcprize.org/api/scorecard/<card_id>` with an `X-API-Key` header
(confirmed via `docs.arcprize.org`'s API reference) — `ARC_API_KEY` is not available
in an agentd worktree by design (`.claude/settings.json` denies reading `.env*`, and
this Bash sandbox blocks shell variable expansion outright), so this is the same class
of gap as the "needs the attended browser lane" carve-out in `FLEET-RULES.md`, just for
API credentials instead of a browser. Full instructions for whoever has the key are in
`SCORECARDS.md`'s new "Validate-path status" note.
