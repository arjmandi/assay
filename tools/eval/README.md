# The evaluation runner

`tools/eval/` runs a design, worlds times arms times seeds, from one job
file, the way the benchmark protocols describe a run: the operator starts
each run and holds the owner token, and the player's session begins in the
run directory after that. It writes a per-seed results file and a summary
with bootstrap intervals, resumes what was interrupted, never reruns what
finished, and has a dry run that prints every command and touches nothing.

It lives beside the kernel, not inside it. The kernel makes no LLM calls;
the runner launches players. It imports nothing from `assay`: it reads the
run directories' files and drives the launcher as a subprocess, and its
own imports are the standard library. Its one world-specific input is the
job file: no world name, no model id, no machine path is in the code.

```bash
python3 -m tools.eval --jobs bench/arcagi/jobs/e1.json --dry-run
python3 -m tools.eval --jobs bench/arcagi/jobs/e1.json --state-dir <dir>
python3 -m tools.eval --jobs bench/arcagi/jobs/e1.json --state-dir <dir> --summarize
```

From the repository root (`python3 -m tools.eval`), or by path from anywhere
(`python3 <repo>/tools/eval/__main__.py`). `python3 -m tools.eval --help`
lists the options.

The session mode plays one run that already exists, as the next section
describes. It is what `/assay:run` (`skills/run/`) launches. The report mode
writes up one run, for `/assay:report` (`skills/report/`), and the compare
mode runs ASSAY against Claude Code with `/goal` on one pack, for
`/assay:compare` (`skills/compare/`), as the two sections after it describe.

## The session mode

```bash
python3 -m tools.eval session --run-dir <run> [--player claude|codex|opencode] [--model M] \
    [--hours 8] [--max-sessions 5] [--max-usd USD] [--dry-run]
python3 -m tools.eval session --run-dir <run> -- <player command template ...>
```

It takes a run that `/assay:attach`, or an operator's `assay start`, left
waiting for its player. It does what the operator would do by hand overnight:
one host session after another, until the run says it is over. Before each
session:

1. `assay --run-dir <run> start <world>` resumes the run: RESUMED while the
   daemon is up, a replay of the journal when it is gone. A world without
   replay cannot come back, and the mode ends START_FAILED.
2. The player runs in the run directory with the prompt (`PLAYER.md`, or
   `--prompt FILE`) as its argument. Its environment is the runner's without
   the names the pinned registry lists under `secrets`, without
   ASSAY_OWNER_TOKEN_FILE, and without any `--strip NAME`. So a world's
   credentials stay in the daemon's environment, and the player has no route
   around the adapter. `ASSAY` names the launcher: by default the one the
   run's hook policy pins, else `bin/assay`.

The hosts, as templates in `session.py`:

| `--player` | The command | Stop gate | Tested |
|---|---|---|---|
| `claude` (default) | `claude -p <prompt> [--model M] [--max-budget-usd USD] --output-format json --permission-mode acceptEdits --allowedTools Bash Read Glob Grep 'Write(./**)' 'Edit(./**)' --disallowedTools WebFetch WebSearch Agent` | yes | yes |
| `codex` | `codex exec --json --skip-git-repo-check --sandbox workspace-write -c sandbox_workspace_write.network_access=true [--model M] <prompt>` (the network is on so that the launcher reaches the daemon's socket) | no | yes, once: the counter world to a WIN with Codex 0.163.0; no cost reported |
| `opencode` | `opencode run --format json --auto [--model M] <prompt>` | no | no |

A command after `--` replaces the template. Its placeholders are `{prompt}`,
`{prompt_file}`, `{run_dir}`, `{model}`, `{assay}`, `{repo}`, `{python}` and
`{session}`, and it gets the prompt on stdin as well. The tests use it with
the fake player. The host's `--version` is read once, and a named host that
does not run is refused before anything is touched.

When a session ends, the runner reads the run with the Stop gate's rules
(below):

| The run says | Outcome | The daemon |
|---|---|---|
| WIN, the paid actions at the registry's cap, the reported spend at its dollar cap | `WIN`, `CAP`, `USD_CAP` | stopped |
| an APPROVAL_REQUIRED or REHEARSAL_REQUIRED refusal in this session that no `approval_granted` or `liveness_waived` answered | `OWNER_NEEDED`, with an `OWNER |` line naming the refusal and the owner's command | up |
| a line beginning `STOP:` added to `.assay/NOTES.md` in this session | `SELF_STOP`, with a `DECLARED |` line | up |
| none of these | the next session starts | up |

The mode also ends, with the daemon left up, when this invocation reaches
`--max-sessions` (`SESSIONS_EXHAUSTED`), its `--hours` (`TIME_EXHAUSTED`,
the running session terminated), or the `--max-usd` its sessions reported
(`USD_EXHAUSTED`; the `claude` host also gets the dollars left as
`--max-budget-usd`). It ends when two sessions in a row exit non-zero
without a paid action (`PLAYER_FAILING`), at a `STOP` file in the state
directory (after the current session), and at SIGINT or SIGTERM, which end
the current session at once (`STOPPED`). A session that a usage limit cut
short (the same signal as the batch mode) pauses the runner
`--pause-minutes` and does not count. The last line is always `OUTCOME |
<outcome> | <sessions> | paid actions <n>/<cap> | $<spend> reported by the
host | <the daemon>`, and the exit status is 0. A refusal before anything
runs is `ERROR | ...`, exit 2.

A relaunched session's prompt gains an operator's note when the owner acted
since the previous one: an approval the daemon still holds (granted less than
600 seconds ago, unused, on a daemon the start kept), a waiver, a ratified
goal. The bill of a `claude -p` session (`total_cost_usd` and the tokens of
its report) is posted to the run with `assay spend report --id
claude-<session id>`, so `assay status` shows it and a registry's
`budget.usd` applies.

The state directory, `--state-dir`, by default `<run>.sessions` beside the
run directory and outside the player's: `sessions.json` (the ledger, one
record per session: the host and its version, the model, times, the exit
code, the start's line, the launch counts, the paid actions at exit, the
names kept from the player, the report, the gate's decisions, the spend
posted, why it ended), `s<n>.prompt.md`, `s<n>.start.log`,
`s<n>.player.out`, `s<n>.player.err`, `status.json`, `stop_gate.json`,
`runner.lock` (one runner per run) and `STOP`.

### The Stop gate

`hooks/stop_gate.py` is the Stop hook of a Claude Code player: the session's
`/goal` with the journal as the judge. The runner merges it into the run
directory's `.claude/settings.json`, beside the kernel's PreToolUse and
PostToolUse entries; `assay hooks install` keeps it. It reads the journal,
the pinned registry, the activity log and the notes, and lets the session
end only:

- at WIN, at the action cap, or at the dollar cap;
- when an owner action is pending since the session's launch;
- after a line beginning `STOP:`, with a reason, that the session added to
  `.assay/NOTES.md`. In this version that declaration is not journaled.

Otherwise it answers `{"decision": "block", "reason": ...}`, and the reason
tells the player to continue from `assay status`, or to write the `STOP:`
line. After three blocks with no paid action between them it lets the stop
through, and the runner decides what comes next. It acts only when
ASSAY_STOP_GATE names its state file, which the runner sets for the player
and rewrites with the launch's counts before each session. A person's own
session in the run directory is never held. A run it cannot read is never
held either. The runner loads the same file by path, so the gate and the
runner apply one rule.

## The report

```bash
python3 -m tools.eval report --run-dir <run> [--out FILE] [--sessions DIR] [--check CMD] [--items N] \
    [--assay CMD | --no-audit]
```

`report.py` writes what one run verified as a Markdown document,
`<run>.report.md` beside the run directory, and the same facts as a JSON
record beside it, `<run>.report.json`, for a tool to read. It writes nothing
in the run directory, and `--out` may not point under its `.assay` or
`.claude`. The last line it prints is the short form:

```
REPORT | SELF_STOP | progress 0/1 | paid actions 3/40 | CLEAN | held 1, missed 1 | $0.04 reported to the run
```

The document's sections:

