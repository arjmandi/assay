# Onboarding: attaching a world to ASSAY

This guide is for a developer or an agent who wants to put a new world behind
the harness. It is derived from `docs/ARCHITECTURE.md`, which stays the single
source for every contract. Where this guide and that document could disagree,
the architecture document wins, and this guide points at it rather than
restating a contract in full. File and function names are given the way the
architecture document gives them, under `src/assay/`.

## 0. What you get

ASSAY sits between an agent (a person at a terminal, or an LLM agent) and a
world you register. The agent is never told what the registered actions do.
Every paid action needs a checkable prediction, validated before anything is
spent and graded in code against what the world actually reported. Everything
lands in an append-only journal under a rolling hash chain whose heads are
anchored outside the run directory, and `assay audit` recomputes the verdict
from the artifacts alone. On top of that sit named channels the agent declares
over the observation, sandboxed verifiers and a world model the agent writes,
behavior modules that advise or demand structure, a standing goal the agent
can propose to change but never ratify, carryover between runs that lands
demoted, and the owner's gates (destructive, approval, budget, liveness). You
supply two things: an adapter (one Python file) and a registry (one JSON
file). Nothing else about your world enters the kernel.

## 1. Install on a clean machine

Python 3.12 or newer, on macOS or Linux. The kernel uses `fcntl` file locks,
Unix domain sockets for the daemon, and resource limits for the sandbox, so
Windows is not supported. Three ways in, exactly as `README.md` states them:

1. No install. `bin/assay` is the launcher: it runs the first `python3` on
   PATH that has numpy 2.x, else `uv run` resolves the inline dependencies of
   `src/assay_cli.py`, else it builds a private runtime once under the temp
   directory. `ASSAY_PYTHON=/path/to/python` pins the interpreter, and a pin
   that fails the fingerprint is an error, never a silent fallback.
2. Editable install, recommended for benchmark worlds, because the same venv
   holds the adapter's dependencies and the daemon inherits it:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -e '.[grid]'
   .venv/bin/assay doctor            # or put .venv/bin on PATH
   ```

   `[grid]` adds pillow for frame worlds (rendering). A dict world does not
   need it. `[arcagi]` adds the ARC-AGI-3 client. `[dev]` adds pytest.
3. The CLI alone. `pipx install '.[grid]'` puts `assay` on PATH in its own
   environment, for worlds whose adapters have no dependencies of their own.

Check the result before anything else:

```bash
assay doctor
```

It prints one `DOCTOR | ok | ...`, `DOCTOR | WARN | ...` or `DOCTOR | FAIL | ...`
line per check: the Python version and path, numpy, pillow (a warning if
missing, since only frame worlds need it), the sandbox mode, the socket path
length, the anchor directory, and, inside a run directory, the run state, the
daemon, the adapter (resolved and dry-imported) and the registry. The last line
counts failures and warnings, and the exit code is 2 when something failed
(`cli._doctor`).

## 2. The counter world in five minutes

The README quickstart, verbatim (the conformance test runs these same lines):

```bash
ASSAY=<repo>/bin/assay
mkdir demo && cd demo
"$ASSAY" start counterdemo \
    --adapter "<repo>/examples/counter_world.py:factory" \
    --registry "<repo>/examples/example_registry.json"

