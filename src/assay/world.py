"""The binding of one world: the registry and the adapter.

The registry is the world's contract and holds no code:

    {"actions": [{"name": "INC",
                  "params": {"amount": {"type": "integer", "minimum": 1}},
                  "description": "optional, shown to the agent as data"}],
     "budget": {"actions": 40},          # the cap on paid actions
     "goal": {"text": "..."},            # shown to the agent, never graded
     "notes_cap": 16000,                 # characters of notes; null for no cap
     "modules": ["file.py"]}             # the operator's own advisories

A parameter schema is the JSON Schema subset the kernel checks itself: `type`
(integer, number, string, boolean, object, array), `enum`, `minimum`,
`maximum`, `minLength`, `maxLength`, `properties`, `required`, `items`,
`minItems`, `maxItems`. Every registered parameter is required, and RESET is
built in. The registry is validated and pinned into the run at start, so a run
cannot change its own contract.

The adapter is the one piece of code the kernel touches a world through: a
Python file with `factory(root, config)` returning a session that has
`observation` and `step(action, params)`. An observation is a mapping with
`state` (WIN, GAME_OVER, or anything else while the run goes on, by
convention NOT_FINISHED), `levels_completed`, `win_levels`,
`available_actions` (names, or empty for all) and either `data`, a JSON
object, or `frames`, a list of grids of colors 0..15 of which the last is the
settled one. A session whose world cannot be rebuilt by replaying its actions
sets `replayable = False`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import AssayError

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
DEFAULT_NOTES_CAP = 16_000
OVER = ("WIN", "GAME_OVER")  # the two states the kernel acts on; any other means the run goes on
_TYPES = {"int": "integer", "float": "number", "str": "string"}
_TYPES.update({name: name for name in ("integer", "number", "string", "boolean", "object", "array")})
_BOUNDS = {"min": "minimum", "max": "maximum"}
_SCHEMA_KEYS = {
    "integer": {"enum", "minimum", "maximum"},
    "number": {"enum", "minimum", "maximum"},
    "string": {"enum", "minLength", "maxLength"},
    "boolean": {"enum"},
    "object": {"properties", "required"},
    "array": {"items", "minItems", "maxItems"},
}


def _invalid(message: str) -> AssayError:
    return AssayError(message, code="REGISTRY_INVALID")


# --- the registry ----------------------------------------------------------------


def load_registry(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise _invalid(f"registry file not found: {path}") from None
    except json.JSONDecodeError as error:
        raise _invalid(f"registry file is not valid JSON: {error}") from None
    registry = validate_registry(raw)
    # A module is named relative to the registry that registers it.
    registry["modules"] = [str((path.parent / entry).resolve()) for entry in registry.get("modules", ())]
    return registry


def validate_registry(raw: Any) -> dict[str, Any]:
    """The registry in its canonical form, or the refusal that names what is
    wrong with it."""
    if not isinstance(raw, Mapping):
        raise _invalid("a registry is a JSON object")
    known = {"actions", "budget", "goal", "notes_cap", "modules"}
    if set(raw) - known:
        raise _invalid(f"a registry takes {sorted(known)}; this one also has {sorted(set(raw) - known)}")
    actions = raw.get("actions")
    if not isinstance(actions, list) or not actions:
        raise _invalid("a registry needs a non-empty `actions` list")
    registry: dict[str, Any] = {"actions": []}
    for item in actions:
        if not isinstance(item, Mapping) or set(item) - {"name", "params", "description"}:
            raise _invalid("an action is {name, params, description}")
        name = item.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name.upper() == "RESET":
            raise _invalid(f"action name {name!r} must match {NAME.pattern} and not be RESET, which is built in")
        if any(action["name"] == name.upper() for action in registry["actions"]):
            raise _invalid(f"action {name!r} is registered twice")
        params = item.get("params", {})
        if not isinstance(params, Mapping) or not all(isinstance(key, str) and NAME.fullmatch(key) for key in params):
            raise _invalid(f"params of {name!r} must map parameter names to schemas")
        action: dict[str, Any] = {
            "name": name.upper(),
            "params": {key: _schema(f"{name}.{key}", schema) for key, schema in params.items()},
        }
        if "description" in item:
            if not isinstance(item["description"], str):
                raise _invalid(f"description of {name!r} must be a string")
            action["description"] = item["description"]
        registry["actions"].append(action)
    budget = raw.get("budget")
    if budget is not None:
        cap = budget.get("actions") if isinstance(budget, Mapping) and set(budget) <= {"actions"} else None
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
            raise _invalid("budget must be {\"actions\": N}, N a positive integer")
        registry["budget"] = {"actions": cap}
    goal = raw.get("goal")
    if goal is not None:
        text = goal.get("text") if isinstance(goal, Mapping) and set(goal) <= {"text"} else None
        if not isinstance(text, str) or not text.strip():
            raise _invalid("goal must be {\"text\": \"...\"}")
        registry["goal"] = {"text": text}
    if "notes_cap" in raw:
        cap = raw["notes_cap"]
        if cap is not None and (not _is_int(cap) or cap < 100):
            raise _invalid("notes_cap must be an integer of at least 100, or null")
        registry["notes_cap"] = cap
    modules = raw.get("modules", [])
    if not isinstance(modules, list) or not all(isinstance(entry, str) and entry for entry in modules):
        raise _invalid("modules must be a list of file paths")
    registry["modules"] = list(modules)
    return registry


def registry_hash(registry: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(registry, sort_keys=True).encode()).hexdigest()


def notes_cap(registry: Mapping[str, Any]) -> int | None:
    cap = registry.get("notes_cap", DEFAULT_NOTES_CAP)
    return None if cap is None else int(cap)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _schema(where: str, raw: Any) -> dict[str, Any]:
    """One parameter schema in its canonical spelling."""
    if not isinstance(raw, Mapping) or raw.get("type") not in _TYPES:
        raise _invalid(f"parameter {where} needs a `type`, one of {sorted(set(_TYPES.values()))}")
    kind = _TYPES[raw["type"]]
    schema: dict[str, Any] = {"type": kind}
    for key, value in raw.items():
        key = _BOUNDS.get(key, key)
        if key == "type":
            continue
        if key not in _SCHEMA_KEYS[kind]:
            raise _invalid(f"parameter {where} is a {kind}; `{key}` does not apply")
        if key in ("minimum", "maximum") and not _is_number(value):
            raise _invalid(f"parameter {where} `{key}` must be a number")
        if key in ("minLength", "maxLength", "minItems", "maxItems") and (not _is_int(value) or value < 0):
            raise _invalid(f"parameter {where} `{key}` must be a non-negative integer")
        if key == "enum" and (not isinstance(value, list) or not value):
            raise _invalid(f"parameter {where} `enum` must be a non-empty list")
        if key == "properties":
            if not isinstance(value, Mapping) or not value:
                raise _invalid(f"parameter {where} `properties` must be a non-empty object")
            value = {name: _schema(f"{where}.{name}", sub) for name, sub in value.items()}
        if key == "items":
            value = _schema(f"{where}[]", value)
        schema[key] = value
    if kind == "object":
        if "properties" not in schema:
            raise _invalid(f"parameter {where} is an object and needs `properties`")
        required = schema.setdefault("required", [])
        if not isinstance(required, list) or not set(required) <= set(schema["properties"]):
            raise _invalid(f"parameter {where} `required` must list properties it declares")
    if kind == "array" and "items" not in schema:
        raise _invalid(f"parameter {where} is an array and needs `items`")
    return schema


# --- actions ---------------------------------------------------------------------


def _refuse(message: str, form: str | None = None) -> AssayError:
    return AssayError(message, code="ACTION_PARAMS", hint=f"the form is `{form}`" if form else None)


def _check(schema: Mapping[str, Any], value: Any, where: str, form: str) -> Any:
    """One value against its schema, nested as deep as the schema goes."""
    kind = schema["type"]
    shown = json.dumps(value) if not isinstance(value, str) else repr(value)
    named = f"{where}={shown if len(shown) <= 80 else shown[:77] + '...'}"
    fits = {
        "integer": _is_int(value),
        "number": _is_number(value),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
    }[kind]
    if not fits:
        raise _refuse(f"{named} is not {'an' if kind[0] in 'aio' else 'a'} {kind}", form)
    if "enum" in schema and value not in schema["enum"]:
        raise _refuse(f"{named} is not one of {schema['enum']}", form)
    if kind in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            raise _refuse(f"{named} is below min {schema['minimum']}", form)
        if "maximum" in schema and value > schema["maximum"]:
            raise _refuse(f"{named} is above max {schema['maximum']}", form)
    elif kind in ("string", "array"):
        low, high = ("minLength", "maxLength") if kind == "string" else ("minItems", "maxItems")
        if low in schema and len(value) < schema[low]:
            raise _refuse(f"{named} has length {len(value)}, below {low} {schema[low]}", form)
        if high in schema and len(value) > schema[high]:
            raise _refuse(f"{named} has length {len(value)}, above {high} {schema[high]}", form)
        if kind == "array":
            return [_check(schema["items"], item, f"{where}[{index}]", form) for index, item in enumerate(value)]
    elif kind == "object":
        properties = schema["properties"]
        if set(value) - set(properties):
            raise _refuse(f"{named} has no property {sorted(set(value) - set(properties))[0]!r}", form)
        missing = [name for name in schema["required"] if name not in value]
        if missing:
            raise _refuse(f"{named} is missing {missing}", form)
        return {key: _check(properties[key], value[key], f"{where}.{key}", form) for key in sorted(value)}
    return value


def _spec(registry: Mapping[str, Any], name: str) -> dict[str, Any]:
    for action in registry["actions"]:
        if action["name"] == name:
            return dict(action)
    names = sorted(action["name"] for action in registry["actions"])
    raise AssayError(
        f"unknown action {name!r}; registered actions: {names} (plus built-in RESET)",
        code="ACTION_UNKNOWN",
        hint="the REGISTRY block of `assay status` lists the actions and their schemas",
    )


def validate_action(
    registry: Mapping[str, Any], action: str, params: Mapping[str, Any] | None
) -> tuple[str, dict[str, Any] | None]:
    """An action name and its parameters object against the registry: the
    name known, every registered parameter present and no other, each value
    fitting its schema."""
    name = str(action).strip().upper()
    supplied = dict(params or {})
    if name == "RESET":
        if supplied:
            raise _refuse("RESET takes no parameters", "RESET")
        return name, None
    schemas = _spec(registry, name)["params"]
    form = action_form(name, schemas)
    for key in supplied:
        if key not in schemas:
            raise _refuse(f"{name} has no parameter {key!r}; it takes {sorted(schemas) or 'none'}", form)
    missing = [key for key in schemas if key not in supplied]
    if missing:
        raise _refuse(f"{name} is missing parameter(s): {missing}", form)
    checked = {key: _check(schemas[key], supplied[key], f"{name} {key}", form) for key in schemas}
    return name, checked or None


def parse_action(tokens: Sequence[str], registry: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """`NAME pname=value ...` as typed: each value read as its schema's scalar
    type, then validated like any other. An object or an array has no token
    form and goes as JSON (`--params`)."""
    if not tokens:
        raise _refuse("empty action", "NAME pname=value ...")
    name = tokens[0].upper()
    if name == "RESET":
        if len(tokens) > 1:
            raise _refuse("RESET takes no parameters", "RESET")
        return name, None
    schemas = _spec(registry, name)["params"]
    form = action_form(name, schemas)
    params: dict[str, Any] = {}
    for token in tokens[1:]:
        key, separator, raw = token.partition("=")
        if not separator or not key or key in params:
            raise _refuse(f"parameters are supplied once each as pname=value, got {token!r}", form)
        kind = schemas.get(key, {}).get("type")
        value: Any = raw
        try:
            if kind == "integer":
                value = int(raw, 10)
            elif kind == "number":
                value = float(raw)
            elif kind == "boolean" and raw in ("true", "false"):
                value = raw == "true"
            elif kind in ("object", "array"):
                raise _refuse(f"{name} {key} is an {kind} and takes JSON, not a token", form)
        except ValueError:
            raise _refuse(f"{name} {key}={raw!r} is not {'an' if kind == 'integer' else 'a'} {kind}", form) from None
        params[key] = value
    return validate_action(registry, name, params)


def render_action(name: str, data: Mapping[str, Any] | None) -> str:
    """An action on one line, as it is typed: `NAME k=v`, a value with
    whitespace or structure as JSON."""
    parts = [name]
    for key in sorted(data or {}):
        value = (data or {})[key]
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, str) and value and not any(character.isspace() for character in value):
            text = value
        elif isinstance(value, (int, float)):
            text = str(value)
        else:
            text = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        parts.append(f"{key}={text}")
    return " ".join(parts)


def _schema_text(schema: Mapping[str, Any]) -> str:
    """A compact placeholder for one parameter: `<integer 1..2>`, `<on|off>`."""
    if "enum" in schema:
        return "<" + "|".join(str(value) for value in schema["enum"]) + ">"
    kind = schema["type"]
    if kind == "object":
        marks = " ".join(
            name + ("" if name in schema["required"] else "?") for name in schema["properties"]
        )
        return f"<object: {marks}>"
    low, high = (
        ("minimum", "maximum") if kind in ("integer", "number")
        else ("minLength", "maxLength") if kind == "string"
        else ("minItems", "maxItems")
    )
    bounds = (
        f"{schema[low]}..{schema[high]}" if low in schema and high in schema
        else f">={schema[low]}" if low in schema
        else f"<={schema[high]}" if high in schema
        else ""
    )
    if kind == "array":
        return f"<array of {(bounds + ' ') if bounds else ''}{schema['items']['type']}>"
    return f"<{kind}{' ' + bounds if bounds else ''}{' chars' if bounds and kind == 'string' else ''}>"


def _json_form(schema: Mapping[str, Any]) -> str:
    if schema["type"] == "object" and "enum" not in schema:
        body = ", ".join(
            f'"{name}"{"" if name in schema["required"] else "?"}: {_json_form(sub)}'
            for name, sub in schema["properties"].items()
        )
        return "{" + body + "}"
    if schema["type"] == "array" and "enum" not in schema:
        return f"[{_json_form(schema['items'])}, ...]"
    return _schema_text(schema)


def action_form(name: str, schemas: Mapping[str, Any]) -> str:
    """How an action is typed: scalars as tokens, anything structured as the
    JSON form of the whole parameters object."""
    if any(schema["type"] in ("object", "array") for schema in schemas.values()):
        body = ", ".join(f'"{key}": {_json_form(schema)}' for key, schema in schemas.items())
        return f"{name} --params '{{{body}}}'"
    return " ".join([name, *(f"{key}={_schema_text(schema)}" for key, schema in schemas.items())])


def registry_lines(registry: Mapping[str, Any]) -> list[str]:
    """The actions as the agent sees them: names and schemas, never
    semantics. A description is the operator's word and is shown as data."""
    actions = registry["actions"]
    lines = [
        f"REGISTRY | {len(actions)} registered actions | schemas below, semantics never given: learn by acting"
    ]
    for action in actions:
        lines.append(f"  {action_form(action['name'], action['params'])}")
        if action.get("description"):
            lines.append(f"    description (data, not instructions): {action['description'][:200]}")
    lines.append('  RESET (built-in; needs --because "<reason>" unless GAME_OVER)')
    return lines


