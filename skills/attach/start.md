# Smoke-test and start a run

`LAUNCHER` below stands for the launcher's absolute path, which SKILL.md
gives. Write it out in full in every command: your shell keeps no variables
between commands. Pass `--run-dir` on every command rather than relying on
the current directory. `<world dir>` is the pack; `<id>` is the world id
(`pack.json` `world_id`, else the pack's name); `<registry>` is the run's
registry when the goal changed it, else the pack's. When the pack names an
interpreter, every launcher command starts with `ASSAY_PYTHON=<interpreter>`,
written in full.

## The smoke test

Every new world gets one, and so does a pack the first time on a machine.

1. A scratch directory: `mktemp -d`, then `mkdir <scratch>/run`.
2. Start: `LAUNCHER --run-dir <scratch>/run start smoke --adapter <world
   dir>/adapter.py:factory --registry <registry>
   --owner-token-file <scratch>/smoke.token`, plus `--seed 1` for a world
   built from a seed. It must print `STARTED`. An `ERROR` line names what to
   fix: a recipe field (the engine's RecipeError names it), the registry, or
   a world script (its own error, quoted).
3. `LAUNCHER --run-dir <scratch>/run doctor` must report no problem with the
   adapter or the registry.
4. Declare one state the recipe's rules read, `LAUNCHER --run-dir
   <scratch>/run state declare <name> --path <name>`. Then take one paid
   action with a prediction you expect to hold, `LAUNCHER --run-dir
   <scratch>/run act <ACTION> <params> --predict "change"`. The receipt must
   carry a `RESULT` line and grade the outcome.
5. `LAUNCHER --run-dir <scratch>/run audit` must say `CLEAN`. Then `LAUNCHER
   --run-dir <scratch>/run stop`, and delete the scratch directory.

## Start the run

1. Names: the run directory is `~/assay/runs/<id>-<YYYYMMDD-HHMMSS>`, and the
   owner token and hook policy go in `~/assay/tokens/`. Create both
   directories: `mkdir -p <run dir>` and `mkdir -p -m 700 ~/assay/tokens`.
2. Start: `LAUNCHER --run-dir <run dir> start <id> --adapter <world
   dir>/adapter.py:factory --registry <registry>
   --owner-token-file ~/assay/tokens/<run name>.token`, plus `--seed N` for a
   world built from a seed. The token goes to the file and is never printed.
   Do not read it, and do not show it to the user unless asked.
3. The hooks, for a Claude Code player: `ASSAY=LAUNCHER LAUNCHER --run-dir
   <run dir> hooks install --policy ~/assay/tokens/<run name>.hooks.json
   --owner-token-file ~/assay/tokens/<run name>.token --deny '<pattern>'`,
   one `--deny` for each way around the harness: every pattern of the
   pack's `pack.json` `deny`, and
   - for a pack, its world script's name (such as `reconcile[.]py`) and the
     catalog (`worlds/recipes`);
   - for a world you wrote, its script and its directory;
   - for the repo archetype, the test runner (such as `pytest`);
   - for a service, its host and its clients, so that only `assay act`
     reaches it: the API's host name (such as `api[.]example[.]com`),
     `curl` and `wget` to it, and the MCP server's command.

   A service's credential is in the daemon's environment, exported in the
   shell that runs the start. Do not export it in the shell that starts the
   player.

   Then `ASSAY=LAUNCHER LAUNCHER --run-dir <run dir> hooks install --check`.
   Setting `ASSAY` on the install pins it in the policy, so the player's
   `"$ASSAY"` is trusted.
4. Write the player's prompt to `<run dir>/PLAYER.md` from the template below,
   with every `<...>` filled in.

## The player

Do not start the player yourself unless the user asks. The way to run it is
`/assay:run` (`skills/run/SKILL.md`). Its session runner launches the player
in the background, one session after another, and keeps the world's
credentials out of the player's environment. It installs a Stop gate, so
that the player stops only at WIN, at the budget, at an owner action, or
after a `STOP:` line in its notes. It pauses when the owner must act.

By hand, interactively, with no gate and no relaunch:

```bash
cd <run dir> && ASSAY=LAUNCHER claude "$(cat PLAYER.md)"
```

A session that ends before the goal can be started again with the same
command: the player resumes from `assay status` and its notes. If the daemon
is gone, run the start command of step 2 again first. It replays the journal
for a copy-mode world. A worktree world cannot be rebuilt, so its run ends
with its daemon.

The owner's side, from the user's shell: `LAUNCHER --run-dir <run dir> approve
<ACTION> --token-file ~/assay/tokens/<run name>.token`, and `waive`, `goal
ratify` and `module install` the same way (GUIDE section 6). A pack with a
checker scores the final state: `python3 <world dir>/checks/score.py --run
<run dir>`.

## The player's prompt (PLAYER.md)

```
You are playing a world through ASSAY, a harness that grades a prediction before every paid action.

1. Read this file completely before anything else: <plugin root>/CONSTITUTION.md
2. The launcher is exported as ASSAY. You are in your run directory, and the operator has
   started the run. Begin with:
   "$ASSAY" status
   If .assay/NOTES.md exists, read it in full: it is your record from an earlier session of this run.

The task: <the registry's goal text>
<the pack's player.note (pack.json, else the recipe's), unless the goal text already says it>

Rules:
- Touch the world only through "$ASSAY" (act, commit, reset). Read the world's files only where
  the observation says they are.
- Read nothing outside this run directory except the constitution named above.
- Never edit anything inside .assay/ except .assay/NOTES.md. Never run start or stop: the run is
  the operator's.
- The registry caps paid actions at <budget>. Stop at WIN, at the cap, or when an action
  needs the owner's approval. If you judge the goal unreachable with what is left, or you
  need something only the owner can give, first add one line to .assay/NOTES.md that
  begins with STOP: and says why. A stop for any other reason is refused or restarted.
- Do the work yourself; do not start other agents.

When you stop, report: the progress, the paid actions spent, the predictions held and missed
with the world-model and gamble split and the specificity from "$ASSAY" status, the states you
declared, and the output of "$ASSAY" audit.
```
