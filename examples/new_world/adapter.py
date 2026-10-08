"""The new-world template: copy this file and registry.json to start a world.

The world is a vault with two rooms. Each room has a door with a dial; the
door opens when the dial sits on the room's code, and entering the open door
completes that room (one progress unit). Entering the second room is the
goal. The code is derived from the run's seed, so the world is deterministic
and a resumed run replays to the same state, which is what local replay
needs. Nothing here is specific to ASSAY beyond the session contract below.

The contract (see docs/ARCHITECTURE.md section 2.2):

    factory(root, config) -> session         named on the command line as adapter.py:factory
    session.observation                      the dict shape: state, levels_completed,
                                             win_levels, available_actions, data
    session.step(action, data, reasoning)    apply one validated action, return the observation
    session.finalize()                       optional, called once when state becomes WIN
    session.public_info                      optional, stored in config.json at start

Three things worth copying from this file:

- a refusal is reported THROUGH THE OBSERVATION (`data.last_result.status`
  is "refused", `data.refusals` counts), never by raising, so the spend is
  journaled as evidence. Raising from step() aborts the action before the
  mutation is written: nothing is spent and the error reaches the CLI.
- `reasoning` is the agent's journaled prediction context. The world may
  log it, never obey it.
- `finalize` writes its own file under `.assay/`; the kernel never reads it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOMS = 2
DIAL_POSITIONS = 10


def room_code(seed: int, room: int) -> int:
    """The dial position that opens the door of `room` under this seed."""
    return (seed * 7 + room * 3 + 1) % DIAL_POSITIONS


class VaultWorld:
    def __init__(self, root: Any, config: dict[str, Any]):
        self.root = Path(root)
        self.seed = int(config.get("seed", 0))
        self.room = 1
        self.dial = 0
        self.door = "locked"
        self.peeked = False
        self.state = "NOT_FINISHED"
        self.level = 0
        self.refusals = 0
        self.steps = 0
        self.last_result: dict[str, Any] = {"action": "START", "status": "ready"}
        self._closed = False

    # -- the observer --------------------------------------------------------

    @property
    def observation(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "levels_completed": self.level,
            "win_levels": ROOMS,
            "available_actions": ["TURN", "DIAL", "OPEN", "ENTER", "PEEK", "ALARM", "DRILL", "SIREN"],
            "data": {
                "room": self.room,
                "dial": self.dial,
                "door": self.door,
                "hint": ("even" if room_code(self.seed, self.room) % 2 == 0 else "odd") if self.peeked else None,
                "refusals": self.refusals,
                "last_result": self.last_result,
            },
        }

    @property
    def public_info(self) -> dict[str, Any]:
        return {"world": "new_world template (a vault with two rooms)", "rooms": ROOMS}

    # -- the actuator --------------------------------------------------------

    def step(self, action: str, data: dict[str, Any] | None, reasoning: dict[str, Any] | None) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("the vault is closed")
        payload = dict(data or {})
        self.steps += 1
        if action == "RESET":
            self.dial, self.door, self.peeked = 0, "locked", False
            self.state = "NOT_FINISHED"
            self.last_result = {"action": action, "status": "ok", "detail": "room rewound"}
        elif action == "TURN":
            self.dial = (self.dial + int(payload["delta"])) % DIAL_POSITIONS
            self.last_result = {"action": action, "status": "ok"}
        elif action == "DIAL":
            # The structured parameter: an array the registry validated item
            # by item, applied in order within one paid action.
            for delta in payload["turns"]:
                self.dial = (self.dial + int(delta)) % DIAL_POSITIONS
            self.last_result = {"action": action, "status": "ok", "turns": len(payload["turns"])}
        elif action == "OPEN":
            if self.dial == room_code(self.seed, self.room):
                self.door = "open"
                self.last_result = {"action": action, "status": "ok", "detail": "the door swings open"}
            else:
                self._refuse(action, "the dial is not on the code; the door stays locked")
        elif action == "ENTER":
            if self.door == "open":
                self._complete_room()
            else:
                self._refuse(action, "the door is locked")
        elif action == "PEEK":
            self.peeked = True
            self.last_result = {"action": action, "status": "ok", "what": payload.get("what")}
        elif action == "ALARM":
            self.state = "GAME_OVER"
            self.last_result = {"action": action, "status": "ok", "detail": "the alarm ends this room"}
        elif action == "DRILL":
            self.door = "open"
            self.last_result = {"action": action, "status": "ok", "detail": "drilled through"}
        elif action == "SIREN":
            self.last_result = {"action": action, "status": "ok", "volume": payload.get("volume")}
        else:
            raise ValueError(f"unknown action {action!r}")
        return self.observation

    def _refuse(self, action: str, why: str) -> None:
        self.refusals += 1
        self.last_result = {"action": action, "status": "refused", "detail": why}

    def _complete_room(self) -> None:
        self.level += 1
        self.last_result = {"action": "ENTER", "status": "ok", "detail": f"room {self.room} done"}
        if self.level >= ROOMS:
            self.state = "WIN"
            return
        self.room += 1
        self.dial, self.door, self.peeked = 0, "locked", False

    # -- the end -------------------------------------------------------------

    def finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        state_dir = self.root / ".assay"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "new_world_summary.json").write_text(
            json.dumps({"steps": self.steps, "refusals": self.refusals, "rooms": self.level}, indent=1) + "\n"
        )


def factory(root: Path, config: dict[str, Any]) -> VaultWorld:
    return VaultWorld(root, config)
