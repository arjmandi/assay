"""The analyst of tools/eval (analyst.py): the bundle it reads from a fixture
run, the output shapes it parses, a failing, a timing-out and a garbage
analyst each recorded with no directions, and the failed directions carried
until a meaningful improvement clears them. The analyst here is a Python
one-liner; nothing starts a model."""

from __future__ import annotations

import importlib
import json
import shlex
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
analyst = importlib.import_module("tools.eval.analyst")
frontier = importlib.import_module("tools.eval.frontier")
EvalError = importlib.import_module("tools.eval").EvalError

T0 = 1_800_000_000.0
STAMP = "2026-11-16T08:00:00+00:00"
RULE = frontier.StallRule(min_run_age_seconds=0, flat_seconds=100, min_attempts=3)
DIRECTION = {"rank": 1, "try": "fuse the two loads into one vector load", "why": "evaluations 4-9 sat at 1500", "layer": 4}


def _ev(n: int, at: float, cycles: int = 1500, **extra) -> object:
    return frontier.Evaluation(n=n, at=at, valid=True, cycles=cycles, best_cycles=cycles, label=f"try {n}", **extra)


def _run(root: Path) -> Path:
    """A run whose journal holds a held and two missed predictions, two
    declared states and notes."""
    run = root.resolve() / "run"
    store = run / ".assay"
    store.mkdir(parents=True)
    grade = {"text": "ch counter = 5", "kind": "channel_eq", "channel": "counter", "bucket": "world_model"}
    events = [{"id": 0, "timestamp": STAMP, "action": "START", "counts_action": False, "state": "NOT_FINISHED",
               "levels_completed": 0, "win_levels": 1, "observation": {"counter": 0}}]
    for index, ok in enumerate([True, False, False], 1):
        events.append({
            "id": index, "timestamp": STAMP, "action": "INC", "data": {"amount": 1}, "counts_action": True,
            "state": "NOT_FINISHED", "levels_completed": 0, "win_levels": 1, "observation": {"counter": index},
            "predict": grade["text"], "predict_ok": ok,
            "grade": [{**grade, "ok": ok, "actual": "" if ok else f"ch counter read {index}"}],
        })
    (store / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    (store / "registry.json").write_text(json.dumps({"actions": [], "budget": {"actions": 40}}))
    (store / "config.json").write_text(json.dumps({"game_id": "fixture"}))
    (store / "channels.json").write_text(json.dumps({"counter": {"form": "path", "path": "counter"}}))
    (store / "NOTES.md").write_text("# Notes\n" + "x" * 20000 + "\nthe last line\n")
    return run


def _stalled_state(records: list) -> object:
    state = frontier.begin(T0, [])
    return frontier.apply(state, frontier.Recorded(records[-1].at, tuple(records)), RULE)


def test_the_bundle_from_a_fixture_run(tmp_path):
    run = _run(tmp_path)
    records = [_ev(1, T0 + 10, 1600, improved=True), *[_ev(n, T0 + 10 + 50 * n, failure_stage=None) for n in range(2, 6)],
               frontier.Evaluation(n=6, at=T0 + 400, valid=False, cycles=None, best_cycles=1500, failure_stage="tests")]
    state = _stalled_state(records)
    (intervention,) = state.interventions
    data = analyst.bundle(run, sessions_dir=tmp_path / "run.sessions", records=records, state=state, intervention=intervention)
    assert data["intervention"]["after_evaluation"] == intervention.record_n
    findings = data["diagnosis"]["findings"]
    assert data["diagnosis"]["outcome"] == "UNFINISHED" and all(item["layer_name"] for item in findings)
    model = data["world_model"]
    form = model["actions"]["INC"]["forms"]["ch counter = V"]
    assert (form["held"], form["missed"]) == (1, 2)
    assert [miss["actual"] for miss in form["latest_misses"]] == ["ch counter read 2", "ch counter read 3"]
    assert model["meter"]["world_model"] == {"graded": 3, "missed": 2}
    assert model["declared_states"] == {"counter": {"form": "path", "path": "counter"}}
    assert model["notes"].startswith("[earlier notes cut]") and model["notes"].endswith("the last line\n")
    assert "states_never_graded" in model["coverage"]
    view = data["frontier"]
    assert (view["best_cycles"], view["anchor_cycles"], view["evaluations"], view["valid"]) == (1500, 1500, 6, 5)
    assert view["failure_stages"] == {"none": 5, "tests": 1}
    assert [row["n"] for row in view["improvements"]] == [1] and view["recent"][-1]["failure_stage"] == "tests"
    assert data["failed_directions"] == []
    # A run the report cannot read still gets its frontier.
    broken = analyst.bundle(tmp_path / "nothing", sessions_dir=tmp_path, records=records, state=state,
                            intervention=intervention)
    assert broken["diagnosis"]["error"].startswith("the report could not be built: EvalError: no journal in ")
    assert broken["frontier"]["evaluations"] == 6


def test_failed_directions_are_carried_and_cleared_by_an_improvement():
    records = [_ev(n, T0 + 50 * n) for n in range(1, 5)]
    state = _stalled_state(records)
    advice = {"directions": [DIRECTION], "reason": None, "record": "a1.json"}
    state = frontier.apply(state, frontier.Advised(1, advice), RULE)
    state = frontier.apply(state, frontier.Exited(T0 + 300), RULE)
    state = frontier.apply(state, frontier.Launched(T0 + 400), RULE)
    records += [_ev(n, T0 + 400 + 50 * (n - 4)) for n in range(5, 9)]
    state = frontier.apply(state, frontier.Recorded(T0 + 600, tuple(records)), RULE)
    assert len(state.interventions) == 2
    (failed,) = analyst.failed_directions(records, state)
    assert failed["call"] == 1 and failed["directions"] == [DIRECTION]
    assert failed["evaluations_after"] == ["try 5", "try 6", "try 7", "try 8"]
    assert frontier.standing_advice(state).index == 1
    records += [_ev(9, T0 + 650, 1400)]
    state = frontier.apply(state, frontier.Recorded(T0 + 650, tuple(records)), RULE)
    assert analyst.failed_directions(records, state) == [] and frontier.standing_advice(state) is None


@pytest.mark.parametrize("stdout", [
    json.dumps({"directions": [DIRECTION]}),
    json.dumps({"type": "result", "result": "Here they are.\n```json\n" + json.dumps({"directions": [DIRECTION]}) + "\n```",
                "total_cost_usd": 0.4}),
    "\n".join(json.dumps(line) for line in [
        {"type": "thread.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"directions": [DIRECTION]})}},
    ]),
    "\n".join(json.dumps(line) for line in [
        {"type": "text", "part": {"type": "text", "text": "```json\n" + json.dumps({"directions": [DIRECTION]}, indent=2) + "\n```"}},
        {"type": "step_finish", "part": {"type": "step-finish", "cost": 0.1, "tokens": {"input": 5, "output": 2}}},
    ]),
    "I read the run.\n\n```json\n" + json.dumps({"directions": [DIRECTION]}, indent=2) + "\n```\nGood luck.",
])
def test_each_output_shape_parses(stdout):
    assert analyst.parse(stdout) == ([DIRECTION], None)


def test_directions_are_validated_ranked_and_capped():
    items = [
        {"rank": 3, "try": "c", "why": "w", "layer": 2}, {"rank": 1, "try": "a", "why": "w", "layer": 1},
        {"rank": 2, "try": "b", "why": "w", "layer": 6}, {"rank": 2, "try": " ", "why": "w", "layer": 3},
        {"rank": 4, "try": "d", "why": "w" * 2001, "layer": 3}, {"try": "e", "why": "w", "layer": True},
        *[{"rank": 5 + i, "try": f"t{i}", "why": "w", "layer": 5} for i in range(6)],
    ]
    directions = analyst.directions_of({"directions": items})
    assert [(item["rank"], item["try"]) for item in directions] == [(1, "a"), (2, "c"), (3, "t0"), (4, "t1"), (5, "t2")]
    assert analyst.parse("no json here") == ([], "the output holds no JSON object with directions")
    assert analyst.parse('{"directions": [{"try": "x"}]}')[1] == (
        "no direction in the output has a try, a why and a layer from 1 to 5")


def _config(tmp_path: Path, code: str, **settings) -> object:
    script = tmp_path / "analyst_script.py"
    script.write_text(code)
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{prompt_file}}"
    return analyst.AnalystConfig.from_command(command, **settings)


def _call(config, directory: Path, timeout: float = 30.0) -> dict:
    return analyst.call(config, index=1, fired_at=T0, data={"frontier": {"best_cycles": 1500}}, directory=directory,
                        env={"PATH": "/usr/bin:/bin"}, timeout_seconds=timeout, version="test")


def test_a_good_a_failing_a_timing_out_and_a_garbage_analyst(tmp_path):
    report = {"type": "result", "result": json.dumps({"directions": [DIRECTION]}), "total_cost_usd": 0.4,
              "usage": {"input_tokens": 10, "output_tokens": 5}}
    good = _config(tmp_path, f"import sys\nassert '1500' in open(sys.argv[1]).read()\nprint({json.dumps(report)!r})\n")
    record = _call(good, tmp_path / "good")
    assert record["directions"] == [DIRECTION] and record["reason"] is None and record["exit_code"] == 0
    assert record["dollars"] == 0.4 and record["tokens"]["total"] == 15 and record["host"] == "analyst_script.py"
    assert record["command"].endswith(f"{tmp_path / 'good' / 'a1.prompt.md'} < {tmp_path / 'good' / 'a1.prompt.md'}")
    for name in ("a1.input.json", "a1.prompt.md", "a1.out", "a1.err"):
        assert (tmp_path / "good" / name).is_file()
    assert json.loads((tmp_path / "good" / "a1.json").read_text()) == record
    assert '"best_cycles": 1500' in (tmp_path / "good" / "a1.prompt.md").read_text()

    failing = _call(_config(tmp_path, "import sys\nprint('quota', file=sys.stderr)\nsys.exit(3)\n"), tmp_path / "failing")
    assert (failing["directions"], failing["reason"], failing["exit_code"]) == ([], "exit 3: quota", 3)
    slow = _call(_config(tmp_path, "import time\ntime.sleep(30)\n"), tmp_path / "slow", timeout=0.5)
    assert slow["directions"] == [] and slow["timed_out"] is True and slow["reason"] == "timed out after 0.5 s"
    garbage = _call(_config(tmp_path, "print('I think you should try harder')\n"), tmp_path / "garbage")
    assert garbage["directions"] == [] and garbage["reason"] == "the output holds no JSON object with directions"
    for directory in ("failing", "slow", "garbage"):
        assert json.loads((tmp_path / directory / "a1.json").read_text())["directions"] == []


def test_the_configuration_is_refused_before_any_call(tmp_path):
    with pytest.raises(EvalError, match=r"--analyst-command names \{run_dir\}"):
        analyst.AnalystConfig.from_command("cat {run_dir}")
    with pytest.raises(EvalError, match="--analyst gemini is not one of claude, codex, opencode"):
        analyst.AnalystConfig.from_host("gemini")
    claude = analyst.AnalystConfig.from_host("claude", model="opus")
    assert claude.argv("PROMPT", tmp_path / "p.md")[:5] == ["claude", "-p", "PROMPT", "--model", "opus"]
    prompt = tmp_path / "prompt.md"
    prompt.write_text("no placeholder")
    with pytest.raises(EvalError, match="has no {{INPUT}} placeholder"):
        analyst.AnalystConfig.from_host("claude", prompt=prompt).template_text()
    assert "{{INPUT}}" in analyst.PROMPT.read_text()
    with pytest.raises(EvalError, match="the analyst's command `no-such-analyst` is not found"):
        analyst.AnalystConfig.from_command("no-such-analyst {prompt}").version()
