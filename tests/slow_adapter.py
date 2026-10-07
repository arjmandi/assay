"""A deterministic dict-observation session whose steps can be made slow.

The counter world of fake_adapter.py plus one actuator, SLEEP seconds=<int>,
which blocks inside step() for that long. Lifecycle tests use it to hold the
daemon inside a step while the CLI probes, resumes or stops it. The adapter
imports nothing from the test package because the daemon loads it by file.
"""

from __future__ import annotations

import time
from typing import Any


class SlowSession:
    def __init__(self, root: Any, config: dict[str, Any]):
        self.counter = 0
        self.state = "NOT_FINISHED"
        self.level = 0
        self.slept = 0

    @property
    def observation(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "levels_completed": self.level,
            "win_levels": 1,
            "available_actions": ["INC", "NOOP", "SLEEP"],
            "data": {"counter": self.counter, "slept": self.slept},
        }

    def step(
        self,
        action: str,
        data: dict[str, Any] | None,
        reasoning: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if action == "RESET":
            self.counter = 0
            self.state = "NOT_FINISHED"
        elif action == "INC":
            self.counter += int((data or {})["amount"])
        elif action == "SLEEP":
            seconds = int((data or {})["seconds"])
            time.sleep(seconds)
            self.slept += seconds
        elif action != "NOOP":
            raise ValueError(f"slow adapter got an unknown action {action!r}")
        if self.counter >= 3:
            self.state = "WIN"
            self.level = 1
        return self.observation


def factory(root: Any, config: dict[str, Any]) -> SlowSession:
    return SlowSession(root, config)
