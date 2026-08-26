"""Factorio Learning Environment (FLE) world adapter for the ASSAY broker.

Wraps one FLE lab-play throughput task as an ASSAY session. The adapter owns the
Factorio server connection end to end: the agent never sees an address, a port,
or the RCON password, and the throughput verifier is computed here from game
state the agent has no writable path to.

    assay start iron_ore_throughput \
        --adapter <repo>/bench/factorio/adapter.py:factory \
        --registry <repo>/bench/factorio/registry_lab64.json

Two paid actuators (registry Option A — FLE parity):

    RUN  program=<base64 python>   execute one program against the FLE API
    WAIT ticks=<int>               advance the simulation by an exact tick count

`program` is base64 because ASSAY action tokens are whitespace-split; see
PROTOCOL.md. Every program is screened by a fail-closed AST gate before it
reaches the interpreter — FLE's own namespace hands agent programs the raw RCON
client, and through it the Lua console, the host filesystem, and the production
statistics the verifier reads. NAMESPACE_AUDIT.md documents the proof.

Time is never implicit. The game is paused except during metered advances, so a
journal of (program, tick-delta) pairs is the whole of what moved the world.
"""

from __future__ import annotations

import ast
import base64
import binascii
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from assay.core import AssayError

# ---------------------------------------------------------------------------
# Fixed protocol constants. Changing any of these changes the benchmark; they
# are restated in PROTOCOL.md and must move together with it.
# ---------------------------------------------------------------------------

TICKS_PER_SECOND = 60
WINDOW_TICKS = 60 * TICKS_PER_SECOND  # 3600 — one 60-second measurement window
MAX_WAIT_TICKS = WINDOW_TICKS  # one WAIT may not exceed a single window
RUN_PATH_TICKS = 180  # pathfinding allowance granted to a RUN that needs one
WIN_LEVELS = 4
DEFAULT_ADDRESS = "localhost"
DEFAULT_TCP_PORT = 27000
DEFAULT_SPEED = 10.0
DEFAULT_SEED = 44340  # the seed FLE hardcodes into its generated compose file
ENTITY_SAMPLE_CAP = 40
STREAM_CAP = 4000  # chars of program stdout/stderr carried in the observation

# ASSAY world ids are [a-z0-9]{2,16} (assay.core.normalize_game_id), so FLE's
# task keys cannot be used verbatim. This table is the whole mapping; PROTOCOL.md
# reproduces it. `assay start ironore` runs FLE's `iron_ore_throughput`.
TASK_ALIASES = {
    "advcircuit": "advanced_circuit_throughput",
    "autosci": "automation_science_pack_throughput",
    "battery": "battery_throughput",
    "chemsci": "chemical_science_pack_throughput",
    "circuit": "electronic_circuit_throughput",
    "crudeoil": "crude_oil_throughput",
    "engineunit": "engine_unit_throughput",
    "inserter": "inserter_throughput",
    "irongear": "iron_gear_wheel_throughput",
    "ironore": "iron_ore_throughput",
    "ironplate": "iron_plate_throughput",
    "lds": "low_density_structure_throughput",
    "logisci": "logistics_science_pack_throughput",
    "milsci": "military_science_pack_throughput",
    "petrogas": "petroleum_gas_throughput",
    "piercing": "piercing_round_throughput",
    "plastic": "plastic_bar_throughput",
    "procunit": "processing_unit_throughput",
    "prodsci": "production_science_pack_throughput",
    "steelplate": "steel_plate_throughput",
    "stonewall": "stone_wall_throughput",
    "sulfacid": "sufuric_acid_throughput",
    "sulfur": "sulfur_throughput",
    "utilsci": "utility_science_pack_throughput",
}

