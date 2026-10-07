"""The legacy numbered-action path: runs started without a registry on a frame
world, which speak ACTION1..ACTION7 and ACTION6:x,y with a 0..63 bound.

Nothing in the published evidence depends on it (all 25 ARC-AGI-3 runs were
registry runs). It is kept, undocumented, for the run directories that used it
before registries existed, and the status advice of that path lives here
with it.

TODO(owner: O2): delete this module in 1.2, or keep it. The review's
recommendation is move now (done), delete later.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from assay.core import AssayError

NUMBERED_MIN = 1
NUMBERED_MAX = 7
COORDINATE_ACTION = "ACTION6"
COORDINATE_MAX = 63


def parse_action(token: str) -> tuple[str, dict[str, Any] | None]:
    name, separator, suffix = token.strip().upper().partition(":")
    if name == "RESET":
        if separator:
            raise AssayError("RESET takes no coordinates")
        return name, None
    if (
        not name.startswith("ACTION")
        or not name[6:].isdigit()
        or not NUMBERED_MIN <= int(name[6:]) <= NUMBERED_MAX
    ):
        raise AssayError(
            f"invalid action {token!r}; use ACTION1..ACTION7 or ACTION6:x,y"
        )
    if name == COORDINATE_ACTION:
        if not separator:
            raise AssayError("ACTION6 requires coordinates: ACTION6:x,y")
        try:
            x_text, y_text = suffix.split(",", 1)
            x, y = int(x_text), int(y_text)
        except (ValueError, TypeError):
            raise AssayError(f"invalid coordinate action {token!r}") from None
        if not (0 <= x <= COORDINATE_MAX and 0 <= y <= COORDINATE_MAX):
            raise AssayError(f"ACTION6 coordinates must be in 0..{COORDINATE_MAX}")
        return name, {"x": x, "y": y}
    if separator:
        raise AssayError(f"only ACTION6 accepts coordinates: {token!r}")
    return name, None


def action_number(token: str) -> int:
    name, _ = parse_action(token)
    if name == "RESET":
        return 0
    return int(name[6:])


def check_public_action(token: str, available: Sequence[Any]) -> None:
    number = action_number(token)
    if number not in {int(value) for value in available}:
        raise AssayError(
            f"{token} is unavailable; public actions are {list(available)}"
        )


def action_token(action: str, coordinates: Sequence[str]) -> str:
    """`assay act ACTION6 X Y` on the command line becomes ACTION6:X,Y."""
    token = action.upper()
    if coordinates:
        if token != COORDINATE_ACTION or len(coordinates) != 2:
            raise AssayError(
                "coordinates are only accepted as `assay act ACTION6 X Y`"
            )
        try:
            token = f"{COORDINATE_ACTION}:{int(coordinates[0])},{int(coordinates[1])}"
        except ValueError:
            raise AssayError(
                "ACTION6 coordinates must be integers: `assay act ACTION6 X Y`"
            ) from None
    return token


def advertised_names(event: Mapping[str, Any]) -> list[str]:
    """A frame world advertises bare action ids; registered names carry the
    ACTION prefix. The affordance check compares names, so render the ids."""
    return [f"ACTION{int(value)}" for value in event.get("available_actions") or ()]


def available_line(event: Mapping[str, Any]) -> str:
    # Bare action numbers: semantics are earned by acting, never assumed.
    return "ACTIONS | available: " + (
        " · ".join(str(number) for number in event["available_actions"]) or "none"
    ) + " · RESET (built-in)"


def _level_action_count(events: Sequence[Mapping[str, Any]]) -> int:
    completed = int(events[-1]["levels_completed"])
    count = 0
    for event in reversed(events):
        if int(event["levels_completed"]) != completed:
            break
        if event.get("counts_action"):
            count += 1
    return count


def _recent_predictions(
    events: Sequence[Mapping[str, Any]], window: int = 10
) -> tuple[int, int]:
    graded = [event for event in events if event.get("predict_ok") is not None]
    recent = graded[-window:]
    hits = sum(1 for event in recent if event["predict_ok"])
    return hits, len(recent)


def wall_spend_advice(level_actions: int) -> str | None:
    if level_actions >= 40:
        return (
            f"{level_actions} paid actions on this level — stop manual probing; "
            "write rules.py for it (`assay rules help`), verify with `assay rules replay`, "
            "then `assay rules solve`"
        )
    if level_actions >= 25:
        return (
            f"{level_actions} paid actions on this level — re-read your notes, "
            "kill dead assumptions, and consider the rules.py tier (`assay rules help`)"
        )
    return None


def nudges(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """Status-time advice on a run without a registry (no modules there)."""
    lines: list[str] = []
    advice = wall_spend_advice(_level_action_count(events))
    if advice:
        lines.append(f"NUDGE | {advice}")
    hits, total = _recent_predictions(events)
    misses = total - hits
    if misses >= 3:
        lines.append(
            f"NUDGE | {misses} of the last {total} predictions missed — the mechanics "
            "story in NOTES.md is wrong; fix it before spending more actions"
        )
    return lines
