"""The command-world engine, behind the local and repo archetypes.

A command world is a world on this machine whose actions are commands. A
recipe (recipe.json, beside a pack's adapter.py) names them: one that prints
the world's state as a JSON object (observe), one that applies a validated
action (act), optionally one that builds a fresh world (setup), and the rules
that read the state (win, lose, levels). The engine is the session contract of
docs/ARCHITECTURE.md section 2.2 around those commands, so a pack supplies
configuration and world scripts, never adapter code. worlds/README.md is the
recipe's reference. The two modes:

- copy, the local archetype. The world is a fresh copy under `.assay/world/`,
  rebuilt from the recipe's source and setup command whenever the daemon
  builds the session: at start, and before a resume replays the journal. The
  world scripts must therefore be deterministic given the seed and the
  actions. The agent reads the copy with its own tools and changes it only
  through `assay act`; in a Claude Code session the hooks refuse its writes
  under `.assay/`, which is also why hidden state never belongs in the copy
  (derive it from {seed} inside the act command). A RESET restores the copy
  as it stood when the current progress unit began.
- worktree, the repo archetype. The world is a working tree at `work/` in the
  run directory, which the agent edits freely, and a paid action runs a
  command over it, such as the tests. The tree changes outside the journal,
  so the world declares no replay and lives one daemon long, and a RESET
  leaves the tree as it is.

The protocol with the world scripts, each run with the working directory as
its current directory and PYTHONDONTWRITEBYTECODE set:

- observe prints one JSON object, the world's data. The engine adds `workdir`
  (the working directory relative to the run directory), `last_result` and
  `refusals`, so the world may not use those keys.
- act receives the action's validated parameters as one JSON object on stdin
  and prints one JSON object, {"status": "ok" | "refused", "detail": "..."},
  plus fields of its own, which land in `last_result`. A refusal is the world
  saying no for its own reasons, so the spend is journaled as evidence. A
  non-zero exit, a timeout, or output that is not that object is a fault of
  the world scripts: the engine puts the copy back as it was and raises, and
  the kernel spends nothing.
- setup, when the recipe names one, builds the world in the new working
  directory.

A command is an argv list, never a shell line. Its words may carry these
placeholders: {python} (the daemon's interpreter, `world_python`), {recipe} (the recipe's
directory), {archetypes} (this file's directory), {workdir}, {state} (a
scratch directory under `.assay/` for the world scripts, which a RESET does
not rewind), {seed} (the run's seed) and, in act, {action}.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assay.adapters import SessionCapability

ARCHETYPES = Path(__file__).resolve().parent
RECIPE_FORMAT = 1
MODES = ("copy", "worktree")
ARCHETYPE_OF_MODE = {"copy": "local", "worktree": "repo"}
TOP_LEVEL_KEYS = frozenset({"recipe", "name", "card", "world", "tests", "player"})
WORLD_KEYS = frozenset(
    {"mode", "source", "setup", "observe", "act", "actions", "timeout_seconds", "win", "lose", "levels"}
)
ENGINE_KEYS = ("workdir", "last_result", "refusals")
PLACEHOLDER = re.compile(r"\{([a-z]+)\}")
PLACEHOLDERS = frozenset({"python", "recipe", "archetypes", "workdir", "state", "seed", "action"})
ACTION_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
EQUALITY = ("==", "!=")
ORDERING = ("<", "<=", ">", ">=")
RESULT_STATUSES = ("ok", "refused")
MAX_TIMEOUT_SECONDS = 3600.0

# Where the engine keeps what it owns, inside the run's state directory: the
# copy-mode world, the scratch directory of the world scripts, and the
# engine's own snapshots (the entry state of each progress unit and the
# checkpoint taken before each action).
COPY_PARTS = (".assay", "world")
STATE_PARTS = (".assay", "world_state")
ENGINE_PARTS = (".assay", "world_engine")
WORKTREE_PARTS = ("work",)


class RecipeError(ValueError):
    """A recipe that does not fit the format, named by its file and field."""


class _Missing:
    """A path that the data does not hold."""


MISSING = _Missing()


@dataclass(frozen=True)
class Recipe:
    """A loaded, validated recipe. `world` holds the engine's part; `extra`
    the archetype's own blocks (such as the repo archetype's `tests`), which
    the world scripts read from the file themselves."""

    path: Path
    name: str
    mode: str
    source: Path | None
    setup: tuple[str, ...] | None
    observe: tuple[str, ...]
    act: tuple[str, ...]
    actions: tuple[str, ...]
    timeout_seconds: float
    win: Mapping[str, Any] | None
    lose: Mapping[str, Any] | None
    levels_completed: int | Mapping[str, Any] | None
    win_levels: int | Mapping[str, Any] | None
    card: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def archetype(self) -> str:
        return ARCHETYPE_OF_MODE[self.mode]


def load_recipe(path: Path | str) -> Recipe:
    """Read and check a recipe file. A problem is a RecipeError naming the file
    and the field, raised at import time so `assay doctor` and `assay start`
    report it before anything runs."""
    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RecipeError(f"{path}: cannot read the recipe: {error}") from error

    def fail(where: str, message: str) -> RecipeError:
        return RecipeError(f"{path}: {where}: {message}")

    if not isinstance(raw, dict):
        raise fail("recipe", "the file must hold one JSON object")
    unknown = sorted(set(raw) - TOP_LEVEL_KEYS)
    if unknown:
        raise fail(unknown[0], f"not a recipe key; the keys are {sorted(TOP_LEVEL_KEYS)}")
    if raw.get("recipe") != RECIPE_FORMAT:
        raise fail("recipe", f"the format version must be {RECIPE_FORMAT}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise fail("name", "a non-empty string is required")
    card = raw.get("card", {})
    if not isinstance(card, dict):
        raise fail("card", "an object is required")
    world = raw.get("world")
    if not isinstance(world, dict):
        raise fail("world", "an object is required")
    unknown = sorted(set(world) - WORLD_KEYS)
    if unknown:
        raise fail(f"world.{unknown[0]}", f"not a world key; the keys are {sorted(WORLD_KEYS)}")

    mode = world.get("mode", "copy")
    if mode not in MODES:
        raise fail("world.mode", f"one of {list(MODES)}")
    source: Path | None = None
    if world.get("source") is not None:
        text = world["source"]
        if not isinstance(text, str) or not text:
            raise fail("world.source", "a directory path, relative to the recipe or absolute")
        candidate = Path(text).expanduser()
        source = (candidate if candidate.is_absolute() else path.parent / candidate).resolve()
        if not source.is_dir():
            raise fail("world.source", f"{source} is not a directory")
    elif mode == "worktree":
        raise fail("world.source", "the worktree mode copies a source tree into work/; name it")

    setup = _command(world.get("setup"), "world.setup", fail, optional=True)
    observe = _command(world.get("observe"), "world.observe", fail)
    act = _command(world.get("act"), "world.act", fail, action=True)
    assert observe is not None and act is not None

    actions = world.get("actions")
    if not isinstance(actions, list) or not actions:
        raise fail("world.actions", "a non-empty list of the registry's action names")
    for name_ in actions:
        if not isinstance(name_, str) or not ACTION_NAME.match(name_) or name_ == "RESET":
            raise fail("world.actions", f"{name_!r} is not an action name (RESET is built in)")
    if len(set(actions)) != len(actions):
        raise fail("world.actions", "a name is listed twice")

    timeout = world.get("timeout_seconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise fail("world.timeout_seconds", f"a number of seconds above 0 and at most {MAX_TIMEOUT_SECONDS:g}")

    win = world.get("win")
    lose = world.get("lose")
    if win is not None:
        _check_condition(win, "world.win", fail)
    if lose is not None:
        _check_condition(lose, "world.lose", fail)
    levels = world.get("levels", {})
    if not isinstance(levels, dict) or set(levels) - {"completed", "total"}:
        raise fail("world.levels", 'an object with "completed" and "total"')
    completed = _levels_spec(levels.get("completed"), "world.levels.completed", fail)
    total = _levels_spec(levels.get("total"), "world.levels.total", fail)
    if isinstance(total, int) and total < 1:
        raise fail("world.levels.total", "at least 1")

    extra = {key: raw[key] for key in ("tests", "player") if key in raw}
    for key, value in extra.items():
        if not isinstance(value, dict):
            raise fail(key, "an object is required")
    return Recipe(
        path=path,
        name=name.strip(),
        mode=mode,
        source=source,
        setup=setup,
        observe=observe,
        act=act,
        actions=tuple(actions),
        timeout_seconds=float(timeout),
        win=win,
        lose=lose,
        levels_completed=completed,
        win_levels=total,
        card=card,
        extra=extra,
    )


def _command(
    value: Any, where: str, fail: Callable[[str, str], RecipeError], *, optional: bool = False, action: bool = False
) -> tuple[str, ...] | None:
    if value is None:
        if optional:
            return None
        raise fail(where, "an argv list is required")
    if not isinstance(value, list) or not value or not all(isinstance(word, str) and word for word in value):
        raise fail(where, "an argv list of non-empty strings, never a shell line")
    for word in value:
        for placeholder in PLACEHOLDER.findall(word):
            if placeholder not in PLACEHOLDERS:
                raise fail(where, f"{{{placeholder}}} is not a placeholder; the placeholders are {sorted(PLACEHOLDERS)}")
            if placeholder == "action" and not action:
                raise fail(where, "{action} belongs to the act command alone")
    return tuple(value)


def _levels_spec(
    value: Any, where: str, fail: Callable[[str, str], RecipeError]
) -> int | Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, dict) and set(value) == {"path"} and isinstance(value["path"], str) and value["path"]:
        return value
    raise fail(where, 'a non-negative integer, or {"path": "a.b"} into the observation data')


def _check_condition(value: Any, where: str, fail: Callable[[str, str], RecipeError]) -> None:
    """A condition is a test, {"path", "op", "value"}, or {"all": [...]},
    {"any": [...]} or {"not": condition}."""
    if not isinstance(value, dict):
        raise fail(where, "a condition is a JSON object")
    for key in ("all", "any"):
        if key in value:
            items = value[key]
            if set(value) != {key} or not isinstance(items, list) or not items:
                raise fail(where, f'"{key}" takes a non-empty list of conditions and nothing beside it')
            for index, item in enumerate(items):
                _check_condition(item, f"{where}.{key}[{index}]", fail)
            return
    if "not" in value:
        if set(value) != {"not"}:
            raise fail(where, '"not" takes one condition and nothing beside it')
        _check_condition(value["not"], f"{where}.not", fail)
        return
    if set(value) != {"path", "op", "value"}:
        raise fail(where, 'a test has exactly "path", "op" and "value"')
    if not isinstance(value["path"], str) or not value["path"]:
        raise fail(where, "the path is a dotted path into the observation data")
    op = value["op"]
    if op not in EQUALITY + ORDERING:
        raise fail(where, f"the op is one of {list(EQUALITY + ORDERING)}")
    expected = value["value"]
    if expected is not None and not isinstance(expected, (str, int, float, bool)):
        raise fail(where, "the value is a JSON scalar")
    if op in ORDERING and not _is_number(expected):
        raise fail(where, f"{op} compares numbers")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def read_path(data: Any, path: str) -> Any:
    """The value at a dotted path, walking objects by key and arrays by index;
    MISSING when the data does not hold it."""
    value = data
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return MISSING
    return value


def condition_paths(condition: Mapping[str, Any]) -> list[str]:
    for key in ("all", "any"):
        if key in condition:
            return [path for item in condition[key] for path in condition_paths(item)]
    if "not" in condition:
        return condition_paths(condition["not"])
    return [condition["path"]]


def holds(condition: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    """Whether the condition holds over the data. A path the data does not
    hold makes its test false."""
    if "all" in condition:
        return all(holds(item, data) for item in condition["all"])
    if "any" in condition:
        return any(holds(item, data) for item in condition["any"])
    if "not" in condition:
        return not holds(condition["not"], data)
    actual = read_path(data, condition["path"])
    if actual is MISSING:
        return False
    op, expected = condition["op"], condition["value"]
    if op in EQUALITY:
        same = _same(actual, expected)
        return same if op == "==" else not same
    if not _is_number(actual):
        return False
    return bool({
        "<": actual < expected,
        "<=": actual <= expected,
        ">": actual > expected,
        ">=": actual >= expected,
    }[op])


def _same(actual: Any, expected: Any) -> bool:
    # JSON keeps true apart from 1; Python does not, so the types decide first.
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return bool(actual == expected)


def world_python() -> str:
    """The interpreter {python} names: the daemon's own, else its base, else
    the first python3 on PATH. A daemon started through an ephemeral
    environment (`uv run --with`) outlives it, and its sys.executable can
    point at a path that is already gone."""
    for candidate in (sys.executable, getattr(sys, "_base_executable", "")):
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which("python3") or sys.executable


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        symlinks=True,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )


class CommandWorld:
    """The session: the observer and the actuator over the recipe's commands.
    Built by a pack's `factory(root, config)`; see the module docstring for
    the two modes and the protocol."""

    def __init__(self, root: Path, config: Mapping[str, Any], recipe: Recipe) -> None:
        self.root = Path(root).resolve()
        self.recipe = recipe
        self.seed = int(config.get("seed") or 0)
        parts = COPY_PARTS if recipe.mode == "copy" else WORKTREE_PARTS
        self.workdir = self.root.joinpath(*parts)
        self.state_dir = self.root.joinpath(*STATE_PARTS)
        self.engine_dir = self.root.joinpath(*ENGINE_PARTS)
        self.refusals = 0
        self.last_result: dict[str, Any] = {"action": "START", "status": "ready"}
        self._closed = False
        self._build()
        self._data = self._observe()
        self._check_rule_paths()
        self._unit = self._completed(self._view())
        self._save_unit()

    # -- the observer ---------------------------------------------------------

    @property
    def observation(self) -> dict[str, Any]:
        view = self._view()
        won = self.recipe.win is not None and holds(self.recipe.win, view)
        lost = not won and self.recipe.lose is not None and holds(self.recipe.lose, view)
        return {
            "state": "WIN" if won else "GAME_OVER" if lost else "NOT_FINISHED",
            "levels_completed": self._completed(view, won=won),
            "win_levels": self._total(view),
            "available_actions": list(self.recipe.actions),
            "data": view,
        }

    @property
    def public_info(self) -> dict[str, Any]:
        return {"world": self.recipe.name, "archetype": self.recipe.archetype, "card": dict(self.recipe.card)}

    @property
    def session(self) -> SessionCapability:
        if self.recipe.mode == "worktree":
            return SessionCapability(replayable=False)
        return SessionCapability()

    # -- the actuator ---------------------------------------------------------

    def step(self, action: str, data: dict[str, Any] | None, reasoning: Mapping[str, Any] | None) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError(f"{self.recipe.name}: the world is closed")
        if action == "RESET":
            self._rewind()
        elif action in self.recipe.actions:
            self._act(action, data)
        else:
            raise ValueError(f"{self.recipe.name}: unknown action {action!r}")
        unit = self._completed(self._view())
        if unit != self._unit:
            self._unit = unit
            self._save_unit()
        return self.observation

    def finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        summary = {"world": self.recipe.name, "refusals": self.refusals, "data": self._view()}
        target = self.root / ".assay" / "world_summary.json"
        target.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")

    # -- the parts ------------------------------------------------------------

    def _build(self) -> None:
        if self.recipe.mode == "copy":
            for directory in (self.workdir, self.state_dir, self.engine_dir):
                if directory.exists():
                    shutil.rmtree(directory)
            self.workdir.mkdir(parents=True)
            self.state_dir.mkdir(parents=True)
            self.engine_dir.mkdir(parents=True)
            if self.recipe.source is not None:
                _copy_tree(self.recipe.source, self.workdir)
            if self.recipe.setup is not None:
                self._run(self.recipe.setup, "setup")
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.workdir.exists():
            assert self.recipe.source is not None
            _copy_tree(self.recipe.source, self.workdir)
            if self.recipe.setup is not None:
                self._run(self.recipe.setup, "setup")

    def _run(
        self, template: Sequence[str], step: str, *, action: str = "", stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        values = {
            "python": world_python(),
            "recipe": str(self.recipe.root),
            "archetypes": str(ARCHETYPES),
            "workdir": str(self.workdir),
            "state": str(self.state_dir),
            "seed": str(self.seed),
            "action": action,
        }
        argv = [PLACEHOLDER.sub(lambda match: values[match.group(1)], word) for word in template]
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            if stdin is None:
                completed = subprocess.run(
                    argv, cwd=self.workdir, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=self.recipe.timeout_seconds, env=environment, check=False,
                )
            else:
                completed = subprocess.run(
                    argv, cwd=self.workdir, input=stdin, capture_output=True, text=True,
                    timeout=self.recipe.timeout_seconds, env=environment, check=False,
                )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"{self.recipe.name}: the {step} command ran past {self.recipe.timeout_seconds:g} seconds"
            ) from error
        except OSError as error:
            raise RuntimeError(f"{self.recipe.name}: the {step} command could not start: {error}") from error
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout).strip()[-600:]
            raise RuntimeError(f"{self.recipe.name}: the {step} command exited {completed.returncode}: {tail}")
        return completed

    def _json_object(self, completed: subprocess.CompletedProcess[str], step: str) -> dict[str, Any]:
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"{self.recipe.name}: the {step} command printed something other than one JSON object: {error}"
            ) from error
        if not isinstance(value, dict):
            raise RuntimeError(f"{self.recipe.name}: the {step} command printed a {type(value).__name__}, not an object")
        return value

    def _observe(self) -> dict[str, Any]:
        data = self._json_object(self._run(self.recipe.observe, "observe"), "observe")
        clash = sorted(set(data) & set(ENGINE_KEYS))
        if clash:
            raise RuntimeError(f"{self.recipe.name}: the observe command used the engine's keys {clash}")
        return data

    def _act(self, action: str, params: Mapping[str, Any] | None) -> None:
        checkpoint = self._checkpoint()
        try:
            completed = self._run(
                self.recipe.act, "act", action=action, stdin=json.dumps(dict(params or {}), sort_keys=True)
            )
            result = self._json_object(completed, "act")
            status = result.get("status")
            if status not in RESULT_STATUSES:
                raise RuntimeError(
                    f"{self.recipe.name}: the act command's status is {status!r}, not one of {list(RESULT_STATUSES)}"
                )
            if "action" in result:
                raise RuntimeError(f"{self.recipe.name}: the act command may not set the result's action")
            data = self._observe()
        except BaseException:
            if checkpoint is not None:
                self._restore(checkpoint, self.workdir)
            raise
        finally:
            if checkpoint is not None and checkpoint.exists():
                shutil.rmtree(checkpoint)
        if status == "refused":
            self.refusals += 1
        self.last_result = {"action": action, **result}
        self._data = data

    def _rewind(self) -> None:
        if self.recipe.mode == "copy":
            self._restore(self._unit_dir(self._unit), self.workdir)
            detail = "the working copy is back where the current unit began"
        else:
            detail = "the working tree is the agent's and stays as it is"
        self._data = self._observe()
        self.last_result = {"action": "RESET", "status": "ok", "detail": detail}

    def _checkpoint(self) -> Path | None:
        if self.recipe.mode != "copy":
            return None
        target = self.engine_dir / "checkpoint"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.workdir, target, symlinks=True)
        return target

    def _restore(self, snapshot: Path, target: Path) -> None:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(snapshot, target, symlinks=True)

    def _unit_dir(self, unit: int) -> Path:
        return self.engine_dir / "units" / str(unit)

    def _save_unit(self) -> None:
        if self.recipe.mode != "copy":
            return
        target = self._unit_dir(self._unit)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.workdir, target, symlinks=True)

    def _view(self) -> dict[str, Any]:
        return {
            **self._data,
            "workdir": self.workdir.relative_to(self.root).as_posix(),
            "last_result": dict(self.last_result),
            "refusals": self.refusals,
        }

    def _check_rule_paths(self) -> None:
        view = self._view()
        paths = [
            *(condition_paths(self.recipe.win) if self.recipe.win is not None else []),
            *(condition_paths(self.recipe.lose) if self.recipe.lose is not None else []),
            *(spec["path"] for spec in (self.recipe.levels_completed, self.recipe.win_levels) if isinstance(spec, Mapping)),
        ]
        missing = [path for path in paths if read_path(view, path) is MISSING]
        if missing:
            raise RuntimeError(
                f"{self.recipe.name}: the recipe's rules read {missing}, which the first observation does not hold"
            )

    def _level_value(self, spec: Any, view: Mapping[str, Any], what: str) -> int:
        value = read_path(view, spec["path"])
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(f"{self.recipe.name}: {what} at {spec['path']!r} is {value!r}, not a count")
        return value

    def _completed(self, view: Mapping[str, Any], *, won: bool | None = None) -> int:
        spec = self.recipe.levels_completed
        if isinstance(spec, Mapping):
            return self._level_value(spec, view, "levels completed")
        if isinstance(spec, int):
            return spec
        if won is None:
            won = self.recipe.win is not None and holds(self.recipe.win, view)
        return 1 if won else 0

    def _total(self, view: Mapping[str, Any]) -> int:
        spec = self.recipe.win_levels
        if isinstance(spec, Mapping):
            return max(1, self._level_value(spec, view, "the level total"))
        if isinstance(spec, int):
            return spec
        return 1
