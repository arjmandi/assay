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
describes. It is what `/assay:run` (`skills/run/`) launches.

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
| `codex` | `codex exec --json --skip-git-repo-check --sandbox workspace-write -c sandbox_workspace_write.network_access=true [--model M] <prompt>` (the network is on so that the launcher reaches the daemon's socket) | no | no |
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
past a STOP file, a failing player and a usage limit. The suite never starts
a session against anything under `bench/`; the E1 and E1b job files are only
dry-run there.

## Reproducing E1 and E1b

`bench/arcagi/jobs/e1.json` and `e1b.json` are the pre-registered designs
as job files; `bench/arcagi/jobs/README.md` says what a live run needs (the
interpreter with the `arc-agi` client, the games in the local cache,
`claude` on PATH) and where the control arms' manuals come from (they did
not ship, by owner decision O4; the dry run names them as missing until the
operator fetches them from the experiment tag). The runs that were played
are reported in the paper's archive and verified in `evidence/e1` and
`evidence/e1b`; a rerun on the 1.2.0 kernel is a new experiment.
