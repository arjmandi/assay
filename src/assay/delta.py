"""What changed between two observations.

One measure serves the `noop` and `change` outcomes, the KEY DELTA and the
history lines, and the coverage audit: the keys of a dict observation that
were added, removed or changed, or the cells of a settled frame that differ.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .journal import Event


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Nested objects as dotted paths; lists and scalars are leaves."""
    if isinstance(value, Mapping) and value:
        output: dict[str, Any] = {}
        for key in value:
            output.update(flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return output
    return {prefix or "<root>": value}


def key_delta(before: Any, after: Any) -> dict[str, list[Any]]:
    """The added, removed and changed dotted keys between two objects."""
    left, right = flatten(before or {}), flatten(after or {})
    return {
        "added": [(path, right[path]) for path in sorted(set(right) - set(left))],
        "removed": [(path, left[path]) for path in sorted(set(left) - set(right))],
        "changed": [
            (path, left[path], right[path]) for path in sorted(set(left) & set(right)) if left[path] != right[path]
        ],
    }


def _short(value: Any, limit: int = 60) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def delta_lines(before: Any, after: Any, limit: int = 24) -> list[str]:
    """One line per added, removed or changed key, with its values."""
    delta = key_delta(before, after)
    lines = [f"+ {path} = {_short(value)}" for path, value in delta["added"]]
    lines += [f"- {path} (was {_short(value)})" for path, value in delta["removed"]]
    lines += [f"~ {path}: {_short(old)} -> {_short(new)}" for path, old, new in delta["changed"]]
    if not lines:
        return ["no observed change"]
    return lines if len(lines) <= limit else [*lines[:limit], f"… {len(lines) - limit} more changed keys"]


def changed(before: Event, after: Event) -> tuple[int | None, str]:
    """How much of the observation differs between two events, and in what
    unit: keys of a dict observation, cells of the settled frame. None when
    the two cannot be compared cell by cell (the frame changed its size)."""
    if "frames" in after and "frames" in before:
        old, new = before["frames"][-1], after["frames"][-1]
        if len(old) != len(new) or any(len(a) != len(b) for a, b in zip(old, new)):
            return None, "cells"
        return sum(a != b for row_a, row_b in zip(old, new) for a, b in zip(row_a, row_b)), "cells"
    delta = key_delta(before.get("observation"), after.get("observation"))
    return sum(len(items) for items in delta.values()), "keys"
