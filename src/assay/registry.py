"""Registry-driven general actions with a hard action budget.

Every run starts with `assay start WORLD_ID --registry file.json --adapter
mod:factory`: the registry names the actions and their typed parameter
schemas, and RESET is built in. Semantics are never part of the registry; the
agent learns them by acting.

Registry JSON schema (v2: every key beyond "actions" is optional; v1 files
stay valid unchanged):

    {
      "actions": [
        {"name": "TOKEN",
         "params": {"pname": <schema>, ...},   # a JSON Schema subset, below
         "destructive": bool?,      # kernel teeth: refuses without a declared
                                    # worst case + recovery plan; banned in batches
         "approval": bool?,         # default-deny: needs a fresh owner approval
         "liveness": "live"|"sim"?, # live actuation may demand rehearsal first
         "rehearsal_quota": N?,     # graded sim attempts required before live use
         "description": "text"?}    # admissible, UNTRUSTED; rendered as data;
                                    # withheld entirely in zero-prior mode
      ],
      "budget": {"actions": N, "usd": X?},  # hard caps at the boundary
      "goal": {"text": "..."},        # the standing goal, re-presented in status
      "batching": {"hand_cap": N|null},  # hand-written batch step cap; null =
                                         # uncapped; DEFAULT 3 when absent (the
                                         # batching law); model-fit plans lift it
      "notes_cap": N|null,            # notes size cap in chars (default 16000)
      "status_budget": N,             # tokens one status may print (an estimate,
                                      # characters over four); the renderer drops
                                      # the lowest-value blocks first and names them
      "zero_prior": bool,             # withhold action descriptions
      "modules": ["path.py", ...],    # behavior modules, pack-tier trust
      "module_modes": {"name": "off"|"advise"|"block"},
      "secrets": ["ENV_NAME", ...],   # env values redacted at the journal boundary
      "observers": [...],             # DECLARED ONLY in 1.2.0 (journaled, inert)
      "control": {...},               # DECLARED ONLY in 1.2.0: journaled and pinned,
                                      # inert for the kernel; a world adapter may
                                      # read it from the pinned copy (a mode switch)
      "mode_note": "free text",       # optional, shown in status (data only)
      "gate": "required"|"optional"|"off"   # CONTROL-ARM SWITCH (default
                                      # required). optional: act/commit steps may
                                      # omit their prediction; a bare one is journaled
                                      # UNGATED. off: the instrument is removed, no
                                      # prediction is accepted or graded, every paid
                                      # action is journaled UNGATED. Under both the
                                      # audit keeps the run invalid for scoring.
    }

A parameter schema is a JSON Schema subset, nested as deep as the world
needs and holding nothing the kernel cannot check (`validate_value`):

    {"type": "integer"|"number"|"string"|"boolean"|"object"|"array",
     "enum": [values]?,                        # any type; each member fits the schema
     "minimum": number?, "maximum": number?,   # integer and number
     "minLength": N?, "maxLength": N?,         # string
     "properties": {"name": <schema>, ...},    # object; no other property is admitted
     "required": ["name", ...]?,               # object; absent means none is required
     "additionalProperties": false?,           # object; the one admitted value
     "items": <schema>,                        # array; the schema of every item
     "minItems": N?, "maxItems": N?}           # array

`int`, `float` and `str` are accepted as aliases of `integer`, `number` and
`string`, and `min`/`max` of `minimum`/`maximum`: a registry written for
1.1.0 is valid unchanged, and the pinned copy keeps the spelling the file
used.

Actions on the command line: `NAME pname=value ...` for scalar parameters,
`NAME --params '{"pname": value, ...}'` (or `--params @FILE`) for any
parameter and the only form for an object or an array. Every registered
parameter is required; values are type-coerced and validated (bounds, enum,
lengths, items, properties) before any action is spent. RESET stays
built-in.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core import AssayError, RunPaths, one_line_json, read_json
from .records import Event

if TYPE_CHECKING:
    from .run import Run

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
# The parameter types of the schema subset: the JSON Schema names, and the
# three names the 1.1.0 registries used as aliases (the pinned copy keeps
# the spelling the file used, so a registry's hash never moves).
_TYPE_ALIASES = {"int": "integer", "float": "number", "str": "string"}
_TYPES = ("object", "array", "string", "number", "integer", "boolean")
_KEYS_BY_TYPE: dict[str, frozenset[str]] = {
    "integer": frozenset({"type", "enum", "minimum", "maximum", "min", "max"}),
    "number": frozenset({"type", "enum", "minimum", "maximum", "min", "max"}),
    "string": frozenset({"type", "enum", "minLength", "maxLength"}),
    "boolean": frozenset({"type", "enum"}),
    "object": frozenset({"type", "enum", "properties", "required", "additionalProperties"}),
    "array": frozenset({"type", "enum", "items", "minItems", "maxItems"}),
}
_SCHEMA_KEYS = frozenset().union(*_KEYS_BY_TYPE.values())
# A refused value is named up to this many characters; a program or a list
# of spans is clipped, a scalar never is.
SHOWN_WIDTH = 80
# A schema nests at most this deep below its parameter (an array's items,
# an object's property, each one step of depth); the limit bounds the
# validator's recursion on a value as well, since a value is only ever
# checked along its schema.
SCHEMA_DEPTH_LIMIT = 32
_TOP_KEYS = {
    "actions",
    "budget",
    "mode_note",
    "goal",
    "batching",
    "notes_cap",
    "status_budget",
    "zero_prior",
    "modules",
    "module_modes",
    "secrets",
    "observers",
    "control",
    "gate",
}
_ACTION_KEYS = {
    "name",
    "params",
    "destructive",
    "approval",
    "liveness",
    "rehearsal_quota",
    "description",
}
MODULE_MODES = ("off", "advise", "block")
# A built-in a registry may still name by the word it carried before 1.2.0.
RENAMED_MODULES = {"sharpness": "specificity"}
_GATES = ("required", "optional", "off")

DEFAULT_HAND_CAP = 3       # the batching law's kernel default; registry-overridable
DEFAULT_NOTES_CAP = 16_000  # chars; generous enough that compliant runs never see it
BUDGET_HINT = (
    "`assay audit` gives the verdict and `assay export` the knowledge file for the next run"
)
SCHEMA_HINT = "the REGISTRY block of `assay status` lists the actions and their schemas"
STATUS_BUDGET_HINT = (
    'the form is `"status_budget": N`, N a positive count of tokens (an estimate, characters '
    "over four) every status renders under; leave it out for no budget"
)
TOKEN_FORM_HINT = "the form is `NAME pname=value ...`"


def load_registry_file(path: Path | str) -> dict[str, Any]:
    """Read and validate a registry JSON file into its canonical form."""
    source = Path(path)
    try:
        raw = json.loads(source.read_text())
    except FileNotFoundError as error:
        raise AssayError(f"registry file not found: {source}", code="REGISTRY_INVALID") from error
    except json.JSONDecodeError as error:
        raise AssayError(f"registry file is not valid JSON: {error}", code="REGISTRY_INVALID") from error
    return validate_registry(raw)


def validate_registry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AssayError("registry must be a JSON object", code="REGISTRY_INVALID")
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise AssayError(f"registry has unknown keys {sorted(unknown)}", code="REGISTRY_INVALID")
    actions = raw.get("actions")
    if not isinstance(actions, list) or not actions:
        raise AssayError("registry needs a non-empty 'actions' list", code="REGISTRY_INVALID")
    canonical_actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(actions):
        if not isinstance(item, Mapping):
            raise AssayError(f"registry action {index} must be an object", code="REGISTRY_INVALID")
        extra = set(item) - _ACTION_KEYS
        if extra:
            raise AssayError(
                f"registry action {index} has unknown keys {sorted(extra)}",
                code="REGISTRY_INVALID",
            )
        name = item.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise AssayError(
                f"registry action {index} needs a name matching {_NAME.pattern}",
                code="REGISTRY_INVALID",
            )
        token = name.upper()
        if token == "RESET":
            raise AssayError("RESET is built-in and cannot be registered", code="REGISTRY_INVALID")
        if token in seen:
            raise AssayError(f"registry action {name!r} is registered twice", code="REGISTRY_INVALID")
        seen.add(token)
        params_raw = item.get("params", {})
        if not isinstance(params_raw, Mapping):
            raise AssayError(f"params of {name!r} must be an object", code="REGISTRY_INVALID")
        params: dict[str, dict[str, Any]] = {}
        for pname, schema in params_raw.items():
            if not isinstance(pname, str) or not _NAME.fullmatch(pname):
                raise AssayError(f"{name!r} has an invalid parameter name {pname!r}", code="REGISTRY_INVALID")
            params[pname] = _validate_param(name, pname, schema)
        canonical: dict[str, Any] = {"name": token, "params": params}
        for flag in ("destructive", "approval"):
            if flag in item:
                if not isinstance(item[flag], bool):
                    raise AssayError(f"{name!r} {flag} must be true or false", code="REGISTRY_INVALID")
                if item[flag]:
                    canonical[flag] = True
        if "liveness" in item:
            if item["liveness"] not in ("live", "sim"):
                raise AssayError(f"{name!r} liveness must be 'live' or 'sim'", code="REGISTRY_INVALID")
            canonical["liveness"] = item["liveness"]
        if "rehearsal_quota" in item:
            quota = item["rehearsal_quota"]
            if not isinstance(quota, int) or isinstance(quota, bool) or quota < 1:
                raise AssayError(f"{name!r} rehearsal_quota must be a positive integer", code="REGISTRY_INVALID")
            if canonical.get("liveness") != "live":
                raise AssayError(
                    f"{name!r} rehearsal_quota applies only to liveness 'live' actions",
                    code="REGISTRY_INVALID",
                )
            canonical["rehearsal_quota"] = quota
        if "description" in item:
            if not isinstance(item["description"], str):
                raise AssayError(f"{name!r} description must be a string", code="REGISTRY_INVALID")
            canonical["description"] = item["description"]
        canonical_actions.append(canonical)
    output: dict[str, Any] = {"actions": canonical_actions}
    budget = raw.get("budget")
    if budget is not None:
        if not isinstance(budget, Mapping) or set(budget) - {"actions", "usd"}:
            raise AssayError("registry budget must be {'actions': N, 'usd': X?}", code="REGISTRY_INVALID")
        entry: dict[str, Any] = {}
        cap = budget.get("actions")
        if cap is not None:
            if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
                raise AssayError("budget.actions must be a positive integer", code="REGISTRY_INVALID")
            entry["actions"] = cap
        usd = budget.get("usd")
        if usd is not None:
            if isinstance(usd, bool) or not isinstance(usd, (int, float)) or usd <= 0:
                raise AssayError("budget.usd must be a positive number", code="REGISTRY_INVALID")
            entry["usd"] = float(usd)
        if entry:
            output["budget"] = entry
    goal = raw.get("goal")
    if goal is not None:
        if (
            not isinstance(goal, Mapping)
            or set(goal) - {"text"}
            or not isinstance(goal.get("text"), str)
            or not goal["text"].strip()
        ):
            raise AssayError("registry goal must be {'text': '<non-empty>'}", code="REGISTRY_INVALID")
        output["goal"] = {"text": goal["text"]}
    batching = raw.get("batching")
    if batching is not None:
        if not isinstance(batching, Mapping) or set(batching) - {"hand_cap"}:
            raise AssayError("registry batching must be {'hand_cap': N or null}", code="REGISTRY_INVALID")
        cap = batching.get("hand_cap")
        if cap is not None and (
            not isinstance(cap, int) or isinstance(cap, bool) or cap < 1
        ):
            raise AssayError("batching.hand_cap must be a positive integer or null", code="REGISTRY_INVALID")
        output["batching"] = {"hand_cap": cap}
    if "notes_cap" in raw:
        cap = raw["notes_cap"]
        if cap is not None and (
            not isinstance(cap, int) or isinstance(cap, bool) or cap < 100
        ):
            raise AssayError("notes_cap must be an integer >= 100, or null", code="REGISTRY_INVALID")
        output["notes_cap"] = cap
    if "status_budget" in raw:
        tokens = raw["status_budget"]
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 1:
            raise AssayError(
                "status_budget must be a positive integer (tokens)",
                code="REGISTRY_INVALID",
                hint=STATUS_BUDGET_HINT,
            )
        output["status_budget"] = tokens
    if "zero_prior" in raw:
        if not isinstance(raw["zero_prior"], bool):
            raise AssayError("zero_prior must be true or false", code="REGISTRY_INVALID")
        output["zero_prior"] = raw["zero_prior"]
    modules = raw.get("modules")
    if modules is not None:
        if not isinstance(modules, list) or not all(
            isinstance(entry, str) and entry for entry in modules
        ):
            raise AssayError("modules must be a list of file paths", code="REGISTRY_INVALID")
        output["modules"] = list(modules)
    modes = raw.get("module_modes")
    if modes is not None:
        if not isinstance(modes, Mapping) or not all(
            isinstance(key, str) and value in MODULE_MODES
            for key, value in modes.items()
        ):
            raise AssayError(
                f"module_modes must map module names to one of {list(MODULE_MODES)}",
                code="REGISTRY_INVALID",
            )
        output["module_modes"] = module_modes(raw)
    secrets = raw.get("secrets")
    if secrets is not None:
        if not isinstance(secrets, list) or not all(
            isinstance(entry, str) and entry for entry in secrets
        ):
            raise AssayError("secrets must be a list of environment variable NAMES", code="REGISTRY_INVALID")
        output["secrets"] = list(secrets)
    for declared_only in ("observers", "control"):
        if declared_only in raw:
            value = raw[declared_only]
            if not isinstance(value, (list, Mapping)):
                raise AssayError(f"{declared_only} must be a JSON array or object", code="REGISTRY_INVALID")
            # 1.2.0: accepted and journaled, no runtime behavior (honest gap).
            output[declared_only] = json.loads(json.dumps(value))
    if "gate" in raw:
        if raw["gate"] not in _GATES:
            raise AssayError(f"gate must be one of {list(_GATES)}", code="REGISTRY_INVALID")
        output["gate"] = raw["gate"]
    note = raw.get("mode_note")
    if note is not None:
        if not isinstance(note, str):
            raise AssayError("mode_note must be a string", code="REGISTRY_INVALID")
        output["mode_note"] = note
    return output


def action_spec(
    registry: Mapping[str, Any], name: str
) -> Mapping[str, Any] | None:
    """The canonical spec of one registered action (None for RESET/unknown)."""
    for item in registry.get("actions", ()):
        if item["name"] == name.upper():
            spec: Mapping[str, Any] = item
            return spec
    return None


def hand_cap(registry: Mapping[str, Any] | None) -> int | None:
    """The batching law's hand-written-batch cap; None means uncapped."""
    if not registry:
        return None
    batching = registry.get("batching")
    if batching is not None and "hand_cap" in batching:
        cap = batching["hand_cap"]
        return None if cap is None else int(cap)
    return DEFAULT_HAND_CAP