"$ASSAY" act INC amount=1 --predict "change"        # graded ✓
"$ASSAY" act NOOP --predict "change"                # graded ✗, with the counter-fact
"$ASSAY" channel declare counter --path counter     # register a named reading
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"   # WIN
"$ASSAY" audit                                      # chain + integrity verdict
```

Line by line:

- `start counterdemo --adapter ... --registry ...` creates the run in the
  current directory. `counterdemo` is the world id, a label for this run (a
  benchmark adapter may read it to pick the instance). `start` validates the
  registry and dry-imports the adapter before anything is spawned or written
  (`broker.resolve_adapter_spec`, `broker.check_adapter_spec`), spawns the
  daemon that owns the world (`broker.start_broker`), takes the first
  observation as journal event 0, and prints `STARTED | counterdemo | local
  simulator | no action-idle lease | replay recovery enabled` followed by
  the full status: the observation, the registered actions with their
  schemas and never their meanings, the budget, the standing goal, the
  `CHANNELS` block and the `ANCHORS` line.
- Before the status, `start` prints the owner token once, on a line that
  begins `OWNER TOKEN |` and carries the token itself. Only the sha256 is
  kept in `.assay/owner.json`. It authorizes ratifications, approvals and
  waivers. With `--owner-token-file PATH` (or `ASSAY_OWNER_TOKEN_FILE`) the
  token is written to that file with mode 0600, outside the run directory,
  and the line says `OWNER TOKEN | written to PATH (mode 0600)` instead.
  Here one person plays both roles; an evaluated agent does not start the
  run (chapter 7).
- `act INC amount=1 --predict "change"` is one paid action. The daemon
  validates the name, the typed parameter, the budget and the claim before
  spending, applies the action, and grades the claim against what actually
  happened. The counter moved, so the receipt starts with
  `OUTCOME | PREDICTED | result matched the prediction`, followed by a
  `KEY DELTA` of the changed keys and the new observation.
- `act NOOP --predict "change"` makes the same claim, but NOOP changes
  nothing. The receipt begins

  ```
  OUTCOME | SURPRISE | prediction missed: change | no observed change (0 keys)
  ```

  The counter-fact is the machine's statement of what happened. A miss is
  the product, not the failure: it corrects the agent's model at the price
  of one action.
- `channel declare counter --path counter` registers a named reading of the
  observation, here the `counter` key of the dict the adapter put under
  `data`. It prints a line beginning `CHANNEL | declared counter (path)`.
  From now on claims can name the referent exactly. The declaration is free
  and journaled.
- `act INC amount=2 --predict "ch counter = 3; win"` makes two claims on one
  action: the channel reads exactly 3 afterwards, and this action reaches the
  goal state. Both grade, the world reports `WIN`, the receipt says
  `OUTCOME | GAME_COMPLETE | the goal is reached; this run is complete`, the
  daemon calls the adapter's `finalize` if it has one, anchors the chain
  head, and exits.
- `audit` recomputes integrity from the artifacts: contiguity, the chain, the
  anchors, and a scan for any paid action that bypassed the gate. A clean run
  prints `AUDIT | CLEAN | events 4 (paid 3) | contiguous yes | chain intact |
  anchors intact (1)`.

Stop and resume. A run that has not reached WIN keeps a daemon alive. Stop it
cleanly with

```bash
"$ASSAY" stop
```

which prints `STOPPED | environment owner pid N exited after 0.0Xs | `assay
start` resumes the run`. The daemon finishes any step it is inside before it
exits, so a paid action is never cut between spend and record. Resume with
the same `start` command in the same directory. The first word tells you
what happened: `RESUMED` means the daemon was still alive and answered, and
the CLI reconnected. `RECOVERED` means a fresh daemon was started and the
journal was replayed through the adapter, action by action, with every
recorded observation checked (`broker._replay_local_session`). Either way
the journal is untouched, which is why the adapter must be deterministic.
Starting a completed run prints `RESUMED | counterdemo | completed run`.

## 3. The component model

Read `docs/ARCHITECTURE.md`. Eight components make up every run, each with
one owner, one contract, what is required and optional, what must never go
there, and its extension points:

- Registry (2.1): the world's contract, operator-owned, pinned per run.
- Adapter (2.2): the world plug, world-owned, the one file ASSAY touches a
  world through.
- Runtime configuration (2.3): the operator's side beyond the registry: owner
  token, approvals, waivers, budgets, carryover, anchors, interpreter.
- Constitution (2.4): the agent-facing manual, one file for every world.
- Modules (2.5): declare, advise, demand units over the journal.
- Channels (2.6): named readings the agent declares over the observation.
- Verifiers and the world model (2.7): agent-written code, sandboxed,
  trusted only by replay fit.
- Journal, chain, anchors, audit (2.8): the record, kernel-owned.

Section 3 describes the frame-world extra, section 4 is the per-world
conformance table (ARC-AGI-3, Factorio, OOLONG, the counter example, the
template), and section 5 lists what the public contract freezes. This guide
does not copy the table. When you have attached a world, you fill a column of
it (chapter 11).

## 4. Write your adapter

Copy `examples/new_world/adapter.py` and change the world. It is a vault with
two rooms, written so that every part of the contract is exercised, and the
conformance test drives it end to end. The contract, from the architecture
document's section 2.2:

- `factory(root, config) -> session`, named on the command line as
  `adapter.py:factory` (or `module:factory` for an installed module).
  `root` is the run directory, `config` is `config.json`: `game_id` (the
  world id typed at start), `seed`, `mode`, `adapter`, `registry` (a bool),
  `created_at`, `harness`, `harness_version`, `journal_spec`, `python`,
  `binding_hash`, `registry_hash`, and on registry runs `anchor_file`.
- `session.observation`, a property returning the current world state.
- `session.step(action, data, reasoning)`, applying one validated action and
  returning the observation. `data` is the typed parameter dict the registry
  validated, or `None`. `reasoning` is the agent's journaled prediction
  context, for the world to log or ignore, never to obey.
- `session.finalize()`, optional, called once by the daemon on the first
  observation whose state is `WIN`. An exception here becomes a warning on
  the receipt. The template writes `.assay/new_world_summary.json`.
- `session.public_info`, optional, a dict stored in `config.json` at start.
- `session.session`, optional, the world's declaration of its session rules
  (`assay.adapters.SessionCapability`): an action-idle lease, whether a reset
  on a fresh progress unit is answered by the kernel without reaching the
  world, and whether the run replays on resume. The kernel implements each
  from the declaration and records it in `config.json` at start; a world
  that declares nothing gets local semantics (architecture section 2.2).
- `session.replay(transitions)`, optional, called at a resume with every
  recorded transition (action, parameters, observation) before the kernel
  steps them through the fresh session, for a world that needs its own
  record of what it spent. The template declares neither.

The dict observation shape (`core.normalize_observation`, dict branch):

```python
{
    "state": "NOT_FINISHED",            # or "WIN", or a world terminal such as "GAME_OVER"
    "levels_completed": 0,              # progress units done
    "win_levels": 2,                    # progress units in total (1 when there is one)
    "available_actions": ["TURN", "OPEN", "ENTER"],   # usable right now
    "data": {"room": 1, "dial": 0, "door": "locked"},  # any JSON object
}
```

`data` is where everything the agent may read and claim against goes. The
kernel sorts and stringifies `available_actions` and checks each action
against them before spend (`registry.check_registry_action`), so advertise
what is usable. The lifecycle values the kernel reads are `NOT_FINISHED`,
`WIN` (terminal, triggers `finalize`, ends the daemon) and `GAME_OVER`
(`assay reset` needs no reason after it, and the hazard module tags the
action class). A world with no intermediate milestones uses `win_levels: 1`.

A frame world returns `"frame"` (or `"frames"`), a list of 2-D integer grids
with values 0 to 15, instead of `data`. The frame-world extra `assay_grid`
then renders and inspects it, selected by that shape alone (architecture
section 3).

Refusals go through the observation. A world that refuses an action for its
own reasons reports the refusal in `data`, so the spend is journaled as
evidence. The Factorio adapter writes `POLICY_REFUSED | reason` into
`data.stderr` and counts `data.policy_refusals` (`bench/factorio/adapter.py`,
`_do_run`). The OOLONG adapter sets `data.last_result.status` to `refused`
and counts `data.refusals`. The template does the same in `_refuse`:
`last_result` becomes `{"status": "refused", "detail": ...}` and `refusals`
increments. Raising from `step()` instead aborts the action before the
mutation is written: nothing is spent, and the error reaches the CLI.

Determinism and replay. On a local run, `assay start` in an existing
directory replays every recorded action through a fresh session and compares
each observation with the one recorded. A difference is
`LOCAL_REPLAY_DIVERGED` and the run stops. So the world must be deterministic
given the same `seed` and action sequence. The template derives its door
codes from the seed (`room_code`). If your world needs randomness, seed it
from `config["seed"]`.

The world id rule (`core.normalize_game_id`): any non-empty string up to 64
characters with no whitespace, control characters or path separators, stored
and shown as given. It labels the run (the socket and anchor paths hash the
run directory, never the id). A benchmark adapter may read it to pick the
instance, as the ARC adapter does with the game id and the OOLONG adapter
with the pack id.

## 5. Write your registry

Copy `examples/new_world/registry.json`, which sets every optional key so the
shape is visible, and delete what you do not need. The schema is validated
key by key by `registry.validate_registry`, and unknown keys are refused.
From the architecture document's section 2.1:

- `actions`, required, non-empty. Each action has a `name` matching
  `^[A-Za-z][A-Za-z0-9_]{0,31}$` (upper-cased, `RESET` refused because it is
  built in) and `params`, each parameter `{"type": "int"|"float"|"str",
  "min"?, "max"?, "enum"?}`. Every parameter is required on the command line
  as `pname=value` and is coerced and bounds-checked before spend. The
  template's `TURN delta=<int -3..3>`, `PEEK what=<code|door>` and
  `SIREN volume=<float 0..1>` show the three types.
- Per-action flags: `destructive: true` refuses without `--declare
  worst_case=... --declare recovery=...` and is banned inside batches
  (`ALARM`). `approval: true` is default deny, one owner-granted use at a time
  (`DRILL`). `liveness: "live"` with `rehearsal_quota: N` refuses until an
  imported sim-binding run shows N graded rehearsals or the owner waives it
  (`SIREN`). `description` is admissible, untrusted text rendered as data.
- `budget.actions`, the hard cap, checked before every spend with the planned
  count, so a batch that would cross it is refused whole. No cap means no
  cap. `budget.usd`, the dollar cap fed by `assay spend report`.
- `goal.text`, the standing goal shown in every status until the world
  reports WIN.
- `batching.hand_cap`, the cap on hand-written batches, default 3 when
  absent (the batching law), `null` for no cap. A replay-fit model lifts it.
- `notes_cap`, the size of `.assay/NOTES.md` in characters, default 16000.
  Past twice the cap, paid actions refuse until it is trimmed.
- `zero_prior`, default false. True withholds every description.
- `modules`, paths to external module files, pinned at start under a
  manifest. `module_modes`, `off`, `advise` or `block` per module name,
  built-ins included.
- `secrets`, environment variable names whose values are redacted from every
  agent-supplied string before it is written. Names only, never values.
- `observers` and `control`, accepted and journaled, no kernel behavior in
  1.2.0. A world adapter may read `control` from the pinned registry as its
  own settings (the OOLONG adapter's `bank_mode`).
- `mode_note`, free text shown in status as data.
- `gate`, default `required`, which is the rule: every paid action needs a
  prediction. `optional` and `off` are the control-arm modes for experiments.
  Under `optional` a bare act is journaled UNGATED. Under `off` no prediction
  is accepted at all and every paid action is journaled UNGATED. Under both,
  the audit marks the run invalid for scoring. Neither is scorable.

## 6. Run it as an operator

The owner token. Minted at start on registry runs (`agenda.mint_owner_token`),
only its sha256 stored. Printed once, or written to a file with
`--owner-token-file PATH` or `ASSAY_OWNER_TOKEN_FILE`. The file must lie
outside the run directory. Keep it out of the agent's reach. It authorizes:

```bash
assay goal ratify 2 --token TOK                 # accept the agent's proposal #2
assay approve DRILL --token TOK                 # one use of an approval-gated action, expires in 600 s
assay waive SIREN --token TOK --because "..."   # journaled waiver of a rehearsal quota
assay module install PATH --token TOK           # add a behavior module mid-run
```

Machine-readable output. Every command takes `--json`: exactly one JSON
document on stdout and nothing on stderr. For `status`, `view`, `audit`,
`act`, `commit`, `reset`, `channel list` and `module list` it is the
command's result record (`docs/ARCHITECTURE.md` section 7.3: the `Status`
record, the view record, the audit report, the receipt, the channel list, the
module list); for every other command, `start` and `stop` included, it is
`{"lines": [...]}` with the lines the command would have printed; on a
refusal it is the error object `{"code", "kind", "message", "hint",
"detail"}` alone, with the exit status the prose form would have given (2,
3, 4 or 5 by kind). The prose output without the flag is unchanged.

The spend feed. The kernel cannot see the LLM bill, so the launcher posts it:
`assay spend report --usd 4.20 --tokens 91000 --id turn-7`. Entries are
idempotent by `--id` (the last entry per id wins), and `budget.usd` is as
fresh as the feed.

Anchors. Every 25 events and on WIN the chain head is appended to a file
outside the run, under `ASSAY_ANCHOR_DIR` or `~/.assay/anchors/`
(`integrity.anchor_file`). The file is pinned in `config.json` at start
(`anchor_file`), so a later shell with a different `ASSAY_ANCHOR_DIR` still
anchors to and audits the same file. Status shows it on every registry run as
`ANCHORS | <file> | n anchor(s), last e<id>` (or `none yet`). A failed write
is journaled and shown on that line, never swallowed. If the environment
disagrees with the recorded file, `assay audit` reports
`anchor_env_mismatch` as information, not as a verdict.

Stop and resume. `assay stop` sends SIGTERM to the daemon, which finishes any
step in flight, journals it, replies, and exits. The daemon is identified by
process, never by the stored pid alone (`broker.find_daemon`: a live process
running the `broker_server` module with `--run-dir` naming this directory),
so a reused pid after a reboot is never signalled. `assay start` resumes. If
the daemon is alive and ours but does not answer (a slow world inside a step,
or hung), start refuses rather than killing it:

```
ERROR | DAEMON_BUSY | the environment owner is busy or hung (pid N, started T)
NEXT | wait and rerun `assay start`, or run `assay stop` (it exits after the current step)
```

If `.assay` was removed by hand while the daemon lived, a fresh start refuses
and names `assay stop`, which works without any run state.

Directory hygiene. One directory is one run. Never delete `.assay` by hand
(stop first, then remove the whole directory). `.assay/` is in `.gitignore`,
because run state is never committed. Resume is the same `start` command.

Interpreter binding. The daemon runs the interpreter of the CLI that started
it, recorded in `config.json` as `python`. A resume from a different
interpreter prints a line beginning `WARNING | interpreter changed:` that
names both interpreters and `ASSAY_PYTHON`. For a benchmark world, install
the package into the venv that holds the adapter's dependencies and point
`ASSAY_PYTHON` at it. The ARC-AGI-3 adapter has such an interpreter on
record, `bench/arcagi/requirements.txt` (Python 3.14.5 and four pins), made
once outside the repository; the Factorio and OOLONG adapters need nothing
beyond the harness.

`assay doctor` works with or without a run in the directory and checks all of
the above in one voice. `assay module list` shows every active module with
its mode, origin, constitution paragraph and telemetry.

## 7. Run an agent on it

The agent-facing manual is `CONSTITUTION.md`. The order is the operator
protocol of `docs/ARCHITECTURE.md` section 8.6: the operator starts the run
and holds the owner token, and the agent's session begins after that.

1. The operator, in a shell the agent does not share, starts the run:

   ```bash
   mkdir <run-dir> && cd <run-dir>
   assay start WORLD_ID --adapter <adapter.py:factory> --registry <registry.json> \
       --owner-token-file <tokens>/WORLD_ID.token
   ```

   The token file lies outside the run directory (`start` refuses a path
   inside it) and outside anything the agent's session reads, with mode
   0600; the line printed is `OWNER TOKEN | written to PATH (mode 0600)`,
   never the token. In a Claude Code session the hooks of section 8.4 (#11)
   are installed at this point, with the token file and the policy file
   among the refused paths.
2. The agent's session starts in `<run-dir>`, with the daemon already up.
   The integration is one prompt with four parts: read `CONSTITUTION.md`
   completely, begin with `assay status` in the run directory, solve for the
   goal, and never touch the world except through `assay`. The agent never
   runs `start`, and the token never appears in its transcript.
3. Ratifications, approvals and waivers are the operator's, from the
   operator's shell: `assay goal ratify N --token "$(cat
   <tokens>/WORLD_ID.token)"`, and `approve` and `waive` the same way
   (chapter 6). A resume after an interruption is the operator's too: the
   same `start` command in the same directory replays the journal and keeps
   the token, and the agent begins again at `status`.

