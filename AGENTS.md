# For coding agents working on the harness

ASSAY is an agent harness: an agent predicts what each action will do, code
grades the prediction against the world's response, and the graded record is
the agent's world model. You are maintaining the harness, not playing a
world. `CONSTITUTION.md` is the manual an agent *under* the harness reads; it
is not for you. `README.md` is the pitch and the user guide,
`docs/ARCHITECTURE.md` the reference for every format and rule.

## Commands

```sh
uv run --with pytest --with numpy --with pillow pytest tests/           # the suite
uv run --with ruff ruff check src tests worlds                            # the linter
uv run --with mypy --with numpy --with pillow mypy --strict src/assay     # strict typing
```

Run all three before calling a task done. The suite drives the real command
line and the real daemon on the counter world and on a small frame world,
keeps its anchors under the pytest temp root and stops every daemon it
starts. Test the harness through the suite, not by launching `bin/assay`
against a world: a real run is long and can be paid.

## Layout

```
src/assay/           the kernel, one concept per module (see its __init__.py):
                       world, journal, grammar, states, grade, sandbox, daemon,
                       meters, modules, show, diagnose, analysis, cli
src/assay/grid/      the frame-world extra, loaded only for grid observations
bin/assay            the launcher (ASSAY_PYTHON pins the interpreter)
worlds/counter/      the example world: the whole adapter contract in one file
worlds/arc-agi-3/    the ARC-AGI-3 adapter and registry
tests/               end to end through the CLI, the units, the layers, the laws
docs/ARCHITECTURE.md the reference
CONSTITUTION.md      the agent-facing manual
```

## What must hold

- **The kernel makes no model calls**, names no world and imports nothing
  from a world. World policy lives in an adapter. `tests/test_laws.py`.
- **The journal line format is frozen** (`assay-journal-v1`, section 3 of the
  architecture document): key names, the chain rule and its seed, the
  ungated rule. A change there is a change to a public contract.
- **A module advises and never refuses.** The gate refuses only what is
  listed in section 6 of the architecture document.
- **The manual, the reference and the code agree.** A change to what the
  agent sees updates `CONSTITUTION.md`; a change to a format or a rule
  updates `docs/ARCHITECTURE.md`; `tests/test_laws.py` checks the manual
  against the command line and the grammar.
- **The writing rule**: no em dash and no arrow character in the source or
  the docs, and no machine path.
- One error voice: `ERROR | CODE | message`, then `NEXT | hint`, exit 2.
  Validate before anything is spent.

## Commits

Plain messages: what changed and why, and the tests run.
