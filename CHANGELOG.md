# Changelog

All notable changes to ASSAY are recorded here. The form follows Keep a
Changelog. Dates are UTC.

## sonnet-haiku-5-5-baseline-gate (2026-10-28, a tag on the same branch, not a release)

The Claude Code + Stop gate control the pre-submission review asked for: the
operator layer of the ASSAY rounds with no ASSAY under it.

### Added

- `bench/arcagi/baseline/stop_gate_baseline.py`: the gate of
  `hooks/stop_gate.py` (loaded by path, the same rules) over the baseline's
  journal and NOTES.md, its message pointed at `play.py` and `NOTES.md`.
- `run_baseline.py --stop-gate coverage --prompt FILE --prefix NAME`: the
  gate armed before each session as the ASSAY runner arms it, its decisions
  in the session ledger; `baseline_player_gate.md`, the baseline prompt with
  the fix-2 budget sentence and the gate's rule.

## sonnet-haiku-5-5-baseline (2026-10-27, a tag on the same branch, not a release)

The Claude Code baseline the experiment lacked: the same ARC-AGI-3 public
games played by `claude -p` with no ASSAY.

### Added

- `bench/arcagi/baseline/`: `game_server.py` holds one game through the same
  `ArcSession` wrapper as the ASSAY runs and journals every counted action
  outside the player's directory in the fields `rhae.py` reads (`.arc/`);
  `play.py` is the player's only access (status, look, act, reset, history);
  `run_baseline.py` runs models x games under the ASSAY rounds' budgets (1500
  actions, $15 and 4 hours a session, the same pinned tools) with up to five
  sessions a game and a usage-limit pause; `baseline_player.md` is the
  prompt, the ASSAY player prompt without the harness.

## sonnet-haiku-5-5-experiment-2 (2026-10-27, a tag on the same branch, not a release)

The changes the second Haiku layer diagnosis (the two Haiku 5.5 runs on the
first tag, default effort and xhigh) recommended, on the experiment branch
and kept off `main`. `bench/arcagi/jobs/sonnet-haiku-5-5-r2.json` is the run.

### Changed

- The Stop gate accepts a STOP block that is new by content (its digest
  differs from every block the notes held at the launch) and whose RULES cite
  an event of this session; a first stop with more than half the cap left is
  held once unless its NEXT PROBE cites a bought event or a cost above what
  is left. Its message states the rule and counts the blocks. Release after
  three blocks in the session whatever was bought between them (a new block
  restarts the count, twelve in all), or after fifty paid actions bought
  under the hold. Placeholder `STOP:` lines (`none`, `n/a`, a parenthesis,
  `not stopped`) declare nothing. Both runners write the launch snapshot
  (`launch_snapshot`: last event, block digests, complete blocks).
- The player prompt: "Play to WIN. The cap is a limit, not a target", and
  the gate's rule in its words.
- `conclusion_gate` reads `STOP:` lines anywhere by the gate's rule, leaves
  numbered steps, disclaimers, placeholders and the block's own heading out
  of its verdicts, records an audit only while one is owed (and says "no
  audit demanded" otherwise), and keys the RESUME CHECK on the newest
  verdict's text instead of the notes' time.
- `wall_spend` counts the steps before its own in a batch.
- The state cap counts the active states declared on the current progress
  unit.
- The grid pack's object ledger leaves out components on the frame's edge
  and on HUD rows (rows that change on nearly every paid action).
- CONSTITUTION.md: the frame-world `assay python` paragraph moves to the grid
  section of `act --help` and the arc-agi-3 pack's README, corrected (`grid`
  is the current settled grid, `settled` the list); frame worlds declare
  extractor states "as well", not "instead"; the gate bullets state the
  rules.

### Added

- `game_over_retry`, a built-in module: a non-reset action after one the
  world already refused in GAME_OVER owes `--declare game_over=...`.
- The grid pack's regime ledger: for each click target the colors it was
  clicked in and the color under the mover, for each non-move action the
  color under the mover and the move before it; at a stop the pairs the unit
  showed and never exercised, color matches first.
- `json` in the frame world's `assay python`; a NameError lists the names
  defined.

### Not done

- `budget.usd` per session: the gate reads the run's reported spend, nothing
  in the batch runner reports a session's spend, Claude Code's Stop hook
  carries no cost, and the transcript writes it once, at the end. The
  release after fifty paid actions under a hold stands in for it.

## sonnet-haiku-5-5-experiment (2026-10-26, a tag on its own branch, not a release)

The changes the Haiku layer diagnosis of the 2026-10-25 Haiku and Sonnet 5.5
sweep recommended, built on 1.5.1 for one experiment and kept off `main`.
`bench/arcagi/jobs/sonnet-haiku-5-5.json` is the run.

### Added

- The Stop gate's `coverage` demand: a stop with budget left is allowed by a
  new STOP block with RULES (an event id of the run), UNTESTED and NEXT PROBE,
  not by a bare STOP line. The batch runner installs the gate when the job
  file sets `stop_gate` (`line` or `coverage`) and keeps its decisions in the
  ledger; `tools/eval/gate.py` holds what both runners share.
- `conclusion_gate`, a built-in module: the coverage-audit demand on a reset
  that did not follow GAME_OVER and on a verdict the notes' Plan gained since
  the last audit, and the RESUME CHECK at status.
- `gamble_streak`, a built-in module: after three missed milestone gambles on
  a unit, a gamble names its win-rule hypothesis (`win_rule`).
- The coverage ledger's object part for frame worlds: board objects no point
  action touched, on the status line of a one-action unit and in the gap
  phrase; regions and objects on the status line at a stop.
- Registry keys `frame_forms` (`admit` lets a frame world parse `cell`,
  `move`, `vanish` and `region`) and `state_cap`; `assay state retire NAME`
  and the `state_retire` tool.
- The frame-world `assay python` names the manual documents: `observations`,
  `key_delta`, `delta_lines`.

### Changed

- The player prompt `e1_player.md` no longer licenses a stop on judging the
  game unfinishable.
- The first line of an act receipt ends with the settled diff.
- `region` grades a local change: something changes inside the box.
- `wall_spend` speaks at 25, 50, 100, 200, 400 and 800 paid actions on a unit
  and no longer says "stop manual probing"; `miss_streak` counts world-model
  misses apart from gambles.
- CONSTITUTION.md: four world-agnostic sentences under "Before you conclude",
  the two new gates under "Gates you may hit", a frame-world `assay python`
  example.
- The ARC adapter refuses a non-RESET action in GAME_OVER before it reaches
  the game, which otherwise stranded the daemon.

## 1.5.1 (2026-10-22)

### Changed