The published benchmark runs were played before this order existed: the
agent ran `start` itself and held the owner token in its own terminal, so
"the agent proposes, the owner ratifies" was nominal there. The records say
what they say; the protocols under `bench/` describe the operator-first
order for the runs to come, and `tests/test_operator_start.py` runs the
counter example in it.

The channel pattern, worked. The Factorio M2 runs declared their channels
first and claimed every action with a channel form. The irongear run declared
seven path channels at its first event:

```bash
assay channel declare tick     --path tick
assay channel declare ents     --path entities_total
assay channel declare refusals --path policy_refusals
assay channel declare prod     --path throughput_corroboration.producer_present
assay channel declare wins     --path windows_complete
assay channel declare gears    --path target_produced_total
assay channel declare auto     --path target_automated_total
```

and then claimed, verbatim from the published journal:

```
e1 RUN  --predict "change; ch tick = 0; ch ents = 0; ch refusals = 0"
e2 RUN  --predict "change; ch tick = 180; ch ents delta sign +; ch refusals = 0; ch prod = False"
e4 RUN  --predict "change; ch tick = 360; ch ents = 27; ch refusals = 0; ch prod = True"
        graded SURPRISE: ch ents = 27 -> ch ents = 18
e8 WAIT --predict "verify:checks/first_window.py; ch tick = 3600; ch wins = 1; ch prod = True"
e9 WAIT --predict "win; level+1; verify:checks/holdout.py; ch tick = 7200; ch wins = 2; ch gears delta >= 16; ch prod = True"
```

