"""The journal meters live once (`assay.meters`) and the two surfaces that
print them, the status lines and the module advisories, read the same
counts. The sharpness counts are held to the two formulas they replaced:
the CLAIMS line's, kept byte for byte on the published runs, and the
sharpness module's, which reads the agent's own claims."""

from __future__ import annotations

from conftest import event_of


def _graded(index: int, *grades: dict, **overrides) -> object:
    return event_of(id=index, predict_ok=all(item.get("ok", True) for item in grades), grade=list(grades), **overrides)


def test_unit_walk_and_paid_count():
    from assay.meters import level_action_count, unit_indices

    events = [
        event_of(id=0, action="START", counts_action=False, level_before=None),
        event_of(id=1, levels_completed=0),
        event_of(id=2, levels_completed=1, level_before=0),
        event_of(id=3, levels_completed=1, action="NOOP"),
        event_of(id=4, levels_completed=1, action="RESET", counts_action=True),
    ]
    assert unit_indices(events) == [2, 3, 4]
    assert level_action_count(events) == 3
    assert unit_indices([]) == [] and level_action_count([]) == 0


def test_recent_predictions_window():
    from assay.meters import recent_predictions

    events = [event_of(id=0, action="START", counts_action=False, level_before=None)]
    events += [event_of(id=index, predict_ok=index % 3 != 0) for index in range(1, 14)]
    events.append(event_of(id=14, predict_ok=None))
    hits, total = recent_predictions(events)
    assert (hits, total) == (7, 10)
    assert recent_predictions(events, window=3) == (2, 3)
    assert recent_predictions(events[:1]) == (0, 0)


def _claims_line_formula(events) -> tuple[int, int]:
    """The CLAIMS line's sharpness as inspect.py computed it before #24."""
    graded_total = coerced = 0
    for event in events:
        for item in event.grade:
            if item.kind == "note":
                continue
            graded_total += 1
            if item.invalid or item.ungradable:
                continue
            if item.kind == "coerced":
                coerced += 1
    return graded_total - coerced, graded_total


def _module_formula(events) -> tuple[int, int]:
    """The sharpness module's ratio as modules.py computed it before #24."""
    graded = coerced = 0
    for event in events:
        for item in event.grade:
            if item.kind == "note" or item.machine:
                continue
            graded += 1
            if item.kind == "coerced":
                coerced += 1
    return graded - coerced, graded


def test_sharpness_counts_reproduce_both_formulas():
    from assay.meters import sharpness

    events = [event_of(id=0, action="START", counts_action=False, level_before=None)]
    for index in range(1, 12):
        events.append(_graded(index, {"kind": "coerced", "ok": True, "coerced": True}))
    for index in range(12, 20):
        events.append(_graded(index, {"kind": "noop", "ok": True}, {"kind": "note", "ok": True}))
    events.append(_graded(20, {"kind": "verify", "ok": False, "invalid": True}))
    for index in range(21, 30):
        events.append(_graded(index, {"kind": "plan_step", "ok": True, "machine": True}))
    counts = sharpness(events)
    assert (counts.graded, counts.coerced, counts.machine) == (29, 11, 9)
    assert (counts.sharp, counts.graded) == _claims_line_formula(events) == (18, 29)
    assert (counts.agent_graded - counts.coerced, counts.agent_graded) == _module_formula(events) == (9, 20)
    assert sharpness([]) == sharpness(events[:1])
    assert sharpness([]).graded == 0


def test_claims_line_and_advisory_read_the_same_counts(paths):
    from conftest import run_of

    from assay.inspect import _claim_meter_lines
    from assay.modules import BUILTINS, ModuleView

    events = [event_of(id=0, action="START", counts_action=False, level_before=None)]
    for index in range(1, 12):
        events.append(_graded(index, {"kind": "coerced", "ok": True, "coerced": True}))
    for index in range(12, 21):
        events.append(_graded(index, {"kind": "noop", "ok": True}))
    for index in range(21, 41):
        events.append(_graded(index, {"kind": "plan_step", "ok": True, "machine": True}))
    run = run_of(paths, events, registry={"actions": []})
    assert _claim_meter_lines(run)[0] == (
        "CLAIMS | world-model misses 0/9 (0.0%) | gamble misses 0/0 | "
        "sharpness 29/40 (72%) | invalid 0"
    )
    module = next(item for item in BUILTINS if item.NAME == "sharpness")
    # Twenty of the agent's own claims, eleven coerced: the advisory fires on
    # the agent's ratio, and the machine predictions do not dilute it.
    assert module.trigger(ModuleView(run), None) == (
        "sharpness is 9/20: over half your claims are coerced free text; "
        "they earn nothing. State checkable claims."
    )


def test_module_modes_have_one_home():
    from assay import modules, registry

    assert registry.MODULE_MODES == ("off", "advise", "block")
    assert modules.MODULE_MODES is registry.MODULE_MODES
