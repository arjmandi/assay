"""ARC-AGI-3 world adapter for the ASSAY broker.

Wraps the `arc_agi` client library as an ASSAY session: local runs play a
cached copy of the public game (downloaded once with ARC_API_KEY), and
remote runs (`--mode remote`) talk to the competition server. The session
rules of each are declared to the kernel through the `session` property
(docs/ARCHITECTURE.md section 2.2), and the kernel implements them: the
competition session's fifteen-minute action-idle lease and its single life
(no replay), and the local run's opening RESET answered without rewinding
the cached game. Start ARC runs with

    assay start <game_id> --adapter <repo>/bench/arcagi/adapter.py:factory \
        --registry <repo>/bench/arcagi/registry_200.json

The adapter needs the `arc-agi` client library importable in the runtime that
serves the broker (`pip install arc-agi` into the interpreter `bin/assay`
selects).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from assay.adapters import SessionCapability
from assay.core import AssayError

# The modes the adapter reads from the run's config.json: the kernel's
# `local` and `remote`, and the value runs before 1.2.0 carried for remote.
LOCAL_MODE = "local"
REMOTE_MODES = ("remote", "competition")
# The competition server drops a session after fifteen idle minutes.
REMOTE_LEASE_SECONDS = 15 * 60


def _cache_base() -> Path:
    xdg = os.getenv("XDG_CACHE_HOME")
    return Path(xdg).expanduser() if xdg else Path.home() / ".cache"


def local_cache_root() -> Path:
    """Return the durable shared cache used by local public-game runs."""
    configured = os.getenv("ASSAY_CACHE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (_cache_base() / "assay" / "arcade").resolve()


def _legacy_cache_donors() -> list[Path]:
    """Older local caches silently adopted from, so cached games keep loading."""
    donors = [Path(value).expanduser() for value in (os.getenv("AA3_CACHE_DIR"),) if value]
    donors.append(_cache_base() / "aa3" / "arcade")
    return donors


def _cached_game_exists(environments: Path, game_id: str) -> bool:
    game = environments / game_id.lower()
    return game.is_dir() and any(game.glob("*/metadata.json"))


def _adopt_cached_game(environments: Path, game_id: str) -> None:
    """Copy an already-downloaded game from another local cache, if one exists."""
    if _cached_game_exists(environments, game_id):
        return
    for donor in _legacy_cache_donors():
        source = donor / "environment_files" / game_id.lower()
        if _cached_game_exists(donor / "environment_files", game_id):
            shutil.copytree(
                source, environments / game_id.lower(), dirs_exist_ok=True
            )
            return


class ArcSession:
    def __init__(self, root: Path, config: Mapping[str, Any]):
        try:
            import arc_agi
            from arc_agi import OperationMode
        except ImportError as error:
            raise AssayError(
                "the arc-agi client library is unavailable in this runtime; "
                "install it into the interpreter serving the broker"
            ) from error

        mode_name = str(config.get("mode", LOCAL_MODE)).lower()
        if mode_name != LOCAL_MODE and mode_name not in REMOTE_MODES:
            raise AssayError(f"unsupported session mode {mode_name!r}")
        self.remote = mode_name in REMOTE_MODES

        if self.remote:
            runtime = Path(tempfile.gettempdir()) / f"assay-{os.getuid()}" / "remote"
            mode = OperationMode.COMPETITION
        else:
            runtime = Path(str(config.get("cache_dir") or local_cache_root()))
            _adopt_cached_game(
                runtime / "environment_files", str(config["game_id"])
            )
            game_cached = _cached_game_exists(
                runtime / "environment_files", str(config["game_id"])
            )
            mode = OperationMode.OFFLINE if game_cached else OperationMode.NORMAL
        environments = runtime / "environment_files"
        recordings = root / ".assay" / "recordings"
        environments.mkdir(parents=True, exist_ok=True)
        recordings.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("assay.broker")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.arcade = arc_agi.Arcade(
            arc_api_key=os.getenv("ARC_API_KEY", ""),
            operation_mode=mode,
            environments_dir=str(environments),
            recordings_dir=str(recordings),
            logger=logger,
        )
        self.environment = self.arcade.make(
            str(config["game_id"]), seed=int(config.get("seed", 0))
        )
        if self.environment is None:
            if self.remote:
                raise AssayError(
                    f"could not initialize remote competition run for {config['game_id']}; ARC_API_KEY and network access are required"
                )
            if not os.getenv("ARC_API_KEY"):
                raise AssayError(
                    f"LOCAL_CACHE_MISS | {config['game_id']} is not in {environments}; set ARC_API_KEY once so the adapter can download the public game into its durable local cache"
                )
            raise AssayError(
                f"could not initialize local {config['game_id']}: the authenticated download or cached game failed"
            )

    @property
    def session(self) -> SessionCapability:
        """The session rules the kernel implements for this world. A
        competition session lives on the server, expires after fifteen idle
        minutes and cannot be replayed; every action, the opening RESET
        included, goes to the server. A local run plays the cached game and
        replays from its journal; a RESET on a fresh level is answered by
        the kernel with the current observation, so an opening reset never
        rewinds the cached game."""
        if self.remote:
            return SessionCapability(
                idle_lease_seconds=REMOTE_LEASE_SECONDS,
                reset_on_fresh_unit="world",
                replayable=False,
            )
        return SessionCapability(reset_on_fresh_unit="noop")

    @property
    def observation(self) -> Any:
        observed = self.environment.observation_space
        if observed is None:
            self._missing_observation()
        return observed

    @property
    def public_info(self) -> dict[str, Any]:
        info = getattr(self.environment, "info", None)
        if info is None:
            return {}
        output: dict[str, Any] = {}
        for name in ("game_id", "title", "tags", "default_fps"):
            value = getattr(info, name, None)
            if value is not None:
                output[name] = list(value) if isinstance(value, tuple) else value
        return output

    def step(
        self,
        action: str,
        data: dict[str, int] | None,
        reasoning: Mapping[str, Any] | None,
    ) -> Any:
        from arcengine import GameAction

        observed = self.environment.step(
            GameAction.from_name(action), data=data, reasoning=reasoning
        )
        if observed is None:
            self._missing_observation()
        return observed

    def _missing_observation(self) -> None:
        if self.remote:
            raise AssayError(
                "REMOTE_SESSION_UNAVAILABLE | the competition server returned no observation. This remote run cannot be reconstructed; preserve its artifacts and use a fresh directory for another run"
            )
        raise AssayError("the local simulator returned no observation")

    def finalize(self) -> None:
        self.arcade.close_scorecard()


def factory(root: Path, config: Mapping[str, Any]) -> ArcSession:
    """Broker entry point: `--adapter .../bench/arcagi/adapter.py:factory`."""
    return ArcSession(root, config)
