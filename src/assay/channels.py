"""Channels: registered, named, code-extracted readings of the observation.

A channel is a pair (observer, extractor). Three sources:

- HOST channels, always present: `goal` (boolean — the environment's win
  state; the one channel an agent-invented proxy can never replace), `level`
  (levels completed — the milestone channel), and `budget_remaining` (paid
  actions left under the registered cap, when one exists).
- AGENT-DECLARED channels (`assay channel declare NAME --path a.b.c` or
  `--file extractor.py`): journaled at declaration. The dotted-path form is
  graded in-kernel (pure data lookup over the dict observation). The
  extractor-file form is agent-authored code and runs ONLY in the verifier
  sandbox (`python3 -I`, empty env, rlimits): `def extract(obs) -> value`.
- Pack channels are a later stage (no second domain pack exists yet).

Claims naming an unregistered channel are refused before any spend and
counted on the mis-reference meter — the surviving referent-grounding
instrument.
"""

from __future__ import annotations

import hashlib
import json
import re
import resource
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .core import AssayError, RunPaths, append_jsonl, atomic_json, read_json

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
HOST_CHANNELS = ("goal", "level", "budget_remaining")
MILESTONE_CHANNELS = ("goal", "level")
EXTRACT_TIMEOUT_SECONDS = 5.0
MAX_DECLARED_CHANNELS = 16

_RUNNER = """\
import importlib.util, json, sys
payload = json.loads(sys.stdin.read())
spec = importlib.util.spec_from_file_location("extractor", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
value = module.extract(payload["obs"])
sys.stdout.write("\\n" + json.dumps({"value": value}) + "\\n")
"""


def channels_path(paths: RunPaths) -> Path:
    return paths.state / "channels.json"


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
                f"{known_channels(paths)} — declare one with `assay channel declare`"
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


def _run_extractor(
    paths: RunPaths, digest: str, obs: Mapping[str, Any]
) -> tuple[bool, Any]:
    stored = extractor_dir(paths) / f"{digest}.py"
    if not stored.exists():
        return False, f"stored extractor {digest[:12]} missing"
    payload = json.dumps({"obs": obs}).encode()
    cpu_seconds = max(1, int(EXTRACT_TIMEOUT_SECONDS))

    def _limits() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    with tempfile.TemporaryDirectory(prefix="assay-channel-") as scratch:
        try:
            completed = subprocess.run(  # noqa: S603 - deliberate sandboxed run
                [sys.executable, "-I", "-c", _RUNNER, str(stored.resolve())],
                input=payload,
                capture_output=True,
                cwd=scratch,
                env={},
                timeout=EXTRACT_TIMEOUT_SECONDS,
                preexec_fn=_limits,
            )
        except subprocess.TimeoutExpired:
            return False, f"extractor timed out after {EXTRACT_TIMEOUT_SECONDS:g}s"
    if completed.returncode != 0:
        stderr = completed.stderr.decode(errors="replace").strip().splitlines()
        tail = stderr[-1][:200] if stderr else "no stderr"
        return False, f"extractor crashed: {tail}"
    lines = [
        line
        for line in completed.stdout.decode(errors="replace").splitlines()
        if line.strip()
    ]
    if not lines:
        return False, "extractor produced no output"
    try:
        return True, json.loads(lines[-1])["value"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return False, f"malformed extractor output: {lines[-1][:120]!r}"


def channel_value(
    paths: RunPaths, name: str, event: Mapping[str, Any]
) -> tuple[bool, Any]:
    """(True, value) or (False, reason). Host channels come from event fields;
    path channels walk the dict observation; extractor channels run sandboxed."""
    if name == "goal":
        return True, str(event["state"]) == "WIN"
    if name == "level":
        return True, int(event["levels_completed"])
    if name == "budget_remaining":
        # Filled by the caller when a budget exists (it needs the full journal);
        # graded standalone it reads as ungradable rather than guessing.
        return False, "budget_remaining is a status meter, not a claimable reading"
    spec = load_declared(paths).get(name)
    if spec is None:
        return False, f"channel {name!r} is not registered"
    if spec["form"] == "path":
        observation = event.get("observation")
        if observation is None:
            return False, "dotted-path channels need a dict observation (this event has frames)"
        return _walk(observation, spec["path"])
    from .verifiers import observation_view

    return _run_extractor(paths, spec["hash"], observation_view(event))


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
    ok_after, after = channel_value(paths, name, event)
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
                f"got {json.dumps(before)} → {json.dumps(after)}"
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
            "actual": f"ch {name} moved {delta:+g} ({before_number:g} → {after_number:g})",
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
            "actual": f"ch {name} went {before_number:g} → {after_number:g} (threshold {threshold:g})",
        }
    raise AssayError(f"unknown channel claim kind {kind!r}")  # pragma: no cover
