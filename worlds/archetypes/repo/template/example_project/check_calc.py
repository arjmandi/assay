from calc import add, clamp, mean


def test_add():
    assert add(2, 3) == 5


def test_mean():
    assert mean([1, 2, 3]) == 2


def test_clamp_inside():
    assert clamp(5, 0, 10) == 5


def test_clamp_above():
    assert clamp(15, 0, 10) == 10


def test_clamp_below():
    assert clamp(-5, 0, 10) == 0