# FLE lab-play starting inventory (fle.eval.tasks.throughput_task).
LAB_PLAY_STARTING_INVENTORY = {
    "coal": 500,
    "burner-mining-drill": 50,
    "wooden-chest": 10,
    "burner-inserter": 50,
    "inserter": 50,
    "transport-belt": 500,
    "stone-furnace": 10,
    "boiler": 2,
    "offshore-pump": 2,
    "steam-engine": 2,
    "electric-mining-drill": 50,
    "medium-electric-pole": 500,
    "pipe": 500,
    "assembling-machine-2": 10,
    "electric-furnace": 10,
    "pipe-to-ground": 100,
    "underground-belt": 100,
    "pumpjack": 10,
    "oil-refinery": 5,
    "chemical-plant": 5,
    "storage-tank": 10,
}

# ---------------------------------------------------------------------------
# Program screening — the capability boundary (see NAMESPACE_AUDIT.md)
# ---------------------------------------------------------------------------

# Fail closed: a node kind absent from this set is refused. Everything an FLE
# program legitimately needs is here; nothing that reaches outside the game is.
_ALLOWED_NODES = frozenset(
    {
        "Module", "Expr", "Assign", "AugAssign", "AnnAssign", "NamedExpr",
        "Name", "Load", "Store", "Constant", "Attribute", "Subscript", "Slice",
        "Call", "keyword", "Starred",
        "BinOp", "UnaryOp", "BoolOp", "Compare", "IfExp", "Lambda",
        "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow",
        "LShift", "RShift", "BitOr", "BitXor", "BitAnd", "MatMult",
        "UAdd", "USub", "Not", "Invert", "And", "Or",
        "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn",
        "List", "Tuple", "Dict", "Set",
        "ListComp", "SetComp", "DictComp", "GeneratorExp", "comprehension",
        "If", "For", "While", "Break", "Continue", "Pass",
        "FunctionDef", "arguments", "arg", "Return",
        "Try", "TryStar", "ExceptHandler", "Raise", "Assert",
        "JoinedStr", "FormattedValue",
    }
)

# Names that are either a handle out of the game or a way to build one. The
# leading-underscore rule below covers the dunder walks; these are the rest.
_DENIED_NAMES = frozenset(
    {
        "instance", "controllers", "rcon", "rcon_client", "connection",
        "lua_script_manager", "game_state", "namespace", "namespaces",
        "first_namespace", "persistent_vars", "script_dict",
        "eval", "exec", "compile", "open", "input", "breakpoint", "help",
        "globals", "locals", "vars", "getattr", "setattr", "delattr", "hasattr",
        "object", "super", "memoryview", "exit", "quit", "license", "credits",
        "os", "sys", "subprocess", "importlib", "builtins", "socket", "shutil",
        "pathlib", "pickle", "ctypes", "threading", "atexit", "signal",
        "sleep",  # time passes only through WAIT; FLE's sleep() is a no-op here
    }
)

# Attribute names that hand back a live handle even from an ordinary object.
_DENIED_ATTRS = frozenset(
    {
        "instance", "controllers", "rcon", "rcon_client", "connection",
        "lua_script_manager", "game_state", "namespace", "namespaces",
        "first_namespace", "persistent_vars", "script_dict", "game_control",
        "send_command", "execute", "eval", "eval_with_error", "reset",
        "cleanup", "connect_to_server", "reconnect", "load", "func",
        "gi_frame", "cr_frame", "tb_frame",
    }
)

# Calls whose FLE implementation blocks on Factorio's asynchronous path finder.
# `move_to` and `connect_entities` use it directly; `harvest_resource` walks to
# an out-of-reach resource through `move_to`. A program naming one of these
# earns a fixed tick allowance; every other program runs against a frozen world.
_PATHFINDING_CALLS = frozenset({"move_to", "connect_entities", "harvest_resource"})


