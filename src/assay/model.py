"""The generalized world-model tier for registry runs.

The grid `rules.py` tier is this law's grid instance; registry runs get the
general form. The agent writes `model.py` in the run root:

    CHANNELS = ["counter", "lamp"]        # declared channel set (registered names)

    def next(obs, action, params):        # the model: predict the next observation
        ...                               # return the predicted obs dict, or None
                                          # (= Unknown) when out of scope

    def actions(obs):                     # optional, required for solve:
        return [("INC", None), ...]       #   candidate (action, params) pairs

    def key(obs):                         # optional: dedup key for search
        return json.dumps(obs["data"], sort_keys=True)

Trust is exactly replay-fit — no other trust states exist:

- `assay model replay` re-predicts every recorded paid transition in the verifier
  sandbox and grades ONLY the declared channels: MISMATCH = declared channel
  wrong; INCOMPLETE = Unknown (excluded from fit, reported); undeclared
  channels are out of scope. Fit is written to `.assay/model_fit.json`.
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
  a plan; every plan step carries machine-generated channel predictions,
  marked `machine` — they never enter the agent's claim meters. Plans carry
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
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .core import (
    AssayError,
    RunPaths,
    append_jsonl,
    atomic_json,
    load_events,
    load_jsonl,
    read_json,
)
from .channels import channel_value, load_declared

PROMOTION_MIN_GRADED = 20
PROMOTION_MIN_RECENT = 5
SOLVE_DEFAULT_SECONDS = 15.0
SOLVE_DEFAULT_NODES = 100_000
SOLVE_MAX_DEPTH = 40

MODEL_TEMPLATE = '''"""Your world model. Declare channels; predict the next observation.

