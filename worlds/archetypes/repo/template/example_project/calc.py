"""A small module with one bug, the source tree of the repo archetype's
template. The tests are in check_calc.py, named so that a bare `pytest` at
the repository root does not collect them."""


def add(a: float, b: float) -> float:
    return a + b


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def clamp(value: float, low: float, high: float) -> float:
    """The value, limited to the range from low to high."""
    return max(low, min(value, low))
