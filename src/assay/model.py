"""The generalized world-model tier for registry runs.

The grid `rules.py` tier is this law's grid instance; registry runs get the
general form. The agent writes `model.py` in the run root:

    STATES = ["counter", "lamp"]          # declared state set (registered names)

    def next(obs, action, params):        # the model: predict the next observation
        ...                               # return the predicted obs dict, or None
                                          # (= Unknown) when out of scope

    def actions(obs):                     # optional, required for solve:
        return [("INC", None), ...]       #   candidate (action, params) pairs

    def key(obs):                         # optional: dedup key for search
        return json.dumps(obs["data"], sort_keys=True)

Trust is exactly replay-fit; no other trust states exist:

- `assay model replay` re-predicts every recorded paid transition in the verifier
  sandbox and grades ONLY the declared states: MISMATCH = declared state
  wrong; INCOMPLETE = Unknown (excluded from fit, reported); undeclared
  states are out of scope. Fit is written to `.assay/model_fit.json`.
- PROMOTION LAW (pinned): a model hash is admitted at the journal event of
  its first replay (`admitted_at_event` in the fit record, read from the
  earliest `model_replay` activity record that carries the hash and its
  event, or this replay when there is none), and only the paid transitions
  recorded after that event are counted, so the model earns rights by
  predicting the future, never by fitting the past (a lookup table over the
  journal fits every recorded transition). Batching rights need missed == 0
  over the whole fit, >= 20 counted transitions AND >= 5 counted transitions
  inside the most recent quarter of the journal (thin-evidence promotion was
  a named failure risk). Rights are void while an ungated event exists or an
  aggregate consequence revoked them.
- `assay model solve --to "ch NAME = V"` searches the model (sandboxed BFS) for
  a plan; every plan step carries machine-generated state predictions,
  marked `machine`; they never enter the agent's prediction meters. Plans carry
  provenance hashes and refuse to run against a changed world or model.
- Imported models NEVER carry rights: the fit record is never exported and
  must be re-earned on the current run's journal.

Known open risk, stated: a model can fit every recorded transition and still
not contain the win condition; replay makes misfit visible, it cannot force
the model universe to contain the answer.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core import (
    AssayError,
    RunPaths,
    append_jsonl,
    atomic_json,
    load_jsonl,
    read_json,
    render_action,
)
from .states import state_value, load_declared
from .sandbox import run_program
from .records import Event

if TYPE_CHECKING:
    from .run import Run

PROMOTION_MIN_GRADED = 20
PROMOTION_MIN_RECENT = 5
SOLVE_DEFAULT_SECONDS = 15.0
SOLVE_DEFAULT_NODES = 100_000
SOLVE_MAX_DEPTH = 40

MODEL_TEMPLATE = '''"""Your world model. Declare states; predict the next observation.

The kernel grades ONLY the states you declare (registered state names).
Return the predicted observation dict from next(), or None when this
transition is outside your model (Unknown: honest, excluded from fit).
"""

STATES = []  # e.g. ["counter", "level"]: registered state names you model


def next(obs, action, params):
    """obs: {"state", "levels_completed", "win_levels", "available_actions",
    "data": {...}} (dict runs) or {"frames": ...} (grid runs).
    Return the predicted obs dict, or None (= Unknown)."""
    return None


def actions(obs):
    """Optional, required for `assay model solve`: candidate (action, params)."""
    return []
'''

_RUNNER = """\
import importlib.util, json, sys, time
payload = json.loads(sys.stdin.read())

spec = importlib.util.spec_from_file_location("assay_model", payload["model_path"])
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)

extractors = {}
for name, entry in payload["states"].items():
    if entry["form"] == "extractor":
        espec = importlib.util.spec_from_file_location("x_" + name, entry["file"])
        emod = importlib.util.module_from_spec(espec)
        espec.loader.exec_module(emod)
        extractors[name] = emod.extract

def read_state(name, obs):
    entry = payload["states"][name]
    if entry["form"] == "host":
        if name == "goal":
            return str(obs.get("state")) == "WIN"
        if name == "level":
            return int(obs.get("levels_completed", 0))
        return None
    if entry["form"] == "path":
        node = obs.get("data", obs.get("observation"))
        for key in entry["path"].split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            elif isinstance(node, list):
                node = node[int(key)]
            else:
                return None
        return node
    return extractors[name](obs)

# STATES names the states the model predicts. CHANNELS, the name before
# 1.2.0, is read for one release.
declared = [str(name) for name in getattr(model, "STATES", getattr(model, "CHANNELS", []))]

