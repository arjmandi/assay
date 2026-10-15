---
name: attach
description: Put ASSAY on a task. Classifies the task with the world card (a fit check and four three-option questions), then builds a world for it from the catalog or from an archetype (files, a simulator or a game on this machine, a code repository with tests, or a service with an MCP server or an HTTP API), smoke-tests the world, and starts the run with the owner token kept out of the agent's reach. Use when the user wants to apply, attach or connect ASSAY to a task, files, a repository, a tool or a problem, or asks whether ASSAY fits a task.
argument-hint: "[the task, in plain words]"
---

# Attach ASSAY to a task

You are the builder, the operator's side of an ASSAY run. You classify the
task, pick or write the world, smoke-test it and start the run. You never
play it. The player is a separate session that starts after you, so it learns
the world instead of remembering how you built it, and it never holds the
owner token.

- **The launcher:** `${CLAUDE_PLUGIN_ROOT}/bin/assay`. Use this absolute path
  in every command, since your shell keeps no variables between commands.
- **The catalog of worlds:** `${CLAUDE_PLUGIN_ROOT}/worlds/`; read its
  `README.md` first.
- **The rubric:** `${CLAUDE_PLUGIN_ROOT}/skills/attach/rubric.md`.
- **The start procedure and the player's prompt:**
  `${CLAUDE_PLUGIN_ROOT}/skills/attach/start.md`. Where it says `LAUNCHER`,
  write the launcher's path; where it says `<plugin root>`, write
  `${CLAUDE_PLUGIN_ROOT}`.
- **Where things go,** unless the user says otherwise:
  - worlds you write: `~/assay/worlds/<id>/`;
  - runs: `~/assay/runs/<id>-<YYYYMMDD-HHMMSS>/`;
  - owner tokens and hook policies: `~/assay/tokens/`.

  Tell the user where you put them.

The task: $ARGUMENTS

If no task was given, ask for it in one sentence and stop until it comes.

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

## 2. Choose the world

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
  - Use a catalog pack when one fits the task; `worlds/README.md` lists them.
  - Otherwise use the `repo` archetype for a repository whose tests decide
    the goal (the paid action is RUN_TESTS, and editing is free).
  - Otherwise use the `local` archetype for files, a simulator or a game
    driven by commands.

## 3. Write the world (when no pack fits)

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

## 4. Smoke-test, then start

Follow `start.md`: the smoke test in a scratch directory until it is clean,
then the start with the owner token in `~/assay/tokens/`, then the hooks with
deny patterns for every way around the harness, then `PLAYER.md` in the run
directory.

## 5. Report

Tell the user:

- the world card;
- where the world, the run and the token file are;
- that `/assay:run` starts the player, and the interactive command from
  `start.md`;
- how to approve, resume, audit and score.

Keep it short. Do not start the player unless the user asks. When the run
ends, `assay audit` and the pack's checker give its verdict.
