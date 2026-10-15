"""The service archetype (worlds/archetypes/service_world.py): a remote system a
program drives, an MCP server over stdio or an HTTP API, as a world. The
recipe's checks, the stdlib MCP client against the template's notebook
server, the registry drafted from a tool list and from an OpenAPI document,
and two worlds through the real CLI and daemon: the notebook template (MCP:
a refusal through the observation, a cap, a resume that replays, a WIN by the
dashboard) and the legacy-api pack (HTTP: the documentation's gaps, the rate
limit and WAIT, a resume that replays, the hidden verdict, the checker)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
ARCHETYPES = REPO / "worlds" / "archetypes"
TEMPLATE = ARCHETYPES / "service" / "template"
PACK = REPO / "worlds" / "recipes" / "legacy-api"

sys.path.insert(0, str(ARCHETYPES))
sys.path.insert(0, str(ARCHETYPES / "service"))
sys.path.insert(0, str(PACK))
import command_world  # noqa: E402
import legacy_crm  # noqa: E402
import make_registry  # noqa: E402
import service_world  # noqa: E402
from mcp_client import McpClient, McpError  # noqa: E402

NOTES = [sys.executable, str(TEMPLATE / "notes_server.py"), "--seed", "2"]


def _recipe(tmp_path: Path, mutate) -> Path:
    raw = json.loads((TEMPLATE / "recipe.json").read_text())
    mutate(raw)
    target = tmp_path / "recipe" / "recipe.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(raw))
    return target


# --- the recipe --------------------------------------------------------------------


def test_the_template_and_the_pack_load():
    notebook = service_world.load_service_recipe(TEMPLATE / "recipe.json")
    assert notebook.backend == "mcp" and notebook.replayable and notebook.reset == "restart"
    assert notebook.caps == {"DELETE_NOTE": 2}
    legacy = service_world.load_service_recipe(PACK / "recipe.json")
    assert legacy.backend == "http" and legacy.verdict is not None and "WAIT" in legacy.actions
    assert legacy.view == service_world.VIEW_DEFAULTS


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["world"].update(mode="copy"), 'world.mode: a service recipe says "service"'),
        (lambda raw: raw["service"].update(backend="grpc"), "service.backend: one of ['mcp', 'http']"),
        (lambda raw: raw["world"]["actions"].append("RENAME_NOTE"), "service.actions: RENAME_NOTE has no call"),
        (lambda raw: raw["service"]["actions"].update(EXTRA={"tool": "x"}), "service.actions: EXTRA is not in world.actions"),
        (lambda raw: raw["service"]["actions"]["ADD_NOTE"].update(arguments="positional"),
         "service.actions.ADD_NOTE: arguments is one of ['flat', 'object', 'none']"),
        (lambda raw: raw["service"]["mcp"].update(command=["{python}", "{interpreter}"]),
         "service.mcp.command: {interpreter} is not a placeholder here"),
        (lambda raw: raw["service"].update(caps={"LIST_NOTES": 0}), "service.caps.LIST_NOTES: an action with a call"),
        (lambda raw: raw["service"].update(reset="rewind"), "service.reset: one of ['none', 'restart']"),
        (lambda raw: raw["service"]["dashboard"].append({"name": "Count", "call": {"tool": "count_notes"}}),
         "service.dashboard[2]: a unique lowercase name"),
        (lambda raw: raw["world"]["actions"].append("WAIT"), "service.actions: WAIT has no call"),
        (lambda raw: raw["service"].update(retries=3), "service.retries: not a service key"),
    ],
)
def test_a_service_recipe_that_does_not_fit_is_refused_by_field(tmp_path, mutate, message):
    with pytest.raises(command_world.RecipeError) as caught:
        service_world.load_service_recipe(_recipe(tmp_path, mutate))
    assert message in str(caught.value)


def test_an_http_call_must_name_its_path_parameters(tmp_path):
    raw = json.loads((PACK / "recipe.json").read_text())
    raw["service"]["actions"]["GET_CUSTOMER"]["path_params"] = []
    target = tmp_path / "recipe.json"
    target.write_text(json.dumps(raw))
    with pytest.raises(command_world.RecipeError, match=r"path_params must name the path's parameters, \['id'\]"):
        service_world.load_service_recipe(target)


def test_the_command_engine_points_a_service_recipe_to_its_loader():
    with pytest.raises(command_world.RecipeError, match="a service recipe loads with service_world.load_service_recipe"):
        command_world.load_recipe(TEMPLATE / "recipe.json")


def test_trim_keeps_the_observation_compact():
    view = {"max_items": 2, "max_string": 5, "max_depth": 2}
    assert service_world.trim([1, 2, 3, 4], view) == [1, 2, "... 2 more items"]
    assert service_world.trim("abcdefgh", view) == "abcde... (8 characters)"
    assert service_world.trim({"a": {"b": {"c": 1}}}, view) == {"a": {"b": "{...}"}}


def test_env_references_come_from_the_daemons_environment(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "s3cret")
    assert service_world.expand_env("Bearer ${env:SERVICE_TOKEN}") == "Bearer s3cret"
    monkeypatch.delenv("SERVICE_TOKEN")
    with pytest.raises(RuntimeError, match="the environment has no SERVICE_TOKEN"):
        service_world.expand_env("Bearer ${env:SERVICE_TOKEN}")


# --- the MCP client ------------------------------------------------------------------


def test_the_mcp_client_against_the_notebook_server(tmp_path):
    client = McpClient(NOTES, stderr_log=tmp_path / "server.log", timeout=10)
    try:
        assert client.server_info == {"name": "notebook", "version": "1"}
        tools = {tool["name"]: tool for tool in client.list_tools()}
        assert sorted(tools) == ["add_note", "count_notes", "delete_note", "find_note", "list_notes"]
        assert tools["delete_note"]["annotations"]["destructiveHint"] is True
        # The server pinged the client after initialization; the session goes on.
        added = client.call_tool("add_note", {"title": "groceries", "body": "milk"})
        assert added["structuredContent"] == {"id": 5, "count": 5} and added["isError"] is False
        refused = client.call_tool("delete_note", {"id": 99})
        assert refused["isError"] is True and refused["content"][0]["text"] == "no note 99"
        with pytest.raises(McpError) as caught:
            client.call_tool("rename_note", {})
        assert caught.value.code == -32602
    finally:
        client.close()
    assert client.process.poll() is not None


def test_a_server_that_cannot_start_is_an_error(tmp_path):
    with pytest.raises(McpError, match="could not start"):
        McpClient([str(tmp_path / "no-such-server")])


# --- the registry, drafted from the service --------------------------------------------


def test_the_draft_from_the_openapi_document_is_the_packs_registry():
    draft = make_registry.from_openapi(json.loads((PACK / "openapi.json").read_text()), "http://127.0.0.1:{port}")
    registry = json.loads((PACK / "registry.json").read_text())
    recipe = json.loads((PACK / "recipe.json").read_text())
    assert draft["registry"]["actions"] == [action for action in registry["actions"] if action["name"] != "WAIT"]
    assert draft["service"]["actions"] == recipe["service"]["actions"]
    assert draft["notes"] == []
    assert [action["name"] for action in draft["registry"]["actions"] if action.get("destructive")] == ["REMOVE_IMPORTED"]


def test_the_draft_from_an_mcp_server_follows_its_hints(tmp_path):
    client = McpClient(NOTES, timeout=10)
    try:
        draft = make_registry.from_mcp_tools(client.list_tools())
    finally:
        client.close()
    actions = {action["name"]: action for action in draft["registry"]["actions"]}
    assert [name for name, action in actions.items() if action.get("destructive")] == ["DELETE_NOTE"]
    assert draft["service"]["actions"]["ADD_NOTE"] == {"tool": "add_note", "arguments": "flat"}
    assert draft["service"]["actions"]["COUNT_NOTES"] == {"tool": "count_notes", "arguments": "none"}
    assert actions["ADD_NOTE"]["params"]["title"] == {"type": "string", "minLength": 1, "maxLength": 80}


def test_the_conversion_keeps_what_the_registry_checks_and_names_what_it_drops():
    converter = make_registry.Converter({"schemas": {"Point": {"type": "object", "properties": {"x": {"type": "number"}}}}})
    assert converter.schema({"$ref": "#/components/schemas/Point"}, "p") == {
        "type": "object", "properties": {"x": {"type": "number"}},
    }
    merged = converter.schema({"allOf": [{"properties": {"a": {"type": "string"}}, "required": ["a"]},
                                         {"properties": {"b": {"type": ["integer", "null"], "format": "int64"}}}]}, "m")
    assert merged == {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}, "required": ["a"]}
    assert converter.schema({"oneOf": [{"type": "string"}, {"type": "integer"}]}, "o") == {"type": "string"}
    assert converter.schema({"type": "object", "properties": {"bad-name": {"type": "string"}}}, "d") is None
    assert converter.schema({"type": "object", "additionalProperties": True}, "f") is None
    assert converter.notes == [
        "o: oneOf narrowed to its first option",
        "d.bad-name: a property name the registry cannot hold; dropped",
        "d: an object with no property the registry can hold; dropped",
        "f: an object with no property the registry can hold; dropped",
    ]


def test_action_names_fit_the_registry():
    taken: set[str] = set()
    assert make_registry.action_name("listCustomers", taken) == "LIST_CUSTOMERS"
    assert make_registry.action_name("browser.navigate-to", taken) == "BROWSER_NAVIGATE_TO"
    assert make_registry.action_name("3d_render", taken) == "A_3D_RENDER"
    assert make_registry.action_name("reset", taken) == "RESET_2"
    assert make_registry.action_name("list_customers", taken) == "LIST_CUSTOMERS_2"
    long_name = make_registry.action_name("a_very_long_tool_name_that_goes_past_the_limit", taken)
    assert len(long_name) == 32 and long_name.isupper()


# --- the notebook template, through the real CLI and daemon ------------------------------


def _event(run: Path) -> dict:
    lines = [line for line in (run / ".assay" / "events.jsonl").read_text().splitlines() if line.strip()]
    return json.loads(lines[-1])["observation"]


def _destroy(run: Path, note: int, predict: str):
    return run_cli(
        run, "act", "DELETE_NOTE", f"id={note}", "--predict", predict,
        "--declare", "worst_case=a note is lost", "--declare", "recovery=reset restarts the notebook",
    )


def test_the_notebook_template_end_to_end(tmp_path):
    run = tmp_path / "notebook"
    run.mkdir()
    adapter = f"{TEMPLATE / 'adapter.py'}:factory"
    try:
        started = run_cli(run, "start", "notebook1", "--adapter", adapter, "--registry", str(TEMPLATE / "registry.json"),
                          "--seed", "2", "--owner-token-file", str(tmp_path / "notebook.token"))
        assert started.returncode == 0, started.stderr
        assert "STARTED | notebook1 | local simulator | no action-idle lease | replay recovery enabled" in started.stdout
        first = _event(run)
        assert first["dashboard"] == {"count": 4, "groceries": False} and first["response"] is None
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["public_info"]["archetype"] == "service" and config["public_info"]["backend"] == "mcp"
        for name, path in (("count", "dashboard.count"), ("calls", "calls"), ("refusals", "refusals")):
            assert run_cli(run, "state", "declare", name, "--path", path).returncode == 0
        added = run_cli(run, "act", "ADD_NOTE", "--params", '{"title": "groceries", "body": "milk"}',
                        "--predict", "ch count = 5; ch calls delta = 1")
        assert added.returncode == 0 and "RESULT | PREDICTED" in added.stdout, added.stdout
        response = _event(run)["response"]
        assert response["structured"] == {"id": 5, "count": 5} and response["saved"].startswith(".assay/world_state/responses/")
        assert json.loads((run / response["saved"]).read_text())["structuredContent"] == {"id": 5, "count": 5}
        # An error answer is the world refusing; it does not use the cap.
        missing = _destroy(run, 99, "ch refusals delta = 1; ch count = 5")
        assert missing.returncode == 0 and "RESULT | PREDICTED" in missing.stdout, missing.stdout
        assert _event(run)["last_result"] == {"action": "DELETE_NOTE", "status": "refused",
                                              "detail": "delete_note answered with an error: no note 99"}
        assert _destroy(run, 1, "ch count = 4").returncode == 0
        # A resume restarts the server and replays the calls.
        assert run_cli(run, "stop").returncode == 0
        resumed = run_cli(run, "start", "notebook1", "--adapter", adapter, "--seed", "2")
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED | notebook1 | local simulator | replayed 3 paid actions" in resumed.stdout
        won = _destroy(run, 2, "win; ch count = 3")
        assert won.returncode == 0 and "RESULT | GAME_COMPLETE" in won.stdout, won.stdout
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)


def test_a_capped_action_is_refused_past_its_cap(tmp_path):
    run = tmp_path / "capped"
    run.mkdir()
    try:
        assert run_cli(run, "start", "notebook2", "--adapter", f"{TEMPLATE / 'adapter.py'}:factory",
                       "--registry", str(TEMPLATE / "registry.json"), "--seed", "2").returncode == 0
        for name, path in (("calls", "calls"), ("refusals", "refusals")):
            assert run_cli(run, "state", "declare", name, "--path", path).returncode == 0
        assert _destroy(run, 1, "change").returncode == 0
        assert _destroy(run, 2, "change").returncode == 0
        capped = _destroy(run, 3, "ch calls delta = 0; ch refusals delta = 1")
        assert capped.returncode == 0 and "RESULT | PREDICTED" in capped.stdout, capped.stdout
        assert _event(run)["last_result"]["detail"] == "DELETE_NOTE is capped at 2 uses in this run"
    finally:
        stop_run(run)


# --- the legacy-api pack, through the real CLI and daemon ---------------------------------


def _act(run: Path, action: str, params: dict, predict: str = "change"):
    acted = run_cli(run, "act", action, "--params", json.dumps(params), "--predict", predict)
    assert acted.returncode == 0, (action, params, acted.stderr)
    return acted, _event(run)


def _active_rows(run: Path) -> list[dict]:
    rows, page = [], 0
    while True:
        _, observed = _act(run, "LIST_CUSTOMERS", {"query": {"page": page, "per_page": 25, "status": "active"}})
        response = observed["response"]
        if response["status"] == 429:
            _act(run, "WAIT", {"seconds": int(response["headers"]["retry-after"])})
            continue
        found = [row for row in response["body"]["data"] if isinstance(row, dict)]
        if not found:
            return rows
        rows.extend(found)
        page += 1


def test_the_legacy_api_pack_end_to_end(tmp_path):
    seed = 5
    run = tmp_path / "legacy"
    run.mkdir()
    adapter = f"{PACK / 'adapter.py'}:factory"
    truth = legacy_crm.customers(seed)
    active = [row for row in truth if row["status"] == "active"]
    archived = next(row for row in truth if row["status"] == "archived")
    try:
        started = run_cli(run, "start", "legacy1", "--adapter", adapter, "--registry", str(PACK / "registry.json"),
                          "--seed", str(seed), "--owner-token-file", str(tmp_path / "legacy.token"))
        assert started.returncode == 0, started.stderr
        assert _event(run)["dashboard"] == {"imported": 0}
        for name, path in (("status", "response.status"), ("imported", "dashboard.imported"), ("refusals", "refusals")):
            assert run_cli(run, "state", "declare", name, "--path", path).returncode == 0
        # The documentation says up to 100 a page; the service sends 25.
        _, observed = _act(run, "LIST_CUSTOMERS", {"query": {"per_page": 100}}, "ch status = 200")
        assert len(observed["response"]["body"]["data"]) == 25
        # An archived customer is listed but cannot be read.
        _, observed = _act(run, "GET_CUSTOMER", {"id": archived["id"]}, "ch status = 404; ch refusals delta = 1")
        assert observed["last_result"]["status"] == "refused"
        # A burst past the rate limit answers 429 with Retry-After; WAIT clears it.
        statuses = []
        for _ in range(6):
            _, observed = _act(run, "COUNT_CUSTOMERS", {})
            statuses.append(observed["response"]["status"])
        assert statuses.count(429) >= 1, statuses
        assert observed["response"]["headers"]["retry-after"].isdigit()
        _act(run, "WAIT", {"seconds": 10})
        _, observed = _act(run, "COUNT_CUSTOMERS", {}, "ch status = 200")
        assert observed["response"]["body"] == {"count": len(truth)}
        # The hub refuses an email that is not lowercase and trimmed, and a batch past 20.
        messy = next(row for row in active if row["email"] != legacy_crm.normalized(row["email"]))
        _, observed = _act(run, "IMPORT_CUSTOMERS", {"body": {"customers": [
            {"id": messy["id"], "name": messy["name"], "email": messy["email"]}]}}, "ch status = 422")
        twenty_one = [{"id": row["id"], "name": row["name"], "email": legacy_crm.normalized(row["email"])} for row in active[:21]]
        _, observed = _act(run, "IMPORT_CUSTOMERS", {"body": {"customers": twenty_one}}, "ch status = 413")
        # Half the migration, then a resume that restarts the service and replays the calls.
        rows = _active_rows(run)
        assert {row["id"] for row in rows} == {row["id"] for row in active}
        records = [{"id": row["id"], "name": row["name"], "email": legacy_crm.normalized(row["email"])} for row in rows]
        batches = [records[index:index + 20] for index in range(0, len(records), 20)]
        _act(run, "IMPORT_CUSTOMERS", {"body": {"customers": batches[0]}}, "ch imported = 20")
        paid = sum(1 for line in (run / ".assay" / "events.jsonl").read_text().splitlines() if line.strip()) - 1
        assert run_cli(run, "stop").returncode == 0
        resumed = run_cli(run, "start", "legacy1", "--adapter", adapter, "--seed", str(seed))
        assert resumed.returncode == 0, resumed.stderr
        assert f"RECOVERED | legacy1 | local simulator | replayed {paid} paid actions" in resumed.stdout
        for batch in batches[1:-1]:
            _act(run, "IMPORT_CUSTOMERS", {"body": {"customers": batch}}, "ch imported delta = 20")
        won = run_cli(run, "act", "IMPORT_CUSTOMERS", "--params", json.dumps({"body": {"customers": batches[-1]}}),
                      "--predict", f"win; ch imported = {len(active)}")
        assert won.returncode == 0 and "RESULT | GAME_COMPLETE" in won.stdout, won.stdout
        scored = subprocess.run([sys.executable, str(PACK / "checks" / "score.py"), "--run", str(run)],
                                capture_output=True, text=True, timeout=60)
        assert scored.returncode == 0, scored.stdout + scored.stderr
        assert scored.stdout.startswith(f"SCORE | legacy-api | seed {seed} | correct")
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)
    # The service the engine started is gone with the run.
    listing = subprocess.run(["ps", "-ww", "-eo", "command="], capture_output=True, text=True).stdout
    assert not [line for line in listing.splitlines() if "legacy_crm.py" in line and str(run) in line]


def test_the_checker_scores_any_hub(tmp_path):
    seed = 4
    truth = legacy_crm.customers(seed)
    active = [row for row in truth if row["status"] == "active"]
    hub = tmp_path / "hub.json"

    # Each pack's checker is checks/score.py: load this one by its path.
    spec = importlib.util.spec_from_file_location("legacy_api_score", PACK / "checks" / "score.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)

    def scored(rows):
        hub.write_text(json.dumps({"customers": rows}))
        return checker.score(seed, hub)

    clean = [{"id": row["id"], "name": row["name"], "email": legacy_crm.normalized(row["email"])} for row in active]
    assert scored(clean)["verdict"] == "correct"
    assert scored(clean[:-3])["verdict"] == "incomplete"
    archived = next(row for row in truth if row["status"] == "archived")
    with_archived = scored([*clean, {"id": archived["id"], "name": archived["name"], "email": "x@example.com"}])
    assert with_archived["verdict"] == "wrong" and with_archived["archived"] == [archived["id"]]
    messy = next(row for row in active if row["email"] != legacy_crm.normalized(row["email"]))
    malformed = [dict(row, email=messy["email"]) if row["id"] == messy["id"] else row for row in clean]
    assert scored(malformed)["malformed"] == [messy["id"]]


def test_the_legacy_world_is_deterministic_and_its_clock_is_logical(tmp_path):
    assert legacy_crm.customers(8) == legacy_crm.customers(8)
    assert legacy_crm.customers(8) != legacy_crm.customers(9)
    world = legacy_crm.World(8, tmp_path / "state")
    waits = [world.tick(limited=True) for _ in range(7)]
    assert waits[:6] == [None] * 6 and waits[6] == 4
    world.clock += 10
    assert world.tick(limited=True) is None
    assert world.verdict() is False and json.loads((tmp_path / "state" / "hub.json").read_text()) == {"customers": []}
