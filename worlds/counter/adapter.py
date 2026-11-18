"""A complete world in one file: a counter and a lamp.

INC adds 1 or 2 to the counter, SET_LAMP switches the lamp, NOOP does
nothing, BOMB loses. The goal is counter >= 3. This is the whole of an
adapter: a factory that returns a session with `observation` and `step`.

    assay start demo --adapter worlds/counter/adapter.py --registry worlds/counter/registry.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class CounterWorld:
    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        # root is the run directory; config holds world_id and seed. A world
        # that randomizes must do it from the seed, so a resume can replay it.
        self.counter = 0
        self.lamp = "off"
        self.state = "NOT_FINISHED"

    @property
    def observation(self) -> dict[str, Any]:
        return {
            "state": self.state,  # NOT_FINISHED, WIN or GAME_OVER
            "levels_completed": int(self.state == "WIN"),
            "win_levels": 1,  # progress units in total
            "available_actions": ["INC", "SET_LAMP", "NOOP", "BOMB"],
            "data": {"counter": self.counter, "lamp": self.lamp},
        }

    def step(self, action: str, params: dict[str, Any] | None) -> dict[str, Any]:
        # params is the object the registry validated, or None.
        params = params or {}
        if action == "RESET":
            self.counter, self.lamp, self.state = 0, "off", "NOT_FINISHED"
        elif action == "INC":
            self.counter += params["amount"]
        elif action == "SET_LAMP":
            self.lamp = params["state"]
        elif action == "BOMB":
            self.state = "GAME_OVER"
        if self.state != "GAME_OVER" and self.counter >= 3:
            self.state = "WIN"
        return self.observation


def factory(root: Path, config: dict[str, Any]) -> CounterWorld:
    return CounterWorld(root, config)