The kernel grades ONLY the channels you declare (registered channel names).
Return the predicted observation dict from next(), or None when this
transition is outside your model (Unknown — honest, excluded from fit).
"""

CHANNELS = []  # e.g. ["counter", "level"] — registered channel names you model


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
for name, entry in payload["channels"].items():
    if entry["form"] == "extractor":
        espec = importlib.util.spec_from_file_location("x_" + name, entry["file"])
        emod = importlib.util.module_from_spec(espec)
        espec.loader.exec_module(emod)
        extractors[name] = emod.extract

def read_channel(name, obs):
    entry = payload["channels"][name]
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

declared = [str(name) for name in getattr(model, "CHANNELS", [])]

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
                values[name] = read_channel(name, predicted)
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
        return read_channel(goal["channel"], obs) == goal["value"]
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
                        "values": {name: read_channel(name, predicted) for name in declared}}
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
        raise AssayError(f"{target} already exists; edit it in place")
    target.write_text(MODEL_TEMPLATE)
    return target


def _channel_specs_for_sandbox(paths: RunPaths, declared: list[str]) -> dict[str, Any]:
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
                f"model declares unregistered channel {name!r}; declare it with "
                "`assay channel declare` first (referents are registered, never assumed)"
            )
    return specs


def _run_sandbox(paths: RunPaths, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    cpu_seconds = max(2, int(timeout))

    def _limits() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    with tempfile.TemporaryDirectory(prefix="assay-model-") as scratch:
        try:
            completed = subprocess.run(  # noqa: S603 - deliberate sandboxed run
                [sys.executable, "-I", "-c", _RUNNER],
                input=json.dumps(payload).encode(),
                capture_output=True,
                cwd=scratch,
                env={},
                timeout=timeout + 5.0,
                preexec_fn=_limits,
            )
        except subprocess.TimeoutExpired:
            raise AssayError(f"model run timed out after {timeout:g}s") from None
    if completed.returncode != 0:
        stderr = completed.stderr.decode(errors="replace").strip().splitlines()
        tail = stderr[-1][:300] if stderr else "no stderr"
        raise AssayError(f"model crashed in the sandbox: {tail}")
    lines = [
        line for line in completed.stdout.decode(errors="replace").splitlines() if line.strip()
    ]
    if not lines:
        raise AssayError("model produced no output")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError:
        raise AssayError(f"malformed model output: {lines[-1][:160]!r}") from None
    if result.get("error"):
        raise AssayError(f"model error: {result['error']}")
    return result


def _declared_channels(paths: RunPaths) -> list[str]:
    """Read CHANNELS from model.py without executing agent code in-process."""
    source = model_source(paths)
    if not source.exists():
        raise AssayError("no model.py in the run root; `assay model init` creates one")
    payload = {
        "mode": "replay",
        "model_path": str(source.resolve()),
        "channels": {},
        "transitions": [],
    }
    result = _run_sandbox(paths, payload, timeout=10.0)
    declared = [str(name) for name in result.get("declared", [])]
    if not declared:
        raise AssayError(
            "model.py declares no CHANNELS; a model without declared channels "
            "grades nothing and earns nothing"
        )
    return declared


def _observation_view(event: Mapping[str, Any]) -> dict[str, Any]:
    from .verifiers import observation_view

    return observation_view(event)


def _admitted_at_event(paths: RunPaths, current_hash: str | None, head: int) -> int:
    """The journal event at which the current model hash was first replayed:
    the earliest `model_replay` activity record carrying the hash and its
    event, or the head of this replay when there is none. A record written
    before the admission rule carries no event and cannot place one."""
    for record in load_jsonl(paths.activity):
        if (
            record.get("kind") == "model_replay"
            and record.get("model_hash") == current_hash
            and isinstance(record.get("event"), int)
        ):
            return int(record["event"])
    return head


def replay_model(paths: RunPaths) -> dict[str, Any]:
    """Grade the model's declared channels over every recorded paid transition.

    The fit covers every transition; promotion counts only the transitions
    recorded after the current model hash was admitted (its first replay)."""
    declared = _declared_channels(paths)
    specs = _channel_specs_for_sandbox(paths, declared)
    events = load_events(paths)
    transitions = []
    indices = []
    for index in range(1, len(events)):
        event = events[index]
        if not event.get("counts_action"):
            continue
        transitions.append(
            {
                "before": _observation_view(events[index - 1]),
                "action": str(event["action"]),
                "params": event.get("data"),
            }
        )
        indices.append(index)
    payload = {
        "mode": "replay",
        "model_path": str(model_source(paths).resolve()),
        "channels": specs,
        "transitions": transitions,
    }
    result = _run_sandbox(paths, payload, timeout=max(30.0, 0.2 * len(transitions)))
    per_channel: dict[str, dict[str, int]] = {
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
                per_channel[name]["unknown"] += 1
            continue
        predicted = outcome.get("predicted") or {}
        graded_this = False
        for name in declared:
            ok, actual = channel_value(paths, name, events[index])
            if not ok:
                per_channel[name]["unknown"] += 1
                continue
            graded_this = True
            if predicted.get(name) == actual:
                per_channel[name]["held"] += 1
            else:
                per_channel[name]["missed"] += 1
                if first_mismatch is None:
                    first_mismatch = {
                        "event": int(events[index]["id"]),
                        "channel": name,
                        "predicted": predicted.get(name),
                        "actual": actual,
                    }
        if graded_this:
            graded_indices.append(index)
    held = sum(entry["held"] for entry in per_channel.values())
    missed = sum(entry["missed"] for entry in per_channel.values())
    graded = held + missed
    fit = (held / graded) if graded else 0.0
    paid_indices = [
        index for index in range(1, len(events)) if events[index].get("counts_action")
    ]
    recent_cut = set(paid_indices[-max(1, len(paid_indices) // 4):] if paid_indices else [])
    recent_graded = sum(1 for index in graded_indices if index in recent_cut)
    # Promotion counts only the transitions recorded after the admission of
    # this model hash (its first replay): the fit over every transition is
    # reported, the rights are earned on the ones the model had not seen.
    current_hash = model_hash(paths)
    head = int(events[-1]["id"]) if events else -1
    admitted_at_event = _admitted_at_event(paths, current_hash, head)
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
        "per_channel": per_channel,
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


def batching_rights(paths: RunPaths) -> tuple[bool, str]:
    """(rights, reason). Rights = current model passed replay-fit on THIS
    journal (promotion counted on the transitions recorded after the model's
    admission), no ungated event exists, and no consequence revoked batching."""
    from .aggregates import batching_revoked
    from .integrity import first_ungated

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
    events = load_events(paths)
    if record.get("computed_at_event") != (int(events[-1]["id"]) if events else -1):
        return False, "the journal moved since the replay-fit; rerun `assay model replay`"
    if first_ungated(events) is not None:
        return False, "an ungated event exists; trust earned after it is demoted"
    if batching_revoked(paths):
        return False, "an aggregate consequence revoked batching rights for this run"
    return True, "replay-fit promotion holds"


def parse_goal_expression(text: str) -> dict[str, Any]:
    import re as _re

    found = _re.fullmatch(
        r"\s*ch\s+([A-Za-z][A-Za-z0-9_]{0,31})\s*=\s*(\S+)\s*", text or ""
    )
    if not found:
        raise AssayError('solve goal is `--to "ch NAME = VALUE"`')
    from .predictions import _parse_value

    return {"channel": found.group(1).lower(), "value": _parse_value(found.group(2))}


def solve_model(
    paths: RunPaths,
    goal_text: str,
    *,
    seconds: float = SOLVE_DEFAULT_SECONDS,
    max_nodes: int = SOLVE_DEFAULT_NODES,
    max_depth: int = SOLVE_MAX_DEPTH,
) -> dict[str, Any]:
    goal = parse_goal_expression(goal_text)
    declared = _declared_channels(paths)
    if goal["channel"] not in declared:
        raise AssayError(
            f"solve goal channel {goal['channel']!r} is not in the model's declared "
            f"channels {declared}"
        )
    specs = _channel_specs_for_sandbox(paths, declared)
    events = load_events(paths)
    if not events:
        raise AssayError("timeline is empty")
    payload = {
        "mode": "solve",
        "model_path": str(model_source(paths).resolve()),
        "channels": specs,
        "start_obs": _observation_view(events[-1]),
        "goal": goal,
        "limits": {"seconds": seconds, "max_nodes": max_nodes, "max_depth": max_depth},
    }
    result = _run_sandbox(paths, payload, timeout=seconds + 15.0)
    plan_steps = result.get("plan")
    record = {
        "kind": "model-plan",
        "goal": goal,
        "nodes": int(result.get("nodes", 0)),
        "source": {
            "event": int(events[-1]["id"]),
            "model_hash": model_hash(paths),
        },
        "actions": [
            step["action"]
            + (
                " " + " ".join(f"{k}={v}" for k, v in sorted(step["params"].items()))
                if step.get("params")
                else ""
            )
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
