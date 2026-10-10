"""Addressable states first-class in code: budget_remaining grades, status
shows every state's reading without spawning an extractor, `state list
--read` computes fresh, receipts show path states that changed, and the
retired command word `channel` still answers for one release."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
    {"name": "NOOP", "params": {}},
]


def _prepare(run: Path, **extra) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, **extra}))


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_budget_remaining_grades(tmp_path):
    run = tmp_path / "budget"
    _prepare(run, budget={"actions": 10})
    try:
        assert _start(run).returncode == 0
        held = run_cli(run, "act", "NOOP", "--predict", "ch budget_remaining = 9")
        assert held.returncode == 0 and "RESULT | PREDICTED" in held.stdout, held.stdout
        moved = run_cli(run, "act", "NOOP", "--predict", "ch budget_remaining delta = -1")
        assert moved.returncode == 0 and "RESULT | PREDICTED" in moved.stdout, moved.stdout
        missed = run_cli(run, "act", "NOOP", "--predict", "ch budget_remaining = 99")
        assert missed.returncode == 0 and "RESULT | SURPRISE" in missed.stdout
        assert "ch budget_remaining = 7" in missed.stdout
        grade = _events(run)[-1]["grade"][0]
        assert grade["bucket"] == "world_model" and grade["ok"] is False
    finally:
        stop_run(run)
    run2 = tmp_path / "nocap"
    _prepare(run2)
    try:
        assert _start(run2).returncode == 0
        ungradable = run_cli(run2, "act", "NOOP", "--predict", "ch budget_remaining = 1")
        assert ungradable.returncode == 0 and "RESULT | INVALID_CLAIM" in ungradable.stdout
        assert "needs a registered action cap" in ungradable.stdout
    finally:
        stop_run(run2)


EXTRACTOR = """\
def extract(obs):
    return obs["data"]["counter"] * 10 + (1 if obs["data"]["lamp"] == "on" else 0)
