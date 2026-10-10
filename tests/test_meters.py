"""The journal meters live once (`assay.meters`) and the two surfaces that
print them, the status lines and the module advisories, read the same
counts. The specificity count is held to the PREDICTIONS line's formula, kept
byte for byte on the published runs; the module reads the same count over
the same grades, so its advisory's N/M is the PREDICTIONS line's."""

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


def _predictions_line_formula(events) -> tuple[int, int]:
    """The PREDICTIONS line's specificity as inspect.py computed it before #24,
    under the meter's old name, sharpness."""
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


def test_specificity_counts_reproduce_the_predictions_line_formula():
    from assay.meters import specificity

    events = [event_of(id=0, action="START", counts_action=False, level_before=None)]
    for index in range(1, 12):
        events.append(_graded(index, {"kind": "coerced", "ok": True, "coerced": True}))
    for index in range(12, 20):
        events.append(_graded(index, {"kind": "noop", "ok": True}, {"kind": "note", "ok": True}))
    events.append(_graded(20, {"kind": "verify", "ok": False, "invalid": True}))
    for index in range(21, 30):
        events.append(_graded(index, {"kind": "plan_step", "ok": True, "machine": True}))
    counts = specificity(events)
    # The nine machine predictions count among the graded, as on the line.
    assert (counts.graded, counts.coerced) == (29, 11)
    assert (counts.specific, counts.graded) == _predictions_line_formula(events) == (18, 29)
    assert specificity([]) == specificity(events[:1])
    assert specificity([]).graded == 0


def test_predictions_line_and_advisory_read_the_same_counts(paths):
    from conftest import run_of

    from assay.inspect import _prediction_meter_lines
    from assay.modules import BUILTINS, ModuleView

    module = next(item for item in BUILTINS if item.NAME == "specificity")
    events = [event_of(id=0, action="START", counts_action=False, level_before=None)]
    for index in range(1, 23):
        events.append(_graded(index, {"kind": "coerced", "ok": True, "coerced": True}))
    for index in range(23, 31):
        events.append(_graded(index, {"kind": "noop", "ok": True}))
    for index in range(31, 41):
        events.append(_graded(index, {"kind": "plan_step", "ok": True, "machine": True}))
    run = run_of(paths, events, registry={"actions": []})
    # Forty grades, twenty-two coerced, ten of them machine predictions: the
    # advisory carries the N/M the PREDICTIONS line prints, over the same grades.
    assert _prediction_meter_lines(run)[0] == (
        "PREDICTIONS | world-model misses 0/8 (0.0%) | gamble misses 0/0 | "
        "specificity 18/40 (45%) | invalid 0"
    )
    assert module.trigger(ModuleView(run), None) == (
        "specificity is 18/40: over half the graded outcomes are coerced free text; "
        "they earn nothing. State checkable outcomes."
    )
    # Eleven coerced of forty, twenty of them machine predictions: before #25
    # the module read 9/20 over the agent's own outcomes and fired; it reads
    # the PREDICTIONS line's 29/40 now and is silent.
    diluted = events[:12]
    diluted += [_graded(index, {"kind": "noop", "ok": True}) for index in range(12, 21)]
    diluted += [
        _graded(index, {"kind": "plan_step", "ok": True, "machine": True}) for index in range(21, 41)
    ]
    run = run_of(paths, diluted, registry={"actions": []})
    assert _prediction_meter_lines(run)[0] == (
        "PREDICTIONS | world-model misses 0/9 (0.0%) | gamble misses 0/0 | "
        "specificity 29/40 (72%) | invalid 0"
    )
    assert module.trigger(ModuleView(run), None) is None


def test_module_modes_have_one_home():
    from assay import modules, registry

    assert registry.MODULE_MODES == ("off", "advise", "block")
    assert modules.MODULE_MODES is registry.MODULE_MODES
