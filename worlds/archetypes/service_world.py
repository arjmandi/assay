"""The service engine, behind the service archetype: a remote system a program
drives, an MCP server over stdio or an HTTP API, as an ASSAY world.

Every paid action is one call to the service, named in the recipe's
`service.actions`: a tool call, or an HTTP request with path parameters, a
`query` object and a `body` object. The observation shows the call's answer
trimmed (`response`), with the full answer saved under `.assay/world_state/
responses/` for the agent to read. It also shows the dashboard: reads with
fixed arguments, made after every action for free, never counted, which give
a service the before-and-after a microscope needs. An answer the service gives
as an error (an HTTP status of 400 or more, an MCP result with isError, a
JSON-RPC error) is the world refusing: the spend is journaled as evidence. A
service that cannot be reached, or does not answer in time, is a fault: the
engine raises, and the kernel spends nothing.

The recipe may also declare:
- a local service the session starts and stops (`service.start`: a mock, a
  dev server), with a free `{port}`;
- the built-in WAIT action, which advances a mock's clock or sleeps;
- caps on scarce actions, refused past the cap through the observation;
- a verdict: a call whose answer decides the WIN and never enters the data,
  as a game's engine keeps its own win state;
- `reset: "restart"`, a RESET that starts the service afresh;
- `replayable`, which is true only for a deterministic local service.

A remote service changes outside the journal, so by default the world
declares no replay and lives one daemon long. worlds/README.md and
worlds/archetypes/service/README.md are the recipe's reference.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from assay.adapters import SessionCapability
from command_world import (
    ACTION_NAME,
    ARCHETYPES,
    MISSING,
    RECIPE_FORMAT,
    RecipeError,
    _check_condition,
    _levels_spec,
    condition_paths,
    holds,
    read_path,
    world_python,
)
from mcp_client import McpClient, McpError, child_environment

BACKENDS = ("mcp", "http")
TOP_LEVEL_KEYS = frozenset({"recipe", "name", "card", "world", "service", "player"})
WORLD_KEYS = frozenset({"mode", "actions", "timeout_seconds", "win", "lose", "levels"})
SERVICE_KEYS = frozenset(
    {"backend", "start", "mcp", "http", "actions", "wait", "dashboard", "verdict", "caps", "view", "replayable", "reset"}
)
ENGINE_KEYS = ("response", "dashboard", "calls", "errors", "last_result", "refusals")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
ARGUMENT_FORMS = ("flat", "object", "none")
RESETS = ("none", "restart")
ENV = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
PLACEHOLDER = re.compile(r"\{([a-z]+)\}")
START_PLACEHOLDERS = frozenset({"python", "recipe", "archetypes", "port", "seed", "state"})
SHOWN_HEADERS = ("content-type", "link", "location", "retry-after", "x-ratelimit-remaining", "x-total-count")
VIEW_DEFAULTS = {"max_items": 50, "max_string": 500, "max_depth": 8}
INTERNAL_HEADER = "X-Assay-Internal"
STATE_PARTS = (".assay", "world_state")
MAX_TIMEOUT_SECONDS = 3600.0


@dataclass(frozen=True)
class ServiceRecipe:
    path: Path
    name: str
    actions: tuple[str, ...]
    timeout_seconds: float
    win: Mapping[str, Any] | None
    lose: Mapping[str, Any] | None
    levels_completed: int | Mapping[str, Any] | None
    win_levels: int | Mapping[str, Any] | None
    backend: str
    calls: Mapping[str, Mapping[str, Any]]
    start: Mapping[str, Any] | None = None
    mcp: Mapping[str, Any] | None = None
    http: Mapping[str, Any] | None = None
    wait: Mapping[str, Any] | None = None
    dashboard: tuple[Mapping[str, Any], ...] = ()
    verdict: Mapping[str, Any] | None = None
    caps: Mapping[str, int] = field(default_factory=dict)
    view: Mapping[str, int] = field(default_factory=dict)
    replayable: bool = False
    reset: str = "none"
    card: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def archetype(self) -> str:
        return "service"


def load_service_recipe(path: Path | str) -> ServiceRecipe:
    """Read and check a service recipe; a problem is a RecipeError naming the
    file and the field, raised at import time, before anything runs."""
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
        raise fail(unknown[0], f"not a service recipe key; the keys are {sorted(TOP_LEVEL_KEYS)}")
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
        raise fail(f"world.{unknown[0]}", f"not a service world key; the keys are {sorted(WORLD_KEYS)}")
    if world.get("mode") != "service":
        raise fail("world.mode", 'a service recipe says "service"')
    actions = world.get("actions")
    if not isinstance(actions, list) or not actions:
        raise fail("world.actions", "a non-empty list of the registry's action names")
    for action in actions:
        if not isinstance(action, str) or not ACTION_NAME.match(action) or action == "RESET":
            raise fail("world.actions", f"{action!r} is not an action name (RESET is built in)")
    if len(set(actions)) != len(actions):
        raise fail("world.actions", "a name is listed twice")
    timeout = world.get("timeout_seconds", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise fail("world.timeout_seconds", f"a number of seconds above 0 and at most {MAX_TIMEOUT_SECONDS:g}")
    for key in ("win", "lose"):
        if world.get(key) is not None:
            _check_condition(world[key], f"world.{key}", fail)
    levels = world.get("levels", {})
    if not isinstance(levels, dict) or set(levels) - {"completed", "total"}:
        raise fail("world.levels", 'an object with "completed" and "total"')

    service = raw.get("service")
    if not isinstance(service, dict):
        raise fail("service", "an object is required")
    unknown = sorted(set(service) - SERVICE_KEYS)
    if unknown:
        raise fail(f"service.{unknown[0]}", f"not a service key; the keys are {sorted(SERVICE_KEYS)}")
    backend = service.get("backend")
    if backend not in BACKENDS:
        raise fail("service.backend", f"one of {list(BACKENDS)}")
    start = service.get("start")
    if start is not None:
        if not isinstance(start, dict) or set(start) - {"command", "ready", "timeout_seconds"}:
            raise fail("service.start", 'an object with "command", and optionally "ready" and "timeout_seconds"')
        _argv(start.get("command"), "service.start.command", fail, START_PLACEHOLDERS)
        if start.get("ready") is not None and (not isinstance(start["ready"], str) or not start["ready"].startswith("/")):
            raise fail("service.start.ready", "a path on the service, such as /health")
    mcp = service.get("mcp")
    http = service.get("http")
    if backend == "mcp":
        if not isinstance(mcp, dict) or set(mcp) - {"command", "env"}:
            raise fail("service.mcp", 'an object with "command", and optionally "env"')
        _argv(mcp.get("command"), "service.mcp.command", fail, START_PLACEHOLDERS)
        if not isinstance(mcp.get("env", {}), dict):
            raise fail("service.mcp.env", "an object of names to values")
    else:
        if not isinstance(http, dict) or set(http) - {"base_url", "headers", "shown_headers"}:
            raise fail("service.http", 'an object with "base_url", and optionally "headers" and "shown_headers"')
        if not isinstance(http.get("base_url"), str) or not http["base_url"].startswith(("http://", "https://")):
            raise fail("service.http.base_url", "an http:// or https:// URL, which may carry {port}")
        if not isinstance(http.get("headers", {}), dict):
            raise fail("service.http.headers", "an object of header names to values")

    wait = service.get("wait")
    calls = service.get("actions")
    if not isinstance(calls, dict):
        raise fail("service.actions", "an object from each action name to its call")
    for action in actions:
        if action == "WAIT" and wait is not None:
            continue
        if action not in calls:
            raise fail("service.actions", f"{action} has no call")
    for action, spec in calls.items():
        if action not in actions:
            raise fail("service.actions", f"{action} is not in world.actions")
        _check_call(spec, f"service.actions.{action}", backend, fail, paid=True)
    if wait is not None:
        if "WAIT" not in actions:
            raise fail("service.wait", "WAIT must be in world.actions")
        if not isinstance(wait, dict) or not (set(wait) == {"call"} or set(wait) == {"sleep_max_seconds"}):
            raise fail("service.wait", 'either {"call": ...} on the service, or {"sleep_max_seconds": N}')
        if "call" in wait:
            _check_call(wait["call"], "service.wait.call", backend, fail, paid=False)
        elif isinstance(wait["sleep_max_seconds"], bool) or not isinstance(wait["sleep_max_seconds"], int) or wait["sleep_max_seconds"] < 1:
            raise fail("service.wait.sleep_max_seconds", "a positive whole number of seconds")
    dashboard = service.get("dashboard", [])
    if not isinstance(dashboard, list):
        raise fail("service.dashboard", "a list of reads")
    names: set[str] = set()
    for index, entry in enumerate(dashboard):
        where = f"service.dashboard[{index}]"
        if not isinstance(entry, dict) or set(entry) - {"name", "call", "select"} or "name" not in entry or "call" not in entry:
            raise fail(where, 'an object with "name" and "call", and optionally "select"')
        if not isinstance(entry["name"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", entry["name"]) or entry["name"] in names:
            raise fail(where, "a unique lowercase name")
        names.add(entry["name"])
        _check_call(entry["call"], f"{where}.call", backend, fail, paid=False)
    verdict = service.get("verdict")
    if verdict is not None:
        if not isinstance(verdict, dict) or set(verdict) - {"call", "select", "equals"} or "call" not in verdict:
            raise fail("service.verdict", 'an object with "call", and optionally "select" and "equals"')
        _check_call(verdict["call"], "service.verdict.call", backend, fail, paid=False)
    caps = service.get("caps", {})
    if not isinstance(caps, dict):
        raise fail("service.caps", "an object from action names to counts")
    for action, cap in caps.items():
        if action not in calls or isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise fail(f"service.caps.{action}", "an action with a call, capped at a positive count")
    view = service.get("view", {})
    if not isinstance(view, dict) or set(view) - set(VIEW_DEFAULTS):
        raise fail("service.view", f"an object with any of {sorted(VIEW_DEFAULTS)}")
    for key, value in view.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise fail(f"service.view.{key}", "a positive count")
    replayable = service.get("replayable", False)
    if not isinstance(replayable, bool):
        raise fail("service.replayable", "true or false")
    reset = service.get("reset", "none")
    if reset not in RESETS:
        raise fail("service.reset", f"one of {list(RESETS)}")
    extra = {"player": raw["player"]} if "player" in raw else {}
    return ServiceRecipe(
        path=path,
        name=name.strip(),
        actions=tuple(actions),
        timeout_seconds=float(timeout),
        win=world.get("win"),
        lose=world.get("lose"),
        levels_completed=_levels_spec(levels.get("completed"), "world.levels.completed", fail),
        win_levels=_levels_spec(levels.get("total"), "world.levels.total", fail),
        backend=backend,
        calls=calls,
        start=start,
        mcp=mcp,
        http=http,
        wait=wait,
        dashboard=tuple(dashboard),
        verdict=verdict,
        caps=caps,
        view={**VIEW_DEFAULTS, **view},
        replayable=replayable,
        reset=reset,
        card=card,
        extra=extra,
    )


def _argv(value: Any, where: str, fail: Callable[[str, str], RecipeError], allowed: frozenset[str]) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(word, str) and word for word in value):
        raise fail(where, "an argv list of non-empty strings, never a shell line")
    for word in value:
        for name in PLACEHOLDER.findall(word):
            if name not in allowed:
                raise fail(where, f"{{{name}}} is not a placeholder here; the placeholders are {sorted(allowed)}")


def _check_call(spec: Any, where: str, backend: str, fail: Callable[[str, str], RecipeError], *, paid: bool) -> None:
    """A paid call maps an action's parameters; a fixed call (a dashboard
    read, the verdict, the wait) carries its own arguments."""
    if not isinstance(spec, dict):
        raise fail(where, "a call is an object")
    if backend == "mcp":
        allowed = {"tool", "arguments"}
        if set(spec) - allowed or not isinstance(spec.get("tool"), str) or not spec["tool"]:
            raise fail(where, 'an MCP call is {"tool": NAME, "arguments": ...}')
        if paid and spec.get("arguments", "object") not in ARGUMENT_FORMS:
            raise fail(where, f"arguments is one of {list(ARGUMENT_FORMS)}: the action's parameters as the tool's arguments")
        if not paid and not isinstance(spec.get("arguments", {}), dict):
            raise fail(where, "a fixed call's arguments are an object")
        return
    allowed = {"method", "path", "path_params", "query", "body"}
    if set(spec) - allowed or spec.get("method") not in HTTP_METHODS:
        raise fail(where, f'an HTTP call is {{"method": one of {list(HTTP_METHODS)}, "path": "/..."}}')
    if not isinstance(spec.get("path"), str) or not spec["path"].startswith("/"):
        raise fail(where, "the path starts with /")
    in_path = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", spec["path"])
    if paid:
        listed = spec.get("path_params", [])
        if not isinstance(listed, list) or sorted(listed) != sorted(in_path):
            raise fail(where, f"path_params must name the path's parameters, {sorted(in_path)}")
        for key in ("query", "body"):
            if not isinstance(spec.get(key, False), bool):
                raise fail(where, f"{key} is true when the action takes a {key} object")
    else:
        if in_path:
            raise fail(where, "a fixed call's path has no parameters")
        if not isinstance(spec.get("query", {}), dict):
            raise fail(where, "a fixed call's query is an object")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def expand_env(value: str) -> str:
    """${env:NAME} replaced by the daemon's environment; a name it lacks is an
    error, never an empty credential."""

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise RuntimeError(f"the environment has no {name}, which the recipe names")
        return os.environ[name]

    return ENV.sub(substitute, value)


def trim(value: Any, view: Mapping[str, int], depth: int = 0) -> Any:
    """A compact copy for the observation: lists past max_items end with a note
    of how many more there were, strings past max_string are cut with their
    length, and nesting past max_depth is elided. The full value is on disk."""
    if depth >= view["max_depth"] and isinstance(value, (dict, list)):
        return "{...}" if isinstance(value, dict) else "[...]"
    if isinstance(value, dict):
        return {str(key): trim(item, view, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        shown = [trim(item, view, depth + 1) for item in value[: view["max_items"]]]
        if len(value) > view["max_items"]:
            shown.append(f"... {len(value) - view['max_items']} more items")
        return shown
    if isinstance(value, str) and len(value) > view["max_string"]:
        return value[: view["max_string"]] + f"... ({len(value)} characters)"
    return value


def _error_excerpt(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("error", "message", "errors", "detail"):
            if key in body:
                return json.dumps(body[key], sort_keys=True)[:240]
    return (body if isinstance(body, str) else json.dumps(body))[:240]


class ServiceWorld:
    """The session: the observer and the actuator over a service."""

    def __init__(self, root: Path, config: Mapping[str, Any], recipe: ServiceRecipe) -> None:
        self.root = Path(root).resolve()
        self.recipe = recipe
        self.seed = int(config.get("seed") or 0)
        self.state_dir = self.root.joinpath(*STATE_PARTS)
        if self.state_dir.exists():
            shutil.rmtree(self.state_dir)
        (self.state_dir / "responses").mkdir(parents=True)
        self.calls = 0
        self.errors = 0
        self.refusals = 0
        self.uses: dict[str, int] = {}
        self.response: Any = None
        self.last_result: dict[str, Any] = {"action": "START", "status": "ready"}
        self.dashboard: dict[str, Any] = {}
        self._verdict = False
        self._closed = False
        self._service: subprocess.Popen[str] | None = None
        self._service_log: IO[str] | None = None
        self._mcp: McpClient | None = None
        self._base_url = ""
        atexit.register(self._shutdown)
        self._open()
        self._refresh()
        self._check_rule_paths()

    # -- the observer ---------------------------------------------------------

    @property
    def observation(self) -> dict[str, Any]:
        view = self._view()
        won = self._won(view)
        lost = not won and self.recipe.lose is not None and holds(self.recipe.lose, view)
        return {
            "state": "WIN" if won else "GAME_OVER" if lost else "NOT_FINISHED",
            "levels_completed": self._completed(view, won),
            "win_levels": self._total(view),
            "available_actions": list(self.recipe.actions),
            "data": view,
        }

    @property
    def public_info(self) -> dict[str, Any]:
        return {
            "world": self.recipe.name,
            "archetype": "service",
            "backend": self.recipe.backend,
            "card": dict(self.recipe.card),
        }

    @property
    def session(self) -> SessionCapability:
        return SessionCapability(replayable=self.recipe.replayable)

    # -- the actuator ---------------------------------------------------------

    def step(self, action: str, data: dict[str, Any] | None, reasoning: Mapping[str, Any] | None) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError(f"{self.recipe.name}: the world is closed")
        params = dict(data or {})
        if action == "RESET":
            if self.recipe.reset == "restart":
                self._shutdown()
                self._open()
                detail = "the service was started afresh"
            else:
                detail = "a service is not rewound; nothing changed"
            self.last_result = {"action": "RESET", "status": "ok", "detail": detail}
        elif action == "WAIT" and self.recipe.wait is not None:
            seconds = int(params["seconds"])
            if "call" in self.recipe.wait:
                self._fixed(self.recipe.wait["call"], {"seconds": seconds})
            else:
                time.sleep(min(seconds, int(self.recipe.wait["sleep_max_seconds"])))
            self.last_result = {"action": "WAIT", "status": "ok", "detail": f"waited {seconds} seconds"}
        elif action in self.recipe.calls:
            self._paid(action, params)
        else:
            raise ValueError(f"{self.recipe.name}: unknown action {action!r}")
        self._refresh()
        return self.observation

    def finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        summary = {"world": self.recipe.name, "calls": self.calls, "refusals": self.refusals, "data": self._view()}
        (self.root / ".assay" / "world_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
        self._shutdown()

    # -- the service ----------------------------------------------------------

    def _values(self, port: int | None) -> dict[str, str]:
        return {
            "python": world_python(),
            "recipe": str(self.recipe.root),
            "archetypes": str(ARCHETYPES),
            "port": str(port) if port is not None else "",
            "seed": str(self.seed),
            "state": str(self.state_dir),
        }

    def _argv(self, template: Sequence[str], port: int | None) -> list[str]:
        values = self._values(port)
        return [expand_env(PLACEHOLDER.sub(lambda match: values[match.group(1)], word)) for word in template]

    def _open(self) -> None:
        port = _free_port() if self.recipe.start is not None or "{port}" in str(self.recipe.http) else None
        if self.recipe.start is not None:
            self._service_log = (self.state_dir / "service.log").open("a")
            # stdin stays open and unread by us: a service that watches it
            # sees the end of file when the daemon dies, and exits.
            self._service = subprocess.Popen(
                self._argv(self.recipe.start["command"], port), stdin=subprocess.PIPE,
                stdout=self._service_log, stderr=self._service_log, text=True,
                cwd=self.recipe.root, env=child_environment({"PYTHONDONTWRITEBYTECODE": "1"}),
            )
        if self.recipe.backend == "http":
            assert self.recipe.http is not None
            values = self._values(port)
            self._base_url = PLACEHOLDER.sub(lambda match: values[match.group(1)], self.recipe.http["base_url"]).rstrip("/")
            if self.recipe.start is not None:
                self._await_ready()
            return
        assert self.recipe.mcp is not None
        environment = {name: expand_env(str(value)) for name, value in self.recipe.mcp.get("env", {}).items()}
        try:
            self._mcp = McpClient(
                self._argv(self.recipe.mcp["command"], port),
                env=child_environment({"PYTHONDONTWRITEBYTECODE": "1", **environment}),
                cwd=self.recipe.root, stderr_log=self.state_dir / "mcp.log", timeout=self.recipe.timeout_seconds,
            )
            tools = {str(tool.get("name")) for tool in self._mcp.list_tools()}
        except McpError as error:
            raise RuntimeError(f"{self.recipe.name}: the MCP server failed to start a session: {error}") from error
        wanted = [str(spec["tool"]) for spec in self.recipe.calls.values()]
        wanted += [str(entry["call"]["tool"]) for entry in self.recipe.dashboard]
        if self.recipe.verdict is not None:
            wanted.append(str(self.recipe.verdict["call"]["tool"]))
        if self.recipe.wait is not None and "call" in self.recipe.wait:
            wanted.append(str(self.recipe.wait["call"]["tool"]))
        missing = sorted({tool for tool in wanted if tool not in tools})
        if missing:
            raise RuntimeError(f"{self.recipe.name}: the MCP server offers no tool {missing}; it offers {sorted(tools)}")

    def _await_ready(self) -> None:
        assert self.recipe.start is not None
        deadline = time.monotonic() + float(self.recipe.start.get("timeout_seconds", 20))
        path = self.recipe.start.get("ready") or "/"
        while time.monotonic() < deadline:
            if self._service is not None and self._service.poll() is not None:
                raise RuntimeError(f"{self.recipe.name}: the local service exited at start; see .assay/world_state/service.log")
            try:
                request = urllib.request.Request(self._base_url + path, headers={INTERNAL_HEADER: "1"})
                with urllib.request.urlopen(request, timeout=2) as answer:
                    if answer.status < 500:
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.05)
        raise RuntimeError(f"{self.recipe.name}: the local service did not answer {path} in time")

    def _shutdown(self) -> None:
        if self._mcp is not None:
            self._mcp.close()
            self._mcp = None
        service, self._service = self._service, None
        if service is not None and service.poll() is None:
            try:
                if service.stdin is not None:
                    service.stdin.close()
                service.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                service.terminate()
                try:
                    service.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    service.kill()
        if self._service_log is not None:
            self._service_log.close()
            self._service_log = None

    # -- calls ----------------------------------------------------------------

    def _paid(self, action: str, params: Mapping[str, Any]) -> None:
        cap = self.recipe.caps.get(action)
        if cap is not None and self.uses.get(action, 0) >= cap:
            self.refusals += 1
            self.last_result = {"action": action, "status": "refused", "detail": f"{action} is capped at {cap} uses in this run"}
            return
        spec = self.recipe.calls[action]
        ok, raw, shown, detail = self._http(spec, params) if self.recipe.backend == "http" else self._tool(spec, params)
        self.calls += 1
        saved = Path("responses") / f"{self.calls:04d}-{action}.json"
        (self.state_dir / saved).write_text(json.dumps(raw, indent=1, sort_keys=True) + "\n")
        self.response = {**trim(shown, self.recipe.view), "saved": (Path(*STATE_PARTS) / saved).as_posix()}
        if ok:
            self.uses[action] = self.uses.get(action, 0) + 1
        else:
            self.errors += 1
            self.refusals += 1
        self.last_result = {"action": action, "status": "ok" if ok else "refused", "detail": detail}

    def _http(self, spec: Mapping[str, Any], params: Mapping[str, Any]) -> tuple[bool, Any, dict[str, Any], str]:
        path = spec["path"]
        for name in spec.get("path_params", []):
            path = path.replace("{" + name + "}", urllib.parse.quote(str(params[name]), safe=""))
        query = params.get("query") if spec.get("query") else None
        body = params.get("body") if spec.get("body") else None
        status, headers, parsed = self._request(spec["method"], path, query, body, internal=False)
        shown_names = [name.lower() for name in (self.recipe.http or {}).get("shown_headers", SHOWN_HEADERS)]
        shown_headers = {name: value for name, value in sorted(headers.items()) if name in shown_names}
        ok = status < 400
        detail = f"{spec['method']} {path} answered {status}" + ("" if ok else f": {_error_excerpt(parsed)}")
        raw = {"status": status, "headers": headers, "body": parsed}
        return ok, raw, {"status": status, "headers": shown_headers, "body": parsed}, detail

    def _request(
        self, method: str, path: str, query: Mapping[str, Any] | None, body: Any, *, internal: bool
    ) -> tuple[int, dict[str, str], Any]:
        url = self._base_url + path
        if query:
            pairs = []
            for key, value in query.items():
                for item in value if isinstance(value, list) else [value]:
                    pairs.append((key, json.dumps(item) if isinstance(item, bool) else str(item)))
            url += "?" + urllib.parse.urlencode(pairs)
        headers = {"Accept": "application/json"}
        for name, value in ((self.recipe.http or {}).get("headers") or {}).items():
            headers[name] = expand_env(str(value))
        if internal:
            headers[INTERNAL_HEADER] = "1"
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.recipe.timeout_seconds) as answer:
                status, answer_headers, raw = answer.status, dict(answer.headers.items()), answer.read()
        except urllib.error.HTTPError as error:
            status, answer_headers, raw = error.code, dict(error.headers.items()), error.read()
        except (urllib.error.URLError, OSError) as error:
            raise RuntimeError(f"{self.recipe.name}: {method} {path} did not reach the service: {error}") from error
        text = raw.decode("utf-8", errors="replace").replace(self._base_url, "")
        try:
            parsed: Any = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            parsed = text
        normalized = {name.lower(): value.replace(self._base_url, "") for name, value in answer_headers.items()}
        return status, normalized, parsed

    def _tool(self, spec: Mapping[str, Any], params: Mapping[str, Any]) -> tuple[bool, Any, dict[str, Any], str]:
        form = spec.get("arguments", "object")
        arguments = dict(params) if form == "flat" else dict(params.get("arguments") or {}) if form == "object" else {}
        assert self._mcp is not None
        try:
            result = self._mcp.call_tool(spec["tool"], arguments)
        except McpError as error:
            if error.code is None:
                raise RuntimeError(f"{self.recipe.name}: the MCP server did not answer {spec['tool']}: {error}") from error
            shown = {"is_error": True, "structured": None, "text": str(error)}
            return False, {"error": {"code": error.code, "message": str(error)}}, shown, f"{spec['tool']} refused: {error}"
        is_error = bool(result.get("isError"))
        text = _content_text(result.get("content"))
        shown = {"is_error": is_error, "structured": result.get("structuredContent"), "text": text}
        detail = f"{spec['tool']} answered" + (f" with an error: {text[:240]}" if is_error else "")
        return not is_error, result, shown, detail

    def _fixed(self, call: Mapping[str, Any], values: Mapping[str, Any] | None = None) -> Any:
        """A call the engine makes for itself: a dashboard read, the verdict,
        the wait. Never counted, never capped. A "{seconds}" string in its
        arguments takes the WAIT's value."""

        def fill(value: Any) -> Any:
            if isinstance(value, str) and values and value in {"{" + key + "}" for key in values}:
                return values[value[1:-1]]
            if isinstance(value, dict):
                return {key: fill(item) for key, item in value.items()}
            if isinstance(value, list):
                return [fill(item) for item in value]
            return value

        if self.recipe.backend == "http":
            status, _, parsed = self._request(
                call["method"], call["path"], fill(call.get("query")), fill(call.get("body")), internal=True
            )
            return parsed if status < 400 else {"error": status}
        assert self._mcp is not None
        try:
            result = self._mcp.call_tool(call["tool"], fill(call.get("arguments") or {}))
        except McpError as error:
            return {"error": str(error)}
        if result.get("isError"):
            return {"error": _content_text(result.get("content"))}
        structured = result.get("structuredContent")
        return structured if structured is not None else _content_text(result.get("content"))

    def _refresh(self) -> None:
        board: dict[str, Any] = {}
        for entry in self.recipe.dashboard:
            value = self._fixed(entry["call"])
            if entry.get("select"):
                selected = read_path(value, entry["select"])
                value = None if selected is MISSING else selected
            board[entry["name"]] = trim(value, self.recipe.view)
        self.dashboard = board
        if self.recipe.verdict is not None:
            value = self._fixed(self.recipe.verdict["call"])
            if self.recipe.verdict.get("select"):
                value = read_path(value, self.recipe.verdict["select"])
            expected = self.recipe.verdict.get("equals", True)
            self._verdict = value is not MISSING and (value == expected and type(value) is type(expected))

    # -- the rules ------------------------------------------------------------

    def _view(self) -> dict[str, Any]:
        view: dict[str, Any] = {
            "response": self.response,
            "calls": self.calls,
            "errors": self.errors,
            "last_result": dict(self.last_result),
            "refusals": self.refusals,
        }
        if self.recipe.dashboard:
            view["dashboard"] = dict(self.dashboard)
        return view

    def _won(self, view: Mapping[str, Any]) -> bool:
        if self.recipe.win is None and self.recipe.verdict is None:
            return False
        rule = self.recipe.win is None or holds(self.recipe.win, view)
        return rule and (self.recipe.verdict is None or self._verdict)

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

    def _completed(self, view: Mapping[str, Any], won: bool) -> int:
        spec = self.recipe.levels_completed
        if isinstance(spec, Mapping):
            value = read_path(view, spec["path"])
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        if isinstance(spec, int):
            return spec
        return 1 if won else 0

    def _total(self, view: Mapping[str, Any]) -> int:
        spec = self.recipe.win_levels
        if isinstance(spec, Mapping):
            value = read_path(view, spec["path"])
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1
        if isinstance(spec, int):
            return spec
        return 1


def _content_text(content: Any) -> str:
    """The text items of an MCP result joined; other content named by type."""
    parts = []
    for item in content or []:
        if isinstance(item, dict):
            parts.append(str(item.get("text", "")) if item.get("type") == "text" else f"[{item.get('type', 'content')}]")
    return "\n".join(parts)

