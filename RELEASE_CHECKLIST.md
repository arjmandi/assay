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
- The OOLONG length-ladder text (O3, decided): six files,
  `bench/oolong/packs/corpus_synth{128k,1m,4m}.txt` and
  `questions_synth{128k,1m,4m}.jsonl`, are out of the tree and must be out of
  the history before the flip. They entered in one commit, 0b6e6b9 (2026-08-26,
  on main before the branch point), so the release branch cannot lose them
  without rewriting main's shared history: the whole repository is rewritten.
  The procedure, proven on a scratch mirror on 2026-10-07 (49 commits rewritten
  from 0b6e6b9 on, six refs moved: `main`, `release/1.1.0`, `exp/2026-10`,
  `agent/issue-767`, `-768`, `-776`; `v1.0-rc1` and the seven older agent
  branches unchanged), run in the repository itself by the owner:

  ```bash
  git filter-repo --invert-paths \
    --path bench/oolong/packs/corpus_synth128k.txt --path bench/oolong/packs/corpus_synth1m.txt \
    --path bench/oolong/packs/corpus_synth4m.txt --path bench/oolong/packs/questions_synth128k.jsonl \
    --path bench/oolong/packs/questions_synth1m.jsonl --path bench/oolong/packs/questions_synth4m.jsonl \
    --replace-refs delete-no-add --force
  git remote add origin https://github.com/arjmandi/assay.git   # filter-repo strips it
  git log --all -- 'bench/oolong/packs/corpus_*'                 # expect f39b47f (the smoke packs) alone
  ```

  Before: a clean working tree and clean agent worktrees (filter-repo resets
  the checked-out one; the agent worktrees at `~/agents/assay/issue-*` are
  realigned with `git reset --hard` in each or removed). After: do not `git
  fetch` before the force-push, or the old `origin/*` refs bring the text
  back. Then `git push --force origin main release/1.1.0` and the tags, and
  delete the agent branches on origin (O8). The pre-rewrite objects stay on
  GitHub until its support purges them. The old and new tips of every moved
  ref are recorded in the archive's release log. Commit hashes quoted in
  `CHANGELOG.md` from 0b6e6b9 on (0373b5f, 1630e46, 76ae414, a3233df, 82bfd5d
  and the round-2 commits) change with the rewrite; filter-repo leaves the
  map in `.git/filter-repo/commit-map`, and the quoted hashes are updated
  from it in one last commit before the tag.

## 2. Must not ship

Present on `exp/2026-10`, absent from `release/1.1.0`, and kept that way by
the pre-tag grep in section 3:

- `tools/` entirely (the night orchestrator, job files, prompt templates,
  ledgers, status files, STOP, dryrun): 40 plus absolute machine paths.
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
  the fleet deny rules of `.claude/settings.json`: gone on this branch.
- Run state (`.assay/`), `.DS_Store`, build output: ignored by `.gitignore`.
- `bench/oolong/packs/corpus_synth*.txt` and `questions_synth*.jsonl` (the
  length-ladder dataset text, O3): out of the tree, out of the history by the
  step in section 1.

## 3. Pre-tag greps (run on the release branch, expect nothing)

```bash
git ls-files | grep -E '^tools/|test_night_orchestrator|test_e3_prepare|registry_e2_1500|agent-journal|CONSTITUTION-ungated'
git grep -I -n -E '/Users/|/home/' -- .            # machine paths: none
git grep -n -i 'doctrine' -- src tests examples    # the old manual name: none in code
git grep -n -i -E 'factorio|oolong|\barc\b|arc_agi|arcengine' -- src/assay   # world names in the kernel: none
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
# machine paths per branch tip (release and main must be 0; exp is known to carry them)
for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do printf '%s: ' "$b"; git grep -I -n -E '/Users/|/home/' "$b" -- . | wc -l; done
# email addresses outside the upstream OOLONG corpus text
git grep -I -n -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' release/1.1.0 -- . ':!bench/oolong/packs'
# the owner's name outside copyright lines
git grep -n -i 'arjmandi' release/1.1.0 -- . ':!LICENSE' ':!NOTICE'
```

Expected: no key shapes, no secret assignments in history, zero machine paths
on `release/1.1.0` and `main`, emails only inside `bench/oolong/packs`
(the smoke packs' upstream dataset text), the owner's name only in
`pyproject.toml` (the authors field and the homepage), `verify/LICENSE` (the
MIT copyright line) and this file's own commands. The two machine-path hits
are this file's grep patterns, not paths.

## 5. Regression gates

| Gate | Command | Required result |
|---|---|---|
| G1 the suite | `uv run --with pytest --with numpy --with pillow pytest tests/` | all green (the 74 of main plus every new test) |
| G2 replay diff | `python3 paper/v4/release-gates/g2_replay_diff.py --main <main checkout> --release <release checkout> --python <venv python> --vocab` (in the archive) | zero differences over the 25 published run directories, four commands each |
| G3 independent checker | `python3 paper/v4/release-gates/g3_verify.py` (in the archive), and `python3 evidence/verify_all.py` here | 25 published ARC runs CLEAN with heads matched, the Factorio, OOLONG and G4 packs CLEAN, the 24 E1, E2 and E3 runs CLEAN, all 56 published journals matching their heads |
| G4 ft09 live regression | scheduled by the owner on the subscription, after code freeze, per DECISIONS item 18 | WIN 6 of 6 within the published bar |
| G5 clean machine | `bash paper/v4/release-gates/g5_clean_machine.sh <scratch dir>` (in the archive) | PASS |

## 6. Owner decisions

From the release review, section 7, decided on 2026-10-07 and implemented
in the second round of the release branch (the archive's release log has
one entry per commit).

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
| O9 contributions under PolyForm NC | decided: issues only in 1.1.0, stated in README.md and CONTRIBUTING.md | nothing |
| O10 the INTEGRITY line on control-arm runs | decided: a neutral `GATE` line with a count, the audit unchanged | nothing |
| O11 cut order under time pressure | not needed, the plan completed | nothing |

## 7. The tag

After G1, G2, G3 and G5 pass on the branch and G4 passes on it live, set the
date in `CHANGELOG.md`, then:

```bash
git tag -a v1.1.0 -m "ASSAY 1.1.0" release/1.1.0
```

Not before, and not by anyone but the owner.
