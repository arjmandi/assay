# Release checklist, 1.2.0

What is checked before the repository goes public and the tag is cut. Every
item is a command or a decision, nothing is a feeling. The owner runs the
decisions and the tag; everything else can run on any checkout of `main`.

## 1. Branch policy

- `main` is the integration branch. Each issue of the milestone is one pull
  request from a branch `issue-<n>` cut from `main` and squash-merged into
  it. There is no release branch: the tag is cut on `main` (section 7).
- No 1.1.0 was released. The tag `v1.1.0` and the branch `release/1.1.0`
  named commit 6ea56e4, the build the evidence packs `e1b`, `factorio-110`
  and `e5` ran on and the paper calls 1.1.0; both refs are retired by the
  owner and the build is named by its commit (#28). The experiment branch
  `exp/2026-10` is never merged; it is archived as the tag `exp-2026-10`
  (owner decision O4).
- The corpus history rewrite of 2026-10-07 (O3: the six length-ladder files
  `bench/oolong/packs/corpus_synth{128k,1m,4m}.txt` and
  `questions_synth{128k,1m,4m}.jsonl` out of every commit) was executed, and
  the old and new tips of every moved ref are recorded in the archive's
  release log. What remains here is the pair of greps in section 3 that keep
  the text out of the tree and out of the history.
- The `agent/issue-*` branches on origin were deleted (O8).

## 2. Must not ship

Present on `exp/2026-10`, absent from `main`, and kept that way by the
pre-tag grep in section 3:

- No machine path under `tools/` (the hygiene test enforces it tree-wide).
  The night orchestrator that ran the paper's experiments, with its job
  files, prompt templates, ledgers, status files, STOP and dryrun output,
  carried 40 plus absolute paths and stays on the experiment tag. What ships
  in its place is the evaluation runner `tools/eval/` (#22): relative paths
  only, the world names in the job files under `bench/*/jobs/`, and its
  state (runs, tokens, ledgers, STOP) in a directory outside the tree.
- `tests/test_night_orchestrator.py`, `tests/test_e3_prepare.py`, the
  archived-run test of the old `tests/test_coverage_module.py`, and the
  absolute checker path that `tests/test_gate_optional.py` carried (replaced
  by the checker's place in this repository, `verify/assay_verify.py`).
- `bench/arcagi/registry_e2_1500_coverage.json` (an absolute module path).
- The E1 registries and the two ungated constitution variants with
  `E1_CONSTITUTION_DIFF.md` (whether they ship is owner decision O4).
- The private handoff documents (`HANDOFF.md`, `REVIEW-20260910.md`) stay in
  the assay-coverage-handoff folder.
- The fleet operations sections of `CLAUDE.md`, `docs/agent-journal/`, and
  the fleet deny rules of `.claude/settings.json`: gone from `main`.
- Run state (`.assay/`), `.DS_Store`, build output: ignored by `.gitignore`.
- `bench/oolong/packs/corpus_synth*.txt` and `questions_synth*.jsonl` (the
  length-ladder dataset text, O3): out of the tree and out of the history
  since the rewrite of 2026-10-07 (section 1).

## 3. Pre-tag greps (run on `main`, expect nothing)

```bash
git ls-files | grep -E 'test_night_orchestrator|test_e3_prepare|registry_e2_1500|agent-journal|CONSTITUTION-ungated'
git grep -I -n -E '/Users/|/home/' -- .            # machine paths: none (tools/ included; the runner's state lives outside the tree)
git grep -n -i 'doctrine' -- src tests examples    # the old manual name: none in code
git grep -n -i -E 'factorio|oolong|\barc\b|arc_agi|arcengine' -- src/assay   # world names in the kernel: none
git grep -n -E $'\u2014|\u2192' -- src tests examples docs verify '*.md' ':!bench/*/RESULTS.md' ':!evidence'   # em dashes and arrows: none (tests/test_hygiene.py covers the whole tree)
git ls-files | grep -E 'packs/(corpus|questions)_synth'                       # the length-ladder text: none
git log --all -- 'bench/oolong/packs/corpus_synth*' 'bench/oolong/packs/questions_synth*'   # and none in history
```

## 4. Secrets scan over every branch

```bash
tips=$(git for-each-ref --format='%(objectname)' refs/heads refs/remotes | sort -u)
# key shapes in any tree at any branch tip
git grep -I -n -E '(sk-ant-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})' $tips -- .
# secret assignments anywhere in history
git log --all -p -G'(ANTHROPIC_API_KEY|ARC_API_KEY|RCON_PASSWORD|OPENAI_API_KEY)\s*=\s*["'"'"']?[A-Za-z0-9]{12,}' --format='%h %s' | grep -E '^[0-9a-f]{7} '
# machine paths per branch tip (main must be 0; exp is known to carry them)
for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do printf '%s: ' "$b"; git grep -I -n -E '/Users/|/home/' "$b" -- . | wc -l; done
# email addresses outside the upstream OOLONG corpus text
git grep -I -n -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' main -- . ':!bench/oolong/packs'
# the owner's name outside copyright lines
git grep -n -i 'arjmandi' main -- . ':!LICENSE' ':!NOTICE'
```

Expected: no key shapes, no secret assignments in history, zero machine paths
on `main`, emails only inside `bench/oolong/packs`
(the smoke packs' upstream dataset text), the owner's name only in
`pyproject.toml` (the authors field and the homepage), `verify/LICENSE` (the
MIT copyright line) and this file's own commands. The two machine-path hits
are this file's grep patterns, not paths.

## 5. Regression gates

| Gate | Command | Required result |
|---|---|---|
| G1 the suite | `uv run --with pytest --with numpy --with pillow --with hypothesis pytest tests/` | all green |
| G2 replay diff | `python3 paper/v4/release-gates/g2_replay_diff.py --main <main checkout> --release <release checkout> --python <venv python> --vocab` (in the archive; `--main` is a checkout of 4dc53e1, the kernel the campaigns ran on, `--release` the checkout under test) | zero differences over the 25 published run directories, four commands each |
| G3 independent checker | `python3 paper/v4/release-gates/g3_verify.py` (in the archive), and `python3 evidence/verify_all.py` here | 25 published ARC runs CLEAN with heads matched, the Factorio, OOLONG, G4, `factorio-110` and E5 packs CLEAN, the 24 E1, E2 and E3 runs CLEAN, the six E1b runs INVALID FOR SCORING as their pack states, all 66 published journals matching their heads |
| G4 ft09 live regression | scheduled by the owner on the subscription, after code freeze, per DECISIONS item 18 | WIN 6 of 6 within the published bar |
| G5 clean machine | `bash paper/v4/release-gates/g5_clean_machine.sh <scratch dir>` (in the archive) | PASS |

## 6. Owner decisions

From the release review, section 7, decided on 2026-10-07 and implemented
in the second round of the release work (the archive's release log has one
entry per commit). The table is the historical record of that day, kept as
written: it says 1.1.0 because that is what the build was then called.

| Decision | On the branch | Blocks |
|---|---|---|
| O1 grid claim forms on frame registry runs | refused, as before | nothing (behavior preserved) |
| O2 the legacy numbered-action path | decided: deleted with the rules tier that only ran on it, a registry is required | nothing |
| O3 the OOLONG corpora | decided: the smoke packs ship, the others fetch on first use, the tree is clean | the history rewrite (section 1), before the flip |
| O4 the experiment tooling and the E1 files | left on `exp/2026-10`, tagged `exp-2026-10` and the tag pushed | nothing on the branch (the paper cites the archive) |
| O5 the owner token | decided: `--owner-token-file` ships (K7), the protocol rewrite is deferred to the next release | nothing |
| O6 hot-load | the manifest, owner install | nothing |
| O7 the world id rule | decided: any string up to 64 characters without whitespace, control characters or path separators, kept as given | nothing |
| O8 the nine agent branches | untouched (three of them move in the history rewrite) | the flip (delete before publishing) |
| O9 contributions | decided: Apache License 2.0 (changed from PolyForm NC on 2026-10-07), issues only in 1.1.0, stated in README.md and CONTRIBUTING.md | nothing |
| O10 the INTEGRITY line on control-arm runs | decided: a neutral `GATE` line with a count, the audit unchanged | nothing |
| O11 cut order under time pressure | not needed, the plan completed | nothing |

## 7. The tag

After the gates of issue #32 pass on `main` (G1, G2, G3 and G5 on the tip,
G4 live on it), set the date in `CHANGELOG.md`, then:

```bash
git tag -a v1.2.0 -m "ASSAY 1.2.0" main
```

Not before, and not by anyone but the owner.
