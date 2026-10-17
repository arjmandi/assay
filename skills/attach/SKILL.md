---
name: attach
description: Start an ASSAY run on a pack with a goal. A pack is a world ASSAY can referee: one of the catalog's (ARC-AGI-3, a flaky test, a bank reconciliation, a legacy API, a made-up language) or one /assay:connect or anyone else built. Sets up the pack's interpreter and credentials, starts the run with the owner token kept out of the agent's reach, installs the hooks and writes the player's prompt. Use when the user wants to apply, attach or start ASSAY on a pack, a world or a task, or to play ARC-AGI-3 or another game with ASSAY. When the world has no pack yet, it builds one with /assay:connect first.
argument-hint: "<pack name or directory> \"<goal>\""
---

# Attach ASSAY to a pack, with a goal

`${CLAUDE_PLUGIN_ROOT}` below is the plugin's root directory: Claude Code
fills it in, and in any other host it is the directory two levels above this
SKILL.md.

You are the operator's side of an ASSAY run. You take a pack and the user's
goal, prepare what the pack needs, and start the run. You never play it. The
player is a separate session that starts after you, so it learns the world
instead of remembering how it was built, and it never holds the owner token.

- **The launcher:** `${CLAUDE_PLUGIN_ROOT}/bin/assay`. Use this absolute path
  in every command, since your shell keeps no variables between commands.
- **The catalog:** `${CLAUDE_PLUGIN_ROOT}/worlds/`. Its `README.md` lists
  the packs and says what a pack holds ("The pack manifest").
- **The start procedure and the player's prompt:**
  `${CLAUDE_PLUGIN_ROOT}/skills/attach/start.md`. Where it says `LAUNCHER`,
  write the launcher's path; where it says `<plugin root>`, write
  `${CLAUDE_PLUGIN_ROOT}`.
- **Where things go,** unless the user says otherwise:
  - runs: `~/assay/runs/<world id>-<YYYYMMDD-HHMMSS>/`;
  - a pack's interpreter: `~/assay/envs/<pack name>/`;
  - owner tokens, key files and hook policies: `~/assay/tokens/`.

  Tell the user where you put them.

The arguments: $ARGUMENTS

## 1. Find the pack and the goal

The first argument is the pack: a catalog name (a directory of
`${CLAUDE_PLUGIN_ROOT}/worlds/recipes/`, such as `arc-agi-3`) or a directory
holding `adapter.py` and `registry.json`. The rest is the goal, in the
user's words.

When the arguments name no pack but describe a task:

- A catalog pack serves it only when the user asked for that pack's own
  world or for a demo of it. The catalog's packs play worlds built from a
  seed, not the user's files, repository or service.
- Otherwise the world needs its own pack. Say so in one sentence, follow
  `${CLAUDE_PLUGIN_ROOT}/skills/connect/SKILL.md` to build and smoke-test
  it, and continue here with the pack it wrote.

When there is no goal, ask for it in one sentence and stop until it comes.

## 2. Read the pack

Read its `pack.json` and `README.md`. A pack without `pack.json` takes the
defaults: `adapter.py:factory`, `registry.json`, the world id is the pack's
name, no interpreter, no credentials, no deny patterns.

- **The world id:** what `pack.json` `world_id` says it is. Take it from the
  goal when the goal names one of its `values` (a game id for ARC-AGI-3),
  else its `default`. Ask when the goal names a value that is not listed.
- **The goal:** the registry's `goal.text` is what the player is told. When
  the user's goal says more or other than it, write the run's registry: a
  copy of the pack's with `goal.text` set to the user's goal, at
  `~/assay/runs/<run name>.registry.json`, and start with that one. The
  world's WIN stays the pack's own (`pack.json` `win`): when the goal asks
  for something the world cannot judge, say so before starting.
- **The card:** print the world card from `pack.json` `card` and the README.

## 3. The pack's interpreter

When `pack.json` names `python`, the world runs under its own interpreter:

1. `uv` must be on PATH. If it is not, give the user its installer
   (https://docs.astral.sh/uv/) and stop until it is.
2. Unless `~/assay/envs/<pack name>/bin/python` exists: `uv venv --python
   <version> ~/assay/envs/<pack name>`, then `uv pip install --python
   ~/assay/envs/<pack name>/bin/python -r <pack>/<requirements>`.
3. Write `ASSAY_PYTHON=~/assay/envs/<pack name>/bin/python` (the path in
   full) before every launcher command of this run, and run `ASSAY_PYTHON=...
   LAUNCHER doctor`. It must report no failure.

The run records the interpreter, and `/assay:run` uses it from any shell.

## 4. Credentials

For each entry of `pack.json` `credentials`:

- **With a `file`:** check that it exists and is not empty with `test -s
  <file>`. Never read it, print it or ask for its contents. When it is
  missing, give the user the line from the pack's README to paste into their
  own terminal, not into this session, and stop until they say it is done.
  The pack's adapter reads the file inside the daemon.
- **Without a file** (a service's token): the user exports it in the shell
  that starts the run, never in the player's; `start.md` says how.

## 5. Smoke-test, then start

Follow `start.md`:

- the smoke test, the first time this pack runs on this machine, unless
  `/assay:connect` just ran it;
- the start, with the world id, the run's registry and the owner token in
  `~/assay/tokens/`;
- the hooks, with the pack's `deny` patterns;
- `PLAYER.md` in the run directory, with the goal and the pack's
  `player.note`.

## 6. Report

Tell the user:

- the world card and the goal the player is told;
- where the run and the token file are;
- that `/assay:run` starts the player, and `/assay:watch` follows it;
- how to approve, resume, audit and score.
