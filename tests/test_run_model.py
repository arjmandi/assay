"""The run model's invariants (docs/ARCHITECTURE.md section 6.6) that the
other tests do not already pin: no function below the entry points reads the
journal (an AST walk over src/assay and src/assay_grid, with an allow-list
naming each surviving reader and why), the daemon writes event 0 before it
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
# The readers of events.jsonl that may exist below the entry points, as
# (file, function), each with its reason. Everything else reads the journal
# through the run it is handed.
ALLOWED_READERS = {
    ("assay/run.py", "_load_journal"): "the loader: the one decode of the journal per process",
    ("assay/run.py", "append"): "the one writer: it opens the file to append",
    ("assay/run.py", "verify_disk"): "the check of the file against the held copies (section 8.3)",
    ("assay/cli.py", "_doctor"): "doctor must read a broken run, so it counts raw lines",
    ("assay/carryover.py", "_journal_digest"): "journal_sha256 is a published key of the knowledge file",
}
ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]


def _reads_journal(node: ast.AST) -> str | None:
    """Why this node reads the journal, or None: a call named `load_events`,
    an attribute chain ending in `.events` on a paths receiver (`paths.events`,
    `run.paths.events`, `self.paths.events`), or a `load_jsonl(...)` whose
    argument mentions the events file."""
    if isinstance(node, ast.Call):
        callee = node.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
        if name == "load_events":
            return "load_events("
        if name == "load_jsonl" and node.args and "events" in ast.unparse(node.args[0]):
            return f"load_jsonl({ast.unparse(node.args[0])})"
    if isinstance(node, ast.Attribute) and node.attr == "events":
        receiver = node.value
        if isinstance(receiver, ast.Name) and "paths" in receiver.id:
            return ast.unparse(node)
        if isinstance(receiver, ast.Attribute) and receiver.attr == "paths":
            return ast.unparse(node)
    return None


def _journal_readers() -> list[tuple[str, str | None, int, str]]:
    found: list[tuple[str, str | None, int, str]] = []
    for root in SOURCES:
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text())
            # The enclosing function of every node, for the allow-list.
            parents: dict[ast.AST, str | None] = {}

            def walk(node: ast.AST, function: str | None) -> None:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function = node.name
                parents[node] = function
                for child in ast.iter_child_nodes(node):
                    walk(child, function)

            walk(tree, None)
            label = f"{root.name}/{path.name}"
            for node, function in parents.items():
                why = _reads_journal(node)
                if why is not None:
                    found.append((label, function, getattr(node, "lineno", 0), why))
    return found


def test_no_function_below_the_entry_points_reads_the_journal():
    readers = _journal_readers()
    offenders = [
        f"{label}:{line} in {function}: {why}"
        for label, function, line, why in readers
        if (label, function) not in ALLOWED_READERS
    ]
    assert not offenders, offenders
    # The allow-list names only readers that exist, so it cannot rot.
    used = {(label, function) for label, function, _, _ in readers}
    assert used == set(ALLOWED_READERS), set(ALLOWED_READERS) ^ used


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
