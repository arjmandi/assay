"""Addressable states: named readings of the observation.

A state is a name bound to a way of reading the observation, so that a rule
the agent holds has a referent code can compute. Three are built in: `goal`
(the world reports WIN), `level` (progress units completed) and
`budget_remaining` (paid actions left under the cap). The agent declares its
own, each once, as a dotted path into a dict observation or as an extractor
program, `def extract(obs) -> value`, that runs in the sandbox. A name cannot
be rebound, so a referent cannot drift. An outcome may only name a declared
state.

Declarations live in `states.json`, written by the daemon. An extractor's
last graded reading is kept there too, so the status shows it without
running a program.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import AssayError, sandbox
from .journal import Event, Paths, Run, is_paid, observation_of, read_json, write_json

BUILT_IN = ("goal", "level", "budget_remaining")
MAX_DECLARED = 16
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def declared(paths: Paths) -> dict[str, dict[str, Any]]:
    value = read_json(paths.states, {})
    return value if isinstance(value, dict) else {}


def known(paths: Paths) -> list[str]:
    return [*BUILT_IN, *sorted(declared(paths))]


def store_program(paths: Paths, reference: str, what: str) -> str:
    """Copy an agent-written file of the run directory under its hash, which
    is what runs from then on: a later edit of the file changes nothing
    already recorded. Returns the hash."""
    source = paths.root / reference
    if Path(reference).is_absolute() or paths.root.resolve() not in source.resolve().parents:
        raise AssayError(
            f"{what} takes a path inside the run directory, relative to it, got {reference!r}",
            code="PATH_INVALID",
        )
    try:
        body = source.read_bytes()
    except OSError:
        raise AssayError(f"{what} file not found: {reference}", code="FILE_NOT_FOUND") from None
    digest = hashlib.sha256(body).hexdigest()
    paths.programs.mkdir(parents=True, exist_ok=True)
    (paths.programs / f"{digest}.py").write_bytes(body)
    return digest


def declare(paths: Paths, name: str, *, path: str | None = None, file: str | None = None) -> dict[str, Any]:
    """Declare a state as exactly one of a dotted path or an extractor file."""

    def refuse(message: str, code: str = "STATE_DECLARE") -> AssayError:
        return AssayError(message, code=code)

    if not _NAME.fullmatch(name or "") or name in BUILT_IN:
        raise refuse(f"a state name matches {_NAME.pattern} and is not built in, got {name!r}")
    if bool(path) == bool(file):
        raise refuse("declare a state with exactly one of --path or --file")
    if path:
        spec: dict[str, Any] = {"form": "path", "path": ".".join(key for key in path.split(".") if key)}
        if not spec["path"]:
            raise refuse("--path needs dotted keys like counters.red")
    else:
        spec = {"form": "extractor", "hash": store_program(paths, str(file), "an extractor"), "source": file}
    states = declared(paths)
    existing = {key: value for key, value in states.get(name, {}).items() if key != "last"}
    if existing and existing != spec:
        raise refuse(
            f"state {name!r} is already declared differently; declare a new name "
            "instead of redefining a referent",
            "STATE_REDEFINED",
        )
    if not existing and len(states) >= MAX_DECLARED:
        raise refuse(f"at most {MAX_DECLARED} declared states per run", "STATE_CAP")
    states.setdefault(name, spec)
    write_json(paths.states, states)
    return spec


def _walk(observation: Any, dotted: str) -> tuple[bool, Any]:
    node = observation
    for key in dotted.split("."):
        if isinstance(node, Mapping) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return False, f"key {key!r} not in observation path {dotted!r}"
    return True, node


def read(run: Run, name: str, event: Event, *, remember: bool = False) -> tuple[bool, Any]:
    """The reading of one state on one event: `(True, value)`, or `(False,
    reason)` when it cannot be taken. `remember` keeps an extractor's reading
    for the status (the daemon sets it when it grades)."""
    if name == "goal":
        return True, event["state"] == "WIN"
    if name == "level":
        return True, event["levels_completed"]
    if name == "budget_remaining":
        if run.cap is None:
            return False, "budget_remaining needs a registered action cap"
        if "id" in event:
            spent = sum(1 for item in run.events[: event["id"] + 1] if is_paid(item))
        else:  # a pending event, graded before it is appended, counts itself
            spent = run.paid + is_paid(event)
        return True, max(0, run.cap - spent)
    states = declared(run.paths)
    spec = states.get(name)
    if spec is None:
        return False, f"state {name!r} is not declared"
    if spec["form"] == "path":
        if "frames" in event:
            return False, "a path state needs a dict observation; declare an extractor for frames"
        return _walk(event.get("observation"), spec["path"])
    ok, result = sandbox.run(run.paths.programs / f"{spec['hash']}.py", "extract", [observation_of(event)])
    if not ok:
        return False, f"extractor {result}"
    if remember:
        spec["last"] = {"event": event.get("id", len(run.events)), "value": result}
        write_json(run.paths.states, states)
    return True, result


def render(value: Any) -> str:
    text = json.dumps(value)
    return text if len(text) <= 40 else text[:39] + "…"


def lines(run: Run, event: Event, *, fresh: bool = False) -> list[str]:
    """The STATES block: every state with its reading on this event. A path
    state is read from the event; an extractor shows its last graded reading
    unless `fresh` asks for a run."""
    built_in = []
    for name in BUILT_IN:
        ok, value = read(run, name, event)
        built_in.append(f"{name}={render(value) if ok else 'n/a'}")
    output = ["STATES | built in: " + " · ".join(built_in)]
    shown = []
    for name, spec in sorted(declared(run.paths).items()):
        if spec["form"] == "path" or fresh:
            ok, value = read(run, name, event)
            shown.append(f"{name}={render(value) if ok else 'unreadable'} ({spec['form']})")
        elif "last" in spec:
            shown.append(f"{name}={render(spec['last']['value'])} @e{spec['last']['event']} (extractor, last graded)")
        else:
            shown.append(f"{name}=not yet graded (extractor)")
    if shown:
        output.append("STATES | declared: " + " · ".join(shown))
    return output


def changes(run: Run, before: Event, after: Event) -> list[str]:
    """The declared path states that read differently across a paid span."""
    output = []
    for name, spec in sorted(declared(run.paths).items()):
        if spec["form"] == "path":
            (was_ok, was), (now_ok, now) = read(run, name, before), read(run, name, after)
            if was_ok and now_ok and was != now:
                output.append(f"STATES | {name}: {render(was)} -> {render(now)}")
    return output
