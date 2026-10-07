# Changelog

All notable changes to ASSAY are recorded here. The form follows Keep a
Changelog. Dates are UTC.

## Unreleased

### Taken from the experiment branch (exp/2026-10)

The release branch is cut from main (0373b5f). The experiment branch is not
merged. Two things were taken from it, by cherry-pick and by file copy:

- The `gate` registry key (exp commit 1630e46): `gate: required|optional`,
  default `required`. Under `optional` an `assay act` without `--predict` and a
  bare `--step "ACTION"` are accepted, executed, and journaled with `predict`
  null, `predict_ok` null, `grade` empty and the marker `gate_optional: true`.
  The audit still reports them UNGATED and keeps the run invalid for scoring,
  and additionally counts them as `ungated_permitted`. This is the control-arm
  switch the E1 experiment ran on. It is not the default and not a scorable
  mode.
- The coverage-audit module file `bench/arcagi/modules/coverage_audit.py`
  (exp commits 76ae414 and a3233df, the second being the settled-frame
  comparison fix). It ships here as a loadable external module only. Its
  split into a world-neutral built-in plus a frame-world extra is a later
  entry in this changelog.
- `tests/test_gate_optional.py` from the gate commit, with its absolute path
  to the independent checker replaced by `ASSAY_VERIFY` or a sibling checkout
  of assay-verify, else a skip.
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
  UNGATED with the marker `gate_off: true`, status says `GATE | off`, and
  the audit keeps the run invalid for scoring and names the mode that
  permitted the bare acts (`ungated_permitted_by`). The default stays
  `required`. Neither relaxed mode is scorable; they exist for the owner's
  control-arm experiments. TODO(owner: O10): the INTEGRITY line that reads as
  an alarm to a control-arm agent on every status is kept as is (E1 ran with
  it) and disclosed, pending the decision on a neutral line.
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
  `start` itself and therefore held the token; the documents say so.
  TODO(owner: O5): whether the benchmark protocols are rewritten so the
  operator runs `start`, or this option plus an honest paragraph is enough.

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

### Changed

- The frame-world tier left the kernel (A8.2). `src/assay_grid/` holds
  perception, the rules tier, the grid claim forms and grader, rendering (the
  one pillow import), the frame halves of status, result, inspect, view and
  export, the grid namespace of `assay python`, the solve-plan executor, and
  the legacy numbered-action vocabulary (`ACTION1..7`, `ACTION6:x,y`, moved
  undocumented, TODO(owner: O2) on deleting it in 1.2). The kernel keeps the
  frame encoding the journal format has, and one hook, `assay.extras`,
  selects the extra by observation shape (`frames` present), never by
  configuration, so the published run directories need no change. A dict run
  never imports `assay_grid` or pillow. The parser declares the frame-only
  `rules` subcommand and the `view` flags itself, inert on a dict run. The
  `ACTION` prefix matching in the affordance check is gone: a frame world's
  advertised ids are rendered as names by the extra before the check. The
  refusal of a grid claim form on a registry run now names the form
  ("a frames-world form ... refused on registry runs") instead of claiming
  the run has no grid observation. Every historical field name, state value
  and claim form is untouched. Proven by the replay diff over the 25
  published run directories: zero differences in `status`, `audit`, `view`
  and `channel list` against the main kernel.
- `parse_claims(text, kind=None)` replaces `parse_claims(text, general=...)`.
  The core forms parse on every run; an observation kind's forms parse only
  when the kind is given (the legacy path), and are refused by name
  otherwise. Test-only API change.
- `live.py` exposes its seam under public names (`paid_step`,
  `record_event`, `write_receipt`, `head_events`, `level_advanced`,
  `validate_batch_tokens`) so an observation kind's executor builds on it
  without importing private functions.

### Known

- The TRANSITION story in `assay view` and after a paid action on a frame
  world lists its moved, resized, vanished and appeared components in an
  order that depends on Python's per-process string hashing (a set of
  component keys is iterated). Display only, never journaled, pre-existing.
  The replay diff pins `PYTHONHASHSEED` for that reason. A deterministic
  order is a one-word change in `assay_grid.perception.transition_story`,
  kept out of the move so the replay diff stays a pure proof.

### Fixed

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
  daemon is confirmed dead or absent (the ungated numbered-action path, whose
  daemon never writes events, is the one exception). The audit reports a spend
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
  states the rule (TODO(owner: O7) on relaxing it). An exception that is not
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

### Regression gates

Recorded per commit in `paper/v4/RELEASE-1.1.0-LOG.md` of the archive. The
suite on main collects 74 tests. After this entry the branch collects 81.

## 1.0-rc1 (2026-08-22)

The release candidate the ARC-AGI-3 campaign ran on (commit c4207c1).
