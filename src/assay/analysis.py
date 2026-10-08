"""`assay python`: offline analysis over the journal with the history
preloaded. The namespace is the dict world's here; an observation kind
supplies its own (the frame world adds grids and perception helpers). The
agent's namespace carries the journal as plain JSON objects, the form the
manual describes, built from the run's held records."""

from __future__ import annotations

import ast
import heapq
import json
import math
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from .core import AssayError, canonical_action
from .extras import kind_for
from .records import Event
from .textobs import delta_lines, key_delta

if TYPE_CHECKING:
    from .run import Run


def show(value: Any) -> str:
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
    queue: deque[tuple[Any, tuple[Any, ...]]] = deque([(start, ())])
    seen = {key(start)}
    nodes = 0
    while queue and nodes < max_nodes:
        state, route = queue.popleft()
        nodes += 1
        if is_goal(state):
            return list(route), nodes
        for action, successor in expand(state):
            marker = key(successor)
            if marker not in seen:
                seen.add(marker)
                queue.append((successor, route + (action,)))
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
    queue: list[tuple[float, int, float, Any, tuple[Any, ...]]] = [
        (float(heuristic(start)), 0, 0.0, start, ())
    ]
    best = {key(start): 0.0}
    sequence = nodes = 0
    while queue and nodes < max_nodes:
        _, _, cost, state, route = heapq.heappop(queue)
        if cost != best.get(key(state)):
            continue
        nodes += 1
        if is_goal(state):
            return list(route), nodes
        for action, successor, edge_cost in expand(state):
            next_cost = cost + float(edge_cost)
            marker = key(successor)
            if next_cost >= best.get(marker, math.inf):
                continue
            best[marker] = next_cost
            sequence += 1
            heapq.heappush(
                queue,
                (
                    next_cost + float(heuristic(successor)),
                    sequence,
                    next_cost,
                    successor,
                    route + (action,),
                ),
            )
    return None, nodes


def _general_namespace(events: Sequence[Event]) -> dict[str, Any]:
    """Offline namespace for dict-observation runs: no grid helpers."""
    observations = [event.observation for event in events]
    transitions = [
        {
            "event": event.id,
            "action": canonical_action(event),
            "before": observations[index - 1],
            "after": observations[index],
            "state": event.state,
            "level": event.levels_completed + 1,
        }
        for index, event in enumerate(events)
        if index
    ]
    return {
        "np": np,
        "json": json,
        "observation": observations[-1],
        "previous": observations[-2] if len(observations) > 1 else None,
        "observations": observations,
        "events": [event.to_json() for event in events],
        "transitions": transitions,
        "actions": [canonical_action(event) for event in events[1:]],
        "key_delta": key_delta,
        "delta_lines": delta_lines,
        "bfs": bfs,
        "astar": astar,
    }


def namespace(run: Run) -> dict[str, Any]:
    events = run.events
    kind = kind_for(events[-1]) if events else None
    if kind is not None:
        return kind.python_namespace(events)
    return _general_namespace(events)


def run_python(run: Run, source: str) -> Any:
    scope = namespace(run)
    try:
        tree = ast.parse(source, mode="exec")
        if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
            value = eval(
                compile(ast.Expression(tree.body[0].value), "<assay-python>", "eval"),
                scope,
                scope,
            )
            if value is not None:
                print(
                    show(value)
                    if isinstance(value, np.ndarray) and value.ndim == 2
                    else repr(value)
                )
            return value
        exec(compile(tree, "<assay-python>", "exec"), scope, scope)  # noqa: S102 - deliberate local analysis console
        return None
    except Exception as error:
        raise AssayError(
            f"analysis failed: {type(error).__name__}: {error}",
            code="PYTHON_FAILED",
        ) from error
