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

Every function here takes the run and reads the journal it holds; the
declarations (`channels.json`, written by the CLI) and the readings cache
(written by the daemon) are small files read on demand from `run.paths`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core import AssayError, RunPaths, append_jsonl, atomic_json, read_json
from .sandbox import run_program
from .records import Claim, Event, Grade

if TYPE_CHECKING:
    from .run import Run

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
    run: Run,
    name: str,
    *,
    path: str | None = None,
    file: str | None = None,
) -> dict[str, Any]:
    """Declare an agent channel: exactly one of a dotted path or an extractor file."""
    paths = run.paths
    if not _NAME.fullmatch(name or ""):
        raise AssayError(
            f"channel name {name!r} must match {_NAME.pattern} (lowercase)",
            code="CHANNEL_DECLARE",
        )
    if name in HOST_CHANNELS:
        raise AssayError(f"{name!r} is a host channel and cannot be redeclared", code="CHANNEL_DECLARE")
    if bool(path) == bool(file):
        raise AssayError("declare a channel with exactly one of --path or --file", code="CHANNEL_DECLARE")
    declared = load_declared(paths)
    if path is not None:
        keys = [key for key in path.split(".") if key]
        if not keys:
            raise AssayError("--path needs dotted keys like counters.red", code="CHANNEL_DECLARE")
        spec: dict[str, Any] = {"form": "path", "path": ".".join(keys)}
    else:
        candidate = Path(str(file))
        if candidate.is_absolute():
            raise AssayError(
                f"--file takes a path relative to the run directory, got {file!r}",
                code="PATH_INVALID",
            )
        source = paths.root / candidate
        try:
            source.resolve().relative_to(paths.root.resolve())
        except ValueError as error:
            raise AssayError(
                "extractor file must stay inside the run directory",
                code="PATH_INVALID",
            ) from error
        try:
            body = source.read_bytes()
        except (FileNotFoundError, IsADirectoryError) as error:
            raise AssayError(f"extractor file not found: {file}", code="FILE_NOT_FOUND") from error
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
            "declare a new name instead of silently redefining a referent",
            code="CHANNEL_REDEFINED",
        )
    if existing is None and len(declared) >= MAX_DECLARED_CHANNELS:
        raise AssayError(
            f"at most {MAX_DECLARED_CHANNELS} declared channels per run",
            code="CHANNEL_CAP",
        )
    declared[name] = spec
    atomic_json(channels_path(paths), declared)
    append_jsonl(
        paths.activity,
        {"kind": "channel_declared", "channel": name, **spec},
    )
    return spec


def known_channels(run: Run) -> list[str]:
    return [*HOST_CHANNELS, *sorted(load_declared(run.paths))]


