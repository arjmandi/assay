---
name: watch
description: Watch an ASSAY run while it plays. One line whenever something changes, showing the session runner's state, the paid actions of the cap, the outcomes held and missed, the last action and how it graded, the spend and the Stop gate's blocks. Use when the user wants to watch, follow, monitor, check on or see the progress of an ASSAY run, or asks how a run is going.
argument-hint: "[run dir] [--every S]"
---

# Watch an ASSAY run

`${CLAUDE_PLUGIN_ROOT}` below is the plugin's root directory: Claude Code
fills it in, and in any other host it is the directory two levels above this
SKILL.md.

You only read the run. The watch mode reads its files and writes nothing.

- **The watch mode:** `python3 ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py
  watch`, described in `${CLAUDE_PLUGIN_ROOT}/tools/eval/README.md` under
  "The watch and diagnose modes".
- **Where runs are,** unless the user put them elsewhere: `~/assay/runs/<run
  name>/`, with the session runner's state in `<run name>.sessions/`.

The arguments: $ARGUMENTS

## 1. Find the run

Take the run directory from the arguments. Otherwise take the newest
directory in `~/assay/runs/` that holds `.assay/config.json`, skipping the
ones ending in `.sessions`. When two are plausible, ask which.

## 2. One look

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py watch --run-dir <run dir> --once
```

Tell the user the line in words: where the runner is, the paid actions of
the cap, held and missed, the last action and whether it held.

## 3. Follow it, when the user asks

Start the mode without `--once`, with `--until-end`, in the background. In
Claude Code, use the Monitor tool so that every new line reaches you:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py watch --run-dir <run dir> --every 30 --until-end
```

It prints only when the line changes, and it ends when the session runner
ends or the run reaches WIN or a cap. Pass on what changes the picture: a
run of misses, a stop the gate blocked, the runner ending. A steady stream
of held outcomes needs no comment. When it ends, offer `/assay:report`, and
`/assay:diagnose` when the run did not go well.