| Section | What it says | Read from |
|---|---|---|
| Summary | one line each: how it ended, integrity, the predictions, what was never exercised, the external check when there is one, the cost | the sections below |
| How it ended | WIN, CAP, USD_CAP, SELF_STOP with the declaration, OWNER_NEEDED, or UNFINISHED, by the Stop gate's rules; the final state and progress; the paid actions of the cap; an external check's verdict | the journal; the registry, the notes, the activity log and the runner's ledger for the stop's reason |
| Integrity | the kernel's audit (the chain, the contiguity, the ungated rule, the anchors outside the run directory) and the head the kernel stored | `assay audit --json`, run for the report, else `.assay/audit.json`; `.assay/chain.json` |
| What held | the PREDICTIONS line, as `assay status` prints it; per action and per outcome form (the text with its value left out), the events where it held and missed | the journal |
| The misses | each missed outcome, with what the code read instead | the journal |
| The player's Verified lines | each line of the notes' Verified section, with how the events it cites graded | the notes and the journal |
| Never exercised | the actions advertised and never taken, the ones taken six times or more that never changed the world, the ones never given a specific outcome, the declared states never graded; the progress unit where the run stands, as the kernel's coverage audit reads it; the conclusions of impossibility the player journaled, with their coverage audits; the player's open questions | the journal; `.assay/channels.json`; the notes |
| The owner's acts and the refusals | approvals, waivers, ratified goals, and the commands the harness refused, by code | the activity log |
| Sessions and cost | per session, the host and its version, the model, the minutes, the paid actions, the bill, the Stop gate's blocks and why it ended; the spend reported to the run | the runner's ledger; the activity log |

