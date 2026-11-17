# kernel-takehome

Anthropic's performance take-home as an ASSAY world. The agent edits
`work/perf_takehome.py`, whose `KernelBuilder.build_kernel` emits the
instructions of a kernel for a simulated machine, and tries to make the
kernel use fewer clock cycles. The unmodified kernel takes 147734. Lower is
better and the goal is open-ended: the run ends at its caps (5000 paid
actions, or whatever the operator sets), never at a WIN.

```
WORLD CARD    kernel-takehome                code M-E-C-0   family Solve   fit yes   clock s
acts          this machine: a copy of the take-home in work/   repo archetype, worktree mode, not replayable
answers       exactly: the cycle count, or the stage that failed   states: cycles, best_cycles, valid, gain
done when     never: no WIN; the run ends at its caps          the frontier record is the result
worst case    nothing: a copy of the take-home in work/        no flags; EVALUATE is paid
proxy         none needed                                      the take-home is rebuilt by prepare.py
breaks first  layer 4: a long search that stalls on one plateau and keeps varying one idea
```

## Prepare it once

The take-home's terms forbid redistributing solutions and it has no licence
file, so nothing from it, and nothing from the evaluator's repository, is in
this pack. An operator fetches both once, from this directory:

```bash
python3 prepare.py
```

That clones the take-home (`anthropics/original_performance_takehome`) and
RSI-top's wrapper (`RSI-top/rsi-top-wrapper`, MIT) into `upstream/` at the
commits `kernel.json` pins, checks that the wrapper's vendored `problem.py`
and `frozen_problem.py` are byte-identical to the take-home's `problem.py` and
`tests/frozen_problem.py` (and refuses otherwise), builds `task/`, the tree
the agent gets (`perf_takehome.py`, `problem.py`, `Readme.md`, `tests/`), and
writes `upstream/manifest.json` with the commits and the hash of every file
in `task/`. A second run changes nothing. To copy from local clones without
the network: `python3 prepare.py --takehome DIR --wrapper DIR`, which still
checks the pinned commits (`--allow-unpinned` skips that). `upstream/` and
`task/` are ignored by git.

## Run it

From an operator's shell, in a fresh directory:

```bash
assay start kernel1 \
    --adapter <repo>/worlds/recipes/kernel-takehome/adapter.py:factory \
    --registry <repo>/worlds/recipes/kernel-takehome/registry.json \
    --owner-token-file <tokens>/kernel1.token
assay hooks install --policy <tokens>/kernel1.hooks.json --deny 'eval_build' --deny 'eval_score' \
    --deny 'submission_tests' --deny 'world_state' --deny 'worlds/recipes'
```

Or let `/assay:attach` start it, which installs the pack's deny patterns
from `pack.json`. The take-home is copied into `work/`. `EVALUATE
label=<name>` builds the kernel from `work/perf_takehome.py` as it stands and
scores it. A label with spaces goes through `--params '{"label": "..."}'`,
because `pname=value` is split at spaces.

An evaluation is the wrapper's evaluator, the one RSI-top's runs use, so
every arm of a comparison is scored the same way. Phase one, `eval_build.py`,
imports the candidate with the take-home on the path and dumps the
instructions as JSON (timeout 180 s). Phase two, `eval_score.py`, which
never imports the candidate, runs the frozen simulator on seeds 1000 and 1001
and needs both trials correct (timeout 600 s). The cycle count is the larger
of the two. Both phases run in a temporary directory, in their own process
group that is killed on a timeout, with an environment cut down to PATH, HOME,
LANG and TMPDIR.

## What an evaluation costs

On the unmodified kernel an evaluation takes about two and a half seconds on
a laptop (measured once), and the clean re-score about five. The timeouts
bound a runaway candidate. The budget in `registry.json` is 5000 actions.

## The observation and the frontier record

Every key is present from the first observation, so predictions can name
them: `evaluations` (completed evaluations), `valid` (the last one, null
before any), `cycles` (the last one's), `best_cycles` (the frontier, starting
at 147734), `improved` (the last beat the frontier), `gain` (the frontier
before minus the last one's cycles, null when invalid or none),
`failure_stage` (null, or `build_timeout`, `build_error`,
`simulation_timeout`, `simulator_error`, `incorrect_output`),
`baseline_cycles`, `rules_clean` and `code_sha256`.

`rules_clean` is false when `work/tests/` or `work/problem.py` differs from
`task/` by sha256. EVALUATE then refuses and records nothing; restore the
files and evaluate again. A failed evaluation is a completed one: status ok,
`valid` false, the frontier unchanged.

Each completed evaluation appends a line to
`.assay/world_state/frontier.jsonl` in the run directory: `n`, `started_at`,
`at` (UTC, milliseconds), `duration_seconds`, `label`, `code_sha256`,
`valid`, `cycles`, `failure_stage`, `error` (at most 300 characters),
`frontier_before`, `improved`, `best_cycles` (the frontier after) and
`trials`. Beside it, `candidates/<n>.py` is the file that evaluation
scored and `best.py` the best valid one. The observation is derived from the
record, so it reads the same after the daemon restarts.

## Score a final file cleanly

```bash
python3 <repo>/worlds/recipes/kernel-takehome/checks/score.py CANDIDATE.py [--json]
```

The checker builds a fresh copy of the pinned take-home in a temporary
directory, places only the candidate as `perf_takehome.py`, scores it with
the same two-phase evaluator, then runs the official `tests/submission_tests.py`
and reports which thresholds passed. It prints cycles, `normalized` (1363
divided by cycles) and the rules: `tests/` unchanged, `problem.py` unchanged,
`N_CORES` is 1. The exit status is 0 when the candidate is valid and the rules
hold, 1 when not, 2 for a setup error.

## Licence and safety

Solutions to the take-home are not to be published, and this pack publishes
none. It vendors nothing from either upstream repository: `prepare.py`
fetches them on the operator's machine.

The build phase runs code the agent wrote, as the daemon's user. The stripped
environment keeps the daemon's secrets out of that process, but it is not a
sandbox: run the daemon in a container or a throwaway account when that
matters.