def notes_cap(registry: Mapping[str, Any] | None) -> int | None:
    """The notes size cap in characters; None disables it."""
    if not registry:
        return None
    if "notes_cap" in registry:
        cap = registry["notes_cap"]
        return None if cap is None else int(cap)
    return DEFAULT_NOTES_CAP


def status_budget(registry: Mapping[str, Any] | None) -> int | None:
    """The token budget every status renders under (docs/ARCHITECTURE.md
    section 7.6); None when the registry sets none."""
    if not registry or "status_budget" not in registry:
        return None
    return int(registry["status_budget"])


def zero_prior(registry: Mapping[str, Any] | None) -> bool:
    return bool(registry and registry.get("zero_prior"))


def module_modes(registry: Mapping[str, Any] | None) -> dict[str, str]:
    """The per-module modes under the built-ins' current names. `sharpness`,
    the name the specificity module carried before 1.2.0, is read as
    `specificity`, so a registry written or pinned under the old name keeps
    its mode; a registry naming both keeps the current name's mode."""
    modes = (registry or {}).get("module_modes") or {}
    output = {RENAMED_MODULES[key]: str(mode) for key, mode in modes.items() if key in RENAMED_MODULES}
    output.update((str(key), str(mode)) for key, mode in modes.items() if key not in RENAMED_MODULES)
    return output


