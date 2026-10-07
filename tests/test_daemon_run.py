"""The daemon holds one run for its life (docs/ARCHITECTURE.md sections 6.3,
6.6 and 8.3): it appends and chains in memory and the disk agrees with it
after a thousand events; two clients racing on its socket leave a contiguous
journal with every mutation recorded once; a file changed under it is refused
before the next paid action and every one after, with nothing adopted,
nothing appended, no install and the refusal on the status line; and the
owner's module install is its operation, refused without it. Every scenario
drives the real CLI and the real daemon with the fake adapter."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, SRC_DIR, journal_head, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]

PROBE = '''
class _Probe:
    NAME = "probe"
    CONSTITUTION = "a module the owner installs"
    MODE = "advise"

    def trigger(self, view, pending):
        return "probe fired"

    def demand(self, view, pending):
        return None

    def telemetry(self, view):
        return {}


MODULE = _Probe()
'''

# A client that speaks the socket directly, without the CLI's run lock: the
# daemon alone serializes it against the other one.
RACER = """\
import sys
from pathlib import Path

from assay.broker import broker_gated
from assay.core import RunPaths

paths = RunPaths(Path(sys.argv[1]))
for _ in range(int(sys.argv[2])):
    receipt = broker_gated(
        paths,
        {"op": "gated_act", "action_token": "NOOP", "predict": "noop",
         "because": None, "at_event": None, "declares": {}},
    )
    assert receipt.outcome == "PREDICTED", receipt
