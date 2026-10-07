"""The kernel's prose says world and progress unit, never game, board or level,
except for the identifiers the public contract freezes and the one module that
owns the display words. An AST walk over every string literal in src/assay
enforces it, so the vocabulary cannot regress by accident."""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "assay"

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
# A literal that is exactly an identifier (a dict key, the host channel name,
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
