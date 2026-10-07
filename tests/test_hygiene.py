"""The writing rule and the pre-tag greps as a test, over `git ls-files`, so a
new file is covered the day it is added: no em dash and no arrow in the source
or the docs, no machine path in any tracked text file, and the old manual name
gone from the code. The world-name rule for the kernel lives in
tests/test_conformance.py and is not repeated here."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The characters the writing rule forbids: the em dash and the arrow, spelled
# by code point so this file passes its own test.
TYPOGRAPHY = re.compile("[" + chr(0x2014) + chr(0x2192) + "]")
TYPOGRAPHY_SUFFIXES = {".py", ".md", ".toml", ".yml", ".txt", ".json"}
# The patterns of the release checklist, assembled so the greps that look for
# them in the tree do not find this file.
MACHINE_PATH = re.compile("/" + "Users/|/" + "home/")
OLD_MANUAL_NAME = re.compile("doc" + "trine", re.IGNORECASE)
CODE_ROOTS = ("src/", "tests/", "examples/")
# Historical results and recorded run data are never edited; the evidence
# packs' READMEs and HEADS files are prose and stay under the rule.
RESULTS = re.compile(r"bench/[^/]+/RESULTS\.md")


def _tracked() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [path for path in completed.stdout.split("\0") if path]


def _text(path: str) -> str | None:
    """The file's text, or None for a binary file (git's own heuristic: a NUL
    byte in the first 8000 bytes)."""
    data = (REPO / path).read_bytes()
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", errors="replace")


def _under_typography_rule(path: str) -> bool:
    if Path(path).suffix not in TYPOGRAPHY_SUFFIXES:
        return False
    if RESULTS.fullmatch(path):
        return False
    if path.startswith("evidence/") and Path(path).name not in ("README.md", "HEADS.md"):
        return False
    return True


def _hits(path: str, pattern: re.Pattern[str]) -> list[str]:
    text = _text(path)
    if text is None:
        return []
    return [
        f"{path}:{number}: {line.strip()[:120]}"
        for number, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]


def test_no_em_dash_or_arrow_in_the_source_or_the_docs():
    offenders = [
        hit
        for path in _tracked()
        if _under_typography_rule(path)
        for hit in _hits(path, TYPOGRAPHY)
    ]
    assert not offenders, "\n".join(offenders)


def test_no_machine_path_in_any_tracked_text_file():
    offenders = [
        hit
        for path in _tracked()
        if path != "RELEASE_CHECKLIST.md"  # its grep patterns name the shapes
        for hit in _hits(path, MACHINE_PATH)
    ]
    assert not offenders, "\n".join(offenders)


def test_the_old_manual_name_is_gone_from_the_code():
    offenders = [
        hit
        for path in _tracked()
        if path.startswith(CODE_ROOTS)
        for hit in _hits(path, OLD_MANUAL_NAME)
    ]
    assert not offenders, "\n".join(offenders)
