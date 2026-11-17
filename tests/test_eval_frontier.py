"""The frontier reader and RSI-top's stall rule (tools/eval/frontier.py), as
a pure state machine driven by a fake clock: the one-hour gate, the attempt
and active-time thresholds, the 0.2 percent gain, what counts as active,
the phase after a fire and after the resume, the state file, the page's
stalled fraction with a pause, and the mode's summary. Nothing here starts
a process."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
frontier = importlib.import_module("tools.eval.frontier")
cli = importlib.import_module("tools.eval.cli")
EvalError = importlib.import_module("tools.eval").EvalError

T0 = 1_800_000_000.0
RULE = frontier.StallRule()


def _ev(n: int, at: float, cycles: int | None = 1000, *, valid: bool = True, **extra) -> object:
    return frontier.Evaluation(n=n, at=at, valid=valid, cycles=cycles if valid else None,
                               best_cycles=extra.pop("best", cycles), **extra)


def _feed(state, records, at, rule=RULE):
    return frontier.apply(state, frontier.Recorded(at, tuple(records)), rule)


def _flat(first_n: int, start: float, count: int, every: float, cycles: int = 1000) -> list:
    return [_ev(first_n + i, start + i * every, cycles) for i in range(count)]


def test_no_fire_before_the_first_hour_and_then_at_the_first_new_record():
    state = frontier.begin(T0, [])
    records = _flat(1, T0 + 60, 20, 60)
    state = _feed(state, records, T0 + 1260)
    assert state.anchor_cycles == 1000 and state.flat_anchor_at == T0 + 60
    assert state.phase == "ordinary" and state.interventions == ()
    # Past the hour, nothing fires without a new record.
    assert _feed(state, records, T0 + 4000) is state
    records += _flat(21, T0 + 3700, 1, 60)
    state = _feed(state, records, T0 + 3700)
    (fired,) = state.interventions
    assert state.phase == "intervening"
    assert (fired.index, fired.record_n, fired.anchor_cycles, fired.attempts) == (1, 21, 1000, 21)
    assert fired.active_seconds == 3640


def test_the_attempt_and_active_time_thresholds():
    start = T0 + 7200
    state = frontier.begin(T0, [])
    records = _flat(1, start, 11, 200)
    state = _feed(state, records, start + 2000)
    assert state.interventions == ()  # 2000 active seconds, 11 attempts
    records += [_ev(12, start + 2200)]
    assert len(_feed(state, records, start + 2200).interventions) == 1
    quick = _flat(1, start, 15, 10)
    assert _feed(frontier.begin(T0, []), quick, start + 140).interventions == ()  # 15 attempts, 140 seconds


def test_a_gain_of_exactly_the_threshold_does_not_move_the_anchor():
    state = _feed(frontier.begin(T0, []), [_ev(1, T0 + 10)], T0 + 10)
    assert (state.anchor_cycles, state.anchor_n, state.flat_anchor_at) == (1000, 1, T0 + 10)
    records = [_ev(1, T0 + 10), _ev(2, T0 + 20, 998)]
    state = _feed(state, records, T0 + 20)
    assert (state.anchor_cycles, state.flat_anchor_at) == (1000, T0 + 10)
    state = _feed(state, [*records, _ev(3, T0 + 30, 997)], T0 + 30)
    assert (state.anchor_cycles, state.anchor_n, state.flat_anchor_at) == (997, 3, T0 + 30)
    # Invalid records and invalid cycles never move it.
    state = _feed(state, [_ev(4, T0 + 40, valid=False)], T0 + 40)
    assert state.anchor_cycles == 997 and state.consumed == 4


def test_paused_and_between_session_time_is_not_active():
    rule = frontier.StallRule(min_run_age_seconds=0)
    state = frontier.begin(T0, [])
    records = _flat(1, T0 + 10, 12, 40)
    state = _feed(state, records, T0 + 500, rule)
    assert state.interventions == ()  # 490 active seconds
    for event in (frontier.Exited(T0 + 600), frontier.Paused(T0 + 600), frontier.Unpaused(T0 + 1800),
                  frontier.Launched(T0 + 3000)):
        state = frontier.apply(state, event, rule)
    assert state.sessions == ((T0, T0 + 600), (T0 + 3000, None)) and state.pauses == ((T0 + 600, T0 + 1800),)
    records += [_ev(13, T0 + 3500)]
    state = _feed(state, records, T0 + 3500, rule)
    assert state.interventions == ()  # 590 + 500 = 1090 active seconds
    records += [_ev(14, T0 + 3700)]
    state = _feed(state, records, T0 + 3700, rule)
    assert state.interventions[0].active_seconds == 1290


def test_after_a_fire_nothing_fires_until_the_resume_which_restarts_only_the_time_anchor():
    rule = frontier.StallRule(min_run_age_seconds=0)
    state = frontier.begin(T0, [])
    records = _flat(1, T0 + 100, 12, 110)
    state = _feed(state, records, T0 + 1310, rule)
    assert len(state.interventions) == 1 and state.flat_anchor_at == T0 + 100
    records += _flat(13, T0 + 1400, 30, 100)
    state = _feed(state, records, T0 + 4300, rule)
    assert len(state.interventions) == 1  # intervening: no second fire
    # An improvement while intervening moves the progress anchor, not the time anchor.
    records += [_ev(43, T0 + 4400, 900)]
    state = _feed(state, records, T0 + 4400, rule)
    assert (state.anchor_cycles, state.flat_anchor_at, state.phase) == (900, T0 + 100, "intervening")
    state = frontier.apply(state, frontier.Exited(T0 + 4500), rule)
    state = frontier.apply(state, frontier.Launched(T0 + 5000), rule)
    assert (state.phase, state.flat_anchor_at, state.anchor_cycles) == ("ordinary", T0 + 5000, 900)
    with pytest.raises(ValueError, match="no transition from ordinary on resumed"):
        frontier.next_phase("ordinary", "resumed")


def test_the_one_hour_gate_applies_once():
    state = frontier.begin(T0, [])
    records = _flat(1, T0 + 2400, 13, 100)
    state = _feed(state, records, T0 + 3600)
    assert len(state.interventions) == 1
    state = frontier.apply(state, frontier.Exited(T0 + 3610), RULE)
    state = frontier.apply(state, frontier.Launched(T0 + 3700), RULE)
    records += _flat(14, T0 + 3800, 12, 100)
    state = _feed(state, records, T0 + 4900)
    assert len(state.interventions) == 2 and state.interventions[1].fired_at - T0 - 3700 < 3600


def test_the_state_survives_a_save_and_a_load_and_a_killed_runner_is_closed(tmp_path):
    rule = frontier.StallRule(min_run_age_seconds=0)
    state = _feed(frontier.begin(T0, [_ev(1, T0 - 50, 1200)]), _flat(2, T0 + 10, 12, 110), T0 + 1330, rule)
    advice = {"directions": [{"rank": 1, "try": "x", "why": "y", "layer": 4}], "reason": None, "record": "a1.json"}
    state = frontier.apply(state, frontier.Advised(1, advice), rule)
    state = frontier.apply(state, frontier.Paused(T0 + 1400), rule)
    path = tmp_path / "stall.json"
    frontier.save_state(path, state)
    assert frontier.load_state(path) == state
    saved = json.loads(path.read_text())
    assert saved["phase"] == "intervening" and saved["sessions"][0][1] is None and saved["interventions"][0]["advice"] == advice
    assert frontier.has_open(state)
    closed = frontier.close_open(state, T0 + 1500)
    assert closed.sessions[-1] == (T0, T0 + 1500) and closed.pauses[-1] == (T0 + 1400, T0 + 1500)
    assert not frontier.has_open(closed)
    assert frontier.load_state(tmp_path / "none.json") is None
    path.write_text('{"version": 99}')
    with pytest.raises(EvalError, match="is not a stall state of version 1"):
        frontier.load_state(path)


def test_the_stalled_fraction_with_a_pause():
    records = [
        _ev(1, T0 + 100, 2000), _ev(2, T0 + 4000, 1990), _ev(3, T0 + 4600, 1980), _ev(4, T0 + 5200, 1970),
        _ev(5, T0 + 6000, 1969), _ev(6, T0 + 7000, 1900),
    ]
    assert frontier.best_curve(records)[:2] == [(T0 + 100, 2000), (T0 + 4000, 1990)]
    paused = frontier.stalled_fraction(records, T0, T0 + 10800, [(T0 + 8000, T0 + 8600)])
    # Stalled after the first hour: 3600-4000, then 5200-7000 (one cycle gained), then 7000 to the end, 600 s paused.
    assert paused == {"fraction": round(5400 / 6600, 4), "stalled_seconds": 5400.0, "window_seconds": 6600.0,
                      "paused_seconds": 600.0}
    assert frontier.stalled_fraction(records, T0, T0 + 10800, [])["fraction"] == round(6000 / 7200, 4)
    assert frontier.stalled_fraction(records, T0, T0 + 1800, [])["fraction"] is None


def test_the_reader_skips_a_torn_last_line_and_a_missing_file_is_empty(tmp_path):
    path = tmp_path / "frontier.jsonl"
    assert frontier.read_frontier(path) == []
    line = {"n": 1, "started_at": "2026-11-16T08:00:00+00:00", "at": "2026-11-16T08:00:30Z", "duration_seconds": 30.0,
            "label": "unroll", "code_sha256": "ab", "valid": True, "cycles": 1500, "failure_stage": None, "error": None,
            "frontier_before": 147734, "improved": True, "best_cycles": 1500, "trials": 3}
    path.write_text(json.dumps(line) + "\n" + json.dumps({**line, "n": 2})[:40])
    (record,) = frontier.read_frontier(path)
    assert (record.n, record.label, record.cycles, record.best_cycles, record.frontier_before, record.trials) == (
        1, "unroll", 1500, 1500, 147734, 3)
    assert record.at - record.started_at == 30.0


def test_the_mode_prints_the_frontier_and_the_interventions(tmp_path, capsys):
    run = tmp_path.resolve() / "run"
    (run / ".assay" / "world_state").mkdir(parents=True)
    rows = [{"n": n, "at": frontier.iso(T0 + 60 * n), "valid": True, "cycles": cycles, "best_cycles": best,
             "label": f"try {n}", "improved": cycles == best} for n, cycles, best in
            [(1, 2726, 2726), (2, 2726, 2726), (3, 1363, 1363)]]
    frontier.frontier_file(run).write_text("".join(json.dumps(row) + "\n" for row in rows))
    state = frontier.begin(T0, [])
    state = frontier.apply(state, frontier.Exited(T0 + 200), RULE)
    frontier.save_state(run.with_name("run.sessions") / "stall.json", state)
    assert cli.main(["frontier", "--run-dir", str(run)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "FRONTIER | run | 3 evaluation(s), 3 valid | best 1363 cycles | normalized 1.0 | 54.0 per hour"
    assert out[1] == "STALLED | n/a of 0.0 min after the first hour | paused 0.0 min"
    assert cli.main(["frontier", "--run-dir", str(run), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["best_cycles"] == 1363
    assert cli.main(["frontier", "--run-dir", str(tmp_path / "none")]) == 2
