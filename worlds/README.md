# Worlds: the catalog, the archetypes and the recipe

This directory holds worlds that ASSAY can referee without anyone writing an
adapter. An archetype is an adapter written once, which a recipe configures.
A pack is a recipe ready to run, with its world scripts and a checker. The
`attach` skill (`skills/attach/`, the `/assay:attach` command in Claude Code)
classifies a task with the four questions of `skills/attach/rubric.md` and
then starts a pack, or writes a new recipe from an archetype's template.

## The catalog

| Pack | Code | Clock | Family | What it shows |
|---|---|---|---|---|
| `recipes/spreadsheet-reconcile` | M-E-C-0 | s | Solve | three months of a bank statement against a company's books, with seeded discrepancies; `checks/score.py` scores any final copy against the answer key |
| `recipes/legacy-api` | S-E-C-U | s | Solve | a legacy CRM's customers moved into a new hub through two HTTP APIs whose documentation leaves things out; the hub's own verdict decides the WIN, and `checks/score.py` scores any arm's hub |

A code is the four answers of the rubric: where the agent acts (M this
machine, S a remote system a program drives, H the physical world through
people), what a program can read after one action (E exactly, R roughly, F
only the final result), how you know the task is done (C a check a program
runs, N enough found, P a person judges), and the worst a wrong action does
(0 nothing, U an undoable mess, X money, the irreversible, people or permits).

The benchmark worlds live under `bench/` and are not part of this catalog:
they are the owner's research runs, started by their own protocols.

## The archetypes

| Archetype | Mode | The world | Replay |
|---|---|---|---|
| `local` | copy | files, a simulator or a game on this machine, rebuilt in `.assay/world/` | yes: the world scripts are deterministic given the seed and the actions |
| `repo` | worktree | a code repository copied into `work/`, which the agent edits; the paid action runs the tests | no: the tree changes outside the journal, so the run lives one daemon long |
| `service` | service | an MCP server over stdio or an HTTP API; each paid action is one call, and the observation holds the answer, a dashboard of free reads and the counts | only when the service is local and starts the same every time |

The first two are the command-world engine, `archetypes/command_world.py`,
configured by a recipe whose commands are the world. The third is the service
engine, `archetypes/service_world.py`, with the standard-library MCP client
`archetypes/mcp_client.py`; its recipe names the calls instead of commands
(`service/README.md`). Each archetype directory has a README and a template:
a small world that runs as it is, which a new world copies.
`local/template/` is a switchboard, `repo/template/` a project with a failing
test, and `service/template/` a notebook MCP server.
`service/make_registry.py` drafts a service world's registry and calls from
an MCP server's tool list or an OpenAPI document.

## A pack, file by file

- `recipe.json`: the world for the engine (below), the card (the four
  answers and the tags), and a note for the player.
- `registry.json`: the kernel's contract: the actions with their typed
  parameters, the budget, the goal, the flags (GUIDE section 3).
- `adapter.py`: a stub that finds the engine and binds the recipe beside it.
  Every pack carries the same stub; only its docstring differs.
- The world scripts the recipe's commands run, such as `reconcile.py`.
- `checks/`: the external success checker, which scores a final state the
  same way whichever arm produced it.
- `README.md`: what the pack shows, how to start it, what it costs.

Start a pack from an operator's shell (one directory is one run; the token
file lies outside it):

```bash
mkdir run1 && cd run1
assay start reconcile1 \
    --adapter <repo>/worlds/recipes/spreadsheet-reconcile/adapter.py:factory \
    --registry <repo>/worlds/recipes/spreadsheet-reconcile/registry.json \
    --seed 7 --owner-token-file <tokens>/reconcile1.token
```

The player's session then begins in `run1` at `assay status` (GUIDE section 5).

## The recipe

This section is the command-world recipe. A service recipe keeps the same
`recipe`, `name`, `card`, `player` and the rules in `world`, says `"mode":
"service"`, and puts its calls in a `service` block, which
`archetypes/service/README.md` describes.

```json
{
  "recipe": 1,
  "name": "switchboard",
  "card": {"code": "M-E-C-0", "family": "Solve", "fit": "yes", "clock": "s",
           "proxy": "...", "scarce_commit": null, "hypothesis": "..."},
  "world": {
    "mode": "copy",
    "source": "world",
    "setup": ["{python}", "{recipe}/world.py", "setup", "--workdir", "{workdir}"],
    "observe": ["{python}", "{recipe}/world.py", "observe", "--workdir", "{workdir}"],
    "act": ["{python}", "{recipe}/world.py", "act", "{action}", "--workdir", "{workdir}", "--seed", "{seed}"],
    "actions": ["TOGGLE"],
    "timeout_seconds": 20,
    "win": {"path": "on", "op": "==", "value": 4},
    "lose": {"path": "alarm", "op": "==", "value": true},
    "levels": {"completed": {"path": "rooms_done"}, "total": 2}
  },
  "tests": {"command": ["..."], "timeout_seconds": 120, "max_repeat": 5},
  "player": {"note": "where the agent reads the world"}
}
```

`world` is the engine's part:

- `mode`: `copy` (the local archetype, the default) or `worktree` (the repo
  archetype).
- `source`: a directory copied into the working directory before setup,
  relative to the recipe or absolute. The worktree mode needs one.
- `setup`, `observe`, `act`: argv lists, never shell lines. observe prints the
  world's data as one JSON object. act reads the action's parameters as one
  JSON object on stdin and prints `{"status": "ok" | "refused", "detail":
  "..."}` plus fields of its own. A refusal is the world saying no for its own
  reasons: the spend is journaled as evidence. A non-zero exit, a timeout or
  output that is not that object is a fault of the scripts: the engine puts
  the copy back, raises, and the kernel spends nothing.
- Placeholders in a command: `{python}` (the daemon's interpreter), `{recipe}`
  (the recipe's directory), `{archetypes}` (this catalog's `archetypes/`),
  `{workdir}`, `{state}` (a scratch directory under `.assay/` that a RESET does
  not rewind), `{seed}` and, in act, `{action}`.
- `actions`: the registry's action names. RESET is built in: in the copy mode
  it restores the copy as it stood when the current progress unit began, and
  in the worktree mode it leaves the tree alone.
- `win`, `lose`: conditions over the observation data, a test `{"path", "op",
  "value"}` with `==`, `!=`, `<`, `<=`, `>`, `>=`, or `{"all": [...]}`, `{"any":
  [...]}`, `{"not": ...}`. A path the data does not hold makes its test false,
  and every path a rule reads must be in the first observation.
- `levels`: the progress pair, each a count or `{"path": ...}`. Without it a
  world has one unit, completed on WIN.

The engine adds three keys to the data: `workdir` (relative to the run
directory), `last_result` (the last act's result, with its action) and
`refusals` (a count). The world may not use them.

Two rules keep a copy-mode world honest. The agent can read the working
copy, so hidden state never goes in it: derive it from `{seed}` inside the
act command, as the switchboard's wiring is. And replay rebuilds the world
from the seed and the journal, so the scripts must give the same output for
the same input: no clock, no unseeded randomness, no network.