Three claim forms carry the pattern. Equality, `ch tick = 360`, pins a
reading. Delta, `ch ents delta sign +` and `ch gears delta >= 16`, pins a
change. Crossing, `ch automated crosses 16 from below` in the ironplate run,
pins a threshold. The circuit run added a tolerance, `ch lastrate = 20 ± 5`.
Every miss carries the machine's counter-fact, as e4 shows: the agent
predicted 27 entities and the world reported 18.

One mistake to avoid. The dotted path walks the object the adapter put under
`data`, so the reading is declared as `--path tick`, not `--path data.tick`.
The circuit run's first event made that mistake and earned
`UNGRADABLE: key 'data' not in observation path 'data.tick'`.

Frame worlds have no dict to walk, so their channels are extractor files:
`assay channel declare NAME --file extractor.py` with
`def extract(obs) -> value`, content-hashed, stored under `.assay/channels/`,
and run only in the sandbox against the observation view (`state`,
`levels_completed`, `win_levels`, `available_actions`, `frames`). The 25
ARC-AGI-3 runs declared up to sixteen such channels each.

The sandbox is `sandbox-exec` on macOS and `bwrap` (bubblewrap) on Linux:
agent code (verifiers, extractors, the world model) runs from a scratch copy
with no network, no fork and no path into the run directory, under CPU,
file-size and wall-clock limits, and the 512 MB memory limit is Linux-only,
since macOS refuses an address-space limit. Without either tool the process
limits alone apply, `assay doctor` prints `sandbox | process isolation only
(no sandbox-exec or bwrap)` and `config.json` records that mode at start; on
Linux, install bubblewrap. A stock Ubuntu 24.04 restricts unprivileged user
namespaces through AppArmor, which bwrap needs: `doctor` then shows the probe
failing with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`
until `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (or an
AppArmor profile for bwrap) lifts the restriction. `ASSAY_SANDBOX=process-isolation-only`
forces the fallback on any machine, which is how the suite tests it; `doctor`
then says `forced by ASSAY_SANDBOX`, and `start` refuses any other value. The
daemon records the mode it runs under in `broker.json`, and `doctor` warns
when it differs from the one `config.json` recorded at the run's creation.