The facts about the world come from the journal alone, the public contract
of `verify/JOURNAL_SPEC.md`, so anyone holding the run directory gets the
same facts. The verdict on the record is the kernel's own: the report runs
`assay audit --json` through the launcher (`--assay CMD`, by default the
repository's `bin/assay`) and reads the head the kernel stored in
`.assay/chain.json`. With `--no-audit`, or when the audit cannot run, it reads
the last `audit.json` and says so; a directory with neither is reported as
not audited. The meters count as
`assay status` counts its PREDICTIONS line, leaving out notes, free text
graded as change, the kernel's own predictions and the passes of a vacuous
verifier (the identity rule of 1.2.0, recomputed from the journal). The
coverage counts as the kernel's coverage audit counts it. The report imports
nothing from `assay`. Each section names the other files it reads, because
the chain covers none of them. The registry, the notes, the declared states,
the activity log, the audit file and the ledger are operator-side and
agent-side state, outside the journal.

`--check CMD` runs an external checker, such as a pack's
`checks/score.py --run {run_dir} --json`, with `{run_dir}` replaced by the
run directory. The report records its exit status (0 is a pass) and its
output, as JSON when it prints a JSON object. `--sessions DIR` names the
runner's state when it is not `<run>.sessions`. `--items N` caps the
document's lists, 25 by default. The JSON record always holds every item.

## The watch and diagnose modes

```bash
python3 -m tools.eval watch --run-dir <run> [--every 10] [--once] [--until-end]
python3 -m tools.eval diagnose --run-dir <run> [--json]
```

`watch.py` prints one line whenever the run changes: the session runner's
state, the run's outcome so far, the paid actions of the cap, the outcomes
held and missed (as `assay status` counts them), the last paid action and how
it graded, the spend reported to the run and the Stop gate's blocks.
`--once` prints one line; `--until-end` stops when the session runner ends
or the run reaches WIN or a cap. `/assay:watch` runs it, in the background
with the Monitor tool when the user wants to follow a run.

`diagnose.py` reads the report's record against the five layers of the
README ("Reasoning layers") and prints, per layer, each finding with its
evidence and where to steer:

| Layer | What it finds |
|---|---|
| 1 the constitution | a declared stop, or a conclusion of impossibility, that rests on untested rules; milestone predictions that keep missing |
| 2 the modules | an outcome form missed three times or more with no hold; a stalled progress unit; actions taken six times that never changed the world; refusals a module's demand caused; three or more blocked stops |
| 3 the registry and the adapter | outcomes the code could not grade; three or more commands refused for their form (a parameter, an action, a state name, the prediction's syntax) |
| 4 the addressable states | predictions that name no particular result; declared states never graded; world-model misses of 30 percent or more over five or more outcomes, with the forms that missed most; Verified lines that cite nothing the record holds |
| 5 the model | nothing above, and the run is not won |

A finding is a place to look, not a verdict. Both modes read the run's
files and write nothing; `/assay:diagnose` explains the findings and offers
changes only to what is the operator's (a registry, a recipe, a prompt).

## The compare mode

```bash
python3 -m tools.eval compare --pack <pack> [--seeds 1 2 3] [--model M] [--max-usd USD] \
    [--hours H] [--max-sessions N] [--arms assay,goal] [--state-dir DIR] [--dry-run]
```

`compare.py` runs two arms on one pack whose directory holds `compare.json`,
the design: the world, the seeds, the model, the dollars and hours per run,
the hypothesis, the metric, how the result is read, the `/goal` condition,
the goal arm's wording of the task and its way to the world, the deny
patterns for the ASSAY arm's hooks, and the checker command for each arm.
`worlds/recipes/flaky-test` and `worlds/recipes/hidden-grammar` carry one.

- **The assay arm** is `/assay:run`: per seed, the operator's `assay start`
  with the pack's adapter, registry and seed, the owner token outside the
  run, `assay hooks install` with the design's deny patterns, a prompt from
  `prompts/compare_assay.md`, the session mode with its Stop gate, then
  `assay stop`, `assay audit` and the report with the checker (`--check`).
- **The goal arm** is Claude Code alone: per seed, a bare copy of the world
  built by the recipe's own setup (`world/` for the copy mode, `work/` for a
  worktree) and `world.py`, which runs the recipe's own observe and act
  commands, so the world answers and refuses as it does under ASSAY, with no
  prediction, no gate and no journal. One `claude -p "/goal <condition> ..."`
  session runs there, with the prompt of `prompts/compare_goal.md`. Every act
  is logged outside the directory, and in the copy mode the log is replayed
  on a fresh copy afterwards, so a copy changed by hand is named.

Both arms run with the same model, no MCP server (`--strict-mcp-config`),
acceptEdits with the same tools allowed and refused, `--max-usd` and
`--hours` per run. The ASSAY arm may use up to `--max-sessions` sessions
inside those; the goal arm is one session, which `/goal` keeps going.

How each run ended, and whether it claimed the goal:

| Arm | Ended by | Claimed the goal |
|---|---|---|
| assay | the session mode's outcome: WIN, SELF_STOP, CAP, USD_CAP, an exhausted limit, OWNER_NEEDED | at WIN or SELF_STOP |
| goal | `met` (the session ended with its last stop let through, so the evaluator judged the condition met), `cap` (Claude Code ended it after nine blocked stops in a row), `budget`, `time`, `error` | at `met` |

A false completion is a run that claimed the goal while the checker did not
score it correct. The state directory (default `./compare-state/<pack>`):

```
PREREGISTRATION.json, .md   the design as run, written before the first launch
runs/assay-s<seed>/         the ASSAY runs, with their .sessions beside them
runs/goal-s<seed>/          the goal arm's directories, world.py in each
tokens/, anchors/           the owner tokens and hook policies, the anchor files
goal-state/                 the goal arm's act logs and scratch, outside its directories
reports/assay-s<seed>.*     the report of every ASSAY run
logs/                       the session mode's output, the goal sessions' streams
results.jsonl               one row per arm and seed
COMPARE.md                  the totals per arm, every row, the reading as registered
```

A rerun skips the rows already there. A state directory whose
pre-registration names another design (the pack's files, the prompts, the
arms, the seeds, the model, the budgets) is refused: a new design is a new
state directory. `--dry-run` prints the plan, both arms' commands and the goal
arm's prompt, and writes nothing. `--goal-player`, `--assay-player` and
`--assay-prompt` replace the players and the ASSAY prompt; the tests use them
with stand-ins.

What a met goal looks like was checked on 2026-10-16 with Claude Code
2.1.292: the JSON result names no verdict, so the mode reads it from the
stream, `--output-format stream-json --verbose`. A session whose condition is
met ends without a `Stop hook feedback` message after its last turn. One whose
condition never is met ends at the `stop-hook-block-cap` notification. Auto
mode was not active for Haiku in `-p` (the session reported the default
mode), so both arms use acceptEdits.

## The job file

JSON, one object. JSON because every other contract file of the harness is
JSON (the registry, `config.json`, the journal), because an arm's registry
variant is a JSON fragment laid over a JSON registry, and because the
orchestrator that ran the paper's experiments took JSON job files, so the
designs ported without translation; the `description` fields carry what
comments would have. Every path in the file is relative and is resolved
against the file's own directory; an absolute path is refused.

```json
{
  "name": "e1",
  "description": "prose, for the reader",
  "adapter": "../adapter.py:factory",
  "concurrency": 3,
  "max_sessions": 3,
  "max_hours": 4,
  "player": {
    "model": "claude-opus-5",
    "command": ["claude", "--model", "{model}", "-p", "{prompt}", "--output-format", "json"]
  },
  "worlds": [
    {"id": "ft09", "levels": 6, "budget": 200, "registry": "../registry_200.json"}
  ],
  "arms": [
    {"name": "gated", "prompt": "../../../tools/eval/prompts/e1_player.md", "constitution": "../../../CONSTITUTION.md"},
    {"name": "ungated", "prompt": "../../../tools/eval/prompts/e1_player_ungated.md",
     "constitution": "manuals/CONSTITUTION-ungated.md", "registry_overlay": {"gate": "optional"}}
  ],
  "seeds": [1, 2]
}
```

- `adapter`: `file.py:factory`, the file relative to the job file; a world
  may carry its own.
- `concurrency`: players at a time (default 1). `max_sessions`: launches
  per job before it is reported as it stands (default 3). `max_hours`: the
  wall clock per session (default 4). The command line overrides each.
- `player.command`: the player's argv as a template. Placeholders:
  `{model}`, `{prompt}` (the rendered prompt text, as one argument),
  `{prompt_file}`, `{run_dir}`, `{world}`, `{job}`, `{seed}`, `{assay}`
  (the launcher), `{repo}` (the checkout the runner lives in), `{python}`
  (the interpreter running the runner). The rendered prompt is also fed to
  the player on stdin. The player runs with the run directory as its working
  directory, so a relative path in the command is relative to that; a player
  kept in the tree is named through `{repo}`. The environment carries
  `ASSAY` (the launcher the operator's start used, the convention of
  `GUIDE.md` section 5) and `ASSAY_ANCHOR_DIR` (the state directory's
  anchors); everything else is inherited from the shell that runs the
  runner, `ASSAY_PYTHON` included.
- `worlds`: `id` (the world id `assay start` takes), `levels` (the progress
  units, for the prompt), `budget` (the paid-action cap; it must equal the
  registry's `budget.actions`, and the runner refuses a job where it does
  not), `registry` (relative path), `pass_seed` (optional, default false:
  pass the seed to `assay start --seed`; the benchmark designs do not, a
  seed there is only a fresh run directory).
- `arms`: `name`, `prompt` (a template, below), `constitution` (optional:
  the manual the prompt points the player at), `registry` (optional: the
  arm's own registry, instead of the world's), `registry_overlay` (optional:
  keys laid over the registry's top level; the runner writes the result to
  `<state>/registries/<arm>-<registry stem>.json` before the start, so the
  variant never has to exist in the tree), `model` (optional override),
  `description`.
- `seeds`: integers.

One job per (seed, world, arm), nested in that order, which is the queue
order the protocols fixed: every arm of a world at seed 1 before the next
world, and the whole first seed before the second. The job id is
`<name>-<world>-<arm>-s<seed>`, for example `e1-ft09-gated-s1`.

## Prompt templates

`tools/eval/prompts/` holds the E1 player templates (`e1_player.md`,
`e1_player_ungated.md`), ported from the experiment tag with every machine
path removed and the operator-first order written in: the player begins at
`assay status` in a run the operator has already started, and never runs
`start` or `stop`. A template fills `{{NAME}}` placeholders: `WORLD`,
`LEVELS`, `BUDGET`, `SEED`, `MODEL`, `JOB`, `RUN_DIR`, `REGISTRY` (the file
the run pinned, the materialized variant when the arm has an overlay),
`CONSTITUTION` and `CONSTITUTION_NAME` (when the arm names one), `ASSAY`,
`ADAPTER`, `REPO`, `STATE_DIR`. An unfilled placeholder is an error before
anything is launched.

## What one job does

For each session of a job, in the runner's own process:

1. `assay --run-dir <state>/runs/<job> start <world> --adapter ...
   --registry ... --owner-token-file <state>/tokens/<job>.token`, with
   `ASSAY_ANCHOR_DIR=<state>/anchors`. The token file lies outside the run
   directory and never reaches the player. On a run that already exists the
   same command resumes it: the kernel replays the journal when its daemon
   is gone (`RECOVERED`) or answers `RESUMED` when it is still up.
2. The player's command, as a subprocess with the run directory as its
   working directory and the rendered prompt on stdin, with the wall clock
   of `max_hours`.

When the player exits, the runner reads the run: `WIN`, or paid actions at
the budget (`CAP`), finishes the job, and `assay stop` takes its daemon down
(harmless when the daemon already exited on WIN). A session that ended
without either is relaunched, up to `max_sessions`, after which the job is
reported `SESSIONS_EXHAUSTED` as it stands. A usage-limit or 429 signal in
the player's report pauses the whole queue for `--pause-minutes` (default
30) and does not count as a session. A `STOP` file in the state directory,
SIGINT or SIGTERM stop new launches and let the running sessions finish.
Nothing is rerun because of its result; every launched run is reported.

