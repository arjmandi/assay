---
name: connect
description: Connect ASSAY to a new world by building an integration pack for it, the world's adapter, registry and scripts, and smoke-testing it. For files, a simulator or a game on this machine, a code repository with tests, or a service with an MCP server or an HTTP API. Use when the user wants to connect, integrate or bring their own world, system, game, data, tool or service to ASSAY, or asks for a pack. /assay:attach then starts a run on the pack with a goal.
argument-hint: "[the world, and the kind of goal you will want there]"
---

# Connect ASSAY to a world

`${CLAUDE_PLUGIN_ROOT}` below is the plugin's root directory: Claude Code
fills it in, and in any other host it is the directory two levels above this
SKILL.md.

You build a pack: the one thing ASSAY needs to referee a world. A pack is a
directory with the adapter, the registry, the world's scripts and a
`pack.json` manifest (`worlds/README.md`, "The pack manifest"). You do not
start a run and you never play one: `/assay:attach` starts it with the
user's goal, and the player learns the world from scratch instead of
remembering how you built it.

- **The launcher:** `${CLAUDE_PLUGIN_ROOT}/bin/assay`. Use this absolute path
  in every command, since your shell keeps no variables between commands.
- **The catalog and the archetypes:** `${CLAUDE_PLUGIN_ROOT}/worlds/`; read
  its `README.md` first.
- **The rubric:** `${CLAUDE_PLUGIN_ROOT}/skills/attach/rubric.md`.
- **The smoke test:** `${CLAUDE_PLUGIN_ROOT}/skills/attach/start.md`, "The
  smoke test". Where it says `LAUNCHER`, write the launcher's path.
- **Where packs go,** unless the user says otherwise:
  `~/assay/worlds/<id>/`. Tell the user where you put it.

The world: $ARGUMENTS

If no world was given, ask for it in one sentence and stop until it comes.
A pack needs to know what counts as done there, so when the user named no
kind of goal, ask for one with the world (for example "the reports match the
ledger" or "the game reports WIN").

The catalog's packs play their own worlds, built from a seed: they are
demos and samples. A world of the user's own (their files, repository,
service or game) gets a pack of its own, even when a catalog pack looks
similar.

## 1. Classify

Read the rubric, then:

1. **Step 0.** Is it a world, or a program over worlds? For a program, say
   which parts of ASSAY it would use, and stop.
2. **The fit check,** silently. If it fails, say why in two sentences and
   offer a proxy if there is one. Otherwise recommend Claude Code with
   `/goal`, and stop unless the user still wants a run.
3. **Infer the four answers and the tags** from the task. Confirm the answers
   with the AskUserQuestion tool, in one call:
   - the four questions (three when Q1 is M, which skips Q4);
   - three options each, with your inferred option first and labelled
     (Recommended);
   - short option labels, with the consequence in the description.

   Where you cannot ask (a host without the tool, or a session that is not
   interactive), print the questions with your answers, say that you took
   them, and go on.
4. **Print the world card** in the rubric's format, with the concrete target,
   states, rules and flags for this task.

## 2. Choose the archetype

- **Q1 is H, or the clock is h or d:** this version cannot run the world.
  Offer a proxy on this machine (a simulator, an export, an archive replay)
  and classify that instead, or stop.
- **Q1 is S:** use the `service` archetype when the system has an MCP server
  (over stdio) or an HTTP API.
  - Propose a proxy first: a sandbox, a test account, a dev store, a staging
    instance, or a local copy of the service. The live system comes later,
    with `approval` on its irreversible actions.
  - A website with no API goes through a browser's MCP server, the least
    proven path; say so.
  - An instrument with an MCP server or an API is a service too.
  - A system with neither is not in this version: offer an export of its
    data as an M world, or stop.
- **Q1 is M:**
  - Use the `repo` archetype for a repository whose tests decide
    the goal (the paid action is RUN_TESTS, and editing is free).
  - Otherwise use the `local` archetype for files, a simulator or a game
    driven by commands.

## 3. Write the pack

1. Copy the archetype's template,
   `${CLAUDE_PLUGIN_ROOT}/worlds/archetypes/<local|repo|service>/template/`,
   to `~/assay/worlds/<id>/`, and read that archetype's `README.md`.
   - For `local`: rewrite `world.py`'s setup, observe and act for the task.
     Keep hidden state out of the working copy, and keep the scripts
     deterministic.
   - For `repo`: drop `example_project/`, point `world.source` at the user's
     repository, set the test command and the WIN rule's selector.
   - For `service`: drop `notes_server.py`. Draft the calls and the registry
     from the service itself, with
     `python3 ${CLAUDE_PLUGIN_ROOT}/worlds/archetypes/service/make_registry.py
     mcp -- <server command>` or `... openapi <spec.json> --base-url <url>`.
     Merge its draft into `recipe.json` and `registry.json`, and read its
     notes on what it dropped. Choose a dashboard: the few reads whose before
     and after the agent should predict, such as a count or a status. A
     credential goes in a header or the server's `env` as `${env:NAME}`; add
     NAME to the registry's `secrets`, and have the user export it in the
     shell that starts the run, never in the player's.
2. Edit `recipe.json`:
   - `name`;
   - `card`: the code, the family, the fit, the clock, the proxy, the scarce
     commit and the hypothesis;
   - the commands and `actions`;
   - `win`, plus `lose` and `levels` where they apply;
   - `player.note`: where the agent reads the world.

   `worlds/README.md` is the recipe's reference.
3. Edit `registry.json`:
   - the actions, with typed parameters;
   - a budget that leaves room to probe;
   - the goal, in one or two sentences the player can act on.

   Give each action a description that says what it is for, never the hidden
   rules. Set flags from Q4:
   - for U, `destructive: true` on deletes and overwrites;
   - for X, `approval: true` on each irreversible action.

   A scarce commit gets its cap in the act script, which refuses past it.
4. Keep `adapter.py` exactly as copied.
5. Write `pack.json` beside them, as `worlds/README.md` ("The pack
   manifest") says: `name`, `summary`, `world_id`, `card`, `player.note`,
   `win`, the `deny` patterns for every way around the harness (the world's
   scripts and directory, the test runner, the service's host and clients),
   `credentials` for a service's, and `python` only when the world's scripts
   need packages the plugin's interpreter lacks.
6. A `README.md` of a few lines: what the world is, and what it cannot judge.

## 4. Smoke-test

Run "The smoke test" of `start.md` on the pack, in a scratch directory, with
`ASSAY_PYTHON` set when `pack.json` names an interpreter (build it as
`/assay:attach` does, `skills/attach/SKILL.md` step 3). Fix the pack until it
is clean.

## 5. Hand it over

Tell the user:

- the world card;
- where the pack is, and what it cannot judge;
- the next command, with the goal in their words:
  `/assay:attach ~/assay/worlds/<id> "<goal>"`.