def check_channel_references(run: Run, claims: Sequence[Claim]) -> None:
    """Refuse (free) any claim naming an unregistered channel; count it."""
    known = set(known_channels(run))
    for claim in claims:
        name = claim.channel
        if name is not None and name not in known:
            append_jsonl(
                run.paths.activity,
                {"kind": "mis_reference", "channel": name, "claim": claim.text},
            )
            raise AssayError(
                f"claim names unregistered channel {name!r}",
                code="CHANNEL_UNKNOWN",
                hint=(
                    f"registered channels: {known_channels(run)}; declare one with "
                    f"`assay channel declare {name} --path <dotted.path>` (or --file <extractor.py>)"
                ),
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


def _journal_position(run: Run, event: Event) -> int | None:
    """The event's index in the held journal, or None for a pending event
    (graded before it is appended; its id will be the journal's length)."""
    events = run.events
    if 0 <= event.id < len(events) and events[event.id].id == event.id:
        return event.id
    return None


def _budget_remaining(run: Run, event: Event) -> tuple[bool, Any]:
    """Paid actions left under the registered cap once this event is in the
    journal: the cap minus every paid event up to and including it. A pending
    event (grade time) counts itself."""
    registry = run.registry
    cap = ((registry or {}).get("budget") or {}).get("actions") if registry else None
    if cap is None:
        return False, "budget_remaining needs a registered action cap (budget.actions)"
    events = run.events
    index = _journal_position(run, event)
    if index is None:
        paid = sum(1 for record in events if record.counts_action)
        paid += 1 if event.counts_action else 0
    else:
        paid = sum(1 for record in events[: index + 1] if record.counts_action)
    return True, max(0, int(cap) - paid)


def channel_value(
    run: Run, name: str, event: Event, *, remember: bool = False
) -> tuple[bool, Any]:
    """(True, value) or (False, reason). Host channels come from event fields
    and the registry cap; path channels walk the dict observation; extractor
    channels run sandboxed, and with remember=True (grade time, in the daemon)
    the reading is cached for status."""
    if name == "goal":
        return True, str(event.state) == "WIN"
    if name == "level":
        return True, int(event.levels_completed)
    if name == "budget_remaining":
        return _budget_remaining(run, event)
    spec = load_declared(run.paths).get(name)
    if spec is None:
        return False, f"channel {name!r} is not registered"
    if spec["form"] == "path":
        observation = event.observation
        if observation is None:
            return False, "dotted-path channels need a dict observation (this event has frames)"
        return _walk(observation, spec["path"])
    from .verifiers import observation_view

    ok, value = _run_extractor(run.paths, spec["hash"], observation_view(event))
    if ok and remember:
        index = _journal_position(run, event)
        # A pending event takes the next id; the daemon is the only writer.
        _remember_reading(
            run.paths, name, index if index is not None else len(run.events), value
        )
    return ok, value


def _render(value: Any) -> str:
    try:
        text = json.dumps(value)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= 40 else text[:39] + "…"


@dataclasses.dataclass(frozen=True, slots=True)
class HostReading:
    """One host channel's reading at the event: the value, or the reason it
    could not be read."""

    name: str
    ok: bool
    value: Any
    problem: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class DeclaredReading:
    """One declared channel's reading: read live from the event (a path
    channel, or an extractor run fresh), taken from the daemon's cache with
    the event it was graded on, or not yet graded."""

    name: str
    form: str
    source: str  # live, cached or none
    ok: bool
    value: Any
    problem: str | None
    event: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class ChannelReadings:
    """The CHANNELS block's facts (docs/ARCHITECTURE.md section 7.4): the
    registered names, the host values and the declared readings."""

    registered: tuple[str, ...]
    host: tuple[HostReading, ...]
    declared: tuple[DeclaredReading, ...]


def channel_readings(run: Run, event: Event, *, fresh: bool = False) -> ChannelReadings:
    """The CHANNELS block: host values, path values read from the event, and
    extractor values from the cache (or computed fresh when asked). Never
    spawns a subprocess unless fresh is true."""
    declared = load_declared(run.paths)
    host: list[HostReading] = []
    for name in HOST_CHANNELS:
        ok, value = channel_value(run, name, event)
        host.append(HostReading(name, ok, value if ok else None, None if ok else str(value)))
    readings = load_readings(run.paths)
    rendered: list[DeclaredReading] = []
    for name, spec in sorted(declared.items()):
        form = str(spec["form"])
        if form == "path" or fresh:
            ok, value = channel_value(run, name, event)
            rendered.append(
                DeclaredReading(name, form, "live", ok, value if ok else None, None if ok else str(value), None)
            )
            continue
        last = readings.get(name)
        if isinstance(last, dict) and last.get("event") is not None:
            rendered.append(
                DeclaredReading(name, form, "cached", True, last.get("value"), None, int(last["event"]))
            )
        else:
            rendered.append(DeclaredReading(name, form, "none", False, None, None, None))
    return ChannelReadings(
        registered=tuple(known_channels(run)), host=tuple(host), declared=tuple(rendered)
    )


def channel_text(readings: ChannelReadings) -> list[str]:
    lines = ["CHANNELS | registered: " + " · ".join(readings.registered)]
    host = [f"{item.name}={_render(item.value) if item.ok else 'n/a'}" for item in readings.host]
    lines.append("CHANNELS | host: " + " · ".join(host))
    if not readings.declared:
        return lines
    rendered = []
    for item in readings.declared:
        if item.source == "live":
            rendered.append(f"{item.name}={_render(item.value) if item.ok else 'unreadable'} ({item.form})")
        elif item.source == "cached":
            rendered.append(f"{item.name}={_render(item.value)} @e{item.event} (extractor, last graded)")
        else:
            rendered.append(f"{item.name}=not yet graded (extractor)")
    lines.append("CHANNELS | declared: " + " · ".join(rendered))
    return lines


def channel_lines(run: Run, event: Event, *, fresh: bool = False) -> list[str]:
    return channel_text(channel_readings(run, event, fresh=fresh))


@dataclasses.dataclass(frozen=True, slots=True)
class DeclaredChannel:
    name: str
    form: str
    path: str | None
    hash: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class ChannelList:
    """What `assay channel list` knows: the registered names, the readings
    at the last event (None on a run without events) and the declarations
    with their path or extractor hash."""

    registered: tuple[str, ...]
    readings: ChannelReadings | None
    declared: tuple[DeclaredChannel, ...]

    def to_json(self) -> dict[str, Any]:
        from .records import plain

        return {
            "registered": list(self.registered),
            "readings": None if self.readings is None else plain(self.readings),
            "declared": [plain(item) for item in self.declared],
        }


def channel_declared_text(name: str, spec: Mapping[str, Any]) -> str:
    """The line `assay channel declare` prints: the name, its form and the
    claims it now grades."""
    return (
        f"CHANNEL | declared {name} ({spec['form']}); claims "
        f"like `ch {name} = V` now parse and grade"
    )


def channel_list_of(run: Run, *, fresh: bool = False) -> ChannelList:
    events = run.events
    declared = load_declared(run.paths)
    return ChannelList(
        registered=tuple(known_channels(run)),
        readings=channel_readings(run, events[-1], fresh=fresh) if events else None,
        declared=tuple(
            DeclaredChannel(name, str(spec["form"]), spec.get("path"), spec.get("hash"))
            for name, spec in sorted(declared.items())
        ),
    )


def channel_list_text(listing: ChannelList) -> list[str]:
    if listing.readings is not None:
        lines = channel_text(listing.readings)
    else:
        lines = ["CHANNELS | " + " · ".join(listing.registered)]
    for item in listing.declared:
        detail = item.path or (item.hash or "")[:12]
        lines.append(f"  {item.name}: {item.form} {detail}")
    return lines


def channel_change_lines(run: Run, before: Event, after: Event) -> list[str]:
    """Receipt lines for declared path channels that changed across a paid
    span: `CHANNELS | name: before -> after`. Path channels only (no
    subprocess on the receipt path)."""
    lines: list[str] = []
    for name, spec in sorted(load_declared(run.paths).items()):
        if spec["form"] != "path":
            continue
        ok_before, was = channel_value(run, name, before)
        ok_after, now = channel_value(run, name, after)
        if ok_before and ok_after and was != now:
            lines.append(f"CHANNELS | {name}: {_render(was)} -> {_render(now)}")
    return lines


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def grade_channel_claim(
    run: Run,
    claim: Claim,
    prior_event: Event,
    event: Event,
) -> Grade:
    """Grade one channel claim against before/after channel readings."""
    name = str(claim.channel)
    ok_after, after = channel_value(run, name, event, remember=True)
    if not ok_after:
        return Grade.of(claim, ok=False, ungradable=True, actual=f"UNGRADABLE: {after}")
    kind = claim.kind
    if kind == "channel_eq":
        expected = claim.value
        tol = claim.tol
        actual_text = f"ch {name} = {json.dumps(after)}"
        if tol is not None:
            after_number, expected_number = _numeric(after), _numeric(expected)
            if after_number is None or expected_number is None:
                return Grade.of(
                    claim,
                    ok=False,
                    ungradable=True,
                    actual=f"UNGRADABLE: tolerance needs numeric values, got {json.dumps(after)}",
                )
            ok = abs(after_number - expected_number) <= float(tol)
        elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
            after_number = _numeric(after)
            ok = after_number is not None and after_number == float(expected)
        else:
            ok = after == expected
        return Grade.of(claim, ok=bool(ok), actual=actual_text)
    ok_before, before = channel_value(run, name, prior_event)
    if not ok_before:
        return Grade.of(claim, ok=False, ungradable=True, actual=f"UNGRADABLE: {before}")
    before_number, after_number = _numeric(before), _numeric(after)
    if before_number is None or after_number is None:
        return Grade.of(
            claim,
            ok=False,
            ungradable=True,
            actual=(
                f"UNGRADABLE: {kind.replace('channel_', '')} needs numeric readings, "
                f"got {json.dumps(before)} -> {json.dumps(after)}"
            ),
        )
    if kind == "channel_delta":
        delta = after_number - before_number
        op = claim.op
        target = _numeric(claim.value)
        if op == "sign":
            ok = delta > 0 if claim.sign == "+" else delta < 0
        elif target is None:  # pragma: no cover - parser guarantees a numeric value
            raise AssayError(f"delta claim {claim.text!r} has no numeric value", code="CLAIM_SYNTAX")
        elif op == "=":
            ok = delta == target
        elif op == ">=":
            ok = delta >= target
        elif op == "<=":
            ok = delta <= target
        else:  # pragma: no cover - parser guarantees op
            raise AssayError(f"unknown delta op {op!r}", code="INTERNAL")
        return Grade.of(
            claim,
            ok=bool(ok),
            actual=f"ch {name} moved {delta:+g} ({before_number:g} -> {after_number:g})",
        )
    if kind == "channel_cross":
        threshold = float(claim.value)  # type: ignore[arg-type]  # the parser guarantees a number
        direction = claim.direction
        rose = before_number < threshold <= after_number
        fell = before_number > threshold >= after_number
        if direction == "below":
            ok = rose
        elif direction == "above":
            ok = fell
        else:
            ok = rose or fell
        return Grade.of(
            claim,
            ok=bool(ok),
            actual=f"ch {name} went {before_number:g} -> {after_number:g} (threshold {threshold:g})",
        )
    raise AssayError(f"unknown channel claim kind {kind!r}", code="INTERNAL")  # pragma: no cover
