# ASSAY: user guide

ASSAY is a referee harness that sits between an agent (a person at a terminal,
or an LLM agent) and a world you register. The agent is never told what the
registered actions do; it must state a checkable prediction with every paid
action, and ASSAY grades every prediction in code against what actually
happened. Everything lands in an append-only, hash-chained journal. On top of
that sit memory (knowledge export/import between runs), agency (a standing
goal, a proposal lane, behavior modules), and safety gates (destructive,
approval, budget, liveness), all driven by one JSON file you write.

You supply exactly two things, and they divide cleanly:

- the **registry**, the contract: what the agent is ALLOWED to do (actuator
  names, typed parameter schemas, budgets, safety flags, the goal). Pure
  declaration, no code; the kernel enforces it before anything is spent.
- the **adapter**, the world plug: the one piece of code ASSAY touches your
  world through. It carries BOTH directions of contact: its `observation`
  property is the observer ("what does the world look like right now") and
  its `step()` is the actuator implementation ("apply this validated action,
  return the settled result"). They share one object because in a turn-based
  world an action and its settled observation are a single transaction.

On top of the adapter's raw observation, **channels** (section 4) are the
named, fine-grained observers: readings the agent itself registers, which
claims and world models then grade against.

## 1. Requirements

- Python 3.12 or newer with `numpy`. `pillow` is the frame-world extra (grid
  observations render to images): `pip install -e '.[grid]'`, and a dict
  world never needs it. Benchmark worlds need their own client packages;
  each `bench/<name>/PROTOCOL.md` names them. macOS and Linux (the daemon
  uses Unix sockets and resource limits).
- Three install paths are in `README.md` (the `bin/assay` launcher with no
  install, an editable install into a venv, pipx). `ONBOARDING.md` is the
  long form of this guide for someone attaching a new world, and
  `docs/ARCHITECTURE.md` is the component model it derives from.

## 2. Quickstart (60 seconds, no API keys)

```bash
ASSAY=<repo>/bin/assay
mkdir demo && cd demo
"$ASSAY" start counterdemo \
    --adapter "<repo>/examples/counter_world.py:factory" \
    --registry "<repo>/examples/example_registry.json"

"$ASSAY" act INC amount=1 --predict "change"        # graded ✓
"$ASSAY" act NOOP --predict "change"                # graded ✗, with the counter-fact
"$ASSAY" channel declare counter --path counter     # register a named reading
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"   # WIN
"$ASSAY" audit                                      # chain + integrity verdict
```

Line by line, what actually happens:

- **`start counterdemo --adapter … --registry …`**: creates the run in the
  current directory. `counterdemo` is just the **world id**: a label you
  choose for this run (a benchmark adapter may use it as the id of the game
  to load; your own adapter simply receives it in
  `config["game_id"]`). The `--adapter` flag names your world file and its
  factory function; the `--registry` flag names the JSON declaring what the
  agent may do. `start` spawns the daemon that owns the world, takes the
  first observation as journal event 0, prints the **OWNER TOKEN** once
  (save it outside the run directory: it is your authority for
  ratifications, approvals, and waivers; the agent only ever proposes), and
  prints the full status: the observation, the registered actions with their
  schemas (never their meanings), the budget, and the standing goal.
- **`act INC amount=1 --predict "change"`**: one paid action. The daemon
  validates the name, the typed parameter, the budget, and the claim BEFORE
  spending; applies the action; grades the claim against what actually
  happened. The counter moved, so: ✓ PREDICTED.
- **`act NOOP --predict "change"`**: same claim, but NOOP changes nothing:
  ✗ SURPRISE, with the counter-fact ("no observed change (0 keys)"). Misses
  are the product, not the failure: each one corrects the agent's model at
  the price of one action.
- **`channel declare counter --path counter`**: registers a named reading
  of the observation (here: the `counter` key). From now on claims can name
  it exactly (`ch counter = 3`) instead of the blunt change/noop pair.
  Free, journaled, and it feeds the emergence meter.
- **`act INC amount=2 --predict "ch counter = 3; win"`**: two claims on one
  action: the channel reads exactly 3 afterwards, AND this reaches the goal
  state. Both grade ✓; the world reports WIN; the run is complete and the
  daemon finalizes.
- **`audit`**: recomputes integrity from the artifacts alone: journal
  contiguity, the rolling hash chain, the externally anchored chain heads,
  and a scan for any world contact that bypassed the gate. A clean run says
  CLEAN; an ungated event marks the run invalid for scoring.

Resume is the same command as start: rerun `start` in the same directory
after any crash and the journal replays exactly.

## 3. Adding actuators (the registry)

Actuators are rows in a JSON file. Names and typed parameter schemas only:
**semantics are never written down; the agent earns them by acting.**

```json
{
  "actions": [
    {"name": "MOVE", "params": {"direction": {"type": "str", "enum": ["n","s","e","w"]}}},
    {"name": "BID",  "params": {"amount": {"type": "int", "min": 1, "max": 100}}},
    {"name": "SEND", "params": {"to": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                "body": {"type": "string", "maxLength": 2000}}},
    {"name": "WIPE", "params": {}, "destructive": true},
    {"name": "PAY",  "params": {}, "approval": true}
  ],
  "budget": {"actions": 200, "usd": 25.0},
  "goal": {"text": "empty the inbox to zero"},
  "batching": {"hand_cap": 3},
  "secrets": ["MY_API_KEY"],
  "zero_prior": false
}
```

A parameter schema is a JSON Schema subset: `integer`, `number`, `string`,
`boolean`, `object` (with `properties` and `required`) and `array` (with
`items`, `minItems`, `maxItems`), `enum`, `minimum`/`maximum`,
`minLength`/`maxLength`, nested as deep as you need (`int`, `float`, `str`,
`min` and `max` are accepted as aliases). The agent types a scalar as
`pname=value` and passes an object or an array as JSON, `assay act SEND
--params '{"to": ["a@x"], "body": "..."}'` (or `--params @FILE`); the kernel
validates the value against the schema before any spend and journals it as
given.

What the optional per-action flags buy you:

- `destructive: true`: the action refuses to run without
  `--declare "worst_case=..." --declare "recovery=..."`. It never bans;
  it prices one declaration. Destructive actions cannot hide inside batches.
- `approval: true`: default-deny; each use needs a fresh
  `assay approve NAME --token <owner token>` (expires in 10 minutes, one-shot).
- `liveness: "live"` + `rehearsal_quota: N`: the action refuses until a
  sim-binding run's imported record shows N graded rehearsals, or you waive
  it (`assay waive NAME --token ... --because ...`).
- `description: "..."`: allowed but untrusted; rendered as data, withheld
  entirely when `zero_prior` is on.

Run-level keys: `budget` (hard caps: actions, and optionally dollars fed via
`assay spend report`), `goal` (the standing goal text, re-presented in every
status until the world's win state says achieved), `batching.hand_cap`
(hand-written batches cap at 3 by default; `null` removes the cap; only a
replay-verified model lifts it otherwise), `notes_cap`, `secrets` (env var
NAMES whose values are scrubbed from every journal line), `modules` +
`module_modes` (behavior modules and their advise/block setting).

## 4. Connecting your world (the adapter: observer + actuator bindings)

An adapter is one Python file exposing a factory. ASSAY talks to your world
only through it: observations come in through its `observation` property,
actions go out through its `step()`. See `examples/counter_world.py` for the
full commented version; the seam is:

```python
def factory(root, config):        # named on the command line as file.py:factory
    return MySession(root, config)

class MySession:
    @property
    def observation(self):
        return {
            "state": "NOT_FINISHED",        # or "WIN" / "GAME_OVER"
            "levels_completed": 0,           # milestones done so far
            "win_levels": 3,                 # total milestones
            "available_actions": ["MOVE", "BID"],   # what is usable right now
            "data": {...},                   # your world state, any JSON object
        }

    def step(self, action, data, reasoning):
        ...apply the action...               # data = validated typed params
        return self.observation

    def finalize(self):                      # optional; called once on WIN
        ...
```

Frame worlds return `"frame": [grid]` (a 2-D array of 0 to 15) instead of
`"data"`; the frame-world extra (`src/assay_grid`, selected by the
observation's shape, never by configuration) then renders images, the scene
dossier and the grid views. A world that refuses an action for its own reasons
reports the refusal through the observation (a status field and a counter), so
the spend is journaled as evidence; raising from `step()` aborts the action
before anything is spent. `examples/new_world/` is a template with every part
of the contract in place.

That one observation object is the observer stream. The agent (or you) can
then register **channels**, named readings of it, at run time:
`assay channel declare price --path market.price` (a dotted path), or
`--file extractor.py` for a computed reading (`def extract(obs) -> value`,
sandboxed). Channels are what claims like `ch price delta >= 5` grade
against, and what world models declare.

Two facts worth knowing before you write an adapter:

- ASSAY assumes a **turn-based world**: one action in, one settled
  observation out. Streaming/multi-observer registration is accepted and
  journaled today but drives nothing yet.
- Unless your adapter declares otherwise (the `session` declaration,
  `docs/ARCHITECTURE.md` section 2.2), resume works by **replaying the
  journal through your adapter**, so your world must be deterministic given
  the same action sequence and seed.
- The daemon runs in the interpreter that ran `assay start`; the adapter's
  dependencies live there. `ASSAY_PYTHON` pins it for the launcher, and
  `assay doctor` reports it, dry-imports the adapter, and checks the registry.

The declare-early, claim-every-action channel pattern that the Factorio runs
used is worked through in `CONSTITUTION.md` (the Channels section) and in
`ONBOARDING.md` chapter 7.

## 5. Running an LLM agent on it

The agent-facing manual is `CONSTITUTION.md` at the repo root. The order is
the operator protocol (`docs/ARCHITECTURE.md` section 8.6): you start the run
and hold the owner token, and the agent's session begins after that.

1. In your own shell: `mkdir <run-dir> && cd <run-dir>`, then
   `assay start WORLD ... --owner-token-file <tokens>/WORLD.token`, a path
   outside the run directory and outside anything the agent reads.
2. Start the agent's session in `<run-dir>`, with the daemon already up. The
   whole integration is one prompt: tell the agent to (1) read
   CONSTITUTION.md completely, (2) begin with `assay status` in the run
   directory, (3) solve for the goal, and (4) never touch the world except
   through `assay`. The agent never runs `start` and never sees the token.
3. Ratify, approve and waive from your shell with the token (section 6); a
   resume is yours too, the same `start` command in the same directory.

See `bench/arcagi/PROTOCOL.md` for a real one. The published benchmark runs
were played before this order existed, with the agent running `start` itself.

## 6. Owner operations (your side of the run)

```bash
assay start WORLD ... --owner-token-file ~/.assay/tokens/run7   # keep the token out of the agent's terminal
assay goal ratify 2 --token-file ~/.assay/tokens/run7    # accept the agent's goal proposal #2
assay approve PAY --token-file ~/.assay/tokens/run7      # one use of an approval-gated action
assay waive DRIVE --token-file ~/.assay/tokens/run7 --because "sim rehearsed on Tuesday's binding"
assay module install audit.py --token-file ~/.assay/tokens/run7   # add a behavior module mid-run (journaled, manifest-pinned)
assay module list                  # active modules, modes, constitution, telemetry
assay spend report --usd 4.20 --tokens 91000 --id turn-7  # feed the LLM bill
assay audit                        # chain, anchors, contiguity, ungated scan
assay stop                         # stop the daemon cleanly; start resumes
assay doctor                       # interpreter, dependencies, anchors, daemon, adapter, registry
assay export                       # knowledge file for the next run
assay start WORLD ... --import assay_knowledge.json   # warm-start, demoted
```

`goal ratify`, `approve`, `waive` and `module install` are daemon operations:
each needs the daemon alive (`assay start` resumes it) and is checked inside
the daemon against the token's hash, so nothing the agent writes into a file
can grant one. `--token-file PATH` reads the token from the file `start`
wrote (outside the run directory), so it never appears in the process list;
`--token TOK` passes it as given. An approval lives in the daemon's memory
for ten minutes and does not survive a stop; a waiver lasts the run.

The owner token is printed once at `start` unless `--owner-token-file` (or
`ASSAY_OWNER_TOKEN_FILE`) writes it to a file outside the run directory. In
every published benchmark run the agent ran `start` itself and therefore held
the token; the protocols now start from your shell with the file (section 5).
Chain heads are anchored
outside the run directory (`ASSAY_ANCHOR_DIR`, recorded in `config.json` at
start, shown on the ANCHORS status line).

Imported knowledge always lands FOREIGN: prior "Verified" notes demote to
Assumed, imported verifiers/models carry no standing until re-graded here,
and only hazard tags stay active (their whole point is to warn before the
hazard fires again).

## 7. Reading the meters

`assay status` is self-sufficient: the goal, budgets, the claim meters
(world-model misses = does the agent understand the mechanics; gamble misses
= is it converting understanding into progress; specificity = how much of its
talk was checkable), declared channels, hazard tags, module advisories, and
the emergence meter (self-authored verifiers, channels, models, proposals:
initiative the harness never demanded). `assay audit` is the integrity verdict:
any ungated event marks the run invalid for scoring.

Every refusal is one line on stderr, `ERROR | CODE | message`, with a second
line `NEXT | hint` when the error names a next step, and the exit status says
what kind it was: 2 for a request that is wrong or refused by rule (the
budget, a gate, a module demand), 3 when the world failed or refused, 4 for a
bug (the traceback is in `.assay/last_error.txt`), 5 when the run can no
longer be scored or continued (a tampered file, a diverged chain or replay).
`docs/ERRORS.md` lists every code. Every command takes `--json` and prints
one JSON document instead of the lines: the result record (`status`, `view`,
`audit`, `act`, `commit`, `reset`, `channel list`, `module list`), the lines
as `{"lines": [...]}` for the others, or the error object on a refusal.

`assay status --brief` is the short form: it drops the lowest-value blocks
(the notes tail, the observation tail, the registry descriptions, the history
beyond four lines, in that order and only as far as needed) to fit 1500
tokens, or the registry's `status_budget` when that is smaller, and ends with
one line, `TRUNCATED | <blocks> dropped to fit N tokens; assay status --json
carries them all`. What a dropped block held is where it always was: the
notes in `.assay/NOTES.md`, the observation under `assay view --event N`, and
everything, descriptions and history included, in the record `assay status
--json` carries. A registry `status_budget` applies to every status, `--brief`
or not. Under `--json`, `status`, `act`, `commit` and `reset` also carry
`estimated_tokens`, an estimate of the prose they would have printed
(characters over four, no tokenizer), and `status` carries `truncated`, the
blocks the budget dropped, with the record always whole.

## 8. Honest limits (1.2.0)

One adapter = one observer stream; turn-based synchronous worlds only;
`observers`/`control` registry blocks are declared-but-inert; agent code
(verifiers, extractors, the world model) runs under `sandbox-exec` on macOS or
`bwrap` on Linux with no network, no fork, no path into the run directory, and
CPU, file-size and wall-clock limits, the 512 MB memory limit being Linux-only;
a machine with neither tool gets the process limits alone and `assay doctor`
says `sandbox | process isolation only`; `assay python` runs in-process without
any of it; containment,
watchdog, and the wake scheduler are deployment-stage items that do not exist
yet; macOS and Linux only. The gate is daemon-side on registry runs: a
process that bypasses the CLI still cannot spend without its step being
journaled and flagged. The registry's `gate: optional` and `gate: off` are
control-arm switches for experiments; the audit marks such runs invalid for
scoring.
