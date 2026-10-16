"""The watch and diagnose modes of tools/eval (watch.py, diagnose.py), behind
`/assay:watch` and `/assay:diagnose`.

On fixture journals: each layer's finding comes from its symptom and from
nothing else, a clean win shows none, an unwon run with nothing above falls
to layer 5, and the watch line says where the run stands. End to end, a run
through the real CLI and daemon with a repeated miss, an ungraded state and
a declared stop over untested rules is diagnosed at layers 1, 2 and 4.
Nothing here starts a model."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
report = importlib.import_module("tools.eval.report")
diagnose = importlib.import_module("tools.eval.diagnose")
watch = importlib.import_module("tools.eval.watch")

COUNTER_WORLD = REPO / "examples" / "counter_world.py"
COUNTER_REGISTRY = REPO / "examples" / "example_registry.json"
STAMP = "2026-10-16T08:00:00+00:00"
ACTIONS = ["INC", "SET_LAMP", "NOOP", "BOMB"]


def _grade(text: str, kind: str, ok: bool, actual: str = "", **extra: object) -> dict:
    bucket = "gamble" if kind in ("win", "level_up") else "world_model"
    return {"text": text, "kind": kind, "ok": ok, "actual": actual, "bucket": bucket, **extra}


def _event(action: str, *grades: dict, state: str = "NOT_FINISHED", done: int = 0, paid: bool = True,
           observation: dict | None = None) -> dict:
    event = {"timestamp": STAMP, "action": action, "data": None, "counts_action": paid, "state": state,
             "levels_completed": done, "level_before": 0 if paid else None, "win_levels": 1,
             "available_actions": ACTIONS, "note": "", "observation": observation or {"counter": 0}}
    if grades:
        event.update(predict="; ".join(str(g["text"]) for g in grades), predict_ok=all(g["ok"] for g in grades),
                     grade=list(grades))
    return event


def _run(root: Path, events: list[dict], *, notes: str | None = None, activity: list[dict] | None = None,
         channels: dict | None = None) -> Path:
    run = root.resolve() / "run"
    store = run / ".assay"
    store.mkdir(parents=True)
    (store / "events.jsonl").write_text("".join(json.dumps({"id": i, **e}) + "\n" for i, e in enumerate(events)))
    (store / "registry.json").write_text(json.dumps({"actions": [], "budget": {"actions": 40}}))
    (store / "config.json").write_text(json.dumps({"game_id": "fixture"}))
    (store / "activity.jsonl").write_text("".join(json.dumps(r) + "\n" for r in activity or []))
    if notes is not None:
        (store / "NOTES.md").write_text(notes)
    if channels is not None:
        (store / "channels.json").write_text(json.dumps(channels))
    return run


START = _event("START", paid=False)


def _layers(run: Path) -> list[tuple[int, str]]:
    return [(item["layer"], item["what"]) for item in diagnose.diagnose(report.build(run))]


def test_each_layer_finds_its_own_symptom(tmp_path):
    changed = {"counter": 1}
    won = [START, _event("INC", _grade("ch counter = 1", "channel_eq", True, channel="counter"),
                         _grade("win", "win", True), state="WIN", done=1, observation=changed)]
    assert _layers(_run(tmp_path / "won", won)) == []
    # Layer 3: an outcome the code could not grade, and refusals of form.
    ungraded = [START, _event("INC", _grade("ch tick = 1", "channel_eq", False, "UNGRADABLE: no tick", ungradable=True,
                                            channel="tick"), _grade("change", "change", True), observation=changed)]
    refusals = [{"kind": "command_end", "code": "ACTION_PARAMS", "error": "x"}] * 3
    assert [layer for layer, _ in _layers(_run(tmp_path / "three", ungraded, activity=refusals))].count(3) == 2
    # Layer 4: only `change`, a state never graded, a notes line without a citation.
    vague = [START, *[_event("NOOP", _grade("change", "change", True), observation={"counter": i}) for i in range(1, 4)]]
    layers = _layers(_run(tmp_path / "four", vague, notes="## Verified\n- NOOP moves the counter\n",
                          channels={"counter": {"form": "path", "path": "counter"}}))
    assert [layer for layer, _ in layers] == [4, 4, 4]
    assert any("never graded" in what for _, what in layers)
    # Layer 2: a form missed three times and never held.
    loop = [START, *[_event("NOOP", _grade("ch counter = 5", "channel_eq", False, "ch counter read 0", channel="counter"))
                     for _ in range(3)]]
    assert (2, "ch counter = V after NOOP missed 3 times and never held") in _layers(_run(tmp_path / "two", loop))
    # Layer 1: a declared stop over an untried action, and missed milestones.
    stopped = [START, _event("INC", _grade("ch counter = 1", "channel_eq", True, channel="counter"),
                             _grade("win", "win", False), observation=changed),
               _event("INC", _grade("ch counter = 2", "channel_eq", True, channel="counter"),
                      _grade("win", "win", False), observation={"counter": 2})]
    layers = _layers(_run(tmp_path / "one", stopped, notes="STOP: the counter cannot reach 3\n"))
    assert (1, "a declared stop that rests on untested rules") in layers
    assert (1, "milestone predictions missed 2 of 2") in layers
    # Layer 5: not won, nothing above.
    plain = [START, _event("INC", _grade("ch counter = 1", "channel_eq", True, channel="counter"), observation=changed)]
    assert _layers(_run(tmp_path / "five", plain)) == [(5, "the run is not won and the layers above show nothing")]


def test_the_findings_print_by_layer_with_their_steer(tmp_path, capsys):
    loop = [START, *[_event("NOOP", _grade("ch counter = 5", "channel_eq", False, "ch counter read 0", channel="counter"))
                     for _ in range(3)]]
    run = _run(tmp_path, loop)
    assert diagnose.main(["--run-dir", str(run)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("DIAGNOSE | run | UNFINISHED | paid 3/40 | ")
    assert "LAYER 2 | the modules | 1 finding(s)\n  - ch counter = V after NOOP missed 3 times and never held: e1-e3" in out
    assert '{"miss_streak": "block", "coverage_audit": "block"}' in out
    assert diagnose.main(["--run-dir", str(run), "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["outcome"] == "UNFINISHED" and {item["layer"] for item in record["findings"]} == {2}
    assert diagnose.main(["--run-dir", str(tmp_path / "nothing")]) == 2


def test_the_watch_line_and_its_end(tmp_path, capsys):
    events = [START, _event("NOOP", _grade("ch counter = 5", "channel_eq", False, "ch counter read 0", channel="counter")),
              _event("INC", _grade("win", "win", True), state="WIN", done=1, observation={"counter": 3})]
    run = _run(tmp_path, events, activity=[{"kind": "spend_report", "id": "s1", "usd": 0.25, "tokens": 9}])
    record = watch.snapshot(run)
    assert watch.line(record) == "no runner | WIN | paid 2/40 | held 1 missed 1 | last e2 INC held | $0.25"
    assert watch.ended(record) is True
    assert watch.last_action(run) == "e2 INC held"
    assert watch.main(["--run-dir", str(run), "--until-end", "--every", "0.1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("WATCH | ") and lines[0].endswith("| no runner | WIN | paid 2/40 | "
                                                                                     "held 1 missed 1 | last e2 INC held | $0.25")
    unfinished = _run(tmp_path / "open", events[:2])
    assert watch.last_action(unfinished) == "e1 NOOP missed: ch counter = 5 (ch counter read 0)"
    assert watch.main(["--run-dir", str(unfinished), "--once"]) == 0
    assert watch.main(["--run-dir", str(unfinished), "--every", "0"]) == 2


def test_a_real_run_is_diagnosed_at_layers_1_2_and_4(tmp_path):
    run = tmp_path.resolve() / "counter"
    run.mkdir()
    try:
        started = run_cli(run, "start", "counterdemo", "--adapter", f"{COUNTER_WORLD}:factory", "--registry",
                          str(COUNTER_REGISTRY), "--owner-token-file", str(tmp_path / "counter.token"))
        assert started.returncode == 0, started.stderr
        for name in ("counter", "lamp"):
            assert run_cli(run, "state", "declare", name, "--path", name).returncode == 0
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "ch counter delta = 1").returncode == 0
        for _ in range(3):
            assert run_cli(run, "act", "NOOP", "--predict", "ch counter = 5").returncode == 0
        notes = run / ".assay" / "NOTES.md"
        notes.write_text(notes.read_text() + "\nSTOP: the lamp cannot be lit\n")
    finally:
        stop_run(run)
    done = subprocess.run([sys.executable, "-m", "tools.eval", "diagnose", "--run-dir", str(run), "--json"],
                          cwd=REPO, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)["findings"]
    assert {item["layer"] for item in found} == {1, 2, 4}
    assert any(item["layer"] == 1 and "BOMB" in item["evidence"] and "lamp" in item["evidence"] for item in found)
    watched = subprocess.run([sys.executable, "-m", "tools.eval", "watch", "--run-dir", str(run), "--once"],
                             cwd=REPO, capture_output=True, text=True, timeout=120)
    assert watched.returncode == 0 and "| SELF_STOP | paid 4/40 | held 1 missed 3 | last e4 NOOP missed: ch counter = 5" in watched.stdout
