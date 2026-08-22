"""A complete, minimal ASSAY world in ~40 lines — the integrator's example.

The world: a counter and a lamp. INC adds 1 or 2 to the counter, SET_LAMP
switches the lamp, NOOP does nothing, BOMB loses the level. The goal state is
counter >= 3. Pair it with example_registry.json and run:

    assay start counterdemo \
        --adapter examples/counter_world.py:factory \
        --registry examples/example_registry.json

This file is the whole "observer + environment" side of an integration: a
factory returning a session object with `.observation`, `.step()`, and
optionally `.finalize()`. ASSAY never sees your world any other way.
"""

from __future__ import annotations

from typing import Any


class CounterWorld:
    def __init__(self, root: Any, config: dict[str, Any]):
        # root: the run directory (Path). config: the run's config.json dict
        # (game_id, seed, ...). Use config["seed"] for determinism if you
        # randomize — local replay-resume needs identical reconstruction.
        self.counter = 0
        self.lamp = "off"
        self.state = "NOT_FINISHED"   # NOT_FINISHED | WIN | GAME_OVER
        self.level = 0                # completed milestones so far

    @property
    def observation(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "levels_completed": self.level,
            "win_levels": 1,                       # total milestones
            "available_actions": ["INC", "SET_LAMP", "NOOP", "BOMB"],
            "data": {"counter": self.counter, "lamp": self.lamp},
        }

    def step(self, action, data, reasoning):
        # data is the typed parameter dict the registry validated (or None).
        # reasoning is the agent's journaled prediction context — yours to log
        # or ignore, never to obey.
        if action == "RESET":
            self.counter, self.lamp, self.state = 0, "off", "NOT_FINISHED"
        elif action == "INC":
            self.counter += int(data["amount"])
        elif action == "SET_LAMP":
            self.lamp = str(data["state"])
        elif action == "BOMB":
            self.state = "GAME_OVER"
        elif action != "NOOP":
            raise ValueError(f"unknown action {action!r}")
        if self.state != "GAME_OVER" and self.counter >= 3:
            self.state, self.level = "WIN", 1
        return self.observation


def factory(root: Any, config: dict[str, Any]) -> CounterWorld:
    return CounterWorld(root, config)