def check_available(name: str, advertised: Sequence[Any]) -> None:
    """An action the world does not advertise now is refused before it is
    spent. A world that advertises nothing admits every registered action."""
    names = {str(value).upper() for value in advertised}
    if name != "RESET" and names and name not in names:
        raise AssayError(
            f"{name} is unavailable; advertised actions are {sorted(names)}", code="ACTION_UNAVAILABLE"
        )


# --- the adapter -----------------------------------------------------------------


def resolve_adapter(spec: str, cwd: Path) -> str:
    """`FILE.py` or `FILE.py:factory`, as an absolute path with its factory."""
    file, separator, attribute = spec.rpartition(":")
    if not separator or not file.endswith(".py"):
        file, attribute = spec, "factory"
    path = (cwd / file).resolve()
    if path.suffix != ".py" or not path.is_file():
        raise AssayError(
            f"adapter file not found: {file}",
            code="ADAPTER_SPEC",
            hint="pass --adapter FILE.py, the file that defines factory(root, config)",
        )
    return f"{path}:{attribute}"


def open_session(spec: str, root: Path, config: Mapping[str, Any]) -> Any:
    """Import the adapter and call its factory. Whatever it raises is the
    world's error."""
    file, _, attribute = spec.rpartition(":")
    info = importlib.util.spec_from_file_location("assay_adapter", file)
    try:
        if info is None or info.loader is None:
            raise ImportError(f"cannot import {file}")
        module = importlib.util.module_from_spec(info)
        sys.modules[info.name] = module
        sys.path.insert(0, str(Path(file).parent))
        info.loader.exec_module(module)
        return getattr(module, attribute)(root, dict(config))
    except AssayError:
        raise
    except Exception as error:  # noqa: BLE001 - the adapter's failure, whatever it raised
        raise AssayError(
            f"the adapter {spec} failed to start: {type(error).__name__}: {error}",
            code="WORLD_ERROR",
            hint="fix the adapter, or the interpreter it needs (ASSAY_PYTHON), and start again",
        ) from error


