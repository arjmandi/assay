"""The adapter contract (docs/ARCHITECTURE.md section 2.2) as Protocols.

An adapter is a module with `factory(root, config) -> session`, named on the
command line as `module:factory` or `/path/file.py:factory` and imported
inside the daemon. The session is the one object the kernel touches a world
through: `observation` is the observer, `step(action, data, reasoning)` the
actuator. Four members are optional and the daemon looks for them by name:
`finalize()`, called once on the first observation whose state is WIN;
`public_info`, a dict stored in config.json at start; `session`, the world's
declaration of its session rules (`SessionCapability`), read once after the
factory and stored in config.json at start, from which the kernel implements
the generic parts (the action-idle lease, replay or its absence, a reset on a
fresh progress unit); and `replay(transitions)`, called at a resume with every
recorded transition before the kernel steps them through the fresh session,
so a world that needs its own record of what it spent (a tick ledger) takes
it from the kernel's hand and reads no kernel file. A world that declares
nothing gets local semantics (`LOCAL_SEMANTICS`): no lease, a reset on a
fresh unit goes to the world, replay.

Protocols are not runtime-checkable for members that are not methods, so
`contract_problems` is the runtime check: the conformance test runs it over
every adapter in its table, and reports what is missing by name.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Protocol

from .core import REMOTE_MODE, AssayError, run_mode

FACTORY_PARAMETERS = ["root", "config"]
STEP_PARAMETERS = ["self", "action", "data", "reasoning"]
REPLAY_PARAMETERS = ["self", "transitions"]

RESET_ON_FRESH_UNIT = ("noop", "world")
# The lease the kernel applied to every remote run before the declaration
# existed; a config.json without a `session` record and with the legacy mode
# value is read under it, so such a run's status lines keep their lease text.
LEGACY_REMOTE_LEASE_SECONDS = 15 * 60


@dataclasses.dataclass(frozen=True, slots=True)
class SessionCapability:
    """What a world declares about its session, through the optional `session`
    property; the kernel implements each part from the declaration and the
    adapter implements none of them. Every field has the local default, so a
    world declares only what differs.

    `idle_lease_seconds`: the action-idle lease the world's session expires
    under, None for no lease. The kernel shows the time left on the MODE
    line and refuses a resume past it (`REMOTE_LEASE_EXPIRED`).
    `reset_on_fresh_unit`: what a RESET on a freshly entered progress unit
    (the start of the run, the unit after a RESET or an advance) does:
    `world`, the reset goes to the world like any action; `noop`, the kernel
    answers with the current observation and the world never sees it.
    `replayable`: whether a resume may replay the journal through a fresh
    session (`broker._replay_local_session`). A world that is not replayable
    lives one daemon long: a resume keeps the live daemon and checks its
    observation against the last event, and a dead daemon is
    `REMOTE_SESSION_UNAVAILABLE`; an observation it fails to return is the
    session gone, not a broken adapter."""

    idle_lease_seconds: int | None = None
    reset_on_fresh_unit: Literal["noop", "world"] = "world"
    replayable: bool = True

    def __post_init__(self) -> None:
        lease = self.idle_lease_seconds
        if lease is not None and (isinstance(lease, bool) or not isinstance(lease, int) or lease <= 0):
            raise ValueError(f"idle_lease_seconds must be a positive integer or None, got {lease!r}")
        if self.reset_on_fresh_unit not in RESET_ON_FRESH_UNIT:
            raise ValueError(
                f"reset_on_fresh_unit must be one of {list(RESET_ON_FRESH_UNIT)}, "
                f"got {self.reset_on_fresh_unit!r}"
            )
        if not isinstance(self.replayable, bool):
            raise ValueError(f"replayable must be true or false, got {self.replayable!r}")

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> SessionCapability:
        """The record as config.json stores it; a wrong shape is a TypeError
        or a ValueError naming the field, for the caller to wrap."""
        if not isinstance(obj, Mapping):
            raise TypeError(f"the session record must be a JSON object, got {type(obj).__name__}")
        unknown = sorted(set(obj) - {field.name for field in dataclasses.fields(cls)})
        if unknown:
            raise TypeError(f"the session record does not take {', '.join(unknown)}")
        return cls(**dict(obj))

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


LOCAL_SEMANTICS = SessionCapability()
LEGACY_REMOTE_SEMANTICS = SessionCapability(
    idle_lease_seconds=LEGACY_REMOTE_LEASE_SECONDS, replayable=False
)


@dataclasses.dataclass(frozen=True, slots=True)
class Transition:
    """One recorded paid action as the replay hook receives it: the action,
    the validated parameters the world's `step` took (None when the action
    takes none) and the observation the world returned, in the normalized
    shape the mutation log holds (`core.normalize_observation`)."""

    action: str
    params: dict[str, Any] | None
    observation: dict[str, Any]


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


class Declares(Protocol):
    """The optional `session`, the world's declaration of its session rules,
    read once after the factory and stored in config.json at start."""

    @property
    def session(self) -> SessionCapability: ...


class Replays(Protocol):
    """The optional `replay`, called at a resume of a replayable world with
    every recorded transition, before the kernel steps them through the
    fresh session; never on a fresh run."""

    def replay(self, transitions: Sequence[Transition]) -> None: ...


class Adapter(Protocol):
    """The factory the operator names: `factory(root, config) -> session`."""

    def __call__(self, root: Path, config: Mapping[str, Any]) -> Session: ...


def session_capability(session: Any) -> SessionCapability:
    """The session's declaration, or local semantics when it declares nothing.
    A `session` that is not a `SessionCapability`, or a mapping of its fields,
    is the adapter's fault at the boundary (`WORLD_ERROR`)."""
    declared = getattr(session, "session", None)
    if declared is None:
        return LOCAL_SEMANTICS
    if isinstance(declared, SessionCapability):
        return declared
    hint = (
        "fix the adapter's `session` property (docs/ARCHITECTURE.md section 2.2: a "
        "SessionCapability) and run `assay start WORLD_ID` again"
    )
    if isinstance(declared, Mapping):
        try:
            return SessionCapability.from_json(declared)
        except (TypeError, ValueError) as error:
            raise AssayError(
                f"the session's declaration does not fit: {error}", code="WORLD_ERROR", hint=hint
            ) from error
    raise AssayError(
        f"the session's declaration is a {type(declared).__name__}, not a SessionCapability",
        code="WORLD_ERROR",
        hint=hint,
    )


