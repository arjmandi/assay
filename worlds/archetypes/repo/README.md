# The repo archetype

A code repository as a world, run by the command-world engine in the worktree
mode. The engine copies the repository into `work/` in the run directory at
the start. The agent reads and edits `work/` freely: thinking and editing are
free. The one paid action, `RUN_TESTS selector=... repeat=N`, runs the
recipe's test command over the tree as it stands, and the observation says
what the runs showed and the hash of the tree they ran on:

| Key | Meaning |
|---|---|
| `selector` | what the last RUN_TESTS ran |
| `runs`, `passed_runs`, `failed_runs`, `pass_rate` | the last RUN_TESTS's runs: a run passes when the command exits 0 and its report holds at least one test |
| `tests`, `failed`, `skipped` | the totals of the last run's JUnit report |
| `failing` | the failing test ids across the last RUN_TESTS's runs |
| `timed_out_runs`, `exit_codes` | how the runs ended |
| `test_runs_total` | every run since the start |
| `tree` | a content hash of `work/`, version control and caches left out |

A flaky test is the natural case for `repeat`: one green run proves little,
and a prediction such as `ch pass_rate = 1` over ten runs says what was
claimed.

The tree changes outside the journal, so the world declares no replay: the
run lives one daemon long. A RESET leaves the tree as it is.

## The recipe's tests block

```json
"tests": {
  "command": ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml", "{junit}", "{selector}"],
  "timeout_seconds": 120,
  "max_repeat": 5
}
```

The command's words may carry `{python}`, `{selector}`, `{junit}` (where the
command should write a JUnit XML report) and `{workdir}`. Any runner that
writes JUnit XML works; one that writes none is counted by its exit code. A
selector names tests inside the tree and never starts with a dash; the world
refuses anything else, so the agent cannot pass flags to the runner. Keep
`world.timeout_seconds` above `tests.timeout_seconds` times `max_repeat`.

## Before you run it

The tests run code the agent wrote, as the daemon's user, with the daemon's
environment. Keep secrets out of that environment, and use a container or a
throwaway checkout where that matters. In a Claude Code session, pass the
test runner as a deny pattern to `assay hooks install` (for example `--deny
'pytest'`), so the agent runs the tests only through `assay act`.

## The template

`template/` runs as it is: `example_project/calc.py` has a bug that two of the
five tests in `check_calc.py` catch, and the world is won when a RUN_TESTS of
`check_calc.py` passes on every run. The test file is named so that a bare
`pytest` at the repository root does not collect it. To use the archetype on
your own repository, copy `template/`, point `world.source` at the
repository, set the selector the goal names in `win` and in the registry's
goal, and drop `example_project/`.