On every start the runner reads every run directory first: a job whose
journal already says WIN or cap is `SKIP`ped and never launched again; one
that ran out of sessions earlier is reported as it stands; the rest are
queued, the interrupted ones resuming through the kernel's own `start`.

## The state directory

`--state-dir` (default `./eval-state/<name>`, ignored by git). One runner
per state directory, held by a file lock (`runner.lock`); a second runner
on the same directory is refused with `ERROR | another runner holds ...`.

```
runs/<job>/            the run directories (one per job; the player's cwd)
tokens/<job>.token     the owner tokens, outside every run directory
anchors/               the anchor files (ASSAY_ANCHOR_DIR for every start)
registries/            materialized registry variants (arm + overlay)
prompts/<job>.s<n>.md  the rendered prompts, one per session
sessions/<job>.json    the ledger: one record per session (times, exit code,
                       the player's JSON report, the journal at exit)
sessions/<job>.s<n>.start.log, .player.out, .player.err, .stop.log
status.json            the queue, rewritten on every change
results.jsonl          one row per job that ran (below)
summary.json           the bootstrap summary (below)
STOP                   create it to stop new launches
```

## Results

`results.jsonl`, one row per job that has a run directory or a session, in
queue order. The fields: `job`, `world`, `arm`, `seed`, `model`, `outcome`
(`WIN`, `CAP`, `SESSIONS_EXHAUSTED`, `STOPPED`, `START_FAILED`), `win`,
`state`, `levels_completed`, `win_levels`, `paid_actions`, `budget`,
`predicted_actions` (paid actions carrying a prediction), `receipts` (a
tally by the frozen result tokens: `PREDICTED`, `SURPRISE`,
`INVALID_CLAIM`, `UNGATED`, `LEVEL_COMPLETE`, `GAME_COMPLETE`,
`GAME_OVER`, `RESET`), `refused_predictions` (a `--predict` refused under
`gate: off`), `sessions`, `wall_seconds`, `tokens` (input, output,
cache_read, cache_creation, total), `dollars`, `tokens_per_action`,
`dollars_per_action`, `chain_head`, `run_dir`, `source`.

