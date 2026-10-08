"""A tiny deterministic non-grid session for end-to-end tests.

World state: a counter and a lamp. Registered actions (see the registry JSON
in the tests): INC amount=<int 1..2>, SET_LAMP state=<on|off>, NOOP, and,
where a test registers it, APPLY ops=<array of object> (each op an `inc` with
its amount or a `lamp` with its state, applied in order: the structured
action of #14). The game is won when the counter reaches 3. Matches the
adapter seam used by the broker: observation property, step(action, data,
reasoning), optional finalize().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _registers_apply(root: Any) -> bool:
    """Whether the run's pinned registry registers APPLY: the world advertises
    it only then, so the tests that do not register it see the four actions
    they always saw."""
    try:
        registry = json.loads((Path(root) / ".assay" / "registry.json").read_text())
    except (OSError, ValueError):
        return False
    return any(item.get("name") == "APPLY" for item in registry.get("actions", ()))


class FakeSession:
    def __init__(self, root: Any, config: dict[str, Any]):
        self.counter = 0
        self.lamp = "off"
        self.state = "NOT_FINISHED"
        self.level = 0
        self.actions = ["INC", "SET_LAMP", "NOOP", "BOMB"]
        if _registers_apply(root):
            self.actions.insert(3, "APPLY")

    @property
    def observation(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "levels_completed": self.level,
            "win_levels": 1,
            "available_actions": list(self.actions),
            "data": {"counter": self.counter, "lamp": self.lamp},
        }

    def step(
        self,
        action: str,
        data: dict[str, Any] | None,
        reasoning: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if action == "RESET":
            self.counter = 0
            self.lamp = "off"
            self.state = "NOT_FINISHED"
        elif action == "INC":
            self.counter += int((data or {})["amount"])
        elif action == "SET_LAMP":
            self.lamp = str((data or {})["state"])
        elif action == "NOOP":
            pass
        elif action == "APPLY":
            for op in (data or {})["ops"]:
                if op["kind"] == "inc":
                    self.counter += int(op.get("amount", 1))
                elif op["kind"] == "lamp":
                    self.lamp = str(op.get("state", "on"))
        elif action == "BOMB":
            self.state = "GAME_OVER"
        else:
            raise ValueError(f"fake adapter got an unknown action {action!r}")
        if self.state != "GAME_OVER" and self.counter >= 3:
            self.state = "WIN"
            self.level = 1
        return self.observation


def factory(root: Any, config: dict[str, Any]) -> FakeSession:
    return FakeSession(root, config)
