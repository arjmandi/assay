"""The adapter contract (docs/ARCHITECTURE.md section 2.2) as Protocols.

An adapter is a module with `factory(root, config) -> session`, named on the
command line as `module:factory` or `/path/file.py:factory` and imported
inside the daemon. The session is the one object the kernel touches a world
through: `observation` is the observer, `step(action, data, reasoning)` the
actuator. Two members are optional and the daemon looks for them by name:
`finalize()`, called once on the first observation whose state is WIN, and
`public_info`, a dict stored in config.json at start. #21 adds the optional
`session` capability.

Protocols are not runtime-checkable for members that are not methods, so
`contract_problems` is the runtime check: the conformance test runs it over
every adapter in its table, and reports what is missing by name.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

FACTORY_PARAMETERS = ["root", "config"]
STEP_PARAMETERS = ["self", "action", "data", "reasoning"]


class Session(Protocol):
    """A world session: the observer and the actuator in one object. The
    observation is the dict shape (`state`, `levels_completed`, `win_levels`,
    `available_actions`, `data`) or the frame shape (`frame` or `frames`),
    normalized by `core.normalize_observation`."""

    @property
    def observation(self) -> Any: ...

    def step(
        self, action: str, data: dict[str, Any] | None, reasoning: Mapping[str, Any] | None
    ) -> Any: ...


class Finalizes(Protocol):
    """The optional `finalize`, called once when the state becomes WIN."""

    def finalize(self) -> None: ...


class Publishes(Protocol):
    """The optional `public_info`, stored in config.json at start."""

    @property
    def public_info(self) -> Mapping[str, Any]: ...


class Adapter(Protocol):
    """The factory the operator names: `factory(root, config) -> session`."""

    def __call__(self, root: Path, config: Mapping[str, Any]) -> Session: ...


def session_class(module: ModuleType, factory: Any) -> type | None:
    """The class the factory's return annotation names, when it names one
    the module defines."""
    try:
        returns = inspect.signature(factory).return_annotation
    except (TypeError, ValueError):
        return None
    name = returns if isinstance(returns, str) else getattr(returns, "__name__", None)
    if not isinstance(name, str):
        return None
    candidate = getattr(module, name.split(".")[-1], None)
    return candidate if isinstance(candidate, type) else None


def contract_problems(module: ModuleType) -> list[str]:
    """Every way the module falls short of the adapter contract, by name:
    the factory and its parameters, and, when the factory's return annotation
    names a class of the module, the session's `observation` property and
    `step(self, action, data, reasoning)`."""
    problems: list[str] = []
    factory = getattr(module, "factory", None)
    if not callable(factory):
        return ["no callable `factory`"]
    parameters = list(inspect.signature(factory).parameters)
    if parameters != FACTORY_PARAMETERS:
        problems.append(f"factory takes {parameters}, the contract is {FACTORY_PARAMETERS}")
    session = session_class(module, factory)
    if session is None:
        return problems
    if not isinstance(getattr(session, "observation", None), property):
        problems.append(f"{session.__name__}.observation is not a property")
    step = getattr(session, "step", None)
    if not callable(step):
        problems.append(f"{session.__name__}.step is not callable")
    else:
        step_parameters = list(inspect.signature(step).parameters)
        if step_parameters != STEP_PARAMETERS:
            problems.append(
                f"{session.__name__}.step takes {step_parameters}, the contract is {STEP_PARAMETERS}"
            )
    finalize = getattr(session, "finalize", None)
    if finalize is not None and not callable(finalize):
        problems.append(f"{session.__name__}.finalize is not callable")
    return problems
