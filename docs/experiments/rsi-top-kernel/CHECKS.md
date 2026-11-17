# Checks: what the wrapper's code says

Protocol step 1 of `../rsi-top-kernel.md`. Every fact below was read from code on
2026-11-16, not from a README, unless it says "page". Nothing was executed.

- Wrapper: https://github.com/RSI-top/rsi-top-wrapper at commit
  `64ce40cc6c116e2d08281c811d818281ba0b55a6` (2026-10-19), MIT. Paths below are relative to
  its root; `OE/` is `integrations/openevolve-takehome/`, `SH/` is
  `integrations/shinka-takehome/`.
- Task: https://github.com/anthropics/original_performance_takehome at commit
  `5452f74bd977807ac2e74f3d29432b9df6f25197`, the commit the wrapper's `verify.py` pins.

## Three findings that change the brief

1. **The code has no "Gemini 3.7 Flash".** `run.py:22` maps the provider `gemini` to
   `gemini-3.1-pro-preview`, and `OE/gemini_llm.py:31-34` prices every Gemini id at 2.00 and
   12.00 dollars per million tokens. The page's Gemini 3.7 Flash runs cannot be reproduced
   from this release as shipped. The owner decides which id the G arms use (section "Open").
2. **The baselines are not unmodified upstream.** `THIRD_PARTY.md:10-12` says the bundled
   OpenEvolve and ShinkaEvolve trees "include experiment-specific controller, tracing, and
   prompt changes". The arms OE-G, OE-D, SH-G and SH-D are "the wrapper's baseline command",
   which is what the published baselines are too.
3. **The page and the code define a stall differently.** Page: at least 20 minutes, after the
   first hour, in which the best score improves by fewer than 2 cycles. Code: a relative gain
   of more than 0.002 against a progress anchor. The trigger copies the code; the stalled
   fraction in the report uses the page's definition, because that is what the 84 to 92
   percent was computed with.

Also: the published RSI-top runs are not fresh runs. Each branches from its baseline at the
baseline's first stall, so everything before the branch point is shared (page). An ASSAY run
starts from the unmodified kernel. And the ShinkaEvolve charts span 14 hours, the OpenEvolve
charts 12.

## What `--budget 400` means

US dollars of worker (search model) API spend, estimated at list price. Planner and
deduplication spend is additional and not counted (`run.py:43-44`). OpenEvolve enforces it per
call (`OE/gemini_llm.py:270-279`) and by a watchdog every 20 seconds (`OE/runner.py:241-251`);
ShinkaEvolve natively (`SH/run_evo.py:288`). DeepSeek v4 Flash is priced at 0.14 and 0.28
dollars per million input and output tokens, cache discounts not modelled
(`OE/deepseek_llm.py:19-22`).

For ASSAY: `--max-usd 400` on the player's spend. The analyst's spend is recorded and
reported separately, as the planner's is.

`--hours` is wall clock, default 12. `--seed` seeds only the framework's search randomness;
the model calls carry no seed or temperature (`OE/gemini_llm.py:283-290`), so no arm is
reproducible from its seed. In the ASSAY arms a seed is a replicate label.

## Models, providers, keys

