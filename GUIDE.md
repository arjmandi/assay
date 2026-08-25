# ASSAY — user guide

ASSAY is a referee harness that sits between an agent (a person at a terminal,
or an LLM agent) and a world you register. The agent is never told what the
registered actions do — it must state a checkable prediction with every paid
action, and ASSAY grades every prediction in code against what actually
happened. Everything lands in an append-only, hash-chained journal. On top of
that sit memory (knowledge export/import between runs), agency (a standing
goal, a proposal lane, behavior modules), and safety gates (destructive,
approval, budget, liveness) — all driven by one JSON file you write.

You supply exactly two things, and they divide cleanly:

- the **registry** — the contract: what the agent is ALLOWED to do (actuator
  names, typed parameter schemas, budgets, safety flags, the goal). Pure
  declaration, no code; the kernel enforces it before anything is spent.
- the **adapter** — the world plug: the one piece of code ASSAY touches your
  world through. It carries BOTH directions of contact: its `observation`
  property is the observer ("what does the world look like right now") and
  its `step()` is the actuator implementation ("apply this validated action,
  return the settled result"). They share one object because in a turn-based
  world an action and its settled observation are a single transaction.

On top of the adapter's raw observation, **channels** (section 4) are the
named, fine-grained observers — readings the agent itself registers, which
claims and world models then grade against.

## 1. Requirements

- Python 3.12+ with `numpy` and `pillow` (only grid-rendering worlds need
  pillow). Benchmark worlds may need their own client packages — each
  `bench/<name>/PROTOCOL.md` names them.
- The launcher is **`bin/assay`** at the repo root; the example world lives
  in **`examples/`** at the repo root. That is the entire public surface.

## 2. Quickstart (60 seconds, no API keys)

```bash
ASSAY=<repo>/bin/assay
mkdir demo && cd demo
"$ASSAY" start counterdemo \
    --adapter "<repo>/examples/counter_world.py:factory" \
    --registry "<repo>/examples/example_registry.json"

"$ASSAY" act INC amount=1 --predict "change"        # graded ✓
"$ASSAY" act NOOP --predict "change"                # graded ✗ — with the counter-fact
"$ASSAY" channel declare counter --path counter     # register a named reading
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"   # WIN
"$ASSAY" audit                                      # chain + integrity verdict
```

Line by line, what actually happens:

- **`start counterdemo --adapter … --registry …`** — creates the run in the
  current directory. `counterdemo` is just the **world id**: a label you
  choose for this run (a benchmark adapter may use it as the id of the game
  to load; your own adapter simply receives it in
  `config["game_id"]`). The `--adapter` flag names your world file and its
  factory function; the `--registry` flag names the JSON declaring what the
  agent may do. `start` spawns the daemon that owns the world, takes the
  first observation as journal event 0, prints the **OWNER TOKEN** once
  (save it outside the run directory — it is your authority for
  ratifications, approvals, and waivers; the agent only ever proposes), and
  prints the full status: the observation, the registered actions with their
  schemas (never their meanings), the budget, and the standing goal.
- **`act INC amount=1 --predict "change"`** — one paid action. The daemon
  validates the name, the typed parameter, the budget, and the claim BEFORE
  spending; applies the action; grades the claim against what actually
  happened. The counter moved, so: ✓ PREDICTED.
- **`act NOOP --predict "change"`** — same claim, but NOOP changes nothing:
  ✗ SURPRISE, with the counter-fact ("no observed change, 0 keys"). Misses
  are the product, not the failure — each one corrects the agent's model at
  the price of one action.
- **`channel declare counter --path counter`** — registers a named reading
  of the observation (here: the `counter` key). From now on claims can name
  it exactly (`ch counter = 3`) instead of the blunt change/noop pair.
  Free, journaled, and it feeds the emergence meter.
- **`act INC amount=2 --predict "ch counter = 3; win"`** — two claims on one
  action: the channel reads exactly 3 afterwards, AND this reaches the goal
  state. Both grade ✓; the world reports WIN; the run is complete and the
  daemon finalizes.
- **`audit`** — recomputes integrity from the artifacts alone: journal
  contiguity, the rolling hash chain, the externally anchored chain heads,
  and a scan for any world contact that bypassed the gate. A clean run says
  CLEAN; an ungated event marks the run invalid for scoring.

Resume is the same command as start: rerun `start` in the same directory
after any crash and the journal replays exactly.

## 3. Adding actuators (the registry)

Actuators are rows in a JSON file. Names and typed parameter schemas only —
**semantics are never written down; the agent earns them by acting.**

```json
{
  "actions": [
    {"name": "MOVE", "params": {"direction": {"type": "str", "enum": ["n","s","e","w"]}}},
    {"name": "BID",  "params": {"amount": {"type": "int", "min": 1, "max": 100}}},
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

What the optional per-action flags buy you:

- `destructive: true` — the action refuses to run without
  `--declare "worst_case=..." --declare "recovery=..."`. It never bans;
  it prices one declaration. Destructive actions cannot hide inside batches.
- `approval: true` — default-deny; each use needs a fresh
  `assay approve NAME --token <owner token>` (expires in 10 minutes, one-shot).
- `liveness: "live"` + `rehearsal_quota: N` — the action refuses until a
  sim-binding run's imported record shows N graded rehearsals, or you waive
  it (`assay waive NAME --token ... --because ...`).
- `description: "..."` — allowed but untrusted; rendered as data, withheld
  entirely when `zero_prior` is on.

Run-level keys: `budget` (hard caps — actions, and optionally dollars fed via
`assay spend report`), `goal` (the standing goal text, re-presented in every
status until the world's win state says achieved), `batching.hand_cap`
(hand-written batches cap at 3 by default; `null` removes the cap; only a
replay-verified model lifts it otherwise), `notes_cap`, `secrets` (env var
NAMES whose values are scrubbed from every journal line), `modules` +
`module_modes` (behavior modules and their advise/block setting).

## 4. Connecting your world (the adapter: observer + actuator bindings)

An adapter is one Python file exposing a factory. ASSAY talks to your world
only through it — observations come in through its `observation` property,
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

Grid worlds return `"frame": [grid]` (a 2-D array of 0–15) instead of
`"data"`; ASSAY then renders images and grid views automatically.

That one observation object is the observer stream. The agent (or you) can
then register **channels** — named readings of it — at run time:
`assay channel declare price --path market.price` (a dotted path), or
`--file extractor.py` for a computed reading (`def extract(obs) -> value`,
sandboxed). Channels are what claims like `ch price delta >= 5` grade
against, and what world models declare.

Two facts worth knowing before you write an adapter:

- ASSAY assumes a **turn-based world**: one action in, one settled
  observation out. Streaming/multi-observer registration is accepted and
  journaled today but drives nothing yet.
- On local (non-competition) runs, resume works by **replaying the journal
  through your adapter** — so your world must be deterministic given the
  same action sequence and seed.

## 5. Running an LLM agent on it

The agent-facing manual is `CONSTITUTION.md` at the repo root. The whole
integration is one prompt: tell the agent to (1) read CONSTITUTION.md
completely, (2) `cd` into a fresh run directory, (3) run the `start` command
with your adapter and registry, (4) solve for the goal, and (5) never touch
the world except through `assay`. Every winning benchmark run used exactly
that shape — see `bench/arcagi/PROTOCOL.md` for a real one.

## 6. Owner operations (your side of the run)

```bash
assay goal ratify 2 --token TOK    # accept the agent's goal proposal #2
assay approve PAY --token TOK      # one use of an approval-gated action
assay waive DRIVE --token TOK --because "sim rehearsed on Tuesday's binding"
assay spend report --usd 4.20 --tokens 91000 --id turn-7  # feed the LLM bill
assay audit                        # chain, anchors, contiguity, ungated scan
assay export                       # knowledge file for the next run
assay start WORLD ... --import assay_knowledge.json   # warm-start, demoted
```

Imported knowledge always lands FOREIGN: prior "Verified" notes demote to
Assumed, imported verifiers/models carry no standing until re-graded here,
and only hazard tags stay active (their whole point is to warn before the
hazard fires again).

## 7. Reading the meters

`assay status` is self-sufficient: the goal, budgets, the claim meters
(world-model misses = does the agent understand the mechanics; gamble misses
= is it converting understanding into progress; sharpness = how much of its
talk was checkable), declared channels, hazard tags, module advisories, and
the emergence meter (self-authored verifiers, channels, models, proposals —
initiative the harness never demanded). `assay audit` is the integrity verdict:
any ungated event marks the run invalid for scoring.

## 8. Honest limits (v1-rc1)

One adapter = one observer stream; turn-based synchronous worlds only;
`observers`/`control` registry blocks are declared-but-inert; the verifier
sandbox is subprocess isolation (empty env, CPU/wall limits), not a network
jail; containment, watchdog, and the wake scheduler are deployment-stage
items that do not exist yet. The gate is daemon-side on registry runs — a
process that bypasses the CLI still cannot spend without its step being
journaled and flagged.
