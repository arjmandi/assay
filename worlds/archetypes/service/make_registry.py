"""Draft a service world's registry and its call map from the service itself:
an MCP server's tool list, or an OpenAPI 3 document in JSON.

    python3 make_registry.py mcp [--out DIR] -- COMMAND [ARG ...]
    python3 make_registry.py openapi SPEC.json [--base-url URL] [--out DIR]

It prints one JSON object: `registry` (the actions, for registry.json),
`service` (the backend and the call map, for recipe.json's service block) and
`notes` (each place the conversion dropped or changed something). With
--out it writes registry.json and recipe.json drafts into DIR, refusing to
overwrite. Then the builder writes the goal, the budget, the rules and the
dashboard, and confirms the flags with the user.

The conversion keeps what the registry's schema subset can check (types, enum,
bounds, lengths, properties, required, items) and drops the rest. A property
whose name the registry cannot hold (letters, digits and underscores, at most
32 characters, starting with a letter) is dropped, and so is an object with no
declared properties; each is named in the notes. Flags follow the service's
own hints, which the MCP spec says to treat as untrusted: a tool not marked
read-only, and not marked non-destructive, is `destructive`. An HTTP DELETE
or PUT is `destructive`. A tool's or an operation's description becomes the
action's description, which the agent reads as data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp_client import McpClient, McpError  # noqa: E402

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
SIMPLE = ("string", "integer", "number", "boolean")
KEEP = {
    "string": ("enum", "minLength", "maxLength"),
    "integer": ("enum", "minimum", "maximum"),
    "number": ("enum", "minimum", "maximum"),
    "boolean": ("enum",),
}
DESCRIPTION_LENGTH = 240
DEPTH_LIMIT = 12


class Converter:
    """One document's schemas into the registry's subset, with notes."""

    def __init__(self, components: dict[str, Any] | None = None) -> None:
        self.components = components or {}
        self.notes: list[str] = []

    def resolve(self, schema: Any, where: str) -> Any:
        seen = 0
        while isinstance(schema, dict) and "$ref" in schema and seen < 20:
            reference = str(schema["$ref"])
            if not reference.startswith("#/"):
                self.notes.append(f"{where}: the reference {reference} is outside the document; dropped")
                return None
            target: Any = {"components": self.components}
            for part in reference[2:].split("/"):
                target = target.get(part) if isinstance(target, dict) else None
            schema = target
            seen += 1
        return schema

    def schema(self, schema: Any, where: str, depth: int = 0) -> dict[str, Any] | None:
        schema = self.resolve(schema, where)
        if not isinstance(schema, dict) or depth > DEPTH_LIMIT:
            self.notes.append(f"{where}: not a schema the registry can hold; dropped")
            return None
        if "allOf" in schema:
            merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
            for index, part in enumerate(schema["allOf"]):
                part = self.resolve(part, f"{where}.allOf[{index}]")
                if isinstance(part, dict):
                    merged["properties"].update(part.get("properties") or {})
                    merged["required"].extend(part.get("required") or [])
            schema = merged
        for key in ("oneOf", "anyOf"):
            if key in schema:
                options = [self.resolve(option, where) for option in schema[key]]
                options = [option for option in options if isinstance(option, dict) and option.get("type") != "null"]
                if not options:
                    self.notes.append(f"{where}: {key} with no usable option; dropped")
                    return None
                if len(options) > 1:
                    self.notes.append(f"{where}: {key} narrowed to its first option")
                schema = options[0]
        kind = schema.get("type")
        if isinstance(kind, list):
            kinds = [item for item in kind if item != "null"]
            kind = kinds[0] if kinds else None
        if kind is None:
            kind = "object" if "properties" in schema else "array" if "items" in schema else None
        if kind in SIMPLE:
            output: dict[str, Any] = {"type": kind}
            for key in KEEP[kind]:
                if key in schema:
                    output[key] = schema[key]
            if "exclusiveMinimum" in schema and isinstance(schema["exclusiveMinimum"], (int, float)):
                output.setdefault("minimum", schema["exclusiveMinimum"])
            if "exclusiveMaximum" in schema and isinstance(schema["exclusiveMaximum"], (int, float)):
                output.setdefault("maximum", schema["exclusiveMaximum"])
            return output
        if kind == "array":
            items = self.schema(schema.get("items", {"type": "string"}), f"{where}[]", depth + 1)
            if items is None:
                self.notes.append(f"{where}: an array whose items the registry cannot hold; dropped")
                return None
            output = {"type": "array", "items": items}
            for key in ("minItems", "maxItems"):
                if key in schema:
                    output[key] = schema[key]
            return output
        if kind == "object":
            properties: dict[str, Any] = {}
            for name, sub in (schema.get("properties") or {}).items():
                if not NAME.fullmatch(name):
                    self.notes.append(f"{where}.{name}: a property name the registry cannot hold; dropped")
                    continue
                converted = self.schema(sub, f"{where}.{name}", depth + 1)
                if converted is not None:
                    properties[name] = converted
            if not properties:
                self.notes.append(f"{where}: an object with no property the registry can hold; dropped")
                return None
            output = {"type": "object", "properties": properties}
            required = [name for name in schema.get("required") or [] if name in properties]
            if required:
                output["required"] = sorted(set(required), key=required.index)
            return output
        self.notes.append(f"{where}: type {kind!r} is not one the registry can hold; dropped")
        return None


def action_name(text: str, taken: set[str]) -> str:
    """An action name the registry accepts: camelCase and separators become
    underscores, upper case, a letter first, at most 32 characters, unique."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    name = re.sub(r"[^A-Za-z0-9]+", "_", spaced).strip("_").upper() or "ACTION"
    if not name[0].isalpha():
        name = "A_" + name
    name = name[:32]
    candidate, suffix = name, 2
    while candidate in taken or candidate == "RESET":
        candidate = f"{name[:29]}_{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def _description(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    line = " ".join(text.split())
    return line if len(line) <= DESCRIPTION_LENGTH else line[: DESCRIPTION_LENGTH - 3] + "..."