def gate_mode(registry: Mapping[str, Any] | None) -> str:
    """The registry's gate mode: `required` (the rule, and the default),
    `optional` (a prediction may be omitted, a bare act is journaled UNGATED)
    or `off` (the instrument is removed: no prediction is accepted or graded,
    every paid action is journaled UNGATED). The two relaxed modes are the
    control-arm switches; under either the audit keeps the run invalid for
    scoring. Neither is a scorable mode."""
    value = (registry or {}).get("gate") if registry else None
    return str(value) if value in _GATES else "required"


def gate_optional(registry: Mapping[str, Any] | None) -> bool:
    return gate_mode(registry) == "optional"


def gate_off(registry: Mapping[str, Any] | None) -> bool:
    return gate_mode(registry) == "off"


def schema_kind(schema: Mapping[str, Any]) -> str:
    """The canonical type of a parameter schema: `integer` for `int`,
    `number` for `float`, `string` for `str`, the others as written."""
    kind = str(schema.get("type"))
    return _TYPE_ALIASES.get(kind, kind)


def structured(schema: Mapping[str, Any]) -> bool:
    """Whether the schema admits an object or an array, which only the JSON
    form (`--params`) can carry."""
    return schema_kind(schema) in ("object", "array")


def _bound(schema: Mapping[str, Any], name: str, alias: str) -> Any:
    return schema[name] if name in schema else schema.get(alias)


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    )


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_param(action: str, pname: str, schema: Any) -> dict[str, Any]:
    return _validate_schema(f"{action}.{pname}", schema, 0)


