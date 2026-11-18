"""`assay python`: offline thinking over the record.

Thinking is free and probing is paid, so the whole history is preloaded for
the agent's own code: `events` (the journal as dicts), `actions`,
`transitions` (event, action, before, after), and either `observations` with
`key_delta` and `delta_lines` (a dict world) or the grids as arrays with the
perception helpers (a frame world), plus `np`, `json`, `bfs` and `astar`.
The code runs as the agent, in this process: it reads and never spends.
"""

from __future__ import annotations

import ast
import heapq
import json
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

from . import AssayError
from .delta import delta_lines, key_delta
from .journal import Run
from .world import render_action


def show(value: Any) -> str:
    """A 2-D array as rows of hex digits; anything else as its repr."""
    array = np.asarray(value)
    if array.ndim != 2:
        return repr(value)
    return "\n".join("".join(format(int(cell), "x") for cell in row) for row in array)


def bfs(
    start: Any,
    is_goal: Callable[[Any], bool],
    expand: Callable[[Any], Iterable[tuple[Any, Any]]],
    *,
    key: Callable[[Any], Any] = repr,
    max_nodes: int = 100_000,
) -> tuple[list[Any] | None, int]:
    """Breadth-first search: `expand(state)` yields (action, successor).
    Returns the action route, or None, and the nodes expanded."""
    queue: deque[tuple[Any, tuple[Any, ...]]] = deque([(start, ())])
    seen, nodes = {key(start)}, 0
    while queue and nodes < max_nodes:
        state, route = queue.popleft()
        nodes += 1
        if is_goal(state):
            return list(route), nodes
        for action, successor in expand(state):
            if key(successor) not in seen:
                seen.add(key(successor))
                queue.append((successor, (*route, action)))
    return None, nodes


def astar(
    start: Any,
    is_goal: Callable[[Any], bool],
    expand: Callable[[Any], Iterable[tuple[Any, Any, float]]],
    heuristic: Callable[[Any], float],
    *,
    key: Callable[[Any], Any] = repr,
    max_nodes: int = 100_000,
) -> tuple[list[Any] | None, int]:
    """A*: `expand(state)` yields (action, successor, cost)."""
    queue: list[tuple[float, int, float, Any, tuple[Any, ...]]] = [(float(heuristic(start)), 0, 0.0, start, ())]
    best, sequence, nodes = {key(start): 0.0}, 0, 0
    while queue and nodes < max_nodes:
        _, _, cost, state, route = heapq.heappop(queue)
        if cost != best.get(key(state)):
            continue
        nodes += 1
        if is_goal(state):
            return list(route), nodes
        for action, successor, step in expand(state):
            reached = cost + float(step)
            if reached < best.get(key(successor), float("inf")):
                best[key(successor)] = reached
                sequence += 1
                heapq.heappush(
                    queue, (reached + float(heuristic(successor)), sequence, reached, successor, (*route, action))
                )
    return None, nodes


def namespace(run: Run) -> dict[str, Any]:
    events = run.events
    scope: dict[str, Any] = {
        "np": np,
        "json": json,
        "events": events,
        "actions": [render_action(event["action"], event["data"]) for event in events[1:]],
        "show": show,
        "bfs": bfs,
        "astar": astar,
    }
    if "frames" in events[-1]:
        from . import grid

        return {**scope, **grid.namespace(events)}
    observations = [event.get("observation") for event in events]
    return {
        **scope,
        "observation": observations[-1],
        "previous": observations[-2] if len(observations) > 1 else None,
        "observations": observations,
        "transitions": [
            {
                "event": event["id"],
                "action": render_action(event["action"], event["data"]),
                "before": observations[index - 1],
                "after": observations[index],
                "state": event["state"],
                "level": event["levels_completed"] + 1,
            }
            for index, event in enumerate(events)
            if index
        ],
        "key_delta": key_delta,
        "delta_lines": delta_lines,
    }


def run_python(run: Run, source: str) -> None:
    """Run the agent's source with the history in scope. A single expression
    prints its value, as an interpreter would."""
    scope = namespace(run)
    try:
        tree = ast.parse(source, mode="exec")
        if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
            value = eval(compile(ast.Expression(tree.body[0].value), "<assay-python>", "eval"), scope)  # noqa: S307
            if value is not None:
                print(show(value) if isinstance(value, np.ndarray) else repr(value))
        else:
            exec(compile(tree, "<assay-python>", "exec"), scope)  # noqa: S102 - the agent's own analysis
    except (Exception, SystemExit) as error:
        raise AssayError(f"analysis failed: {type(error).__name__}: {error}", code="PYTHON_FAILED") from error