"""


def test_status_shows_readings_without_spawning_extractors(tmp_path):
    """Why the count goes through the cache: an extractor leaves no trace
    outside its scratch directory (the sandbox refuses the marker file the
    old test had it write), so its runs are counted through what the daemon
    caches at grade time in `.assay/channel_readings.json`. Why 20 against
    21: the extractor reads counter times ten plus one when the lamp is on,
    so after the graded act the cache holds 20 at e1, and once the lamp is
    on a fresh run can only read 21; a status line that says 20 came from
    the cache, a `--read` line that says 21 came from a spawn. What this can
    no longer catch: a status that spawned the extractor and discarded the
    reading, which the marker file counted and the cache cannot see; the
    code path (`state_lines` without `fresh`) never calls an extractor."""
    run = tmp_path / "readings"
    _prepare(run, budget={"actions": 20})
    (run / "tens.py").write_text(EXTRACTOR)
    cache = run / ".assay" / "channel_readings.json"

    def graded_readings() -> dict:
        return json.loads(cache.read_text()) if cache.exists() else {}

    try:
        assert _start(run).returncode == 0
        status = run_cli(run, "status")
        assert "STATES | registered: goal · level · budget_remaining" in status.stdout
        assert "STATES | host: goal=false · level=0 · budget_remaining=20" in status.stdout
        assert run_cli(run, "state", "declare", "counter", "--path", "counter").returncode == 0
        assert run_cli(run, "state", "declare", "tens", "--file", "tens.py").returncode == 0
        status = run_cli(run, "status")
        assert "counter=0 (path)" in status.stdout
        assert "tens=not yet graded (extractor)" in status.stdout
        assert graded_readings() == {}  # no extractor has run
        # A graded outcome runs the extractor in the daemon and caches the reading.
        acted = run_cli(run, "act", "INC", "amount=2", "--predict", "ch tens = 20")
        assert acted.returncode == 0 and "RESULT | PREDICTED" in acted.stdout, acted.stdout
        assert graded_readings() == {"tens": {"event": 1, "value": 20}}
        # Change the world without a tens outcome: a fresh run would now read
        # 21 while the cache still says 20 at e1, so the two are told apart.
        moved = run_cli(run, "act", "SET_LAMP", "state=on", "--predict", "change")
        assert moved.returncode == 0 and "RESULT | PREDICTED" in moved.stdout, moved.stdout
        assert graded_readings() == {"tens": {"event": 1, "value": 20}}  # no extractor ran
        status = run_cli(run, "status")
        assert "counter=2 (path)" in status.stdout
        assert "tens=20 @e1 (extractor, last graded)" in status.stdout  # cached; a spawn would show 21
        listed = run_cli(run, "state", "list")
        assert "tens=20 @e1" in listed.stdout
        assert graded_readings() == {"tens": {"event": 1, "value": 20}}  # status and list spawned nothing
        fresh = run_cli(run, "state", "list", "--read")
        assert fresh.returncode == 0, fresh.stderr
        assert "tens=21 (extractor)" in fresh.stdout  # computed fresh, one spawn
        assert graded_readings() == {"tens": {"event": 1, "value": 20}}  # the CLI never writes the cache
    finally:
        stop_run(run)


def test_receipts_show_path_states_that_changed(tmp_path):
    run = tmp_path / "receipt"
    _prepare(run, budget={"actions": 20})
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "state", "declare", "counter", "--path", "counter").returncode == 0
        assert run_cli(run, "state", "declare", "lamp", "--path", "lamp").returncode == 0
        acted = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert acted.returncode == 0
        assert "STATES | counter: 0 -> 1" in acted.stdout
        assert "STATES | lamp:" not in acted.stdout
        quiet = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert quiet.returncode == 0 and "STATES |" not in quiet.stdout
        batched = run_cli(
            run, "commit", "--step", "INC amount=1 :: change", "--step", "SET_LAMP state=on :: change",
        )
        assert batched.returncode == 0, batched.stderr
        assert "STATES | counter: 1 -> 2" in batched.stdout
        assert 'STATES | lamp: "off" -> "on"' in batched.stdout
        receipt = json.loads(sorted((run / ".assay" / "receipts").glob("*.json"))[-1].read_text())
        assert receipt["states"] == ['STATES | counter: 1 -> 2', 'STATES | lamp: "off" -> "on"']
    finally:
        stop_run(run)


def _activity(run: Path) -> list[dict]:
    lines = (run / ".assay" / "activity.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_the_retired_command_word_answers_and_is_absent_from_the_help(tmp_path):
    """`assay channel declare` and `assay channel list` are `assay state
    declare` and `assay state list` for one release: the retired word is
    replaced before the parse, so both spellings run the same command, the
    activity records carry the current word, and the help lists `state`
    alone."""
    from assay.cli import command_line

    assert command_line(["--run-dir", "x", "channel", "list", "--read"]) == ["--run-dir", "x", "state", "list", "--read"]
    assert command_line(["channel", "declare", "channel", "--path", "channel"]) == ["state", "declare", "channel", "--path", "channel"]
    assert command_line(["state", "declare", "channel", "--path", "channel"]) == ["state", "declare", "channel", "--path", "channel"]
    assert command_line(["--run-dir=x", "status"]) == ["--run-dir=x", "status"]
    assert command_line([]) == []
    run = tmp_path / "alias"
    _prepare(run, budget={"actions": 20})
    try:
        assert _start(run).returncode == 0
        old = run_cli(run, "channel", "declare", "counter", "--path", "counter")
        assert old.returncode == 0, old.stderr
        assert old.stdout.rstrip("\n") == "STATE | declared counter (path); outcomes like `ch counter = V` now parse and grade"
        new = run_cli(run, "state", "declare", "lamp", "--path", "lamp")
        assert new.returncode == 0 and new.stdout.startswith("STATE | declared lamp (path)")
        listed_old = run_cli(run, "channel", "list")
        listed_new = run_cli(run, "state", "list")
        assert listed_old.returncode == 0 and listed_old.stdout == listed_new.stdout
        assert "STATES | registered: goal · level · budget_remaining · counter · lamp" in listed_new.stdout
        assert "CHANNEL" not in listed_new.stdout
        assert [r["command"] for r in _activity(run) if r.get("kind") == "command_start"] == [
            "state", "state", "state", "state",
        ]
        root = run_cli(run, "--help")
        assert root.returncode == 0 and "channel" not in root.stdout and "    state " in root.stdout
        group = run_cli(run, "channel", "--help")
        assert group.returncode == 0 and group.stdout.startswith("usage: assay state [-h] {declare,list} ...")
        # The refusal names the `state` group's field. The choices after it
        # are rendered bare by some interpreters and quoted by others (the
        # 3.13 and 3.14 patch releases), so the line is pinned up to them.
        unknown = run_cli(run, "channel", "nope")
        assert unknown.returncode == 2
        refusal = unknown.stderr.split("\n")[0]
        assert refusal.startswith("ERROR | CLI_USAGE | argument state_command: invalid choice: 'nope' (choose from ")
        assert refusal.endswith(("(choose from declare, list)", "(choose from 'declare', 'list')"))
    finally:
        stop_run(run)


OLD_NAME_MODEL = '''"""A model written before 1.2.0 named its states CHANNELS."""

CHANNELS = ["counter"]


def next(obs, action, params):
    data = dict(obs["data"])
    if action == "INC":
        data["counter"] = int(data["counter"]) + int((params or {})["amount"])
    elif action not in ("NOOP", "SET_LAMP"):
        return None
    out = dict(obs)
    out["data"] = data
    return out
'''


def test_a_model_declaring_channels_still_replays(tmp_path):
    """`model.py` names the states it predicts in STATES; the earlier name
    CHANNELS is read for one release, since models with it exist in the
    published runs."""
    run = tmp_path / "oldname"
    _prepare(run, budget={"actions": 20})
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "state", "declare", "counter", "--path", "counter").returncode == 0
        for _ in range(2):
            assert run_cli(run, "act", "INC", "amount=1", "--predict", "ch counter delta = 1").returncode == 0
        (run / "model.py").write_text(OLD_NAME_MODEL)
        replayed = run_cli(run, "model", "replay")
        assert replayed.returncode == 0, replayed.stderr
        assert "MODEL | replay-fit 100.00% | held 2 missed 0 unknown 0 over 2 transitions" in replayed.stdout
        fit = json.loads((run / ".assay" / "model_fit.json").read_text())
        assert fit["declared"] == ["counter"] and fit["per_channel"] == {"counter": {"held": 2, "missed": 0, "unknown": 0}}
        (run / "model.py").write_text("def next(obs, action, params):\n    return None\n")
        empty = run_cli(run, "model", "replay")
        assert empty.returncode == 2 and "ERROR | MODEL_INVALID | model.py declares no STATES" in empty.stderr
        assert "NEXT | name the states the model predicts in STATES" in empty.stderr
        (run / "model.py").unlink()
        created = run_cli(run, "model", "init")
        assert created.returncode == 0 and created.stdout.rstrip("\n") == f"CREATED | {run / 'model.py'}; declare STATES, define next()"
        assert "STATES = []  # e.g. [\"counter\", \"level\"]: registered state names you model" in (run / "model.py").read_text()
    finally:
        stop_run(run)


def test_a_readings_cache_from_before_the_rename_is_read_after_a_resume(tmp_path):
    """The daemon's cache of graded extractor readings keeps its on-disk
    name, `.assay/channel_readings.json`, so a run directory written before
    the rename shows its last graded readings after a resume instead of
    `not yet graded`, and no second cache appears beside it."""
    run = tmp_path / "cache"
    _prepare(run, budget={"actions": 20})
    (run / "tens.py").write_text(EXTRACTOR)
    cache = run / ".assay" / "channel_readings.json"
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "state", "declare", "tens", "--file", "tens.py").returncode == 0
        acted = run_cli(run, "act", "INC", "amount=2", "--predict", "ch tens = 20")
        assert acted.returncode == 0 and "RESULT | PREDICTED" in acted.stdout, acted.stdout
        assert json.loads(cache.read_text()) == {"tens": {"event": 1, "value": 20}}
        assert run_cli(run, "stop").returncode == 0
        # The directory as an earlier kernel left it: the cache under its
        # name, holding a reading only that file can account for.
        cache.write_text(json.dumps({"tens": {"event": 1, "value": 77}}))
        assert _start(run).returncode == 0
        status = run_cli(run, "status")
        assert "tens=77 @e1 (extractor, last graded)" in status.stdout, status.stdout
        listed = run_cli(run, "state", "list")
        assert "tens=77 @e1 (extractor, last graded)" in listed.stdout
        assert json.loads(cache.read_text()) == {"tens": {"event": 1, "value": 77}}
        assert sorted(path.name for path in (run / ".assay").glob("*readings*")) == ["channel_readings.json"]
    finally:
        stop_run(run)
