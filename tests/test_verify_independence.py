"""The independent checker shares no code with the harness, and every
published journal verifies against its committed head.

`verify/assay_verify.py` is reimplemented from the spec: it imports nothing
outside the standard library, it runs with the harness unimportable, and the
two implementations agreeing on a journal is part of the point (the gate
tests compare their verdicts). The evidence packs under `evidence/` are the
journals the harness reports, each with a published head."""

from __future__ import annotations

import ast
import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "verify" / "assay_verify.py"
EVIDENCE = REPO / "evidence"


def test_the_checker_imports_only_the_standard_library():
    tree = ast.parse(CHECKER.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "the checker has no relative imports"
            imported.add((node.module or "").split(".")[0])
    assert imported, "the checker imports something"
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names
    assert not any(name.startswith("assay") for name in imported)


def test_the_checker_runs_with_the_harness_unimportable(tmp_path):
    """-I -S isolates the interpreter: no PYTHONPATH, no site-packages (so an
    editable install of the harness in the project's own environment is not
    visible either), no script directory on sys.path, so `import assay` fails,
    and the stdlib-only checker still verifies a published journal against its
    head."""
    pack = EVIDENCE / "factorio"
    head = json.loads((pack / "heads.json").read_text())["runs"][0]["chain_head"]
    with gzip.open(pack / "journal-ironplate.jsonl.gz", "rb") as source, (tmp_path / "journal.jsonl").open("wb") as sink:
        shutil.copyfileobj(source, sink)
    probe = subprocess.run(
        [sys.executable, "-I", "-S", "-c", "import assay"], capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert probe.returncode != 0, "the harness must be unimportable in this probe"
    checked = subprocess.run(
        [sys.executable, "-I", "-S", str(CHECKER), str(tmp_path / "journal.jsonl"), "--json", "--expect-head", head],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=120,
    )
    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    assert report["verdict"] == "CLEAN" and report["expect_head"] == "match"


def test_every_published_journal_verifies_against_its_head():
    completed = subprocess.run(
        [sys.executable, str(EVIDENCE / "verify_all.py")], capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "| 66 journals, 0 failed" in completed.stdout