def _is_of_type(kind: str, value: Any) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, Mapping)
    return isinstance(value, list)


def _validate_schema(where: str, schema: Any, depth: int) -> dict[str, Any]:
    """One parameter schema of the subset into its canonical form: the keys
    the type takes and no other, each bound a number of the right shape under
    one spelling, every nested schema validated the same way within
    SCHEMA_DEPTH_LIMIT, every enum member fitting the schema. The spelling
    the file used is kept."""
    if depth > SCHEMA_DEPTH_LIMIT:
        raise AssayError(
            f"parameter schema {where} is nested past the depth limit of {SCHEMA_DEPTH_LIMIT}",
            code="REGISTRY_INVALID",
        )
    if not isinstance(schema, Mapping):
        raise AssayError(f"parameter schema {where} must be an object", code="REGISTRY_INVALID")
    kind = schema.get("type")
    if not isinstance(kind, str) or _TYPE_ALIASES.get(kind, kind) not in _TYPES:
        raise AssayError(
            f"parameter {where} type must be one of {list(_TYPES)} "
            "(int, float and str are accepted for integer, number and string)",
            code="REGISTRY_INVALID",
        )
    canonical = _TYPE_ALIASES.get(kind, kind)
    for key in sorted(set(schema) - _KEYS_BY_TYPE[canonical]):
        if key in _SCHEMA_KEYS:
            raise AssayError(f"parameter {where} is a {kind}; {key} does not apply", code="REGISTRY_INVALID")
        raise AssayError(f"parameter schema {where} has unknown keys {sorted(set(schema) - _SCHEMA_KEYS)}", code="REGISTRY_INVALID")
    output: dict[str, Any] = {"type": kind}
    if canonical in ("integer", "number"):
        _validate_bounds(where, schema, output, ("min", "minimum"), ("max", "maximum"), "a number", _is_finite_number)
    elif canonical == "string":
        _validate_bounds(where, schema, output, ("minLength",), ("maxLength",), "a non-negative integer", _is_count)
    elif canonical == "object":
        _validate_object_schema(where, schema, output, depth)
    elif canonical == "array":
        _validate_bounds(where, schema, output, ("minItems",), ("maxItems",), "a non-negative integer", _is_count)
        if "items" not in schema:
            raise AssayError(
                f"parameter {where} is an array and needs 'items', the schema of every item",
                code="REGISTRY_INVALID",
            )
        output["items"] = _validate_schema(f"{where}[]", schema["items"], depth + 1)
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            raise AssayError(f"parameter {where} enum must be a non-empty list", code="REGISTRY_INVALID")
        # Each member is of the schema's type and fits the rest of it (its
        # bounds, its shape): a member no value could ever match is a
        # registry mistake, refused here rather than at every act.
        for value in values:
            if not _is_of_type(canonical, value):
                raise AssayError(
                    f"parameter {where} enum value {value!r} does not match type {kind}",
                    code="REGISTRY_INVALID",
                )
            try:
                _check(output, value, where, None)
            except AssayError as error:
                raise AssayError(
                    f"parameter {where} enum value {value!r} does not fit the schema: {error}",
                    code="REGISTRY_INVALID",
                ) from None
        output["enum"] = list(values)
    return output


def _validate_bounds(
    where: str,
    schema: Mapping[str, Any],
    output: dict[str, Any],
    lows: tuple[str, ...],
    highs: tuple[str, ...],
    expected: str,
    accept: Callable[[Any], bool],
) -> None:
    """A schema's low and high bound (`min`/`minimum` and `max`/`maximum`,
    `minLength` and `maxLength`, `minItems` and `maxItems`): each given under
    one spelling, of the right shape, and the low no greater than the high."""
    found: list[tuple[str | None, Any]] = []
    for names in (lows, highs):
        present = [name for name in names if name in schema]
        if len(present) > 1:
            raise AssayError(
                f"parameter {where} gives both {present[0]} and {present[1]}", code="REGISTRY_INVALID"
            )
        if present:
            value = schema[present[0]]
            if not accept(value):
                raise AssayError(f"parameter {where} {present[0]} must be {expected}", code="REGISTRY_INVALID")
            output[present[0]] = value
            found.append((present[0], value))
        else:
            found.append((None, None))
    (low_name, low), (high_name, high) = found
    if low is not None and high is not None and low > high:
        raise AssayError(f"parameter {where} has {low_name} > {high_name}", code="REGISTRY_INVALID")