Where they come from today: the journal (`.assay/events.jsonl`, the public
contract) for the paid actions, the state and the progress; the receipts
(`.assay/receipts/*.json`) for the result tally; the activity log
(`.assay/activity.jsonl`) for the refused predictions; `.assay/chain.json`
for the head; and the player's own report for tokens and dollars: when the
player's stdout is one JSON object in the shape `claude -p --output-format
json` prints, `usage` and `total_cost_usd` are read from it and summed over
the job's sessions (list-price accounting, as the ledgers of the paper's
runs), else both are null. Per action means per paid action.

The seam for #13. When the CLI gains `--json`, `assay status --json` and
`assay audit --json` return the same facts as records (the `Status` record
of `docs/ARCHITECTURE.md` section 7.4 and the audit report). The switch is
one function in `tools/eval/results.py`: `read_run_json` beside
`read_run_files`, mapped onto the same `RunFacts`, and `read_run` pointed at
it. Every row carries `source` (`files` today) so a results file that mixes
the two says which rows came from which.

## The summary

`summary.json`, and the same printed: per arm over its runs, and per arm
and world over its seeds, the mean of each metric (`win`, `paid_actions`,
`tokens_per_action`, `dollars_per_action`, `dollars`) with a bootstrap
percentile interval of the mean (`--bootstrap-resamples`, default 2000;
`--bootstrap-seed`, default 0; 95 percent). Deterministic under the seed.
With two seeds per cell, as E1 has, the per-world interval is the pair
itself; the arm's interval over its six runs is the one worth reading.
Nothing here asserts significance; the protocols read direction only.

```
SUMMARY | e1 | 12 run(s) | bootstrap 2000 resamples, 95% percentile intervals of the mean, seed 0
ARM | gated | runs 6 | wins 6/6 1.00 [1.00, 1.00] | paid actions 198.7 [136.2, 258.3] | ...
  ft09 | runs 2 | wins 2/2 | paid actions 80, 75 | mean 77.5 [75.0, 80.0]
