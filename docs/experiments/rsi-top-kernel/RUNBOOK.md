# Runbook: ASSAY against RSI-top on the kernel take-home

The brief is `../rsi-top-kernel.md`; the facts it asked to verify are in `CHECKS.md`. This
file is the order of work on the machine that runs the experiment. Every step is a command
the operator types: nothing starts by itself, and every run costs money.

## What is where

| Piece | Place |
|---|---|
| The world pack (the task as an ASSAY world, the evaluator, the frontier record) | `worlds/recipes/kernel-takehome/` |
| The clean re-score | `worlds/recipes/kernel-takehome/checks/score.py` |
| The frontier reader, the stall rule, the report numbers | `tools/eval/frontier.py` (`python3 -m tools.eval frontier`) |
| The analyst and its prompt | `tools/eval/analyst.py`, `tools/eval/prompts/analyst.md` |
| The runner's hook (`--stall`, `--analyst`) | `tools/eval/session.py` |
| The player's prompt | `player.md` here, written to each run as `PLAYER.md` |
| The arms, the caps, the model ids | `arms.env` here (from `arms.env.example`) |
| One command per step | `kernel_exp.sh` here |
| Run data and results | `$KERNEL_EXP`, outside the repository (default `~/assay/experiments/rsi-top-kernel`) |

A run directory is `$KERNEL_EXP/runs/<arm>-s<seed>`. Beside it, `<arm>-s<seed>.sessions/`
holds the runner's state: `sessions.json` (one record per session, with dollars and tokens),
`stall.json` (the trigger's state and every firing), `analyst/a<k>.*` (each analyst call: its
input, prompt, output, parsed directions, cost), and the prompts each session was given. The
frontier record is `<run>/.assay/world_state/frontier.jsonl`, with every evaluated program in
`candidates/` and the best in `best.py` beside it.

## The version

Every ASSAY arm runs on ASSAY 1.5.1: the tag `v1.5.1`. The kernel (`src/`), the hooks, the
launcher, `CONSTITUTION.md` (the manual the player reads) and the world engine are that tag's,
byte for byte, and the experiment's additions (the pack, `tools/eval/`'s stall trigger and
analyst, this kit) sit on top without touching them. `kernel_exp.sh` checks this before
`setup`, `smoke`, `host-test`, `assay` and `resume`, and refuses a checkout that says another
version or whose files differ from the tag. Other lines of ASSAY (later fixes, experiment
branches, other checkouts) are not used. Each run's `.assay/config.json` records
`harness_version`; the report quotes it.

## 0. The machine

Linux, CPU only. Python 3.12 or later for ASSAY (`uv` provides it) and 3.11 for the wrapper,
`uv`, `git`, Node.js 22.19 or later with npm (the wrapper's setup asks for it even though the
baselines do not call Pi), and OpenCode for A1 and A2. Export the keys in the shell that runs
the steps: `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY` for the two ShinkaEvolve
baselines, and for OpenCode the names its providers read (`GOOGLE_GENERATIVE_AI_API_KEY` for
Google; check `opencode auth list`).

```bash
cp arms.env.example arms.env
```

```bash
./kernel_exp.sh setup
```

Setup clones the task and the wrapper at their pinned commits into the pack's `upstream/`
(ignored by git), checks that the wrapper's frozen simulator is byte-identical to the task's,
builds the tree the player gets, and installs the wrapper's environment.

## 1. Checks

Done: `CHECKS.md`. Its last section holds one decision taken (Gemini 3.7 Flash for the G arms
and A1, with the id and the wrapper's price constants to fill in on this machine before the
pilot) and one still open (the branch-point design).

## 2. Build, and the two tests that need this machine

The suite, from the repository's root:

```bash
uv run --with pytest --with numpy --with pillow --with hypothesis pytest tests/
```

The pack's smoke test: one evaluation of the untouched kernel. It must print a RESULT line,
`cycles` 147734 and `CLEAN`:

```bash
./kernel_exp.sh smoke
```

The OpenCode host test, once per model. It plays the counter world to a WIN and costs cents:

```bash
./kernel_exp.sh host-test "$OPENCODE_GEMINI_MODEL"
```

```bash
./kernel_exp.sh host-test "$OPENCODE_DEEPSEEK_MODEL"
```

Read `sessions.json` in the state directory the command names, and settle four things that
could not be settled without OpenCode installed:

1. The outcome is WIN with each model.
2. The record's `dollars` and `tokens` are filled in. The runner's reader assumes OpenCode's
   JSON lines carry `cost` and `tokens` on their step-finish parts. If they are empty, fix the
   reader against `s1.player.out` before anything else: without it `--max-usd` does not bind
   for A1 and A2, and the budget cap of the study is not applied.
3. How OpenCode sets high reasoning for each model (a flag on `opencode run`, or a setting in
   `opencode.json`), so the player and the analyst run as the study's models did. Record the
   setting in `arms.env` beside the model ids.
4. That `--auto` let the player call the launcher and edit files without a prompt.

Then mark the host tested the way the Codex host records its test: `tested=True` and the
comment in `HOSTS` in `tools/eval/session.py`, the sentence in its module docstring, and the
row in `tools/eval/README.md`, each with the date, the OpenCode version and the two models.

## 3. Smoke test: 30 minutes of each arm

Each arm must start, evaluate, log and stop at its cap. The third argument is the hours; use
seed 0 so the directories do not collide with the grid.

```bash
./kernel_exp.sh assay A1 0 0.5
```

```bash
./kernel_exp.sh assay A2 0 0.5
```

```bash
./kernel_exp.sh baseline OE-G 0 0.5
```

