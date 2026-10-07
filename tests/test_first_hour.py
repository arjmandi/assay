"""The errors a new user meets in the first hour, each answered before anything
is spawned or written: a mistyped adapter, a missing dependency, a bad world
id, a crash with no pointer, and a help page that leads with the general claim
table."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]


def _prepare(run: Path) -> Path:
    run.mkdir()
    registry = run / "reg.json"
    registry.write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}}))
    return registry


def _start(run: Path, adapter: str):
    return run_cli(run, "start", "fake1", "--adapter", adapter, "--registry", str(run / "reg.json"))


def test_missing_adapter_file_is_refused_before_spawn(tmp_path):
    run = tmp_path / "nofile"
    _prepare(run)
    refused = _start(run, "nowhere/world.py:factory")
    assert refused.returncode == 2
    assert "adapter file not found: nowhere/world.py" in refused.stderr
    assert "looked in" in refused.stderr and str(run) in refused.stderr
    assert not (run / ".assay").exists()


def test_unimportable_module_and_missing_factory_are_named(tmp_path):
    run = tmp_path / "nomodule"
    _prepare(run)
    refused = _start(run, "no_such_assay_world:factory")
    assert refused.returncode == 2
    assert "not importable" in refused.stderr and "no_such_assay_world" in refused.stderr
    assert not (run / ".assay").exists()
    refused = _start(run, f"{FAKE_ADAPTER}:no_such_factory")
    assert refused.returncode == 2
    assert "no callable 'no_such_factory'" in refused.stderr
    assert not (run / ".assay").exists()


def test_relative_adapter_path_is_resolved_and_recorded(tmp_path):
    """The README quickstart shape: the adapter path relative to the working
    directory while the run directory is elsewhere."""
    run = tmp_path / "relative"
    _prepare(run)
    import subprocess
    import sys

    from conftest import ASSAY_CLI

    try:
        started = subprocess.run(
            [sys.executable, str(ASSAY_CLI), "--run-dir", str(run), "start", "fake1",
             "--adapter", "examples/counter_world.py:factory",
             "--registry", str(run / "reg.json")],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
        )
        assert started.returncode == 0, started.stderr
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["adapter"] == f"{(REPO / 'examples' / 'counter_world.py').resolve()}:factory"
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
    finally:
        stop_run(run)


def test_world_id_error_names_the_rule(tmp_path):
    """A world id is a label: any non-empty string up to 64 characters with
    no whitespace, control characters or path separators, kept as given."""
    run = tmp_path / "worldid"
    _prepare(run)
    for bad, why in (("my world", "whitespace"), ("a/b", "path separator"),
                     ("", "empty"), ("x" * 65, "65 characters")):
        refused = run_cli(run, "start", bad, "--adapter", f"{FAKE_ADAPTER}:factory",
                          "--registry", str(run / "reg.json"))
        assert refused.returncode == 2, bad
        assert f"invalid world id {bad!r}: " in refused.stderr and why in refused.stderr
        assert "up to 64 characters with no whitespace" in refused.stderr
        assert not (run / ".assay").exists()
    try:
        started = run_cli(run, "start", "My-World_1.v2", "--adapter", f"{FAKE_ADAPTER}:factory",
                          "--registry", str(run / "reg.json"))
        assert started.returncode == 0, started.stderr
        assert "STATUS | My-World_1.v2 |" in started.stdout
        assert json.loads((run / ".assay" / "config.json").read_text())["game_id"] == "My-World_1.v2"
    finally:
        stop_run(run)


def test_internal_error_is_one_line_with_a_saved_traceback(tmp_path):
    run = tmp_path / "crash"
    _prepare(run)
    try:
        assert _start(run, f"{FAKE_ADAPTER}:factory").returncode == 0
        # A proposal record that parses as JSON but is not a proposal (a
        # journal line of that shape is a finding since the run model, below,
        # not a crash).
        with (run / ".assay" / "proposals.jsonl").open("a") as handle:
            handle.write('{"kind": "goal_proposed"}\n')
        status = run_cli(run, "status")
        assert status.returncode == 2
        assert status.stderr.startswith("ERROR | internal: KeyError")
        assert "traceback in" in status.stderr
        saved = run / ".assay" / "last_error.txt"
        assert saved.exists() and "Traceback" in saved.read_text()
        assert "Traceback" not in status.stderr
    finally:
        stop_run(run)


def test_a_malformed_journal_line_is_a_finding_for_the_readers_and_refuses_a_start(tmp_path):
    """A journal line that parses as JSON but is not an event: the readers
    keep the events before it, treat the journal as ending there and name
    the line (status on its INTEGRITY line, audit as a problem), and
    `assay start` refuses to resume with CHAIN_DIVERGED and rewrites nothing."""
    run = tmp_path / "malformed"
    _prepare(run)
    try:
        assert _start(run, f"{FAKE_ADAPTER}:factory").returncode == 0
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        journal = run / ".assay" / "events.jsonl"
        with journal.open("a") as handle:
            handle.write('{"id": 2}\n')
        before = journal.read_text()
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        assert "STATUS | fake1 | event 1 |" in status.stdout
        assert (
            "INTEGRITY | line 3 malformed: missing key 'timestamp'; this run is INVALID FOR "
            "SCORING and `assay start` refuses to resume it (CHAIN_DIVERGED)"
        ) in status.stdout
        audited = run_cli(run, "audit")
        assert audited.returncode == 0, audited.stderr
        assert "AUDIT | INVALID FOR SCORING | events 2 (paid 1)" in audited.stdout
        assert "AUDIT | problem: journal: line 3 malformed: missing key 'timestamp'" in audited.stdout
        resumed = _start(run, f"{FAKE_ADAPTER}:factory")
        assert resumed.returncode == 2
        assert resumed.stderr.startswith("ERROR | CHAIN_DIVERGED | line 3 malformed: missing key 'timestamp'")
        assert journal.read_text() == before
    finally:
        stop_run(run)


def test_act_help_leads_with_the_general_table(tmp_path):
    helped = run_cli(tmp_path, "act", "--help")
    assert helped.returncode == 0, helped.stderr
    text = helped.stdout
    general = text.index("PREDICTION CLAIMS")
    frame = text.index("FRAME WORLDS ONLY")
    assert general < frame
    assert "ch NAME delta OP V" in text and "crosses" in text
    assert text.index("cell X,Y=V") > frame
    assert "ACTION6" not in text
    committed = run_cli(tmp_path, "commit", "--help")
    assert "FRAME WORLDS ONLY" in committed.stdout


def test_a_registry_is_required_and_a_run_without_one_is_not_resumed(tmp_path):
    """Every run has a registry since 1.2.0: `assay start` demands --registry,
    and a directory that owns a pre-registry run is readable but not resumable."""
    run = tmp_path / "noreg"
    run.mkdir()
    adapter = f"{REPO / 'examples' / 'counter_world.py'}:factory"
    bare = run_cli(run, "start", "fake1", "--adapter", adapter)
    assert bare.returncode == 2 and "--registry" in bare.stderr
    assert not (run / ".assay").exists()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}}))
    try:
        assert _start(run, adapter).returncode == 0
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        # A resume needs no --registry: the pinned one is used.
        resumed = run_cli(run, "start", "fake1", "--adapter", adapter)
        assert resumed.returncode == 0 and "RESUMED | fake1" in resumed.stdout
    finally:
        stop_run(run)
    (run / ".assay" / "registry.json").unlink()
    assert run_cli(run, "status").returncode == 0
    resumed = _start(run, adapter)
    assert resumed.returncode == 2 and "not resumed" in resumed.stderr
