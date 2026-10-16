"""The checker's own tests: the contract of `top_categories` on days the
project's suite never shows. The player never sees these; checks/score.py
copies them into a scratch copy of the tree beside the project's own tests."""

from inventory.report import Order, summary_line, top_categories

# Ties whose first appearance is not alphabetical: an alphabetical sort is
# not the documented order.
EVENING = [
    Order("drill-05", "tools", 4),
    Order("pan-02", "kitchen", 4),
    Order("rake-01", "garden", 9),
    Order("bulb-08", "lighting", 4),
]


def test_ties_keep_first_appearance_when_it_is_not_alphabetical():
    assert top_categories(EVENING, 4) == ["garden", "tools", "kitchen", "lighting"]


def test_a_three_way_tie_for_second_place():
    assert top_categories(EVENING, 2) == ["garden", "tools"]


def test_the_summary_line_on_a_tied_evening():
    assert summary_line(EVENING) == "top: garden 9, tools 4, kitchen 4"


def test_a_category_that_reappears_keeps_its_first_place_in_a_tie():
    orders = [
        Order("a", "toys", 1),
        Order("b", "books", 2),
        Order("c", "toys", 1),
    ]
    assert top_categories(orders, 2) == ["toys", "books"]
