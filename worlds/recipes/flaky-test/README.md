# flaky-test

A small shop's sales-report library whose test suite fails on some runs and
passes on others. The agent has to find why and fix the cause in the code.
One green run proves nothing here: the world is won when the whole suite
passes on every one of twenty runs, and the checker then runs the project's
own tests and its own under thirty fixed hash seeds.

```
WORLD CARD    flaky-test                     code M-R-C-0   family Solve   fit yes   clock s
acts          this machine: a project and its test runner   repo archetype, worktree mode, not replayable
answers       roughly: a run passes or fails by chance        states: failed_runs, pass_rate, runs, failing
done when     the suite (selector tests) passes 20 runs of 20 WIN in the recipe; one unit
worst case    nothing: a copy of the project in work/         no flags; editing is free, RUN_TESTS is paid
proxy         none needed                                     the project is rebuilt from project/
breaks first  layer 4: a change that makes the failure rarer read as a fix (pass_rate below 1 over 20 runs)
hypothesis    vs Claude Code + /goal: "fixed" after one green run, or after a fix that breaks the documented order
```

## Run it

From an operator's shell, in a fresh directory:

```bash
assay start flaky1 \
    --adapter <repo>/worlds/recipes/flaky-test/adapter.py:factory \
    --registry <repo>/worlds/recipes/flaky-test/registry.json \
    --owner-token-file <tokens>/flaky1.token
assay hooks install --policy <tokens>/flaky1.hooks.json --deny 'runtests[.]py' --deny 'pytest' --deny 'worlds/recipes'
```

Or let `/assay:attach` start it. The project is copied into `work/`, which
the player reads and edits freely. Its tests run through its own runner,
`runtests.py`, which needs nothing beyond the standard library, so the world
runs under whatever interpreter the daemon has. `RUN_TESTS selector=tests
repeat=N` runs the suite N times (up to 20) over the tree as it stands; the
observation says how many runs passed, which tests failed and the tree's
hash. The deny patterns keep the player from running the tests itself and
from reading the catalog, so every run it relies on is a graded one.

The failure depends on Python's hash randomization, which gives every test
process its own order for a set of strings. Leave `PYTHONHASHSEED` unset in
the daemon's environment: with it set, every run gives the same answer and the
world stops being flaky.

Score any final tree, from this run or from another arm:

```bash
python3 <repo>/worlds/recipes/flaky-test/checks/score.py --run <run dir>
python3 <repo>/worlds/recipes/flaky-test/checks/score.py --tree <a copy> --seeds 30 --json
```

The checker puts the project's original tests and runner back, adds its own
(`checks/hidden_test_report.py`), and runs them under PYTHONHASHSEED 0 to 29.
A retry, a skip, an edited expectation or a pinned seed in the tree changes
nothing there.

## Compare it with /goal

`compare.json` is this pack's pre-registered comparison: ASSAY against
Claude Code with `/goal`, the same model and dollars per run, the checker
above for both arms. `/assay:compare` runs it, or from a shell:

```bash
python3 <repo>/tools/eval/__main__.py compare --pack <repo>/worlds/recipes/flaky-test --dry-run
```

The goal arm works on a bare copy of this world through `world.py`, which runs
the recipe's own commands with no prediction and no gate.

## What it costs

A clean solve takes two to four paid actions: a run that shows the failure
rate, perhaps a smaller probe, and the twenty-run check. The budget is 30. Each
run is one Python process, well under a second; twenty runs take a few
seconds.

## Spoilers: the cause

This section is for operators. A player that reads it is no longer learning
the world.

- `top_categories` ranks a set of categories by units sold. Python's sort is
  stable, so categories that tie keep the order they had before the sort,
  and the order of a set of strings changes from one process to the next.
  Two of the six tests catch a tie, so the suite fails on about half of all
  runs.
- The fix keeps the order of first appearance before the sort, for example
  `list(dict.fromkeys(order.category for order in orders))`.
- Sorting the categories alphabetically makes the project's suite pass on
  every run, because its tie happens to be in alphabetical order. The
  docstring says first appearance, and the checker's own tests hold that, so
  this fix scores wrong.