"""


def _prepare(run: Path, *, budget: int = 30, hand_cap: int | None = None) -> None:
    run.mkdir()
    spec: dict = {"actions": ACTIONS, "budget": {"actions": budget}}
    if hand_cap is not None:
        spec["batching"] = {"hand_cap": hand_cap}
    (run / "reg.json").write_text(json.dumps(spec))


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _token(stdout: str) -> str:
    found = re.search(r"OWNER TOKEN \| (\S+) \|", stdout)
    assert found, stdout
    return found.group(1)


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _events(run: Path) -> list[dict]:
    return _lines(run / ".assay" / "events.jsonl")


def _mutations(run: Path) -> list[dict]:
    return _lines(run / ".assay" / "mutations.jsonl")


def _activity(run: Path, kind: str) -> list[dict]:
    return [record for record in _lines(run / ".assay" / "activity.jsonl") if record["kind"] == kind]


def _bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _held(run: Path):
    from assay.broker import broker_state
    from assay.core import RunPaths

    return broker_state(RunPaths(run))


def _loaded(run: Path):
    from assay.core import RunPaths
    from assay.run import Run

    return Run.load(RunPaths(run), strict=True)


def test_a_thousand_events_through_the_daemon_agree_with_a_fresh_load(tmp_path):
    """Invariant 3 of section 6.6: status, audit and view from a fresh load
    agree with the daemon's held state after a thousand events, and the
    held head is the head over the file (invariant 2)."""
    run = tmp_path / "thousand"
    _prepare(run, budget=1000, hand_cap=100)
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        batch = ["--step", "NOOP :: noop"] * 100
        began = time.monotonic()
        for _ in range(10):
            committed = run_cli(run, "commit", *batch)
            assert committed.returncode == 0, committed.stderr
            assert "OUTCOME | PREDICTED | all 100 steps landed as predicted" in committed.stdout
        elapsed = time.monotonic() - began
        held = _held(run)
        assert held.chain_event == 1000 and held.tampered is None
        loaded = _loaded(run)
        assert loaded.chain_event == 1000 and loaded.chain_head == held.chain_head
        assert loaded.integrity.chain == "intact" and loaded.verify_disk() == []
        assert held.chain_head == journal_head(loaded.paths)
        assert json.loads((run / ".assay" / "chain.json").read_text()) == {
            "event_id": 1000,
            "head": held.chain_head,
        }
        assert [event["id"] for event in _events(run)] == list(range(1001))
        assert [item["mutation_id"] for item in _mutations(run)] == list(range(1, 1001))
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        assert "STATUS | fake1 | event 1000 | progress 1/1 | paid actions 1000 | NOT_FINISHED" in status.stdout
        assert "INTEGRITY" not in status.stdout
        audited = run_cli(run, "audit")
        assert (
            "AUDIT | CLEAN | events 1001 (paid 1000) | contiguous yes | chain intact | "
            "anchors intact (40)" in audited.stdout
        )
        viewed = run_cli(run, "view")
        assert "RUN | event 1000 | progress 1/1 | paid actions 1000 | state NOT_FINISHED" in viewed.stdout
        # The verification the daemon ran before each of those steps, timed
        # on the journal they left; the design note budgets 70 ms worst case
        # for a journal a hundred times this size.
        began = time.perf_counter()
        for _ in range(20):
            assert loaded.verify_disk() == []
        per_call = (time.perf_counter() - began) / 20
        print(
            f"\nthousand events in {elapsed:.1f}s; verify_disk over "
            f"{loaded.journal_bytes} bytes: {per_call * 1000:.2f} ms per paid action"
        )
    finally:
        stop_run(run)


def test_two_clients_racing_on_one_socket_produce_a_contiguous_journal(tmp_path):
    """Invariant 6 of section 6.6, pinned: the daemon is single-threaded, so
    two clients racing on its socket, neither holding the CLI's run lock,
    leave ids 0..50 in order and every mutation recorded exactly once."""
    run = tmp_path / "race"
    _prepare(run, budget=50)
    try:
        assert _start(run).returncode == 0
        environment = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
        racers = [
            subprocess.Popen(
                [sys.executable, "-c", RACER, str(run), "25"],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        for racer in racers:
            _, err = racer.communicate(timeout=170)
            assert racer.returncode == 0, err
        events = _events(run)
        assert [event["id"] for event in events] == list(range(51))
        assert sorted(event["mutation_id"] for event in events[1:]) == list(range(1, 51))
        assert [item["mutation_id"] for item in _mutations(run)] == list(range(1, 51))
        held = _held(run)
        loaded = _loaded(run)
        assert held.chain_event == loaded.chain_event == 50
        assert held.chain_head == loaded.chain_head == journal_head(loaded.paths)
        assert loaded.integrity.contiguous and loaded.verify_disk() == []
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN | events 51 (paid 50) | contiguous yes | chain intact" in audited.stdout
        # Every receipt reached its client: 50 act receipts in the activity
        # log, one per event.
        receipts = [record for record in _activity(run, "act") if "end_event" in record]
        assert sorted(record["end_event"] for record in receipts) == list(range(1, 51))
    finally:
        stop_run(run)


# Each tamper returns the `what` of the first difference the daemon names,
# and a way to put the bytes back when the CLI could not load the run while
# they stood (the daemon's refusal is sticky either way).

Restore = Callable[[], None] | None


def _append_journal_line(run: Path) -> tuple[str, Restore]:
    events = _events(run)
    forged = {**events[-1], "id": events[-1]["id"] + 1, "note": "forged"}
    with (run / ".assay" / "events.jsonl").open("a") as handle:
        handle.write(json.dumps(forged, separators=(",", ":"), sort_keys=True) + "\n")
    return "events.jsonl length", None


def _edit_journal_line(run: Path) -> tuple[str, Restore]:
    path = run / ".assay" / "events.jsonl"
    lines = path.read_text().splitlines()
    last = json.loads(lines[-1])
    last["predict"] = "nope"  # the same length as "noop": only the head differs
    lines[-1] = json.dumps(last, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    return "events.jsonl head", None


def _append_bad_byte(run: Path) -> tuple[str, Restore]:
    with (run / ".assay" / "events.jsonl").open("ab") as handle:
        handle.write(b"\xff\n")
    return "events.jsonl length", None


def _append_mutation(run: Path) -> tuple[str, Restore]:
    mutations = _mutations(run)
    forged = {**mutations[-1], "mutation_id": mutations[-1]["mutation_id"] + 1}
    with (run / ".assay" / "mutations.jsonl").open("a") as handle:
        handle.write(json.dumps(forged, separators=(",", ":"), sort_keys=True) + "\n")
    return "mutations.jsonl length", None


def _replace_chain_head(run: Path) -> tuple[str, Restore]:
    path = run / ".assay" / "chain.json"
    chain = json.loads(path.read_text())
    chain["head"] = "0" * 64
    path.write_text(json.dumps(chain))
    return "chain.json", None


def _delete_chain(run: Path) -> tuple[str, Restore]:
    (run / ".assay" / "chain.json").unlink()
    return "chain.json", None


def _edit_registry(run: Path) -> tuple[str, Restore]:
    path = run / ".assay" / "registry.json"
    registry = json.loads(path.read_text())
    registry["budget"] = {"actions": 999}
    path.write_text(json.dumps(registry))
    return "registry.json", None


def _edit_config(run: Path) -> tuple[str, Restore]:
    path = run / ".assay" / "config.json"
    config = json.loads(path.read_text())
    config["seed"] = 7
    path.write_text(json.dumps(config))
    return "config.json", None


def _corrupt_config(run: Path) -> tuple[str, Restore]:
    path = run / ".assay" / "config.json"
    original = path.read_bytes()
    path.write_bytes(b"{not json")
    return "config.json", lambda: path.write_bytes(original)


def _rewrite_owner(run: Path) -> tuple[str, Restore]:
    (run / ".assay" / "owner.json").write_text(json.dumps({"sha256": "f" * 64}))
    return "owner.json", None


def _forge_manifest(run: Path) -> tuple[str, Restore]:
    modules = run / ".assay" / "modules"
    modules.mkdir(exist_ok=True)
    (modules / "probe.py").write_text(PROBE)
    (modules / "manifest.json").write_text(
        json.dumps({"version": 1, "modules": [{"name": "probe", "file": "probe.py", "sha256": "0" * 64}]})
    )
    return "modules/manifest.json", None


DIVERGED = "ERROR | CHAIN_DIVERGED | chain: stored head at e1 does not match"
# (the edit, what the refusal line shows on disk, how the next start refuses
# or None when it does not: an appended line reads as a crash's one line, a
# deleted chain as a pre-chain run, and a rewritten registry, owner or
# manifest is admitted, which is the limit of section 8.3 and #10's work)
TAMPERS = [
    pytest.param(_append_journal_line, "on disk ", None, id="journal-append"),
    pytest.param(_edit_journal_line, "on disk ", DIVERGED, id="journal-edit"),
    pytest.param(
        _append_bad_byte, "on disk ",
        "ERROR | CHAIN_DIVERGED | line 3 malformed: 'utf-8' codec can't decode byte 0xff",
        id="journal-bad-byte",
    ),
    pytest.param(_append_mutation, "on disk ", None, id="mutation-append"),
    pytest.param(_replace_chain_head, "on disk e1 000000000000", DIVERGED, id="chain-replaced"),
    pytest.param(_delete_chain, "on disk absent", None, id="chain-deleted"),
    pytest.param(_edit_registry, "on disk ", None, id="registry"),
    pytest.param(_edit_config, "on disk ", None, id="config"),
    pytest.param(_corrupt_config, "on disk unreadable: corrupt JSON in", None, id="config-corrupt"),
    pytest.param(_rewrite_owner, "on disk ffffffffffff", None, id="owner"),
    pytest.param(_forge_manifest, "on disk ", None, id="manifest"),
]


@pytest.mark.parametrize("tamper, found, restart", TAMPERS)
def test_a_changed_file_is_refused_before_the_next_paid_action(tmp_path, tamper, found, restart):
    """Section 8.3 as far as #20 takes it: the difference is named, the
    action and every later one refused, an install refused, the activity
    record written once, the refusal on the status line, nothing adopted
    from the disk and nothing appended to the changed file. The sealed
    anchor is #10's."""
    from assay.broker import broker_gated
    from assay.core import AssayError, RunPaths

    run = tmp_path / "tamper"
    _prepare(run)
    (tmp_path / "probe.py").write_text(PROBE)
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        token = _token(started.stdout)
        assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        before = _held(run)
        what, restore = tamper(run)
        state = run / ".assay"
        journal = (state / "events.jsonl").read_bytes()
        mutations = (state / "mutations.jsonl").read_bytes()
        manifest = _bytes(state / "modules" / "manifest.json")
        # The first paid action after the edit, on the socket (the CLI's own
        # load cannot read a corrupt configuration): refused by name.
        with pytest.raises(AssayError) as caught:
            broker_gated(
                RunPaths(run),
                {"op": "gated_act", "action_token": "NOOP", "predict": "noop",
                 "because": None, "at_event": None, "declares": {}},
            )
        message = str(caught.value)
        assert message.startswith(f"AssayError: TAMPER_DETECTED | {what} (held "), message
        assert found in message
        assert "refuses every paid action until it is stopped" in message
        records = _activity(run, "tamper_detected")
        assert len(records) == 1
        assert records[0]["event"] == 1 and records[0]["head"] == before.chain_head
        assert what in [item["what"] for item in records[0]["differences"]]
        # Nothing adopted, nothing appended: the files are as the edit left
        # them and the daemon holds what it held.
        assert (state / "events.jsonl").read_bytes() == journal
        assert (state / "mutations.jsonl").read_bytes() == mutations
        after = _held(run)
        assert after.chain_event == 1 and after.chain_head == before.chain_head
        assert after.tampered is not None and after.tampered.startswith(f"{what} (held ")
        if restore is not None:
            # The bytes put back change nothing: the refusal stands.
            restore()
        # Every later paid action, of every kind, is refused the same way,
        # and so is an install, which would rewrite a changed manifest; the
        # record is not written again.
        refused = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert refused.returncode == 2, refused.stdout
        assert f"ERROR | AssayError: TAMPER_DETECTED | {what} (held " in refused.stderr
        again = run_cli(run, "commit", "--step", "NOOP :: noop")
        assert again.returncode == 2 and f"TAMPER_DETECTED | {what} (held " in again.stderr
        reset = run_cli(run, "reset", "--because", "testing the refusal")
        assert reset.returncode == 2 and "TAMPER_DETECTED" in reset.stderr
        install = run_cli(run, "module", "install", str(tmp_path / "probe.py"), "--token", token)
        assert install.returncode == 2 and f"TAMPER_DETECTED | {what} (held " in install.stderr
        assert _bytes(state / "modules" / "manifest.json") == manifest
        assert not _activity(run, "module_installed")
        assert len(_activity(run, "tamper_detected")) == 1
        assert (state / "events.jsonl").read_bytes() == journal
        # The offline readers still answer, status carries the refusal from
        # the daemon, and the daemon still stops.
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        assert (
            f"INTEGRITY | the daemon refused a paid action: {what} (held " in status.stdout
        )
        assert "every paid action until it is stopped and the record is examined" in status.stdout
        stopped = run_cli(run, "stop")
        assert stopped.returncode == 0, stopped.stderr
        assert "INTEGRITY | the daemon refused" not in run_cli(run, "status").stdout
        if restart is not None:
            resumed = _start(run)
            assert resumed.returncode == 2, resumed.stdout
            assert restart in resumed.stderr
            assert (state / "events.jsonl").read_bytes() == journal
    finally:
        stop_run(run)