def _validate_object_schema(
    where: str, schema: Mapping[str, Any], output: dict[str, Any], depth: int
) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        raise AssayError(
            f"parameter {where} is an object and needs a non-empty 'properties' object",
            code="REGISTRY_INVALID",
        )
    canonical: dict[str, dict[str, Any]] = {}
    for name, sub in properties.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise AssayError(f"parameter {where} has an invalid property name {name!r}", code="REGISTRY_INVALID")
        canonical[name] = _validate_schema(f"{where}.{name}", sub, depth + 1)
    output["properties"] = canonical
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
            raise AssayError(f"parameter {where} required must be a list of property names", code="REGISTRY_INVALID")
        unknown = [name for name in required if name not in canonical]
        if unknown:
            raise AssayError(
                f"parameter {where} requires properties it does not declare: {unknown}",
                code="REGISTRY_INVALID",
            )
        if len(set(required)) != len(required):
            raise AssayError(f"parameter {where} names a property twice in required", code="REGISTRY_INVALID")
        output["required"] = list(required)
    if "additionalProperties" in schema:
        if schema["additionalProperties"] is not False:
            raise AssayError(
                f"parameter {where} additionalProperties must be false: no other property is ever admitted",
                code="REGISTRY_INVALID",
            )
        output["additionalProperties"] = False


def load_registry(paths: RunPaths) -> dict[str, Any] | None:
    """The registry pinned at `assay start`, or None when the directory has
    none (a run started before 1.2.0 without one: readable, not resumable)."""
    value = read_json(paths.registry, None)
    return value if isinstance(value, dict) else None


def require_registry(run: Run) -> dict[str, Any]:
    """The pinned registry, for anything that acts. Every run since 1.2.0 has one."""
    registry = run.registry
    if registry is None:
        raise AssayError(
            "this run has no registry: runs without one are not supported since 1.2.0",
            code="REGISTRY_MISSING",
            hint="start a new run in a fresh directory with `assay start WORLD_ID --registry FILE`",
        )
    return registry


def action_form(name: str, schemas: Mapping[str, Any]) -> str:
    """The action as the REGISTRY block and the ACTION_PARAMS hint print it:
    the name, then every parameter with its schema as `pname=<schema>` when
    every parameter is a scalar, else the JSON form (`params_form`)."""
    if any(structured(schema) for schema in schemas.values()):
        return f"{name} {params_form(schemas)}"
    rendered = " ".join(f"{pname}={_schema_text(schema)}" for pname, schema in schemas.items())
    return f"{name} {rendered}" if rendered else name


def params_form(schemas: Mapping[str, Any]) -> str:
    """The `--params` form of an action's parameters: a JSON skeleton with a
    placeholder where every value goes, an optional property marked `?`."""
    body = ", ".join(f'"{pname}": {_json_form(schema)}' for pname, schema in schemas.items())
    return f"--params '{{{body}}}'"


def _form_hint(form: str) -> str:
    return f"the form is `{form}`"


def _shown(value: Any) -> str:
    """A value as a refusal names it: the repr of a scalar, compact JSON for
    an object or an array, clipped past SHOWN_WIDTH characters."""
    text: str
    if isinstance(value, (Mapping, list)):
        try:
            text = one_line_json(value)
        except (TypeError, ValueError):
            text = repr(value)
    else:
        text = repr(value)
    return text if len(text) <= SHOWN_WIDTH else text[: SHOWN_WIDTH - 3] + "..."


def validate_value(
    schema: Mapping[str, Any], value: Any, *, where: str = "value", form: str | None = None
) -> Any:
    """One value against a parameter schema of the subset, nested as deep as
    the schema goes: the type, finiteness, the enum, the bounds, the lengths,
    the item count and every item, the properties (none the schema does not
    declare, every required one present) and every property. Returns the
    value in its canonical form (a number as a float, an object with its keys
    sorted). A refusal is `ACTION_PARAMS`, naming the value at `where`
    (`INC amount=5`, `RUN program.lines[2]=7`) and, when `form` is given, the
    action's whole form as the hint, the next step."""
    return _check(schema, value, where, _form_hint(form) if form else None)