if payload["mode"] == "replay":
    results = []
    for item in payload["transitions"]:
        try:
            predicted = model.next(item["before"], item["action"], item["params"])
        except Exception as error:
            results.append({"error": f"{type(error).__name__}: {error}"})
            continue
        if predicted is None or (isinstance(predicted, dict) and predicted.get("__unknown__")):
            results.append({"unknown": True})
            continue
        values = {}
        for name in declared:
            try:
                values[name] = read_state(name, predicted)
            except Exception:
                values[name] = None
        results.append({"predicted": values})
    sys.stdout.write("\\n" + json.dumps({"declared": declared, "results": results}) + "\\n")
else:
    goal = payload["goal"]
    limits = payload["limits"]
    start = payload["start_obs"]
    deadline = time.monotonic() + float(limits["seconds"])
    def obs_key(obs):
        if hasattr(model, "key"):
            return str(model.key(obs))
        return json.dumps(obs, sort_keys=True, default=str)
    def goal_met(obs):
        return read_state(goal["channel"], obs) == goal["value"]
    frontier = [(start, [])]
    seen = {obs_key(start)}
    nodes = 0
    found = None
    depth_limit = int(limits["max_depth"])
    while frontier and found is None:
        next_frontier = []
        for obs, path in frontier:
            if time.monotonic() > deadline or nodes >= int(limits["max_nodes"]):
                frontier = []
                break
            if len(path) >= depth_limit:
                continue
            try:
                candidates = model.actions(obs)
            except Exception as error:
                sys.stdout.write("\\n" + json.dumps({"error": f"actions(): {type(error).__name__}: {error}"}) + "\\n")
                raise SystemExit(0)
            for action, params in candidates:
                nodes += 1
                if nodes > int(limits["max_nodes"]) or time.monotonic() > deadline:
                    break
                try:
                    predicted = model.next(obs, str(action), params)
                except Exception:
                    continue
                if predicted is None or (isinstance(predicted, dict) and predicted.get("__unknown__")):
                    continue
                step = {"action": str(action), "params": params,
                        "values": {name: read_state(name, predicted) for name in declared}}
                if goal_met(predicted):
                    found = path + [step]
                    break
                marker = obs_key(predicted)
                if marker in seen:
                    continue
                seen.add(marker)
                next_frontier.append((predicted, path + [step]))
            if found is not None:
                break
        else:
            frontier = next_frontier
            continue
        break
    sys.stdout.write("\\n" + json.dumps({"plan": found, "nodes": nodes,
                                          "declared": declared}) + "\\n")
"""


def model_source(paths: RunPaths) -> Path:
    return paths.root / "model.py"


def fit_path(paths: RunPaths) -> Path:
    return paths.state / "model_fit.json"


def plan_path(paths: RunPaths) -> Path:
    return paths.state / "model_plan.json"


def model_hash(paths: RunPaths) -> str | None:
    try:
        return hashlib.sha256(model_source(paths).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def init_model(paths: RunPaths) -> Path:
    target = model_source(paths)
    if target.exists():
        raise AssayError(f"{target} already exists; edit it in place", code="COMMAND_ARGS")
    target.write_text(MODEL_TEMPLATE)
    return target


def model_created_text(target: Path) -> str:
    """The line `assay model init` prints for the template it wrote."""
    return f"CREATED | {target}; declare STATES, define next()"


def _state_specs_for_sandbox(run: Run, declared: list[str]) -> dict[str, Any]:
    paths = run.paths
    known = load_declared(paths)
    specs: dict[str, Any] = {}
    for name in declared:
        if name in ("goal", "level"):
            specs[name] = {"form": "host"}
        elif name in known:
            entry = dict(known[name])
            if entry["form"] == "extractor":
                entry["file"] = str((paths.state / "channels" / f"{entry['hash']}.py").resolve())
            specs[name] = entry
        else:
            raise AssayError(
                f"model declares unregistered state {name!r}",
                code="STATE_UNKNOWN",
                hint=(
                    f"declare it with `assay state declare {name} ...` first (referents are "
                    "registered, never assumed)"
                ),
            )
    return specs


def _invalid_reason(outcome: Mapping[str, Any], timeout: float) -> str:
    """The words for a run the sandbox refused, as they have always read."""
    kind = outcome.get("kind")
    if kind == "timeout":
        return f"model run timed out after {timeout:g}s"
    if kind == "crash":
        return f"model crashed in the sandbox: {str(outcome['tail'])[:300]}"
    if kind == "no_output":
        return "model produced no output"
    if kind == "malformed":
        return f"malformed model output: {str(outcome['output'])[:160]!r}"
    return f"model {outcome['reason']}"


def _run_sandbox(paths: RunPaths, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Run the model runner in the sandbox: `model.py` and the extractor files
    of the declared states travel as companions, and the payload's
    `model_path` and `states[name]["file"]` point at the scratch copies."""
    companions = [payload["model_path"]] + [
        entry["file"]
        for entry in payload["states"].values()
        if entry.get("form") == "extractor"
    ]
    # The budgets as they have always been: CPU at the search budget (two
    # seconds at least), the wall clock five seconds past it.
    outcome = run_program(
        _RUNNER,
        payload,
        timeout=timeout + 5.0,
        companions=companions,
        cpu_seconds=max(2, int(timeout)),
    )
    if outcome["status"] != "ok":
        raise AssayError(_invalid_reason(outcome, timeout), code="MODEL_FAILED")
    result = outcome["result"]
    if not isinstance(result, dict):
        raise AssayError(f"malformed model output: {json.dumps(result)[:160]!r}", code="MODEL_FAILED")
    if result.get("error"):
        raise AssayError(f"model error: {result['error']}", code="MODEL_FAILED")
    return result


