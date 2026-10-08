# ASSAY

A **referee harness** that sits between an agent (human or LLM) and a world you
register: enforced predict-before-act, code-graded claims, hash-chained
journals, and a memory/agency layer. `README.md` is the pitch, `GUIDE.md` the
user guide, `ONBOARDING.md` the long form for attaching a world,
`docs/ARCHITECTURE.md` the component model, `CONSTITUTION.md` the agent-facing
manual an *evaluated* agent follows when driving a run. Do not confuse that
manual with these instructions, which are for you working ON the harness's own
code. `AGENTS.md` is the short version of this file for any coding agent.

## Commands

```sh
uv run --with pytest --with numpy --with pillow --with hypothesis pytest tests/      # full suite
uv run --with pytest --with numpy --with pillow --with hypothesis pytest tests/test_registry.py  # one file
uv run --with ruff ruff check src tests                                       # the linter
uv run --with mypy --with numpy --with pillow mypy --strict src/assay src/assay_grid  # strict typing, the kernel and the extra
```

Or, in a venv with the editable install: `pip install -e '.[grid,dev]'` then
`pytest tests/`. The suite isolates anchors and caches under the pytest temp
root and stops every daemon it started. ruff is configured in `pyproject.toml`
with its default rule set, and `uv run --with ruff ruff check src tests` must
pass; mypy is configured there too, strict over `src/assay` and
`src/assay_grid` and checked as Python 3.12, and `uv run --with mypy --with
numpy --with pillow mypy --strict src/assay src/assay_grid` must be clean (a
plain `mypy` from the root is the same run).
`tests/test_hygiene.py` enforces the writing rule (no em dash and no
arrow in the source or the docs, no machine path, the old manual name gone
from the code). `.pre-commit-config.yaml` runs the same ruff and mypy commands
and a grep for the em dash and the arrow before each commit for whoever
installs it (optional; CI is the gate). Run the full suite before considering
any task done. If it fails for reasons unrelated to your change, say so rather
than fixing unrelated breakage.

## Layout

```
src/assay/           the kernel: cli.py (the command line), broker.py (daemon
                      spawn, identity, the serve loop), broker_server.py (the
                      daemon module), live.py (the per-action loop), registry.py,
                      predictions.py, channels.py, verifiers.py, model.py,
                      sandbox.py (the one place agent code runs),
                      modules.py, integrity.py (chain, anchors, audit, redaction),
                      agenda.py, carryover.py, aggregates.py, extras.py (the
                      observation-kind hook), words.py (the display vocabulary),
                      ops.py (the wire table: the daemon operations with their
                      request and result records),
                      meters.py (the journal meters the status lines and the
                      modules share)
src/assay_grid/      the frame-world extra: perception, grid claims, rendering,
                      views
src/assay_cli.py     PEP 723 entry point for the zero-install launcher
bin/assay            the launcher (ASSAY_PYTHON pins the interpreter)
examples/            counter_world.py and example_registry.json (the quickstart),
                      new_world/ (the template a new world copies)
tests/               pytest; e2e tests drive the real CLI and daemon through the
                      adapters in tests/, not mocks; test_conformance.py and
                      test_vocabulary.py enforce the kernel's boundaries,
                      test_hygiene.py the writing rule
bench/{arcagi,factorio,oolong}/   benchmark adapters, registries, protocols and
                      recorded results: long, expensive sessions, not CI
```

## Conventions

- Python 3.12+, stdlib first. The kernel depends on numpy, and pillow is the
  `grid` extra. `pyproject.toml` is the distribution (`assay-harness`).
- Match the surrounding style exactly. One error voice: `ERROR | message`,
  exit 2. Validation before spend. The kernel makes no LLM calls.
- The kernel imports nothing from `bench/` or `assay_grid` at module level
  and names no world (`tests/test_conformance.py`). Its prose says world and
  progress unit (`src/assay/words.py`, `tests/test_vocabulary.py`).

## Constraints that are not obvious from the code

- **Never run `bin/assay` against a benchmark under `bench/`, and never start
  a bench run.** These are real, long, sometimes paid sessions, the owner's
  research runs. Testing the harness means running the test suite, which uses
  the counter, grid, slow and OOLONG spam4k adapters only.
- **`CONSTITUTION.md` is not for you.** It is the manual an agent under
  evaluation reads. You maintain the harness's source.
- **The hash chain in `integrity.py` is the product's core trust claim**, and
  the journal format is a public contract (`verify/JOURNAL_SPEC.md`).
  A change to chaining, redaction, anchors, the ungated rule, or any field
  named in `docs/ARCHITECTURE.md` section 5 needs the full suite green and the
  replay diff over the published run directories, not a spot check.
- **`bench/*/RESULTS.md` and recorded run data are real, verified results.**
  Historical record, never regenerated or edited to match new code.

## Definition of done, 1.2.0

`AGENTS.md` carries the seven items in full; every pull request of the
milestone meets all of them and says so with the commands' output in its
description. In short: the suite green and ruff clean (strict mypy from #27
on); for a change under `src/`, G2 (the replay diff over the 25 published run
directories, run from the owner's archive with `--vocab`; a renamed display
string extends its VOCAB list in the same change, as narrowly as the phrase
allows) printing `G2 | PASS | 0 differing outputs over 25 runs x 4 commands`,
and G3 (`python3 evidence/verify_all.py`) reporting 66 journals, 0 failed; a
contract change updating `docs/ARCHITECTURE.md`, a change to what the agent or
the operator sees updating `CONSTITUTION.md`, `GUIDE.md` or `ONBOARDING.md`,
and a line under 1.2.0 in `CHANGELOG.md` from every pull request; a design
note followed, or revised first; the pre-tag greps of `RELEASE_CHECKLIST.md`
section 3 passing (the suite runs them, in `tests/test_conformance.py` and
`tests/test_hygiene.py`); at most one behavior change per pull request, named
in its description; and every exact-string assertion a rename touches updated
deliberately, never loosened to a form that would also accept the old string.
