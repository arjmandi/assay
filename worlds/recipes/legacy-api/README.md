# legacy-api

A legacy CRM (v1) and the customer hub that replaces it (v2), as one local
HTTP service built from the run's seed. The task is the migration every team
has met: move the active customers into the hub with their ids, names and
emails, and leave the archived ones behind. `openapi.json` is the API as its
teams documented it. The registry is drafted from it by
`worlds/archetypes/service/make_registry.py`, plus the built-in WAIT. Where
the documentation is silent or wrong, the agent has to find out by asking
the service.

```
WORLD CARD    legacy-api                     code S-E-C-U   family Solve   fit yes   clock s
acts          a service: two HTTP APIs       service archetype, http backend, local service, replayable
answers       exactly                        states: response.status, dashboard.imported, errors, refusals
done when     the hub holds exactly the active customers, well formed   the hub's own verdict, hidden
worst case    a mess you can undo            REMOVE_IMPORTED is destructive; RESET restarts both systems
proxy         a local copy of both systems   rebuilt from the seed; a real migration comes after
breaks first  layer 3: paging and the rate limit (429 answers, refusals)
hypothesis    vs Claude Code + /goal: pages until a short page, imports archived customers or skips a page, and says done when the counts look right
```

## Run it

From an operator's shell, in a fresh directory:

```bash
assay start legacy1 \
    --adapter <repo>/worlds/recipes/legacy-api/adapter.py:factory \
    --registry <repo>/worlds/recipes/legacy-api/registry.json \
    --seed 3 --owner-token-file <tokens>/legacy1.token
```

Or let `/assay:attach` start it. The engine starts `legacy_crm.py` on a free
port and stops it when the run ends. Score the hub of any run, or of a
baseline that drove its own copy of the service:

```bash
python3 <repo>/worlds/recipes/legacy-api/checks/score.py --run <run dir>
python3 <repo>/worlds/recipes/legacy-api/checks/score.py --seed 3 --hub <hub.json> --json
```

## What it costs

A clean migration is about ten paid actions: four or five pages, five
imports of twenty. Finding the documentation's gaps costs more, and the
budget of 60 leaves room for that. Each call answers in milliseconds.

## Spoilers: what the documentation leaves out

This section is for operators. A player that reads it is no longer learning
the world.

- Pages count from 0. Without `per_page` a page holds 10. A page never holds
  more than 25, whatever `per_page` asks, though the documentation says 100.
  The list says nothing about how many pages there are: the first empty page
  is the end.
- Without `status`, the list includes archived customers. `GET_CUSTOMER` on
  an archived customer answers 404, and `COUNT_CUSTOMERS` counts the archived
  ones too.
- The legacy API allows 6 requests in 10 seconds of the service's clock. Each
  request moves the clock one second; the seventh within the window answers
  429 with `Retry-After`. WAIT moves the clock.
- The hub takes at most 20 customers per import, though the documentation
  says 100 (413 past that). An email must be lowercase with no surrounding
  spaces (422). An import is all or nothing, and a customer imported twice
  is a conflict (409).