def test_module_install_is_the_daemons_operation(tmp_path):
    """Section 6.4: without a live daemon the install refuses and names the
    way back; with one, the module is pinned into the manifest the daemon
    holds and fires on the next paid action without a restart."""
    run = tmp_path / "install"
    _prepare(run)
    (tmp_path / "probe.py").write_text(PROBE)
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        token = _token(started.stdout)
        assert run_cli(run, "stop").returncode == 0
        refused = run_cli(run, "module", "install", str(tmp_path / "probe.py"), "--token", token)
        assert refused.returncode == 2
        assert (
            "ERROR | module install is a daemon operation and this run's environment "
            "owner is not running; resume it with `assay start WORLD_ID`, then install again"
        ) in refused.stderr
        assert not (run / ".assay" / "modules").exists()
        assert not _activity(run, "module_installed")
        assert _start(run).returncode == 0
        denied = run_cli(run, "module", "install", str(tmp_path / "probe.py"))
        assert denied.returncode == 2 and "owner authority required" in denied.stderr
        installed = run_cli(run, "module", "install", str(tmp_path / "probe.py"), "--token", token)
        assert installed.returncode == 0, installed.stderr
        assert (
            f"MODULE | installed probe from {(tmp_path / 'probe.py').resolve()} (sha256 "
            in installed.stdout
        )
        assert ") | journaled | active from the next action" in installed.stdout
        record = _activity(run, "module_installed")[0]
        assert record["name"] == "probe" and record["origin"] == "install"
        manifest = json.loads((run / ".assay" / "modules" / "manifest.json").read_text())
        assert [entry["name"] for entry in manifest["modules"]] == ["probe"]
        # The daemon loaded it into the set it holds: the manifest it wrote
        # is the one it verifies, so the next action runs and the module
        # speaks on it.
        acted = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert acted.returncode == 0, acted.stderr
        assert "MODULE probe | probe fired" in acted.stdout
        assert _held(run).tampered is None
    finally:
        stop_run(run)
