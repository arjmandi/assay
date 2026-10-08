# ASSAY

ASSAY is a reasoning harness. It gives a language-model agent a world to think in
that is not its context window. The agent names the parts of its environment it cares
about as addressable states, reads them through the harness, and before it acts it
says what the action will do to them. Code grades that claim against what the
environment reports. What held goes into the record, what missed is marked, and the
agent learns the world one graded claim at a time. Over a run the record becomes a
world model the agent consults by address instead of by memory: a state is looked up,
not recalled, so a long run does not decay the way a long context does, with details
lost in the middle and referents drifting, and the agent cannot fill its own model
with things it merely said. The record lives on disk, hash-chained, replayable, and
checkable by anyone without the harness.

## Five layers

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
   named, their readings, the claims it made over them and how each was
   graded. Change this layer when reasoning breaks from forgetting or from
   referents that drift.
5. **The model.** Everything behind the model's API. This layer is not
   ASSAY's.

The more the model already knows the world, the less the layers above it
need to say. The more the agent struggles on its own, the more they carry.

## What you supply

A world is attached with two files. The **registry** is a JSON contract of
what the agent may do: the action names, their parameter schemas, the
budgets, the flags and the goal. The **adapter** is one Python file that
plugs ASSAY into your world. The kernel makes no model calls. Any model
process that can drive a shell can operate it, and so can a person at a
terminal.

`GUIDE.md` is the user guide. `ONBOARDING.md` attaches a new world end to
end. `docs/ARCHITECTURE.md` is the component model. `CONSTITUTION.md` is the
manual the agent reads. `AGENTS.md` is for a coding agent working on the
harness. `bench/` holds the benchmark worlds and their results.

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
"$ASSAY" channel declare counter --path counter     # register a named reading
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"   # WIN
"$ASSAY" audit                                      # chain + integrity verdict
```

The third command declares an addressable state. The command keeps the name
`channel` until the next release renames it to `assay state declare`, and
the claim keyword `ch` keeps its spelling in the journal.

Every command takes `--json` and prints one JSON document. A result record
comes back for `status`, `view`, `audit`, `act`, `commit`, `reset`,
`channel list` and `module list`, and the command's lines come back
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