### A separate user for the daemon

An option, not something the kernel implements. Normally the agent and the
daemon share one uid, so nothing on disk is protected from the agent by
permissions; the boundary is detection by the daemon, the hooks, and the
operator keeping the token and the launch out of reach
(`docs/ARCHITECTURE.md` section 8.1). The one real boundary is a dedicated
user that owns `.assay/` and runs the daemon, with the agent in a group that
reads the state and reaches the socket. The steps below are operator
instructions, with `assay` as the group, `assayd` (`_assayd` on macOS) as the
daemon's user and `agent` as the agent's user. They follow the code paths
named in brackets; the suite does not run them.

1. The user and the group, once per machine. Linux:

   ```bash
   sudo groupadd assay
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin --gid assay assayd
   sudo usermod -aG assay agent      # the agent's user logs in again to pick the group up
   sudo install -d -o assayd -g assay -m 2750 /opt/assay /opt/assay/anchors
   sudo install -d -o assayd -g assay -m 2700 /opt/assay/tokens
   ```

   macOS (a role account: the name starts with an underscore, the uid lies
   in 200 to 400):

   ```bash
   sudo dseditgroup -o create assay
   sudo sysadminctl -addUser _assayd -fullName "ASSAY daemon" -UID 300 -shell /usr/bin/false -roleAccount
   sudo dseditgroup -o edit -a _assayd -t user assay
   sudo dseditgroup -o edit -a agent -t user assay
   sudo install -d -o _assayd -g assay -m 2750 /opt/assay /opt/assay/anchors
   sudo install -d -o _assayd -g assay -m 2700 /opt/assay/tokens
   ```

   The repository and the interpreter that serves the daemon must be
   readable by the daemon's user.
