"""A deterministic counter world in seven flavors, picked by the world id, so
one adapter proves what the kernel does with each session declaration
(tests/test_conformance.py, the semantic conformance tests):

- `plain`: declares nothing and has no replay hook, so it gets local
  semantics: no lease, a reset on a fresh unit reaches the world, replay.
- `noop`: declares `reset_on_fresh_unit="noop"`.
- `lease`: declares an action-idle lease of four seconds and no replay.
- `single`: declares no lease and no replay.
- `hook`: declares nothing and has a `replay(transitions)` hook, which
  writes what it received to `replayed.json` under the run root.
- `env`: declares what the environment variable `DECLARATION_VARIABLE`
  holds (the record's fields as JSON), nothing when it is unset, so a test
  can change the declaration between a run's start and its resume.
- `drift`: an observer that disagrees with its actuator, so a resume
  replays every recorded step and then finds the live observation differs
  from the last event.

The observation counts the resets the world saw, so a test can tell a reset
that reached the world from one the kernel answered itself.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from assay.adapters import SessionCapability, Transition

LEASE_SECONDS = 4
DECLARATION_VARIABLE = "CAPABILITY_ADAPTER_DECLARATION"


class PlainSession:
    def __init__(self, root: Path, config: Mapping[str, Any]):
        self.root = Path(root)
        self.world = str(config.get("game_id", "plain"))
        self.counter = 0
        self.resets = 0
        self.state = "NOT_FINISHED"
        self.level = 0

    def _observe(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "levels_completed": self.level,
            "win_levels": 1,
            "available_actions": ["INC", "NOOP"],
            "data": {"counter": self.counter, "resets": self.resets},
        }

    @property
    def observation(self) -> dict[str, Any]:
        return self._observe()

    def step(
        self,
        action: str,
        data: dict[str, Any] | None,
        reasoning: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if action == "RESET":
            self.resets += 1
            self.counter = 0
            self.state = "NOT_FINISHED"
        elif action == "INC":
            self.counter += int((data or {})["amount"])
        elif action == "NOOP":
            pass
        else:
            raise ValueError(f"capability adapter got an unknown action {action!r}")
        if self.counter >= 3:
            self.state = "WIN"
            self.level = 1
        return self.observation


class DeclaringSession(PlainSession):
    """The declaration the world id names."""

    @property
    def session(self) -> SessionCapability:
        if self.world == "noop":
            return SessionCapability(reset_on_fresh_unit="noop")
        if self.world == "lease":
            return SessionCapability(idle_lease_seconds=LEASE_SECONDS, replayable=False)
        if self.world == "single":
            return SessionCapability(replayable=False)
        return SessionCapability()


class HookedSession(PlainSession):
    """Local semantics with the replay hook."""

    def replay(self, transitions: Sequence[Transition]) -> None:
        received = [
            {"action": item.action, "params": item.params, "observation": item.observation}
            for item in transitions
        ]
        (self.root / "replayed.json").write_text(json.dumps(received))


class EnvDeclaredSession(PlainSession):
    """Declares what the environment says."""

    @property
    def session(self) -> SessionCapability:
        raw = os.environ.get(DECLARATION_VARIABLE)
        return SessionCapability.from_json(json.loads(raw)) if raw else SessionCapability()


class DriftingSession(PlainSession):
    """An observer and an actuator that disagree: the observation property
    carries a mark the step's return lacks, so event 0 and the replay of
    every recorded step reproduce, and the live observation read after the
    replay differs from the last event."""

    @property
    def observation(self) -> dict[str, Any]:
        observed = self._observe()
        observed["data"]["mark"] = "observer"
        return observed

    def step(
        self,
        action: str,
        data: dict[str, Any] | None,
        reasoning: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        super().step(action, data, reasoning)
        return self._observe()


def factory(root: Path, config: Mapping[str, Any]) -> PlainSession:
    world = str(config.get("game_id", "plain"))
    if world == "hook":
        return HookedSession(root, config)
    if world == "env":
        return EnvDeclaredSession(root, config)
    if world == "drift":
        return DriftingSession(root, config)
    if world in {"noop", "lease", "single"}:
        return DeclaringSession(root, config)
    return PlainSession(root, config)