| Role | Model id | Endpoint | Key | Reasoning |
|---|---|---|---|---|
| search, DeepSeek | `deepseek-v4-flash` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | high |
| search, Gemini | `gemini-3.1-pro-preview` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` | high |
| planner | `deepseek-v4-flash` through Pi 0.84.1 | Pi's built-in provider | `DEEPSEEK_API_KEY` | `--thinking high` |
| deduplication | `deepseek-v4-flash` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | none sent (the README says high) |

`OPENROUTER_API_KEY` is required for every ShinkaEvolve run: the profile stores embeddings
with `openrouter/openai/text-embedding-3-small` (`SH/shinka.yaml:53`, `run.py:75-76`).

## The stall rule

Constants: relative gain 0.002 (`OE/run_repeat_flat_breadth.py:33`), flat seconds 1200,
minimum attempts 12, minimum run age 3600 (`OE/run_repeat_flat_breadth.py:1234-1236`).
The live rule is `OverlapPortfolioManager._maybe_mark_flat_ready`
(`OE/breadth_controller.py:515-549`).

- An attempt is one unit of search work, terminal whatever its outcome (a failed model call,
  a bad diff, an invalid evaluation, a valid child). Only ordinary attempts count; attempts
  that execute a planner's direction do not.
- The cycles anchor starts at the best program's cycles and moves when a valid new best
  satisfies `(anchor - cycles) / abs(anchor) > 0.002`. An intervention never resets it.
- The time anchor is set at the start, at each such improvement while the search is in its
  ordinary phase, and when ordinary search resumes after an intervention.
- Active seconds are the union of the intervals in which an ordinary attempt was in flight,
  clipped to the time anchor. Time in which planners run with no attempt in flight is not
  active.
- The rule fires when the phase is ordinary, the run is at least 3600 seconds old (wall
  time), the previous cohort of directed attempts has finished, at least 1200 active seconds
  have passed since the time anchor, and at least 12 ordinary attempts have ended since it.
- After an intervention there is no separate cool-down and no maximum number of
  interventions. The time anchor restarts at the first ordinary dispatch, so the next firing
  needs a fresh 20 active minutes and 12 attempts. The one-hour gate applies once.
- Directions that did not beat the frontier by more than 0.2 percent are carried into the
  next planner prompt as failed interventions, and that list is cleared on a meaningful
  improvement (`OE/run_repeat_flat_breadth.py:793-894`).

How ASSAY maps it (`tools/eval/frontier.py`): an attempt is one completed EVALUATE, valid or
not. Active time is the time a player session is running, since one player works
sequentially and its thinking between two evaluations is the attempt in flight. Pauses, the
analyst's call and the gap between sessions are not active. The rule is checked when a new
evaluation lands, which is also what makes the runner end a session after its current
action.

## The planner cohort

Four independent Pi sessions in parallel, each asked for 8 directions, each seeing 15 of the
25 metrics (`OE/run_repeat_flat_breadth.py:47-50, 1270-1276`); one listwise deduplication
call over the 32, with no cap on the number kept; each retained direction is assigned to one
new attempt, whose prompt it replaces. ASSAY's analyst is one call that returns at most five
ranked directions, which the next player session reads. This difference is the experiment.

## The evaluator

`evaluate.py` calls `OE/evaluator.py:56-146`, which runs two standard-library scripts:

1. `OE/eval_build.py <candidate.py> <takehome_dir> <instrs.json>`, untrusted, 180 seconds:
   imports the candidate, builds the kernel for forest height 10, batch 256, rounds 16, and
   dumps the instructions as JSON.
2. `OE/eval_score.py <instrs.json> <takehome_dir> 2 <result.json>`, trusted, 600 seconds:
   runs the frozen simulator on seeds 1000 and 1001. Both trials must be correct (output
   values only, as in the official test), and cycles is the maximum of the two.

`<takehome_dir>` is `OE/vendor/takehome/`, whose `problem.py` and `frozen_problem.py` are
byte-identical to the official `problem.py` and `tests/frozen_problem.py`. The scoring
process imports `N_CORES` and `Machine` from the frozen file, so a candidate cannot change the
core count or the simulator. The `tests/` rule is checked only by `verify.py`, which copies
the candidate into the pinned commit, runs `tests/submission_tests.py`, and compares hashes.
The unmodified kernel scores 147734 (`tests/test_evaluator.py:8-16`).

The pack (`worlds/recipes/kernel-takehome/`) calls the same two scripts from a pinned
checkout, and its `checks/score.py` does what `verify.py` does.

## Published kernel numbers

"Text" is prose on https://rsi-top.github.io/. "Figure data" is the constant the page's
charts are drawn from, read from the page's source: exact values, not read off pixels.

| Pair | Baseline | RSI-top | Stalled | Chart | Branch point | Interventions | Source |
|---|---|---|---|---|---|---|---|
| OpenEvolve, Gemini 3.7 Flash | 1244 | 1109 | 84% | 12 h | 1.69 h | 14 | cycles: text; the rest: figure data |
| OpenEvolve, DeepSeek v4 Flash | 1224 | 1108 | 86% | 12 h | 3.08 h | 13 | figure data |
| ShinkaEvolve, Gemini 3.7 Flash | 1221.5 | 1108 | 87% | 14 h | 3.92 h | 12 | figure data |
| ShinkaEvolve, DeepSeek v4 Flash | 1275 | 1109 | 92% | 14 h | 4.40 h | 9 | figure data |

Reference runs, each framework alone until 300 dollars are spent (figure data): GPT-5.6 Sol
1109 in OpenEvolve and 1107 in ShinkaEvolve; Claude Opus 5 1127 and 1114. The page gives no
seed counts, no variance and no dollar spend per run for the kernel task. The stalled shares
were computed the way the page's script does, and match the text's "84% to 92%".

## Task repository

Baseline 147734 cycles. `N_CORES = 1` at `problem.py:59` and `tests/frozen_problem.py:59`.
Thresholds in `tests/submission_tests.py:85-115`: 18532, 2164, 1790, 1579, 1548, 1487, 1363.
There is no licence file; `perf_takehome.py:4-5` grants use and modification but not
publishing or redistributing solutions. Solutions from this experiment are not published,
and nothing from the task repository is vendored into ASSAY.

## Not verified

- Which model id the authors used for "Gemini 3.7 Flash".
- Whether the wrapper runs on macOS (its README says Linux; the code uses `fcntl`).
- Whether ShinkaEvolve drops the configured 0.7 temperature for `deepseek-v4-flash`.
- The shape of OpenCode's JSON output and its reasoning-effort flag (OpenCode is not
  installed on the machine the kit was built on). `host_test.sh` is where this is settled.

## Open, for the owner

1. Decided on 2026-11-17: OE-G, SH-G and A1 run Gemini 3.7 Flash, the model the page names
   for every published Gemini number. Three things follow, to do on the run machine before
   the pilot:
   - The page gives no API id. Take it from Google's model list (and `opencode models` for
     the OpenCode name) and put both in `arms.env`. `kernel_exp.sh` refuses to run a G arm
     while the placeholder is there.
   - The wrapper prices every Gemini id at 2.00 and 12.00 dollars per million tokens
     (`OE/gemini_llm.py:31-34`), which are Pro prices, and ShinkaEvolve's price table has no
     entry for the model. With `--model` alone the 400 dollar budget would be counted at the
     wrong price. Set the two constants, and add the row to ShinkaEvolve's `pricing.csv`, to
     Gemini 3.7 Flash's list prices in the pack's `upstream/` checkout, and record that
     change as the one edit made to the wrapper.
   - How the authors priced and configured their own Gemini 3.7 Flash runs is not in the
     release. The calibration run (OE-G, seed 42, against 1244) is what tells us whether
     this setup matches theirs.
2. Whether A1 and A2 should also be compared with the branch-point design (start ASSAY from
   a baseline's program at its first stall). The brief says standalone from the start.