2. The run directory, once per run: group `assay`, setgid so everything
   created inside carries the group, group-writable (the daemon creates
   `.assay/` in it; the agent writes its verifier files and `model.py`
   beside it), sticky so that no group member can rename or remove what
   another created, `.assay/` included:

   ```bash
   sudo install -d -g assay -m 3770 <run-dir>
   ```

3. The start, as the daemon's user, from the run directory, with the anchors
   and the token under `/opt/assay` (the anchor file is pinned in
   `config.json`, so the agent's `audit` reads the same one):

   ```bash
   cd <run-dir>
   sudo -u assayd env ASSAY_ANCHOR_DIR=/opt/assay/anchors assay start WORLD_ID \
       --adapter <adapter.py:factory> --registry <registry.json> \
       --owner-token-file /opt/assay/tokens/WORLD_ID.token
   ```

4. After the first start, the files the agent's commands write. `.assay/`
   gets group write and the sticky bit, the `/tmp` arrangement: the agent's
   commands can create their own files there (`channels.json`, the model fit
   and plan, `audit.json`: `core.atomic_json` writes a temporary file beside
   the target and renames it) and can neither delete nor rename over the
   daemon's. The lock and the activity log, which every command appends to
   (`core.run_lock`, `core.append_jsonl`), the proposals file, which the
   agent's `propose` and the operator's `ratify` both append to, and
   `NOTES.md`, the agent's file, are group-writable:

   ```bash
   cd <run-dir>
   sudo -u assayd chmod 3770 .assay
   sudo -u assayd touch .assay/activity.jsonl .assay/proposals.jsonl
   sudo -u assayd chmod g+w .assay/run.lock .assay/activity.jsonl .assay/proposals.jsonl .assay/NOTES.md
   ```

5. After every start, a resume included, the two things a start rewrites.
   The broker token, which the client sends with every request
   (`broker.start_broker` writes it with mode 0600), becomes group-readable.
   The socket, which the daemon binds with mode 0600 under a path that
   carries its own uid (`core.RunPaths.socket`, `broker.serve`), gets the
   group (it is born with the daemon's primary group, not the directory's)
   and group write, and is linked from the path the agent's client computes
   with the agent's uid; `assay doctor` prints each user's path on its
   `socket path` line:

   ```bash
   cd <run-dir>
   sudo -u assayd chmod g+r .assay/broker.token
   daemon_socket=$(sudo -u assayd assay doctor | sed -n 's/.*socket path \([^ ]*\).*/\1/p')
   agent_socket=$(sudo -u agent assay doctor | sed -n 's/.*socket path \([^ ]*\).*/\1/p')
   sudo -u assayd chgrp assay "$daemon_socket" && sudo -u assayd chmod 660 "$daemon_socket"
   sudo -u agent mkdir -p "$(dirname "$agent_socket")" && sudo -u agent ln -sfn "$daemon_socket" "$agent_socket"
   ```

6. The agent's session starts in `<run-dir>` as `agent`, at `assay status`.
   The operator's commands (`goal ratify`, `approve`, `waive`, `module
   install`, `stop`, the resume) run as the daemon's user, with the token
   read from its file: `sudo -u assayd assay goal ratify N --token "$(sudo
   -u assayd cat /opt/assay/tokens/WORLD_ID.token)"`.

What it gives, and what it does not. The journal, the chain, the mutation
log, the pinned registry and configuration, the owner hash, the module
manifest and files, the verifier copies, the receipts and the anchors are
then the daemon's alone: the agent reads them and cannot change them, even
while the daemon is stopped. What the agent's commands write today stays the
agent's to write: the activity log, the channel declarations, the proposals,
the model fit and plan, and `audit.json` (rewritten by whoever runs `audit`,
so under the sticky bit the operator audits after the run or on a copy). The
owner operations move into the daemon with the design of section 7.2; since
#20 the daemon holds the run in memory and checks those files against what it
holds before every paid action, refusing on a difference with
`TAMPER_DETECTED` and saying so on the status INTEGRITY line while it lives;
the arrangement gets stronger with each.

### Many runs at once: the evaluation runner

The order above, scaled to a design: `tools/eval/` runs worlds times arms
times seeds from one job file, and for every job does what this chapter
describes, in the runner's own process (the operator's `start` with the
owner token file outside the run directory, then the player's session in
the run directory, with the daemon already up), at most `concurrency`
players at a time. It writes one JSONL row per run (paid actions, the win,
the receipts by outcome, tokens and dollars per action, the chain head) and
a summary with bootstrap intervals over the seeds of each arm, resumes an
interrupted job through the kernel's own `start`, never reruns a finished
one, and prints every command it would run under `--dry-run` without
writing anything. `tools/eval/README.md` is the manual; the E1 and E1b
designs of the paper are its worked examples under `bench/arcagi/jobs/`.

## 8. Modules

Using them. Seven built-ins ship, all at advise, and `module_modes` in the
registry sets `off`, `advise` or `block` per name:

