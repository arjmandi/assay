# Contributing

In 1.2.0 this repository takes issues only, not pull requests. ASSAY is
open source under the Apache License 2.0 (`LICENSE`), and it is not an
open-contribution project yet.

Report what you find as an issue: a journal that audits wrong, a world the
harness refuses, a document that misleads. Include the output of `assay
version` and `assay doctor`, and for anything about a run, its
`.assay/audit.json` and the verdict of `verify/assay_verify.py` on the run
directory.

If you work on the source anyway, for a fork or for what comes after 1.2.0,
`.pre-commit-config.yaml` runs the checks CI runs (`ruff check src tests`,
`mypy --strict src/assay`, and a grep for the em dash and the arrow over the
staged files) before each commit for whoever installs it with
`uv run --with pre-commit pre-commit install`; it is optional, CI is the gate.
