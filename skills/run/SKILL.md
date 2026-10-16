---
name: run
description: Play an ASSAY run that /assay:attach started. Launches the player (Claude Code, Codex or OpenCode) in the background, one session after another, relaunching it when a session ends before the run is over, with the world's credentials kept out of its environment and a Stop gate that lets it stop only at WIN, at the budget, at an owner action or with a declared reason. When the owner must approve or waive something it pauses and asks the user. Use when the user wants to run, play, start the player on, continue, resume, or leave going overnight an ASSAY run.
argument-hint: "[run dir] [--player claude|codex|opencode] [--model M] [--hours H] [--max-usd USD]"
---

# Run the player on an ASSAY run

`${CLAUDE_PLUGIN_ROOT}` below is the plugin's root directory: Claude Code
fills it in, and in any other host it is the directory two levels above this
SKILL.md.

You are the operator's side of the run. The session runner launches the
player as a separate process. You never play the run yourself, and you never
give the player the owner token.

- **The launcher:** `${CLAUDE_PLUGIN_ROOT}/bin/assay`. Use this absolute path
  in every command.
- **The session runner:** `python3
  ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py session`, described in
  `${CLAUDE_PLUGIN_ROOT}/tools/eval/README.md` under "The session mode".
- **Where things are,** unless the user put them elsewhere:
  - runs: `~/assay/runs/<run name>/`;
  - each run's sessions: `~/assay/runs/<run name>.sessions/`;
  - owner tokens: `~/assay/tokens/<run name>.token`.

The arguments: $ARGUMENTS

## 1. Find the run

1. Take the run directory from the arguments. Otherwise take the newest
   directory in `~/assay/runs/` that holds `.assay/config.json`, skipping
   the ones ending in `.sessions`. When two are plausible, ask which.
2. It must hold `PLAYER.md`, the player's prompt. If it does not, write it
   from the template in `${CLAUDE_PLUGIN_ROOT}/skills/attach/start.md`.
3. If `<run dir>.sessions/status.json` says `"state": "running"` and its
   `pid` is alive (`kill -0 <pid>`), a runner is already playing the run.
   Report its progress instead of starting another.

## 2. Choose the player and the limits

- **The host:** `claude` unless the user names `codex` or `opencode`. Say
  that only the Claude Code player gets the Stop gate, that a Codex
  session's cost is not reported, and that the OpenCode template is
  untested in this version.
- **The model:** the one the user names, else the host's default.
- **The limits of this invocation:** `--hours` (default 8),
  `--max-sessions` (default 5), and `--max-usd` for the host's bill. For a
  run left overnight, propose a dollar cap. Confirm the host, the model and
  the limits with the AskUserQuestion tool in one call, unless the user
  already gave them. Where you cannot ask, take the defaults and say so.

## 3. Launch

1. A dry run, which writes nothing and shows the commands:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py session --run-dir <run dir> \
       --player <host> [--model M] [--hours H] [--max-sessions N] [--max-usd USD] --dry-run
   ```

   An `ERROR` line names what to fix: no started run, no prompt, or a host
   that does not run.
2. The same command without `--dry-run`, in the background (the Bash
   tool's `run_in_background`), so that you hear when it ends. Run it from
   the shell that started the run, where the world's credentials are
   exported: the runner keeps them from the player and needs them only to
   restart the daemon.
3. Tell the user:
   - the logs: `<run dir>.sessions/` (`sessions.json` is the ledger,
     `s<n>.player.out` a session's output);
   - how to watch: `${CLAUDE_PLUGIN_ROOT}/bin/assay --run-dir <run dir>
     status`;
   - how to stop: create `<run dir>.sessions/STOP` to stop after the
     current session, or stop the background task to stop at once.

## 4. When it ends

The last line of the output is `OUTCOME | <outcome> | ...`.
`<run dir>.sessions/status.json` holds the same, with the details.

- **WIN, CAP or USD_CAP:** the run is over and its daemon is stopped. Run
  `${CLAUDE_PLUGIN_ROOT}/bin/assay --run-dir <run dir> audit`, and a pack's
  checker when there is one (`python3 <pack>/checks/score.py --run <run
  dir>`).
- **OWNER_NEEDED:** the `OWNER |` lines name the refused action and the
  owner's command.
  1. Find out why the player wanted the action: the last session's
     `report.result` in `sessions.json`, and `.assay/NOTES.md`.
  2. Ask the user with AskUserQuestion. The options: grant one use
     (`approve`), or waive the rehearsal (`waive`, with their reason, for
     REHEARSAL_REQUIRED); continue without it; stop here.
  3. To grant or waive, run the `OWNER |` command yourself, with
     `--token-file ~/assay/tokens/<run name>.token`. Never print the
     token.
  4. Relaunch the runner at once with the same command, because an
     approval lasts 600 seconds. The next session's prompt tells the player
     about the approval.
- **SELF_STOP:** the `DECLARED |` line is the player's reason. Ask whether
  to accept it, which leaves the run as it stands, or to continue. A
  continued run needs a new declaration before the player may stop again.
- **SESSIONS_EXHAUSTED, TIME_EXHAUSTED or USD_EXHAUSTED:** the run is
  unfinished and its daemon is up. Report the progress, and offer to
  continue with the same command.
- **PLAYER_FAILING or START_FAILED:** the `DETAIL |` line and the session's
  `player.err` say why: a host that is not logged in, a model name it does
  not know, or a world that cannot resume because its daemon is gone. Fix
  the cause, then relaunch.
- **STOPPED:** the STOP file or a signal ended it. Delete
  `<run dir>.sessions/STOP` before the next launch.

## 5. Report

Keep it short: the outcome, the sessions, the paid actions of the budget,
the spend the host reported, the audit's verdict, and where the logs are.
Then offer `/assay:report`, which writes what the run verified as a
document. When the run ended SELF_STOP, the report sets the declaration
beside what the run never exercised.
