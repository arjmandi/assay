"""Channels: registered, named, code-extracted readings of the observation.

A channel is a pair (observer, extractor). Three sources:

- HOST channels, always present: `goal` (boolean, the environment's win
  state; the one channel an agent-invented proxy can never replace), `level`
  (the host progress count, `levels_completed`, the milestone channel), and
  `budget_remaining` (paid actions left under the registered cap after the
  event; ungradable when no cap is registered).
- AGENT-DECLARED channels (`assay channel declare NAME --path a.b.c` or
  `--file extractor.py`): journaled at declaration. The dotted-path form is
  graded in-kernel (pure data lookup over the dict observation). The
  extractor-file form is agent-authored code and runs ONLY in the sandbox
  (`sandbox.run_program`: a scratch copy, `python -I`, empty env, the process
  limits, no run directory, no network): `def extract(obs) -> value`.
- Adapter-declared (pack) channels do not exist in 1.2.0: three worlds run on
  the host channels and the agent-declared ones alone.

Readings. Status shows every channel's current value on a registry run
without ever spawning an extractor: host and path channels are read from the
latest event in-process, extractor channels show their last graded value and
the event it was graded on, cached by the daemon in `.assay/channel_readings.json`
at grade time. `assay channel list --read` computes extractor values fresh.

Claims naming an unregistered channel are refused before any spend and
counted on the mis-reference meter, the surviving referent-grounding
instrument.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .core import AssayError, RunPaths, append_jsonl, atomic_json, load_jsonl, read_json
from .sandbox import run_program

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
HOST_CHANNELS = ("goal", "level", "budget_remaining")
MILESTONE_CHANNELS = ("goal", "level")
EXTRACT_TIMEOUT_SECONDS = 5.0
MAX_DECLARED_CHANNELS = 16

_RUNNER = """\
import importlib.util, json, sys
payload = json.loads(sys.stdin.read())
spec = importlib.util.spec_from_file_location("extractor", payload["extractor_path"])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
value = module.extract(payload["obs"])
sys.stdout.write("\\n" + json.dumps({"value": value}) + "\\n")
"""


def channels_path(paths: RunPaths) -> Path:
    return paths.state / "channels.json"


def readings_path(paths: RunPaths) -> Path:
    """Last graded extractor readings, written by the daemon only (so it never
    races the CLI's writes to channels.json)."""
    return paths.state / "channel_readings.json"


def load_readings(paths: RunPaths) -> dict[str, dict[str, Any]]:
    value = read_json(readings_path(paths), {})
    return value if isinstance(value, dict) else {}


def _remember_reading(paths: RunPaths, name: str, event_id: int, value: Any) -> None:
    readings = load_readings(paths)
    readings[name] = {"event": int(event_id), "value": value}
    atomic_json(readings_path(paths), readings)


def extractor_dir(paths: RunPaths) -> Path:
    return paths.state / "channels"


def load_declared(paths: RunPaths) -> dict[str, dict[str, Any]]:
    value = read_json(channels_path(paths), {})
    return value if isinstance(value, dict) else {}


def declare_channel(
    paths: RunPaths,
    name: str,
    *,
    path: str | None = None,
    file: str | None = None,
) -> dict[str, Any]:
    """Declare an agent channel: exactly one of a dotted path or an extractor file."""
    if not _NAME.fullmatch(name or ""):
        raise AssayError(
            f"channel name {name!r} must match {_NAME.pattern} (lowercase)"
        )
    if name in HOST_CHANNELS:
        raise AssayError(f"{name!r} is a host channel and cannot be redeclared")
    if bool(path) == bool(file):
        raise AssayError("declare a channel with exactly one of --path or --file")
    declared = load_declared(paths)
    if path is not None:
        keys = [key for key in path.split(".") if key]
        if not keys:
            raise AssayError("--path needs dotted keys like counters.red")
        spec: dict[str, Any] = {"form": "path", "path": ".".join(keys)}
    else:
        candidate = Path(str(file))
        if candidate.is_absolute():
            raise AssayError(
                f"--file takes a path relative to the run directory, got {file!r}"
            )
        source = paths.root / candidate
        try:
            source.resolve().relative_to(paths.root.resolve())
        except ValueError as error:
            raise AssayError(
                "extractor file must stay inside the run directory"
            ) from error
        try:
            body = source.read_bytes()
        except (FileNotFoundError, IsADirectoryError) as error:
            raise AssayError(f"extractor file not found: {file}") from error
        digest = hashlib.sha256(body).hexdigest()
        extractor_dir(paths).mkdir(parents=True, exist_ok=True)
        stored = extractor_dir(paths) / f"{digest}.py"
        if not stored.exists():
            stored.write_bytes(body)
        spec = {"form": "extractor", "hash": digest, "source": str(file)}
    existing = declared.get(name)
    if existing is not None and existing != spec:
        raise AssayError(
            f"channel {name!r} is already declared with a different extractor; "
            "declare a new name instead of silently redefining a referent"
        )
    if existing is None and len(declared) >= MAX_DECLARED_CHANNELS:
        raise AssayError(
            f"at most {MAX_DECLARED_CHANNELS} declared channels per run"
        )
    declared[name] = spec
    atomic_json(channels_path(paths), declared)
    append_jsonl(
        paths.activity,
        {"kind": "channel_declared", "channel": name, **spec},
    )
    return spec


def known_channels(paths: RunPaths) -> list[str]:
    return [*HOST_CHANNELS, *sorted(load_declared(paths))]


def check_channel_references(
    paths: RunPaths, claims: list[dict[str, Any]]
) -> None:
    """Refuse (free) any claim naming an unregistered channel; count it."""
    known = set(known_channels(paths))
    for claim in claims:
        name = claim.get("channel")
        if name is not None and name not in known:
            append_jsonl(
                paths.activity,
                {"kind": "mis_reference", "channel": name, "claim": claim.get("text")},
            )
            raise AssayError(
                f"claim names unregistered channel {name!r}; registered channels: "
                f"{known_channels(paths)}; declare one with `assay channel declare`"
            )


def _walk(observation: Any, dotted: str) -> tuple[bool, Any]:
    node = observation
    for key in dotted.split("."):
        if isinstance(node, Mapping) and key in node:
            node = node[key]
        elif isinstance(node, list):
            try:
                node = node[int(key)]
            except (ValueError, IndexError):
                return False, f"key {key!r} not in observation path {dotted!r}"
        else:
            return False, f"key {key!r} not in observation path {dotted!r}"
    return True, node


def _invalid_reason(outcome: Mapping[str, Any]) -> str:
    """The words for a run the sandbox refused, as they have always read."""
    kind = outcome.get("kind")
    if kind == "timeout":
        return f"extractor timed out after {EXTRACT_TIMEOUT_SECONDS:g}s"
    if kind == "crash":
        return f"extractor crashed: {str(outcome['tail'])[:200]}"
    if kind == "no_output":
        return "extractor produced no output"
    if kind == "malformed":
        return f"malformed extractor output: {str(outcome['output'])[:120]!r}"
    return f"extractor {outcome['reason']}"


def _run_extractor(
    paths: RunPaths, digest: str, obs: Mapping[str, Any]
) -> tuple[bool, Any]:
    stored = extractor_dir(paths) / f"{digest}.py"
    if not stored.exists():
        return False, f"stored extractor {digest[:12]} missing"
    payload = {"obs": obs, "extractor_path": str(stored.resolve())}
    outcome = run_program(
        _RUNNER, payload, timeout=EXTRACT_TIMEOUT_SECONDS, companions=(stored,)
    )
    if outcome["status"] != "ok":
        return False, _invalid_reason(outcome)
    result = outcome["result"]
    if not isinstance(result, dict) or "value" not in result:
        return False, f"malformed extractor output: {json.dumps(result)[:120]!r}"
    return True, result["value"]


def _journal_position(paths: RunPaths, event: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    """The journal and the event's index in it, or None for a pending event
    (graded before it is appended; its id will be the journal's length)."""
    events = load_jsonl(paths.events)
    event_id = int(event.get("id", -1))
    if 0 <= event_id < len(events) and int(events[event_id].get("id", -1)) == event_id:
        return events, event_id
    return events, None


def _budget_remaining(paths: RunPaths, event: Mapping[str, Any]) -> tuple[bool, Any]:
    """Paid actions left under the registered cap once this event is in the
    journal: the cap minus every paid event up to and including it. A pending
    event (grade time) counts itself."""
    registry = read_json(paths.registry, None)
    cap = ((registry or {}).get("budget") or {}).get("actions") if isinstance(registry, dict) else None
    if cap is None:
        return False, "budget_remaining needs a registered action cap (budget.actions)"
    events, index = _journal_position(paths, event)
    if index is None:
        paid = sum(1 for record in events if record.get("counts_action"))
        paid += 1 if event.get("counts_action") else 0
    else:
        paid = sum(1 for record in events[: index + 1] if record.get("counts_action"))
    return True, max(0, int(cap) - paid)


def channel_value(
    paths: RunPaths, name: str, event: Mapping[str, Any], *, remember: bool = False
) -> tuple[bool, Any]:
    """(True, value) or (False, reason). Host channels come from event fields
    and the registry cap; path channels walk the dict observation; extractor
    channels run sandboxed, and with remember=True (grade time, in the daemon)
    the reading is cached for status."""
    if name == "goal":
        return True, str(event["state"]) == "WIN"
    if name == "level":
        return True, int(event["levels_completed"])
    if name == "budget_remaining":
        return _budget_remaining(paths, event)
    spec = load_declared(paths).get(name)
    if spec is None:
        return False, f"channel {name!r} is not registered"
    if spec["form"] == "path":
        observation = event.get("observation")
        if observation is None:
            return False, "dotted-path channels need a dict observation (this event has frames)"
        return _walk(observation, spec["path"])
    from .verifiers import observation_view

    ok, value = _run_extractor(paths, spec["hash"], observation_view(event))
    if ok and remember:
        events, index = _journal_position(paths, event)
        # A pending event takes the next id; the daemon is the only writer.
        _remember_reading(paths, name, index if index is not None else len(events), value)
    return ok, value


def _render(value: Any) -> str:
    try:
        text = json.dumps(value)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= 40 else text[:39] + "…"


def channel_lines(
    paths: RunPaths, event: Mapping[str, Any], *, fresh: bool = False
) -> list[str]:
    """The CHANNELS block: host values, path values read from the event, and
    extractor values from the cache (or computed fresh when asked). Never
    spawns a subprocess unless fresh is true."""
    declared = load_declared(paths)
    lines = ["CHANNELS | registered: " + " · ".join(known_channels(paths))]
    host = []
    for name in HOST_CHANNELS:
        ok, value = channel_value(paths, name, event)
        host.append(f"{name}={_render(value) if ok else 'n/a'}")
    lines.append("CHANNELS | host: " + " · ".join(host))
    if not declared:
        return lines
    readings = load_readings(paths)
    rendered = []
    for name, spec in sorted(declared.items()):
        if spec["form"] == "path" or fresh:
            ok, value = channel_value(paths, name, event)
            rendered.append(
                f"{name}={_render(value) if ok else 'unreadable'} ({spec['form']})"
            )
            continue
        last = readings.get(name)
        if isinstance(last, dict) and last.get("event") is not None:
            rendered.append(
                f"{name}={_render(last.get('value'))} @e{last['event']} (extractor, last graded)"
            )
        else:
            rendered.append(f"{name}=not yet graded (extractor)")
    lines.append("CHANNELS | declared: " + " · ".join(rendered))
    return lines


def channel_change_lines(
    paths: RunPaths, before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[str]:
    """Receipt lines for declared path channels that changed across a paid
    span: `CHANNELS | name: before -> after`. Path channels only (no
    subprocess on the receipt path)."""
    lines: list[str] = []
    for name, spec in sorted(load_declared(paths).items()):
        if spec["form"] != "path":
            continue
        ok_before, was = channel_value(paths, name, before)
        ok_after, now = channel_value(paths, name, after)
        if ok_before and ok_after and was != now:
            lines.append(f"CHANNELS | {name}: {_render(was)} -> {_render(now)}")
    return lines


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def grade_channel_claim(
    paths: RunPaths,
    claim: Mapping[str, Any],
    prior_event: Mapping[str, Any],
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """Grade one channel claim against before/after channel readings."""
    record = dict(claim)
    name = str(claim["channel"])
    ok_after, after = channel_value(paths, name, event, remember=True)
    if not ok_after:
        return {**record, "ok": False, "ungradable": True, "actual": f"UNGRADABLE: {after}"}
    kind = claim["kind"]
    if kind == "channel_eq":
        expected = claim["value"]
        tol = claim.get("tol")
        actual_text = f"ch {name} = {json.dumps(after)}"
        if tol is not None:
            after_number, expected_number = _numeric(after), _numeric(expected)
            if after_number is None or expected_number is None:
                return {
                    **record,
                    "ok": False,
                    "ungradable": True,
                    "actual": f"UNGRADABLE: tolerance needs numeric values, got {json.dumps(after)}",
                }
            ok = abs(after_number - expected_number) <= float(tol)
        elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
            after_number = _numeric(after)
            ok = after_number is not None and after_number == float(expected)
        else:
            ok = after == expected
        return {**record, "ok": bool(ok), "actual": actual_text}
    ok_before, before = channel_value(paths, name, prior_event)
    if not ok_before:
        return {**record, "ok": False, "ungradable": True, "actual": f"UNGRADABLE: {before}"}
    before_number, after_number = _numeric(before), _numeric(after)
    if before_number is None or after_number is None:
        return {
            **record,
            "ok": False,
            "ungradable": True,
            "actual": (
                f"UNGRADABLE: {kind.replace('channel_', '')} needs numeric readings, "
                f"got {json.dumps(before)} -> {json.dumps(after)}"
            ),
        }
    if kind == "channel_delta":
        delta = after_number - before_number
        op = claim["op"]
        target = float(claim["value"]) if claim.get("value") is not None else None
        if op == "=":
            ok = delta == target
        elif op == ">=":
            ok = delta >= target
        elif op == "<=":
            ok = delta <= target
        elif op == "sign":
            sign = claim["sign"]
            ok = delta > 0 if sign == "+" else delta < 0
        else:  # pragma: no cover - parser guarantees op
            raise AssayError(f"unknown delta op {op!r}")
        return {
            **record,
            "ok": bool(ok),
            "actual": f"ch {name} moved {delta:+g} ({before_number:g} -> {after_number:g})",
        }
    if kind == "channel_cross":
        threshold = float(claim["value"])
        direction = claim.get("direction")
        rose = before_number < threshold <= after_number
        fell = before_number > threshold >= after_number
        if direction == "below":
            ok = rose
        elif direction == "above":
            ok = fell
        else:
            ok = rose or fell
        return {
            **record,
            "ok": bool(ok),
            "actual": f"ch {name} went {before_number:g} -> {after_number:g} (threshold {threshold:g})",
        }
    raise AssayError(f"unknown channel claim kind {kind!r}")  # pragma: no cover
