"""The names the manual documents for `assay python` exist in frame worlds
(the sonnet-haiku-5-5 experiment): `observations`, `key_delta` and
`delta_lines` in the grid's terms, so the documented one-liners run where
69 of 70 runs of the 2026-10-25 sweep met a NameError."""

from __future__ import annotations

from conftest import event_of

BEFORE = ["000", "010", "000"]
AFTER = ["000", "000", "020"]


def _events():
    return [
        event_of(id=0, action="START", data=None, counts_action=False, frames=[BEFORE], available_actions=[6]),
        event_of(id=1, action="ACTION6", data={"x": 1, "y": 1}, frames=[AFTER], available_actions=[6]),
    ]


def test_the_documented_names_exist_in_the_grid_namespace():
    from assay_grid.analysis import namespace

    names = namespace(_events())
    for name in ("observations", "transitions", "actions", "key_delta", "delta_lines"):
        assert name in names, name
    assert len(names["observations"]) == 2 and names["observations"][-1].tolist() == [[0, 0, 0], [0, 0, 0], [0, 2, 0]]
    transition = names["transitions"][-1]
    delta = names["key_delta"](transition["before"], transition["after"])
    assert delta == {"added": [], "removed": [], "changed": [((1, 1), 1, 0), ((1, 2), 0, 2)]}
    # The manual's one-liner: the set of keys that ever changed.
    keys = set(k for t in names["transitions"] for k, *_ in names["key_delta"](t["before"], t["after"])["changed"])
    assert keys == {(1, 1), (1, 2)}
    assert names["delta_lines"](names["previous"], names["grid"]) == ["~ (1,1): 1 -> 0", "~ (1,2): 0 -> 2"]
    assert names["delta_lines"](names["grid"], names["grid"]) == ["no observed change"]
