# Experiment: ASSAY against RSI-top on the kernel take-home

This brief is for the agent that builds and runs the experiment. It is self-contained: every
path is relative to the ASSAY repository (https://github.com/arjmandi/assay), and every
outside source is a URL. Read it whole before you start. Where it says "verify", the fact
comes from a project page or a README and has not been checked against code.

## 1. Purpose

Find out whether ASSAY, running unattended for 12 hours, can escape the stalls that long
autoresearch runs fall into, and how far it climbs compared with RSI-top, a published method
built for exactly that problem. This is the first time ASSAY runs in this kind of setup.

## 2. Background: RSI-top

- Project page: https://rsi-top.github.io/
- Code: https://github.com/RSI-top/rsi-top-wrapper
- Authors: Mao, Kwok, Talaei, Kozyrakis, Mirhoseini (Stanford).

RSI-top studies "exploration collapse": a long search keeps refining one narrow direction
while the best score stops moving. In their runs the frontier was stalled 84 to 92 percent of
the time after the early gains. RSI-top wraps an existing search framework (OpenEvolve or
ShinkaEvolve) and leaves its selection and mutation rules unchanged. It:

1. computes 25 fixed task-specific metrics over the run in the background;
2. detects a frontier stall;
3. calls a cohort of planners (four calls, each proposing eight directions, then semantic
   deduplication) that read the metrics and pull only the trajectories they need;
4. hands the directions back to the native search, which executes every retained one.

No harness edits itself. The improvement is in the solution, and the wrapper steers the
search when it stalls.

Their kernel-task configuration, from the wrapper README (verify against the code):

- Planner and deduplication model: DeepSeek v4 Flash, high reasoning, run through the Pi
  0.84.1 tool-calling runtime.
- Search models: Gemini 3.7 Flash and DeepSeek v4 Flash, in OpenEvolve and in ShinkaEvolve:
  four framework-model pairs.
- Stall trigger: the first intervention after one hour, 20 active flat minutes and 12
  completed attempts. Progress means more than 0.2 percent fewer cycles relative to the
  progress anchor. Each planner sees 15 of the 25 metrics.
- Concurrency: 4 workers, bursts of up to 32 candidate evaluations.
- Example run: `--hours 12 --budget 400 --seed 42`.
- Reference runs: GPT-5.6 Sol and Claude Opus 5 in the unmodified frameworks, capped at a
  300 dollar API budget.

Headline kernel result: OpenEvolve with Gemini 3.7 Flash stalls at 1,244 cycles; with
RSI-top it ends at 1,109. The page reports similar recovery across all four pairs, mostly as
figures.

## 3. The question and the claim

The question: on the same task, with the same models, the same budget and the same stall
rule, does ASSAY reach fewer cycles than the native frameworks, and how does it compare with
RSI-top's published result?

ASSAY runs standalone: it replaces the search framework, it does not wrap one. Its own
mechanism at a stall is a diagnosis of the run against its five reasoning layers, made by a
separate analyst model, which hands the player new directions.

The claim this experiment can support is about "ASSAY with its analyst" as one unit. It does
not separate what the predict-before-act loop adds from what the analyst adds.

## 4. The benchmark

- Task: Anthropic's performance take-home,
  https://github.com/anthropics/original_performance_takehome. The agent rewrites a kernel
  for a simulated VLIW/SIMD machine to use fewer clock cycles. Lower is better.
- Hardware: CPU only. Linux, Python 3.11, uv, and Node.js 22.19 or later for the wrapper.
- Evaluator: use the wrapper's evaluator, not only the repository's tests, so every arm is
  scored the same way. It checks `forest_height=10`, `rounds=16` and `batch_size=256` on
  seeds 1000 and 1001 (verify in the wrapper code).
- Rules from the task repository, enforced in every arm:
  - `tests/` must not change: `git diff origin/main tests/` is empty.
  - `N_CORES = 1`. Re-enabling multicore is cheating.
  - Solutions below 1,300 cycles were invalid in the past because models edited the tests,
    so treat any such result as suspect until the clean re-score in section 9 confirms it.
- Normalized performance, as the study reports it: 1363 divided by cycles, higher is better.

Reference points (cycles): 1,244 OpenEvolve with Gemini 3.7 Flash (RSI-top's baseline);
1,109 the same with RSI-top; 1,487 Claude Opus 4.5 after 11.5 hours in Anthropic's harness;
1,363 Claude Opus 4.5 in Anthropic's improved harness.

## 5. Arms and models

| Arm | What runs | Search or main model | Analyst or planner |
|---|---|---|---|
| OE-G | OpenEvolve, unmodified (the wrapper's `baseline` command) | Gemini 3.7 Flash | none (native) |
| OE-D | OpenEvolve, unmodified | DeepSeek v4 Flash | none |
| SH-G | ShinkaEvolve, unmodified | Gemini 3.7 Flash | none |
| SH-D | ShinkaEvolve, unmodified | DeepSeek v4 Flash | none |
| A1 (headline) | ASSAY, standalone | Gemini 3.7 Flash | DeepSeek v4 Flash, high reasoning |
| A2 | ASSAY, standalone | DeepSeek v4 Flash | DeepSeek v4 Flash, high reasoning |
| A3 (optional) | ASSAY, standalone | Claude Opus 5.5 | Claude Opus 5.5 |

- The analyst in A1 and A2 is DeepSeek v4 Flash because RSI-top's planner is. A stronger
  analyst would make any win attributable to the analyst model.
- A3 is set against the study's Opus 5 reference runs. It is not an exact match (Opus 5.5
  against Opus 5); if Opus 5 is selectable, use it.
- RSI-top itself is not run. It is compared through its published numbers.
- Baselines run with their own native configuration as the wrapper sets it. ShinkaEvolve's
  original profile uses OpenRouter embeddings; keep them if an OpenRouter key is available,
  otherwise turn them off and record the deviation.

## 6. Run grid

| Arm | Seeds | Runs | Purpose |
|---|---|---|---|
| OE-G | 3 | 3 | calibration against the published 1,244, and the main baseline |
| OE-D, SH-G, SH-D | 1 each | 3 | calibration of the other three pairs |
| A1 | 3 | 3 | headline |
| A2 | 3 | 3 | second model |
| A3 | 1 | 1 | optional |

Total: 12 runs of 12 hours (13 with A3), all CPU. Use seed 42 first, as the wrapper's
example does, then 43 and 44.

## 7. Budgets and caps

- Wall clock: 12 hours per run, as in the study.
- Spend: match the wrapper's `--budget 400`. Find out in the wrapper code what the number
  means (dollars, iterations or attempts) and apply the same cap to ASSAY in the same unit.
  ASSAY's session runner has `--hours` and `--max-usd`.
- Every run records: wall clock, number of evaluations, tokens and dollars per model, and any
  time paused by a provider's rate limit. Paused time is reported separately and is not
  counted as stalled time.
- Billing: A1, A2 and the baselines use paid APIs (Google and DeepSeek, or the providers the
  wrapper uses). A3 runs through `claude -p` on the owner's Claude Team plan. Before A3,
  confirm on Anthropic's support pages whether `claude -p` still draws on the plan or on a
  separate Agent SDK credit. A change to this was announced for June 15, 2026 and reported
  as paused.
- Known asymmetry: OpenEvolve and ShinkaEvolve evaluate up to 4 candidates in parallel, with
  bursts of 32. ASSAY plays one player at a time. Report evaluations per run beside cycles so
  the reader can see it.

## 8. What to build in ASSAY

Keep everything new under `tools/eval/` and `worlds/`. The kernel in `src/assay/` makes no
LLM calls and names no world; do not change that.

1. **A world pack for the kernel task.** Use `/assay:connect`, or build it by hand from
   `worlds/archetypes/local/` and `worlds/README.md`. Actions: edit the kernel program, run
   the evaluator. Outcome: the cycle count, or the evaluator's failure. The pack enforces the
   two task rules (the `tests/` diff and `N_CORES = 1`) as checks, and its goal is "fewer
   cycles than the best so far". Smoke-test it as the connect skill does.
2. **The OpenCode host, tested.** `tools/eval/session.py` has three hosts: `claude`,
   `codex` and `opencode`. Only `opencode` reaches Gemini and DeepSeek, and its template is
   marked untested. Configure OpenCode for both models, play the counter world
   (`examples/counter_world.py`) to a WIN with each, and then mark the host tested in the
   same way the Codex host records its test.
3. **A frontier logger.** It records every evaluation with a timestamp, its cycles and
   whether it beat the frontier. The stall trigger, the curves and the report read this
   record. Use the same "attempt" definition as the wrapper: one completed evaluation.
4. **The stall trigger.** RSI-top's rule, with the same parameters: no intervention before
   the first hour; then a stall is 20 active flat minutes with at least 12 completed
   attempts, where progress means more than 0.2 percent fewer cycles than the anchor. Read
   the wrapper code for what happens after the first intervention (the anchor reset and the
   cool-down) and copy it. Using the same rule means the comparison is about what each method
   does at a stall, not when it notices one.
5. **The analyst.** When the trigger fires, the runner ends the current session after its
   current action, calls the analyst, and starts the next session with the analyst's
   directions in the prompt (the runner already adds an operator's note to a relaunched
   session's prompt; extend that mechanism). The analyst reads:
   - the diagnosis of the run, `python3 -m tools.eval diagnose --run-dir DIR --json`, which
     places findings on the five layers (constitution, modules, registry and adapter,
     addressable states, model);
   - the player's world model: the states and the held and missed predictions in the
     journal;
   - the frontier record.

   It returns a short, ranked list of directions. Each says what to try, why the record
   supports it, and which layer it addresses. It writes nothing to the run. Record every
   analyst call, its inputs, its output and its cost in the session state directory.
6. **Unattended operation for 12 hours.** `python3 -m tools.eval session` already relaunches
   sessions until the run ends, the hours run out or the dollar cap is reached. Confirm that
   it survives 12 hours with the OpenCode host, that a usage-limit pause does not count as a
   session, and that the stall trigger works across session boundaries.

If the build changes anything under `src/`, the repository's definition of done in
`AGENTS.md` applies in full.

## 9. Protocol

1. **Checks.** Read the wrapper code and answer: what `--budget` means; the exact model
   identifiers and providers for Gemini 3.7 Flash and DeepSeek v4 Flash; the stall rule after
   the first intervention; the exact evaluator. Extract RSI-top's published per-pair numbers
   from the project page and its figures, and mark which are read off a figure.
2. **Build** the items in section 8, with the repository's test suite green.
3. **Smoke test.** One 30-minute run of each arm, to check that every arm starts, evaluates,
   logs, and stops at its cap.
4. **Pilot.** One 1-hour run each of OE-G and A1. Measure evaluations per hour, tokens and
   dollars per hour, and paused time. Extrapolate the cost of the full grid and report it to
   the owner before going on.
5. **Calibration.** Run OE-G with seed 42 for 12 hours. If its final cycles are near the
   published 1,244, the setup matches theirs. If they are far off, stop and report: the
   published numbers cannot be used as a reference until the difference is explained.
6. **Main runs.** The rest of the grid in section 6.
7. **Clean re-score.** For every run, take the best program, put it in a fresh clone of the
   task repository, check the two rules, and score it with the wrapper's evaluator. Only this
   number goes into the results.

## 10. What to report

For each run:

- final cycles (clean re-score) and normalized performance (1363 divided by cycles);
- the best-cycles curve over wall-clock time, all arms of one model on one chart, with
  RSI-top's published curve where it is available;
- stalled fraction: the share of time after the first hour in which the frontier did not
  move, comparable with the study's 84 to 92 percent;
- for ASSAY: the number of analyst calls, and for each one whether the frontier moved within
  the next 20 minutes;
- evaluations, tokens and dollars per model, and paused time;
- any rule violation found by the clean re-score.

Across runs: the mean and standard deviation where there are three seeds, and a table beside
RSI-top's published numbers, with each published number marked as read from text or read
from a figure.

Also write a one-page note on each ASSAY run's breaking points: where it stalled, what the
diagnosis said, what the analyst proposed, and what happened next.

## 11. Repository constraints for the builder

- Do not run `bin/assay` against anything under `bench/`, and do not start a bench run.
  This experiment is new work; keep its pack under `worlds/` or outside the repository.
- `CONSTITUTION.md` is the manual the player reads. Change it only if the experiment needs a
  change in what the player sees, and say so.
- Keep run data and results outside the public repository until the owner decides where they
  go. `bench/*/RESULTS.md` files are historical records; do not touch them.
- The writing rule (`tests/test_hygiene.py`): no em dash and no arrow characters in source or
  docs.

## 12. Decided, and open

Decided by the owner:

- Harnesses: OpenEvolve, ShinkaEvolve, ASSAY. No Claude Code arm, no Prime Agent, no
  MLEvolve, no AI Scientist v2, and RSI-top is not run.
- Models: RSI-top's models for the headline (A1, A2); Opus optional (A3).

Defaults taken in this brief, which the owner may change:

- Benchmark: the kernel take-home only, for now. MLE-bench Lite is under discussion.
- ASSAY runs standalone, not as a wrapper around OpenEvolve.
- Caps are both the 12 hours and the wrapper's budget, in its unit.
- Seeds and runs as in section 6.
- The analyst's trigger copies RSI-top's stall rule.
