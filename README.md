# ASSAY

ASSAY is a **referee harness** that sits between an agent (a person at a
terminal, or an LLM agent) and a world you register:

- **Enforced predict-before-act** — the agent is never told what the
  registered actions do; every paid action requires a checkable prediction,
  validated before anything is spent.
- **Code-graded claims** — every prediction is graded in code against what
  actually happened: change/noop, progress and goal gambles, executable
  verifiers, and named channels with equality, delta and threshold claims.
- **Hash-chained journals** — everything lands in an append-only journal with
  a rolling hash chain and external anchors; `assay audit` recomputes
  integrity from the artifacts alone, and any world contact that bypassed the
  gate marks the run invalid. The journal standard and an independent checker
  are in `verify/`, every published journal with its head in `evidence/`.
- **Memory + agency layer** — knowledge export/import between runs (imported
  knowledge lands FOREIGN and must be re-earned), a standing goal with a
  proposal lane the agent cannot self-ratify, behavior modules, and safety
  gates (destructive, approval, budget, liveness).

You supply two things: a **registry** (a JSON contract of what the agent may
do — names, typed parameter schemas, budgets, flags, the goal) and an
**adapter** (one Python file plugging ASSAY into your world). See `GUIDE.md`
for the user guide, `ONBOARDING.md` for attaching a new world end to end,
`docs/ARCHITECTURE.md` for the component model, `CONSTITUTION.md` for the
agent-facing manual, `AGENTS.md` if you are a coding agent working on the
harness, and `bench/` for benchmark harnesses and results.

## Install

Python 3.12 or newer, macOS or Linux (the daemon uses Unix sockets and
resource limits). Three ways in, pick one:

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
"$ASSAY" act NOOP --predict "change"                # graded ✗ — with the counter-fact
"$ASSAY" channel declare counter --path counter     # register a named reading
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"   # WIN
"$ASSAY" audit                                      # chain + integrity verdict
```

## Status

1.1.0, the first source-available release. The ARC-AGI-3 campaign of
2026-08-22 to 08-25 ran on v1.0-rc1, and the Factorio and OOLONG runs on that
kernel plus two small fixes. 1.1.0 is a refactor of it, verified by replaying
the 25 published run directories unchanged (`CHANGELOG.md`).

## License

ASSAY is source-available under the PolyForm Noncommercial License 1.0.0
(`LICENSE`): you may use, modify and share it for noncommercial purposes. For
commercial terms contact the author. In 1.1.0 the repository takes issues
only, not pull requests (`CONTRIBUTING.md`). The vendored OOLONG scorer is MIT
(`NOTICE`). The journal standard, the independent checker and the published
journals are in `verify/` and `evidence/` under MIT (`verify/LICENSE`), so
anyone can verify a journal without a license to the harness. They moved here
from the retired assay-verify repository, which is archived with a pointer.
