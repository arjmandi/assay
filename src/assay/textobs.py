"""Utilities for dict-shaped (non-grid) observations.

A general adapter observes a JSON object instead of a grid. These helpers
flatten it to dotted key paths, diff two observations into a KEY DELTA, and
pretty-print it with sane truncation. No grid concepts appear here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_VALUE_LIMIT = 60
LINE_LIMIT = 200  # characters a pretty-printed line is cut to


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts to dotted paths; lists and scalars are leaves."""
    if isinstance(value, Mapping) and value:
        output: dict[str, Any] = {}
        for key in value:
            path = f"{prefix}.{key}" if prefix else str(key)
            output.update(flatten(value[key], path))
        return output
    return {prefix or "<root>": value}


def _short(value: Any, limit: int = _VALUE_LIMIT) -> str:
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def key_delta(before: Any, after: Any) -> dict[str, list[Any]]:
    """Compare two observations: added / removed / changed dotted keys."""
    left = flatten(before if isinstance(before, Mapping) else {"<root>": before})
    right = flatten(after if isinstance(after, Mapping) else {"<root>": after})
    added = [(path, right[path]) for path in sorted(set(right) - set(left))]
    removed = [(path, left[path]) for path in sorted(set(left) - set(right))]
    changed = [
        (path, left[path], right[path])
        for path in sorted(set(left) & set(right))
        if left[path] != right[path]
    ]
    return {"added": added, "removed": removed, "changed": changed}


def changed_count(before: Any, after: Any) -> int:
    delta = key_delta(before, after)
    return len(delta["added"]) + len(delta["removed"]) + len(delta["changed"])


def delta_lines(before: Any, after: Any, limit: int = 24) -> list[str]:
    """Word a transition as one line per added/removed/changed key."""
    delta = key_delta(before, after)
    lines: list[str] = []
    for path, value in delta["added"]:
        lines.append(f"+ {path} = {_short(value)}")
    for path, value in delta["removed"]:
        lines.append(f"- {path} (was {_short(value)})")
    for path, old, new in delta["changed"]:
        lines.append(f"~ {path}: {_short(old)} -> {_short(new)}")
    if not lines:
        return ["no observed change"]
    if len(lines) > limit:
        omitted = len(lines) - limit
        lines = lines[:limit] + [f"… {omitted} more changed keys"]
    return lines


def _pretty(data: Any) -> tuple[list[str], int]:
    """Every line of the pretty JSON, each cut to the width, and how many
    were cut."""
    try:
        text = json.dumps(data, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = repr(data)
    lines: list[str] = []
    shortened = 0
    for line in text.splitlines():
        if len(line) > LINE_LIMIT:
            shortened += 1
            line = line[: LINE_LIMIT - 1] + "…"
        lines.append(line)
    return lines, shortened


def pretty_lines(data: Any, max_lines: int = 48, *, head_only: bool = False) -> list[str]:
    """Pretty JSON for an observation, truncated in the middle when long, or
    after the head when a status budget dropped the tail (`head_only`,
    docs/ARCHITECTURE.md section 7.6)."""
    lines, _ = _pretty(data)
    if len(lines) <= max_lines:
        return lines
    if head_only:
        return lines[:max_lines] + [f"… {len(lines) - max_lines} lines omitted …"]
    head = max_lines * 2 // 3
    tail = max_lines - head
    omitted = len(lines) - head - tail
    return lines[:head] + [f"… {omitted} lines omitted …"] + lines[-tail:]


def pretty_cuts(data: Any, max_lines: int = 48) -> tuple[int, int, int]:
    """What `pretty_lines` leaves out at this cap: the lines of the whole
    pretty print, the lines the cap omits and the lines cut to the width."""
    lines, shortened = _pretty(data)
    omitted = len(lines) - max_lines if len(lines) > max_lines else 0
    return len(lines), omitted, shortened
