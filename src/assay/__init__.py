"""ASSAY, a referee harness between an agent and a world it is registered to.

`Unknown` is the marker an agent's executable model returns for a transition
it does not model (an explicitly unmodeled step, not a failed prediction). The
perception helpers re-exported below belong to the frame-world extra
(`assay_grid.perception`); they are forwarded here so scripts and rules files
that wrote `from assay import connected_components` keep working.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__version__ = "1.2.0"
JOURNAL_SPEC = "assay-journal-v1"


@dataclass(frozen=True)
class Unknown:
    """An explicitly unmodeled transition, not a failed prediction."""

    reason: str = "unspecified transition"


def _forward(name: str) -> Callable[..., Any]:
    def implementation(*args: Any, **kwargs: Any) -> Any:
        from assay_grid import perception

        return getattr(perception, name)(*args, **kwargs)

    implementation.__name__ = name
    implementation.__doc__ = f"Forwarder to assay_grid.perception.{name} (frame worlds)."
    return implementation


connected_components = _forward("connected_components")
frame_delta = _forward("frame_delta")
infer_lattice = _forward("infer_lattice")
line_graph = _forward("line_graph")
motion_trace = _forward("motion_trace")
repeated_shapes = _forward("repeated_shapes")
transition_story = _forward("transition_story")

__all__ = [
    "JOURNAL_SPEC",
    "Unknown",
    "__version__",
    "connected_components",
    "frame_delta",
    "infer_lattice",
    "line_graph",
    "motion_trace",
    "repeated_shapes",
    "transition_story",
]