```

`--summarize` recomputes `results.jsonl` and `summary.json` from the state
directory without launching anything.

## The dry run

`--dry-run` prints, per job, the run directory, the registry variant it
would write, the prompt it would render, the operator's `start` command and
the player's command as shell lines, then every missing input (`MISSING |
<job> | <label> | <path>`) and every problem (a budget that disagrees with
the registry, a template with an unfilled placeholder), and exits 1 when
there is any. It writes nothing: no state directory, no rendered prompt.

## The fake player and the tests

`tools/eval/fake_player` is a scripted stand-in for the LLM player, for the
runner's own tests: it reads the rendered prompt on stdin, runs every line
of it that begins with `assay ` through the launcher `ASSAY` names, in the
run directory, appends every line that begins with `note: ` to
`.assay/NOTES.md`, and prints a report in the shape `claude -p` prints, with
a fixed cost per command and the names of its environment. `tests/test_eval_runner.py` runs one queue with it
against the counter example (`examples/counter_world.py`) through the real
launcher, CLI and daemon: two seeds, two players at a time, each session a
`status`, one `act` and a `stop`, so the second session goes through the
operator's resume (`RECOVERED | replayed 1 paid actions`) and reaches WIN.
`tests/test_eval_session.py` runs the session mode with it, through the same
launcher and daemon: to a WIN, through an approval, to a declared stop, and
past a STOP file, a failing player and a usage limit.
`tests/test_eval_compare.py` runs both compare arms with stand-in players
through the same launcher and daemon. `tests/test_eval_report.py` reports on fixture journals, on one published
journal on its own (not audited), and on a run the fake player played through
the session mode, against the kernel's own PREDICTIONS line, coverage ledger,
chain and audit. The suite never starts a session against anything under
`bench/`; the E1 and E1b job files are only dry-run there.

## Reproducing E1 and E1b

`bench/arcagi/jobs/e1.json` and `e1b.json` are the pre-registered designs
as job files; `bench/arcagi/jobs/README.md` says what a live run needs (the
interpreter with the `arc-agi` client, the games in the local cache,
`claude` on PATH) and where the control arms' manuals come from (they did
not ship, by owner decision O4; the dry run names them as missing until the
operator fetches them from the experiment tag). The runs that were played
are reported in the paper's archive and verified in `evidence/e1` and
`evidence/e1b`; a rerun on the 1.2.0 kernel is a new experiment.
