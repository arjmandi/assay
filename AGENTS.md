# AGENTS.md: for coding agents working on the harness source

You are maintaining the harness, not playing a world. `CONSTITUTION.md` is the
manual an agent under evaluation reads when driving a registered world. It is
not for you. The user-facing story is `ONBOARDING.md`.

## Run the tests

From the repository root, in an environment with numpy, pillow and pytest:

```bash
uv run --with pytest --with numpy --with pillow --with hypothesis pytest tests/
```

or `python3 -m venv .venv && .venv/bin/pip install -e '.[grid,dev]'` and then
`.venv/bin/pytest tests/`. The suite drives the real CLI and the real daemon
with the adapters under `tests/`. It redirects `ASSAY_ANCHOR_DIR` and
`XDG_CACHE_HOME` under the pytest temp root and stops every daemon it started
when the session ends, so nothing lands under the home directory. Run the whole
suite before calling a task done.

## Where the contracts are

`docs/ARCHITECTURE.md` is the component model and the single source: one
section per component, the frame-world extra, the conformance table, and the
list of what never changes. The public journal contract lives in `verify/`:
`JOURNAL_SPEC.md` (`assay-journal-v1`) and `CLAIM_GRAMMAR.md`, beside the
independent checker. The kernel must keep honoring both.

## What never changes

Everything in `docs/ARCHITECTURE.md` section 5 and in the docstring of
`src/assay/words.py`: journal field names, state values, claim syntax, grade
`actual` texts, host channel names, the `game_id` keys, activity kinds, receipt
outcome tokens, the state-directory layout. `tests/test_conformance.py` and
`tests/test_vocabulary.py` enforce it. The hash chain in `integrity.py` is the
product's trust claim: a change there needs the full suite, not a spot check.

## The kernel's laws

The kernel makes no LLM calls. It imports nothing from `bench/` or
`assay_grid` at module level and names no world. Frame-world behavior lives in
`src/assay_grid/` behind `assay.extras`, selected by observation shape. World
policy lives in the adapter. Module code never bans, it demands structure.

## Add a world

Copy `examples/new_world/` (the adapter and registry contracts are in
`docs/ARCHITECTURE.md` sections 2.1 and 2.2 and in the template's README),
add the adapter to the `ADAPTERS` table in `tests/test_conformance.py`, and
run the template test against it.

## Never launch

`bin/assay` against a benchmark, or anything under `bench/`, in a code task.
Those start long, graded sessions, some against paid APIs. Testing the harness
means running the test suite, not a live session.

## Commits

Plain messages: what changed and why, the tests run, no decoration.
