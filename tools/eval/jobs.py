"""The job file: one JSON document that describes worlds times arms times
seeds, with every path relative to the file's own directory.

JSON, because every other contract file of the harness is JSON (the registry,
`config.json`, the journal), because an arm's registry variant is a JSON
fragment laid over a JSON registry, and because the orchestrator that ran the
paper's experiments took JSON job files, so the E1 design ported without
translation. What TOML would have added is comments; the `description` fields
carry those.

The keys:

    name            the design's name; job ids and the state directory carry it
    description     prose, ignored by the runner
    adapter         `file.py:factory`, relative to the job file; per world too
    concurrency     players at a time (default 1)
    max_sessions    launches per job before it is reported as it stands (default 3)
    max_hours       wall clock per session (default 4)
    player          command: the argv template, with the placeholders below;
                    model: the model id the template's {model} takes
    worlds          id, levels, budget (the paid-action cap the registry must
                    carry), registry (relative path), adapter (optional)
    arms            name, prompt (a template, relative path), constitution
                    (optional, relative path), registry (optional override),
                    registry_overlay (optional: keys laid over the registry
                    and written out as the arm's variant), model (optional)
    seeds           integers; a seed is a fresh run directory, nothing else
                    (the kernel's own `--seed` is passed only when a world
                    sets `pass_seed`)

One job per (seed, world, arm), in that nesting, which is the queue order the
protocols fixed: every arm of a world at one seed before the next world, and
the whole first seed before the second. The id is `<name>-<world>-<arm>-s<seed>`.

Command placeholders: {model} {prompt} {prompt_file} {run_dir} {world} {job}
{seed} {assay} {repo} (the checkout the runner lives in) {python} (the
interpreter running the runner). A relative path in the command is relative
to the run directory, the player's working directory, so a player in the
tree is named through {repo}. The rendered prompt is also fed to the player
on stdin.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

from . import EvalError

COMMAND_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
COMMAND_PLACEHOLDERS = frozenset(
    {"model", "prompt", "prompt_file", "run_dir", "world", "job", "seed", "assay", "repo", "python"}
)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclasses.dataclass(frozen=True)
class World:
    id: str
    levels: int
    budget: int
    registry: Path
    adapter: str
    pass_seed: bool


@dataclasses.dataclass(frozen=True)
class Arm:
    name: str
    prompt: Path
    constitution: Path | None
    registry: Path | None
    registry_overlay: dict[str, Any] | None
    model: str | None
    description: str


@dataclasses.dataclass(frozen=True)
class Job:
    id: str
    world: World
    arm: Arm
    seed: int
    model: str

    @property
    def registry(self) -> Path:
        """The registry file the arm starts from (the arm's own, else the world's)."""
        return self.arm.registry or self.world.registry

    @property
    def overlay(self) -> dict[str, Any] | None:
        return self.arm.registry_overlay

    @property
    def budget(self) -> int:
        return self.world.budget

    @property
    def inputs(self) -> list[tuple[str, Path]]:
        """Every file the job reads before it spends anything, labelled."""
        files = [
            ("adapter", Path(self.world.adapter.rpartition(":")[0])),
            ("registry", self.registry),
            ("prompt", self.arm.prompt),
        ]
        if self.arm.constitution is not None:
            files.append(("constitution", self.arm.constitution))
        return files


@dataclasses.dataclass(frozen=True)
class Design:
    name: str
    path: Path
    description: str
    command: tuple[str, ...]
    model: str
    concurrency: int
    max_sessions: int
    max_hours: float
    worlds: tuple[World, ...]
    arms: tuple[Arm, ...]
    seeds: tuple[int, ...]
    jobs: tuple[Job, ...]

    def job(self, job_id: str) -> Job:
        for job in self.jobs:
            if job.id == job_id:
                return job
        raise KeyError(job_id)


def _relative_path(base: Path, key: str, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise EvalError(f"{key} must be a relative path, got {value!r}")
    if value.startswith(("/", "~")) or Path(value).is_absolute():
        raise EvalError(
            f"{key} must be a relative path, resolved against the job file's "
            f"directory, got {value!r}"
        )
    return (base / value).resolve()


def _adapter_spec(base: Path, key: str, value: Any) -> str:
    """`file.py:factory` with the file resolved; a module spec is refused, since
    a job file names what the operator can see beside it."""
    if not isinstance(value, str) or ":" not in value:
        raise EvalError(f"{key} must be `file.py:factory`, got {value!r}")
    file_part, _, factory = value.rpartition(":")
    if not factory or not file_part.endswith(".py"):
        raise EvalError(f"{key} must be `file.py:factory`, got {value!r}")
    return f"{_relative_path(base, key, file_part)}:{factory}"


def _int(mapping: dict[str, Any], key: str, *, default: int | None = None, minimum: int = 1) -> int:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvalError(f"{key} must be an integer, got {value!r}")
    if value < minimum:
        raise EvalError(f"{key} must be at least {minimum}, got {value}")
    return value


def _name(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not NAME.match(value):
        raise EvalError(
            f"{key} must be a name of letters, digits, dots, dashes and "
            f"underscores, got {value!r}"
        )
    return value


def _command(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise EvalError("player.command must be a non-empty list of strings")
    unknown = {
        name for item in value for name in COMMAND_PLACEHOLDER.findall(item)
    } - COMMAND_PLACEHOLDERS
    if unknown:
        raise EvalError(
            f"player.command uses unknown placeholder(s) {sorted(unknown)}; "
            f"known: {sorted(COMMAND_PLACEHOLDERS)}"
        )
    return tuple(value)


def _world(base: Path, raw: Any, default_adapter: str | None) -> World:
    if not isinstance(raw, dict):
        raise EvalError(f"each world must be an object, got {raw!r}")
    world_id = _name(raw, "id")
    adapter = raw.get("adapter")
    if adapter is None:
        if default_adapter is None:
            raise EvalError(f"world {world_id}: no adapter, and the job file names none")
        spec = default_adapter
    else:
        spec = _adapter_spec(base, f"world {world_id}: adapter", adapter)
    pass_seed = raw.get("pass_seed", False)
    if not isinstance(pass_seed, bool):
        raise EvalError(f"world {world_id}: pass_seed must be true or false")
    return World(
        id=world_id,
        levels=_int(raw, "levels", default=1),
        budget=_int(raw, "budget"),
        registry=_relative_path(base, f"world {world_id}: registry", raw.get("registry")),
        adapter=spec,
        pass_seed=pass_seed,
    )


def _arm(base: Path, raw: Any) -> Arm:
    if not isinstance(raw, dict):
        raise EvalError(f"each arm must be an object, got {raw!r}")
    name = _name(raw, "name")
    overlay = raw.get("registry_overlay")
    if overlay is not None and not isinstance(overlay, dict):
        raise EvalError(f"arm {name}: registry_overlay must be an object")
    model = raw.get("model")
    if model is not None and not isinstance(model, str):
        raise EvalError(f"arm {name}: model must be a string")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise EvalError(f"arm {name}: description must be a string")
    return Arm(
        name=name,
        prompt=_relative_path(base, f"arm {name}: prompt", raw.get("prompt")),
        constitution=(
            _relative_path(base, f"arm {name}: constitution", raw["constitution"])
            if raw.get("constitution") is not None
            else None
        ),
        registry=(
            _relative_path(base, f"arm {name}: registry", raw["registry"])
            if raw.get("registry") is not None
            else None
        ),
        registry_overlay=dict(overlay) if overlay else None,
        model=model,
        description=description,
    )


def load_design(path: Path) -> Design:
    """Parse and validate a job file. Reads nothing but the file itself; the
    inputs it names are checked by `missing_inputs` (the dry run reports them,
    a live run refuses to launch a job until they exist)."""
    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise EvalError(f"no job file at {path}") from None
    except json.JSONDecodeError as error:
        raise EvalError(f"{path} is not valid JSON: {error}") from None
    if not isinstance(raw, dict):
        raise EvalError(f"{path} must hold one JSON object")
    base = path.parent
    name = _name(raw, "name")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise EvalError("description must be a string")
    player = raw.get("player")
    if not isinstance(player, dict):
        raise EvalError("player must be an object with command and model")
    command = _command(player.get("command"))
    model = player.get("model", "")
    if not isinstance(model, str):
        raise EvalError("player.model must be a string")
    default_adapter = (
        _adapter_spec(base, "adapter", raw["adapter"]) if raw.get("adapter") is not None else None
    )
    worlds_raw, arms_raw, seeds_raw = raw.get("worlds"), raw.get("arms"), raw.get("seeds")
    if not isinstance(worlds_raw, list) or not worlds_raw:
        raise EvalError("worlds must be a non-empty list")
    if not isinstance(arms_raw, list) or not arms_raw:
        raise EvalError("arms must be a non-empty list")
    if (
        not isinstance(seeds_raw, list)
        or not seeds_raw
        or not all(isinstance(seed, int) and not isinstance(seed, bool) for seed in seeds_raw)
    ):
        raise EvalError("seeds must be a non-empty list of integers")
    worlds = tuple(_world(base, item, default_adapter) for item in worlds_raw)
    arms = tuple(_arm(base, item) for item in arms_raw)
    seeds = tuple(seeds_raw)
    for label, items in (("world ids", [w.id for w in worlds]), ("arm names", [a.name for a in arms]), ("seeds", list(seeds))):
        if len(items) != len(set(items)):
            raise EvalError(f"duplicate {label} in {path.name}")
    max_hours = raw.get("max_hours", 4)
    if isinstance(max_hours, bool) or not isinstance(max_hours, (int, float)) or max_hours <= 0:
        raise EvalError(f"max_hours must be a positive number, got {max_hours!r}")
    jobs = tuple(
        Job(
            id=f"{name}-{world.id}-{arm.name}-s{seed}",
            world=world,
            arm=arm,
            seed=seed,
            model=arm.model or model,
        )
        for seed in seeds
        for world in worlds
        for arm in arms
    )
    return Design(
        name=name,
        path=path,
        description=description,
        command=command,
        model=model,
        concurrency=_int(raw, "concurrency", default=1),
        max_sessions=_int(raw, "max_sessions", default=3),
        max_hours=float(max_hours),
        worlds=worlds,
        arms=arms,
        seeds=seeds,
        jobs=jobs,
    )


def missing_inputs(design: Design) -> list[tuple[str, str, Path]]:
    """(job id, label, path) for every input file a job names that does not
    exist, in queue order, each path once."""
    missing: list[tuple[str, str, Path]] = []
    seen: set[Path] = set()
    for job in design.jobs:
        for label, file in job.inputs:
            if file in seen:
                continue
            if not file.is_file():
                seen.add(file)
                missing.append((job.id, label, file))
    return missing


def registry_variant(job: Job) -> dict[str, Any] | None:
    """The arm's registry with the overlay laid over its top level, or None
    when the arm has no overlay (the file is used as it is)."""
    if job.overlay is None:
        return None
    try:
        base = json.loads(job.registry.read_text())
    except FileNotFoundError:
        raise EvalError(f"{job.id}: registry {job.registry} does not exist") from None
    except json.JSONDecodeError as error:
        raise EvalError(f"{job.id}: registry {job.registry} is not valid JSON: {error}") from None
    if not isinstance(base, dict):
        raise EvalError(f"{job.id}: registry {job.registry} must hold one JSON object")
    return {**base, **job.overlay}


def check_budget(job: Job) -> None:
    """The registry's paid-action cap must be the job's budget: the prompt
    tells the player the cap, and the two must not disagree."""
    try:
        registry = json.loads(job.registry.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    if not isinstance(registry, dict):
        return
    budget = registry.get("budget")
    cap = budget.get("actions") if isinstance(budget, dict) else None
    if isinstance(cap, int) and not isinstance(cap, bool) and cap != job.budget:
        raise EvalError(
            f"{job.id}: the job's budget is {job.budget} but {job.registry.name} "
            f"caps paid actions at {cap}; the two must agree"
        )


def variant_name(job: Job) -> str:
    """The file name of a materialized registry variant: the arm and the base
    registry's stem, so two worlds sharing both share the file."""
    return f"{job.arm.name}-{job.registry.stem}.json"