- `wall_spend`: too long on one progress unit without progress, model offline.
- `miss_streak`: repeated prediction misses mean the notes story is wrong.
- `null_forensics`: a predicted change that observed nothing wants the raw
  observation read before the hypothesis is closed.
- `park_with_test`: a reset should leave a re-entry test in the notes.
- `specificity`: a majority of coerced free-text claims earns nothing (named
  `sharpness` before 1.2.0; `module_modes` still accepts that name).
- `hazard`: an action class whose outcome entered a loss state or dropped
  progress demands `worst_case=` and `recovery=` on its next use.
- `coverage_audit`: the untried and never-productive actions on this unit, a
  stall, a halt on re-issuing the move that just graded FALSE (demand
  `revised=`), a halt on three identical failing moves, and a conclusion gate
  keyed on declarations such as `impossible=` or `absent=` (demand
  `coverage_audit=`). On a frame world it also names the grid regions a point
  action never probed.

`assay module list` prints each active module with mode, origin,
constitution paragraph and telemetry. In block mode an unmet demand refuses
with `ERROR | MODULE_DEMAND | MODULE name | declaration demanded before this
action: field: why` and `NEXT | repeat the command with --declare
"field=<text>" (one flag per field); any named, non-empty text unlocks the
action` (`--declare "field=value"` on `act`, `commit` and `reset`).

Writing one. The contract, from the architecture document's section 2.5:

```
NAME: str                                     unique, lowercase
CONSTITUTION: str                             one paragraph of way-of-thinking text
MODE: "advise" | "block"                      the default, registry module_modes overrides
trigger(view, pending) -> str | None          advisory message when it fires
demand(view, pending) -> dict[str, str] | None {field: why}, structural
observe(view, event) -> None                  optional, learn from outcomes
telemetry(view) -> dict                       free counters
```

`view` is `modules.ModuleView(run)`: `view.events` (the journal as `Event` records, read-only), `view.registry` (read-only), `view.paths`, and `view.record(kind, **fields)` and `view.hazards()` for what a module persists. The events and the claims are records, read by attribute: `event.predict_ok`, `claim.kind`; `pending["claims"]` holds `records.Claim` records, so a module written against 1.1 that does `claim["kind"]` gets a `TypeError` and reads `claim.kind` instead. `pending` is the
action about to be taken, `{"kind": act|commit|reset, "name", "params",
"claims", "declares"}`, or `None` at status time. Ship at advise. Demands are
for checkable structure (named, non-empty fields), never for confidence, and
declaring always unlocks. A module makes no LLM calls and names no world.
Expose the object as `MODULE` in the file.

Installing one. At start, list the file under `modules` in the registry and
it is copied into `.assay/modules/` with a manifest entry (name, file,
source, sha256, origin). Mid-run, with the daemon running, the owner runs
`assay module install PATH --token TOK`: the daemon checks the token, the
contract and the NAME (a clash with a built-in is refused), adds the manifest
entry, journals `module_installed` and loads the module for the next action;
with the daemon stopped the command refuses and names `assay start`. Only
listed files whose hash still matches are loaded. A file written into
`.assay/modules/` by hand, or a pinned file edited afterwards, is ignored and
named in status: `MODULES | 1 file(s) in .assay/modules ignored ...`.

## 9. Verify and publish

`assay audit` recomputes everything from the artifacts (`integrity.audit`):
contiguity of event ids, the stored chain against the recomputed one, the
anchored heads against the journal prefix, UNGATED events (a paid non-RESET
event carrying no prediction and no grade), recovered orphans, and the
verdict. One ungated event makes the run `INVALID FOR SCORING`. The chain
is `head_0 = sha256("assay-chain-v1")`, `head_n = sha256(hex(head_{n-1}) ||
line_n)` over the raw journal lines, and the anchors are its heads written
outside the run.

The independent checker is `verify/assay_verify.py`. It shares no code with
the harness and reads only the journal:

```bash
python3 verify/assay_verify.py <run-directory>
python3 verify/assay_verify.py <run-directory> --expect-head <sha256>
```

To publish a run, publish the journal (`.assay/events.jsonl`) and its chain
head. Anyone holding the journal can then verify it is byte-identical to the
one whose head was published. The evidence packs under `evidence/` are the
worked example, and `evidence/verify_all.py` checks every one of them.

What the public contract freezes, from the architecture document's section 5:
the journal field names, including the historical `levels_completed`,
`win_levels`, `level_before`, the state values `NOT_FINISHED`, `WIN` and
`GAME_OVER`, the claim syntax, the grade `actual` texts, the host channel
names `goal`, `level` and `budget_remaining`, the chain seed and rule, the
ungated rule and the `RESET` exemption, the `game_id` and `source_game` keys,
the activity kinds, the receipt outcome tokens and the state-directory layout.
Display strings are not frozen, and `src/assay/words.py` is the law for them.

## 10. Troubleshooting

Each item is the message you see and what to do. Every refusal is one line on
stderr, `ERROR | CODE | message`, followed by `NEXT | hint` when the error
names a next step (and by the claims table when the error carries it); the
exit status says what kind of error it was: 2 for a request that is wrong or
refused by rule, 3 when the world failed or refused at the kernel boundary
(`WORLD_ERROR`, the world's own refusal, or `OBSERVATION_INVALID`, an
observation the kernel cannot read: the adapter is broken), 4 for a bug or a
corrupt file, 5 when the run can no longer be scored or continued.
`docs/ERRORS.md` lists every code with its kind and meaning.