def from_mcp_tools(tools: list[dict[str, Any]]) -> dict[str, Any]:
    converter = Converter()
    taken: set[str] = set()
    actions: list[dict[str, Any]] = []
    calls: dict[str, Any] = {}
    for tool in tools:
        tool_name = str(tool.get("name", ""))
        name = action_name(tool_name, taken)
        converted = converter.schema(tool.get("inputSchema") or {}, f"{tool_name}.inputSchema") if (
            (tool.get("inputSchema") or {}).get("properties")
        ) else None
        properties = (converted or {}).get("properties", {})
        required = set((converted or {}).get("required", []))
        if not properties:
            params: dict[str, Any] = {}
            form = "none"
        elif set(properties) == required and "arguments" not in properties:
            params, form = dict(properties), "flat"
        else:
            params, form = {"arguments": converted}, "object"
        action: dict[str, Any] = {"name": name, "params": params}
        annotations = tool.get("annotations") or {}
        if not annotations.get("readOnlyHint") and annotations.get("destructiveHint") is not False:
            action["destructive"] = True
        description = _description(tool.get("description"))
        if description:
            action["description"] = description
        actions.append(action)
        calls[name] = {"tool": tool_name, "arguments": form}
    return {"registry": {"actions": actions}, "service": {"backend": "mcp", "actions": calls}, "notes": converter.notes}


