from inventory.report import Order, summary_line, top_categories, units_by_category

MORNING = [
    Order("rake-01", "garden", 5),
    Order("pan-02", "kitchen", 4),
    Order("saw-03", "tools", 3),
    Order("hose-04", "garden", 7),
    Order("drill-05", "tools", 4),
    Order("knife-06", "kitchen", 3),
    Order("lamp-07", "lighting", 2),
]


def test_units_by_category():
    assert units_by_category(MORNING) == {"garden": 12, "kitchen": 7, "tools": 7, "lighting": 2}


def test_the_best_seller_comes_first():
    assert top_categories(MORNING, 1) == ["garden"]


def test_categories_that_tie_keep_their_first_appearance():
    assert top_categories(MORNING, 3) == ["garden", "kitchen", "tools"]


def test_the_summary_line():
    assert summary_line(MORNING) == "top: garden 12, kitchen 7, tools 7"


def test_a_day_without_sales():
    assert summary_line([]) == "top: no sales"
    assert top_categories([], 3) == []


def test_asking_for_more_categories_than_there_are():
    assert top_categories(MORNING[:2], 5) == ["garden", "kitchen"]