- A missing or mistyped adapter file:

  ```
  ERROR | ADAPTER_SPEC | adapter file not found: X (looked in A and B); pass the file's path, absolute or relative to the run directory, as /path/file.py:factory
  ```

  The file spec is resolved against the run directory, then the working
  directory, before anything is spawned. Pass the path as the message says.
- `adapter 'm:factory' is not importable by /path/python: ModuleNotFoundError
  ...`. The adapter was dry-imported by the interpreter that will serve the
  daemon and a dependency is missing there. Install the adapter's
  dependencies into that interpreter, or set `ASSAY_PYTHON` to the venv that
  has them.
- `invalid world id 'my world': it contains whitespace. A world id is any
  non-empty string up to 64 characters with no whitespace, control characters
  or path separators, kept as given`. Choose an id that fits the rule.
- `assay act --help` prints the general claim table first and the grid forms
  under a section headed `FRAME WORLDS ONLY`. On a dict run those forms are
  refused by name before any spend.
- The owner token appeared in the agent's terminal. Start the run from the
  operator's shell with `--owner-token-file PATH` (or `ASSAY_OWNER_TOKEN_FILE`)
  and hand the agent a directory that is already started.
- Stopping. `assay stop` is the only way to stop a daemon. It signals only a
  process identified as this run's daemon, so a stale pid in `broker.json`
  is marked stale and never signalled. It never sends SIGKILL.
- Resume on a slow world. If `assay start` says `the environment owner is
  busy or hung (pid N, started T)`, the daemon is inside a step. Wait and
  rerun, or `assay stop` and it exits after the step. If your world's steps
  are long, set `ASSAY_BROKER_TIMEOUT` (seconds) so the client's wait and
  the liveness probe allow for them.
- A spend that is in the mutation journal but not in the timeline. Offline
  commands never recover it, and `assay audit` reports it:

  ```
  AUDIT | 1 spend(s) in the mutation journal not yet in the timeline [n] (a step in flight, or a crash between spend and record; `assay start` recovers them once the daemon is gone)
  ```

  Recovery happens only at `assay start`, only once the daemon is confirmed
  dead or absent. The recovered event carries its prediction and grade,
  regraded from the record's claims against the stored response (a claim
  with an `@within` window grades UNGRADABLE, since the step's duration is
  lost), and the run stays CLEAN; a record written before 1.2.0 or by a
  model-plan step is recovered UNGATED and the audit says why.
- Anchors. The `ANCHORS |` status line says where the heads go and when the
  last one was written. `start` prints a `WARNING | ANCHORS | ... NOT
  WRITABLE` line when the directory cannot be written; set `ASSAY_ANCHOR_DIR`
  before the first anchor is due. `anchor_env_mismatch` in the audit means
  the environment names a different directory than the run recorded. The
  recorded file is the one audited.
- Interpreter drift. `WARNING | interpreter changed: ...` on resume means
  this shell's Python is not the one the run started with. Set
  `ASSAY_PYTHON` to the original if the adapter's dependencies live there.
- `ERROR | INTERNAL | <type>: <message>` followed by `NEXT | traceback in
  .assay/last_error.txt; report it with the command that produced it`, exit
  4. A bug or a corrupt file. The traceback is in that file. Report it with
  the command that produced it.
- A contributor's first `pytest`. The suite redirects `ASSAY_ANCHOR_DIR` and
  `XDG_CACHE_HOME` under the pytest temp root and stops its own daemons when
  the session ends, so nothing lands under your home directory.

## 11. The conformance checklist

For a new world, fill one line per component, with one of three answers:
present, optional and unused, or world-specific and where it lives. This is
the column the world adds to the table in `docs/ARCHITECTURE.md` section 4.

- Registry file, actions and parameter types, budget, batching, goal and
  mode_note, secrets, the per-action flags, zero_prior, modules and
  module_modes, gate.
- Adapter file, observation shape, `finalize`, `public_info`, refusals
  through the observation, world policy before execution, determinism,
  the `session` declaration and the `replay` hook, remote mode, any
  workaround of the world id rule.
- Runtime: owner token, approvals and waivers, destructive declarations, usd
  cap and spend feed, carryover import, anchors and chain.
- Constitution: `CONSTITUTION.md` unchanged, plus any world reference handed
  beside it.
- Modules: the built-ins, hazard tags observed, external modules.
- Channels: host channels claimed, declared channels and their form, claim
  kinds used.
- Verifiers and the world model.
- Journal shape, audit verdict, the frame extra (frame worlds only).
- Kernel imports from the world: none, always.

The executable form is `tests/test_conformance.py`: the kernel imports
nothing from a world and names none, every adapter exposes
`factory(root, config)` and a session with `observation` and
`step(action, data, reasoning)`, the README quickstart passes verbatim, and
the template passes the whole loop including the owner operations. Point
its `ADAPTERS` table at your adapter and run the template test against your
world to tick the list.
