# The service archetype

A remote system a program drives, an MCP server over stdio or an HTTP API,
as a world, run by the service engine (`../service_world.py`).

Each paid action is one call to the service:
- an MCP tool call, with the action's parameters as the tool's arguments; or
- an HTTP request, with path parameters, a `query` object and a `body`
  object.

The observation holds four parts:
- `response`: the call's answer, trimmed. For HTTP it holds the status, the
  headers that matter and the body. For MCP it holds `is_error`, the
  structured content and the text. `response.saved` names the file under
  `.assay/world_state/responses/` that holds the full answer.
- `dashboard`: reads with fixed arguments, made after every action, free and
  never counted. They give the agent a before and after to predict against.
- `calls` and `errors`: how many paid calls were made, and how many the
  service answered with an error.
- `last_result` and `refusals`, as in every catalog world.

An error answer is the world refusing, so the spend is journaled as evidence.
That covers an HTTP status of 400 or more, an MCP result with `isError`, and
a JSON-RPC error. A service that cannot be reached, or does not answer in
time, is a fault: the engine raises, and the kernel spends nothing.

A remote service changes outside the journal, so a service world declares no
replay by default and lives one daemon long. A local service that starts the
same every time can declare `"replayable": true`, as the template and the
legacy-api pack do.

## Draft the registry from the service

```bash
python3 make_registry.py mcp -- npx -y @playwright/mcp@latest     # an MCP server's tools
python3 make_registry.py openapi openapi.json --base-url https://api.example.com
```

The script prints a draft: the registry's actions, the recipe's call map, and
notes on everything the conversion dropped. With `--out DIR` it writes
`registry.json` and `recipe.json` drafts. The conversion works like this:

- Tool names and operation ids become action names (`listCustomers` becomes
  LIST_CUSTOMERS).
- A tool whose arguments are all required takes them flat
  (`ADD_NOTE title=...`). Any other tool takes one `arguments` object.
- An HTTP operation takes its path parameters flat, its query parameters as
  a `query` object, and its JSON body as a `body` object.
- **Flags follow the service's own hints, which the MCP spec calls untrusted,
  so confirm them with the user (Q4).** A tool not marked read-only and not
  marked non-destructive becomes `destructive`. An HTTP DELETE or PUT becomes
  `destructive`.

## The recipe's service block

```json
"service": {
  "backend": "http",
  "start": {"command": ["{python}", "{recipe}/server.py", "--port", "{port}", "--seed", "{seed}", "--state", "{state}"],
            "ready": "/health", "timeout_seconds": 20},
  "http": {"base_url": "http://127.0.0.1:{port}", "headers": {"Authorization": "Bearer ${env:API_TOKEN}"}},
  "actions": {"LIST_CUSTOMERS": {"method": "GET", "path": "/v1/customers", "query": true},
              "GET_CUSTOMER": {"method": "GET", "path": "/v1/customers/{id}", "path_params": ["id"]}},
  "wait": {"call": {"method": "POST", "path": "/__world/clock", "body": {"advance": "{seconds}"}}},
  "dashboard": [{"name": "imported", "call": {"method": "GET", "path": "/v2/import/count"}, "select": "count"}],
  "verdict": {"call": {"method": "GET", "path": "/__world/verdict"}, "select": "complete"},
  "caps": {"REMOVE_IMPORTED": 3},
  "view": {"max_items": 50, "max_string": 500, "max_depth": 8},
  "replayable": false,
  "reset": "none"
}
```

For an MCP backend, `"mcp": {"command": [...], "env": {...}}` replaces `http`,
and each call is `{"tool": NAME, "arguments": "flat" | "object" | "none"}`.
The engine starts the server, lists its tools, and refuses to start when a
call names a tool the server lacks.

- **`start`**: a local service the session starts and stops, such as a mock
  or a dev server, on a free `{port}`. The engine waits for `ready` to answer.
  The service's stdin stays open and unread, so a service that watches it
  can exit when the daemon is gone.
- **`${env:NAME}`**: in a header or an MCP server's environment, replaced
  from the daemon's environment. Name the variable in the registry's
  `secrets` so its value is scrubbed from the journal. A credential lives
  only in the daemon's environment, never in the player's.
- **`wait`**: the built-in WAIT action (`seconds`), which must be in the
  registry. It is either a call that advances a mock's clock or, with
  `{"sleep_max_seconds": N}`, real time. Rate limits are the most common
  thing an agent has to learn about a service.
- **`dashboard`**: each read lands under `dashboard.<name>`. `select` is a
  dotted path into the answer (into the structured content, for MCP).
- **`verdict`**: a call whose answer decides the WIN and never enters the
  data, as a game's engine keeps its own win state. The world's `win` rule,
  when there is one, must hold as well.
- **`caps`**: a scarce commit. Uses past the cap are refused through the
  observation.
- **`reset`**: `none` (a RESET changes nothing) or `restart` (the service
  starts afresh). Restart fits a local service with one progress unit.

Plan around the world's clock. A call should answer within seconds, and
`world.timeout_seconds` bounds each one.

## A website

A website without an API is a service through a browser's MCP server, such
as Playwright's (`npx -y @playwright/mcp@latest`), with each page action a
tool call. Prefer a compact page view to screenshots: an accessibility
snapshot, the URL, the title, the extracted values. A view that changes on
every action makes `change` always hold, and the predictions stop carrying
information. Nothing in the test suite runs a browser, so treat this path as
the least proven one.

## The template

`template/` runs as it is. `notes_server.py` is a notebook MCP server in the
standard library; it starts with four notes chosen by the seed. The world is
won when the notebook holds exactly three notes, one titled groceries. Its
delete is destructive and capped at two uses, so the goal takes a plan. To
start a world of your own:
1. copy `template/`;
2. run `make_registry.py` against your service;
3. merge its draft into `recipe.json` and `registry.json`;
4. write the goal, the dashboard and the rules;
5. keep `adapter.py` as it is.
