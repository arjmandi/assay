---
name: diagnose
description: Diagnose an ASSAY run against the five layers of reasoning (the constitution, the modules, the registry and the adapter, the addressable states, the model). Each finding comes with the evidence from the run's record and the place to change. Use when the user asks why a run failed, stalled, stopped early or went badly, what to fix or steer, or where an agent's reasoning broke.
argument-hint: "[run dir]"
---

# Diagnose an ASSAY run

`${CLAUDE_PLUGIN_ROOT}` below is the plugin's root directory: Claude Code
fills it in, and in any other host it is the directory two levels above this
SKILL.md.

You read the run and never change it. The layers are those of the README
("Reasoning layers"): 1 the constitution, 2 the modules, 3 the registry and
the adapter, 4 the addressable states, 5 the model. A finding is a place to
look, with its counts and event ids as the evidence, not a verdict.

- **The diagnose mode:** `python3
  ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py diagnose`, described in
  `${CLAUDE_PLUGIN_ROOT}/tools/eval/README.md` under "The watch and diagnose
  modes".
- **Where runs are,** unless the user put them elsewhere: `~/assay/runs/<run
  name>/`.

The arguments: $ARGUMENTS

## 1. Find the run

Take the run directory from the arguments. Otherwise take the newest
directory in `~/assay/runs/` that holds `.assay/config.json`, skipping the
ones ending in `.sessions`. When two are plausible, ask which.

## 2. Diagnose

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py diagnose --run-dir <run dir>
```

The output is one `LAYER` block per layer with findings, each with its
evidence and a `steer:` line. `--json` gives the same as one object.

## 3. Explain

Tell the user, from the most general layer to the most specific:

- what each finding says, in plain words, with one example from the
  evidence (open the event with `${CLAUDE_PLUGIN_ROOT}/bin/assay --run-dir
  <run dir> view --event N` when an example helps);
- what to change, and where: the constitution or the player's prompt
  (layer 1), the registry's `module_modes` (layer 2), the registry, the
  adapter or the pack's note to the player (layer 3), the states the player
  declares and predicts (layer 4), or the model and the budget (layer 5).

Offer to make a change only where it is the operator's: a registry, a
recipe, a prompt. Never edit the run's journal or notes. A change takes
effect in a new run, or in the next session for a prompt.
