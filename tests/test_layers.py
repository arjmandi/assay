"""The layers an operator steers at: a module of the operator's own, and the
diagnosis that reads a record against the five layers."""

from __future__ import annotations

import json

from conftest import COUNTER, World

MODULE = '''\
NAME = "lamp_watch"


def advise(events, pending):
    if pending and pending["name"] == "SET_LAMP":
        return f"the lamp was set {sum(1 for event in events if event['action'] == 'SET_LAMP')} time(s) before"
    if pending is None and events[-1]["observation"]["lamp"] == "on":
        return "the lamp is on"
    return None
'''


def _registry(world: World, **extra: object) -> dict[str, object]:
    return {**json.loads((COUNTER / "registry.json").read_text()), **extra}


def test_an_operators_module_advises_on_the_receipt_and_in_the_status(world: World) -> None:
    world.write("lamp_watch.py", MODULE)
    world.start(COUNTER / "adapter.py", _registry(world, modules=["lamp_watch.py"]))
    out = world.ok("act", "SET_LAMP", "state=on", "--predict", "change")
    assert "MODULE lamp_watch | the lamp was set 0 time(s) before" in out
    assert "MODULE lamp_watch | the lamp is on" in world.ok("status")
    assert "MODULE lamp_watch" not in world.ok("act", "NOOP", "--predict", "noop")


def test_a_module_that_does_not_load_refuses_the_start(world: World) -> None:
    world.write("broken.py", "NAME = 'hazard'\n\ndef advise(events, pending):\n    return None\n")
    (world.root / "registry.json").write_text(json.dumps(_registry(world, modules=["broken.py"])))
    refusal = world.refused(
        "start", "demo", "--adapter", str(COUNTER / "adapter.py"), "--registry", str(world.root / "registry.json")
    )
    assert refusal.startswith("ERROR | MODULE_CONTRACT | ")


def test_a_clean_record_names_no_layer(counter: World) -> None:
    counter.ok("state", "declare", "counter", "--path", "counter")
    counter.ok("act", "INC", "amount=1", "--predict", "ch counter = 1")
    assert counter.ok("diagnose").splitlines() == [
        "DIAGNOSE | demo | NOT_FINISHED | paid 1/40 | 0 finding(s)",
        "NOTHING | the record shows no failure the layers name",
        "OPEN | the run is not over: these are the findings so far",
    ]
    counter.ok("act", "INC", "amount=2", "--predict", "ch counter = 3; win")
    assert counter.ok("diagnose").splitlines() == [
        "DIAGNOSE | demo | WIN | paid 2/40 | 0 finding(s)",
        "NOTHING | the record shows no failure the layers name",
    ]


def test_a_failure_points_at_the_layer_to_change(world: World) -> None:
    world.start(COUNTER / "adapter.py", _registry(world, budget={"actions": 8}))
    world.ok("state", "declare", "counter", "--path", "counter")
    world.ok("state", "declare", "lamp", "--path", "lamp")
    world.ok("state", "declare", "ghost", "--path", "no.such.key")
    # Layer 1: free text instead of the grammar, and predictions refused for their form.
    world.ok("act", "NOOP", "--predict", "something should happen")
    world.ok("act", "NOOP", "--predict", "surely now")
    for prediction in ("ch counter", "ch counter is 3", ""):
        world.refused("act", "NOOP", "--predict", prediction)
    # Layer 3: a reading that cannot be taken, actions refused at the boundary, the cap.
    world.ok("act", "NOOP", "--predict", "ch ghost = 1")
    for _ in range(3):
        world.refused("act", "INC", "amount=9", "--predict", "change")
    # Layer 4: a wrong model, a state never declared, a state never predicted, loose notes.
    world.ok("act", "INC", "amount=1", "--predict", "ch counter = 5; ch counter delta = 3")
    world.ok("act", "INC", "amount=1", "--predict", "ch counter = 9")
    world.ok("act", "NOOP", "--predict", "ch counter = 2; noop")
    world.refused("act", "NOOP", "--predict", "ch speed = 1")
    world.write(".assay/NOTES.md", "## Verified (cite event ids)\n- NOOP changes nothing (e6)\n- the lamp is decorative\n")
    world.ok("act", "NOOP", "--predict", "noop")
    world.ok("act", "NOOP", "--predict", "noop")
    world.refused("act", "NOOP", "--predict", "noop")
    report = world.ok("diagnose")
    lines = report.splitlines()
    assert lines[0] == "DIAGNOSE | demo | NOT_FINISHED | paid 8/8 | 11 finding(s)"
    layers = [line for line in lines if line.startswith("LAYER")]
    assert layers == [
        "LAYER 1 | the constitution | 2 finding(s)",
        "LAYER 2 | the modules | 2 finding(s)",
        "LAYER 3 | the registry and the adapter | 3 finding(s)",
        "LAYER 4 | the addressable states | 4 finding(s)",
    ]
    for expected in (
        "  - predictions written as free text: 2 of 10 graded outcomes were coerced to `change`",
        "  - predictions refused for their form: PREDICTION_SYNTAX 2, PREDICTION_REQUIRED 1",
        "  - a move that had just missed was re-issued unchanged and missed again: e2, e5",
        "  - actions taken 6 times or more on this progress unit that never changed the world: NOOP",
        "  - 1 action(s) with an outcome the code could not grade: e3; the first: ch ghost = 1: UNGRADABLE: "
        "key 'no' not in observation path 'no.such.key'",
        "  - actions refused at the world's boundary: ACTION_PARAMS 3",
        "  - the action budget ran out before the goal: paid 8 of 8",
        "  - world-model misses 3 of 7: `ch counter = V` after INC: 2 (e4, e5); `ch counter delta = V` after INC: 1 (e4)",
        "  - predictions named states that were never declared: 1 refusal(s)",
        "  - parts of the world the model does not address: declared and never predicted: lamp",
        "  - 1 Verified line(s) in the notes cite no held outcome: the lamp is decorative",
    ):
        assert expected in lines, expected
    assert all(line.startswith("    steer: ") for line in lines if "steer" in line)
    assert "OPEN |" not in report


def test_progress_predicted_and_not_made_points_at_the_model(counter: World) -> None:
    counter.ok("act", "INC", "amount=1", "--predict", "change; win")
    counter.ok("act", "NOOP", "--predict", "noop; level+1")
    report = counter.ok("diagnose")
    assert "LAYER 5 | the model | 1 finding(s)" in report
    assert "  - progress predictions missed 2 of 2 while the world model held: " in report