def _check(
    schema: Mapping[str, Any], value: Any, where: str, hint: str | None, shown: str | None = None
) -> Any:
    """`validate_value` with the refused value named as `shown` when the
    caller has a better name for it (the token as typed)."""
    kind = schema_kind(schema)
    named = f"{where}={_shown(value) if shown is None else shown}"

    def refuse(text: str) -> AssayError:
        return AssayError(f"{named} {text}", code="ACTION_PARAMS", hint=hint)

    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise refuse("is not an integer")
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise refuse("is not a number")
        try:
            value = float(value)
        except OverflowError:
            # A JSON integer past what a float holds.
            raise refuse("must be finite") from None
        if math.isnan(value) or math.isinf(value):
            raise refuse("must be finite")
    elif kind == "string":
        if not isinstance(value, str):
            raise refuse("is not a string")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise refuse("is not true or false")
    elif kind == "object":
        if not isinstance(value, Mapping):
            raise refuse("is not an object")
    elif not isinstance(value, list):
        raise refuse("is not an array")
    if "enum" in schema and value not in schema["enum"]:
        raise refuse(f"is not one of {schema['enum']}")
    if kind in ("integer", "number"):
        low, high = _bound(schema, "min", "minimum"), _bound(schema, "max", "maximum")
        if low is not None and value < low:
            raise refuse(f"is below min {low}")
        if high is not None and value > high:
            raise refuse(f"is above max {high}")
    elif kind == "string":
        low, high = schema.get("minLength"), schema.get("maxLength")
        if low is not None and len(value) < low:
            raise refuse(f"is {len(value)} characters long, below minLength {low}")
        if high is not None and len(value) > high:
            raise refuse(f"is {len(value)} characters long, above maxLength {high}")
    elif kind == "object":
        properties: Mapping[str, Any] = schema["properties"]
        for key in value:
            if key not in properties:
                raise refuse(f"has no property {key!r}; it takes {sorted(properties)}")
        missing = [name for name in schema.get("required", ()) if name not in value]
        if missing:
            raise refuse(f"is missing property(ies): {missing}")
        return {key: _check(properties[key], value[key], f"{where}.{key}", hint) for key in sorted(value)}
    elif kind == "array":
        low, high = schema.get("minItems"), schema.get("maxItems")
        if low is not None and len(value) < low:
            raise refuse(f"has {len(value)} item(s), below minItems {low}")
        if high is not None and len(value) > high:
            raise refuse(f"has {len(value)} item(s), above maxItems {high}")
        return [
            _check(schema["items"], item, f"{where}[{index}]", hint) for index, item in enumerate(value)
        ]
    return value


def _coerce(action: str, pname: str, schema: Mapping[str, Any], raw: str, form: str) -> Any:
    """A typed token's value: the string read as the schema's scalar type,
    then checked like a value that arrived as JSON. An object or an array
    has no token form."""
    kind = schema_kind(schema)
    hint = _form_hint(form)
    value: Any
    if kind == "integer":
        try:
            value = int(raw, 10)
        except ValueError:
            raise AssayError(f"{action} {pname}={raw!r} is not an integer", code="ACTION_PARAMS", hint=hint) from None
    elif kind == "number":
        try:
            value = float(raw)
        except ValueError:
            raise AssayError(f"{action} {pname}={raw!r} is not a number", code="ACTION_PARAMS", hint=hint) from None
    elif kind == "boolean":
        if raw not in ("true", "false"):
            raise AssayError(f"{action} {pname}={raw!r} is not true or false", code="ACTION_PARAMS", hint=hint)
        value = raw == "true"
    elif kind in ("object", "array"):
        raise AssayError(
            f"{action} {pname} is an {kind} parameter and takes JSON, not a token",
            code="ACTION_PARAMS",
            hint=hint,
        )
    else:
        value = raw
    return _check(schema, value, f"{action} {pname}", hint, shown=repr(raw))


def validate_action(
    registry: Mapping[str, Any], action: str, params: Mapping[str, Any] | None
) -> tuple[str, dict[str, Any] | None]:
    """The daemon's check of an action that arrived as its registered name
    and its parameters object (docs/ARCHITECTURE.md section 7.2), before
    any spend: the name case-folded like a typed token's, RESET without
    parameters, every registered parameter present and no other, and each
    value against its schema (`validate_value`), nested values included.
    The same refusals as `parse_registry_action`, with the JSON value where
    the token stood."""
    name = str(action).strip().upper()
    if not name:
        raise AssayError("empty action name", code="ACTION_PARAMS", hint=TOKEN_FORM_HINT)
    if params is not None and not isinstance(params, Mapping):
        raise AssayError(
            f"{name} parameters must be a JSON object, not {_shown(params)}",
            code="ACTION_PARAMS",
            hint=TOKEN_FORM_HINT,
        )
    supplied_raw = dict(params or {})
    if name == "RESET":
        if supplied_raw:
            raise AssayError("RESET takes no parameters", code="ACTION_PARAMS", hint=_form_hint("RESET"))
        return name, None
    by_name = {item["name"]: item for item in registry.get("actions", ())}
    spec = by_name.get(name)
    if spec is None:
        raise AssayError(
            f"unknown action {action!r}; registered actions: "
            f"{sorted(by_name)} (plus built-in RESET)",
            code="ACTION_UNKNOWN",
            hint=SCHEMA_HINT,
        )
    schemas: Mapping[str, Any] = spec.get("params", {})
    form = action_form(name, schemas)
    supplied: dict[str, Any] = {}
    for pname, value in supplied_raw.items():
        if pname not in schemas:
            raise AssayError(
                f"{name} has no parameter {pname!r}; it takes {sorted(schemas) or 'none'}",
                code="ACTION_PARAMS",
                hint=_form_hint(form),
            )
        supplied[pname] = _check(schemas[pname], value, f"{name} {pname}", _form_hint(form))
    missing = [pname for pname in schemas if pname not in supplied]
    if missing:
        raise AssayError(
            f"{name} is missing parameter(s): {missing}", code="ACTION_PARAMS", hint=_form_hint(form)
        )
    return name, supplied or None


