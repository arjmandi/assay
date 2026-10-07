# The new-world template

Copy this folder, rename the world, and you have a world ASSAY can referee.
`adapter.py` is the whole world side (the session contract). `registry.json`
is the whole operator side (what the agent may do). The conformance test
(`tests/test_conformance.py`) drives this template through start, act,
channel declare, commit, reset, the owner operations and the audit, so a
copy that keeps the contract keeps passing.

Run it:

    mkdir vault && cd vault
    assay start vault1 --adapter <repo>/examples/new_world/adapter.py:factory \
        --registry <repo>/examples/new_world/registry.json

## The adapter, line by line

- `factory(root, config)` is the entry point named on the command line.
  `root` is the run directory, `config` is the run's `config.json`
  (`game_id` is the world id you typed, `seed` is `--seed`).
- `observation` returns the dict shape: `state` (`NOT_FINISHED`, `WIN`, or a
  world-specific terminal such as `GAME_OVER`), `levels_completed` and
  `win_levels` (the host progress pair, here rooms done out of two),
  `available_actions` (what can be used right now), and `data`, any JSON
  object. Everything the agent may read and claim against goes in `data`.
- `step(action, data, reasoning)` applies one action the registry already
  validated (`data` holds the typed parameters) and returns the observation.
  A refusal goes **through the observation** (`data.last_result.status` is
  `refused`, `data.refusals` counts) so the spend is journaled as evidence.
  Raising instead aborts the action before anything is spent.
- `finalize()` runs once when `state` becomes `WIN`. It writes its own file
  under `.assay/`, and the kernel never reads it.
- `public_info` is stored in `config.json` at start, for humans and tools.
- Determinism: the code of each door comes from the seed. A local run resumes
  by replaying its journal through a fresh session, so the same actions must
  give the same observations.

## The registry, key by key

Every key beyond `actions` is optional. This file sets all of them so the
shape is visible. Delete what you do not need.

| key | what it does here |
|---|---|
| `actions` | the seven actuators. `TURN delta=<int -3..3>`, `PEEK what=<code\|door>` and `SIREN volume=<float 0..1>` show the three parameter types with bounds and an enum. Every parameter is required on the command line. |
| `description` on `TURN` | admissible, untrusted text shown as data, withheld under `zero_prior` |
| `destructive: true` on `ALARM` | refuses without `--declare worst_case=... --declare recovery=...`, banned inside batches |
| `approval: true` on `DRILL` | default deny, the owner grants one use with `assay approve DRILL --token TOK` |
| `liveness: live` plus `rehearsal_quota: 1` on `SIREN` | refuses until an imported sim-binding run shows one graded rehearsal, or the owner waives it (`assay waive SIREN --token TOK --because ...`) |
| `budget.actions` | the hard cap, and a batch that would cross it is refused whole |
| `budget.usd` | the dollar cap, fed by `assay spend report` |
| `goal.text` | the standing goal shown in every status until the world reports WIN |
| `batching.hand_cap` | hand-written batches cap at 3 steps (the default), and a replay-fit model lifts it |
| `notes_cap` | `.assay/NOTES.md` size in characters (default 16000); paid actions refuse past twice the cap |
| `zero_prior` | true withholds every description |
| `modules` | paths to external behavior module files, pinned at start with a manifest |
| `module_modes` | `off`, `advise` or `block` per module, built-ins included |
| `secrets` | environment variable names whose values are redacted from every journal line |
| `observers`, `control` | accepted and journaled, no behavior in 1.1.0 (an honest gap) |
| `mode_note` | free text shown in status as data |
| `gate` | `required` is the rule and the default. `optional` and `off` are the control-arm modes whose runs the audit marks invalid for scoring |

## What the kernel adds without any work here

Channels (`assay channel declare dial --path dial`), verifiers
(`verify:checks/door_opens.py`), the world model (`assay model`), the six
built-in modules plus the coverage audit, the hash chain and anchors, and the
audit. None of it knows the vault exists.
