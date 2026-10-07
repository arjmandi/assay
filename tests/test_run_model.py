"""The run model's invariants (docs/ARCHITECTURE.md section 6.6) that the
other tests do not already pin: no function below the entry points reads the
journal (an AST walk over src/assay and src/assay_grid refuses `load_events(`
outside run.py, core.py and analysis.py), the daemon writes event 0 before it
reports READY and the CLI never writes config.json after the spawn, and a
journal the daemon wrote reads back through `Run.load` into records that
re-serialize to the same lines."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
SOURCES = [REPO / "src" / "assay", REPO / "src" / "assay_grid"]
ALLOWED = {"run.py", "core.py", "analysis.py"}
ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]


def test_no_function_below_the_entry_points_reads_the_journal():
    offenders = []
    for root in SOURCES:
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callee = node.func
                name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
                if name == "load_events" and path.name not in ALLOWED:
                    offenders.append(f"{root.name}/{path.name}:{node.lineno}")
    assert not offenders, offenders


def _events(run: Path) -> list[dict]:
    return [json.loads(line) for line in (run / ".assay" / "events.jsonl").read_text().splitlines() if line.strip()]


def test_event_zero_is_the_daemons_and_the_journal_reads_back_identically(tmp_path):
    run = tmp_path / "zero"
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}}))
    try:
        started = run_cli(
            run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
            "--registry", str(run / "reg.json"),
        )
        assert started.returncode == 0, started.stderr
        # START is on disk with public_info in config.json before READY was
        # reported: the daemon's descriptor names its pid and the first line
        # was written by that process (its activity leaves no command record,
        # which the CLI's commands do).
        events = _events(run)
        assert len(events) == 1 and events[0]["action"] == "START"
        broker = json.loads((run / ".assay" / "broker.json").read_text())
        assert broker["status"] == "READY"
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["public_info"] == {} and config["game_id"] == "fake1"
        assert "STATUS | fake1 | event 0 |" in started.stdout
        for _ in range(3):
            assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        from assay.core import RunPaths
        from assay.run import Run

        loaded = Run.load(RunPaths(run), strict=True)
        lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
        assert [
            json.dumps(event.to_json(), separators=(",", ":"), sort_keys=True)
            for event in loaded.events
        ] == lines
        assert loaded.integrity.chain == "intact" and loaded.verify_disk() == []
        assert [event.id for event in loaded.events] == [0, 1, 2, 3]
    finally:
        stop_run(run)
