# Release checklist, 1.1.0

What is checked before the repository goes public and the tag is cut. Every
item is a command or a decision, nothing is a feeling. The owner runs the
decisions and the tag; everything else can run on any checkout of
`release/1.1.0`.

## 1. Branch policy

- `release/1.1.0` is cut from `main` (0373b5f). The experiment branch
  `exp/2026-10` is never merged. Two things were taken from it by cherry-pick
  and file copy (the gate flag commit, the coverage module file, both recorded
  in `CHANGELOG.md`). When the experiments are done, archive it as a tag
  (`git tag exp/2026-10-final exp/2026-10`) and do not push it unless its
  tooling has been sanitized (owner decision O4).
- The nine `agent/issue-*` branches on origin are internal workflow residue
  with no reader. Delete them before the flip (owner decision O8):
  `git push origin --delete agent/issue-736 ...` for each.

## 2. Must not ship

Present on `exp/2026-10`, absent from `release/1.1.0`, and kept that way by
the pre-tag grep in section 3:

- `tools/` entirely (the night orchestrator, job files, prompt templates,
  ledgers, status files, STOP, dryrun): 40 plus absolute machine paths.
- `tests/test_night_orchestrator.py`, `tests/test_e3_prepare.py`, the
  archived-run test of the old `tests/test_coverage_module.py`, and the
  absolute checker path that `tests/test_gate_optional.py` carried (replaced
  by `ASSAY_VERIFY` or a sibling checkout).
- `bench/arcagi/registry_e2_1500_coverage.json` (an absolute module path).
- The E1 registries and the two ungated constitution variants with
  `E1_CONSTITUTION_DIFF.md` (whether they ship is owner decision O4).
- The private handoff documents (`HANDOFF.md`, `REVIEW-20260910.md`) stay in
  the assay-coverage-handoff folder.
- The fleet operations sections of `CLAUDE.md`, `docs/agent-journal/`, and
  the fleet deny rules of `.claude/settings.json`: gone on this branch.
- Run state (`.assay/`), `.DS_Store`, build output: ignored by `.gitignore`.

## 3. Pre-tag greps (run on the release branch, expect nothing)

```bash
git ls-files | grep -E '^tools/|test_night_orchestrator|test_e3_prepare|registry_e2_1500|agent-journal|CONSTITUTION-ungated'
git grep -I -n -E '/Users/|/home/' -- .            # machine paths: none
git grep -n -i 'doctrine' -- src tests examples    # the old manual name: none in code
git grep -n -i -E 'factorio|oolong|\barc\b|arc_agi|arcengine' -- src/assay   # world names in the kernel: none
```

## 4. Secrets scan over every branch

```bash
tips=$(git for-each-ref --format='%(objectname)' refs/heads refs/remotes | sort -u)
# key shapes in any tree at any branch tip
git grep -I -n -E '(sk-ant-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})' $tips -- .
# secret assignments anywhere in history
git log --all -p -G'(ANTHROPIC_API_KEY|ARC_API_KEY|RCON_PASSWORD|OPENAI_API_KEY)\s*=\s*["'"'"']?[A-Za-z0-9]{12,}' --format='%h %s' | grep -E '^[0-9a-f]{7} '
# machine paths per branch tip (release and main must be 0; exp is known to carry them)
for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do printf '%s: ' "$b"; git grep -I -n -E '/Users/|/home/' "$b" -- . | wc -l; done
# email addresses outside the upstream OOLONG corpus text
git grep -I -n -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' release/1.1.0 -- . ':!bench/oolong/packs'
# the owner's name outside copyright lines
git grep -n -i 'arjmandi' release/1.1.0 -- . ':!LICENSE' ':!NOTICE'
```

Expected: no key shapes, no secret assignments in history, zero machine paths
on `release/1.1.0` and `main`, emails only inside `bench/oolong/packs`
(upstream dataset text, owner decision O3), the owner's name only in
`pyproject.toml` (the authors field) and the README's license section.

## 5. Regression gates

| Gate | Command | Required result |
|---|---|---|
| G1 the suite | `uv run --with pytest --with numpy --with pillow pytest tests/` | all green (the 74 of main plus every new test) |
| G2 replay diff | `python3 paper/v4/release-gates/g2_replay_diff.py --main <main checkout> --release <release checkout> --python <venv python> --vocab` (in the archive) | zero differences over the 25 published run directories, four commands each |
| G3 independent checker | `python3 paper/v4/release-gates/g3_verify.py` (in the archive) | 25 published ARC runs CLEAN with heads matched, the Factorio and OOLONG packs CLEAN, the 24 E1, E2 and E3 runs CLEAN |
| G4 ft09 live regression | scheduled by the owner on the subscription, after code freeze, per DECISIONS item 18 | WIN 6 of 6 within the published bar |
| G5 clean machine | `bash paper/v4/release-gates/g5_clean_machine.sh <scratch dir>` (in the archive) | PASS |

## 6. Owner decisions that gate the tag

From the release review, section 7. Each is marked `TODO(owner: O<n>)` where
the conservative default was implemented.

| Decision | Default on the branch | Blocks |
|---|---|---|
| O1 grid claim forms on frame registry runs | refused, as before | nothing (behavior preserved) |
| O2 the legacy numbered-action path | moved to `assay_grid.legacy`, undocumented | nothing (deletion is for 1.2) |
| O3 the OOLONG corpora | all packs still in the tree, terms stated in NOTICE | the repository size and the dataset text going public |
| O4 the experiment tooling and the E1 files | left on `exp/2026-10` | nothing on the branch (the paper cites the archive) |
| O5 the owner token | `--owner-token-file` plus the honest paragraph | nothing (the protocols are unchanged) |
| O6 hot-load | the manifest, owner install | nothing |
| O7 the world id rule | kept at `[a-z0-9]{2,16}`, the message names it | nothing |
| O8 the nine agent branches | untouched | the flip (delete before publishing) |
| O9 contributions under PolyForm NC | no statement yet | the README's contribution line |
| O10 the INTEGRITY line on control-arm runs | kept as is, disclosed | nothing (future users only) |
| O11 cut order under time pressure | not needed, the plan completed | nothing |

## 7. The tag

After G1, G2, G3 and G5 pass on the branch and G4 passes on it live, set the
date in `CHANGELOG.md`, then:

```bash
git tag -a v1.1.0 -m "ASSAY 1.1.0" release/1.1.0
```

Not before, and not by anyone but the owner.