def screen_program(source: str) -> tuple[str | None, bool]:
    """Screen one agent program before it reaches FLE.

    Returns (refusal_reason or None, needs_pathfinding_ticks). Screening is a
    pure function of the program text, so a journal replays to the same
    decision and the same tick cost.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return f"the program does not parse: {error}", False

    needs_ticks = False
    for node in ast.walk(tree):
        kind = type(node).__name__
        if kind not in _ALLOWED_NODES:
            return (
                f"{kind} is not permitted in a RUN program; the world is reached "
                "only through the FLE API calls, not through imports, class or "
                "context statements, or async constructs",
                False,
            )
        if isinstance(node, ast.Name):
            if node.id.startswith("_"):
                return f"the name {node.id!r} is private to the harness", False
            if node.id in _DENIED_NAMES:
                return (
                    f"the name {node.id!r} is outside the game boundary; RUN "
                    "programs act only through the FLE API",
                    False,
                )
            if node.id in _PATHFINDING_CALLS:
                needs_ticks = True
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                return f"the attribute {node.attr!r} is private to the harness", False
            if node.attr in _DENIED_ATTRS:
                return (
                    f"the attribute {node.attr!r} is outside the game boundary",
                    False,
                )
        elif isinstance(node, ast.FunctionDef):
            if node.name.startswith("_"):
                return "function names beginning with '_' are reserved", False
        elif isinstance(node, ast.arg):
            if node.arg.startswith("_"):
                return "argument names beginning with '_' are reserved", False
        elif isinstance(node, ast.keyword):
            if node.arg is not None and node.arg.startswith("_"):
                return "keyword names beginning with '_' are reserved", False
        elif isinstance(node, ast.ExceptHandler):
            if node.name is not None and node.name.startswith("_"):
                return "exception names beginning with '_' are reserved", False
    return None, needs_ticks


class _SealedInstance:
    """Defence in depth: what FLE's own tools need off the instance, nothing else.

    FLE stores the live `FactorioInstance` on the namespace under the plain name
    `instance`, and the namespace is copied wholesale into every agent program's
    globals. The AST gate above is the boundary that actually holds; this proxy
    removes the single most obvious handle so a gate mistake is not immediately
    a full compromise.
    """

    __slots__ = ("_target",)
    # Exactly the members FLE's own tool clients read off the instance
    # (`grep -r 'game_state\.instance\.' fle/env/`), and nothing else.
    _VISIBLE = frozenset(
        {
            "fast",
            "get_elapsed_ticks",
            "get_speed",
            "initial_score",
            "is_multiagent",
            "num_agents",
        }
    )

    def __init__(self, target: Any) -> None:
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str) -> Any:
        if name in _SealedInstance._VISIBLE:
            return getattr(object.__getattribute__(self, "_target"), name)
        raise AttributeError(
            f"{name!r} is sealed: agent programs reach the world through the FLE API only"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("the FLE instance is sealed against writes")

    def __repr__(self) -> str:
        return "<sealed FactorioInstance>"


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


def _decode_program(raw: Any) -> str:
    if not isinstance(raw, str) or not raw:
        raise AssayError("RUN needs program=<base64-encoded UTF-8 python source>")
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as error:
        raise AssayError(
            "RUN program must be base64-encoded UTF-8 python source "
            "(ASSAY action tokens are whitespace-split, so raw source cannot be "
            f"passed inline): {error}"
        ) from None


def _port_open(address: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def _round(value: Any) -> Any:
    if isinstance(value, float):
        rounded = round(value, 6)
        return int(rounded) if rounded.is_integer() else rounded
    return value


class FactorioSession:
    """One FLE lab-play throughput task, served to ASSAY as a general observation."""

    def __init__(self, root: Path, config: Mapping[str, Any]):
        try:
            from factorio_rcon import RCONClient
            from fle.cluster.run_envs import RCON_PASSWORD
            from fle.env.instance import FactorioInstance
            from fle.eval.tasks.task_definitions.lab_play.throughput_tasks import (
                THROUGHPUT_TASKS,
            )
        except ImportError as error:
            raise AssayError(
                "the factorio-learning-environment package is unavailable in this "
                "runtime; install it (plus `a2a-sdk<1`) into the interpreter serving "
                "the broker — see bench/factorio/PROTOCOL.md"
            ) from error

        world_id = str(config.get("game_id", "")).strip().lower()
        task_key = TASK_ALIASES.get(world_id, world_id)
        task = THROUGHPUT_TASKS.get(task_key)
        if task is None:
            raise AssayError(
                f"unknown Factorio world {world_id!r}; this adapter serves FLE 0.4.3's "
                f"24 lab-play throughput tasks under the ids {sorted(TASK_ALIASES)} "
                "(see bench/factorio/PROTOCOL.md for the id-to-task table)"
            )
        self.world_id = world_id
        self.task_key = task_key
        self.quota = int(task.quota)
        entity = task.throughput_entity
        value = getattr(entity, "value", entity)
        self.target_item = str(value[0] if isinstance(value, tuple) else value)

        self.address = str(config.get("address") or DEFAULT_ADDRESS)
        self.tcp_port = int(config.get("tcp_port") or DEFAULT_TCP_PORT)
        self.speed = float(config.get("speed") or DEFAULT_SPEED)
        # FLE bakes the map seed into the compose file it generates and offers no
        # way to override it per session, so a seed asked for here would be a lie.
        # Refuse rather than silently ignore it.
        self.map_seed = int(config.get("seed") or DEFAULT_SEED)
        if self.map_seed != DEFAULT_SEED:
            raise AssayError(
                f"this world runs on FLE's fixed map seed {DEFAULT_SEED}; seed "
                f"{self.map_seed} cannot be honoured (FLE hardcodes the seed in the "
                "compose file it generates, fle/cluster/run-envs.sh)"
            )
        self._stop_cluster = bool(config.get("stop_cluster", False))
        self._closed = False
        self._cached_observation: dict[str, Any] | None = None

        password = (
            os.getenv("ASSAY_FLE_RCON_PASSWORD")
            or os.getenv("FLE_RCON_PASSWORD")
            or RCON_PASSWORD
        )

        if not _port_open(self.address, self.tcp_port):
            if config.get("autostart", True):
                self._start_cluster()
            if not _port_open(self.address, self.tcp_port):
                raise AssayError(
                    f"no Factorio server is listening on {self.address}:{self.tcp_port}; "
                    "start one with `python -m fle cluster start -n 1` (Docker must be "
                    "running) — see bench/factorio/PROTOCOL.md"
                )

        try:
            self._instance = FactorioInstance(
                address=self.address,
                tcp_port=self.tcp_port,
                fast=True,
                inventory=dict(LAB_PLAY_STARTING_INVENTORY),
                all_technologies_researched=True,
                cache_scripts=True,
                num_agents=1,
                reset_speed=self.speed,
                reset_paused=False,
            )
        except Exception as error:  # noqa: BLE001 - surface any FLE boot failure clearly
            raise AssayError(
                f"could not initialise the Factorio environment: {type(error).__name__}: {error}"
            ) from error

        # A private RCON socket for tick control and state reads. FLE's own
        # client is not thread-safe and is busy inside a running program while
        # the tick pump needs to talk to the server.
        try:
            self._rcon = RCONClient(self.address, self.tcp_port, password)
            self._rcon.connect()
        except Exception as error:  # noqa: BLE001
            raise AssayError(
                f"could not open the adapter's control connection: {type(error).__name__}: {error}"
            ) from error

        self._namespace = self._instance.first_namespace
        self._provision()

        self._last_action: str | None = None
        self._stdout = ""
        self._stderr = ""
        self._refusals = 0
        self._resets = 0
        self._levels = 0

    # -- lifecycle ---------------------------------------------------------

    def _start_cluster(self) -> None:
        try:
            subprocess.run(
                [sys.executable, "-m", "fle", "cluster", "start", "-n", "1"],
                check=True,
                capture_output=True,
                timeout=300,
            )
        except Exception as error:  # noqa: BLE001
            raise AssayError(
                f"could not start an FLE cluster: {type(error).__name__}: {error}; "
                "start it by hand with `python -m fle cluster start -n 1`"
            ) from error
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            if _port_open(self.address, self.tcp_port):
                return
            time.sleep(0.5)

    def _provision(self) -> None:
        """Reset to the task's start state and take control of time."""
        self._command("/sc game.tick_paused = false")
        self._command(f"/sc game.speed = {self.speed}")
        self._instance.reset()
        # FLE's GameControl caches pause state per process and goes stale, so
        # the server is told directly (M0 finding 4).
        self._command("/sc game.tick_paused = false")
        self._command(f"/sc game.speed = {self.speed}")
        self._command("/sc game.tick_paused = true")
        self._anchor_tick = self._tick()
        self._cursor = 0  # ticks advanced since the anchor; the whole clock
        self._snapshots: list[dict[str, Any]] = [
            {"tick": 0, "flows": self._flows()}
        ]

    def finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._cached_observation = self._observe()
        except Exception:  # noqa: BLE001 - shutdown must not raise over a read
            pass
        for close in (
            lambda: self._rcon.close(),
            lambda: self._instance.cleanup(),
        ):
            try:
                close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
        if self._stop_cluster:
            try:
                subprocess.run(
                    [sys.executable, "-m", "fle", "cluster", "stop"],
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
            except Exception:  # noqa: BLE001
                pass

    # -- server plumbing ---------------------------------------------------

    def _command(self, lua: str) -> str:
        try:
            return self._rcon.send_command(lua) or ""
        except Exception as error:  # noqa: BLE001
            raise AssayError(
                f"the Factorio server stopped responding: {type(error).__name__}: {error}"
            ) from error

    def _tick(self) -> int:
        raw = self._command("/sc rcon.print(game.tick)")
        try:
            return int(raw)
        except ValueError:
            raise AssayError(f"unreadable tick from the server: {raw!r}") from None

    def _tick_step(self, count: int) -> None:
        """Advance exactly `count` ticks with the game otherwise frozen.

        The poll below is wall-clock, but only as a completion test: the number
        of ticks that pass is set by the server, is exact, and is journaled.
        """
        if count <= 0:
            return
        start = self._tick()
        target = start + count
        self._command(f"/sc game.ticks_to_run = {count}")
        deadline = time.monotonic() + 60.0 + count / 20.0
        while time.monotonic() < deadline:
            if self._tick() >= target:
                return
            time.sleep(0.01)
        raise AssayError(
            f"the server did not complete a {count}-tick advance; the run's tick "
            "ledger can no longer be trusted"
        )

    def _advance(self, ticks: int) -> None:
        """Advance the clock, snapshotting production on every window boundary."""
        remaining = int(ticks)
        while remaining > 0:
            to_boundary = WINDOW_TICKS - (self._cursor % WINDOW_TICKS)
            chunk = min(remaining, to_boundary)
            self._tick_step(chunk)
            self._cursor += chunk
            remaining -= chunk
            if self._cursor % WINDOW_TICKS == 0:
                self._snapshots.append(
                    {"tick": self._cursor, "flows": self._flows()}
                )

    # -- world reads (the agent has no write path to any of these) ----------

    def _flows(self) -> dict[str, Any]:
        stats = self._namespace._get_production_stats()
        if not isinstance(stats, Mapping):
            return {"input": {}, "output": {}, "crafted": [], "harvested": {}}
        crafted = stats.get("crafted", [])
        if isinstance(crafted, Mapping):
            crafted = list(crafted.values())
        return {
            "input": {str(k): _round(v) for k, v in (stats.get("input") or {}).items()},
            "output": {str(k): _round(v) for k, v in (stats.get("output") or {}).items()},
            "harvested": {
                str(k): _round(v) for k, v in (stats.get("harvested") or {}).items()
            },
            "crafted": json.loads(json.dumps(crafted, sort_keys=True, default=str)),
        }

    def _inventory(self) -> dict[str, int]:
        inventory = self._namespace.inspect_inventory()
        try:
            items = dict(inventory.items())
        except Exception:  # noqa: BLE001 - pydantic extras fallback (spike.py pattern)
            items = getattr(inventory, "__pydantic_extra__", None) or {}
        return {str(k): int(v) for k, v in sorted(items.items()) if v}

    def _entities(self) -> tuple[list[dict[str, Any]], dict[str, int]]:
        try:
            found = self._namespace.get_entities()
        except Exception:  # noqa: BLE001 - a broken query must not kill the run
            return [], {}
        rows: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for item in found:
            name = str(getattr(item, "name", "?"))
            counts[name] = counts.get(name, 0) + 1
            position = getattr(item, "position", None)
            rows.append(
                {
                    "name": name,
                    "x": _round(float(position.x)) if position is not None else None,
                    "y": _round(float(position.y)) if position is not None else None,
                    "direction": str(getattr(item, "direction", "")),
                    "status": str(getattr(item, "status", "")),
                }
            )
        rows.sort(key=lambda row: json.dumps(row, sort_keys=True))
        return rows[:ENTITY_SAMPLE_CAP], dict(sorted(counts.items()))

    def _research(self) -> dict[str, Any]:
        raw = self._command(
            "/sc local f=game.forces.player local n=0 "
            "for _,t in pairs(f.technologies) do if t.researched then n=n+1 end end "
            'rcon.print(n .. "|" .. (f.current_research and f.current_research.name or "none"))'
        )
        researched, _, current = raw.partition("|")
        try:
            count = int(researched)
        except ValueError:
            count = -1
        return {"researched_count": count, "current": current or "none"}

    # -- the verifier ------------------------------------------------------

    def _dynamic(self, pre: Mapping[str, Any], post: Mapping[str, Any]) -> float:
        """Automated production of the target between two flow snapshots.

        FLE's own accounting: total new output minus what the player hand-mined
        or hand-crafted. Chest-stuffing and hand-crafting land in `static` and
        do not count here — the same reason FLE introduced the holdout.
        """
        from fle.commons.models.achievements import ProductionFlows
        from fle.env.utils.achievements import calculate_achievements

        try:
            achieved = calculate_achievements(
                ProductionFlows.from_dict(dict(pre)),
                ProductionFlows.from_dict(dict(post)),
            )
        except Exception:  # noqa: BLE001 - never let accounting kill a run
            return 0.0
        return float(achieved.get("dynamic", {}).get(self.target_item, 0.0))

    def _window_rates(self) -> list[float]:
        return [
            self._dynamic(pre["flows"], post["flows"])
            for pre, post in zip(self._snapshots, self._snapshots[1:])
        ]

    def _grade(self, flows: Mapping[str, Any], counts: Mapping[str, int]) -> int:
        """The milestone ladder, computed from server state only.

        1  the first unit of the target item has been produced
        2  an automated chain producing it exists
        3  a full 60 s window met the quota
        4  the quota also held through the next 60 s — the holdout — = WIN
        """
        rates = self._window_rates()
        produced = float(dict(flows.get("output") or {}).get(self.target_item, 0.0))
        automated = self._dynamic(self._snapshots[0]["flows"], flows)
        level = 0
        if produced > 0:
            level = 1
        if automated > 0 and counts:
            level = max(level, 2)
        if any(rate >= self.quota for rate in rates):
            level = max(level, 3)
        if any(
            first >= self.quota and second >= self.quota
            for first, second in zip(rates, rates[1:])
        ):
            level = max(level, 4)
        return level

    # -- observation -------------------------------------------------------

    def _observe(self) -> dict[str, Any]:
        flows = self._flows()
        entities, counts = self._entities()
        rates = self._window_rates()
        level = max(self._levels, self._grade(flows, counts))
        self._levels = level
        output = {
            key: value
            for key, value in flows["output"].items()
            if value
        }
        data = {
            "task": self.task_key,
            "target_item": self.target_item,
            "quota_per_window": self.quota,
            "window_ticks": WINDOW_TICKS,
            "tick": self._cursor,
            "last_action": self._last_action,
            "stdout": self._stdout[:STREAM_CAP],
            "stderr": self._stderr[:STREAM_CAP],
            "inventory": self._inventory(),
            "entity_counts": counts,
            "entities": entities,
            "entities_shown": len(entities),
            "entities_total": sum(counts.values()),
            "production": {
                "output": dict(sorted(output.items())),
                "input": dict(sorted(flows["input"].items())),
                "harvested": dict(sorted(flows["harvested"].items())),
            },
            "target_produced_total": _round(
                float(flows["output"].get(self.target_item, 0.0))
            ),
            "target_automated_total": _round(
                self._dynamic(self._snapshots[0]["flows"], flows)
            ),
            "windows_complete": len(rates),
            "window_rates": [_round(rate) for rate in rates][-8:],
            "research": self._research(),
            "policy_refusals": self._refusals,
            "resets": self._resets,
        }
        return {
            "data": data,
            "state": "WIN" if level >= WIN_LEVELS else "NOT_FINISHED",
            "levels_completed": level,
            "win_levels": WIN_LEVELS,
            "available_actions": ["RUN", "WAIT"],
        }

    @property
    def observation(self) -> dict[str, Any]:
        if self._closed:
            if self._cached_observation is None:
                raise AssayError("the Factorio session is closed")
            return self._cached_observation
        return self._observe()

    @property
    def public_info(self) -> dict[str, Any]:
        return {
            "game_id": self.world_id,
            "title": f"FLE lab-play {self.task_key} — {self.target_item} throughput",
            "map_seed": self.map_seed,
        }

    # -- actions -----------------------------------------------------------

    def step(
        self,
        action: str,
        data: dict[str, Any] | None,
        reasoning: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if self._closed:
            raise AssayError("the Factorio session is closed")
        name = str(action).upper()
        payload = dict(data or {})
        if name == "RUN":
            self._do_run(_decode_program(payload.get("program")))
        elif name == "WAIT":
            self._do_wait(payload.get("ticks"))
        elif name == "RESET":
            self._do_reset()
        else:
            raise AssayError(
                f"unknown action {action!r}; this world takes RUN, WAIT, and RESET"
            )
        self._last_action = name
        return self._observe()

    def _do_wait(self, raw: Any) -> None:
        try:
            ticks = int(raw)
        except (TypeError, ValueError):
            raise AssayError("WAIT needs ticks=<integer>") from None
        if not 1 <= ticks <= MAX_WAIT_TICKS:
            raise AssayError(
                f"WAIT ticks must be between 1 and {MAX_WAIT_TICKS} "
                f"({MAX_WAIT_TICKS // TICKS_PER_SECOND} in-game seconds)"
            )
        self._stdout = ""
        self._stderr = ""
        self._advance(ticks)

    def _do_reset(self) -> None:
        self._provision()
        self._resets += 1
        self._levels = 0
        self._stdout = ""
        self._stderr = ""

    def _do_run(self, source: str) -> None:
        refusal, needs_ticks = screen_program(source)
        if refusal is not None:
            # Spend the action and journal the attempt. A refusal that vanished
            # would defeat the point: the record of what was tried is the
            # evidence an audit reads.
            self._refusals += 1
            self._stdout = ""
            self._stderr = f"POLICY_REFUSED | {refusal}"
            return

        result: dict[str, Any] = {}

        def execute() -> None:
            try:
                _, _, response = self._instance.eval(source, timeout=300)
                result["out"] = str(response)
            except Exception as error:  # noqa: BLE001 - the program's failure is data
                result["err"] = f"{type(error).__name__}: {error}"

        worker = threading.Thread(target=execute, daemon=True)
        worker.start()
        if needs_ticks:
            # Factorio's path finder answers on an in-game event, so a program
            # that asks for a path needs ticks while it waits. The allowance is
            # fixed and pre-declared, so the tick cost of a RUN is a function of
            # the program text alone and replays identically.
            try:
                self._advance(RUN_PATH_TICKS)
            except AssayError:
                worker.join(timeout=300)
                raise
        worker.join(timeout=600)
        if worker.is_alive():
            raise AssayError(
                "the FLE program did not return; the session state is no longer "
                "trustworthy and this run should be abandoned"
            )
        self._stdout = result.get("out", "")
        self._stderr = result.get("err", "")


def factory(root: Path, config: Mapping[str, Any]) -> FactorioSession:
    """Broker entry point: `--adapter .../bench/factorio/adapter.py:factory`."""
    session = FactorioSession(root, config)
    try:
        session._namespace.instance = _SealedInstance(session._instance)
    except Exception:  # noqa: BLE001 - the AST gate is the boundary; this is extra
        pass
    return session
