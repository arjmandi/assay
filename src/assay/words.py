"""The display vocabulary of the kernel, and what it must never rename.

ASSAY's first world was a suite of games, and its journal format carries that
history in a few field names. The kernel's prose does not: it says world, not
game, and progress unit, not level, unless the world really is a game with
levels. This module is the one place the prose words live, and its docstring
is the allowlist the vocabulary test enforces.

Frozen, because the public journal contract (`assay-journal-v1`,
verify/JOURNAL_SPEC.md) and the 66 published journals under evidence/
(verified by evidence/verify_all.py) fix them:

- the journal fields `levels_completed`, `win_levels`, `level_before`,
  `state`, `frames`, `n_frames`, `observation`,
- the state values `NOT_FINISHED`, `WIN`, `GAME_OVER`,
- the claim syntax `level+1` and `win`, and the grade `actual` texts
  ("level advanced", "level did not advance", "state WIN"), which are graded
  facts inside journals,
- the host state names `goal` and `level`,
- the config key `game_id`, the knowledge-file key `game_id` and the summary
  key `source_game`, and every activity record kind,
- the receipt outcome tokens `PREDICTED`, `SURPRISE`, `INVALID_CLAIM`,
  `UNGATED`, `LEVEL_COMPLETE`, `GAME_COMPLETE`, `GAME_OVER`, `RESET`,
- the state-directory layout, including `.assay/levels/level-N.md`,
- the compact history prefix `L<n>` in RECENT lines.

Changed, display only: "game" became "world" in prose, "board" became
"state", "level" became "progress unit" where the world is not a game. The
compromise for progress: a world whose `win_levels` is 1 shows `progress 1/1`
and "this unit"; a world with several units shows `level n/m` and "this
level", which is what the published game runs show and what the replay diff
over them keeps byte-identical.
"""

from __future__ import annotations

from .records import Event

WORLD = "world"


def unit_noun(win_levels: int) -> str:
    """What one progress unit is called in prose for this world."""
    return "level" if int(win_levels) > 1 else "unit"


def progress_label(win_levels: int) -> str:
    """The word before `n/m` in headers."""
    return "level" if int(win_levels) > 1 else "progress"


def progress_text(event: Event) -> str:
    """`level 3/6` on a game, `progress 1/1` on a single-unit world."""
    total = int(event.win_levels)
    current = min(total, int(event.levels_completed) + 1)
    return f"{progress_label(total)} {current}/{total}"


def unit_line_label(win_levels: int) -> str:
    """The status line that counts paid actions on the current unit."""
    return "LEVEL" if int(win_levels) > 1 else "PROGRESS"
