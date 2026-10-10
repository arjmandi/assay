"""The kernel's prose says world and progress unit, never game, board or level,
except for the identifiers the public contract freezes and the one module that
owns the display words. It says prediction and outcome, never claim, except
for the spellings frozen on disk. An AST walk over every string literal in
src/assay (and, for the claim rule, src/assay_grid) enforces both, so the
vocabulary cannot regress by accident."""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "assay"
GRID = SRC.parent / "assay_grid"

# Whole words that must not appear in kernel prose.
FORBIDDEN = re.compile(r"\b(game|games|board|boards|level|levels)\b", re.IGNORECASE)

# Tokens that may contain a forbidden word: the frozen identifiers and the
# syntax the public contract fixes (see src/assay/words.py).
ALLOWED_TOKENS = (
    "game_id", "GAME_OVER", "GAME_COMPLETE", "LEVEL_COMPLETE", "source_game",
    "levels_completed", "win_levels", "level_before", "level_up", "level+1",
    "level advanced", "level did not advance", ".assay/levels", "levels/", "level-",
    "ch level", "`level`", '"level"', "level n/m", "level 3/6", "levels)",
)
# A literal that is exactly an identifier (a dict key, the host state name,
# the state directory's `levels` folder) is not prose.
IDENTIFIER_LITERALS = {"level", "levels"}
# Lines of prose that may say the word because they are about the mapping.
ALLOWED_LINES = (
    "unless the world really is a game",
    "games instantiate",
    "first world was a suite of games",
    "which is what the published game runs show",
    "rewinding the world",
)

# The word the harness stopped using in 1.3.0: a prediction is what the agent
# writes before an action, an outcome is one checkable part of it, and the
# code labels each outcome held or missed. Nothing a person reads says claim.
CLAIM = re.compile(r"\b(claim|claims|claimed|claiming)\b", re.IGNORECASE)
# The spellings frozen on disk and on the wire that still carry the word:
# the receipt result token (also the grade `actual` prefix of a verifier
# that did not grade), and the `claims` key of the mutation record, named in
# prose where the record is described.
CLAIM_ALLOWED_TOKENS = ("INVALID_CLAIM", "`claims`")
# The on-disk keys written as bare literals: the mutation record's `claims`
# and the `claim` field of the mis_reference activity record.
CLAIM_IDENTIFIER_LITERALS = {"claims", "claim"}


def _literals(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def _offending(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        if any(token in line for token in ALLOWED_LINES):
            continue
        scrubbed = line
        for token in ALLOWED_TOKENS:
            scrubbed = scrubbed.replace(token, "")
        if FORBIDDEN.search(scrubbed):
            hits.append(line.strip())
    return hits


def _claim_offending(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        scrubbed = line
        for token in CLAIM_ALLOWED_TOKENS:
            scrubbed = scrubbed.replace(token, "")
        if CLAIM.search(scrubbed):
            hits.append(line.strip())
    return hits


def test_kernel_prose_uses_world_and_progress_unit():
    offenders: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "words.py":
            continue  # the allowlist and the display words themselves
        for lineno, text in _literals(path):
            if text in IDENTIFIER_LITERALS:
                continue
            # Regular expressions carry no prose.
            if path.name == "predictions.py" and (text.startswith("^") or "\\b" in text or "(?" in text):
                continue
            for hit in _offending(text):
                offenders.append(f"{path.name}:{lineno}: {hit}")
    assert not offenders, "\n".join(offenders)


def test_kernel_prose_says_prediction_and_outcome_never_claim():
    offenders: list[str] = []
    for path in sorted([*SRC.glob("*.py"), *GRID.glob("*.py")]):
        for lineno, text in _literals(path):
            if text in CLAIM_IDENTIFIER_LITERALS:
                continue
            for hit in _claim_offending(text):
                offenders.append(f"{path.parent.name}/{path.name}:{lineno}: {hit}")
    assert not offenders, "\n".join(offenders)


def test_progress_words():
    from conftest import event_of

    from assay.words import progress_text, unit_line_label, unit_noun

    single = event_of(win_levels=1, levels_completed=0)
    several = event_of(win_levels=6, levels_completed=2)
    assert progress_text(single) == "progress 1/1"
    assert progress_text(several) == "level 3/6"
    assert progress_text(event_of(win_levels=6, levels_completed=6)) == "level 6/6"
    assert unit_noun(1) == "unit" and unit_noun(4) == "level"
    assert unit_line_label(1) == "PROGRESS" and unit_line_label(25) == "LEVEL"
