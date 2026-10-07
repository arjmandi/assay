# ASSAY architecture: the component model

This document is the source of truth for how an ASSAY run is put together. It is
written from the code on `main` at 6ea56e4 and after, and from the recorded
runs of the three benchmark worlds, and it is the document the onboarding guide
and the paper's architecture section are derived from, never the other way round.
References are to files and function names under `src/assay/` so they stay
correct as line numbers move.

Sections 6 to 8 are the 1.2.0 design notes; they describe the target the milestone builds toward and the sections before them describe the code they start from.

Eight components make up every run. Each has one owner, one contract, a list of
what is required and what is optional, a list of what must never be put there,
and its extension points. The same eight components describe ARC-AGI-3,
Factorio, OOLONG and the counter example. The conformance table in section 4
shows, per world, which parts are present, which are optional and unused, and
which are world-specific and where they live.

## 1. The shape of a run

One directory is one run. The kernel keeps all run state under `.assay/` inside
it (`core.RunPaths`). Two processes touch that state:

- the **daemon**, the package module `assay.broker_server` running `broker.serve`, which owns the
  world session for the life of the run. It loads the run strict
  (`run.Run.load`: a contiguity problem or a diverged chain refuses the start
  with `CHAIN_DIVERGED` and rewrites nothing), writes event 0 on a fresh run
  (the first observation, the session's `public_info` into `config.json`, the
  `START` line) before it binds the socket and reports READY, and from then on
  is the only writer of `events.jsonl`, `chain.json`, `mutations.jsonl` and the
  anchor file: every paid action is executed here, behind the gate, appended
  through `run.append` and journaled before the reply leaves the socket. The
  daemon is the only process that spends. It holds that one run for its life
  (#20): the modules load once from the manifest it holds, every request
  handler receives the held run, each append advances the chain in memory and
  writes `chain.json`, the next mutation id is one past the held log's, and
  before every paid action it verifies the files on disk against the copies
  it holds (`run.verify_disk`, section 8.3), refusing with `TAMPER_DETECTED`
  on a difference, for that action and every later one until it is stopped.
- the **CLI**, `assay.cli.main`, a stateless display client on registry runs. At
  `start` it writes `config.json`, the registry copy, the owner file and the
  notes file before it spawns the daemon, and never writes `config.json` after.
  Every other command loads the run once, lenient (the readers report what the
  loader finds), validates what it can before talking to the daemon, sends the
  gated operation over a Unix socket (`broker.broker_gated`), and prints the
  receipt. Its one append to the journal is `reconcile` at start, with no
  daemon alive, which recovers a spend the daemon journaled but never recorded,
  with its prediction regraded from the record (section 6.5).

Both processes read the journal through one object, `run.Run` (section 6.3):
the configuration, the registry, every event as a `records.Event`, the chain
head, the mutation log, the manifest and the owner hash, loaded once per
process and passed to every function below the entry points. The small files
beside the journal (the activity log, `channels.json`, the readings cache, the
verifier statistics, hazards, aggregates, the agenda files, the notes) stay on
disk and are read on demand; each has one writer.

Three trust classes decide where code may run:

- **kernel code** is trusted and runs in the daemon and the CLI,
- **pack and module code** (adapters, behavior modules) is installed by the
  human, runs in the daemon, and is trusted like the kernel,
- **agent-authored code** (verifiers, channel extractors, world models) is
  untrusted and runs only in the sandbox (`sandbox.run_program`, behind
  `verifiers.run_verifier`, `channels._run_extractor` and
  `model._run_sandbox`): a scratch copy of the program, `python -I`, an empty
  environment, CPU, file-size and process limits (no fork), a wall clock, and
  the platform's jail, `sandbox-exec` on macOS or `bwrap` on Linux, which
  denies the network and every path but the interpreter, its packages and the
  scratch directory, the run directory included. The 512 MB memory limit is
  Linux-only, since macOS refuses an address-space limit. Where neither tool
  exists the limits alone apply, `assay doctor` says `sandbox | process
  isolation only (no sandbox-exec or bwrap)`, and `config.json` records the
  mode at start (section 8.5).

The kernel law: **the kernel makes no LLM calls.** Every grade, refusal, meter
and verdict is deterministic code over the journal. That is also what makes
replay possible: a local run resumes by replaying its own journal through the
adapter (`broker._replay_local_session`) and refusing to continue if the world
no longer reproduces a recorded observation.

The public contract the kernel must keep honoring is in `verify/`:
`JOURNAL_SPEC.md` (`assay-journal-v1`, the event schema, the chain
rule, the ungated rule, the verdict) and `CLAIM_GRAMMAR.md` (the claim forms and
what a graded claim asserts). Section 5 lists the identifiers that are frozen by
that contract.

## 2. The components

Each section below has the same six parts: role, owner, contract, required
versus optional, never here, extension points.

### 2.1 Registry

**Role.** The world's contract: what the agent is allowed to do, under which
caps and flags, toward which goal. Pure declaration, no code. The kernel
enforces it before anything is spent.

**Owner.** The operator. Pinned per run: `assay start --registry FILE` validates
the file (`registry.load_registry_file`, `registry.validate_registry`), copies
the canonical form to `.assay/registry.json`, and records `registry_hash` in
`config.json`. A later `start` with a different registry is refused
(`cli._start`). Every run has one: `--registry` is required since 1.2.0, and
a directory that owns a run started without one (before registries existed)
can be inspected but not resumed.

**Contract.** The JSON schema in the `registry.py` module docstring, validated
key by key. Top-level keys: `actions` (required), `budget`, `goal`, `batching`,
`notes_cap`, `zero_prior`, `modules`, `module_modes`, `secrets`, `observers`,
`control`, `mode_note`, `gate`. Unknown keys are refused. Per action: `name`
matching `^[A-Za-z][A-Za-z0-9_]{0,31}$` (upper-cased, `RESET` refused because it
is built in), `params` (each `{"type": int|float|str, "min"?, "max"?, "enum"?}`,
every registered parameter is required on the command line and coerced and
bounds-checked before spend by `registry.parse_registry_action`),
`destructive`, `approval`, `liveness` (`live|sim`), `rehearsal_quota` (live
only), `description` (admissible, untrusted, rendered as data, withheld under
`zero_prior`).

Defaults when a key is absent: `batching.hand_cap` is 3 (the batching law,
`registry.hand_cap`), `notes_cap` is 16000 characters (`registry.notes_cap`),
`zero_prior` is false, `gate` is `required`, no action cap means no cap, no
`usd` cap means no spend ceiling. `budget.actions` is checked by
`registry.check_budget` before every spend with the planned count, so a batch
that would cross the cap is refused whole.

**Required.** `actions`, non-empty. Everything else is optional.

**Optional.** All other keys. Two are declared-only in 1.2.0 and journaled
without kernel behavior: `observers` and `control` (`registry.validate_registry`,
honest gap). A world adapter may read `control` from the run's pinned copy
as its own settings, pinned and hashed with the rest (the OOLONG adapter's
`bank_mode`).

**Never here.** Action semantics (descriptions are data, never instructions, and
the constitution says so to the agent). Credentials (`secrets` lists environment
variable names whose values are redacted at the journal boundary, never the
values). World code. Anything that changes between resumes.

**Extension points.** `description` text per action, `mode_note` free text,
`modules` (paths to behavior module files, pinned at start), `module_modes`
(per-module `off|advise|block`), `gate` (the control-arm switch: under
`optional` an unpredicted act is admitted, journaled UNGATED with the marker
`gate_optional: true`, and the run stays invalid for scoring, `registry.gate_optional`,
`integrity.audit`).

### 2.2 Adapter

**Role.** The world plug. The one piece of code ASSAY touches a world through.
Its `observation` property is the observer and its `step()` is the actuator.
They share one object because in a turn-based world an action and its settled
observation are one transaction.

**Owner.** The world. Pack-tier trust: the adapter is named by the operator at
start (`--adapter module:factory` or `--adapter /path/file.py:factory`,
`broker._import_factory`, relative paths resolve against the run directory) and
is imported inside the daemon.

**Contract.** `factory(root: Path, config: dict) -> session`. `config` is the
run's `config.json`: `game_id` (the world id), `seed`, `mode`, `adapter`,
`registry` (bool), `created_at`, `harness`, `binding_hash`, `registry_hash`.
The session exposes:

- `observation` (property): the current world state, one of two shapes.
  A **dict world** returns `{"state", "levels_completed", "win_levels",
  "available_actions", "data": {...}}` where `data` is any JSON object
  (`core.normalize_observation`, dict branch: `data` must be a JSON object,
  action names are stringified and sorted). A **frame world** returns
  `"frame"` or `"frames"`, a list of 2-D integer grids with values 0 to 15,
  either as a mapping or as an object with those attributes (the ARC client's
  frame object), normalized to hex-encoded rows (`core.grid_to_rows`).
- `step(action, data, reasoning) -> observation`: apply one validated action.
  `data` is the typed parameter dict the registry validated, or `None`.
  `reasoning` is the agent's journaled prediction context, already redacted,
  for the world to log or ignore, never to obey.
- `finalize()` (optional): called once by the daemon on the first observation
  whose state is `WIN` (`broker.serve`, `direct_stepper`). An exception here is
  reported as a finalization warning on the receipt, after the action is
  already journaled.
- `public_info` (optional property): a dict stored in `config.json` at start.

Lifecycle values the kernel reads: `state` is `NOT_FINISHED`, `WIN` (terminal,
triggers `finalize` and ends the daemon) or any other string, of which
`GAME_OVER` has meaning (reset needs no reason, the hazard module tags the
action class). `levels_completed` and `win_levels` are the host progress pair,
`win_levels: 1` for a world with no intermediate milestones.

**Determinism and replay.** On a local run, `assay start` in an existing
directory replays every recorded mutation through a fresh session and compares
each observation to the one recorded (`broker._replay_local_session`). A
difference is `LOCAL_REPLAY_DIVERGED` and the run stops. The adapter must
therefore be deterministic given the same `seed` and action sequence. A remote
run (`--mode competition`) is never replayed and expires after 15 idle minutes.

**Refusals.** A world that refuses an action for its own reasons reports the
refusal **through the observation**, so the spend is journaled as evidence. The
Factorio adapter writes `POLICY_REFUSED | reason` into `data.stderr` and counts
`data.policy_refusals` (`bench/factorio/adapter.py`, `_do_run`), the OOLONG
adapter sets `data.last_result.status` to `refused` and counts `data.refusals`.
Raising from `step()` instead aborts the action before the mutation is written:
nothing is spent and the error is relayed to the CLI.

**Required.** `factory`, `observation`, `step`.

**Optional.** `finalize`, `public_info`, world-specific policy before execution
(the Factorio AST screen `screen_program` and its sealed instance are the worked
example: they live entirely in the adapter).

**Never here.** Nothing crosses from an adapter into the kernel: the kernel
imports nothing from `bench/` and names no world (section 4 and the conformance
tests enforce this). An adapter never reads the journal to decide an outcome,
never writes under `.assay/` except its own files (OOLONG writes
`.assay/corpus.txt` and `.assay/oolong_score.json`), and never sees the owner
token.

**Extension points.** `public_info`, `finalize`, world policy inside `step`,
and the observation `data` itself, which is where a world exposes everything
the agent may read and claim against.

### 2.3 Runtime configuration

**Role.** The operator's side of a run beyond the registry: who holds owner
authority, which gates are armed, how spend is accounted, what is imported,
where anchors go, which interpreter serves the daemon.

**Owner.** The operator.

**Contract.** All of it is optional. The pieces, each with where it lives:

- **World id and mode.** `assay start WORLD_ID` (`core.normalize_game_id`:
  any non-empty string up to 64 characters with no whitespace, control
  characters or path separators, kept as given, since it is a label and never
  a path component), `--mode local|competition` or `ASSAY_MODE`, `--seed N`.
- **Owner token.** Minted at start on registry runs (`agenda.mint_owner_token`),
  only its sha256 is stored in `.assay/owner.json`, printed once. It authorizes
  `assay goal ratify`, `assay approve` and `assay waive` (`agenda.require_owner`).
  The agent proposes, the owner ratifies. In every published benchmark run
  the agent ran `start` itself and therefore held the token; 1.2.0 adds the
  token file option, and the protocols start from the operator's shell with
  it (section 8.6, `GUIDE.md` section 5).
- **Approvals.** `approval: true` actions are default-deny, each use needs a
  fresh one-shot grant that expires after 600 seconds
  (`agenda.grant_approval`, `agenda.consume_approval`). Banned inside batches.
- **Waivers and rehearsal.** A `liveness: live` action with a
  `rehearsal_quota` refuses until an imported knowledge file from the same
  registry under a different binding shows that many graded attempts, or the
  owner journals a waiver (`agenda.check_rehearsal`, `agenda.grant_waiver`).
- **Destructive gate.** `destructive: true` actions refuse without
  `--declare worst_case=... --declare recovery=...` and are banned inside
  batches (`live._enforce_registry_gates`). The demand is structural, it never
  bans.
- **Budgets.** The action cap (`registry.check_budget`) and the dollar cap fed
  by `assay spend report --usd --tokens --id`, idempotent by id
  (`registry.spend_reports`, `registry.check_usd_budget`). The kernel cannot see
  the LLM bill, so the cap is as fresh as the feed.
- **Notes cap.** Past twice `notes_cap`, paid actions refuse until
  `.assay/NOTES.md` is trimmed (`live._notes_hard_stop`).
- **Carryover.** `assay export` writes `assay_knowledge.json` (notes, verified
  lines, verifier sources and stats, model source, hazard tags, a journal
  digest). `assay start --import FILE` lands all of it FOREIGN and demoted
  except hazard tags on a matching registration, which import active
  (`carryover.import_knowledge`).
- **Anchors.** Chain heads are appended outside the run directory every 25
  events and on WIN, to `ASSAY_ANCHOR_DIR` or `~/.assay/anchors/<digest>.jsonl`
  (`integrity.anchor_dir`, `run.Run.append`). 1.2.0 records the anchor
  file in `config.json`.
- **Interpreter.** The daemon runs `sys.executable` of the CLI that started it
  (`broker.start_broker`) and `.assay/python` records it. 1.2.0 honors
  `ASSAY_PYTHON` in the launcher and warns on resume when the interpreter
  changed.
- **Timeouts.** `ASSAY_BROKER_TIMEOUT` sets a floor on the client's socket wait
  for slow worlds (`broker._client_timeout`).

**Required.** Nothing. The benchmark protocols always set an action cap, which
is the one item every published run carried.

**Never here.** World semantics, agent instructions, anything the agent could
grant itself (every owner operation checks the token hash).

**Extension points.** The spend feed (any launcher can post usage), the
knowledge file format (`carryover.KNOWLEDGE_FORMAT`), the anchor directory.

### 2.4 Constitution

**Role.** The agent-facing manual: how to operate any registry-mode world
through `assay`. The loop (look, predict, act, compare, note), the claim
grammar, the verifier contract, batching and the batching law, the gates, the
standing goal and proposals, imported knowledge, reset, offline analysis, the
meters.

**Owner.** The harness authors. One file, `CONSTITUTION.md`, shipped unchanged
across worlds. During the ARC-AGI-3 campaign the same file was named
`DOCTRINE.md` (renamed in commit 566bcab on 2026-08-25).

**Contract.** The kernel never reads or injects it. It is given to the agent by
the launcher prompt (`GUIDE.md` section 5: read the constitution completely,
begin with `status` in the run directory the operator started, solve for the
goal, touch the world only through `assay`). Each behavior module carries its own one-paragraph
`CONSTITUTION` string in the same voice (`modules.py`), rendered by
`assay modules` in 1.2.0.

**Required.** For an LLM agent, the whole file. For a person at a terminal,
nothing.

**Optional.** World reference material that is not about the harness, such as
`bench/factorio/FLE_API.md`, is handed to the agent beside the constitution,
never merged into it.

**Never here.** World names, world mechanics, anything the registry or the
observation already says, and nothing about the gate flag (the control arm is
an experiment switch, not agent guidance).

**Extension points.** Experiment variants live outside this file and are
diffed against it (the E1 ungated variants on the experiment branch).

### 2.5 Modules

**Role.** Behavior modules: declare, advise, demand units over the journal. A
module turns a way of thinking into a trigger that fires at the right moment
and, in block mode, into a structural demand the agent satisfies by declaring
named fields. The shipping ladder is telemetry first, teeth later: everything
ships at advise until an A/B shows blocking pays.

**Owner.** The harness for built-ins, the human installer for external ones
(pack-tier trust). The agent never installs a module mid-run.

**Contract.** From `modules.py`:

    NAME: str                                     unique, lowercase
    CONSTITUTION: str                             one paragraph of way-of-thinking text
    MODE: "advise" | "block"                      the default, registry module_modes overrides
    trigger(view, pending) -> str | None          advisory message when it fires
    demand(view, pending) -> dict[str, str] | None {field: why}, structural
    observe(view, event) -> None                  optional, learn from outcomes
    telemetry(view) -> dict                       free counters

The contract is the `modules.Module` protocol, and `view` is
`modules.ModuleView(run)`: `view.events` (the journal as `records.Event`
records, read-only), `view.registry`, `view.paths` (for reading a module's
own files), and the two kernel methods a module persists through,
`view.record(kind, **fields)` (an activity record of that kind; for
`hazard_tagged` the tag goes into `hazards.json` as well, exactly what the
hazard module wrote by path before) and `view.hazards()`. A module never
writes a kernel file by path. `pending` is the action about to be taken:
`{"kind": act|commit|reset, "name", "params", "claims", "declares"}` with the
claims as `records.Claim` records, or `None` at status time. Modules are
consulted before every paid action on a registry run
(`modules.consult_modules` from `live._enforce_registry_gates` and
`live.reset_level`), told the outcome after every recorded event
(`modules.observe_outcome`), and asked for status-time advisories
(`modules.advisory_lines`). In block mode an unmet demand refuses with
`MODULE name | declaration demanded before this action: --declare f=...` and
the declaration always unlocks the action. Declarations are journaled on the
event under `declares`.

Six built-ins, all `MODE = "advise"`: `wall_spend`, `miss_streak`,
`null_forensics`, `park_with_test`, `sharpness`, `hazard` (effect-signature
tags: `entered_loss_state` and `milestone_dropped`, permanent for the run,
exported as the distinguished carryover class). 1.2.0 adds the world-neutral
`coverage_audit` built-in (untried and never-productive actions, stall, the
re-issue halt, the loop halt, the conclusion gate keyed on declares).

External modules: `modules: ["path.py"]` in the registry, copied into
`.assay/modules/` at start (`modules.pin_external_modules`), loaded by
`modules._load_external` from the manifest the run holds. 1.2.0 pins them by
manifest with a sha256 and adds an owner-authorized install command, so the
hot-add channel is sanctioned and journaled rather than a directory glob. The
install is a daemon operation (`install_module`, section 6.4, #20): the daemon
checks the owner token against the hash it holds, pins the file, checks the
contract and the name, updates the manifest it holds, journals
`module_installed` and reloads its module set, so the module runs from the
next action; without a live daemon `assay module install` refuses and names
`assay start`. The modules load once per process (`modules.active_modules`):
in the daemon at `serve`, from the manifest it holds, for its life; in the
CLI lazily, once per command that consults them (`status`, `module list`). A
run pinned before the manifest existed has it rebuilt from the registry's
`modules` list at `assay start` (`modules.reconstruct_manifest`), never from
a status call: the loader never writes.

**Required.** Nothing. A registry with no `modules` key runs the built-ins at
their default modes.

**Optional.** External modules, per-module modes, `off` for any built-in.

**Never here.** LLM calls. World names or world geometry (the grid region part
of the coverage audit belongs to the frame-world extra, not to the built-in).
Bans: a demand is always for checkable structure (named, non-empty fields),
never for confidence, and declaring always unlocks.

**Extension points.** The module contract itself, `module_modes`, `observe`
for learning modules, `telemetry` for the analytics pack.

### 2.6 Channels

**Role.** Named, code-extracted readings of the observation. Channels are how
an agent names a referent once and then claims against it exactly, instead of
through the blunt `change` and `noop` pair. They are the internalization
mechanism and the grounding meter: a claim naming an unregistered channel is
refused free and counted (`channels.check_channel_references`, the
`mis_reference` activity record).

**Owner.** Host channels belong to the kernel. Declared channels belong to the
agent (or the operator). Available in every world.

**Contract.** Three sources (`channels.py`):

- **Host channels**, always present: `goal` (true when `state == "WIN"`),
  `level` (`levels_completed`), and `budget_remaining` (the registered action
  cap minus the paid actions up to and including the event, so
  `ch budget_remaining delta = -1` holds for any paid action, and UNGRADABLE
  only when no cap is registered, with the reason saying so).
- **Declared path channels**: `assay channel declare NAME --path a.b.c`. The
  dotted path walks the observation object the adapter put under `data`, so a
  Factorio reading is declared as `--path tick`, not `--path data.tick` (the
  circuit run's first event shows the UNGRADABLE that the wrong form earns).
  Lists are indexed by integer keys. Path channels need a dict observation.
- **Declared extractor channels**: `--file extractor.py` with
  `def extract(obs) -> value`. The file is content-hashed, stored at
  `.assay/channels/<hash>.py`, and runs only in the sandbox against the
  observation view (`verifiers.observation_view`: `state`, `levels_completed`,
  `win_levels`, `available_actions`, and `data` or `frames`). This is the form
  frame worlds use.

Names match `^[a-z][a-z0-9_]{0,31}$`, at most 16 declared per run, a name
cannot be redeclared with a different extractor (declare a new name instead of
silently redefining a referent), every declaration is journaled as
`channel_declared`.

Claim forms graded by `channels.grade_channel_claim`: `ch NAME = V [± TOL]`,
`ch NAME delta = | >= | <= V`, `ch NAME delta sign + | -`,
`ch NAME crosses V [from below | from above]`. Delta and crossing forms need
numeric readings before and after. Claims on `goal` and `level` sit in the
`gamble` bucket, all other channel claims in `world_model`
(`predictions.claim_bucket`). Statistical claims
(`agg ch NAME mean|min|max OP V over Na horizon Ma on-fail advise|revoke_batching`)
are additive, open at the gate and resolve at their horizon (`aggregates.py`).

**The worked example** (the Factorio M2 runs, `evidence/factorio`).
The pattern is declare early, name referents, claim every action with a channel
form. The irongear run declared seven path channels at its first event:

    assay channel declare tick     --path tick
    assay channel declare ents     --path entities_total
    assay channel declare refusals --path policy_refusals
    assay channel declare prod     --path throughput_corroboration.producer_present
    assay channel declare wins     --path windows_complete
    assay channel declare gears    --path target_produced_total
    assay channel declare auto     --path target_automated_total

and then claimed, verbatim from the journal:

    e1 RUN  --predict "change; ch tick = 0; ch ents = 0; ch refusals = 0"
    e2 RUN  --predict "change; ch tick = 180; ch ents delta sign +; ch refusals = 0; ch prod = False"
    e4 RUN  --predict "change; ch tick = 360; ch ents = 27; ch refusals = 0; ch prod = True"
            graded SURPRISE: ch ents = 27 -> ch ents = 18
    e8 WAIT --predict "verify:checks/first_window.py; ch tick = 3600; ch wins = 1; ch prod = True"
    e9 WAIT --predict "win; level+1; verify:checks/holdout.py; ch tick = 7200; ch wins = 2; ch gears delta >= 16; ch prod = True"

The ironplate run used the crossing form on the automated-production channel
(`ch automated crosses 16 from below`) and the circuit run used a tolerance
(`ch lastrate = 20 ± 5`). The equality claims pin the tick clock, the delta and
crossing claims pin the mechanics, and every miss carries the machine's
counter-fact.

**Required.** Nothing. Host channels exist without declaration.

**Optional.** Declared channels, aggregate claims.

**Never here.** A channel is graded against the extractor's value over the
world's own response, never against the agent's account of it. The kernel never
invents a channel from a description. Extractor code never runs in-process.

**Extension points.** Path and extractor declarations. Pack channels are a
later stage (no second pack exists).

### 2.7 Verifiers and the world model

**Role.** Agent-written code, admitted under a hash, run in the sandbox,
identity-probed, and promoted only by replay fit. Verifiers are the sharpest
claim available. The world model is the general form of the executable-rules
tier: trust is exactly replay fit and nothing else.

**Owner.** The agent writes them. The kernel admits, stores, runs, probes and
meters them. Available in every world.

**Contract, verifiers** (`verifiers.py`). A claim `verify:PATH.py` names a file
relative to the run directory defining `def verify(before, after) ->
(ok, actual)`. At claim time, before any spend, the file is read, sha256-hashed
and copied to `.assay/verifiers/<hash>.py` (`verifiers.admit_verifier`, the hash
is journaled on the claim as `verifier_hash`). At grading time the stored copy
runs in the sandbox (section 8.5: a scratch copy under `sandbox-exec` on macOS
or `bwrap` on Linux, no path into the run directory, no network, no fork, the
memory limit Linux-only) with both observation views on stdin and must emit one
JSON line `{"ok": bool, "actual": str}`. Crash, timeout (5 seconds CPU and
wall) or malformed output grades as `INVALID_CLAIM`: not a miss, its own
counter, `predict_ok` null, and it halts a containing batch. After each grading
the kernel also runs the verifier on the identity transition `(before, before)`
and journals `identity_verdict`. Per-hash counters live in
`.assay/verifiers/stats.json`. A verifier is VACUOUS when its identity verdict
equalled its real verdict on every one of five or more gradings, which means
it does not use the transition: status says so, its passes are excluded from
the capability meter, and a verifier that never failed is an advisory line,
not a flag (`verifiers.vacuous_hashes`, `inspect._claim_meter_lines`). The
stats file is versioned: a run recorded from 1.2.0 on carries
`"rule": "identity"` with its counters under `"verifiers"`, a run recorded
before it keeps the flat file of the never-failed rule (five or more gradings,
zero failures) and is read under that rule, so the published runs render
unchanged (`verifiers.stats_rule`).

**Contract, world model** (`model.py`). `model.py` in the run root declares
`CHANNELS` (registered channel names), `next(obs, action, params)` returning the
predicted observation or `None` for Unknown, optional `actions(obs)` and
`key(obs)`. `assay model replay` re-predicts every recorded paid transition in
the sandbox and grades only the declared channels (`model.replay_model`, fit
record in `.assay/model_fit.json`). The promotion law is pinned: a model hash
is admitted at the journal event of its first replay (`admitted_at_event` in
the fit record, read from the earliest `model_replay` activity record that
carries the hash and its event, or the current replay when there is none), and
only the paid transitions recorded after that event are counted, so the model
earns rights by predicting the future, not by fitting the past. Batching rights
need `missed == 0` over the whole fit, at least 20 counted transitions and at
least 5 counted inside the most recent quarter of the journal, the current
`model.py` hash and the current journal head, no ungated event, and no
aggregate that revoked batching (`model.batching_rights`).
`assay model solve --to "ch NAME = V"`
searches the model in the sandbox and writes `.assay/model_plan.json` with one
machine prediction per step. `assay commit @.assay/model_plan.json` is the only
way past the hand-batch cap (`live.execute_model_plan`), every step is graded
against the channel values and marked `machine: true`, so machine predictions
never enter the agent's meters. Imported models never carry rights.

**Required.** Nothing.

**Optional.** Verifier claims, a world model, model plans.

**Never here.** In-process execution of agent code on the grading path. Trust
states other than replay fit. Promotion on thin evidence.

**Extension points.** The verifier contract, the model contract, `key` and
`actions` for search.

### 2.8 Journal, chain, anchors, audit

**Role.** The record. Everything the run did, in order, append-only, under a
rolling hash chain whose heads are anchored outside the run directory, with an
audit that recomputes integrity from the artifacts alone.

**Owner.** The kernel. World-neutral.

**Contract.**

- **The journal**, `.assay/events.jsonl`, one JSON object per line: a
  `records.Event`, built by `core.make_event` and appended by `run.Run.append`
  (the one writer while a daemon lives) with fsync and a file lock, as
  `json.dumps(event.to_json(), separators=(",", ":"), sort_keys=True)`. Fields
  per `JOURNAL_SPEC.md` section 2: `id` (equal to the line index, checked on
  every load by `run.Run.load`, which decodes every line through
  `records.Event.from_json`, refuses a wrong type on a known key and carries an
  unknown key through unchanged), `timestamp`, `action`, `data`,
  `counts_action`, `state`, `levels_completed`, `level_before`, `win_levels`,
  `available_actions`, `frames` and `n_frames` or `observation`, `note`,
  `predict`, `predict_ok`, `grade` (a tuple of `records.Grade`), `mutation_id`,
  plus `declares`, `gate_optional` and `gate_off` when present. The grades,
  the claims (`records.Claim`), the receipts (`records.Receipt`) and the
  mutation records (`records.Mutation`) are typed the same way, and every
  published journal under `evidence/` reads and re-serializes to the same
  bytes (`tests/test_records.py`).
- **The write-ahead spend record**, `.assay/mutations.jsonl`, appended by the
  daemon before the event line (`broker._Daemon.spend`, through
  `run.record_mutation`): a `records.Mutation` with the action, its data, the
  reasoning (`predict`, `because`, `declares`), the world's response and, from
  1.2.0 on, for an act and for each commit step, the parsed claims with their
  admitted verifier hashes (`claims`); a model-plan step's record carries its
  plan reasoning and no claims, a reset's none. A crash between spend and
  record is recovered by `broker.reconcile_mutations` at the next `assay
  start` with the daemon dead, the one append outside a daemon: a record with
  claims is regraded against its stored response through the live path's
  grader (`live.recovered_pending`, `predictions.grade_pending` with
  `elapsed_s=None`, under which a claim with an `@within` window is UNGRADABLE
  with the actual `UNGRADABLE: recovered, step duration unknown`) and
  journaled with `predict`, `predict_ok`, `grade` and `declares`, gated by its
  fields; a record without claims is journaled UNGATED, as before 1.2.0. Both
  carry the note `recovered from broker mutation journal`. The audit lists
  recovered orphans and says which were recovered without their prediction
  (section 6.5).
- **The chain.** `head_0 = sha256("assay-chain-v1")`, `head_n =
  sha256(hex(head_{n-1}) || line_n)` over the raw line (`integrity._advance`).
  `run.Run.load` recomputes the head over every line it reads and classifies
  the stored `chain.json` as intact, absent, behind by the lines a crash left
  (its `event_id` below the last id, its head equal to the head of that
  prefix) or diverged; `run.Run.append` advances the held head with the exact
  line it wrote and writes `chain.json` after every append, so an absent or
  crash-behind chain is held as the recomputed head and repaired by the next
  append. A strict load (`start`, the daemon) refuses a diverged chain or a
  contiguity problem with `CHAIN_DIVERGED` and rewrites nothing; a lenient
  load (the readers) reports it: `audit` as before, and status on an
  INTEGRITY line.
- **Anchors.** Every 25 events and on WIN the head is appended to the anchor
  file outside the run (`integrity.ANCHOR_EVERY`, `integrity.anchor_file`). An
  unwritable anchor directory degrades to chain-only integrity today and is
  made visible in 1.2.0.
- **The audit**, `assay audit` (`integrity.audit`): contiguity, stored chain
  intact or DIVERGED or absent, anchors intact or DIVERGED or none, UNGATED
  events (paid, not `RESET`, carrying none of `predict`, `predict_ok`, `grade`),
  the `gate_optional` subset as `ungated_permitted`, recovered orphans, and the
  verdict `invalid_for_scoring`, written to `.assay/audit.json`. One ungated
  event invalidates the run for scoring and voids batching rights earned after
  it.
- **Redaction at the boundary.** Values of `ANTHROPIC_API_KEY`,
  `CLAUDE_CODE_OAUTH_TOKEN` and the registry's `secrets` names, when at least 8
  characters long, are replaced by `[REDACTED:<NAME>]` in every agent-supplied
  string before it is written (`integrity.redact`, `integrity.redact_mapping`).
- **Beside the journal**, out of the spec's scope: `.assay/activity.jsonl`
  (command starts and ends, receipts, declarations, mis-references, hazard tags,
  spend reports, approvals, waivers, proposals), `.assay/receipts/`, the notes
  file, channel and verifier stores, caches.

**Required.** All of it, on every run.

**Optional.** Nothing in this component is optional, which is the point.

**Never here.** Updates, rewrites or deletions of a journal line. Status of any
kind stored over the record (every meter is recomputed). Plaintext secrets.
World-specific fields.

**Extension points.** None in v1. Spec v2 (planned, in `verify/`) chains a
canonical form so that agent prose becomes redactable, and reserves the general
aliases `progress`, `progress_total`, `status`.

## 3. The frame-world extra

Frame worlds (observation is a grid) have a tier the dict worlds do not need:
image rendering, a scene dossier, perception helpers and the grid claim
forms. In 1.2.0 these live outside the kernel in the extra package `assay_grid` (`src/assay_grid/`,
same repository, same distribution), selected automatically by observation
shape (`"frames" in event`, the test `core.general_event` makes), never by
configuration: registries are pinned per run and the 25 published run
directories carry no such key, yet the kernel keeps rendering, inspecting and
auditing them.

What lives in the extra, by module: `perception.py` (connected components,
repeated shapes, lattice inference, line graph, frame delta, motion trace,
transition story, the scene dossier), `claims.py` (the claim kinds `cell`,
`move`, `vanish`, `region` and the frame grader, which also grades the general
forms on frames by cell comparison), `render.py` (the palette, one PNG per
event, the frame history line, the one pillow import), `views.py` (the frame
halves of status, result, inspect, view and export: board text, diffs, scene
summary, animation, click candidates, the advertised-action line and the
advertised-id to name rendering the affordance check needs), and
`analysis.py` (the grid namespace of `assay python`).

What stays in the kernel because the journal format has it: the frame encoding
(`core.grid_to_rows`, `core.rows_to_grid`, the frame branch of
`core.normalize_observation` and `core.make_event`, `core.frame_at`,
`core.general_event`). numpy stays a kernel dependency for that encoding in
1.2.0. `textobs.py` stays. The dict branch of `evidence.history_lines` stays.
The parser declares the frame-only `view` flags (`--grid`, `--frames`,
`--crop`, `--export`) itself, inert on a dict run, so the command line surface
is kernel-owned while the behavior is the extra's.

The hook is one kernel module, `extras.py`: the `ObservationKind` protocol
(`applies`, `claim_patterns`, `claim_fields`, `claims_help`, `grade_claims`,
`after_record`, `status_head_lines`, `status_lines`, `result_lines`,
`view_text`, `history_line`, `canonical_action`, `advertised_names`,
`python_namespace`, `export_history`) and the functions
`kind_for(event)` (imports `assay_grid` lazily and only when an event has
frames), `all_kinds()` (every importable kind, used when help is rendered) and
`require_kind`. `assay_grid.KIND` is the one implementation. A dict run never
imports `assay_grid` or pillow, and `import assay.live, assay.inspect,
assay.cli` imports neither (the conformance tests pin both).

Behavior preserved, proven by the replay diff over the 25 published run
directories (zero differences in `status`, `audit`, `view` and `channel list`
against the main kernel): no journal field changes, no change to grade
records, the grid claim forms stay refused exactly as before
(`predictions.parse_claims` with `kind=None`, the refusal now names the form),
which is why no published journal contains one. Whether frame worlds should
gain those forms is owner decision O1. The legacy numbered-action path (runs
without a registry, `ACTION1..7` and `ACTION6:x,y` with the 0 to 63 bound,
the status nudges) and the executable-rules tier that only ran there are
deleted in 1.2.0 (O2).

## 4. Per-world conformance

Five worlds, read from the three bench adapters, the counter example, the
new-world template, the pinned registries, and the run directories behind the
published journals (25 ARC-AGI-3 runs, 3 Factorio runs, 3 OOLONG runs). Cells
say **present**, **optional, unused**, or **world-specific** with the file.

| Component | ARC-AGI-3 | Factorio (FLE) | OOLONG | Counter example | New-world template |
|---|---|---|---|---|---|
| Registry file | `bench/arcagi/registry_{200,500,1500}.json` (differ in the cap only) | `bench/factorio/registry_lab{64,128}.json` | `bench/oolong/registry_{40,200}.json` (cap only) and `registry_200_batch.json` (`control.bank_mode: batch`, which changes the action forms) | `examples/example_registry.json` | `examples/new_world/registry.json` (every optional key present, explained in its README) |
| Registry: actions | `ACTION1..ACTION7`, `ACTION6 x,y` int 0..63, descriptions | `RUN program=<str base64>`, `WAIT ticks=<int 1..3600>`, descriptions | `BANK_FACT text,span=<str base64>`, `SUBMIT answer,spans=<str base64>`, descriptions; the batch registry: `BANK_FACT spans=<str base64>` (several spans in one action), `SUBMIT answer=<str>` (plain text, `_` for a space) | `INC amount=<int 1..2>`, `SET_LAMP state=<on\|off>`, `NOOP`, `BOMB` | `TURN delta=<int -3..3>`, `OPEN`, `ENTER`, `PEEK what=<code\|door>`, `ALARM`, `DRILL`, `SIREN volume=<float 0..1>` |
| Registry: budget | actions 200, 500 or 1500 | actions 64 or 128 | actions 40 or 200 | actions 40 | actions 60, usd 5.0 |
| Registry: batching | `hand_cap: null` | `hand_cap: null` | `hand_cap: null` | default 3 | `hand_cap: 3` |
| Registry: goal, mode_note | optional, unused | present (goal text, mode_note) | present (goal text, mode_note) | present | present |
| Registry: secrets | `ARC_API_KEY` | four RCON password names | optional, unused | optional, unused | `NEW_WORLD_API_KEY` |
| Registry: destructive, approval, liveness | optional, unused | optional, unused | optional, unused | `BOMB` destructive | `ALARM` destructive, `DRILL` approval, `SIREN` live with rehearsal quota 1 |
| Registry: zero_prior | off (parity with the hint line) | off (explicit) | off | off | off (explicit) |
| Registry: modules, module_modes | unused in the 25 (the E2 experiment loaded `coverage_audit` externally) | optional, unused | optional, unused | optional, unused | `modules: []`, modes for `coverage_audit` and `hazard` |
| Registry: gate | absent (required) | absent | absent | absent | `required` (explicit) |
| Adapter | world-specific, `bench/arcagi/adapter.py` | world-specific, `bench/factorio/adapter.py` | world-specific, `bench/oolong/adapter.py` | `examples/counter_world.py` | `examples/new_world/adapter.py` |
| Adapter: observation shape | frames (the ARC client's frame object, 64x64, values 0..15) | dict: task, tick, stdout, stderr, inventory, entities, production, windows, policy_refusals, namespace_watch | dict: corpus path and shape (never the body), current question, census, last_result | dict: counter, lamp | dict: room, dial, door, hint, refusals, last_result |
| Adapter: `finalize` | present (`close_scorecard`) | present (teardown, cluster stop, cached observation) | present (sealed scoring, writes `.assay/oolong_score.json`) | optional, unused | present (writes `.assay/new_world_summary.json`) |
| Adapter: `public_info` | present (game_id, title, tags, default_fps) | present (game_id, title, map_seed) | present (game_id, title, benchmark, dataset_revision, context_len, dataset) | optional, unused | present (world, rooms) |
| Adapter: refusals through the observation | not needed (the engine accepts every action) | world-specific: `POLICY_REFUSED` in `stderr`, `policy_refusals` counter | world-specific: `last_result.status = refused`, `refusals` counter | unknown action raises (no refusal path) | present: `last_result.status = refused`, `refusals` counter |
| Adapter: world policy before execution | none | world-specific: AST screen `screen_program`, `_SealedInstance`, namespace watch | world-specific: verbatim span check, census gate | none | none |
| Adapter: determinism | seed plus cached game, replay on resume | recorded tick deltas replayed exactly | pure (no time, no network) | pure | codes derived from the seed |
| Adapter: remote mode | present (`--mode competition`) | local only | local only | local only | local only |
| Adapter: world id rule workaround | none (ids are 4 chars) | world-specific: 24-entry `TASK_ALIASES` table | none (pack ids are valid ids) | none | none |
| Runtime: owner token | minted, held by the agent | minted, held by the agent | minted, held by the agent | minted | minted, delivered to a file in the test |
| Runtime: approvals, waivers | optional, unused | optional, unused | optional, unused | optional, unused | both exercised (`DRILL`, `SIREN`) |
| Runtime: destructive declarations | optional, unused (declares present in 3 runs came from hazard advisories) | optional, unused | optional, unused | required for `BOMB` | required for `ALARM` |
| Runtime: usd cap, spend feed | optional, unused | optional, unused | optional, unused | optional, unused | cap present, feed unused |
| Runtime: carryover import | unused in the 25 (E3 on the experiment branch imported) | optional, unused | optional, unused | optional, unused | optional, unused |
| Runtime: anchors, chain | chain intact on 22, absent on the 3 pre-chain runs (cd82, tn36, sp80) | chain intact | chain intact | chain intact | chain intact |
| Constitution | `CONSTITUTION.md` (named `DOCTRINE.md` when played) | `CONSTITUTION.md` plus `FLE_API.md` as world reference | `CONSTITUTION.md` | `CONSTITUTION.md` | `CONSTITUTION.md` |
| Modules: built-ins | six, advise | six, advise | six, advise | six, advise | seven, advise |
| Modules: hazard tags observed | bp35 (3 classes), tu93, wa30 | none | none | `BOMB` after `GAME_OVER` | `ALARM` after `GAME_OVER` |
| Modules: external | none in the 25 | none | none | none | none |
| Channels: host | present, unclaimed (progress claimed as `level+1` and `win`) | present, unclaimed (same) | present, unclaimed (same) | present, `ch goal` claimed in the e2e tests | present, `level+1` and `win` claimed |
| Channels: declared, form | extractor files, 6 to 16 per run in 22 of 25 (one path channel in r11l, ungradable on frames) | path channels, 7 to 14 per run, one extractor (circuit `lastrate`) | path channels `banked` and `submitted` in synth1m, none in 128k and 4m | one path channel (`counter`) | path channels `dial`, `door`, `refusals` |
| Channels: claim kinds used | eq, delta (meter, cursor rows and columns, bars) | eq, delta, sign, crosses, tolerance | delta | eq, delta | eq, delta |
| Verifiers | all 25 runs, 20 to 794 graded verify claims per run | 3 to 5 per run | optional, unused in the three published runs | tests only | optional, unused |
| World model (`model.py`) | written in cn04, s5i5, sc25, su15, tu93, replayed in cn04, no plan ever executed | optional, unused | optional, unused | tests only | optional, unused |
| Journal | frames, `n_frames`, 83 to 1172 events | dict observation, 6 to 10 events | dict observation, 41 to 51 events | dict observation | dict observation, two progress units |
| Audit verdict | CLEAN on 25 of 25 | CLEAN on 3 of 3 | CLEAN on 3 of 3 | CLEAN in the tests | CLEAN in the test |
| Frame extra | present: images, dossier, scene lines, click candidates, grid `assay python` namespace | not applicable | not applicable | not applicable | not applicable |
| Rules tier | optional, unused (registry runs refuse it) | not applicable | not applicable | not applicable | not applicable |
| Kernel imports from the world | none | none | none | none | none |

Counts come from the published journals (`evidence/*/journal-*.jsonl.gz`)
and from `channels.json`, `verifiers/`, `hazards.json`, `model.py` and
`model_fit.json` in the run directories. The template column is what
`tests/test_conformance.py` exercises on every run of the suite.

## 5. What must never change

Frozen by the public contract (`assay-journal-v1`) and by the 66 published
journals under `evidence/`, which `evidence/verify_all.py` verifies against
their heads:

- the journal field names, including the historical ones: `levels_completed`,
  `win_levels`, `level_before`, `state`, `frames`, `n_frames`, `observation`,
- the state values the grammar reads: `NOT_FINISHED`, `WIN`, and `GAME_OVER`
  as a world-reported terminal,
- the claim syntax: `noop`, `change`, `level+1`, `win`, `verify:PATH`, the
  `ch` forms, the `agg` form, `@within Ns`, and the frame forms `cell`, `move`,
  `vanish`, `region` on frame worlds,
- the grade `actual` texts ("level advanced", "level did not advance",
  "state WIN"), which are graded facts inside journals,
- the host channel names `goal`, `level` and `budget_remaining`,
- the chain seed `assay-chain-v1`, the chain rule, the ungated rule and the
  `RESET` exemption,
- the config key `game_id`, the knowledge-file key `game_id`, the import
  summary key `source_game`, and every activity record kind,
- the receipt outcome tokens `PREDICTED`, `SURPRISE`, `INVALID_CLAIM`,
  `UNGATED`, `LEVEL_COMPLETE`, `GAME_COMPLETE`, `GAME_OVER`, `RESET`,
- the state-directory layout, including `.assay/levels/level-N.md`, and the
  `L<n>` prefix of the RECENT history lines.

Display strings are not frozen. The vocabulary pass of 1.2.0 changed prose
(game to world, board to state, level to progress unit where the world is
not a game) and nothing above. `src/assay/words.py` carries the same list as
its docstring, and `tests/test_vocabulary.py` enforces it.

## 6. The run model (1.2.0, design note 1)

Status: a design note of the 1.2.0 milestone, written 2026-10-07 from the review of the
same day and revised the same evening after an independent review against the code,
implemented by #12, #20, #24 and #16. Sections 1 to 5 describe the code at 6ea56e4; where
this note and a section above differ, the note is the target and the pull request that
lands the change updates the section.

What #12 landed, and where it stops: the records of 6.2 (`records.py`, without `Status`,
which is #13's), the run of 6.3 (`run.py`: `Run.load` strict and lenient, `run.append`,
`run.verify_disk` with its `Tamper` record, the mutation log through
`run.record_mutation`), the signature change of 6.3 across the kernel and the frame
extra, event 0 written by the daemon, the `Module`, `ModuleView` and `Adapter` protocols
of 6.4, and the tests of 6.6 (the 66-journal round trip, the AST walk, the daemon's
journal read back). What #20 landed: the daemon holds one `Run` for its life, loaded
strict at `serve`, and every request handler receives it; it calls `verify_disk` before
every paid action and refuses on a difference with `TAMPER_DETECTED`, holding a flag that
refuses every later paid action until it is stopped (the sealing record in the anchor
file and audit's reading of it are #10's); the modules load once at `serve`;
`install_module` is the daemon operation of 6.4. What #16 landed: the claims on the
mutation record at spend time and the regrade at recovery of 6.5, with
`predictions.grade_pending` the one grader of the live path and of recovery. One thing
6.3 describes still waits:
approvals and waivers stay files until #24 moves the owner operations into the daemon.
`core.load_events` and `core.append_event` are gone rather than kept: `analysis`
builds the agent's namespace from the held records (the journal still reaches the agent
as plain JSON objects), and the AST test still refuses `load_events(` outside `run.py`,
`core.py` and `analysis.py`, and refuses every other reader of `events.jsonl` below the
entry points. A malformed journal line (a wrong type on a known key, a missing required
key, no JSON) is an integrity finding for the readers and a `CHAIN_DIVERGED` refusal for
a start; a malformed `chain.json` reads as diverged; a chain is behind only by the one
line a crash leaves.

### 6.1 Why

The kernel passes events, claims, grades, receipts and mutation records as untyped
dictionaries and never validates a journal on read. Every function takes `paths` and
reloads the journal: one `assay act` loads it six times, re-imports every external module,
and nothing the daemon believes can be checked against the disk. One run model, loaded
once and passed down, ends all three.

### 6.2 The records

Module `src/assay/records.py`. Frozen dataclasses with `slots=True`. Each has a
classmethod `from_json(obj)` that validates the type of every known key and a method
`to_json()` that emits exactly the keys the spec names plus the carried extras. The
journal line is `json.dumps(record.to_json(), separators=(",", ":"), sort_keys=True)`,
as today, so the chain rule and every published head are untouched. The reviewer's census
of the 66 published journals: every one round-trips through that form with zero differing
lines; the known keys and their observed types are `data` None or object, `predict` None
or string, `predict_ok` None or bool, `level_before` None or int, `note` string,
`available_actions` a list of strings or of integers, `declares` object, `gate_off` bool.

- `Event`: `id`, `timestamp`, `action`, `data` (any JSON object or None), `counts_action`,
  `state`, `levels_completed`, `level_before`, `win_levels`, `available_actions` (strings
  or integers, as recorded), `observation` for dict worlds, `frames` and `n_frames` for
  frame worlds (the hex rows, decoded on demand by `core.frame_at`), `note`, `predict`,
  `predict_ok`, `grade` (a tuple of `Grade`), `mutation_id`, `declares`, `gate_optional`,
  `gate_off`, and `extra`: every key the decoder does not know, carried through unchanged,
  so that every published journal loads and re-serializes to the same line. Validation
  refuses a wrong type on a known key and never refuses an unknown key.
- `Claim`: `kind`, `text`, `window_s`, `channel`, `op`, `value`, `sign`, `direction`,
  `tol`, `path`, `verifier_hash`, `stat`, `over`, `horizon`, `on_fail`, `coerced`,
  `extra`. One class for every claim form; a field that does not apply is None.
- `Grade`: the fields of `Claim` plus `ok` (bool), `actual` (str), `bucket` (str),
  `invalid` (bool), `ungradable` (bool), `verifier` (bool), `identity_verdict` (bool, the
  string `invalid`, or None: a crashed identity probe is journaled as the string today),
  `excluded_from_meter` (bool), `machine` (bool). On the journal a coerced claim's kind is
  the string `coerced`, as today.
- `Receipt`: `kind`, `outcome`, `detail`, `start_event`, `end_event`, `level`, `action`,
  `predict`, `grade` (the rendered lines), `because`, `modules`, `aggregates`, `channels`,
  `steps` (a tuple of `ReceiptStep`: `event`, `action`, `ok`, `failed`, `invalid`,
  `ungated`, `machine`, `kind`, `problem`), `plan`, `estimated_tokens` (section 7.6),
  `timestamp`. Written to `.assay/receipts/` and to the activity log as today.
- `Mutation`: `mutation_id`, `action`, `data`, `reasoning`, `observation`, `claims` (a tuple
  of `Claim`, written by the daemon at spend time from #16 on, absent on older records),
  `timestamp`.
- `Status`: section 7.4.
- The registry stays the canonical dictionary `validate_registry` produces, read through
  the accessors in `registry.py`; it changes shape in #14 and is not a record in 1.2.0.

### 6.3 The run

Module `src/assay/run.py`, class `Run`.

Held in memory: `paths`, `config`, `registry`, `events` (a list of `Event`), `chain_head`,
`chain_event`, `journal_bytes` (the length of `events.jsonl` after the last append or
verification), `mutations` (the decoded log) with `mutations_bytes`, `manifest` (the module
manifest), `modules` (the loaded module objects with their effective modes), `owner_hash`
(the sha256 of the owner token from `owner.json`), `opened_at`.

Read from disk on demand and never held: the activity log (append-only, written by both
processes), `channels.json` (written by the CLI at declaration) and the readings cache
(written by the daemon at grade time), the verifier statistics (daemon), `hazards.json`,
`aggregates.json`, `proposals.jsonl` (CLI) and `goal.json` (the daemon, at ratification),
the notes. They are small and each key has one writer. Approvals and waivers move into the
daemon (section 7.2): approvals are held in memory for their 600 seconds, waivers last the
run and are rebuilt at `Run.load` from the `liveness_waived` activity records, so neither
has a file, and the one file two processes rewrote is gone. The three owner commands need
the daemon alive.

- `Run.load(paths, strict)`: reads config, registry, the journal (every line into an
  `Event`), `chain.json`, the mutation log, `owner.json`, the manifest and the
  `liveness_waived` activity records. It never writes. What it finds about the record goes
  into `run.integrity`: a contiguity problem, a chain file that is absent, behind by the
  lines a crash left (its `event_id` strictly less than the last id and its head equal to
  the head of that prefix), or diverged. `serve` and `start` load strict: a contiguity
  problem or a diverged chain raises `CHAIN_DIVERGED` (kind invalid), the start is refused
  and nothing is rewritten, so the evidence of an edit stays on disk; an absent or
  crash-behind chain is held as the recomputed head and written by the daemon's next
  `run.append`, as today. The readers (`audit`, `status`, `view` and the rest) load
  lenient: `audit` reports `run.integrity` as it reports today (the three pre-chain
  published runs keep printing `chain absent`) and the INTEGRITY line shows it. A line
  that does not decode (a wrong type on a known key, a missing required key, no JSON) is
  a finding too: the lenient reader keeps the events before it and treats the journal as
  ending there, status names the line on its INTEGRITY line and audit as a problem, and
  the strict load refuses with `CHAIN_DIVERGED | line N malformed: ...`. Modules
  load once (section 6.4); the CLI loads them lazily, only for the commands that consult
  them. Every CLI command calls `Run.load` once at entry; the daemon calls it once in
  `serve`.
- `run.append(pending)`: the only writer of `events.jsonl` while a daemon lives. Assigns
  `id = len(events)`, serializes, appends under the file lock with fsync, advances the
  held head with the exact line written, writes `chain.json`, anchors when due (every 25
  events and on WIN), appends to `events`, updates `journal_bytes`. It replaces
  `core.append_event` and the re-read in `live.record_event`.
- Event 0 is the daemon's: on a fresh run `serve` takes the first observation, writes
  `public_info` into `config.json`, appends the `START` event through `run.append` and runs
  the observation kind's `after_record`, all before it binds the socket and reports READY;
  `assay start` then prints status. The CLI writes `config.json`, the registry copy, the
  owner file and the notes file before it spawns the daemon and never writes `config.json`
  after. The one append outside a daemon is `reconcile` at start with no daemon alive
  (section 6.5).
- `run.verify_disk()`: recomputes the head over the whole `events.jsonl` (over the raw
  bytes, never decoded: `integrity.chain_over_bytes`, the same rule) and compares it and
  the file length to the held values; compares the mutation log, `registry.json`,
  `config.json`, `chain.json` (against the stored record the last append wrote: a replaced
  head or a deleted file is a difference, not something the next append repairs),
  `owner.json` (against the held hash, until #10 moves the owner operations into the
  daemon) and the manifest file to the held copies the same way. Any difference is a
  `Tamper(what, expected, found)`, and a file that cannot be read or parsed is one too,
  with `found` reading `unreadable: ...`. The daemon calls it before every paid action
  (section 8.3; from its stepper, before the world step of every act, commit step,
  model-plan step and reset, so a batch is checked before each of its steps); `assay
  audit` reports the same comparison against `chain.json`. Measured on the thousand-event
  test of 6.6: about a millisecond per paid action over the 443 KB journal it leaves.
  Measured cost on
  the published journals: the largest ARC journal (1172 events, 5.9 MB) hashes as a chain
  in 5 ms and the largest journal of all (32 MB) in 32 ms; with the mutation log of the
  same size, budget 70 ms per paid action worst case, against a world step that takes
  seconds. An implementer may hold a raw digest of the file bytes instead of the chain and
  compare `hashlib.file_digest`, which halves it.
- Every function under `live.py`, `inspect.py`, `channels.py`, `modules.py`,
  `aggregates.py`, `agenda.py`, `model.py` and `carryover.py` that took `paths` and
  reloaded the journal takes the run, and uses `run.paths` for its own files. The
  `ObservationKind` protocol's display and after-record methods (`after_record`,
  `status_head_lines`, `result_lines`, `view_text`, `export_history`, `history_line`) take
  the run too, so the frame extra stops reloading the journal (`render.current_image`,
  `views.export_history` do today). `core.load_events` is gone: `analysis.run_python`
  builds the agent's namespace from the held records, and no function below the entry
  points reads `events.jsonl`; the readers that survive (`doctor`, which must read a
  broken run, and the knowledge file's published `journal_sha256`) are named, with their
  reasons, in the allow-list of the AST test (`tests/test_run_model.py`).
- The daemon holds one `Run` for its life; the request handlers of section 7.2 receive it.
  The id of the next event is the held count, never the line count on disk, and the next
  mutation id is one past the held log's last. `ping` answers with the held chain event
  and head and what the daemon refused over, if anything (`broker.broker_state`, a
  `DaemonState` record); a fresh load from disk must match the head at every quiescent
  point.
- The manifest is reconstructed for a pre-manifest run only at `start`, never from a status
  call.

### 6.4 Modules and adapters under the run model

- A `Module` Protocol in `modules.py`: `NAME`, `CONSTITUTION`, `MODE`,
  `trigger(view, pending)`, `demand(view, pending)`, `telemetry(view)`, and the optional
  `observe(view, event)`.
- `ModuleView(run)`, read-only: `events` (a tuple of `Event`), `registry`, `paths` for
  reading a module's own files, plus two kernel methods: `record(kind, **fields)` persists
  a module-owned record through the kernel (for `hazard_tagged`: the tag into
  `hazards.json` and the activity record, exactly what the hazard module writes by path
  today, so the HAZARDS line of the published runs that carry tags does not change) and
  `hazards()` reads the file. `JournalView` is removed; `pending` keeps its shape.
- Modules load once per process from the held manifest: the daemon at `serve`, the CLI
  lazily for status.
- `assay module install` is the daemon operation `install_module` (section 7.2, #20): the
  daemon checks the owner token against the held hash, copies the file, checks the contract
  and the name, updates the held manifest and writes it, records `module_installed` in the
  activity log, loads the module; without a live daemon the command refuses and names
  `assay start`. While the daemon lives the manifest on disk is a record,
  not the source of truth. At the next start, `Run.load` admits a manifest entry only if
  the registry's `modules` list names its source or a `module_installed` activity record
  carries its hash; an entry without either is ignored and reported on the MODULES line
  (section 8.7).
- An `Adapter` Protocol in a new `adapters.py`, typed from the duck interface of section
  2.2: `factory(root, config)` returning a session with `observation`, `step(action, data,
  reasoning)`, the optional `finalize()` and `public_info`. #21 adds the optional `session`
  capability. The conformance test checks every adapter in its table against it.

### 6.5 Recovery under the run model (#16)

- The daemon writes `Mutation.claims` at spend time for `act` and `commit` steps: the
  parsed claims, verifier hashes included; the record keeps `reasoning` (`predict`,
  `because`, `declares`). A model-plan step's record carries its plan reasoning and no
  claims.
- `reconcile`, at start with the daemon dead, grades the stored claims against the stored
  response through the live path's grader, `grade_pending(run, claims, prior, pending,
  elapsed_s=None)`; `elapsed_s=None` makes every windowed claim UNGRADABLE with the actual
  "UNGRADABLE: recovered, step duration unknown"; the event is journaled with `predict`,
  `predict_ok`, `grade`, `declares` from the reasoning, and the note "recovered from broker
  mutation journal", and it is gated. A mutation record without `claims` (written before
  #16, or a model-plan step) is recovered as today, UNGATED, and the audit says why.

Landed by #16. Two details the bullets leave open: a bare act or step of a control arm
(`gate: optional`, `gate: off`) spends with an empty claim list, so its record carries
`claims: []` and recovery journals it as the live path does, UNGATED with the mode's
marker, counted among the permitted; and recovery is the grade and the journal line only:
the modules do not observe a recovered outcome, an aggregate claim on a recovered record
is not opened, and no receipt is written, since no command is waiting for one. The audit
names a recovered event without its prediction (`recovered_without_prediction` in
`audit.json`, the line `n of them recovered without its prediction: the record predates
1.2.0 or was a model-plan step; counted UNGATED above`); the independent checker needs
no change, since the ungated rule reads the fields. `grade_pending(run, claims, prior,
pending, *, elapsed_s)` takes the duration as a required keyword, so every live caller
passes the measured one and only recovery passes None.

### 6.6 Invariants and tests

1. One writer: while a daemon lives only it appends to `events.jsonl`, through
   `run.append`; the one append without a daemon is `reconcile` at start.
2. At every quiescent point the held head equals the head recomputed over the file;
   `verify_disk` is the test.
3. `Run.load` over a journal the daemon wrote yields records that re-serialize to the same
   lines; a test writes a thousand events through the daemon and checks that status, audit
   and view agree between the daemon's view and a fresh load (`tests/test_daemon_run.py`).
4. No function below the entry points reads `events.jsonl`: an AST test over `src/assay`
   and `src/assay_grid` refuses `load_events(` outside `run.py`, `core.py` and
   `analysis.py`.
5. Every published journal under `evidence/` loads through `Event.from_json` and
   re-serializes identically (a test over the 66 journals).
6. Two clients racing on one socket produce a contiguous journal (the daemon is
   single-threaded and the CLI holds the run lock; the test pins it with two clients that
   speak the socket directly, without the lock).
7. The replay gate passes unchanged.

### 6.7 What changes for the agent and the operator

Nothing visible: the same commands, the same lines. One paid action does constant journal
work plus the graders and one verification pass over the files.

## 7. The protocol, the errors and the surfaces (1.2.0, design note 2)

Status: a design note, implemented by #13, #14, #15, #11 and #23.

### 7.1 The error model

- `AssayError(message, *, code="UNSPECIFIED", kind="refused", hint=None)`. `code` is
  UPPER_SNAKE, unique and stable. `kind` is one of `usage` (the request is wrong: schema,
  syntax, a missing file, an unknown action; exit 2), `refused` (a well-formed request the
  kernel refuses by rule: the budget, the gates, a module demand, the batching law, the
  affordance check, the notes cap; exit 2, so the manuals' "exit 2" and the suite's
  `returncode == 2` assertions stay true, and the kind carries the distinction on the socket
  and in `--json`), `world` (the adapter or the world failed or refused at the kernel
  boundary; exit 3), `internal` (a bug or a corrupt file; exit 4), `invalid` (the run can
  no longer be scored or continued: tamper, chain or replay divergence, remote divergence;
  exit 5). `hint` is the next step in one sentence.
- The catalogue, `src/assay/errors.py`, lists every code with its kind and a one-line
  meaning. A test over the AST, in the pattern of the vocabulary test and covering
  `src/assay` and `src/assay_grid` (211 raise sites today), refuses a `raise AssayError(`
  without `code=`, and one whose `code=` is not a string literal or an `errors.NAME`
  attribute in the catalogue. The catalogue's kind wins for a catalogued code; the `kind`
  keyword applies only to `UNSPECIFIED`.
  Adapters, examples and tests are not covered: the two bench adapters raise `AssayError`
  without a code at twenty sites and keep working through the defaults. The argparse
  error path (`Parser.error`) raises with `CLI_USAGE`. The catalogue renders into
  `docs/ERRORS.md` by a script in the repository; a test asserts the file is current.
- The adapter boundary is the daemon's four calls into the session: `factory`,
  `observation`, `step` and `finalize`. Anything raised there, `AssayError` or not, becomes
  `WORLD_ERROR` (kind world) carrying the text; a `finalize` failure stays the warning on
  the receipt, as today. Anything else that is not an `AssayError` becomes `INTERNAL` (kind
  internal) with the traceback saved as today; ONBOARDING's troubleshooting section, which
  shows that line with exit 2, follows.
- Existing codes keep their names: `BUDGET_EXHAUSTED`, `LOCAL_REPLAY_DIVERGED`,
  `REMOTE_LEASE_EXPIRED`, `REMOTE_STATE_DIVERGED`; `UNGATED_STEP_REFUSED` is retired with
  the `step` operation (section 7.2) and an unknown operation is refused with
  `UNKNOWN_OPERATION` (kind usage); the module demand is
  `MODULE_DEMAND`; `REMOTE_SESSION_EXPIRED_OR_UNAVAILABLE` becomes
  `REMOTE_SESSION_UNAVAILABLE` with #21, the last rename: the codes freeze at the 1.2.0
  tag, like the outcome tokens.
- The CLI prints to stderr one line `ERROR | CODE | message` and, when a hint exists, a
  second line `NEXT | hint`; multi-line help (the claims table) follows as today. The exit
  code follows the kind. The `ERROR | ` prefix and exit 2 for usage and refused errors are
  unchanged.
- Over the socket the error is `{"v": 2, "ok": false, "error": {"code", "kind",
  "message", "hint"}}`; the client raises the same `AssayError`.
- With `--json` the error object is the only output, on stdout, and the exit code follows
  the kind.

### 7.2 The operation table and the protocol

- `src/assay/ops.py`: `Operation(name, request, result, handler, paid, owner, offline)`.
  `request` and `result` are record classes with `from_json`, `to_json` and a
  `json_schema()` classmethod (hand-written per record, no dependency). `paid` marks the
  operations that spend, `owner` the ones that need the owner token (checked by the daemon
  against the held hash), `offline` the ones the client runs from disk without the daemon.
  #24 builds the argparse parser from this table; the list below is complete.
- Daemon operations, over the socket: `ping`, `observe`, `act` (`action`, `params` as an
  object or null, `predict`, `because`, `at_event`, `declares`), `commit` (`steps` as a list
  of `{action, params, predict}`, or `plan`, with `at_event` and `declares`), `reset`
  (`because`, `at_event`, `declares`), and the owner operations `install_module` (`path`,
  `token`), `approve` (`action`, `token`), `waive` (`action`, `token`, `because`),
  `goal_ratify` (`id`, `token`). The daemon holds the approvals in memory, rebuilds the
  waivers from the activity log at load, and records each grant and use in the activity
  log as today, so the agent cannot write an approval into a file and the file two
  processes rewrote is gone; the three owner commands need the daemon alive. The `step` operation is
  removed: it was refused on every registry run and every run has a registry since the
  1.1.0 build; the test that asserted `UNGATED_STEP_REFUSED` asserts that an unknown
  operation is refused with `UNKNOWN_OPERATION` and no mutation is written.
- Offline operations, in the client from disk: `start`, `stop`, `status`, `view` (with
  `--export`), `audit`, `channel declare`, `channel list`, `export`, `spend report`,
  `goal propose`, `goal list`, `model init`, `model replay`, `model solve`, `module list`,
  `python`, `doctor`, `version`, `hooks install`, `hooks post-tool-use` (section 8.4),
  `serve-tools` (section 7.5).
- The wire: one JSON line per request, `{"v": 2, "token": "...", "op": "act", "args":
  {...}}`, one JSON line per reply, `{"v": 2, "ok": true, "result": {...}}` or the error
  object. The daemon reads the line, checks `v` (a request without it, or with another
  value, is refused with `PROTOCOL_VERSION`, kind usage), authenticates the token, looks
  the operation up, decodes the request record, calls the handler with the run, encodes the
  result; one function per operation (#24). The client checks the reply's `v` the same way,
  so a daemon that outlived an upgrade is refused rather than misread; the client sends the
  version of the package it belongs to.
- `action` and `params`: the client parses `NAME k=v ...` into the name and a scalar
  params object using the pinned registry (the coercion of `registry._coerce`), or takes
  `--params JSON` as given (#14); the daemon validates the object against the registry's
  schema (the scalar types until #14, the JSON Schema subset from #14) and refuses before
  any spend; the journal stores the validated object under `data`.
- Receipts come back as `Receipt` records; `result_text` renders them as today.

### 7.3 `--json`

Every command with a result record accepts `--json` and prints exactly one JSON document
on stdout: the result record (`Receipt`, `Status`, the audit report, the view record, the
channel list, the module list) or the error object, nothing else; stderr stays empty on
success. `python`, `doctor`, `version`, `start` and `stop` have no result record and no
flag. The view record is `{"event": Event, "previous": Event or null, "lines": [...]}`:
the two events and the rendered lines, since the frame extra's view is prose. Without
the flag the prose output is unchanged.

### 7.4 The `Status` record

The rule: every fact a status line prints is a field of the record, so the renderer
derives the lines and nothing is stored pre-rendered, with two exceptions: the module
advisory lines and the observation kind's own lines, which are produced by code the
kernel does not own.

Blocks, each optional, each a small record: `run` (world id, event, progress as completed,
total and label, paid, state), `mode` (local or remote, the lease text), `observation` (the
JSON object and `omitted_lines`), `actions` (advertised or registered names), `kind` (the
observation kind's record, rendered by the kind: for the frame extra the image path and the
advertised-actions line that `status_head_lines` prints today), `registry` (actions with
their schemas, flags and descriptions unless zero_prior, budget, gate, mode_note), `budget`
(cap, spent, remaining), `gate` (mode, unpredicted), `agenda` (goal text, source, achieved,
pending proposals), `foreign` (the imported-knowledge facts), `channels` (registered, host
values, declared readings with their event), `model` (fit, graded, rights, reason),
`hazards` (every active tag; the renderer shows four), `spend`, `aggregates`,
`mis_references`, `integrity` (the unpermitted ungated events), `anchors` (file, count,
last event, last failure, writable), `emergence`, `unit` (paid actions this unit, recent
hits and total), `claims` (the meters), `vacuous` (digest, graded, failed per verifier),
`advisories` (module lines), `recent` (history lines as records: event, paid, unit, action,
mark, change, state), `notes` (path, text, size, cap state, the demotion banner's facts:
the archive's unit and whether the notes changed since), `estimated_tokens`.
`inspect.status_text` becomes `render_status(status)` and produces today's lines byte for
byte, which the replay gate proves; `--json` prints the record; `--brief` (section 7.6)
drops blocks under the budget.

### 7.5 The surfaces

- The CLI: a parser built from the operation table (#24); each command is either an
  offline function over `Run.load` or a daemon operation through the client; the
  agent-facing lines are unchanged.
- The tool server (#15): the module `assay.server`, shipped as the optional extra
  `server` (the `mcp` package), started per run by the operator (`assay serve-tools
  --run-dir DIR`, over stdio). One tool per operation in the table: daemon operations are
  forwarded over the socket, offline operations run in the server process from disk, with
  `Run.load` on every call and no cache (the daemon appends between calls; a load of the
  largest ARC journal is 16 ms); `inputSchema` comes from the request record's
  `json_schema()`; the result is the record as JSON with a `text` field holding the prose
  rendering, so a model can read either; a tool error carries the error object. Command
  records in the activity log say `surface: "mcp"`.
- The hooks (#11): section 8.4.
- Activity command records gain `surface` (`cli` or `mcp`); the PostToolUse record of
  section 8.4 joins to them by the receipt's `end_event` when the tool output carries one,
  and by order and command text otherwise.

### 7.6 Token-aware output (#23)

`estimated_tokens = len(text) // 4` on receipts and status, labelled an estimate in the
docs. The registry key `status_budget` (tokens, optional) and `assay status --brief`:
the renderer drops the lowest-value blocks first when over budget (the notes tail, the
observation tail, the registry descriptions, the history beyond four lines) and appends
one line, `TRUNCATED | <blocks> dropped to fit <budget> tokens; assay view and assay
channel list show them`. The estimate appears in `--json` output only. The prose status
truncates only under `--brief` or when the registry sets `status_budget`; no published
registry sets it, so the replay gate is unaffected.

## 8. The trust model (1.2.0, design note 3)

Status: a design note, implemented by #10, #11, #19 and #31, and stated to users in the
README paragraph of section 8.8.

### 8.1 The parties and what each may write

| party | runs as | may write | may read |
|---|---|---|---|
| the operator (a human, or an agent with permission) | its own user | everything: the registry, the adapter, the modules, the run directory; holds the owner token | everything |
| the agent under evaluation | the same uid as the daemon, in its own shell | the run directory outside `.assay/` and `.claude/` (its verifier files, `model.py`, the knowledge file) and the one agent-owned path inside the state directory, `.assay/NOTES.md` | everything, the journal included |
| the daemon | the same uid, its own process | everything under `.assay/`, the anchor file | everything |
| the world (the adapter) | inside the daemon | its own files under `.assay/` (pack tier, section 2.2) | the registry's `control` block from the pinned copy; nothing else of the kernel's |
| agent code (verifiers, extractors, models) | a sandboxed subprocess | a scratch directory, nothing else | its own program file, the observation it is handed, the interpreter and its packages |

The uid is shared. Nothing on disk is protected from the agent by permissions, and no
secret the daemon holds is hidden from it (`.assay/broker.token` is 0600, which the same
user reads). The boundary is therefore made of three things: detection by the daemon
(8.3), refusal by the hooks in a Claude Code session (8.4), and the operator keeping the
token and the launch outside the agent's reach (8.6). A separate uid for the daemon is the
one real boundary and is an operator option, documented in ONBOARDING by #31 with the
exact steps (a dedicated user owning `.assay/`, the socket group-readable, the agent in
the group), not implemented by the kernel. The owner operations (approve, waive, ratify)
run in the daemon against the held token hash (section 7.2), so an approval cannot be
written into a file by the agent.

### 8.2 What the daemon holds and checks at start

`Run.load` (section 6.3) checks on every start: the journal's contiguity, the stored chain
against the recomputed head (an unexplained mismatch refuses the start with
`CHAIN_DIVERGED` and rewrites nothing), the anchor file's last head against the journal
prefix and its seal (8.3), and the module manifest against its two sources of authority:
the registry's `modules` list and the `module_installed` records of the activity log. A
manifest entry without either is ignored and reported on the MODULES line. This raises an
install from two file writes to three, one of them a forged activity record; it is not
security, and the note says so.

### 8.3 What the daemon verifies before every spend

Status: #20 landed the verification, the `TAMPER_DETECTED` refusal, the `tamper_detected`
activity record, the refusal of every later paid action and of an install, and the
status line; the sealing record and audit's reading of a sealed anchor are #10's.

Before every paid action the daemon calls `run.verify_disk()` (section 6.3): the journal's
length and recomputed head, the mutation log, `registry.json`, `config.json`, `chain.json`,
`owner.json` and the manifest against the held copies, an unreadable file counting as a
difference. While it refuses, `install_module` is refused too (the install would rewrite
a changed manifest), `ping` carries what differed, and `assay status` prints `INTEGRITY |
the daemon refused a paid action: <what differed>; ...` from it, since a registry,
configuration, chain, owner, mutation-log or manifest edit leaves no trace in the journal
the readers load. On a difference it refuses the action with
`TAMPER_DETECTED` (kind invalid, exit 5), appends an activity record `tamper_detected`
with what differed, appends a sealing record to the anchor file, `{"event_id":
<held count - 1>, "head": <held head>, "seal": "tamper_detected"}`, keeps its held head
and count, and refuses every later paid action the same way until it is stopped. It never
adopts the disk state and never appends to a changed file. `assay audit` and the kernel
treat a sealed anchor as "the journal ends exactly here": a prefix mismatch or any line
beyond the sealed event is anchors DIVERGED and the run is invalid for scoring, whatever
`chain.json` says, which is what makes the detection visible after the daemon is gone.
The checker in `verify/` reads no anchors and keeps its limit honest: it reports the
chain against `chain.json` and the published head only.

The remedy is the operator's: the anchor file is the operator's own file outside the run,
and removing the seal by hand lets the run resume, with the `tamper_detected` activity
record standing as the record of what happened. The next `assay start` after a tamper
replays the journal through the adapter and fails
on the first divergence (`LOCAL_REPLAY_DIVERGED`) or refuses on the chain
(`CHAIN_DIVERGED`) or the sealed anchor. An edit that rewrote the journal, the chain file,
the mutation log and the anchor file consistently, while the daemon was stopped, is caught
only by a head published or anchored outside the agent's reach. That is the honest limit
(8.8).

The daemon writes the next event with `id = len(events)` from memory. Between
`verify_disk` and `append` the world step runs, which can take seconds; an edit in that
window is not refused, but one that changed the number of lines becomes a contiguity
violation in the file, which every checker sees, and one that changed bytes is caught
before the following action.

### 8.4 The hooks (Claude Code sessions)

`hooks/` in the repository, installed into a run directory's project settings by
`assay hooks install --policy FILE [--deny PATTERN ...]`, which writes the hook entries
into `.claude/settings.json` and the policy (the refused paths, the deny patterns, the
anchor directory and the token file path from the operator) into FILE, outside the run
directory, whose path the hook commands carry; the hook scripts read nothing the agent can
write. Matchers: `Bash`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit` and, for the join,
`mcp__assay__.*`. Paths are resolved against `CLAUDE_PROJECT_DIR` and realpath'd.

- PreToolUse: refuse a `Write`, `Edit`, `MultiEdit` or `NotebookEdit` whose resolved path
  has the component `.assay` or `.claude` under the run directory (except
  `.assay/NOTES.md`), or lies under the anchor directory, or is the token file or the
  policy file. Refuse a `Bash` command that names any of those (the path component
  `.assay`, not the string `.assay/`, so `rm -rf .assay` and `ln -s .assay x` are caught)
  unless it is a single simple command whose first word is the launcher path pinned in the
  policy file (the absolute path of `bin/assay` or of the installed `assay` entry point,
  written by `hooks install`), or `"$ASSAY"` when the policy pins the value the operator
  exported into the session before the agent ran. The bare word `assay` and the variable
  are not trusted on their own, since the agent controls its shell: a command that
  assigns, exports, unsets or aliases `ASSAY` or `PATH`, or defines a function named
  `assay`, is refused. A command that names a refused path and contains `;`, `&&`, `||`,
  `|`, a newline, `$(` or a backtick is refused whatever it begins with. Refuse a `Bash` command that matches a deny
  pattern: the operator names the world's client libraries, hosts and commands per run, so
  the agent reaches the world only through `assay`. The refusal text names the rule and
  the allowed form. The policy is a text match over the command; it is the operator's tool
  policy made explicit and journaled, not a sandbox.
- PostToolUse on the same matchers: run `assay hooks post-tool-use`, which reads the hook
  JSON on stdin and appends one fixed-shape record to `.assay/activity.jsonl` under the file
  lock: `{"kind": "tool_use", "tool_use_id", "session_id", "tool", "command_prefix",
  "end_event"}`, with `end_event` taken from the tool output when it carries a receipt (the
  `EVENT | e<id>` line, or the record under `--json`) and null otherwise. A journal event
  joins to its transcript by `end_event`, and by order and command text when that is
  missing (parallel tool calls make order alone unreliable).
- A test drives the hook scripts directly on synthetic hook input: a refused write under
  `.assay/`, an allowed edit of `NOTES.md`, a refused compound command, a refused direct
  world call, an allowed `assay act`, and the `tool_use` record with its `end_event`.

### 8.5 The sandbox (#19)

One module, `src/assay/sandbox.py`, with one function `run_program(program, payload, *,
timeout, companions=(), cpu_seconds=None)` used by the verifier runner, the channel
extractor and the model runner. The program and its companions (the stored verifier or
extractor, `model.py` and the extractor files of the model's declared channels) are copied
into a fresh scratch directory, every payload string naming a companion is rewritten to
the copy, and the program runs from there as `python -I` (isolated: no `PYTHONPATH`, no
user site, the current directory not on the path; not `-S`, which would drop site-packages
and with them numpy, which verifiers may import), with an empty environment, the scratch
directory as the working directory, the payload on stdin and one JSON line back on stdout.
Nothing the program receives names the run directory. The kernel reads at most 1 MB of
stdout and 1 MB of stderr and kills the program past either; that, a crash, a timeout, no
output or malformed output grades INVALID with the reason in the words each runner has
always used (`verifier crashed (exit N): ...`, `extractor timed out after 5s`, `malformed
model output: ...`).

The limits are set by a prelude inside the interpreter before any agent code (the sandbox
tools create the process before the interpreter starts, and `bwrap` forks a helper that
`RLIMIT_NPROC` 1 in a `preexec_fn` would block): `RLIMIT_CPU` at the timeout rounded up,
or the caller's `cpu_seconds` (the model runner keeps its budget of `max(2, int(timeout))`
under a wall clock of `timeout + 5`), the hard limit one second later and `SIGXCPU` handled
in Python, so a runaway that leaves the handler in place ends with a message rather than a
core dump (a program that resets the handler or calls `os.abort` can still die by a
signal, and the wall clock is the backstop either way); `RLIMIT_FSIZE` 1 MB;
`RLIMIT_NPROC` 1 (the program cannot fork; a test proves a `subprocess` call fails); on
Linux `RLIMIT_AS` 512 MB and the BLAS thread cap (`OPENBLAS_NUM_THREADS=1` and its kin,
set before any import, since on Linux `RLIMIT_NPROC` counts threads and OpenBLAS creates
its pool at load). macOS cannot set an address-space limit (`setrlimit` refuses it), so
the memory limit is Linux-only, `doctor` and ONBOARDING say so, and the allocation test
runs on Linux only. The wall clock is the caller's timeout, enforced by the kernel process
with `SIGKILL`.

On macOS the process runs under `sandbox-exec` with a deny-default profile: `(deny
default)`, `(deny network*)`, then the rules, each with a comment giving its measured
reason, a later rule winning over an earlier one. `process-exec` on the interpreter
(`sys.executable` as named and as resolved) and the executables under its prefix and base
prefix, which the python.org framework needs, its `bin/python` being a stub that spawns
`Resources/Python.app/Contents/MacOS/Python` in place; the program can exec nothing
outside them, `/bin/sh` included. `file-read-metadata` and `sysctl-read` unfiltered (the interpreter's `os.uname`
reads `kern.ostype` and its kin by MIB, which only an unfiltered rule admits), then a deny
of `sysctl-read` for the process-argument keys (`kern.procargs`, `kern.procargs2`) by name,
which closes the `sysctlbyname` route to the arguments and exec-time environment of any
process of the daemon's uid, the daemon's included; the MIB form of that read bypasses the
sandbox's sysctl filter in XNU (measured: under one profile `kern.boottime` by MIB is
denied and `kern.procargs2` by MIB is not), so section 8.9 states what stays open.
`file-read*` on the root directory as a
literal (the dynamic loader aborts without it), the interpreter's prefix and base prefix
as real paths, the package directories of the interpreter's module search path outside
those prefixes (the `sys.path` entries that exist, carry a `site-packages` or
`dist-packages` component and lie under neither prefix: `uv run` keeps numpy in its
archive directory and a Homebrew interpreter keeps site-packages outside the framework
prefix, and with the prefixes alone `import numpy` fails; a source tree an editable
install puts on the path has no such component and is not admitted, so a run directory
inside a checkout stays unreadable), `/usr/lib`, `/usr/share`, `/System`,
`/private/var/db/dyld` where it exists, `/dev/null`, `/dev/urandom` and the scratch
directory, and nothing under the run directory, since every file the program needs was
copied into scratch; `file-write*` on the scratch directory and `/dev/null`. No
`mach-lookup`: interpreter start and numpy do not need it, and with it a program reaches
the pasteboard, launch services, the metadata and directory services. The rules were
measured on Darwin 25 with the kernel's interpreter and numpy and are OS-version
dependent, so the module keeps them in one place with a comment per rule.

On Linux the process runs under `bwrap --unshare-net --unshare-pid --die-with-parent`
(the sandboxed process dies with its wrapper, which the kernel kills on the wall clock)
with read-only binds of the same places (the prefixes, the package directories, `/lib`,
`/lib64`, `/usr/lib`, `/usr/lib64`, `/usr/share`, and `/etc/ld.so.cache` for the loader's
library lookup; a symlinked place is recreated as the symlink), a fresh `/proc`, a minimal
`/dev`, and the scratch directory bound writable as the working directory.

The mode is decided once per process by `sandbox_mode()`: `sandbox-exec`, `bwrap` or
`process-isolation-only`. On a platform with its tool, every allowed path is checked to
exist first (an absent optional path is omitted from the profile; an absent root or
prefix means the fallback with no probe), then a probe runs `python -I -c "import json"`
under the profile once. A probe that exits by a signal (the loader aborting under a
profile that no longer fits the OS), fails, hangs or cannot start settles the fallback for
that process with the reason, so a profile that no longer fits costs each probing process
one failure, never one per grading: the daemon probes once in its life, and so do `assay
start` and each `assay doctor`. Where neither tool is present, or
`ASSAY_SANDBOX=process-isolation-only` is set (the override the tests use; `assay start`
refuses any other value before anything is written, on a fresh start and a resume alike),
the process runs with the limits alone and `assay doctor` prints `sandbox | process
isolation only (no sandbox-exec or bwrap)`, with the probe's failure or `forced by
ASSAY_SANDBOX` in the parenthesis instead when that is the reason. On a stock Ubuntu 24.04
the bwrap probe fails with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`
until `kernel.apparmor_restrict_unprivileged_userns` is set to 0 or bwrap gets an AppArmor
profile (ONBOARDING section 7).

`config.json` records `sandbox`, the mode the creating process found at the run's
creation; `broker.json` records `sandbox`, the daemon's own mode, decided before it
reports READY and kept on its STOPPED and FINISHED records; `assay doctor` prints the mode
of the shell it runs in and warns when the recorded one differs from the daemon's or from
its own, as it does for the interpreter.

Tests: a verifier that reads `events.jsonl` fails, one that writes the run directory
fails, one that opens a socket fails, one that forks fails, one that execs a shell fails,
one that reads the daemon's arguments through `sysctl` fails (macOS), one that prints
past the output cap fails with that reason, one that allocates past the limit is killed
(Linux), and the same reading verifier passes under the forced fallback with the doctor
line saying so; a verifier importing numpy passes; `doctor` prints the mode, `start`
records it, the daemon records its own. The channels test that counted extractor calls
through a marker file the extractor wrote outside the run directory counts them through
the daemon's readings cache instead.

### 8.6 The operator protocol (#31)

The operator runs `assay start` with `--owner-token-file` pointing outside the run
directory and outside the agent's working set, installs the hooks with the policy file
and the token file in the refused paths, and starts the agent's session after that; the
agent never sees the token. The three benchmark protocols, the new-world example and
ONBOARDING describe that order, ONBOARDING gains the separate-uid section of 8.1, and a
test runs the counter example the same way: operator start, agent act, no token in the
agent's output.

### 8.7 Modules

Modules are pack tier: they run in the daemon with the kernel's privileges, loaded once
from the held manifest (section 6.4). The install path is the daemon operation with the
owner token; the start-time cross-check of 8.2 admits nothing else. The agent cannot
extend the harness from inside a run: it can write a module file, and the daemon will not
load it.

### 8.8 The paragraph for README

"ASSAY's record is tamper-evident, not tamper-proof. The agent and the daemon run as the
same user, so the agent can write the run directory. The daemon holds the chain head in
memory, verifies the journal on disk before every paid action, and refuses to continue on
a difference, sealing the anchor file at the last trusted event; in a Claude Code session
the shipped hooks refuse writes under `.assay/` and world access that bypasses `assay`;
the operator holds the owner token outside the run, and approvals and waivers are granted
inside the daemon against it. An agent that rewrites the journal, the chain file, the
mutation log and the anchor file consistently while the daemon is stopped is caught only
by a head published or anchored outside its reach, which is why heads are published with
every evidence pack and why a separate user for the daemon is the recommended setup where
the stakes warrant it. Nothing the agent asserts is load-bearing: every grade is computed
by the kernel from the world's own response, and the published journals replay through
the engine."

### 8.9 What is not claimed

Tamper-proofness; isolation of agent code beyond the sandbox's stated limits, which
differ by platform; any guarantee against an operator who controls the machine. Inside the
sandbox, `file-read-metadata` is unfiltered, so a program can learn the existence, size and
modification time of any path (the journal's size, whether `~/.ssh/id_ed25519` exists),
though not its content; `sysctl-read` still answers what every process may ask, the
hostname, the OS version and the hardware keys among them; and the process-argument keys
are denied by name only, since XNU's `kern.procargs2` handler does not consult the
sandbox's sysctl filter for the MIB form of the read (measured, section 8.5), so a program
under the daemon's uid can still read the daemon's arguments and exec-time environment, as
any process of that uid can, and echo what the operator's shell exported into a verdict.
The sandbox does not close that; the daemon under its own uid (8.1, the ONBOARDING section)
does, and an operator who shares the uid should hand the adapter its secrets through files
rather than the environment. The
paper's AI-control sentence is qualified by the paragraph above at its next revision.

### 8.10 Tests

Those of 8.3 (an append, an edited line, a rewritten `chain.json`, an edited registry, a
forged manifest entry, each detected before the next paid action; the sealed anchor read
by `audit`; the start-time chain and manifest checks), 8.4, 8.5 and 8.6.
