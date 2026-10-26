"""The coverage ledger about objects and regions (the sonnet-haiku-5-5
experiment): a click-only progress unit gets the board objects no click
touched instead of "untried [none]", never "saturated", and the regions and
objects reach the status line at a stop."""

from __future__ import annotations

from pathlib import Path

from conftest import event_of, run_of

# A 16x16 board: background 0, a 2x2 object of color 3 at x=2..3 y=2..3, a
# 3x1 object of color 5 at x=10..12 y=4, and a 2x2 object of color 7 at
# x=12..13 y=12..13.
ROWS = ["0" * 16 for _ in range(16)]


def _paint(rows: list[str], color: str, cells: list[tuple[int, int]]) -> list[str]:
    grid = [list(row) for row in rows]
    for x, y in cells:
        grid[y][x] = color
    return ["".join(row) for row in grid]


BOARD = _paint(ROWS, "3", [(2, 2), (3, 2), (2, 3), (3, 3)])
BOARD = _paint(BOARD, "5", [(10, 4), (11, 4), (12, 4)])
BOARD = _paint(BOARD, "7", [(12, 12), (13, 12), (12, 13), (13, 13)])


def _events(clicks: list[tuple[int, int]]):
    events = [event_of(id=0, action="START", data=None, counts_action=False, frames=[BOARD],
                       available_actions=[6], win_levels=3)]
    for index, (x, y) in enumerate(clicks, start=1):
        events.append(event_of(id=index, action="ACTION6", data={"x": x, "y": y}, frames=[BOARD],
                               available_actions=[6], win_levels=3, predict="change", predict_ok=False,
                               grade=[{"kind": "change", "text": "change", "ok": False, "actual": "", "bucket": "world_model"}]))
    return events


def _view(events, root: Path | None = None, notes: str | None = None):
    from assay.core import RunPaths
    from assay.modules import ModuleView

    paths = RunPaths(root or Path("/nonexistent/assay-coverage-objects"))
    if root is not None:
        paths.state.mkdir(parents=True, exist_ok=True)
        paths.notes.write_text(notes or "")
    return ModuleView(run_of(paths, events, registry={"actions": []}))


def _module():
    from assay.modules import BUILTINS

    return next(module for module in BUILTINS if module.NAME == "coverage_audit")


def test_objects_never_clicked_replace_untried_none_on_a_click_only_unit():
    from assay_grid import coverage

    events = _events([(2, 2), (2, 3), (3, 3)])
    found = coverage._objects(events, [1, 2, 3])
    # The background (the board-sized component) is not an object; the
    # untouched ones are listed largest first.
    assert found == {"objects": 3, "touched": 1, "examples": ["color 7 at 12,12 (4 cells)", "color 5 at 10,4 (3 cells)"]}
    line = _module().trigger(_view(events), None)
    assert line.startswith("coverage level 1: one action is advertised (ACTION6), so untried and no-op-only say nothing here")
    assert "2/3 board objects never clicked on this unit" in line
    assert "untried [none]" not in line
    # Every object clicked: the ledger says so instead of inventing a gap.
    clicked_all = _events([(2, 2), (11, 4), (13, 13)])
    assert "all 3 board objects clicked on this unit" in _module().trigger(_view(clicked_all), None)


def test_one_advertised_action_is_never_saturated():
    from assay.modules import _gap_phrase, coverage_ledger

    # Every object clicked and every region probed (the fourth click lands on
    # background in the last region): nothing is left to list, and still not
    # "saturated".
    events = _events([(2, 2), (11, 4), (13, 13), (2, 12)])
    phrase = _gap_phrase(events, coverage_ledger(events))
    assert phrase.startswith("unexercised: one action is advertised, so the action ledger cannot judge coverage")
    partial = _events([(2, 2), (11, 4), (13, 13)])
    assert _gap_phrase(partial, coverage_ledger(partial)).startswith("unexercised: 1/4 grid regions unprobed")


def test_regions_and_objects_reach_the_status_line_at_a_stop(tmp_path):
    events = _events([(2, 2), (2, 3), (3, 3)])
    quiet = _module().trigger(_view(events, tmp_path / "quiet", "# Notes\n## Plan\n- try the 5 bar next\n"), None)
    assert "AT A STOP" not in quiet and "regions unprobed" not in quiet
    stopped = _module().trigger(_view(events, tmp_path / "stop", "# Notes\n## Plan\n- level 1 parked: blocked\n"), None)
    assert " AT A STOP: unexercised: 2/3 board objects never clicked on this unit" in stopped
    assert "grid regions unprobed" in stopped
