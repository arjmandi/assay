# The E1 and E1b designs as job files

`e1.json` and `e1b.json` are the pre-registered designs of October 2026
(`runs/E1_E2_PROTOCOL.md` and `runs/E1B_PROTOCOL.md` in the paper's archive)
written for the evaluation runner, `tools/eval/` (its README is the manual).
They document the designs and are the inputs of the runner's dry run:

```bash
python3 -m tools.eval --jobs bench/arcagi/jobs/e1.json --dry-run
python3 -m tools.eval --jobs bench/arcagi/jobs/e1b.json --dry-run
```

The dry run prints the twelve (six) jobs with every command each would run
and writes nothing. The test suite runs it; nothing in the suite starts a
live session.

Every path in a job file is relative to this directory. The registries are
the canonical `bench/arcagi/registry_200.json` and `registry_1500.json`; the
control arms lay one key over them (`gate: optional` for E1's ungated arm,
`gate: off` for E1b) and the runner writes the variant under its state
directory before the start, so no variant registry is kept in the tree.

Two inputs are not in the tree, by owner decision O4 (`RELEASE_CHECKLIST.md`
section 2): the manuals the control arms read. The dry run reports them as
`MISSING` and a live run refuses to launch the arm until they exist under
`manuals/` here (the directory is ignored by git except for its placeholder):

- `manuals/CONSTITUTION-ungated.md`, the strong ungated manual of E1 (sha256
  `61a5f21a6a619b40e32f63cbcec3b89262acd93a8e9ed9d049e5069c1e767e01`), kept on
  the experiment tag:
  `git show exp-2026-10:bench/arcagi/CONSTITUTION-ungated.md > bench/arcagi/jobs/manuals/CONSTITUTION-ungated.md`
- `manuals/CONSTITUTION-e1b-off.md`, the E1b manual (sha256
  `2ef4b38321503f6b81e95551cd0dc84bd26c27a52fa74ea6b6af6daac17aef99`): the
  file above minus the one sentence about `--predict` and the sentence about
  the CLAIMS line, as `E1B_PROTOCOL.md` section 5 records; the archive holds
  it beside the E1b runs.

A live run needs what `bench/arcagi/PROTOCOL.md` needs: an interpreter with
the `arc-agi` client (`ASSAY_PYTHON` exported in the shell that runs the
runner; the runner and every player inherit it), the three games in the
local cache, and `claude` on PATH. The runs that were played are reported in
the archive's results files and verified in `evidence/e1` and `evidence/e1b`;
rerunning the designs on the 1.2.0 kernel is a new experiment, with the
shipped manual for the gated arm (the E1 runs read the manual of their day).