def parse_registry_action(
    token: str, registry: Mapping[str, Any]
) -> tuple[str, dict[str, Any] | None]:
    """Parse `NAME pname=value ...` against the registered schema.

    Validation happens entirely before any spend: unknown action, unknown or
    missing parameter, bad type, out-of-bounds, and enum violations are free
    refusals. RESET stays built-in and takes no parameters.
    """
    parts = token.strip().split()
    if not parts:
        raise AssayError("empty action token", code="ACTION_PARAMS", hint=TOKEN_FORM_HINT)
    name = parts[0].upper()
    if name == "RESET":
        if len(parts) > 1:
            raise AssayError("RESET takes no parameters", code="ACTION_PARAMS", hint=_form_hint("RESET"))
        return name, None
    by_name = {item["name"]: item for item in registry.get("actions", ())}
    spec = by_name.get(name)
    if spec is None:
        raise AssayError(
            f"unknown action {parts[0]!r}; registered actions: "
            f"{sorted(by_name)} (plus built-in RESET)",
            code="ACTION_UNKNOWN",
            hint=SCHEMA_HINT,
        )
    schemas: Mapping[str, Any] = spec.get("params", {})
    form = action_form(name, schemas)
    supplied: dict[str, Any] = {}
    for part in parts[1:]:
        pname, separator, raw = part.partition("=")
        if not separator or not pname:
            raise AssayError(
                f"parameters are supplied as pname=value, got {part!r}",
                code="ACTION_PARAMS",
                hint=_form_hint(form),
            )
        if pname in supplied:
            raise AssayError(
                f"{name} parameter {pname!r} supplied twice", code="ACTION_PARAMS", hint=_form_hint(form)
            )
        if pname not in schemas:
            raise AssayError(
                f"{name} has no parameter {pname!r}; it takes {sorted(schemas) or 'none'}",
                code="ACTION_PARAMS",
                hint=_form_hint(form),
            )
        supplied[pname] = _coerce(name, pname, schemas[pname], raw, form)
    missing = [pname for pname in schemas if pname not in supplied]
    if missing:
        raise AssayError(
            f"{name} is missing parameter(s): {missing}", code="ACTION_PARAMS", hint=_form_hint(form)
        )
    return name, supplied or None


def check_registry_action(name: str, available: Sequence[Any]) -> None:
    """Affordance check: honored only when the observation advertises names.
    A world that advertises ids rather than names (a frame world) has its
    observation kind render them as names before this check."""
    if name == "RESET":
        return
    advertised = [str(value) for value in available or ()]
    if not advertised:
        return
    if name.upper() not in {value.upper() for value in advertised}:
        raise AssayError(
            f"{name} is unavailable; advertised actions are {sorted(advertised)}",
            code="ACTION_UNAVAILABLE",
        )


def spent_actions(events: Sequence[Event]) -> int:
    return sum(1 for event in events if event.counts_action)


def check_budget(
    events: Sequence[Event],
    registry: Mapping[str, Any] | None,
    planned: int = 1,
) -> None:
    """Refuse any spend that would pass the registered hard cap."""
    if not registry:
        return
    cap = (registry.get("budget") or {}).get("actions")
    if cap is None:
        return
    spent = spent_actions(events)
    remaining = max(0, int(cap) - spent)
    if planned > remaining:
        raise AssayError(
            f"action budget cap={cap} spent={spent} "
            f"remaining={remaining}; this command needs {planned} paid action(s) "
            "and was refused",
            code="BUDGET_EXHAUSTED",
            hint=BUDGET_HINT,
        )


def _range_text(low: Any, high: Any) -> str | None:
    if low is not None and high is not None:
        return f"{low}..{high}"
    if low is not None:
        return f">={low}"
    if high is not None:
        return f"<={high}"
    return None


def _enum_text(value: Any) -> str:
    if isinstance(value, (bool, Mapping, list)):
        return one_line_json(value)
    return str(value)


def _schema_text(schema: Mapping[str, Any]) -> str:
    """The compact placeholder for one parameter, as the REGISTRY block
    prints it: a scalar with its enum or its bounds (`<int 1..2>`,
    `<on|off>`, `<string 1..200 chars>`), an object as its property names
    (`<object: lines[] note? meta{}>`: `[]` an array property, `{}` an
    object property, `?` an optional one), an array as its item type and
    count (`<array of 1..8 string>`)."""
    if "enum" in schema:
        return "<" + "|".join(_enum_text(value) for value in schema["enum"]) + ">"
    kind = str(schema["type"])
    canonical = schema_kind(schema)
    if canonical in ("integer", "number"):
        count = _range_text(_bound(schema, "min", "minimum"), _bound(schema, "max", "maximum"))
        return f"<{kind}>" if count is None else f"<{kind} {count}>"
    if canonical == "string":
        count = _range_text(schema.get("minLength"), schema.get("maxLength"))
        return f"<{kind}>" if count is None else f"<{kind} {count} chars>"
    if canonical == "boolean":
        return f"<{kind}>"
    if canonical == "object":
        required = set(schema.get("required", ()))
        marks = " ".join(
            name + ("" if name in required else "?") + _property_mark(sub)
            for name, sub in schema["properties"].items()
        )
        return f"<object: {marks}>"
    count = _range_text(schema.get("minItems"), schema.get("maxItems"))
    inner = _item_text(schema["items"])
    return f"<array of {inner}>" if count is None else f"<array of {count} {inner}>"


def _property_mark(schema: Mapping[str, Any]) -> str:
    kind = schema_kind(schema)
    return "[]" if kind == "array" else "{}" if kind == "object" else ""


def _item_text(schema: Mapping[str, Any]) -> str:
    if "enum" in schema:
        return "|".join(_enum_text(value) for value in schema["enum"])
    return str(schema["type"])


