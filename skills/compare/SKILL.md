---
name: compare
description: Compare ASSAY with Claude Code running /goal on one pack of the catalog. The same model, the same dollars and hours per run, and the same external checker score both arms, and the result is how often each arm claimed the goal while the checker disagreed. The design is pre-registered before the first run. Use when the user wants to compare ASSAY against Claude Code or /goal, run a baseline, measure false completions, or produce evidence that ASSAY changes the result.
argument-hint: "[pack] [--seeds N ...] [--model M] [--max-usd USD]"
---

# Compare ASSAY with Claude Code and /goal

You are the operator of the comparison. The compare mode launches both
players; you never play either arm, and you never change a result.

- **The compare mode:** `python3
  ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py compare`, described in
  `${CLAUDE_PLUGIN_ROOT}/tools/eval/README.md` under "The compare mode".
- **The packs that carry a design:** the directories under
  `${CLAUDE_PLUGIN_ROOT}/worlds/recipes/` that hold `compare.json`.
- **The state:** `~/assay/compare/<pack>-<YYYYMMDD-HHMMSS>/` unless the
  user names another.

The arguments: $ARGUMENTS

## 1. Choose the pack and the limits

1. The pack from the arguments, else ask which of the packs with a
   `compare.json`. Read its `compare.json` and its README's world card.
2. The model, the seeds and the dollars per run come from the design. Tell
   the user the most it can cost, the seeds times two arms times the
   dollars per run, and confirm the model, the seeds and the dollars with
   the AskUserQuestion tool in one call, unless the user already gave them.
   This step spends real money and needs the user's yes.
3. Say what the comparison is and is not. Both arms get the same model, the
   same dollars and hours per run, no MCP server, the same tool permissions
   and the same checker. The goal arm reaches the world through `world.py`
   with no prediction and no gate. A few seeds read direction only, as the
   design says.

## 2. Dry run, then launch

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py compare --pack <pack dir> \
    --state-dir <state> [--seeds ...] [--model M] [--max-usd USD] --dry-run
```

It prints the plan, both arms' commands and the goal arm's prompt, and
writes nothing. Then the same command without `--dry-run`, in the
background (the Bash tool's `run_in_background`). Each run ends with a `ROW |`
line, and the end with `ARM |` lines.

## 3. Report

Read `<state>/COMPARE.md`. Tell the user, briefly:
- per arm: the runs, how many claimed the goal, how many the checker
  scored correct, and the false completions;
- the cost;
- for the hidden-grammar pack, the rules each run never tested;
- that the reading is the one registered: direction only at this n.

Point at `<state>/reports/` (the report of every ASSAY run) and
`<state>/logs/` (every session's output). Offer `/assay:report` on one of
the ASSAY runs.

Never rerun a seed to change its row, and never edit `results.jsonl`. A
different design goes in a new state directory: the compare mode refuses a
changed design in a state that holds a pre-registration.