def from_openapi(document: dict[str, Any], base_url: str | None) -> dict[str, Any]:
    converter = Converter(document.get("components") or {})
    taken: set[str] = set()
    actions: list[dict[str, Any]] = []
    calls: dict[str, Any] = {}
    for path, item in sorted((document.get("paths") or {}).items()):
        shared = item.get("parameters") or []
        for method in ("get", "post", "put", "patch", "delete"):
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            where = f"{method.upper()} {path}"
            name = action_name(operation.get("operationId") or f"{method}_{path}", taken)
            params: dict[str, Any] = {}
            query: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
            path_params: list[str] = []
            for parameter in [*shared, *(operation.get("parameters") or [])]:
                parameter = converter.resolve(parameter, where)
                if not isinstance(parameter, dict):
                    continue
                place, pname = parameter.get("in"), str(parameter.get("name", ""))
                if place not in ("path", "query"):
                    converter.notes.append(f"{where}: the {place} parameter {pname} is not an action parameter; dropped")
                    continue
                if not NAME.fullmatch(pname):
                    converter.notes.append(f"{where}: the parameter name {pname!r} is not one the registry can hold; dropped")
                    continue
                schema = converter.schema(parameter.get("schema") or {"type": "string"}, f"{where} {pname}")
                if schema is None:
                    continue
                if place == "path":
                    params[pname] = schema
                    path_params.append(pname)
                else:
                    query["properties"][pname] = schema
                    if parameter.get("required"):
                        query["required"].append(pname)
            if query["properties"]:
                if not query["required"]:
                    del query["required"]
                params["query"] = query
            body = (((operation.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
            if body is not None:
                converted = converter.schema(body, f"{where} body")
                if converted is not None:
                    params["body"] = converted
            action: dict[str, Any] = {"name": name, "params": params}
            if method in ("delete", "put"):
                action["destructive"] = True
            description = _description(operation.get("summary") or operation.get("description"))
            if description:
                action["description"] = description
            actions.append(action)
            call: dict[str, Any] = {"method": method.upper(), "path": path}
            if path_params:
                call["path_params"] = path_params
            if "query" in params:
                call["query"] = True
            if "body" in params:
                call["body"] = True
            calls[name] = call
    servers = document.get("servers") or []
    url = base_url or (servers[0].get("url") if servers and isinstance(servers[0], dict) else None)
    service: dict[str, Any] = {"backend": "http", "http": {"base_url": url or "http://127.0.0.1:{port}"}, "actions": calls}
    return {"registry": {"actions": actions}, "service": service, "notes": converter.notes}


def _write(out: Path, draft: dict[str, Any], name: str, mcp_command: list[str] | None) -> None:
    registry_path, recipe_path = out / "registry.json", out / "recipe.json"
    for path in (registry_path, recipe_path):
        if path.exists():
            raise SystemExit(f"{path} exists; move it aside or merge the printed draft by hand")
    out.mkdir(parents=True, exist_ok=True)
    registry = {**draft["registry"], "budget": {"actions": 50}, "goal": {"text": "TO WRITE: the goal, in one or two sentences"}}
    service = dict(draft["service"])
    if mcp_command is not None:
        service["mcp"] = {"command": mcp_command}
    recipe = {
        "recipe": 1,
        "name": name,
        "card": {"code": "S-?-?-?", "family": "?", "fit": "?", "clock": "s", "proxy": "?", "scarce_commit": None, "hypothesis": "?"},
        "world": {"mode": "service", "actions": [action["name"] for action in draft["registry"]["actions"]], "timeout_seconds": 30},
        "service": service,
        "player": {"note": "The service's answers arrive in the observation's response, and each full answer is saved in the file response.saved names."},
    }
    registry_path.write_text(json.dumps(registry, indent=1) + "\n")
    recipe_path.write_text(json.dumps(recipe, indent=1) + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="kind", required=True)
    mcp = sub.add_parser("mcp", help="list an MCP server's tools; the server command follows --")
    mcp.add_argument("--out", type=Path)
    mcp.add_argument("command", nargs=argparse.REMAINDER)
    openapi = sub.add_parser("openapi", help="read an OpenAPI 3 document in JSON")
    openapi.add_argument("spec", type=Path)
    openapi.add_argument("--base-url")
    openapi.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.kind == "mcp":
        command = [word for word in args.command if word != "--"] if args.command else []
        if not command:
            parser.error("give the MCP server's command after --")
        try:
            client = McpClient(command)
        except McpError as error:
            raise SystemExit(f"the MCP server did not start a session: {error}") from error
        try:
            draft = from_mcp_tools(client.list_tools())
        finally:
            client.close()
        if args.out:
            _write(args.out, draft, args.out.name, command)
    else:
        draft = from_openapi(json.loads(args.spec.read_text()), args.base_url)
        if args.out:
            _write(args.out, draft, args.out.name, None)
    print(json.dumps(draft, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