def recorded_capability(config: Mapping[str, Any]) -> SessionCapability:
    """The declaration config.json recorded at the run's start, for the
    processes that have no session (the command line, the status). A run
    without the record is from before the declaration existed: remote
    semantics under the legacy lease when its mode says remote, local
    semantics otherwise."""
    record = config.get("session")
    if record is None:
        return LEGACY_REMOTE_SEMANTICS if run_mode(config) == REMOTE_MODE else LOCAL_SEMANTICS
    try:
        return SessionCapability.from_json(record)
    except (TypeError, ValueError) as error:
        raise AssayError(
            f"config.json's session record is malformed: {error}",
            code="RECORD_CORRUPT",
            hint="the record is written by the daemon at the run's start and never edited",
        ) from error


def transitions_of(mutations: Sequence[Any]) -> tuple[Transition, ...]:
    """The recorded mutations as the replay hook receives them."""
    return tuple(
        Transition(
            action=str(mutation.action),
            params=None if mutation.data is None else dict(mutation.data),
            observation=dict(mutation.observation),
        )
        for mutation in mutations
    )


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
    names a class of the module, the session's `observation` property,
    `step(self, action, data, reasoning)`, and the optional members' shapes:
    `finalize` callable, `session` a property, `replay(self, transitions)`."""
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
    declared = getattr(session, "session", None)
    if declared is not None and not isinstance(declared, property):
        problems.append(f"{session.__name__}.session is not a property")
    replay = getattr(session, "replay", None)
    if replay is not None:
        if not callable(replay):
            problems.append(f"{session.__name__}.replay is not callable")
        else:
            replay_parameters = list(inspect.signature(replay).parameters)
            if replay_parameters != REPLAY_PARAMETERS:
                problems.append(
                    f"{session.__name__}.replay takes {replay_parameters}, "
                    f"the contract is {REPLAY_PARAMETERS}"
                )
    return problems