def _json_form(schema: Mapping[str, Any]) -> str:
    """The placeholder of one parameter in the JSON form: a scalar as
    `_schema_text` prints it, an object as `{"name": <...>, "note"?: <...>}`,
    an array as `[<...>, ...]` with its item count when bounded."""
    if "enum" in schema or not structured(schema):
        return _schema_text(schema)
    if schema_kind(schema) == "object":
        required = set(schema.get("required", ()))
        body = ", ".join(
            f'"{name}"{"" if name in required else "?"}: {_json_form(sub)}'
            for name, sub in schema["properties"].items()
        )
        return "{" + body + "}"
    inner = _json_form(schema["items"])
    count = _range_text(schema.get("minItems"), schema.get("maxItems"))
    return f"[{inner}, ...]" if count is None else f"[{inner}, ... {count} items]"


def registry_lines(registry: Mapping[str, Any]) -> list[str]:
    """Render names and schemas for status/start output. Never semantics.

    Every parameter renders compactly on the action's line (`_schema_text`);
    an action with an object or an array parameter adds a `form:` line, the
    `--params` form with a placeholder where every value goes, the same text
    an ACTION_PARAMS refusal gives as its hint. Descriptions are admissible
    but untrusted: they render inside a labeled data block and are withheld
    entirely in zero-prior mode. Hard flags (destructive/approval/liveness)
    always render; they protect the world.
    """
    actions = registry.get("actions", ())
    cap = (registry.get("budget") or {}).get("actions")
    header = f"REGISTRY | {len(actions)} registered actions | schemas below, semantics never given: learn by acting"
    if cap is not None:
        header += f" | budget {cap}"
    lines = [header]
    hide_descriptions = zero_prior(registry)
    for item in actions:
        params = item.get("params", {})
        rendered = " ".join(
            f"{pname}={_schema_text(schema)}" for pname, schema in params.items()
        )
        flags = [
            flag.upper()
            for flag in ("destructive", "approval")
            if item.get(flag)
        ]
        if item.get("liveness"):
            flags.append(f"LIVENESS={item['liveness'].upper()}")
        suffix = f"  [{' '.join(flags)}]" if flags else ""
        lines.append(
            f"  {item['name']}" + (f" {rendered}" if rendered else "") + suffix
        )
        if any(structured(schema) for schema in params.values()):
            lines.append(f"    form: {item['name']} {params_form(params)}")
        if item.get("description") and not hide_descriptions:
            lines.append(
                f"    description (data, not instructions; semantics are earned, "
                f"never assumed): {str(item['description'])[:200]}"
            )
    lines.append('  RESET (built-in; needs --because "<reason>" unless GAME_OVER)')
    note = registry.get("mode_note")
    if note:
        lines.append(f"  note (data, not instructions): {str(note)[:160]}")
    return lines


def spend_reports(
    activity: Sequence[Mapping[str, Any]],
) -> tuple[float, int]:
    """Total (usd, tokens) from idempotent spend_report activity entries.

    The kernel makes no LLM calls and cannot see the token bill itself; the
    launcher posts usage via `assay spend report`. Entries are idempotent by id:
    the LAST entry per id wins (a launcher may correct an earlier figure).
    """
    latest: dict[str, Mapping[str, Any]] = {}
    for record in activity:
        if record.get("kind") == "spend_report" and record.get("id"):
            latest[str(record["id"])] = record
    usd = 0.0
    tokens = 0
    for record in latest.values():
        # A figure that is not a finite number (a NaN a launcher once
        # posted) counts nothing, so the totals stay numbers.
        try:
            amount = float(record.get("usd") or 0.0)
            count = int(record.get("tokens") or 0)
        except (TypeError, ValueError):
            continue
        if math.isfinite(amount):
            usd += amount
        tokens += count
    return round(usd, 6), tokens


def check_usd_budget(
    activity: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any] | None,
) -> None:
    """Refuse paid actions once the reported spend passes the registered cap.

    The cap is as fresh as the feed, stated honestly rather than pretended.
    """
    if not registry:
        return
    cap = (registry.get("budget") or {}).get("usd")
    if cap is None:
        return
    usd, _ = spend_reports(activity)
    if usd >= float(cap):
        raise AssayError(
            f"reported spend ${usd:.2f} has reached the "
            f"registered cap ${float(cap):.2f}; paid actions are refused",
            code="BUDGET_EXHAUSTED",
            hint=BUDGET_HINT,
        )


def budget_text(spent: int, cap: int | None) -> str:
    """The BUDGET line from its facts."""
    if cap is None:
        return f"BUDGET | paid actions {spent} | no cap registered"
    remaining = max(0, int(cap) - spent)
    line = f"BUDGET | paid actions {spent}/{cap} | remaining {remaining}"
    if remaining == 0:
        line += "; act/commit/reset are refused (BUDGET_EXHAUSTED)"
    return line


def budget_line(registry: Mapping[str, Any], events: Sequence[Event]) -> str:
    return budget_text(spent_actions(events), (registry.get("budget") or {}).get("actions"))


def gate_text(mode: str, unpredicted: int) -> str:
    """The GATE line of a control-arm run: the mode and a count, nothing
    more. Under `optional` the count is the unpredicted actions, under `off`
    every paid action is one. The audit, not this line, carries the verdict
    such a run gets (invalid for scoring)."""
    noun = "unpredicted action(s)" if mode == "optional" else "action(s)"
    return f"GATE | {mode} | {unpredicted} {noun}"
