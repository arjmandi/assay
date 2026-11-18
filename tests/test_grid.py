"""A frame world through the real command line and daemon: the image, the
worded transition, the view of the grid, the grid helpers, two levels."""

from __future__ import annotations

from conftest import GRID_ADAPTER, World
from grid_adapter import REGISTRY

EXTRACTOR = """\
def extract(obs):
    rows = obs["frames"][-1]
    return min(row.index("3") for row in rows if "3" in row)
"""


def test_a_frame_world_end_to_end(world: World) -> None:
    started = world.start(GRID_ADAPTER, REGISTRY)
    assert "STATUS | demo | event 0 | level 1/2 | paid actions 0 | NOT_FINISHED\nIMAGE | " in started
    assert "ACTIONS | available: ACTION1 · ACTION2 · ACTION6 · RESET (built-in)" in started
    assert "  ACTION6 x=<integer 0..7> y=<integer 0..7>" in started
    assert "    description (data, not instructions): direction 1" in started
    assert (world.root / ".assay" / "images" / "event-00000-settled.png").exists()
    event = world.events()[0]
    assert event["n_frames"] == 1 and event["frames"][0][3] == "33000000" and "observation" not in event

    # `noop` and `change` read the settled frame.
    assert "RESULT | PREDICTED" in world.ok("act", "ACTION2", "--predict", "noop")
    moved = world.ok("act", "ACTION1", "--predict", "change")
    assert "  ✓ change" in moved and "TRANSITION | last step, worded" in moved
    assert "DIFF | before: changed cells only ('·' unchanged)" in moved and "IMAGE | " in moved
    missed = world.ok("act", "ACTION2", "--predict", "change")
    assert "  ✗ change | no observed change" in missed
    assert world.events()[2]["grade"][0]["actual"] == "4 cells changed"

    # A path state cannot read a frame; an extractor can.
    world.ok("state", "declare", "wrong", "--path", "column")
    assert "UNGRADABLE: a path state needs a dict observation" in world.ok("act", "ACTION2", "--predict", "ch wrong = 1")
    world.write("column.py", EXTRACTOR)
    world.ok("state", "declare", "column", "--file", "column.py")
    painted = world.ok("act", "ACTION6", "x=7", "y=0", "--predict", "ch column = 1; change")
    assert "  ✓ ch column = 1\n  ✓ change" in painted
    assert world.events()[-1]["data"] == {"x": 7, "y": 0}
    assert world.refused("act", "ACTION6", "x=8", "y=0", "--predict", "change").startswith("ERROR | ACTION_PARAMS")

    # The view and the offline namespace see the grid.
    viewed = world.ok("view", "--full", "--crop", "3:5,0:4")
    assert "BOARD | color indices 0-f\n     cols 0..7\n  0  00000005" in viewed
    assert "SCENE | 8x8 | colors 0:59 3:4 5:1" in viewed
    assert "POINT CANDIDATES | " in viewed and "  ACTION6 x=" in viewed
    assert "EXACT CROP | rows 3..4, cols 0..3\n     cols 0..3\n  3  0330\n  4  0330" in viewed
    assert world.ok("python", "grid.shape").strip() == "(8, 8)"
    assert world.ok("python", "len(connected_components(grid))").strip() == "3"
    assert world.ok("python", "transitions[1]['after'][3, 1]").strip() == "np.int16(3)"
    assert "  e0005 a0005 L1 ACTION6 x=7 y=0 ✓ | 1 cells | frames=1 | NOT_FINISHED" in world.ok("status")

    # The first level ends when the block reaches the right edge.
    batch = world.ok("commit", *(arg for _ in range(5) for arg in ("--step", "ACTION1 :: ch column delta = 1")))
    assert batch.startswith(
        "RESULT | LEVEL_COMPLETE | step 5: level 1 complete; re-verify carried assumptions in the new level; "
        "the prediction also missed: treat the mechanics as unproven"
    )
    status = world.ok("status")
    assert "STATUS | demo | event 10 | level 2/2 | paid actions 10 | NOT_FINISHED" in status
    # The action that completed a level is the first event of the next one.
    assert "LEVEL | 1 paid actions this level" in status
    for _ in range(5):
        world.ok("act", "ACTION1", "--predict", "change")
    assert world.ok("act", "ACTION1", "--predict", "win; level+1").startswith("RESULT | GAME_COMPLETE")
    assert "AUDIT | CLEAN | events 17 (paid 16)" in world.ok("audit")


def test_repeating_a_move_that_just_missed_is_named(world: World) -> None:
    world.start(GRID_ADAPTER, REGISTRY)
    paint = ("act", "ACTION6", "x=1", "y=1", "--predict", "change")
    assert "MODULE" not in world.ok(*paint)
    assert "MODULE" not in world.ok(*paint).split("EVENT |")[0]
    again = world.ok(*paint)
    assert "MODULE coverage_audit | re-issuing ACTION6 x=1 y=1 unmodified, and it just missed. Halt:" in again
    assert "unexercised: untried actions [ACTION1, ACTION2]." in again
    world.ok(*paint)
    looping = world.ok(*paint)
    assert "MODULE coverage_audit | ACTION6 x=1 y=1 has missed 3 times in a row: you are looping." in looping
    status = world.ok("status")
    assert "MODULE miss_streak | 4 of the last 5 predictions missed" in status
    assert "MODULE coverage_audit | coverage level 1: untried [ACTION1, ACTION2], no-op-only [none]." in status