def _declared_states(run: Run) -> list[str]:
    """Read STATES from model.py without executing agent code in-process.
    CHANNELS, the name before 1.2.0, is read for one release."""
    paths = run.paths
    source = model_source(paths)
    if not source.exists():
        raise AssayError(
            "no model.py in the run root", code="MODEL_INVALID", hint="`assay model init` creates one"
        )
    payload = {
        "mode": "replay",
        "model_path": str(source.resolve()),
        "states": {},
        "transitions": [],
    }
    result = _run_sandbox(paths, payload, timeout=10.0)
    declared = [str(name) for name in result.get("declared", [])]
    if not declared:
        raise AssayError(
            "model.py declares no STATES",
            code="MODEL_INVALID",
            hint=(
                "name the states the model predicts in STATES; a model without declared "
                "states grades nothing and earns nothing"
            ),
        )
    return declared


def _observation_view(event: Event) -> dict[str, Any]:
    from .verifiers import observation_view

    return observation_view(event)


def _admitted_at_event(run: Run, current_hash: str | None, head: int) -> int:
    """The journal event at which the current model hash was first replayed:
    the earliest `model_replay` activity record carrying the hash and its
    event, or the head of this replay when there is none. A record written
    before the admission rule carries no event and cannot place one."""
    for record in load_jsonl(run.paths.activity):
        if (
            record.get("kind") == "model_replay"
            and record.get("model_hash") == current_hash
            and isinstance(record.get("event"), int)
        ):
            return int(record["event"])
    return head


