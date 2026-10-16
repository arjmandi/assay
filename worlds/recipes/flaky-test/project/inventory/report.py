"""Sales reports for a small shop: units per category, the best-selling
categories, and the one-line summary the morning email carries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Order:
    sku: str
    category: str
    units: int


def units_by_category(orders: Iterable[Order]) -> Counter[str]:
    """Units sold per category."""
    counts: Counter[str] = Counter()
    for order in orders:
        counts[order.category] += order.units
    return counts


def top_categories(orders: list[Order], n: int = 3) -> list[str]:
    """The `n` categories that sold the most units, the best first.
    Categories that sold the same number of units keep the order in which
    they first appear in `orders`."""
    counts = units_by_category(orders)
    categories = {order.category for order in orders}
    ranked = sorted(categories, key=lambda category: -counts[category])
    return ranked[:n]


def summary_line(orders: list[Order], n: int = 3) -> str:
    """The morning email's line: `top: garden 12, kitchen 7, tools 7`."""
    if not orders:
        return "top: no sales"
    counts = units_by_category(orders)
    return "top: " + ", ".join(f"{category} {counts[category]}" for category in top_categories(orders, n))
