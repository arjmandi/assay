# FLE API reference: how a run gets it

Published FLE agents receive the full Factorio Learning Environment API
reference in their prompt. A run that wants to be comparable to those numbers
must give the agent the same thing. This note says how, and what the ASSAY
adapter changes about it.

The reference is **tier-2 starting information**, the same tier as the ARC
hint line. It is a fact about the world handed over before play, not something
earned by acting. `zero_prior: true` in the registry withholds it (and the
action descriptions with it); that is the ablation, not the default.

## Getting the reference

FLE generates it. It is not vendored here: it is ~117,000 characters, it is
generated from the installed package, and a stale copy would silently drift
from the version actually under test.

```python
from fle.env.instance import FactorioInstance

instance = FactorioInstance(address="localhost", tcp_port=27000, fast=True,
                            inventory={}, all_technologies_researched=True,
                            num_agents=1)
reference = instance.get_system_prompt()
```

Three fenced sections come back, in this order:

1. **`types`** (~815 lines), the enums and models a program names:
   `Prototype`, `Resource`, `RecipeName`, `Technology`, `Direction`,
   `Position`, `BoundingBox`, `Entity` and its subclasses, `Inventory`,
   `EntityStatus`, and the group types (`BeltGroup`, `PipeGroup`,
   `ElectricityGroup`).
2. **`methods`** (~240 lines), every agent-facing tool with its signature and
   docstring.
3. **the tool manual**, worked examples per tool.

Inject it as a prefix to the run's starting information. It is static for a
given FLE version, so it prompt-caches after the first step.

## The tool inventory

FLE 0.4.3 attaches these to the program namespace. Admin tools
(`set_inventory`, `clear_entities`, `regenerate_resources`, `load_blueprint`,
…) are attached under a leading underscore and are not agent-facing.

- **Queries**: `inspect_inventory`, `get_entity`, `get_entities`, `nearest`,
  `nearest_buildable`, `get_resource_patch`, `get_prototype_recipe`,
  `get_research_progress`, `get_connection_amount`, `can_place_entity`, `score`
- **Building**: `place_entity`, `place_entity_next_to`, `rotate_entity`,
  `shift_entity`, `pickup_entity`, `connect_entities`, `set_entity_recipe`
- **Items**: `craft_item`, `insert_item`, `extract_item`, `harvest_resource`
- **Movement**: `move_to`
- **Research**: `set_research`
- **Endgame**: `launch_rocket`
- **Multi-agent**: `send_message` (out of scope: ASSAY is single-agent)
- **Time**: `sleep`, **refused under this adapter, see below**

## What this adapter changes

An agent handed the stock FLE reference and nothing else will make three
mistakes. State these alongside the reference.

1. **Source is base64.** `RUN program=<base64 of the UTF-8 source>`. ASSAY
   action tokens are whitespace-split, so raw source cannot be passed inline.
2. **`sleep()` does nothing here, and is refused.** FLE's `sleep()` increments
   a bookkeeping counter and then sleeps in wall-clock; it does not advance the
   simulation. This adapter keeps the game paused and moves time only through
   the `WAIT ticks=<int>` actuator; 60 ticks is one in-game second, 3600 is
   one measurement window. Pathfinding calls (`move_to`, `connect_entities`)
   get their ticks automatically; see PROTOCOL.md.
3. **The namespace is screened.** Imports, `eval`/`exec`/`open`/`getattr`,
   underscore-prefixed names and attributes, and every handle that leads out of
   the game are refused before execution. Ordinary FLE programs, calls,
   loops, `def`, `try`, are unaffected. `NAMESPACE_AUDIT.md` says why this
   exists and exactly what it blocks.

Everything else is FLE as shipped: the namespace persists across `RUN`s, a
variable assigned in one program is visible in the next, `print()` output comes
back as the observation's `stdout`, and an exception comes back as `stderr`
rather than ending the run.
