# ASSAY

A **referee harness** that sits between an agent (human or LLM) and a world you
register: enforced predict-before-act, code-graded claims, hash-chained
journals, and a memory/agency layer. See `README.md` for the pitch, `GUIDE.md`
for the user guide, `CONSTITUTION.md` for the agent-facing manual an *evaluated*
agent follows when driving a registry-mode run — do not confuse that manual
with these instructions, which are for you working ON the harness's own code.
Status: v1-rc1, private, no license yet — currently zero contract customers,
this is Mohsen's own research project.

## Commands

```sh
uv run --with pytest --with numpy --with pillow pytest tests/      # full suite
uv run --with pytest --with numpy --with pillow pytest tests/test_registry.py  # one file
```

No `pyproject.toml` — the CLI (`src/assay_cli.py`) declares its own deps via a
PEP 723 inline block; `bin/assay` resolves a matching Python (3.12+) or falls
back to `uv run --script`. No configured linter; do not add one unasked.

Run the full suite before considering any task done. If it fails for reasons
unrelated to your change, say so rather than fixing unrelated breakage.

## Layout

```
src/assay/           the harness: broker.py (run loop), registry.py, verifiers.py,
                      predictions.py, channels.py, integrity.py (hash chain,
                      redaction), rules.py, modules.py, agenda.py (goal/proposal lane)
src/assay_cli.py      PEP 723 entry point — declares numpy/pillow deps inline
bin/assay             the launcher script (see README quickstart)
examples/             counter_world.py + example_registry.json — the toy adapter
tests/                pytest; e2e tests drive the real CLI + broker subprocess
                      via fake_adapter.py, not mocks
bench/{arcagi,factorio,oolong}/   benchmark harnesses + real run results —
                      long, expensive game-playing sessions, not CI
```

## Conventions

- Python 3.12+, stdlib-first — the only third-party deps anywhere are numpy
  and pillow, declared inline in `assay_cli.py`, not in a requirements file.
- No formatter/linter is enforced; match the surrounding style exactly.
- Tests: pytest, `tests/test_*.py`. Unit tests import `assay` directly; e2e
  tests spawn the real CLI + broker as a subprocess — genuinely integration,
  not mocked.

## Constraints that are not obvious from the code

- **Never run `bin/assay` or any benchmark under `bench/`.** These start real,
  long-running graded sessions (some against paid game APIs per
  `bench/{arcagi,factorio,oolong}`) — they are Mohsen's research runs, not
  something a code-change task should trigger. Denied in `.claude/settings.json`.
  Testing the harness means running the test suite, not a live session.
- **`CONSTITUTION.md` is not for you.** It is the manual an agent under evaluation
  reads when *using* ASSAY to play a registered world. You are maintaining the
  harness's source, not operating inside one of its runs — don't let its
  "never inspect the environment's source" framing bleed into how you work here.
- **The hash chain in `integrity.py` is the product's core trust claim** — a
  bug there is not an ordinary bug. Any change to chaining/redaction/anchor
  logic needs the full test suite green, not a spot check.
- **`bench/*/RESULTS.md` and recorded run data are real, already-verified
  results** (e.g. "24 games, 23 wins, server-verified") — historical record,
  not something to regenerate or edit to match new code.
- This repo currently shows as **private** on GitHub despite being described
  as the public/open-source side of the project — don't assume public
  visibility or a license in anything you draft; ask/flag instead of guessing.

## Working under agentd

You may be running unattended, triggered by a board move or an issue comment.

- **Read `.claude/FLEET-RULES.md` first.** It carries the rules that apply to
  every route: never chain a probe in front of the command you need, scratch
  files go in `.scratch/` and never `/tmp`, and the browser (where equipped)
  is read-and-verify only. The daemon refreshes it from pado on every run.
- You are in a **git worktree** on branch `agent/issue-N`. Stay in it.
- **Commit** your work in logical units, referencing the issue number.
- **Do not push and do not open a PR.** The daemon does both; your `git push`
  is denied by `.claude/settings.json` on purpose.
- **Journal entries are per-issue files** — a NEW
  `docs/agent-journal/<issue>-<slug>.md`, only if this run taught you
  something a future agent would otherwise rediscover the hard way. Most runs
  need none.
- End your final message with a 3–6 line summary suitable for a GitHub
  comment, then `STATUS: DONE` or `STATUS: BLOCKED` with the reason. The
  daemon parses those tokens, so the spelling matters.
- **Don't close the issue or merge a PR on your own judgment.** `STATUS: DONE`
  means "ready for review". The one exception is an explicit instruction in
  the thread ("close it", "merge it") — the human's decision already made.
- **Board writes.** You may comment on the `arjmandi/me` board, but every
  comment must START with the literal marker `<!-- agentd -->`; an unmarked
  comment reads as an instruction from Mohsen and re-triggers an agent. The
  daemon posts your headless final summary automatically. Link the URL of
  every comment you post in your summary.
- **This route has no headless browser.** Anything browser-bound goes as far
  as files allow, then `STATUS: BLOCKED` naming "needs the attended browser
  lane" (`agentd.py --session assay <issue>`).
- If the task is underspecified, prefer `STATUS: BLOCKED` with a specific
  question over guessing.