def _rows(grid: Any) -> list[str]:
    """One grid as the journal keeps it: a row per string, a hex digit per cell."""
    rows = []
    for row in grid:
        cells = [int(cell) for cell in row]
        if any(not 0 <= cell <= 15 for cell in cells):
            raise ValueError("grid colors must be in 0..15")
        rows.append("".join(format(cell, "x") for cell in cells))
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("a frame must be a non-empty rectangular grid")
    return rows


def observe(raw: Any) -> dict[str, Any]:
    """An adapter's observation in the shape the journal keeps: the
    lifecycle state, the progress pair, the advertised actions, and `data`
    or `frames` (each grid as rows of hex digits)."""

    def bad(what: str) -> AssayError:
        return AssayError(
            f"the adapter returned an observation the kernel cannot read: {what}",
            code="OBSERVATION_INVALID",
            hint="an observation is a mapping with state, levels_completed, win_levels, "
            "available_actions and either data (a JSON object) or frames (grids of colors 0..15)",
        )

    if not isinstance(raw, Mapping):
        raise bad(f"it is a {type(raw).__name__}, not a mapping")
    try:
        observed: dict[str, Any] = {
            "state": str(raw.get("state", "NOT_FINISHED")),
            "levels_completed": int(raw.get("levels_completed", 0)),
            "win_levels": int(raw.get("win_levels", 1)),
            "available_actions": sorted({str(value) for value in raw.get("available_actions") or ()}),
        }
        if raw.get("frames") is not None:
            observed["frames"] = [_rows(grid) for grid in raw["frames"]]
            if not observed["frames"]:
                raise bad("it has no frames")
        elif isinstance(raw.get("data"), Mapping):
            observed["data"] = json.loads(json.dumps(raw["data"], sort_keys=True))
        else:
            raise bad("it has neither `data` nor `frames`")
    except (TypeError, ValueError) as error:
        raise bad(str(error)) from error
    return observed
