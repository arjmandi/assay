---
name: report
description: Write the report of an ASSAY run as a document beside it. The report gives how the run ended, the kernel's audit and the chain head, the predictions that held with their event ids, the misses with what the world reported instead, the player's Verified lines checked against the journal, what the run never exercised, and what the sessions cost. Use when the user wants a report, a write-up, the results or the evidence of an ASSAY run, wants to know what a run verified, or must decide whether to accept a player's declared stop.
argument-hint: "[run dir] [--check CMD]"
---

# Report on an ASSAY run

`${CLAUDE_PLUGIN_ROOT}` below is the plugin's root directory: Claude Code
fills it in, and in any other host it is the directory two levels above this
SKILL.md.

You read the run and never change it. Its facts about the world come from
the journal, so anyone holding the run directory gets the same facts, and
its verdict on the record is the kernel's own audit, which the report runs
through the launcher.

- **The launcher:** `${CLAUDE_PLUGIN_ROOT}/bin/assay`. Use this absolute path
  in every command.
- **The report:** `python3
  ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py report`, described in
  `${CLAUDE_PLUGIN_ROOT}/tools/eval/README.md` under "The report".
- **Where things are,** unless the user put them elsewhere:
  - runs: `~/assay/runs/<run name>/`;
  - each run's sessions: `~/assay/runs/<run name>.sessions/`;
  - the report: `~/assay/runs/<run name>.report.md`, with the JSON record
    beside it as `<run name>.report.json`.

The arguments: $ARGUMENTS

## 1. Find the run

1. Take the run directory from the arguments. Otherwise take the newest
   directory in `~/assay/runs/` that holds `.assay/config.json`, skipping
   the ones ending in `.sessions`. When two are plausible, ask which.
2. If `<run dir>.sessions/status.json` says `"state": "running"` and its
   `pid` is alive (`kill -0 <pid>`), a runner is still playing the run. The
   report is then a snapshot; say so.

## 2. Run it from the run's shell

The report runs `assay audit` itself, which checks the chain, the ungated
rule and the heads anchored outside the run directory. Run the report from
the shell that started the run, so that ASSAY_ANCHOR_DIR is the same. A
problem line `anchor_env_mismatch` means it is not.

## 3. Add the external check, when the world has one

When the `adapter` in `<run dir>/.assay/config.json` names a pack's
`adapter.py`, and that pack holds `checks/score.py`, pass the checker:

```bash
--check "python3 <pack>/checks/score.py --run {run_dir} --json"
```

The report runs it and records its verdict. Exit status 0 is a pass. A
world without a checker has only the run's own verdict, and the report says
nothing about one.

## 4. Write the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py report --run-dir <run dir> \
    --assay ${CLAUDE_PLUGIN_ROOT}/bin/assay [--check "..."]
```

The first line names the document and the JSON record. The last line,
`REPORT | <outcome> | ...`, is the short form. An `ERROR` line names what
to fix: no journal, an `--out` under the run's `.assay` or `.claude`, or a
check that is not on PATH.

## 5. Tell the user

Read the document, then say in a few lines:

- how the run ended, and the integrity verdict;
- how many outcomes held and missed, and the one or two misses that taught
  the most;
- what was never exercised;
- the cost;
- the path of the document.

Offer the document rather than pasting it whole.

- **INVALID FOR SCORING, or an audit problem:** say so first. The rest of
  the report describes a record that cannot be trusted.
- **SELF_STOP, or a journaled conclusion of impossibility:** set the
  declaration beside "Never exercised". Name the untested rule it rests
  on, say which probes would test it, and offer to continue the run with
  `/assay:run` to buy them. The decision is the user's.
- **A Verified line that cites no event, or an event the journal does not
  hold:** say that the player's notes claim more than the record shows. A
  line that cites a miss is not wrong for that. The miss can be the very
  evidence of its rule, the world correcting a prediction the player made
  before it knew the rule.

Never edit the report to soften it, and never edit the run's files.