and the same for OE-D, SH-G and SH-D. For the ASSAY arms check: the OUTCOME line says
TIME_EXHAUSTED, `frontier.jsonl` has lines, and `./kernel_exp.sh frontier A1 0` prints them.
The trigger cannot fire in 30 minutes (its first hour). To see a firing and an analyst call
end to end before the long runs, run the runner once by hand on a smoke run with small
thresholds: `--stall-min-age-minutes 5 --stall-flat-minutes 5 --stall-min-attempts 3`.

## 4. Pilot: one hour of OE-G and of A1

```bash
./kernel_exp.sh baseline OE-G 1 1
```

```bash
./kernel_exp.sh assay A1 1 1
```

Measure evaluations per hour, tokens and dollars per hour, and paused time (`frontier` prints
the first and the last for ASSAY; the baseline's are in its `search/cost_ledger.json` and
`search/attempts.jsonl`). Extrapolate the grid, 12 runs of 12 hours, and report the estimate
to the owner. Stop here until the owner says go.

## 5. Calibration

```bash
./kernel_exp.sh baseline OE-G 42
```

Re-score its best program (step 7). Near 1244: the setup matches the study's. Far off: stop
and report, because the published numbers are then not a reference.

## 6. Main runs

| Arm | Seeds | Command |
|---|---|---|
| OE-G | 43, 44 | `./kernel_exp.sh baseline OE-G <seed>` |
| OE-D, SH-G, SH-D | 42 | `./kernel_exp.sh baseline <arm> 42` |
| A1 | 42, 43, 44 | `./kernel_exp.sh assay A1 <seed>` |
| A2 | 42, 43, 44 | `./kernel_exp.sh assay A2 <seed>` |
| A3, optional | 42 | `./kernel_exp.sh assay A3 42` |

Runs are independent and CPU bound; an evaluation of a fast kernel takes seconds and of the
untouched one about a minute. Run as many side by side as the machine has cores to spare, and
keep one core per evaluation in flight (a baseline evaluates up to four at once).

Things to know while a run is going:

- The kernel world keeps its tree outside the journal, so it has no replay: a run lives as
  long as its daemon. Do not run `assay stop` on a live run and do not reboot the machine. If
  the daemon is lost, the frontier record and the best program are still on disk, the run is
  reported as cut short at that time, and it is not resumed.
- `resume` continues a run whose runner ended (STOPPED, OWNER_NEEDED, SELF_STOP,
  PLAYER_FAILING) while the daemon is up. The trigger's state continues from `stall.json`.
  The hours given to `resume` are a new allowance: pass what is left of the 12.
- To end a run early, create `STOP` in its state directory.
- A usage-limit pause is recorded, is not counted as a session, and is not active time for
  the trigger.
- OpenCode gets no hooks and no Stop gate, so nothing but the prompt keeps the player from
  running the evaluator by hand. The score cannot be changed that way (the world scores a
  copy on the frozen simulator), but evaluations made outside `assay act` would be missing
  from the frontier record. The clean re-score and a read of `s<n>.player.out` cover this.
- Before A3: confirm on Anthropic's support pages whether `claude -p` draws on the Team plan
  or on a separate credit (brief, section 7).

## 7. Clean re-score

For every run, the best program goes through the re-score, and only that number is reported:

```bash
./kernel_exp.sh rescore "$KERNEL_EXP/runs/A1-s42/.assay/world_state/best.py"
```

```bash
./kernel_exp.sh rescore "$KERNEL_EXP/baselines/OE-G-s42/search/best/best_program.py"
```

It scores the program in a fresh copy of the pinned task with the wrapper's evaluator, runs
the task's own `tests/submission_tests.py`, and checks that `tests/` and `problem.py` are
unchanged and the core count is 1. A result under 1300 cycles is suspect until this passes.

## 8. The report

Per ASSAY run, `./kernel_exp.sh frontier <arm> <seed>` gives the evaluations, the best
cycles, the normalized performance (1363 divided by cycles), the stalled fraction by the
page's definition with paused time taken out, and each analyst call with whether the
frontier moved within the next 20 active minutes. Dollars and tokens per model are in
`sessions.json` (the player) and `analyst/a<k>.json` (the analyst). For the baselines the
same numbers come from `search/attempts.jsonl` and `search/cost_ledger.json`; the curve and
the stalled fraction are computed from their evaluation events with the same functions.

The breaking-points note for each ASSAY run is written from `stall.json` and `analyst/`: for
each firing, the evaluation it fired on, what the diagnosis in `a<k>.input.json` said, the
directions in `a<k>.json`, and the labels and scores of the evaluations that followed.

Results, curves and notes stay in `$KERNEL_EXP` until the owner decides where they go.
Programs are not published: the task's terms forbid redistributing solutions.

## Known differences from the study, to state in the write-up

- ASSAY plays one player at a time; the frameworks evaluate four candidates at once with
  bursts of 32. Report evaluations per run beside cycles.
- The published RSI-top runs branch from a baseline at its first stall; an ASSAY run starts
  from the untouched kernel.
- Active time for a single player is session time, not the union of attempts in flight.
- The analyst is given, beside the diagnosis, the world model and the frontier record that
  the brief names, the text of three files as they stand at the call: the task's readme, the
  best program so far and the program the player is working on (`--analyst-context`). Without
  them it would propose changes to code it never saw. Drop the three flags in
  `kernel_exp.sh` to run the brief's narrower reading.
- One analyst call with at most five directions, against four planner calls with eight each
  and a deduplication call.
- The wrapper's Gemini price constants, set to Gemini 3.7 Flash's list prices: the one edit
  made to the wrapper.
