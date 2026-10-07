# AGENTS.md: for coding agents working on the harness source

You are maintaining the harness, not playing a world. `CONSTITUTION.md` is the
manual an agent under evaluation reads when driving a registered world. It is
not for you. The user-facing story is `ONBOARDING.md`.

## Run the tests

From the repository root, in an environment with numpy, pillow and pytest:

```bash
uv run --with pytest --with numpy --with pillow --with hypothesis pytest tests/
```

or `python3 -m venv .venv && .venv/bin/pip install -e '.[grid,dev]'` and then
`.venv/bin/pytest tests/`. On Linux the sandbox tests need bubblewrap
(`apt install bubblewrap`) and, on Ubuntu 24.04, `sudo sysctl -w
kernel.apparmor_restrict_unprivileged_userns=0`, since AppArmor otherwise
denies bwrap its user namespace; without them the suite runs with the
process limits alone and `tests/test_doctor.py` counts the warning. The suite
drives the real CLI and the real daemon
with the adapters under `tests/`. It redirects `ASSAY_ANCHOR_DIR` and
`XDG_CACHE_HOME` under the pytest temp root and stops every daemon it started
when the session ends, so nothing lands under the home directory. Run the whole
suite before calling a task done, and the linter with it:

```bash
uv run --with ruff ruff check src tests
```

ruff is configured in `pyproject.toml` with its default rule set and nothing
more. `tests/test_hygiene.py` enforces the writing rule over every tracked
file: no em dash and no arrow in the source or the docs, no machine path, the
old manual name gone from the code.

## Where the contracts are

`docs/ARCHITECTURE.md` is the component model and the single source: one
section per component, the frame-world extra, the conformance table, and the
list of what never changes. The public journal contract lives in `verify/`:
`JOURNAL_SPEC.md` (`assay-journal-v1`) and `CLAIM_GRAMMAR.md`, beside the
independent checker. The kernel must keep honoring both.

## What never changes

Everything in `docs/ARCHITECTURE.md` section 5 and in the docstring of
`src/assay/words.py`: journal field names, state values, claim syntax, grade
`actual` texts, host channel names, the `game_id` keys, activity kinds, receipt
outcome tokens, the state-directory layout. `tests/test_conformance.py` and
`tests/test_vocabulary.py` enforce it. The hash chain in `integrity.py` is the
product's trust claim: a change there needs the full suite, not a spot check.

## The kernel's laws

The kernel makes no LLM calls. It imports nothing from `bench/` or
`assay_grid` at module level and names no world. Frame-world behavior lives in
`src/assay_grid/` behind `assay.extras`, selected by observation shape. World
policy lives in the adapter. Module code never bans, it demands structure.

## Add a world

Copy `examples/new_world/` (the adapter and registry contracts are in
`docs/ARCHITECTURE.md` sections 2.1 and 2.2 and in the template's README),
add the adapter to the `ADAPTERS` table in `tests/test_conformance.py`, and
run the template test against it.

## Never launch

`bin/assay` against a benchmark, or anything under `bench/`, in a code task.
Those start long, graded sessions, some against paid APIs. Testing the harness
means running the test suite, not a live session.

## Definition of done, 1.2.0

Every pull request of the milestone meets all seven, and says so with the
commands' output in its description.

1. The suite green and ruff clean:
   `uv run --with pytest --with numpy --with pillow --with hypothesis pytest tests/`
   and `uv run --with ruff ruff check src tests`. From #27 on, strict mypy
   clean as well.
2. A change under `src/` runs G2 and G3. G2 is the replay diff over the 25
   published run directories, run from the owner's archive:
   `python3 <archive>/paper/v4/release-gates/g2_replay_diff.py --main <checkout of 4dc53e1> --release <the checkout under test> --python <an interpreter with numpy and pillow> --vocab`,
   and it must print `G2 | PASS | 0 differing outputs over 25 runs x 4 commands`.
   G3 is `python3 evidence/verify_all.py`, and it must report 66 journals,
   0 failed. The gate masks the campaign kernel's output through the VOCAB
   list in `g2_replay_diff.py`, so a display string the pull request renames
   extends that list in the same change, as narrowly as the renamed phrase
   allows: a rename without its entry fails G2, and an entry without its
   rename is a false pass.
3. A contract change updates `docs/ARCHITECTURE.md` in the same pull request;
   a change to what the agent or the operator sees updates `CONSTITUTION.md`,
   `GUIDE.md` or `ONBOARDING.md`; every pull request adds its line to
   `CHANGELOG.md` under 1.2.0.
4. A pull request under a design note cites the note's section and does not
   depart from it; a departure is a revision of the note first.
5. The pre-tag greps of `RELEASE_CHECKLIST.md` section 3 pass: no world name
   in the kernel, no machine path, no em dash or arrow in `src/` and the
   docs. `tests/test_conformance.py` and `tests/test_hygiene.py` run the same
   checks, so a green suite is the proof.
6. At most one behavior change per pull request, named in its description; a
   refactor says "no behavior change" and G2 proves it.
7. Nearly every test file asserts exact display strings: a rename updates its
   assertions in the same pull request, deliberately, never by loosening them
   to a substring or a pattern that would also accept the old form.

## Commits

Plain messages: what changed and why, the tests run, no decoration.
