"""The summary: per arm, over the runs (one per seed and world), the mean of
each metric with a bootstrap percentile interval, and the same per arm and
world over its seeds. Deterministic under a seed: the resampling uses
`random.Random(seed)` and nothing else.

The interval is the percentile bootstrap of the mean: `resamples` draws of n
values with replacement, the mean of each, the 2.5th and 97.5th percentiles
at the default level. With n of 2 per cell, as E1 has, the interval over a
cell is the pair itself, which is the honest width of two seeds; the arm's
interval over six runs is the one worth reading. Nothing here claims
significance.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

METRICS = ("win", "paid_actions", "tokens_per_action", "dollars_per_action", "dollars")


def bootstrap_interval(
    values: Sequence[float], *, seed: int, resamples: int = 2000, level: float = 0.95
) -> tuple[float, float] | None:
    """The percentile interval of the mean, or None without values."""
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    lower = (1.0 - level) / 2.0
    low = means[min(len(means) - 1, int(lower * len(means)))]
    high = means[min(len(means) - 1, int((1.0 - lower) * len(means)))]
    return (low, high)


def _metric_values(rows: Sequence[Mapping[str, Any]], metric: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(metric)
        if isinstance(value, bool):
            values.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _cell(rows: Sequence[Mapping[str, Any]], *, seed: int, resamples: int, level: float) -> dict[str, Any]:
    cell: dict[str, Any] = {"runs": len(rows), "jobs": [row["job"] for row in rows]}
    for metric in METRICS:
        values = _metric_values(rows, metric)
        interval = bootstrap_interval(values, seed=seed, resamples=resamples, level=level)
        cell[metric] = {
            "n": len(values),
            "values": values,
            "mean": (sum(values) / len(values)) if values else None,
            "interval": list(interval) if interval else None,
        }
    cell["wins"] = int(sum(_metric_values(rows, "win")))
    return cell


def summarize(
    rows: Sequence[Mapping[str, Any]],
    *,
    name: str,
    seed: int = 0,
    resamples: int = 2000,
    level: float = 0.95,
) -> dict[str, Any]:
    by_arm: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_arm_world: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_arm[str(row["arm"])].append(row)
        by_arm_world[(str(row["arm"]), str(row["world"]))].append(row)
    arms: dict[str, Any] = {}
    for arm, arm_rows in by_arm.items():
        cell = _cell(arm_rows, seed=seed, resamples=resamples, level=level)
        cell["worlds"] = {
            world: _cell(world_rows, seed=seed, resamples=resamples, level=level)
            for (cell_arm, world), world_rows in by_arm_world.items()
            if cell_arm == arm
        }
        arms[arm] = cell
    return {
        "name": name,
        "rows": len(rows),
        "bootstrap": {"seed": seed, "resamples": resamples, "level": level},
        "arms": arms,
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    return "none" if value is None else f"{value:.{digits}f}"


def _stat(cell: Mapping[str, Any], metric: str, digits: int = 2) -> str:
    stat = cell[metric]
    if stat["mean"] is None:
        return "none"
    low, high = stat["interval"]
    return f"{_fmt(stat['mean'], digits)} [{_fmt(low, digits)}, {_fmt(high, digits)}]"


def render_summary(summary: Mapping[str, Any]) -> str:
    boot = summary["bootstrap"]
    lines = [
        f"SUMMARY | {summary['name']} | {summary['rows']} run(s) | bootstrap "
        f"{boot['resamples']} resamples, {int(boot['level'] * 100)}% percentile "
        f"intervals of the mean, seed {boot['seed']}"
    ]
    for arm, cell in summary["arms"].items():
        lines.append(
            f"ARM | {arm} | runs {cell['runs']} | wins {cell['wins']}/{cell['runs']} "
            f"{_stat(cell, 'win')} | paid actions {_stat(cell, 'paid_actions', 1)} | "
            f"tokens/action {_stat(cell, 'tokens_per_action', 1)} | "
            f"USD/action {_stat(cell, 'dollars_per_action', 4)} | "
            f"USD/run {_stat(cell, 'dollars')}"
        )
        for world, world_cell in cell["worlds"].items():
            paid = ", ".join(f"{int(value)}" for value in world_cell["paid_actions"]["values"])
            lines.append(
                f"  {world} | runs {world_cell['runs']} | wins "
                f"{world_cell['wins']}/{world_cell['runs']} | paid actions {paid} | "
                f"mean {_stat(world_cell, 'paid_actions', 1)}"
            )
    return "\n".join(lines)