def replay_model(run: Run) -> dict[str, Any]:
    """Grade the model's declared states over every recorded paid transition.

    The fit covers every transition; promotion counts only the transitions
    recorded after the current model hash was admitted (its first replay)."""
    paths = run.paths
    declared = _declared_states(run)
    specs = _state_specs_for_sandbox(run, declared)
    events = run.events
    transitions = []
    indices = []
    for index in range(1, len(events)):
        event = events[index]
        if not event.counts_action:
            continue
        transitions.append(
            {
                "before": _observation_view(events[index - 1]),
                "action": str(event.action),
                "params": event.data,
            }
        )
        indices.append(index)
    payload = {
        "mode": "replay",
        "model_path": str(model_source(paths).resolve()),
        "states": specs,
        "transitions": transitions,
    }
    result = _run_sandbox(paths, payload, timeout=max(30.0, 0.2 * len(transitions)))
    per_state: dict[str, dict[str, int]] = {
        name: {"held": 0, "missed": 0, "unknown": 0} for name in declared
    }
    graded_indices: list[int] = []
    unknown = errors = 0
    first_mismatch: dict[str, Any] | None = None
    for index, outcome in zip(indices, result["results"]):
        if outcome.get("error"):
            errors += 1
            continue
        if outcome.get("unknown"):
            unknown += 1
            for name in declared:
                per_state[name]["unknown"] += 1
            continue
        predicted = outcome.get("predicted") or {}
        graded_this = False
        for name in declared:
            ok, actual = state_value(run, name, events[index])
            if not ok:
                per_state[name]["unknown"] += 1
                continue
            graded_this = True
            if predicted.get(name) == actual:
                per_state[name]["held"] += 1
            else:
                per_state[name]["missed"] += 1
                if first_mismatch is None:
                    first_mismatch = {
                        "event": int(events[index].id),
                        "channel": name,
                        "predicted": predicted.get(name),
                        "actual": actual,
                    }
        if graded_this:
            graded_indices.append(index)
    held = sum(entry["held"] for entry in per_state.values())
    missed = sum(entry["missed"] for entry in per_state.values())
    graded = held + missed
    fit = (held / graded) if graded else 0.0
    paid_indices = [
        index for index in range(1, len(events)) if events[index].counts_action
    ]
    recent_cut = set(paid_indices[-max(1, len(paid_indices) // 4):] if paid_indices else [])
    recent_graded = sum(1 for index in graded_indices if index in recent_cut)
    # Promotion counts only the transitions recorded after the admission of
    # this model hash (its first replay): the fit over every transition is
    # reported, the rights are earned on the ones the model had not seen.
    current_hash = model_hash(paths)
    head = int(events[-1].id) if events else -1
    admitted_at_event = _admitted_at_event(run, current_hash, head)
    counted_indices = [index for index in graded_indices if index > admitted_at_event]
    counted = len(counted_indices)
    counted_recent = sum(1 for index in counted_indices if index in recent_cut)
    promotion = (
        missed == 0
        and counted >= PROMOTION_MIN_GRADED
        and counted_recent >= PROMOTION_MIN_RECENT
    )
    record = {
        "model_hash": current_hash,
        "computed_at_event": head,
        "admitted_at_event": admitted_at_event,
        "declared": declared,
        "transitions": len(transitions),
        "graded": graded,
        "held": held,
        "missed": missed,
        "unknown": unknown,
        "errors": errors,
        "recent_graded": recent_graded,
        "counted": counted,
        "counted_recent": counted_recent,
        "per_channel": per_state,  # the fit record keeps its earlier keys
        "fit": round(fit, 4),
        "promotion": promotion,
        "first_mismatch": first_mismatch,
        "computed_at": time.time(),
    }
    atomic_json(fit_path(paths), record)
    append_jsonl(
        paths.activity,
        {
            "kind": "model_replay",
            "model_hash": current_hash,
            "event": head,
            "admitted_at_event": admitted_at_event,
            "graded": graded,
            "missed": missed,
            "counted": counted,
            "promotion": promotion,
        },
    )
    return record


def batching_rights(run: Run) -> tuple[bool, str]:
    """(rights, reason). Rights = current model passed replay-fit on THIS
    journal (promotion counted on the transitions recorded after the model's
    admission), no ungated event exists, and no consequence revoked batching."""
    from .aggregates import batching_revoked
    from .integrity import first_ungated

    paths = run.paths
    record = read_json(fit_path(paths), None)
    if not isinstance(record, dict):
        return False, "no replay-fit record (`assay model replay`)"
    if not record.get("promotion"):
        if "admitted_at_event" in record:
            return False, (
                f"replay-fit not promoted: counted {record.get('counted')} since "
                f"the admission at e{record.get('admitted_at_event')}, missed "
                f"{record.get('missed')}, recent {record.get('counted_recent')} "
                f"(needs missed=0, counted>={PROMOTION_MIN_GRADED}, recent>={PROMOTION_MIN_RECENT})"
            )
        # A fit record from before the admission rule reads as it always did.
        return False, (
            f"replay-fit not promoted: graded {record.get('graded')}, missed "
            f"{record.get('missed')}, recent {record.get('recent_graded')} "
            f"(needs missed=0, graded>={PROMOTION_MIN_GRADED}, recent>={PROMOTION_MIN_RECENT})"
        )
    if record.get("model_hash") != model_hash(paths):
        return False, "model.py changed since its replay-fit; rerun `assay model replay`"
    events = run.events
    if record.get("computed_at_event") != (int(events[-1].id) if events else -1):
        return False, "the journal moved since the replay-fit; rerun `assay model replay`"
    if first_ungated(events) is not None:
        return False, "an ungated event exists; trust earned after it is demoted"
    if batching_revoked(run):
        return False, "an aggregate consequence revoked batching rights for this run"
    return True, "replay-fit promotion holds"


def parse_goal_expression(text: str) -> dict[str, Any]:
    import re as _re

    found = _re.fullmatch(
        r"\s*ch\s+([A-Za-z][A-Za-z0-9_]{0,31})\s*=\s*(\S+)\s*", text or ""
    )
    if not found:
        raise AssayError('solve goal is `--to "ch NAME = VALUE"`', code="COMMAND_ARGS")
    from .predictions import _parse_value

    return {"channel": found.group(1).lower(), "value": _parse_value(found.group(2))}


def solve_model(
    run: Run,
    goal_text: str,
    *,
    seconds: float = SOLVE_DEFAULT_SECONDS,
    max_nodes: int = SOLVE_DEFAULT_NODES,
    max_depth: int = SOLVE_MAX_DEPTH,
) -> dict[str, Any]:
    paths = run.paths
    goal = parse_goal_expression(goal_text)
    declared = _declared_states(run)
    if goal["channel"] not in declared:
        raise AssayError(
            f"solve goal state {goal['channel']!r} is not in the model's declared "
            f"states {declared}",
            code="MODEL_INVALID",
            hint="add it to model.py's STATES, or solve toward one of the declared states",
        )
    specs = _state_specs_for_sandbox(run, declared)
    events = run.events
    if not events:
        raise AssayError("timeline is empty", code="TIMELINE_EMPTY")
    payload = {
        "mode": "solve",
        "model_path": str(model_source(paths).resolve()),
        "states": specs,
        "start_obs": _observation_view(events[-1]),
        "goal": goal,
        "limits": {"seconds": seconds, "max_nodes": max_nodes, "max_depth": max_depth},
    }
    result = _run_sandbox(paths, payload, timeout=seconds + 15.0)
    plan_steps = result.get("plan")
    if plan_steps is not None and not isinstance(plan_steps, list):
        raise AssayError("malformed model output: the plan is not a list", code="MODEL_FAILED")
    record: dict[str, Any] = {
        "kind": "model-plan",
        "goal": goal,
        "nodes": int(result.get("nodes", 0)),
        "source": {
            "event": int(events[-1].id),
            "model_hash": model_hash(paths),
        },
        # Each action as the object the wire carries (`{action, params}`),
        # validated against the registry when the plan is committed; a
        # model's `actions()` gives the params, so they can be any JSON.
        "actions": [
            {"action": str(step["action"]), "params": step.get("params") or None}
            for step in plan_steps or ()
        ],
        "predictions": [step["values"] for step in plan_steps or ()],
        "computed_at": time.time(),
    }
    if plan_steps:
        atomic_json(plan_path(paths), record)
    append_jsonl(
        paths.activity,
        {
            "kind": "model_solve",
            "found": bool(plan_steps),
            "nodes": record["nodes"],
            "steps": len(record["actions"]),
        },
    )
    return record


def solve_lines(result: Mapping[str, Any]) -> list[str]:
    """The lines `assay model solve` prints: the plan found, its actions and
    where it was written, or the search that found none."""
    if not result["actions"]:
        return [
            f"SOLVE | no plan inside the model | nodes {result['nodes']}; "
            "actions() or next() are too narrow, or the goal needs "
            "something unmodeled"
        ]
    return [
        f"SOLVE | plan found | {len(result['actions'])} steps | nodes {result['nodes']}",
        "ACTIONS | "
        + " -> ".join(render_action(item["action"], item["params"]) for item in result["actions"]),
        "PLAN | .assay/model_plan.json; execute with "
        "`assay commit @.assay/model_plan.json` (needs replay-fit "
        "promotion on the current journal)",
    ]


def fit_lines(record: Mapping[str, Any]) -> list[str]:
    lines = [
        f"MODEL | replay-fit {record['fit']:.2%} | held {record['held']} missed "
        f"{record['missed']} unknown {record['unknown']} over {record['transitions']} "
        f"transitions | recent-quarter graded {record['recent_graded']} | "
        f"promotion {'EARNED: model plans lift the batch cap' if record['promotion'] else 'not earned'}"
    ]
    mismatch = record.get("first_mismatch")
    if mismatch:
        lines.append(
            f"MODEL | first mismatch e{mismatch['event']} ch {mismatch['channel']}: "
            f"predicted {json.dumps(mismatch['predicted'])}, actual {json.dumps(mismatch['actual'])}"
        )
    still: list[str] = []
    if record["missed"]:
        still.append("no miss")
    if record["counted"] < PROMOTION_MIN_GRADED:
        still.append(f"{PROMOTION_MIN_GRADED - record['counted']} more counted")
    if record["counted_recent"] < PROMOTION_MIN_RECENT:
        still.append(
            f"{PROMOTION_MIN_RECENT - record['counted_recent']} more in the most recent quarter"
        )
    lines.append(
        f"MODEL | counted {record['counted']} transition(s) recorded after this model's "
        f"first replay at e{record['admitted_at_event']}, {record['counted_recent']} in "
        "the most recent quarter of the journal | promotion "
        + ("earned on the counted transitions" if record["promotion"] else f"still needs {', '.join(still)}")
    )
    return lines
