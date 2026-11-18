"""The ARC-AGI-3 world: one public game, played through the `arc_agi` client.

    assay start ft09 --adapter worlds/arc-agi-3/adapter.py --registry worlds/arc-agi-3/registry.json

The world id is the game id. A local run plays a cached copy of the game,
downloaded once with the ARC API key, and replays from its journal. The key
is read from the environment (ARC_API_KEY) or from a key file
(`~/assay/tokens/arc_api_key`, or the file ASSAY_ARC_KEY_FILE names), inside
the daemon, so it reaches neither the agent nor the journal. With
ASSAY_ARC_MODE=remote the run talks to the competition server instead: every
action goes to the server, and the run lives as long as its daemon.

The interpreter that runs the daemon needs `requirements.txt` (ASSAY_PYTHON
pins it).
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _api_key() -> str:
    key = os.getenv("ARC_API_KEY", "")
    if not key:
        file = Path(os.getenv("ASSAY_ARC_KEY_FILE") or "~/assay/tokens/arc_api_key").expanduser()
        key = file.read_text().strip() if file.is_file() else ""
    return key


def _cache() -> Path:
    """Where downloaded games are kept, across runs."""
    base = os.getenv("ASSAY_CACHE_DIR") or os.path.join(os.getenv("XDG_CACHE_HOME") or "~/.cache", "assay", "arcade")
    return Path(base).expanduser().resolve()


class ArcSession:
    def __init__(self, root: Path, config: Mapping[str, Any]) -> None:
        import arc_agi
        from arc_agi import OperationMode

        game, key = str(config["world_id"]), _api_key()
        self.remote = os.getenv("ASSAY_ARC_MODE", "local").lower() == "remote"
        self.replayable = not self.remote  # a server session cannot be rebuilt
        if self.remote:
            runtime = Path(tempfile.gettempdir()) / f"assay-{os.getuid()}" / "remote"
            mode = OperationMode.COMPETITION
        else:
            runtime = _cache()
            cached = any((runtime / "environment_files" / game.lower()).glob("*/metadata.json"))
            mode = OperationMode.OFFLINE if cached else OperationMode.NORMAL
        (runtime / "environment_files").mkdir(parents=True, exist_ok=True)
        (root / ".assay" / "recordings").mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("assay.arc")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.arcade = arc_agi.Arcade(
            arc_api_key=key,
            operation_mode=mode,
            environments_dir=str(runtime / "environment_files"),
            recordings_dir=str(root / ".assay" / "recordings"),
            logger=logger,
        )
        self.environment = self.arcade.make(game, seed=int(config.get("seed", 0)))
        if self.environment is None:
            raise RuntimeError(
                f"could not open {game}: "
                + ("the competition server needs ARC_API_KEY and the network" if self.remote
                   else "it is not in the local cache, and downloading it needs ARC_API_KEY once" if not key
                   else "the download or the cached game failed")
            )
        self.fresh = True  # no action yet on the current level
        self.closed = False

    def _observe(self, observed: Any) -> dict[str, Any]:
        if observed is None:
            raise RuntimeError("the game returned no observation" + ("; the server session is gone" if self.remote else ""))
        available = [getattr(action, "value", action) for action in observed.available_actions or ()]
        numbers = [int(action[0] if isinstance(action, tuple) else action) for action in available]
        return {
            "frames": observed.frame,
            "state": str(getattr(observed.state, "value", observed.state)),
            "levels_completed": int(observed.levels_completed),
            "win_levels": int(observed.win_levels),
            "available_actions": ["RESET" if number == 0 else f"ACTION{number}" for number in numbers],
        }

    @property
    def observation(self) -> dict[str, Any]:
        return self._observe(self.environment.observation_space)

    def step(self, action: str, params: dict[str, Any] | None) -> dict[str, Any]:
        from arcengine import GameAction

        # A local game restarts from its first level when it is reset on a
        # level nothing has been done on, so that reset changes nothing here.
        if action == "RESET" and self.fresh and not self.remote:
            return self.observation
        before = self.observation["levels_completed"]
        observed = self._observe(self.environment.step(GameAction.from_name(action), data=params, reasoning=None))
        self.fresh = action == "RESET" or observed["levels_completed"] != before
        if observed["state"] == "WIN" and not self.closed:
            self.closed = True
            try:
                self.arcade.close_scorecard()
            except Exception:  # noqa: BLE001 - the win is journaled whether or not the scorecard closes
                pass
        return observed


def factory(root: Path, config: Mapping[str, Any]) -> ArcSession:
    return ArcSession(root, config)
