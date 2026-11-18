"""The meters: pure functions of the record. Nothing here is stored.

The two miss rates separate what the agent predicts about mechanics
(`world_model`) from what it predicts about progress (`gamble`). They count
held and missed outcomes only: an invalid or ungradable outcome has its own
count, a coerced outcome and a vacuous verifier's pass earn nothing.
Specificity is the share of graded outcomes that were not coerced free text.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from .delta import changed
from .grade import void
from .journal import Event, advanced, grades, is_paid
from .world import OVER

VACUOUS_MIN_GRADED = 5
DEAD_MIN_TRIES = 6  # tried this often and never productive
STALL_WINDOW = 8  # this many paid actions in a row that changed nothing


def unit_indices(events: Sequence[Event]) -> list[int]:
    """The events of the current progress unit: the tail of the journal with
    the last event's `levels_completed`."""
    indices: list[int] = []
    for index in range(len(events) - 1, -1, -1):
        if events[index]["levels_completed"] != events[-1]["levels_completed"]:
            break
        indices.append(index)
    return indices[::-1]


def unit_paid(events: Sequence[Event]) -> list[int]:
    return [index for index in unit_indices(events) if is_paid(events[index])]


def recent(events: Sequence[Event], window: int = 10) -> tuple[int, int]:
    """(held, graded) over the last `window` graded actions."""
    verdicts = [event["predict_ok"] for event in events if event.get("predict_ok") is not None][-window:]
    return sum(verdicts), len(verdicts)


def vacuous(events: Sequence[Event]) -> dict[str, int]:
    """The verifiers, by hash, whose identity verdict equalled their real
    verdict on every one of five or more gradings of a transition that
    changed something, with that count."""
    counts: dict[str, list[int]] = {}
    for event in events:
        for item in grades(event):
            if item.get("verifier") and not void(item) and "identity_verdict" in item:
                slot = counts.setdefault(item["verifier_hash"], [0, 0])
                slot[0] += 1
                slot[1] += item.get("identity_verdict") is item["ok"]
    return {digest: graded for digest, (graded, same) in counts.items() if graded >= VACUOUS_MIN_GRADED and same == graded}


@dataclasses.dataclass(frozen=True)
class Predictions:
    """The counts behind the PREDICTIONS line."""

    world_model: tuple[int, int]  # (graded, missed)
    gamble: tuple[int, int]
    graded: int  # every outcome graded, the void and the coerced included
    coerced: int
    void: int

    @property
    def specificity(self) -> float:
        return (self.graded - self.coerced) / self.graded if self.graded else 1.0


def predictions(events: Sequence[Event]) -> Predictions:
    hollow = vacuous(events)
    counts = {"world_model": [0, 0], "gamble": [0, 0]}
    graded = coerced = invalid = 0
    for event in events:
        for item in grades(event):
            graded += 1
            if void(item):
                invalid += 1
            elif item["kind"] == "coerced":
                coerced += 1
            elif not (item.get("verifier") and item["ok"] and item["verifier_hash"] in hollow):
                slot = counts[item["bucket"]]
                slot[0] += 1
                slot[1] += not item["ok"]
    world, gamble = counts["world_model"], counts["gamble"]
    return Predictions((world[0], world[1]), (gamble[0], gamble[1]), graded, coerced, invalid)


def event_changed(events: Sequence[Event], index: int) -> bool:
    """Whether paid event `index` changed the world: progress, another
    lifecycle state, or any difference from the observation before it."""
    if not index:
        return False
    if advanced(events[index]) or events[index]["state"] != events[index - 1]["state"]:
        return True
    count, _ = changed(events[index - 1], events[index])
    return count is None or count > 0


@dataclasses.dataclass(frozen=True)
class Coverage:
    """What the current progress unit has exercised: the advertised actions
    never tried on it, the ones tried six times or more that never changed
    the world, and whether the last eight paid actions changed nothing."""

    unit: int
    paid: int
    untried: list[str]
    dead: list[str]
    stalled: bool


def coverage(events: Sequence[Event]) -> Coverage:
    paid = unit_paid(events)
    plays: dict[str, list[int]] = {}
    for index in paid:
        slot = plays.setdefault(events[index]["action"], [0, 0])
        slot[0] += 1
        slot[1] += event_changed(events, index)
    advertised = sorted(str(name) for name in events[-1]["available_actions"] if name != "RESET")
    tail = paid[-STALL_WINDOW:]
    return Coverage(
        unit=events[-1]["levels_completed"],
        paid=len(paid),
        untried=[name for name in advertised if name not in plays],
        dead=[name for name in advertised if name in plays and plays[name][0] >= DEAD_MIN_TRIES and not plays[name][1]],
        stalled=len(tail) == STALL_WINDOW
        and events[-1]["state"] not in OVER
        and not any(event_changed(events, index) for index in tail),
    )


def hazards(events: Sequence[Event]) -> dict[str, int]:
    """The action names whose use entered GAME_OVER or lost a completed
    unit, each with the first event that showed it."""
    found: dict[str, int] = {}
    for previous, event in zip(events, events[1:]):
        entered = event["state"] == "GAME_OVER" and previous["state"] != "GAME_OVER"
        lost = event["levels_completed"] < previous["levels_completed"]
        if event["action"] != "RESET" and (entered or lost):
            found.setdefault(event["action"], event["id"])
    return found
