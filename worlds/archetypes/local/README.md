# The local archetype

A world on this machine, run by the command-world engine in the copy mode:
files, a simulator or a game, rebuilt in `.assay/world/` from the recipe at
every start and replayed by the kernel on resume.

Use it when the answer to Q1 is M and the world can be driven by commands: a
CSV or a spreadsheet exported to CSV, a SQLite file, a simulator with a
command line, a game engine you can step. The template, `template/`, is a
switchboard: five switches, four lamps, a hidden wiring and one jammed switch.
It runs as it is.

## Write a world from the template

1. Copy `template/` to a directory of your own and rename the world in
   `recipe.json` (`name`) and in `registry.json` (`goal`, `mode_note`).
2. Replace `world.py`'s three subcommands. setup builds the world in the
   working directory, observe prints its state as one JSON object, act
   applies one action and prints the result object. Keep the parameters'
   shapes in step with `registry.json`.
3. Put what the agent may read in the working directory and nothing hidden:
   derive hidden state from `--seed`, as `wiring()` does.
4. Write the rules in `recipe.json`: `win`, optionally `lose` and `levels`.
5. Keep `adapter.py` as it is. It finds the engine from the catalog, or from
   the ASSAY checkout or plugin the daemon runs from.
6. Try it: `assay doctor` in a scratch directory, then a start, one act with
   a prediction, and a stop.

The scripts may be in any language: the commands are argv lists. Python
scripts get the daemon's interpreter as `{python}`.