- CI runs on Linux alone, every supported Python. The macOS cell is gone:
  the suite had been red on every push since 2026-10-16 because the runner's
  DNS made the legacy-api service miss its start deadline, and the macOS
  paths (the sandbox-exec profile, the launcher's re-exec) are covered on
  the owner's machine before a release. The published-journal check runs
  under the cell's interpreter with numpy, which `evidence/verify_all.py`
  has needed since 1.4.0, when it began importing the kernel's integrity
  module.
- The legacy-api service binds without the hostname lookup of
  `HTTPServer.server_bind`, which asks DNS for the name of 127.0.0.1 and
  can take half a minute on a Mac whose name is not in `/etc/hosts`.

## 1.5.0 (2026-10-17)

### Added

- Packs: a world ASSAY can referee is a pack, a directory with the adapter,
  the registry, the world's scripts and a manifest, `pack.json`
  (`worlds/README.md`, "The pack manifest"): the world id, the pack's own
  interpreter, its key files, the hook's deny patterns, the card, the
  player's note and the WIN in words. A pack without the manifest takes the
  defaults, so every pack of 1.4.0 runs as before.
- `worlds/recipes/arc-agi-3`, the ARC-AGI-3 pack: the adapter of the
  published runs (`bench/arcagi/adapter.py`, unchanged) behind a loader that
  reads the ARC API key from `~/assay/tokens/arc_api_key` inside the daemon,
  the 200-action registry with a goal, the 25 public game ids and the pinned
  interpreter. `tests/test_arc_pack.py` holds it to the benchmark's registry
  and pins.
- `/assay:connect`, the skill that builds a pack for a new world: the
  classification, the archetype and the smoke test that `/assay:attach` did
  before, ending with the pack in `~/assay/worlds/<id>/` and the command
  that attaches it.

### Changed

- `/assay:attach <pack> "<goal>"` starts a run on a pack with the user's
  goal: the catalog's, `/assay:connect`'s or anyone's. It builds the pack's
  interpreter with uv, checks its key files without reading them, writes the
  run's registry when the goal changes the goal text, and installs the
  hooks with the pack's deny patterns. Given a task and no pack, it builds
  one with `/assay:connect` first; a catalog pack serves a task only when
  the user asked for that pack's own world, since the catalog's packs play
  worlds built from a seed.
- The session runner restarts the daemon and runs the player with
  `ASSAY_PYTHON` set to the interpreter the run was started with, when the
  shell names none, so `/assay:run` resumes a world with its own packages
  from any shell.

## 1.4.0 (2026-10-16)

### Added

- `/assay:watch` and `/assay:diagnose`, the last two skills of the connector's
  design, with their modes in the evaluation runner.
  - `python3 -m tools.eval watch --run-dir DIR` prints one line whenever the
    run changes: the runner's state, the paid actions of the cap, held and
    missed, the last action and how it graded, the spend, the Stop gate's
    blocks. `--once` and `--until-end`; the skill follows a run with the
    Monitor tool.
  - `python3 -m tools.eval diagnose --run-dir DIR` reads the report's record
    against the README's five layers of reasoning and prints, per layer,
    each finding with its evidence (counts and event ids) and where to
    steer. A finding is a place to look, not a verdict.
  - The report no longer reads a `STOP:` line under the notes' Verified
    heading as a Verified claim.
  - `tests/test_eval_diagnose.py` covers each layer's symptom, the printed
    and JSON forms, the watch line and its end, and a real run through the
    CLI and daemon diagnosed at layers 1, 2 and 4.

### Removed

- The independent checker, `verify/assay_verify.py`, with its two test files
  (`tests/test_verify_checker.py`, `tests/test_verify_independence.py`). It
  existed so that a third party could verify a journal while the harness
  was closed; the harness is open source, so its own `assay audit` is the
  check. The tag `v1.3.0` holds the checker's last version, the one the
  paper describes.
  - `evidence/verify_all.py` (gate G3) judges every published journal by
    the verdict rules of `verify/JOURNAL_SPEC.md` with the kernel's own
    chain and ungated code, and still reports 66 journals, 0 failed.
    `tests/test_evidence.py` runs it and checks that it notices an edited,
    a deleted and a reordered line and an injected bare paid event.
  - `tools/eval/report.py` takes its verdict from the kernel's audit, which
    it runs through the launcher (`--assay CMD`, `--no-audit` to read the
    last `audit.json`), and the head the kernel stored in `chain.json`. A
    directory with neither audit nor run is reported as not audited.
  - The kernel tests that compared their verdicts with the checker's
    (`test_gate_off`, `test_gate_optional`, `test_reconcile`,
    `test_properties`) keep the kernel's own audit and the spec's reference
    rules.
  - `verify/` is the journal standard alone: `JOURNAL_SPEC.md`,
    `PREDICTION_GRAMMAR.md` and a README that says how to check a journal
    with the audit. The evidence packs' commands are the gate's, and their
    HEADS files say the published verdicts came from the checker of 1.2.0.
    The README, NOTICE, GUIDE, ONBOARDING, AGENTS, CONTRIBUTING,
    ARCHITECTURE, the release checklist's G3 row and one kernel docstring
    follow. The published heads, their recorded verdicts, `bench/` and this
    changelog's past entries stay as they were.

## 1.3.0 (2026-10-16)

### Changed

- The release of the plugin. The version is 1.3.0 in the package and in
  `.claude-plugin/plugin.json`, which now carries it, so an installed plugin
  updates by version rather than by commit. The README's first screen is the
  two install lines for Claude Code and one `/assay:attach` example, followed
  by the description as it was.
- The plugin under Codex. Codex 0.163.0 adds the repository as a marketplace
  from `.claude-plugin/marketplace.json`, installs `assay@assay` at 1.3.0,
  and lists the four skills. Each skill now says what `${CLAUDE_PLUGIN_ROOT}`
  means outside Claude Code: the directory two levels above its SKILL.md. The
  session mode's Codex template played the counter world to a WIN through the
  daemon from inside Codex's `workspace-write` sandbox. It is marked tested,
  with no Stop gate, and its JSONL output reports no cost.
- The words for a prediction. A prediction is what the agent writes before
  an action. An outcome is one checkable part of it, and a prediction
  carries several outcomes separated by `;`. Held and missed are the labels
  the code gives each outcome after the action. A held outcome in the record
  is a fact. Fact-checking is the name of the mechanism and grounding is the
  property it gives the world model. The word claim appears nowhere a person
  reads, except the three frozen spellings named below.
  `tests/test_vocabulary.py` enforces the rule over the kernel and the frame
  extra. The action's result is called a result, so that outcome keeps one
  meaning. The receipt line label `OUTCOME |` is `RESULT |`, the prose says
  the action's result and receipt result tokens, `modules.observe_outcome` is
  `observe_result`, and `server.Outcome` is `ToolResult`. What a reader sees
  change: the status line
  `CLAIMS | world-model misses ...` is `PREDICTIONS | world-model misses ...`
  with the same content. The MIS-REFERENCE line counts `prediction(s) named
  unregistered states`. The `claims` block of `assay status --json` is
  `predictions`. The error code `CLAIM_SYNTAX` is `PREDICTION_SYNTAX`, with
  the same kind and meaning. The grammar document `verify/CLAIM_GRAMMAR.md`
  is `verify/PREDICTION_GRAMMAR.md`. The grammar table in the help opens with
  `PREDICTION | one or more outcomes, separated by ";"` and the `--predict`
  placeholder reads `<outcomes>`. What changes for code: `records.Claim` is
  `records.Outcome`, `claim_bucket` is `outcome_bucket`,
  `predictions.parse_claims` is `parse_prediction`, `grade_general_claims` is
  `grade_general_outcomes`, `claims_help` is `prediction_help`, the
  observation-kind hook's `claim_patterns`, `claim_fields`, `claims_help` and
  `grade_claims` are `outcome_patterns`, `outcome_fields`, `prediction_help`
  and `grade_outcomes`, `status.ClaimsBlock` is `PredictionsBlock`, the
  module file `src/assay_grid/claims.py` is `src/assay_grid/outcomes.py`,
  and the `claims` key of the `pending` mapping a module receives is
  `outcomes`. What stays spelled as before, on disk and on the wire: the
  journal fields `predict`, `predict_ok` and `grade`, the keys of the grade
  objects, the `bucket` values `world_model` and `gamble`, the grade `kind`
  values (`noop`, `change`, `win`, `level_up`, `verify`, `channel_eq`,
  `channel_delta`, `channel_cross`, `aggregate`, `coerced` and the frame
  kinds), the keyword `ch`, every activity record kind, the receipts' keys
  (the key `outcome` among them) and result tokens, the `.assay/` file names
  and the journal spec version.
  Three frozen spellings still carry the old word and are allowed by name in
  the vocabulary test. The receipt result token `INVALID_CLAIM` is frozen,
  and it is also the `actual` prefix of a verifier grade that did not grade.
  The mutation record in `.assay/mutations.jsonl` keeps its key `claims`,
  which recovery reads back. The `mis_reference` activity record keeps its
  field `claim`. No journal byte changes. The replay diff over the 25
  published run directories passes with two entries added to its vocabulary
  map, one for each renamed status line.

### Added

- The repository as a Claude Code plugin (preview). `.claude-plugin/marketplace.json`
  makes the repository its own marketplace, with one plugin, `assay`, whose
  source is the repository root, so an install carries the kernel, the
  launcher, the skill and the catalog together: `/plugin marketplace add
  arjmandi/assay`, then `/plugin install assay@assay`. `.claude-plugin/plugin.json`
  is the manifest; it sets no version yet, so the preview tracks commits.
  While the plugin is enabled, `bin/assay` is on the Bash tool's PATH. The
  one skill, `skills/attach` (`/assay:attach "<task>"`), makes Claude the
  builder, the operator's side of a run. It classifies the task with the
  world card of `skills/attach/rubric.md`: step 0 (a world, or a program over
  worlds), a fit check (the world answers back to a program, many cheap
  graded interactions, a clock this version can carry), four three-option
  questions whose letters make a world code such as M-E-C-0, and the
  inferred clock, proxy and scarce-commit tags. It then starts a catalog pack
  or writes a world from an archetype's template, smoke-tests it in a scratch
  directory, and starts the run with the owner token in `~/assay/tokens/`
  and the hooks installed, as `skills/attach/start.md` says. It writes the
  player's prompt into the run directory and never plays the run itself. The
  plugin declares no hooks file and no MCP server: the hooks stay per run
  directory (`assay hooks install`), and a plugin MCP server would be
  configured per project rather than per run. `claude plugin validate .`
  passes, with two warnings: no version, and the developer `CLAUDE.md` at the
  root, which a plugin does not load.
- `worlds/`, a catalog of worlds that need no adapter code. The command-world
  engine, `worlds/archetypes/command_world.py`, is the session contract
  around a recipe's commands. observe prints the world's data, act takes the
  validated parameters on stdin and prints `{"status": "ok" | "refused"}` (a
  refusal goes through the observation and is spent and journaled), and
  setup builds the world. The recipe holds declarative win, lose and level
  rules over the data. The engine adds `workdir`, `last_result` and
  `refusals` to the data. A fault of the world scripts (an exit, a timeout,
  output that is not one object) restores the copy and raises, so the kernel
  spends nothing. A recipe that does not fit is refused at start, with its
  field named. Two archetypes run on it:
  - `local`, the copy mode: the world is rebuilt in `.assay/world/` at every
    start and replayed on resume, and a RESET restores the copy as it stood
    when the unit began. Its template is a switchboard with a hidden wiring
    and a jammed switch.
  - `repo`, the worktree mode: the repository is copied into `work/`, which
    the agent edits freely, and `RUN_TESTS selector=... repeat=N` runs the
    recipe's JUnit-writing test command over the tree. The observation
    carries the run counts, the failing ids and a content hash of the tree.
    A selector that would pass a flag to the runner is refused. The world
    declares no replay. Its template is a project with one bug and two
    failing tests.

  Every pack and template carries the same `adapter.py` stub, which finds the
  engine in the catalog or in the checkout the daemon runs from. The first
  pack, `worlds/recipes/spreadsheet-reconcile` (M-E-C-0), is three months of
  a bank statement against a company's books, generated from the seed, with
  five discrepancies a month drawn from eight kinds. A month closes when its
  lines are paired, recorded or flagged under the world's rules.
  `checks/score.py` scores any final copy against the answer key, which never
  enters the run directory, so a run under ASSAY and a baseline working on
  its own copy are scored alike. The kernel is unchanged: the engine imports
  only `SessionCapability` from `assay.adapters`, as the ARC adapter does.
  `tests/test_worlds_engine.py`, `tests/test_worlds_repo.py`,
  `tests/test_spreadsheet_reconcile.py` and `tests/test_plugin.py` cover them,
  through the real CLI and daemon where a run is involved. The three catalog
  adapters join the conformance table, and ruff now checks `worlds/` beside
  `src`, `tests` and `hooks`.
- The service archetype. `worlds/archetypes/service_world.py` makes a remote
  system a program drives into a world: an MCP server over stdio, through
  `worlds/archetypes/mcp_client.py` (a standard-library client), or an HTTP
  API.
  - **A paid action is one call:** a tool call, or a request with its path
    parameters, a `query` object and a `body` object.
  - **The observation** shows the answer trimmed (`response`), with the full
    answer saved under `.assay/world_state/responses/`. It also shows a
    dashboard of free reads made after every action, and the counts of calls
    and errors.
  - **An error answer is the world refusing,** and is spent and journaled.
    That covers an HTTP status of 400 or more, an MCP result with isError,
    and a JSON-RPC error. A service that cannot be reached is a fault, and
    nothing is spent.
  - **The recipe may declare:**
    - a local service the session starts on a free port and stops;
    - `${env:NAME}` credentials taken from the daemon's environment;
    - a built-in WAIT that advances a mock's clock or sleeps;
    - caps on scarce actions;
    - a verdict call that decides the WIN without entering the data;
    - a RESET that restarts the service;
    - replay, for a local service that starts the same every time. A remote
      service declares no replay.
  - **`worlds/archetypes/service/make_registry.py`** drafts the registry and
    the call map from an MCP server's tool list or an OpenAPI document. It
    converts schemas into the registry's subset and names what it drops, and
    it marks a tool not declared read-only or non-destructive, and an HTTP
    DELETE or PUT, as destructive.
  - **The template** is a notebook MCP server with a capped destructive
    delete.
  - **The second pack, `worlds/recipes/legacy-api` (S-E-C-U),** is a legacy
    CRM and its successor hub as one seeded local HTTP service with a
    logical clock. The task is to move the active customers into the hub. The
    documentation is drafted into the registry, and the agent has to find
    what it leaves out or gets wrong:
    - pages count from 0 and hold at most 25;
    - archived customers appear unless filtered out;
    - a rate limit with Retry-After;
    - an import cap of 20;
    - emails that must be lowercase and trimmed.

    The hub's hidden verdict decides the WIN, and `checks/score.py` scores
    any arm's hub.
  - **The attach skill** now builds service worlds, with a sandbox or a test
    account proposed first.

  `tests/test_worlds_service.py` covers the recipe's checks, the MCP client,
  the drafts, the conversion, and both worlds through the real CLI and
  daemon. Both worlds include a resume that replays, and the test confirms
  that the started service is gone when the run is. The two new adapters
  join the conformance table. Each pack's checker is loaded by its path in
  the tests, since every pack names its checker `checks/score.py`.
- `/assay:run` and the session mode of the evaluation runner, `python3 -m
  tools.eval session --run-dir DIR`. It plays one run the operator already
  started, one host session after another.
  - **Before each session,** `assay start WORLD` resumes the run (RESUMED
    while the daemon is up, a replay when it is gone).
  - **The player** is `claude -p`, `codex exec` or `opencode run` from a
    template, or any command given after `--`. Its environment holds neither
    the names the pinned registry lists under `secrets` nor
    ASSAY_OWNER_TOKEN_FILE, so a world's credentials stay in the daemon's
    environment.
  - **When a session ends,** the run decides. WIN, the action cap or the
    dollar cap ends the mode and stops the daemon. An approval or a waiver
    the session left pending ends it OWNER_NEEDED, with the owner's command
    printed and the daemon left up. A stop the player declared ends it
    SELF_STOP. Otherwise the next session starts, within `--max-sessions`,
    `--hours` and `--max-usd`.
  - **A usage limit** pauses the runner, and the session does not count.
    Two sessions in a row that exit non-zero without a paid action end it
    PLAYER_FAILING.
  - **A relaunched session's prompt** carries an operator's note when the
    owner acted since the previous one, such as an approval the daemon
    still holds.
  - **Every session's record** carries the host and its `--version`, the
    launch counts and the session's bill. The bill of a `claude -p` session
    is posted to the run with `assay spend report`, so `assay status` shows
    it and a registry's dollar cap applies.

  The Stop gate, `hooks/stop_gate.py` (standard library only, strict mypy),
  is the session's `/goal` with the journal as the judge. The runner merges
  it into the run directory's `.claude/settings.json` for a Claude Code
  player, beside the kernel's hooks, which keep it on a reinstall. It lets
  the player stop only at WIN, at either cap, at a pending owner action, or
  after a line beginning `STOP:` added to `.assay/NOTES.md` during the
  session; otherwise it blocks the stop and says what to do. After three
  blocks with no paid action between them it lets the stop through. It is
  inert outside the runner, which names its state file in ASSAY_STOP_GATE,
  so a person's own session in the run directory is never held. In a live
  check, a Haiku player told to stop after one `status` was blocked, drove
  the counter world to WIN in two actions, and was then let through ($0.10).
  The Codex and OpenCode templates are untested and get no gate in this
  version. The player's prompt in `skills/attach/start.md` now says how to
  declare a stop. The fake player gains `note:` lines and reports its
  environment's names. `tests/test_eval_session.py` covers the gate's rules
  and the hook process, the templates, the environment and the settings
  merge, and runs the fake player through the real CLI and daemon: to a
  WIN, through an approval, to a declared stop, and past a STOP file, a
  failing player and a usage limit.
- `/assay:report` and the report mode of the evaluation runner, `python3 -m
  tools.eval report --run-dir DIR`. It writes what one run verified as a
  Markdown document beside the run directory, `<run>.report.md`, with the
  same facts as a JSON record beside it, and nothing in the run directory.
  - **How it ended:** WIN, CAP, USD_CAP, SELF_STOP with the player's
    declaration, OWNER_NEEDED or UNFINISHED, by the Stop gate's rules and
    the runner's ledger.
  - **Integrity:** the verdict of `verify/assay_verify.py`, which the report
    loads by path, the chain head with the command that checks a copy
    against it, and the kernel's last audit for the anchors outside the
    run directory.
  - **What held and the misses:** the PREDICTIONS line exactly as `assay
    status` prints it; per action and outcome form, the events where it
    held and missed; each miss with what the code read instead.
  - **The player's Verified lines,** each with how the events it cites
    graded in the journal.
  - **Never exercised:** the coverage audit over the journal, the unit where
    the run stands as the kernel's audit reads it, the conclusions of
    impossibility journaled with their coverage audits, and the player's
    open questions.
  - **The owner's acts, the refusals by code, and the sessions and their
    cost** from the activity log and the runner's ledger.

  The facts about the world come from the journal alone, so anyone holding
  the run directory gets the same ones, and each section names the other
  files it reads. `--check CMD` runs an external checker, such as a pack's
  `checks/score.py`, and records its verdict. Over the 66 published
  journals the report's heads and verdicts equal the published ones. The
  skill refreshes the kernel's audit, adds a pack's checker when the world
  has one, and sets a declared stop beside what the run never exercised.
  `/assay:run` now offers it when a run ends. `tests/test_eval_report.py`
  covers the meters, the forms, the coverage against the kernel's coverage
  ledger, the Verified lines, the ending, the declarations, the escaping of
  the player's text, the command line and the check, one published journal
  against its pack's head, the runner's imports (the standard library and
  itself), and a run the fake player played through the session mode,
  against the kernel's own PREDICTIONS line, chain and audit.
- Two packs and `/assay:compare`.
  - **`worlds/recipes/flaky-test`** (M-R-C-0, the repo archetype): a
    sales-report library whose suite fails on about half its runs,
    because a sort keeps a set's hash-dependent order for ties. The world
    is won when the suite passes twenty runs of twenty. Its checker puts
    the project's tests and runner back, adds tests of its own, and runs
    them under thirty fixed hash seeds, so a retry, a skip or an
    alphabetical sort that breaks the documented order scores wrong. The
    project carries its own standard-library runner, `runtests.py`, which
    writes the JUnit report the repo world reads, so the pack needs no
    pytest.
  - **`worlds/recipes/hidden-grammar`** (M-E-N-0, the local archetype): a
    constructed language drawn from the seed, five rules, and a speaker
    who answers 40 questions with acceptable or not. The agent writes
    `accept.py`. Its checker scores the classifier on 400 held-out
    sentences, by rule, and names the rules the arm never tested with a
    sentence that broke only that rule.
  - **The compare mode,** `python3 -m tools.eval compare --pack DIR`, runs
    a pack's pre-registered `compare.json`. The assay arm is the session
    mode with its Stop gate, then the report with the checker. The goal
    arm is one `claude -p "/goal ..."` session on a bare copy of the world,
    through a `world.py` that runs the recipe's own commands with no
    prediction and no gate. Its acts are logged, and replayed in the copy
    mode to name a copy changed by hand. Both arms get the same model, no
    MCP server, the same tools, and the same dollars and hours per run. The
    rows say how each run ended, whether it claimed the goal, the checker's
    verdict and the cost. `COMPARE.md` counts the false completions. A
    changed design in a pre-registered state directory is refused.
  - **`skills/compare`** confirms the pack, the model, the seeds and the
    most it can cost, runs a dry run, and launches in the background.
  - **The claude host of the session mode** now passes
    `--strict-mcp-config`, so the player reaches the world through the
    launcher alone.

  `tests/test_flaky_test.py`, `tests/test_hidden_grammar.py` and
  `tests/test_eval_compare.py` cover the packs' worlds and checkers, both
  through the real CLI and daemon, the goal arm's copy and replay, how a
  `/goal` stream ended, and both arms with stand-in players.

## 1.2.0 (2026-10-09)

The first open-source release, under the Apache License 2.0 (`LICENSE`,
`NOTICE`), developed on `main` from commit 6ea56e4, the build the
experiments of October 2026 ran on and the paper calls 1.1.0. There is no
release branch: `main` is the integration branch, each issue lands as one
pull request, and the tag is cut on `main`.

### Which kernel the paper's campaigns ran on

The ARC-AGI-3 campaign of 2026-08-21 to 08-23 (first journal event
2026-08-21 19:15 UTC, last 2026-08-23 16:21 UTC) ran on the kernel published as
v1.0-rc1 (c4207c1, 2026-08-22 16:37 UTC, the repository's first commit) with the
agent manual then named `DOCTRINE.md`. Six of the 25 journals (cd82, tn36, sp80,
ft09, su15, ls20) began before that commit existed, so for them the kernel is
pinned by the G2 replay below and not by a commit hash. Three later
commits touched `src/assay` before the OOLONG final at 4dc53e1: 566bcab
(2026-08-25, the module field renamed from DOCTRINE to CONSTITUTION, after the
ARC campaign), e4c669d (2026-08-26, the broker survives a client hangup, the
client timeout became configurable through `ASSAY_BROKER_TIMEOUT`, the owner
token never leads with a dash), and 3143cfd (2026-08-26, a comment only).
Factorio M2 and the OOLONG four-arm comparison ran on that kernel. The 1.2.0
kernel is a refactor of it, verified by replaying the 25 published run
directories unchanged (the G2 gate below). Run configurations recorded
`"harness": "assay"` and no version until now: 1.2.0 writes
`harness_version` and `journal_spec` into `config.json`, and `assay version`
prints them.

### Taken from the experiment branch (exp/2026-10)

The release work started from main at 4dc53e1 and is `main` now (6ea56e4 and
after). The experiment branch is not merged. Two things were taken from it, by
cherry-pick and by file copy:

- The `gate` registry key (exp commit d94e536): `gate: required|optional`,
  default `required`. Under `optional` an `assay act` without `--predict` and a
  bare `--step "ACTION"` are accepted, executed, and journaled with `predict`
  null, `predict_ok` null, `grade` empty and the marker `gate_optional: true`.
  The audit still reports them UNGATED and keeps the run invalid for scoring,
  and additionally counts them as `ungated_permitted`. This is the control-arm
  switch the E1 experiment ran on. It is not the default and not a scorable
  mode.
- The coverage-audit module file `bench/arcagi/modules/coverage_audit.py`
  (exp commits aba2d5b and 45f595e, the second being the settled-frame
  comparison fix). It ships here as a loadable external module only. Its
  split into a world-neutral built-in plus a frame-world extra is a later
  entry in this changelog.
- `tests/test_gate_optional.py` from the gate commit, with its absolute path
  to the independent checker replaced by the checker's place in this
  repository, `verify/assay_verify.py`.
- `tests/test_coverage_module.py` reduced to the two tests that need no
  archived run directory.

Left on the experiment branch, deliberately:

- `tools/` entirely (the night orchestrator, job files, prompt templates,
  ledgers, status files). They carry machine paths and are experiment
  tooling, not harness code.
- `tests/test_night_orchestrator.py`, `tests/test_e3_prepare.py`, and the
  archived-run test in `tests/test_coverage_module.py`, which read absolute
  paths on the experiment machine.
- The E1 registries (`bench/arcagi/registry_e1_*.json`), the E2 registry
  `bench/arcagi/registry_e2_1500_coverage.json` (which carries an absolute
  module path), the two ungated constitution variants and
  `E1_CONSTITUTION_DIFF.md`. The paper reports E1 from the archive. Whether
  these ship in the repository is an owner decision (see the release review,
  section 7, O4).
- The resume tooling (`resume_at.py`, `notes_at.py`, `e2_prepare.py`,
  `e3_prepare.py`), the run launcher and the orchestrator.

### Added

- The Claude Code hooks (#11, design note 3 section 8.4). `hooks/pre_tool_use.py`
  in the repository, standard library only, is the PreToolUse script, and
  `assay hooks install --policy FILE [--deny PATTERN ...] [--launcher PATH]
  [--owner-token-file PATH]` installs it for a run directory: the policy
  (version 1: the run directory, the anchor directory, the token file, the
  policy file's own path, the launcher's absolute path, the `ASSAY` the
  operator exported, the deny patterns, the interpreter) goes into FILE
  outside the run directory with mode 0600, and eleven hook entries into
  `<run dir>/.claude/settings.json`, PreToolUse on `Bash`, `Write`, `Edit`,
  `MultiEdit` and `NotebookEdit`, PostToolUse on those and `mcp__assay__.*`,
  merged into the file as it is (a foreign entry stays, the harness's own
  are replaced by their command text, a second install is idempotent); a
  policy path, a launcher or a hook script inside the run directory is
  `PATH_INVALID`. The hook refuses with exit status 2 and one line on
  stderr, `HOOK | REFUSED | <rule>; <allowed form>`, which Claude Code shows
  to the agent as the denial reason: a write whose resolved path has the
  component `.assay` or `.claude` under the run directory (except
  `.assay/NOTES.md`), lies under the anchor directory, or is the token or
  policy file; a Bash command naming any of those (the path component, a
  glob that expands to it, a word resolving to them) unless it is a single
  simple command whose first word is the pinned launcher, or `"$ASSAY"`
  when the policy pins the exported value, with a compound command, a
  redirection or an unparsable command naming one refused whatever it
  begins with; a command that assigns, exports, unsets or aliases `ASSAY`
  or `PATH`, or defines a function named `assay`; and a command matching a
  deny pattern. Everything else is exit status 0 with no output. `assay
  hooks post-tool-use --policy FILE` appends one `tool_use` record to the
  activity log of the policy's run directory under the file lock,
  `{"kind": "tool_use", "tool_use_id", "session_id", "tool",
  "command_prefix", "end_event", "timestamp"}`, with `end_event` from the
  receipt's `EVENT | e<id>` line, the `end_event` field of a `--json`
  receipt or the record an `mcp__assay__` tool returned, null otherwise; a
  malformed event is `HOOK_INPUT_MALFORMED` and an unreadable policy
  `HOOK_POLICY_INVALID`, two new usage codes. Both are lifecycle commands
  (no run loaded, no command records of their own) under the `hooks` group,
  take `--json`, and are in the help fixture. ONBOARDING section 7 shows the
  install in the launcher pattern and the separate-user form, GUIDE section
  5 what the agent sees, CONSTITUTION that the hooks exist; ruff and strict
  mypy cover `hooks/`, and the world-name rule of the conformance test too.
  Hardened in review (122 Bash and 28 editor cases): the components are
  compared lowered, since the default macOS filesystem folds case, and
  refused wherever the path lies, `.claude.json` included; the operators are
  tested outside quotes, a bare `&` among them, so a two-claim prediction is
  not compound; the launcher's `python` and `export` sub-commands are never
  exempt, and `export --out` under `.assay` or `.claude` is `PATH_INVALID` in
  the kernel; the shell-state rule covers `ASSAY*`, `PYTHON*`, `PATH`,
  `BASH_ENV`, `ENV`, `PROMPT_COMMAND`, `LD_PRELOAD`, `DYLD_*`, `printf -v`,
  `mapfile` and a function named as the launcher; `eval`, `source`, `exec`,
  `xargs`, `trap`, `find -delete` and `-exec`, `git clean`, `rsync --delete`,
  `base64` decoding, `$'...'` and byte escapes are refused; the run directory
  and its ancestors are protected from `rm`, `mv`, `chmod` and their kind
  with `cd` followed; the policy protects the installation's own paths; the
  interpreter is pinned unresolved and `hooks install --check` and an `assay
  doctor` line run it on the script (`HOOK_CHECK_FAILED`), since a hook that
  cannot start fails open; a symlinked `.claude` refuses the install;
  `end_event` comes from the outermost receipt document only; an editor
  call without a path is refused; the NOTES.md exception matches the name
  exactly; `hooks install --check --policy FILE` checks a given policy, exit
  status 2 on failure; the 8.4 status paragraph says what the policy does
  not see and names the strong form.
- The tool server (#15, design note 2 section 7.5). `assay serve-tools
  --run-dir DIR` serves one run's agent-facing operations as MCP tools over
  stdio, under the server name `assay`, through the module `assay.server`
  and the optional extra `server` (`pip install 'assay-harness[server]'`:
  the `mcp` package, 2.3 or later and below 3, which the kernel never
  imports). One tool per operation, named after the command: `status`,
  `view`, `act`, `commit`, `reset`, `python`, `channel_declare`,
  `channel_list`, `model_init`, `model_replay`, `model_solve`,
  `module_list`, `goal_propose`, `goal_list` and `audit`. Not tools, by one
  rule in the module: the owner operations (`owner=True` in the wire table:
  `module install`, `approve`, `waive`, `goal ratify`), the daemon's own
  `ping` and `observe`, the lifecycle commands, and the operator's side
  effects beyond the run, `export` and `spend report`. The paid tools go
  through the daemon as the command line goes, the parameters validated
  against the pinned registry before the socket; the readers load the run
  from disk on every call and never cache it; `python` runs the source in a
  child process per call, as the command does, for at most 120 seconds
  (`PYTHON_FAILED` past it, the child's whole process group killed), so
  nothing the source does reaches the server; `assay python` itself now
  refuses a source that calls `sys.exit` as `PYTHON_FAILED`, as the tool
  does, where it exited silently with the code.
  A tool's input schema is its request record's `json_schema()` (the wire
  record for a daemon operation, a record of `server.py` for an offline
  one) with a `format` property, and `predict` required on `act` and on a
  commit step when the run's pinned registry requires the prediction; the
  paid records' schemas now describe each property in one line. The result
  is the text the command prints, as one text block, or, with `format:
  "json"`, the record `--json` prints (with `estimated_tokens` and
  `truncated` where the command line carries them) as the text block and as
  the structured content; a refusal is a tool error carrying the error
  object as the structured content and the command line's error lines as
  the text, an act without a prediction refused with `PREDICTION_REQUIRED`
  as on the command line; a failure that is no refusal is `INTERNAL`, its
  traceback in `.assay/server.log` (4,000 characters an entry, 1 MB the
  file). Activity
  `command_start` and `command_end` records carry `surface`, `cli` from the
  command line (`start` and `stop` included) and `mcp` from the server; the
  records' shape is otherwise unchanged, and their readers read by kind.
  `EXTRA_MISSING` covers the missing `mcp` package too. The prose of
  `channel declare`, `model init`, `model solve`, `goal propose` and `goal
  list` moved beside their records so both surfaces print one text; the
  lines are unchanged. The constitution gains the tool form, `assay --help`
  the `serve-tools` entry.
- Token-aware output (#23, design note 2 section 7.6). `--json` on `status`,
  `act`, `commit` and `reset` carries `estimated_tokens`, the prose the call
  would have printed (without its final newline) in characters over four, an
  estimate with no tokenizer, beside the record's fields; it enters no record
  on disk, and the `Receipt` record's never-set `estimated_tokens` field is
  gone. The registry key `status_budget`, a positive integer of tokens
  (anything else is `REGISTRY_INVALID` with a hint naming the form), renders
  every status under it; `assay status --brief` fits 1500 tokens, or that
  budget when it is smaller. Over budget the renderer drops the lowest-value
  blocks first, over the `Status` record's fields and each only as far as
  needed: the notes tail (the head that fits, four lines at least), the
  observation tail (eight lines at least), the registry descriptions, the
  history beyond four lines; then one line, `TRUNCATED | <blocks> dropped to
  fit N tokens; assay status --json carries them all`. `status --json` carries
  the whole record either way, with `truncated`, the blocks dropped. A receipt
  whose observation block was cut (40 lines, 200 characters a line) says so on
  one line after it, `OBSERVATION | N of M lines omitted, K line(s) cut at
  200 characters; assay view --event E --json shows it in full`, K counted
  over the lines shown. The NOTES header of the 120-line cap says `120 of M
  lines` when the file is longer, where it said `shown in full`; no published
  run exceeds the cap, so the replay gate cannot see it, and without `--brief`
  and without the key the status prose is otherwise unchanged, which the gate
  proves.
- Action parameters as a JSON Schema subset, and `--params` (#14, design
  note 2 section 7.2). A registry parameter is a schema with `type` one of
  `integer`, `number`, `string`, `boolean`, `object` or `array`, `enum`,
  `minimum`/`maximum`, `minLength`/`maxLength`, `properties` and `required`
  (no undeclared property is ever admitted), `items` with `minItems` and
  `maxItems`, nested as deep as the world needs; `int`, `float`, `str`,
  `min` and `max` stay accepted as aliases, so every existing registry is
  valid unchanged and its pinned copy and hash do not move. One validator in
  the kernel, `registry.validate_value`, with no dependency, refuses with
  `ACTION_PARAMS` naming the value by its path (`RUN program.lines[2]=7 is
  not a string`) and the action's form as the hint; the daemon runs it
  before any spend and the command line before sending. `assay act NAME
  --params '{...}'` or `--params @FILE` carries any parameters as JSON, the
  only form for an object or an array (and the one for a string with
  whitespace); `pname=value` stays as the sugar for scalars, and the two
  forms are refused together in one command. `--step` takes the JSON form
  `{"action", "params", "predict"}`, a JSON list of them, or `--step @FILE`
  holding the list, beside the `NAME pname=value :: claims` string. A schema
  nests at most 32 levels (`REGISTRY_INVALID` past it); one request line is
  at most 1,000,000 bytes, refused `REQUEST_MALFORMED` by the daemon before
  parsing (the connection answered, never hung) and `COMMAND_ARGS` by the
  command line for a `--params` or `--step` document, as are a repeated key
  in a JSON object and a nesting the interpreter cannot read; a rendered
  action line escapes the line separators JSON leaves raw (U+0085, U+2028,
  U+2029 and their kind), so it stays one line. A model plan carries its
  actions as `{action, params}` objects, written by `assay model solve` and
  validated at commit; a plan written in the old string form is refused with
  `PLAN_INVALID` and the hint to solve again. The REGISTRY block renders an
  object as its property names (`<object: lines[] note?>`), an array as its
  item type and count (`<array of >=1 string>`) and adds a `form:` line
  under such an action with the `--params` skeleton; a receipt and `assay
  view` render a nested value, or a string with whitespace, as compact JSON
  after its key, and the RECENT history lines clip a long action at 96
  characters. The journal format does not change: `data` was any JSON object
  under `assay-journal-v1` already. The new-world template gains `DIAL
  turns=<array of 1..4 integer>`.
- The error catalogue and `docs/ERRORS.md` (#13, design note 2 section 7.1).
  `AssayError(message, *, code, kind, hint, detail)` in `core.py`; `errors.py`
  lists 61 codes, each with its kind and a one-line meaning, and renders
  `docs/ERRORS.md` with `python -m assay.errors --render` (a test asserts the
  file is current). Every raise in `src/assay` and `src/assay_grid` names a
  code (224 call sites; an AST test refuses one without a catalogued code), with
  a hint where the message did not already say what to do. The daemon's
  calls into the adapter (the factory, the observation, a step) wrap
  whatever the world raised into `WORLD_ERROR`, and the kernel's own shape
  checks on the observation are `OBSERVATION_INVALID` (a broken adapter,
  not a refused action); a finalize failure stays the warning on the
  receipt; anything else that is not an `AssayError` is `INTERNAL` with the
  traceback saved. A request body that does not fit the operation's record
  is `REQUEST_MALFORMED` (usage, naming the record's fields, no traceback)
  and a reply the client cannot read `REPLY_MALFORMED`. The bench adapters
  keep raising `AssayError` without a code (`UNSPECIFIED`, outside the
  table). Where a message carried its next step after a semicolon, the step
  is the hint now, and `ACTION_PARAMS` names the registered form; the
  claims table rides as the error's `detail`, printed after the two lines,
  so `message` stays one line. The `command_end` activity record of a
  failed command carries `code` and `error_kind`, and `start` and `stop`
  write `command_start` and `command_end` records when the run state
  existed before them. The two unguarded `int` conversions in
  `parse_claims` refuse an aggregate count past nine digits
  (`CLAIM_SYNTAX`) instead of escaping as a `ValueError`.
- `--json` (#13, section 7.3, as amended in review). Every command takes
  the flag and prints exactly one JSON document on stdout, with stderr empty
  on success: `status`, `view`, `audit`, `act`, `commit`, `reset`, `channel
  list` and `module list` print their result record (`Status`, `View`,
  `AuditReport`, `Receipt`, `ChannelList`, `ModuleList`), every other
  command `{"lines": [...]}` with the lines it would have printed; on a
  refusal the error object `{code, kind, message, hint, detail}` alone,
  with the exit status by kind. The document is compact, with the journal's
  separators and no NaN (`spend report` refuses a `--usd` that is not
  finite). The `Status` record (section 7.4, `status.py`) holds every fact
  a status line prints as a field, none of it rendered text, and
  `render_status` derives the lines from it: the replay gate over the 25
  published runs holds the rendering byte for byte. The audit report is a
  record whose `to_json()` is the shape `.assay/audit.json` always had; the
  view record is `{event, previous, lines}` plus `exported` when `--export`
  wrote a file.
- The versioned protocol (#13, section 7.2). Every request is `{"v": 2,
  "token", "op", "args": {...}}` and every reply `{"v": 2, "ok": true,
  "result": {...}}` or `{"v": 2, "ok": false, "error": {code, kind, message,
  hint}}`; the daemon refuses a line without `v`, or with another value,
  with `PROTOCOL_VERSION` before the token and the operation (the integer
  2, not a float or boolean that compares equal), and the client refuses
  such a reply the same way, with the hint to stop and start the daemon, so
  a daemon that outlived an upgrade is never misread. A socket wait that
  runs out is `DAEMON_BUSY` with the hint to read the last event before
  acting again, never to resume under a step that may still land;
  `DAEMON_UNAVAILABLE` is the daemon not running.
- The typed records and the run model of design note 1 (#12). `records.py`
  holds the frozen dataclasses `Event`, `Claim`, `Grade`, `Receipt` with
  `ReceiptStep`, and `Mutation`: `from_json` validates the type of every
  known key and carries unknown keys through `extra`, `to_json` emits exactly
  the keys the spec names, and every one of the 66 published journals loads
  and re-serializes to the same bytes (`tests/test_records.py`). `run.py`
  holds the `Run`: `Run.load(paths, strict=...)` reads the configuration, the
  registry, the journal, the chain, the mutation log, the manifest and the
  owner hash once per process and never writes; `run.append` is the one
  writer of the journal, advancing the held head with the exact line it wrote,
  writing `chain.json` and anchoring when due; `run.verify_disk` names every
  difference between the files and the held copies as a `Tamper` (the daemon
  calls it from #20). Every function below the entry points takes the run.
  The `Module` protocol and `ModuleView(run)` (read-only events, the
  registry, the paths, `record` and `hazards`) replace `JournalView`; the
  `Adapter` and `Session` protocols in `adapters.py` state the adapter
  contract, and the conformance test checks every adapter against them.
  `mypy --strict src/assay` is clean (36 errors before), and mypy joins the
  dev extra; #27 puts it in CI.
- The tamper refusal and the daemon-side module install (#20). Before every
  paid action (an act, each step of a commit or a model plan, a reset) the
  daemon verifies the files on disk against the copies it holds: the
  journal's length and recomputed head (hashed as raw bytes), the mutation
  log, `registry.json`, `config.json`, `chain.json`, `owner.json` and the
  module manifest, a file that cannot be read or parsed counting as a
  difference. On a difference it refuses with `TAMPER_DETECTED | <what
  differed> (held ..., on disk ...)`, appends a `tamper_detected` activity
  record carrying every difference, keeps what it holds, appends nothing to
  the changed file, and refuses every later paid action and every install
  the same way until it is stopped; `ping` carries what differed and `assay
  status` prints it as `INTEGRITY | the daemon refused a paid action: ...`
  while the daemon lives; the offline readers keep answering and `assay
  stop` works. A journal byte that is not UTF-8 is a malformed line for the
  readers and `CHAIN_DIVERGED | line N malformed: ...` for a start, never an
  internal error. The sealing record in the anchor file and audit's reading
  of it are #10's. `assay module install PATH --token
  TOK` is the daemon operation `install_module`: the daemon checks the token
  against the hash it holds, pins the file, updates the manifest it holds,
  journals `module_installed` and loads the module, which speaks on the next
  action; without a live daemon (none in the process table) the command
  refuses and names `assay start`. `broker.broker_state` reads the daemon's
  held chain event, head and refusal as a record (`DaemonState`, the
  `ops.PingResult` of #24).
- The trust boundary, stated and closed where the note closes it (#10,
  design note 3). The owner operations `approve`, `waive` and `goal_ratify`
  are daemon operations in the wire table (`owner=True`, the token checked
  by the daemon against the hash it holds): an approval is held in the
  daemon's memory for its 600 seconds and consumed there by the paid path,
  so it no longer survives a stop; the waivers are rebuilt at `Run.load`
  from the `liveness_waived` activity records and held; a ratification
  writes `goal.json` from the daemon; `approvals.json` and `waivers.json`
  are gone, and every grant and use is recorded in the activity log as
  before. The three commands print what they printed and refuse without a
  live daemon with `DAEMON_UNAVAILABLE` and the hint to resume. On a
  tamper the daemon now appends a sealing record to the anchor file beside
  the `tamper_detected` activity record, `{"event_id": <held count - 1>,
  "head": <held head>, "seal": "tamper_detected"}`; `assay audit` treats a
  sealed anchor as the end of the journal: anchors DIVERGED and the run
  invalid for scoring, whatever `chain.json` says, with a problem line that
  names the seal and, when the journal changed after it, how; `assay start`
  refuses to resume over a seal with the new code `RUN_SEALED` (kind
  invalid, exit 5), and the remedy is the operator's, removing the line
  from their own anchor file (ONBOARDING section 10). At start, `Run.load`
  admits a manifest entry only if the registry's `modules` list names its
  file or a `module_installed` activity record carries its hash; an entry
  without either is held but never loaded, and the MODULES line reports it
  (`manifest entry not admitted: neither registered nor installed`), so a
  module file plus a manifest entry written while the daemon was stopped no
  longer runs at the next start. README gains the trust-model paragraph of
  section 8.8, verbatim, and sections 6 to 8 of `docs/ARCHITECTURE.md` say
  what landed and what the honest limits are.
  The `Status` record's `integrity` block carries `refused_code`, the code
  its INTEGRITY line names (`CHAIN_DIVERGED`, `RECORD_CORRUPT` or
  `RUN_SEALED`). From the review: the seal is a typed record
  (`integrity.Seal`); the remedy is ordered (put back the file the
  `tamper_detected` record names, then remove the sealing line last), and
  `audit` and `status` print `TAMPER | N tamper_detected record(s) in the
  activity log; ...` whenever such records stand, information beside the
  verdict (a `tamper` block in the `Status` record, `tamper_records` and
  `tamper_state` in the audit report); an anchor file that cannot be read as
  one JSON object per line refuses a start with `RECORD_CORRUPT` naming the
  line, reads as anchors DIVERGED in the audit and as `unreadable` on the
  ANCHORS line; `assay start` refuses an anchor directory it cannot write
  with the new code `ANCHOR_DIR_UNWRITABLE` (usage) instead of printing a
  warning, and a seal the daemon could not write (an `anchor_failed` record
  carrying `seal`) is an audit problem with anchors DIVERGED; a resume and
  the daemon refuse a `config.json` whose `anchor_file` is not the one the
  environment names with the new code `ANCHOR_FILE_MISMATCH` (refused), and
  the audit reads a recorded anchor file inside the run directory as anchors
  DIVERGED while the `anchor_env_mismatch` line stays what it was; a sealed
  run's ANCHORS line says `sealed at eN` instead of counting the seal; the
  owner's grant is stamped on the monotonic clock, checked by the gate
  before any spend and consumed by the daemon after the disk is verified, so
  a tamper refusal no longer burns it; the owner hash is compared in
  constant time; a wrong owner token leaves an `owner_refused` activity
  record naming the operation; `approve`, `waive`, `goal ratify` and
  `module install` take `--token-file PATH` (outside the run directory)
  beside `--token`, so the token never shows in the process list, and
  ONBOARDING's recipes use it.
- The 1.2.0 design notes, sections 6 to 8 of `docs/ARCHITECTURE.md`: the run
  model with typed records, the protocol with its error model and surfaces, and
  the trust model; `verify/JOURNAL_SPEC.md` states that `data` may hold any JSON
  object the registry schema defines (#33).
- `assay version`: the harness version (`assay.__version__`, the one source
  `pyproject.toml` reads), the journal spec it writes (`assay-journal-v1`),
  the interpreter and its Python version. `assay doctor` prints the same
  first. `LICENSE` (Apache License 2.0),
  `NOTICE` (the vendored OOLONG scorer under MIT, the two smoke packs under
  the dataset's terms), and
  `RELEASE_CHECKLIST.md` (what must not ship, the pre-tag greps, the secrets
  scan over every branch, the gates with their commands, the owner decisions
  that gate the tag).
- The coverage audit as a built-in module, `coverage_audit`, advise by
  default like every module (A1, split per the release review). It reads the
  journal's own change signal (the graded `change` or `noop` outcome, else
  the settled observation compared with the previous event's) and groups by
  the host progress unit, and provides: the untried and never-productive
  actions on this unit, a stall (eight consecutive non-productive actions),
  a halt on re-issuing the move that just graded FALSE (demand `revised=`),
  a halt on three identical failing moves, and a conclusion gate keyed on
  declarations (`impossible`, `unsolvable`, `unwinnable`, `absent`,
  `missing`, `dead_end`, `give_up`, or a `conclusion=` value naming one;
  demand `coverage_audit=`). The frame-world extra adds the grid regions a
  point action never probed, derived from the observed frame shape and
  surfaced only inside the gap phrases, never in the every-status line. The
  external module file this grew from (`bench/arcagi/modules/coverage_audit.py`
  on the experiment branch, and taken by the first entry of this release) is
  retired; a run directory that pinned it keeps running the built-in.
- `assay module list` shows each active module's constitution paragraph and
  telemetry beside its name, mode and origin.
- `gate: off`, the second control-arm mode beside `gate: optional`. Under
  `off` the instrument is removed: `--predict` is refused free on `act` and
  on every `--step`, nothing is graded, every paid action is journaled
  UNGATED with the marker `gate_off: true`, and the audit keeps the run
  invalid for scoring and names the mode that permitted the bare acts
  (`ungated_permitted_by`). The default stays `required`. Neither relaxed
  mode is scorable; they exist for the owner's control-arm experiments.
- The status line of a control-arm run is neutral (O10): `GATE | optional |
  n unpredicted action(s)` or `GATE | off | n action(s)`, the mode and a
  count, in place of the INTEGRITY alarm that E1 ran with. A mode permits
  exactly what it marks: an ungated event without the mode's marker still
  raises the INTEGRITY line, on any run. The audit is unchanged (such runs
  stay invalid for scoring, the permitted events counted apart).
- The journal standard and the independent checker moved in (A5, B2).
  `verify/` holds `assay_verify.py` (unchanged in logic, the standard library
  only, sharing no code with `src/assay`, which
  `tests/test_verify_independence.py` enforces by reading its imports and by
  running it with the harness unimportable), `JOURNAL_SPEC.md`,
  `CLAIM_GRAMMAR.md` and its own tests, under MIT (`verify/LICENSE`).
  `evidence/` holds every published journal with its committed head and the
  checker's verdict: the ARC-AGI-3, Factorio and OOLONG packs as published,
  the E1, E2 and E3 packs (journals and stored chains only, since the pinned
  registries and creation records carry local paths and stay with the
  archived run directories), the G4 pack, the live ft09 regression gate of
  this release (WIN 6 of 6 in 80 paid actions, CLEAN, head `cc65462f...`),
  the E1b pack (the prediction instrument removed, six journals that read
  INVALID FOR SCORING by design), the `factorio-110` pack (ironplate and
  circuit replayed on commit 6ea56e4, and the hand-played smoke) and the E5 pack
  (OOLONG 1M in batched bank mode on the same build, 0.740 for 43.39 USD against
  48.76 in single mode, with the paid phase at seven and a half minutes of 76).
  `evidence/verify_all.py` checks all 66 journals against their heads with
  the standard library alone, and the suite runs it. The former assay-verify
  repository is archived with a pointer here.
- OOLONG batch banking, the E5 variant (B3). `control.bank_mode: batch` in
  the registry (`bench/oolong/registry_200_batch.json`) makes `BANK_FACT`
  take several spans in one paid action (`spans`, base64 of a JSON list, all
  verbatim or the whole action is refused) and `SUBMIT` take a plain-text
  answer (`answer`, a space written as `_`) cited by the spans banked for the
  question. The census and the sealed scoring are the same code, the default
  (`single`) is unchanged, and the suite drives spam4k to WIN in both modes.
  The registry's `control` block, declared-only for the kernel, is read by
  the world here, which is what it is for.
- `assay stop`: stops the run's daemon cleanly (SIGTERM, wait, report). Works
  without run state, so an orphaned daemon left behind by a hand-deleted
  `.assay` can be stopped. Never sends SIGKILL.

- `assay module list` (active modules with mode and origin, plus any ignored
  file in `.assay/modules`) and `assay module install PATH --token TOK`, the
  owner-authorized, journaled way to add a module mid-run.

- `assay start --owner-token-file PATH` (or `ASSAY_OWNER_TOKEN_FILE`): the
  owner token is written to that file with mode 0600, outside the run
  directory, and the path is printed instead of the token. The default is
  unchanged (printed once). In every benchmark protocol so far the agent ran
  `start` itself and therefore held the token; the documents say so. The
  protocol rewrite is issue #31 of this milestone (O5).

- `assay act --help` and `assay commit --help` lead with the general claim
  table (every world) and put the grid forms under a labelled "frame worlds
  only" section that says they are refused on registry runs.

- `assay doctor`: Python version and path, numpy and pillow, the socket path
  length, the run state, the anchor directory, the daemon (alive, identified,
  answering), the adapter (resolved and dry-imported), the registry, and the
  interpreter the run started with. Works with or without a run in the
  directory. Exit 2 when something fails.
- `ASSAY_PYTHON`: `bin/assay` runs that interpreter first, and refuses loudly
  when it fails the dependency fingerprint instead of falling back. `assay
  start` records `python` in `config.json` and a resume from a different
  interpreter prints a warning that names both and `ASSAY_PYTHON`.

- `assay reset --declare "field=value"`: a reset can carry declarations, so a
  module demand can key on a reset (a conclusion expressed as giving up on the
  current state) and the declaration is journaled on the RESET event.

- Channel readings. Status prints a CHANNELS block on every registry run:
  the registered names, the host values (`goal`, `level`,
  `budget_remaining`), each declared path channel's current value, and each
  extractor channel's last graded value with the event it was graded on
  (cached by the daemon in `.assay/channel_readings.json`, so status never
  spawns an extractor). `assay channel list --read` computes extractor values
  fresh. Receipts print `CHANNELS | name: before -> after` for declared path
  channels that changed across the act or the batch.

- The conformance audit (A8.4): `tests/test_conformance.py` checks that the
  kernel imports nothing from `bench`, `assay_grid` or pillow at module level
  and names no world, that every adapter (the three benchmarks, the counter
  example, the template) exposes `factory(root, config)` and a session with
  `observation` and `step(action, data, reasoning)`, that the README
  quickstart passes verbatim, and that the new-world template passes the
  whole loop including the owner operations. `examples/new_world/` is that
  template: an adapter with a refusal reported through the observation,
  `finalize`, `public_info`, two progress units and a seed-derived
  determinism, a registry with every optional key present, and a README
  explaining each key. The conformance table in `docs/ARCHITECTURE.md` gains
  the template column.

- Packaging. `pyproject.toml` names the distribution `assay-harness`
  (import names `assay` and `assay_grid`), reads the version from
  `assay.__version__`, requires Python 3.12 or newer and numpy 2.x, puts
  pillow in the `grid` extra (frame worlds only), the ARC client in
  `arcagi`, pytest in `dev`, and installs the `assay` entry point. Three
  documented install paths: the launcher with no install, an editable
  install into a venv (recommended for benchmark worlds, the daemon inherits
  it), and pipx for the CLI alone. The daemon is the package module
  `assay.broker_server`, spawned as `python -m assay.broker_server` with the
  package's parent on the path, so an installed package and a checkout
  spawn it the same way. `bin/assay` demands numpy only; `assay doctor`
  reports a missing pillow as a warning naming the extra. `.gitignore`
  covers `.venv/`, `.scratch/`, build output and, so run state is never
  committed, `.assay/`.

- Documentation. `ONBOARDING.md`, the long form for attaching a world,
  derived from `docs/ARCHITECTURE.md` (install, the counter world in five
  minutes, the component model, the adapter, the registry, the operator's
  side, running an agent with the Factorio channel pattern as the worked
  example, modules, verify and publish, troubleshooting, the conformance
  checklist). `AGENTS.md` for coding agents. `CONSTITUTION.md` gains a
  Channels section (declare early, name referents, claim every action, the
  three forms with the recorded Factorio claims), a "Before you conclude"
  section (the coverage audit declarations), the coverage audit's demands
  under the gates, `budget_remaining` among the host channels and one line on
  `agg` claims. `GUIDE.md` and `README.md` updated for the extra, the install
  paths, the owner operations, the 1.2.0 limits and the license. Stale
  facts fixed: the Factorio README and PROTOCOL (M2 calibration, three of
  three won), the OOLONG README (M2 complete, the four-arm comparison), the
  channels, registry, inspect and modules docstrings, the pillow sentence.
  `CLAUDE.md` is rewritten for the harness alone (the fleet operations
  sections are gone, as is `docs/agent-journal`, and `.claude/settings.json`
  keeps only repository-relevant rules).
- Property tests for the claim parser, the chain rule, the ungated rule and
  the registry parameters, with hypothesis under the dev extra (#26).
- CI on every push and pull request (`.github/workflows/ci.yml`: ubuntu and
  macOS, Python 3.12 to 3.14, the suite, `ruff check`, the published journals
  against their heads), ruff configured in `pyproject.toml` with its default
  rule set and under the dev extra, `tests/test_hygiene.py` (no em dash or
  arrow in the source or the docs, no machine path, the old manual name gone
  from the code, over `git ls-files`), and the platform statement in
  `README.md`: macOS and Linux, Windows not supported (#30).
- The sandbox (#19): one module, `src/assay/sandbox.py`, runs every piece of
  agent code (verifiers, channel extractors, the world model) from a scratch
  copy under `python -I` with an empty environment, CPU, file-size and
  process limits and a wall clock, inside `sandbox-exec` on macOS (a
  deny-default profile measured on Darwin 25, kept in one place with a
  comment per entry) or `bwrap --unshare-net --unshare-pid` on Linux: no
  network, no fork, no exec of anything but the interpreter, no path into
  the run directory, no reading of other processes' arguments through
  `sysctl`, and at most 1 MB of output on each stream. Tests prove each
  refusal on the grading result and that a verifier importing numpy still
  passes.
- CI's Linux cells install bubblewrap and lift Ubuntu 24.04's AppArmor
  restriction on unprivileged user namespaces
  (`kernel.apparmor_restrict_unprivileged_userns=0`), so the suite runs under
  bwrap for real, and every cell prints its sandbox mode (#19).
- An evaluation runner in the repository, outside the kernel, `tools/eval/`
  (#22): worlds times arms times seeds from one JSON job file with relative
  paths only, each job run in the operator-first order (the operator's
  `assay start` with the owner token file outside the run directory, then
  the player's session in the run directory, the player a command template
  from the job file), with a concurrency limit, per-seed JSONL rows (paid
  actions, wins, receipts by outcome, tokens and dollars per action, read
  from the run's journal, receipts and activity log today, with a seam for
  `--json` once #13 lands), a summary with bootstrap intervals over seeds per
  arm, resumption through the kernel's own `start`, a finished job never
  rerun, a `--dry-run` that prints every command and writes nothing, and a
  lock per state directory. The E1 and E1b designs as job files under
  `bench/arcagi/jobs/`, the player prompts ported to `tools/eval/prompts/`
  with every machine path removed, a fake player for the suite's end-to-end
  queue against the counter example, and the release rule "`tools/` entirely"
  replaced by "no machine path under `tools/`", which the hygiene test
  enforces tree-wide. No kernel behavior change.
- Strict mypy in CI and a pre-commit configuration (#27). Every cell runs
  `mypy --strict src/assay src/assay_grid` after ruff, through uv under the
  cell's interpreter, and `[tool.mypy]` in `pyproject.toml` (strict, Python
  3.12 as the floor, the kernel and the frame-world extra) makes a plain
  `mypy` from the root the same run. The extra needed two annotations, which
  the kernel-only check had not reported because the editable install made
  the followed package look installed: `Counter[int]` for the lattice origin
  scores in `perception.py`, and a variable-length tuple for the neighbor
  steps in `analysis.py`, which the code extends with the diagonals.
  `.pre-commit-config.yaml`, for whoever wants the gate before each commit,
  runs the same ruff and mypy commands through uv and a grep for the em dash
  and the arrow over the staged files; it is optional, CI is the gate. No
  behavior change.

### Changed

- The remote session's words, and its rules as an adapter declaration (#21).
  The mode value is `remote` for new runs (`--mode local|remote`; a
  `config.json` carrying `competition`, the value of a remote run before
  1.2.0, is read as remote); the MODE line says `REMOTE` where it said
  `REMOTE COMPETITION`,
  and its middle field is the lease on every run (`no action-idle lease` on
  a local one, where it said `competition action/reset accounting`); the
  STARTED and RESUMED lines say `REMOTE` and carry the declared lease; the
  receipt's `scorecard finalization warning` is `finalization warning from
  the world`; `REMOTE_SESSION_EXPIRED_OR_UNAVAILABLE` is
  `REMOTE_SESSION_UNAVAILABLE`, the last rename before the codes freeze;
  `broker._competition_step` is `_apply_step`. The rules themselves come from
  the adapter: the optional `session` property declares a `SessionCapability`
  (`adapters.py`: `idle_lease_seconds`, `reset_on_fresh_unit`, `replayable`),
  recorded in `config.json` under `session` at start, and the kernel
  implements each part from it: the lease (declared by a world without
  replay, the record refuses the pair; its one rule is the resume refused
  past it, and the countdown on the MODE and RESUMED lines is a report),
  the fresh-unit reset no-op (live and in replay), and the single daemon
  life of a world without replay. At every later start the daemon holds the
  live declaration against the record and refuses to serve when they differ,
  with `DECLARATION_CHANGED` (kind refused, a new code) naming the fields,
  since the command line routes the resume by the record. The ARC adapter declares a
  fifteen-minute lease and no replay in remote mode, and the reset no-op in
  local mode, the mode the 25 published runs ran under, so their semantics
  are the ones they ran with. A world that declares nothing gets local semantics, which
  changes one behavior for every other world: the fresh-unit reset skip was
  the kernel's rule for every local world and is the ARC adapter's
  declaration now, so a RESET on a fresh unit reaches the world (the
  published Factorio and OOLONG journals open with no RESET, so none is
  affected). The optional `replay(transitions)` hook hands a world the
  recorded transitions at a resume. The conformance test's word list gains
  competition, scorecard and arcade, and the test drives the kernel against
  a fake adapter per declaration (`docs/ARCHITECTURE.md` sections 2.2 and
  2.3).
- The bench adapters and their protocols drop base64 (#14). Factorio's `RUN`
  takes `program` as the Python source itself, a plain string passed as
  `--params '{"program": "..."}'` or `--params @FILE`; OOLONG's `BANK_FACT`
  takes `text` and `span` as plain strings and `SUBMIT` takes `answer` as a
  string and `spans` as an array of strings, and the batch registry's
  `SUBMIT answer` no longer rewrites `_` as a space (a space goes through
  `--params`). The registries, `PROTOCOL.md` of both and `FLE_API.md` teach
  the JSON form; `tests/test_oolong_tenant.py` drives the adapter with it.
  The recorded results and the evidence packs are untouched: the old
  journals keep base64 in `data` and still verify (G3), and the adapters no
  longer decode it, so a Factorio or OOLONG run directory recorded before
  1.2.0 is history: a resume through the new adapters replays the base64
  text as the program or the span, the observation differs from the
  journal, and the operator sees `LOCAL_REPLAY_DIVERGED`.
- The error voice and the exit codes (#13). The command line prints `ERROR |
  CODE | message` on stderr (the code between the two bars), then `NEXT |
  hint` when the error names a next step, then the claims table where one
  followed before, and exits by the code's kind: 2 for `usage` and
  `refused` (every exit 2 of 1.1.0 is one of these, so the manuals' "exit 2"
  stays true), 3 for `world`, 4 for `internal` (`ERROR | INTERNAL | <type>:
  <message>` replaces `ERROR | internal: ...`, which exited 2), 5 for
  `invalid` (`CHAIN_DIVERGED`, `TAMPER_DETECTED`, `LOCAL_REPLAY_DIVERGED`
  and the remote codes, which exited 2). The eleven refusals that led with
  `CODE | ` carry the code beside the message instead, so their printed
  line is unchanged. The daemon answers with the error object and the
  client raises the same `AssayError`, so the `AssayError: ` prefix the
  flattened string carried before the message is gone from every refusal
  that crossed the socket. A world's refusal or failure inside a step was
  exit 2 and is `WORLD_ERROR`, exit 3.
- `action` and `params` on the wire (#13). `act` carries the registered
  name and the parameters as an object of scalars (or null) instead of the
  typed token `action_token`, and a commit step is `{action, params,
  predict}` instead of the `NAME k=v :: claims` line: the command line
  parses the token and the step syntax against the pinned registry
  (`registry.parse_registry_action`, `live.split_step`), and the daemon
  validates the object against the same schema before any spend
  (`registry.validate_action`: the name folded, every registered parameter
  present and no other, the scalar type, finiteness, the bounds and the
  enum, with the token parser's refusals). The journal stores the validated
  object under `data`, as it always did. `live.execute_action` and
  `execute_steps` take the validated forms; the observation kind supplies
  `history_change` (the change text of a history line) instead of
  `history_line`.
- The daemon is a dispatcher over the wire table (#24, design note 2
  section 7.2). `src/assay/ops.py` names the six daemon operations once,
  `Operation(name, request, result, paid, owner)`: `ping`, `observe`, `act`,
  `commit`, `reset` and `install_module`, each with its request and result
  record (small frozen classes with `from_json`, `to_json` and a
  hand-written `json_schema()`, carrying today's wire fields and refusing a
  key they do not take, so the daemon spends on nothing it did not read
  whole). The table holds nothing that runs: the daemon binds one `serve_*`
  function per name (`broker.HANDLERS`) and the command line its commands,
  so `ops.py` imports neither and the tool server (#15) reads it alone.
  `broker.serve` is the setup and the accept loop, `_Daemon.handle` the
  dispatcher (the token checked, the operation looked up, the request
  decoded, the name's handler called with the daemon and the held run, the
  result encoded), and the daemon's state is the `_Daemon` attributes; the
  closures and the dict cells are gone. One client, `broker.call(paths,
  operation, request)`, sends every operation and decodes its result record;
  `broker_ping`, `broker_observe`, `broker_gated`, `broker_state` and
  `broker_install_module` are wrappers over it. The `step` operation is
  retired: it was refused on every registry run, and the daemon now refuses
  it as an unknown operation, by name (#13 names the code); `broker_step`
  goes with it, and the stepper of every paid-action function is the
  daemon's. The wire op names are the table's (`act`, not `gated_act`); the
  client and the daemon ship together. `LOCAL_MODE` and `REMOTE_MODE` live
  in `core`; `assay.broker` still exports them.
- The command line is built from its own tables (#24): `cli.LIFECYCLE`, the
  four commands that run before any run is loaded, typed to return the exit
  status, and `cli.COMMANDS`, every command over the loaded run as an
  identifier (`channel_declare`, `goal_ratify`) with its path on the command
  line, its help, its arguments as `add_argument` spells them, the lazy
  claims epilog, and for a paid command the operation of the wire table it
  is the client of. `cli._parser` assembles the sub-parsers from them in
  their order and renders `assay --help` and every sub-command's help byte
  for byte as before (`tests/test_cli_help.py` holds it to a fixture
  rendered from the hand-written parser); the parsed command is looked up
  and run. `_parser`, `_start` and `main` are 23, 16 and 11 lines where they
  were 268, 260 and 46, and the 201-line `_dispatch` is a 15-line lookup;
  `serve` and `handle` are 32 and 19 where they were 143 and 92.
- The helpers that existed twice exist once (#24). `meters.py` holds the
  unit walk, the paid-action count of the current progress unit, the recent
  prediction window and the one `sharpness(events)` count, read by the
  status lines and by the modules; `registry.MODULE_MODES` is the one
  module-mode list; the sharpness module reads the count the CLAIMS line
  prints, over the agent's own claims (the machine predictions of model-plan
  steps left out, as before; the CLAIMS line's formula is unchanged); the
  coverage audit's frame change signal lives behind the observation-kind
  hook (`ObservationKind.changed`, `assay_grid.coverage.changed`); the
  frame claim forms live in the kernel's table `extras.FRAME_FORMS`, which
  `assay_grid.claims` reads back for its own patterns; and the decoding kit
  of `records.py` is public (`read_str`, `read_opt_int`, `wrong_type`,
  `refuse_unknown` and the rest), the one kit the journal records and the
  wire records decode through. `live.level_advanced`, a re-export with no
  caller, is gone: `Event.level_advanced` is the one predicate.
- Recovery keeps the prediction (#16, design note 1 section 6.5). The daemon
  writes the parsed claims of an act and of each commit step, their admitted
  verifier hashes included, into the mutation record at spend time
  (`Mutation.claims`; a model-plan step's record carries its plan reasoning
  and no claims, a reset's none). `assay start` with the daemon dead regrades
  a record's claims against its stored response through the live path's
  grader and journals the recovered event with `predict`, `predict_ok`,
  `grade` and `declares`, under the note `recovered from broker mutation
  journal`, gated by its fields, so the run stays CLEAN; before, the
  recovered event was UNGATED and the run was invalid for scoring from then
  on. A record without claims (written before this change, or by a
  model-plan step) is recovered UNGATED as before, and `assay audit` says
  why: `n of them recovered without its prediction: the record predates
  1.2.0 or was a model-plan step; counted UNGATED above`
  (`recovered_without_prediction` in `audit.json`). The independent checker
  is unchanged: the ungated rule reads the fields.
- The window rule on recovery (#16). The step's duration dies with the
  process, so on recovery a claim with an `@within` window grades UNGRADABLE
  with the actual `UNGRADABLE: recovered, step duration unknown` and the
  event's `predict_ok` is null, its own outcome, never a silent pass or
  miss. `predictions.grade_action_claims` is `grade_pending(run, claims,
  prior, pending, elapsed_s=...)`, the one grader of the live path and of
  recovery, with the duration a required keyword: the live path passes the
  measured one, recovery passes None.
- The daemon holds one `Run` for its life (#20). `serve` loads it strict
  once, every gated request handler receives it, `act`, `commit` and `reset`
  append through `run.append` and `run.record_mutation` with no re-read of
  the journal, the next mutation id is one past the held log's, and the
  modules load once at `serve` from the manifest the run holds (the CLI
  keeps loading them lazily for `status` and `module list`). Measured on the
  thousand-event test: a thousand steps through the daemon in about two
  seconds, `verify_disk` at about a millisecond per paid action over the
  443 KB journal they leave. Nothing printed by the offline commands changes;
  G2 proves it.
- Event 0 is the daemon's (#12). On a fresh run `serve` takes the first
  observation, writes the session's `public_info` into `config.json`, appends
  `START` through `run.append` and runs the observation kind's after-record
  work before it binds the socket and reports READY; `assay start` prints
  status from the journal the daemon opened. The CLI writes `config.json`, the
  registry copy, the owner file and the notes file before the spawn and never
  writes `config.json` after.
- The one-writer rule (#12). While a daemon lives only it appends to
  `events.jsonl`, through `run.append`; the one append without a daemon is
  `reconcile` at start. The id of the next event is the held count, never the
  line count on disk, and the daemon writes its mutation records through the
  run as well. Behavior modules persist through `view.record`, never by path.
- The strict and lenient loads (#12). `assay start` and the daemon load the
  run strict: a contiguity problem or a diverged chain refuses with
  `CHAIN_DIVERGED | ...` and rewrites nothing, so the evidence of an edit
  stays on disk; an absent or crash-behind chain is held as the recomputed
  head and repaired by the next append, as before. The readers (`status`,
  `view`, `audit`, `channel list` and the rest) load lenient: `audit` reports
  as it did, and status names a contiguity problem or a diverged chain on an
  INTEGRITY line instead of refusing; a line that does not decode is a
  finding the same way (the events before it are kept, the journal ends
  there) where it was an internal error. A malformed `chain.json` reads as
  diverged, and a chain counts as behind only by the one line a crash
  leaves. A manifest missing from a run pinned before manifests existed is
  rebuilt at `assay start`, never from a status call.
- Model promotion counts only the transitions recorded after the current
  model hash was first replayed (#18). The admission event is stored as
  `admitted_at_event` in the fit record, read from the earliest
  `model_replay` activity record carrying the hash (the record now carries
  its event), and batching rights need `missed == 0` over the whole fit with
  at least 20 counted transitions and 5 of them in the most recent quarter,
  so a model earns rights by predicting the future, not by fitting the past.
  The fit over every recorded transition is still computed and reported, and
  a fit record written before this rule reads exactly as it did.
- The vacuity rule (#17). A verifier is vacuous when its identity verdict
  (the verifier run on the unchanged observation) equalled its real verdict
  on every one of five or more gradings: it does not use the transition.
  Never having failed is an advisory line in status, not the flag.
  `.assay/verifiers/stats.json` is versioned, not migrated: a file written
  from now on carries `"rule": "identity"` with the counters under
  `"verifiers"`; a file recorded before keeps the never-failed rule, so the
  published runs render unchanged.
- Version 1.2.0 replaces 1.1.0 (#28): no 1.1.0 was released; the build it
  named is commit 6ea56e4.
- The frame-world tier left the kernel (A8.2). `src/assay_grid/` holds
  perception, the grid claim forms and grader, rendering (the one pillow
  import), the frame halves of status, result, inspect, view and export, and
  the grid namespace of `assay python`. The kernel keeps the frame encoding
  the journal format has, and one hook, `assay.extras`, selects the extra by
  observation shape (`frames` present), never by configuration, so the
  published run directories need no change. A dict run never imports
  `assay_grid` or pillow. The parser declares the frame-only `view` flags
  itself, inert on a dict run. The `ACTION` prefix matching in the affordance
  check is gone: a frame world's advertised ids are rendered as names by the
  extra before the check. The refusal of a grid claim form now names the
  form ("a frames-world form ... not admitted") instead of claiming the run
  has no grid observation. Every historical field name, state value
  and claim form is untouched. Proven by the replay diff over the 25
  published run directories: zero differences in `status`, `audit`, `view`
  and `channel list` against the main kernel.
- The world id rule (O7). A world id is any non-empty string up to 64
  characters with no whitespace, control characters or path separators,
  stored and shown as given (`My-World_1.v2` stays `My-World_1.v2`). The old
  rule, `[a-z0-9]{2,16}` with upper case lowered, is a subset, so every
  published id is still valid. The id is a label: the socket and anchor paths
  hash the run directory, never the id, which is why no path rule is needed.
  The error names what is wrong and states the rule. The Factorio task
  aliases stay as the published ids.
- `parse_claims(text, kind=None)` replaces `parse_claims(text, general=...)`.
  The core forms parse on every run; an observation kind's forms parse only
  when the kind is given, which nothing does in 1.2.0 (owner decision O1), and
  are refused by name otherwise. Test-only API change.
- `live.py` exposes its seam under public names (`paid_step`,
  `record_event`, `write_receipt`, `head_events`, `level_advanced`,
  `validate_batch_tokens`) so an observation kind's executor builds on it
  without importing private functions.

- Vocabulary (A8.3). The kernel's prose says world, not game, state, not
  board, and progress unit, not level, unless the world really is a game with
  levels: a world whose `win_levels` is 1 shows `progress 1/1` and "this
  unit" in status, receipts and module texts, a world with several units
  shows `level n/m` and "this level" exactly as before, so the published game
  runs render byte-identically. `src/assay/words.py` holds the display words
  and, in its docstring, the allowlist of what is frozen (journal fields,
  state values, claim syntax, grade `actual` texts, host channel names, the
  `game_id` keys, activity kinds, receipt outcome tokens, the state-directory
  layout). A test walks every string literal in the kernel against it.
  `assay start` names its positional `world_id`. The ARC mapping (world id is
  the game id, a progress unit is a level, `GAME_OVER` is the engine's state)
  is stated once in `bench/arcagi/PROTOCOL.md`.
- Em dashes and arrows out of every string and document. An arrow between
  two values is `->`, the form the CHANNELS receipt lines already used; a
  grade line reads `✗ claim | actual` and `! claim | actual` (the pipe is the
  kernel's field separator), and the SURPRISE receipt detail follows it,
  `prediction missed: claim | actual`; an em dash inside a `LABEL | text`
  line became a colon or a semicolon. Nothing graded, journaled or chained
  changes, except that the `actual` text of a channel delta or crossing
  claim in a new journal reads `0 -> 1` where it read the arrow (#30).
- The benchmark protocols start from the operator's shell (#31, design note
  3, section 8.6): `assay start ... --owner-token-file` with the file outside
  the run directory and the agent's working set, then the agent's session in
  the run directory, beginning at `assay status`; the token never enters the
  agent's transcript, and ratifications, approvals and waivers are the
  operator's. `CONSTITUTION.md` no longer tells the agent to run `start`:
  the run is started by the operator, the agent begins with `status`, and a
  directory that is not initialized is reported, not started. ONBOARDING
  and GUIDE follow the same order, ONBOARDING gains the separate-user option
  of section 8.1 with the steps for macOS and Linux, and
  `tests/test_operator_start.py` runs the counter example in that order.

- Agent code can no longer read the run directory, write outside its scratch
  directory, open a socket or fork (#19). The memory limit (512 MB) is
  Linux-only, since macOS refuses an address-space limit; where neither
  sandbox tool exists `assay doctor` reports `sandbox | process isolation
  only (no sandbox-exec or bwrap)`; `config.json` records the mode found at
  the run's creation and `broker.json` the daemon's own, and `doctor` warns
  when they differ. The channels test counts extractor runs through the
  daemon's readings cache instead of a marker file the extractor wrote.
- The claim meter named sharpness is named specificity (#25). The CLAIMS
  line prints `specificity N/M (P%)` where it printed `sharpness N/M (P%)`,
  the built-in module is `specificity`, the `claims` block of the `Status`
  record (`assay status --json`) carries `specific` where it carried
  `sharp`, and `meters.specificity` is the one count behind both surfaces.
  Specificity is the share of graded claims that are not coerced free text;
  a claim graded as `change` because the prediction was prose counts
  against it. The word changed because sharpness means something else in
  forecasting, the concentration of a predictive distribution, and the
  harness scores no distributions: a grade is binary. One formula is one
  number for one word: the module's advisory now reads the count over every
  grade of the run, the machine predictions of model-plan steps included,
  where it read the agent's own claims, so `MODULE specificity |
  specificity is N/M: over half the graded claims are coerced free text;
  they earn nothing. State checkable claims.` carries the N/M the CLAIMS
  line prints at that moment (the advisory fires on none of the 25
  published runs, so the replay is unchanged). A registry whose
  `module_modes` still says `sharpness` is accepted: validation pins it
  under `specificity`, a copy pinned under the old name is read as
  `specificity` (`registry.module_modes`; a registry naming both keeps the
  current name's mode), and a resume with `--registry FILE` compares the
  pinned copy under the current name, so a run pinned before the rename
  resumes whether FILE says `sharpness` or `specificity`. The replay gate's
  vocabulary map carries the renamed CLAIMS line.
- Addressable states, where the code said channels (the articulation of
  2026-10-08, the addressable world model and its five layers). The feature
  an agent declares over the observation, a dotted path or an extractor, and
  claims against with the `ch` forms, is an addressable state everywhere a
  person reads it. The command is `assay state declare` and `assay state
  list`. `assay channel declare` and `assay channel list` answer as the same
  commands for one release: the retired word is replaced before the parse
  (`cli.command_line`), so the help lists `state` alone and the activity
  records carry `state`. The status block is `STATES |` where it was
  `CHANNELS |`, the declaration prints `STATE | declared NAME`, the receipt
  lines read `STATES | name: a -> b`, the EMERGENCE line says `declared
  states N` and the MIS-REFERENCE line `named unregistered states`. The
  `--json` documents carry `states` where they carried `channels`: the
  `Status` record's block and its `emergence` block, the receipt's lines and
  the `StateList` record of `state list`. The tool server's tools are
  `state_declare` and `state_list`, with no alias, since the server is new
  in 1.2.0. The error codes are `STATE_UNKNOWN`, `STATE_DECLARE`,
  `STATE_CAP` and `STATE_REDEFINED` where they were `CHANNEL_UNKNOWN`,
  `CHANNEL_DECLARE`, `CHANNEL_CAP` and `CHANNEL_REDEFINED`, with no alias,
  since the codes are new in 1.2.0 and nothing published carries them. A
  `model.py` names the states it predicts in `STATES`, and `CHANNELS` is
  read for one release. The module is `states.py` (`declare_state`,
  `state_value`, `state_readings`, `StateReadings`, `StateList`,
  `state_list_of`, `state_list_text`, `state_declared_text`,
  `state_change_lines`, `grade_state_claim`, `check_state_references`,
  `records.STATE_KINDS`). The journal keeps every spelling: the claim kinds
  `channel_eq`, `channel_delta` and `channel_cross`, the keyword `ch`, the
  `channel` field of a grade. Beside the journal, the activity kind
  `channel_declared` with its `channel` field, the `mis_reference` record,
  `.assay/channels.json`, `.assay/channels/`, `.assay/channel_readings.json`,
  and the keys of `model_fit.json`, `model_plan.json` and `aggregates.json`
  keep theirs. The receipt files under `.assay/receipts/` and the activity
  log's receipt records carry their state lines under `states` from 1.2.0
  on, where earlier runs carry them under `channels`. Nothing reads that key
  back, and the records carry an unknown key through. A run directory
  written by an earlier kernel loads and resumes unchanged, its cached
  extractor readings included. The replay gate's vocabulary map carries the
  EMERGENCE and MIS-REFERENCE lines, and its mask covers both `CHANNELS |`
  and `STATES |`. The entry "Channel readings" under Added above describes
  the block and `assay channel list --read` under the earlier words.

### Removed

- `core.load_events`, `core.append_event`, `integrity.extend_chain` and
  `modules.JournalView` (#12): the run's held events, `run.append` and
  `ModuleView` replace them, and an AST test refuses a journal reload below
  the entry points (`tests/test_run_model.py`).
- The legacy numbered-action path (O2): runs started without a registry,
  which spoke `ACTION1..7` and `ACTION6:x,y` with a 0 to 63 bound, had the
  CLI write their events, and admitted the grid claim forms. `assay start`
  now requires `--registry` for a fresh run (a resume uses the pinned one),
  `assay act` takes `NAME pname=value ...` on
  every run, and every paid command goes through the daemon's gate. A
  directory that owns such a run (from before registries existed) can still
  be inspected (`status`, `view`, `audit`) but is refused on resume. With the
  path went the executable-rules tier that only ran there (`rules.py`, `assay
  rules help|init|replay|solve`, `assay commit @.assay/plan.json`, the RULES
  and PLAN status lines): its registry counterpart, the general world model
  (`assay model`), stays. None of the 25 published run directories used
  either, and the replay diff is unchanged.
- The OOLONG length-ladder packs (`synth128k`, `synth1m`, `synth4m`: 14 MB of
  upstream dataset text, O3). Their manifests stay, now with the sha256 of the
  questions file beside the corpus's, and `bench/oolong/packs/build_pack.py
  fetch <id>` rebuilds any of them on first use from the pinned dataset
  revision (ASSAY's cache, the Hugging Face hub cache, or one download) and
  verifies the result against the manifest, deleting a mismatch. `verify`
  checks a built pack with the standard library alone, and the adapter names
  the fetch command when a pack with a manifest is not built. The `oolong`
  extra installs pyarrow and pandas for the builder. The two smoke packs
  (`spam4k`, `spam8k`) ship whole. The repository's history was rewritten on
  2026-10-07 to drop the six files (the record is in the archive's release
  log; the greps that keep the text out are in `RELEASE_CHECKLIST.md`,
  section 3).

### Fixed

- `assay start` stops the daemon it just started when the reconstructed
  world differs from the latest event (#21). The resume refused with
  `LOCAL_REPLAY_DIVERGED` and left that daemon READY, so the next paid
  action would have spent on a world the journal does not describe.
- The two adapters that reached into the kernel (#21). The ARC adapter
  imported the mode constants from `assay.broker`; it reads the mode from
  the config and declares its session rules instead. The Factorio adapter
  read `.assay/mutations.jsonl` at replay to recover its tick deltas and
  appended a courtesy copy of its namespace watch to `.assay/activity.jsonl`;
  it takes the deltas from the `replay` hook, and the copy is dropped, since
  the observation body carries the watch and event 0 journals it. Section
  2.2's rule, that an adapter never reads the journal and never writes under
  `.assay/` except its own files, is true of every adapter in the table.
- A grid claim on a dict run is refused by name whether or not the extra and
  pillow are installed (#24). `parse_claims` recognized the frame forms
  through `extras.all_kinds()`, which imported `assay_grid`, and so pillow,
  on a dict run, against the stated invariant, and when pillow was absent
  the import failed silently and the claim became a note. The kernel holds
  the four forms by name and shape (`extras.FRAME_FORMS`) and refuses from
  there, with the same text; `all_kinds()` is left to the claim help. The
  pin that a dict run never imports the extra gains its runtime form through
  `parse_claims`, with the extra importable and with it blocked.
- A model plan's receipt reports the module advisories (#24). `assay commit
  @.assay/model_plan.json` consulted the modules and discarded their
  advisory lines; they now ride on the receipt and print after the outcome,
  as on an act, a hand batch and a reset. The one behavior change of #24.
- Daemon lifecycle (ranked fix 1). The daemon is identified by process, not by
  the pid stored in `broker.json`: a live process running `broker_server.py`
  with `--run-dir` naming the directory. A stale descriptor (pid reuse after a
  reboot) is never signalled and is marked `STOPPED`. The daemon handles
  SIGTERM: idle it exits at once, inside a step it finishes the step, journals
  it, replies, then exits, writing `status: STOPPED`. `assay start` on a run
  whose daemon does not answer but is alive, ours, and still owns its socket
  refuses with "busy or hung" and names `assay stop` instead of killing it mid
  action. A fresh start in a directory an orphaned daemon still serves is
  refused until `assay stop`. The liveness probe's timeout honors
  `ASSAY_BROKER_TIMEOUT` within 0.5 to 10 seconds.
- Reconcile race (ranked fix 2). Orphan recovery (`reconcile_mutations`) ran
  on every CLI command under the CLI's own lock, which the daemon never takes.
  After a client died, a concurrent `assay status` could recover the mutation
  the daemon was still grading, and the daemon then appended its own graded
  event for the same mutation id: the run became both invalid and
  double-counted. Recovery now runs only in `assay start`, and only once the
  daemon is confirmed dead or absent. The audit reports a spend
  that is in the mutation journal but not yet in the timeline as information,
  never as a verdict. No cross-process lock was added around the daemon's
  write window on purpose: it would block `assay status` for the length of a
  slow step, and with recovery confined to a dead daemon it is not needed.
- Anchors (ranked fix 3). The anchor file is pinned in `config.json` at start
  (`anchor_file`), so every later command anchors to and audits the same file
  whatever `ASSAY_ANCHOR_DIR` says in that shell. Runs without the key use the
  environment as before. Status prints `ANCHORS | <file> | n anchor(s), last
  e<id>` on registry runs. A failed anchor write is journaled to activity and
  shown in status instead of swallowed. Start warns when the anchor directory
  is not writable. The audit reports `anchor_env_mismatch` when the
  environment disagrees with the recorded file (information, not a verdict).
- Module manifest (ranked fix 4). External modules used to be every `.py`
  file under `.assay/modules/`, loaded on every consult, which let an agent
  install a module by writing a file. Now `assay start` writes
  `.assay/modules/manifest.json` (name, file, source, sha256, origin) for the
  registry's `modules`, and only listed files whose hash still matches are
  loaded. A stranger file or an edited pinned file is ignored and named in
  status and in `assay module list`. Module files are loaded once at pin or
  install time to check the contract and refuse a NAME that clashes with a
  built-in or another entry, before any spend. Runs that predate the manifest
  rebuild it from the registry's `modules` list by file name, so they keep
  their modules. TODO(owner: O6): the alternative (keep the glob, call the
  channel unguarded in the paper) is a one-line revert in
  `modules._load_external`.
- First-hour errors (ranked fix 6). The adapter spec is resolved and
  dry-imported in a subprocess of the serving interpreter before the daemon is
  spawned or anything is written: a missing file names the directories
  searched (run directory, then working directory), an unimportable module
  names the interpreter and the remedy, a missing factory is named. A file
  spec is recorded in `config.json` as an absolute path. The world id error
  states the rule. An exception that is not
  an `AssayError` prints one line, `ERROR | internal: <type>: <message>`, and
  saves the traceback to `.assay/last_error.txt` instead of dumping it.
- Interpreter drift (ranked fix 7). The daemon runs `sys.executable` of the
  CLI that started it and nothing read the recorded path. Now it is recorded
  in `config.json`, checked on resume, pinned by `ASSAY_PYTHON` in the
  launcher, and reported by `assay doctor`.
- Declares on commit steps (ranked fix 8). `assay commit --declare` reached
  the modules but was never written to the step events, while `assay act
  --declare` was. Every step of a batch now carries the declaration, redacted
  like the single-act form. Declaration counts over the historical journals
  are therefore lower bounds for batches.
- Test hygiene (ranked fix 9). The suite isolates `ASSAY_ANCHOR_DIR` and
  `XDG_CACHE_HOME` under the pytest temp root for the whole session (a
  contributor's first `pytest` no longer writes anchors under their home
  directory) and stops any daemon still serving a directory under that root
  when the session ends. No test reads an absolute path on the authoring
  machine. Two new tenants: a deterministic frame world
  (`tests/grid_adapter.py`) that exercises rendering, the frame grader, the
  grid refusal on registry runs, view and the offline namespace end to end,
  and the OOLONG spam4k pack driven to WIN through the bench adapter with its
  sealed score written at finalize.
- `budget_remaining` (ranked fix 10). The host channel was advertised but
  refused to read, so a claim on it was accepted by the reference check and
  then graded UNGRADABLE. It now reads as the registered cap minus the paid
  actions up to and including the event (so `ch budget_remaining delta = -1`
  holds for any paid action), and is UNGRADABLE only when no cap is
  registered, with the reason saying so.
- Daemon identification on Linux without a terminal. `find_daemon` reads
  `ps -ww -eo pid=,command=`: procps cuts the listing at 80 columns when no
  terminal is attached (under pytest, in CI), so the daemon's `--run-dir`
  fell off the line and `assay stop`, `assay doctor` and the resume rule
  did not recognize a live daemon; `-ww` lifts the limit on procps and BSD
  ps alike. Found by the first CI run on ubuntu (#30).
- The start of a run on a slow disk. `start_broker` rewrote `broker.json`
  with the child's pid right after spawning the daemon, and that write raced
  the daemon's own: when its fsync outlasted a warm daemon's startup, the
  rewrite buried the daemon's READY descriptor under STARTING, the daemon
  never wrote READY again, and `assay start` waited out its 45-second
  deadline against a live daemon before failing with "environment owner did
  not start". The rewrite is gone: once spawned, the daemon is the only
  writer of `broker.json` and publishes its own pid there; identity was
  already the process table, never a stored pid. Reproduced by stalling that
  one write for a second; found by the second CI run on ubuntu, in two of
  three cells on different tests (#30).
- Dangling references, stale rules and plan codes out of the shipped tree.
  `docs/ARCHITECTURE.md` no longer cites `DESIGN.md` (a file not in the
  repository), describes the OOLONG batch registry, and counts the 66
  published journals under `evidence/`, as does the `words.py` docstring;
  the integrity docstring names the checker's ungated rule instead of
  `evidence/bypass_audit.py`; the stale world id TODO in `core.py` and the
  planning-code comments in `predictions.py`, `live.py`, `modules.py` and
  the frame-world extra are plain sentences that say what the code does;
  the `aggregates.py` and `modules.py` docstrings say when and where the
  code runs; ONBOARDING quotes the current world id rule, GUIDE the grade
  text as printed, and the `--predict` help a general claim (#29).
- The nesting limit on a JSON document is the kernel's own, not the
  interpreter's. `ops.decode_json` refused a document nested too deep by
  catching the `RecursionError` of `json.loads`, whose threshold moves with
  the version (3.12 gives up past about 20,000 levels, 3.14.5 reads 100,000)
  and with the thread's stack: on Python 3.14 the daemon read a 100,000-deep
  request and refused it later as `args is not an object`, and the command
  line passed one on to the registry. The depth is measured after the parse
  with a stack of the kernel's own and refused past
  `ops.DOCUMENT_DEPTH_LIMIT` (64, the outermost container the first level; a
  value within a schema of `SCHEMA_DEPTH_LIMIT` never reaches it), in the
  same words on both surfaces, `malformed request: nested too deep` on the
  wire and `--params is nested too deep` on the command line. The suite's
  inline 100,000-deep `--params` argument, 200 KB, was over the 128 KiB
  Linux allows one argument, so every ubuntu cell failed with `Argument list
  too long` before the command line ran; the extreme depth goes through
  `--params @FILE` and the inline case is 200 deep. Found by the first CI
  run after the billing block, on every ubuntu cell and on macOS 3.14 (#30).
- The TRANSITION story of a frame world, in `assay view` and after a paid
  action, is a function of the records (#58). It visited its (color, shape)
  groups in set order, which is the order of the interpreter's per-process
  string hashing, so three `view` invocations on the last event of a
  published run printed three texts, and the replay diff never saw it
  because it pins the hash seed. Three things followed the seed: the order
  among components of equal size, the pairing of a vanished component with
  the appeared one it was reported as resized into, and which items the cap
  of six per kind showed. Every list of the story is now in one stated
  order, largest first, then top-left first (rows before columns), then
  color, the groups are visited sorted, and a resize pairs a vanished
  component with the largest appeared component of its color that overlaps
  it. The suite renders the same event under three hash seeds in three
  processes and holds the texts byte-identical, on a crafted frame pair
  with ties on every key but position and on the last event of the
  published ar25 journal. The replay diff drops the story's component lines
  on both sides, since on the campaign kernel they were never a function of
  the records (16 of the 25 views differed even at the pinned seed, 10 of
  them in content), and keeps the block's count line.

### Regression gates

Recorded per commit in `paper/v4/RELEASE-1.1.0-LOG.md` of the archive, with
the scripts under `paper/v4/release-gates/` there and the commands in
`RELEASE_CHECKLIST.md`. Final results on the tip of the release work, after
the owner's decisions were implemented (the second round, 2026-10-07):

- G1, the suite: 158 passed (the 74 of main, two of them with one keyword
  and one expected message changed, plus 84 new, two legacy tests removed),
  run with the shell's anchor directory unset to prove the isolation.
- G2, the replay diff: zero differences in `status`, `audit`, `view` and
  `channel list` over the 25 published ARC-AGI-3 run directories (read-only
  copies, six of them recorded under `.arc/`) between the main kernel and
  this one, under the enumerated mask (the additive ANCHORS, CHANNELS and
  MODULES lines, the coverage_audit advisory that fires on lf52 alone, the
  audit's pending-mutation line, and the one vocabulary change in the AGENDA
  goal text), with the hash seed pinned because the transition story's order
  was already process-dependent.
- G3, the independent checker: all 56 published journals CLEAN with their
  published heads matched (the 25 ARC-AGI-3, the three Factorio, the three
  OOLONG, the twelve E1, the ten E2, the two E3 and the one G4), both from
  the archived run directories and from the published copies under
  `evidence/` (`evidence/verify_all.py`, which the suite also runs).
- G4, the ft09 live regression on the release kernel at 3b7eb19: WIN 6 of 6
  in 80 paid actions, 80 of 80 predicted, under the bar of 113 (decision
  18), kernel audit CLEAN, checker CLEAN. Published as `evidence/g4`. The
  commits after 3b7eb19 change no journal field and no grading rule (the
  replay diff above is the proof), so the gate stands for the tip.
- G5, the clean machine: a fresh venv, `pip install -e '.[grid]'`, `assay
  doctor` clean, the README quickstart verbatim through the installed entry
  point, the OOLONG spam4k pack through the bench adapter with `assay stop`
  leaving no process, the launcher with `ASSAY_PYTHON` pinned. PASS.

## 1.0-rc1 (2026-08-22)

The release candidate the ARC-AGI-3 campaign ran on (commit c4207c1).
