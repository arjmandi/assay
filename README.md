# ASSAY

ASSAY is a reasoning harness. To start, it needs a world to interact with, tools it
has access to, and the goal we want it to achieve. Then it starts to explore that world
by building a small formal language that symbolically describes the world. It trusts
its own world model only where that model predicted the world correctly: every
prediction is labeled held or missed, and the agent reasons from what held. The formal
language is the basis for addressing context rot and referent grounding issues in LLM
(and LRM) agents, and it reduces the need for compaction or summarization of knowledge
in long-running agents working toward a goal. Prediction is the agent's way of
fact-checking its knowledge against the environment: a fact enters its world model
only through a prediction that explained how the world behaved, so the agent can trust
what it learned when it reasons toward the goal.

## What you supply

A world, through an **adapter**: one Python file that carries an action to the
world and brings the observation back. The actions the agent may take, through
a **registry**: a JSON contract with the action names, their typed parameters,
the budgets, the flags and the goal. And a manual, `CONSTITUTION.md`, that
tells the agent how to learn any world under the harness. The agent operates
the harness from a shell through the `assay` command line, or through the tool
server, which offers the same operations as tools. The kernel makes no model
calls, so any model process that can drive a shell can play, and so can a
person at a terminal.

`GUIDE.md` is the user guide. `ONBOARDING.md` attaches a new world end to
end. `docs/ARCHITECTURE.md` is the component model. `AGENTS.md` is for a
coding agent working on the harness. `bench/` holds the benchmark worlds and
their results.

## The language

The agent names the parts of the world it cares about as addressable states:
a path into the observation, or a small program that extracts a value from
it. The grammar then gives it a fixed set of forms to say what an action will
do: a state equals a value, moves by an amount or crosses a threshold, the
observation changes or stays the same, a level clears, the goal is reached,
or a program the agent wrote decides. One prediction can carry several
outcomes:

```bash
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"
```

Two outcomes: the state named `counter` reads 3 after the action, and the
world reports its goal reached. What the forms cannot say, a verifier program
can, and the agent writes those too.

## Held or missed

Before every paid action the agent writes its prediction, and the daemon
refuses an action without one. After the action, code checks each outcome
against what the world reported and labels it held or missed. The receipt
shows the labels, and on a miss it shows the value the world reported:

```
  ✓ ch nkeys = 3
  ✗ ch hl = 1 | ch hl = 0
```

What held is a fact in the record. What missed stays in the record as a
miss. The agent reasons from the facts.

## Fact-checking as grounding

Nothing enters the agent's world model except through a prediction that was
checked. The agent's own notes are not the record, and knowledge imported
from an earlier run enters demoted and must be re-earned by predictions in
the new run. The record lives on disk, and the agent looks a state up by its
address instead of recalling it, which is what lets a long run keep its
detail instead of summarizing it. The one page of notes the agent keeps is
its own summary, and the manual says what belongs there.

## Reasoning layers

ASSAY places what an agent needs to reason at five layers, from the most
general to the most specific. Each layer is a place to steer the agent, and
each layer tells the operator where to work when the agent fails.

1. **The constitution.** The mental model a domain needs, written by a human
   once. It says how to learn any world under the harness, and it carries the
   experience that pretraining does not. Change it when the agent lacks a way
   of working that no training data contains.
2. **Modules.** Reasoning in code that the agent can pick up. A module
   watches the record and advises or demands. Change this layer when the
   agent fails to recognize a pattern that repeats across a long trace.
3. **The registry and the adapter.** The description of one world and the
   only way to touch it. The registry lists the actions, their typed
   parameters, the budgets and the goal. The adapter carries an action to the
   world and the observation back. Change this layer when the failures come
   from the interaction itself.
4. **Addressable states.** The world model the agent builds: the states it
   named, their readings, the predictions it made over them and how each was
   labeled. Change this layer when reasoning breaks from forgetting or from
   referents that drift.
5. **The model.** Everything behind the model's API. This layer is not
   ASSAY's.

The more the model already knows the world, the less the layers above it
need to say. The more the agent struggles on its own, the more they carry.

## Platforms

macOS and Linux. The daemon listens on a Unix domain socket, the run state is
guarded by file locks, and verifiers run under resource limits. Windows
provides none of these in the same form and is not supported.

## Install

Python 3.12 or newer, macOS or Linux (see Platforms). Three ways in, pick
one:

1. **No install.** `bin/assay` is the launcher: it runs the first `python3` on
   PATH that has numpy 2.x, else `uv run` resolves the inline dependencies,
   else it builds a private runtime once. `ASSAY_PYTHON=/path/to/python`
   pins the interpreter.
2. **Editable install, recommended for benchmark worlds.** The same venv holds
   the adapter's dependencies, and the daemon inherits it:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -e '.[grid]'
   .venv/bin/assay doctor            # or put .venv/bin on PATH
   ```

   `[grid]` adds pillow for frame worlds (rendering). A dict world does not
   need it. `[arcagi]` adds the ARC-AGI-3 client. `[dev]` adds pytest.
   `[server]` adds the `mcp` package for `assay serve-tools`, the tool server
   that serves the agent-facing operations as MCP tools over the same daemon
   (GUIDE section 5).
3. **The CLI alone.** `pipx install '.[grid]'` puts `assay` on PATH in its own
   environment, for worlds whose adapters have no dependencies of their own.

`assay doctor` checks the interpreter, the dependencies, the anchor directory,
and, inside a run directory, the daemon, the adapter and the registry.

## Quickstart (60 seconds, no API keys)

```bash
ASSAY=<repo>/bin/assay
mkdir demo && cd demo
"$ASSAY" start counterdemo \
    --adapter "<repo>/examples/counter_world.py:factory" \
    --registry "<repo>/examples/example_registry.json"

"$ASSAY" act INC amount=1 --predict "change"        # graded ✓
"$ASSAY" act NOOP --predict "change"                # graded ✗, with the counter-fact
"$ASSAY" state declare counter --path counter       # declare an addressable state
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"   # WIN
"$ASSAY" audit                                      # chain + integrity verdict
```

The third command declares an addressable state. `assay channel declare` is
the same command under its earlier name and answers for one release. The
keyword `ch` keeps its spelling in the journal.

Every command takes `--json` and prints one JSON document. A result record
comes back for `status`, `view`, `audit`, `act`, `commit`, `reset`,
`state list` and `module list`, and the command's lines come back
otherwise. A refusal is one line, `ERROR | CODE | message`, with the codes
listed in `docs/ERRORS.md`.

The quickstart is one person at a terminal playing both roles. An evaluated
agent does not start the run. The operator starts it with
`--owner-token-file`, and the agent's session begins at `assay status`
(`GUIDE.md` section 5).

## Trust model

ASSAY's record is tamper-evident, not tamper-proof. The agent and the daemon
run as the same user, so the agent can write the run directory. The daemon
holds the chain head in memory and verifies the journal on disk before every
paid action. On a difference it refuses to continue and seals the anchor
file at the last trusted event. In a Claude Code session the shipped hooks
refuse writes under `.assay/` and world access that bypasses `assay`. The
operator holds the owner token outside the run, and approvals and waivers
are granted inside the daemon against it. The activity log is the agent's to
write, so a waiver or an installed module recorded there is admitted at the
next start on the operator's word, not the daemon's. The separate-user setup
is what puts it out of reach. An agent that rewrites the journal, the chain
file, the mutation log and the anchor file consistently while the daemon is
stopped is caught only by a head published or anchored outside its reach.
That is why heads are published with every evidence pack, and why a separate
user for the daemon is the recommended setup where the stakes warrant it.
Nothing the agent asserts is load-bearing. Every grade is computed by the
kernel from the world's own response, and the published journals replay
through the engine.

## Status

1.2.0 is the first open-source release. The ARC-AGI-3 campaign of
2026-08-21 to 08-23 ran on the kernel published as v1.0-rc1, and the Factorio
and OOLONG runs on that kernel plus two small fixes. The 1.2.0 kernel is a
refactor of it, verified by replaying the 25 published run directories
unchanged. The experiments of October 2026 ran on commit 6ea56e4 of it
(`CHANGELOG.md`).

## License

ASSAY is open source under the Apache License 2.0 (`LICENSE`, `NOTICE`). In
1.2.0 the repository takes issues only, not pull requests (`CONTRIBUTING.md`). The vendored OOLONG scorer is MIT
(`NOTICE`). The journal standard, the independent checker and the published
journals are in `verify/` and `evidence/` under MIT (`verify/LICENSE`), so
anyone can verify a journal without a license to the harness. They moved here
from the retired assay-verify repository, which is archived with a pointer.
