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

- `assay stop`: stops the run's daemon cleanly (SIGTERM, wait, report). Works
  without run state, so an orphaned daemon left behind by a hand-deleted
  `.assay` can be stopped. Never sends SIGKILL.

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

### Regression gates

Recorded per commit in `paper/v4/RELEASE-1.1.0-LOG.md` of the archive. The
suite on main collects 74 tests. After this entry the branch collects 81.

## 1.0-rc1 (2026-08-22)

The release candidate the ARC-AGI-3 campaign ran on (commit c4207c1).
