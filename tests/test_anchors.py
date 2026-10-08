"""Anchors are pinned in config.json at start, shown in status, and a failed
anchor write or a changed ASSAY_ANCHOR_DIR is visible instead of silent. A
start refuses an anchor directory it cannot write, a resume refuses a
config.json whose anchor file is not the one the environment names, an anchor
file inside the run directory is no evidence, an unreadable anchor file is a
finding for the readers and a refusal for a start, and a seal the daemon
could not write stands as its activity record (docs/ARCHITECTURE.md sections
8.2 and 8.3)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]


def _prepare(run: Path) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}}))


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _resume(run: Path):
    return run_cli(run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory")


def _config(run: Path) -> dict:
    return json.loads((run / ".assay" / "config.json").read_text())


def _activity(run: Path, kind: str) -> list[dict]:
    lines = (run / ".assay" / "activity.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip() and json.loads(line)["kind"] == kind]


def _win(run: Path) -> None:
    assert run_cli(run, "act", "INC", "amount=2", "--predict", "change").returncode == 0
    won = run_cli(run, "act", "INC", "amount=1", "--predict", "win")
    assert won.returncode == 0 and "GAME_COMPLETE" in won.stdout


def test_anchor_file_is_pinned_and_shown(tmp_path, monkeypatch):
    anchors_a = tmp_path / "anchors-a"
    anchors_b = tmp_path / "anchors-b"
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors_a))
    run = tmp_path / "pin"
    _prepare(run)
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        pinned = Path(_config(run)["anchor_file"])
        assert pinned.parent == anchors_a
        assert f"ANCHORS | {pinned} | none yet" in started.stdout
        assert "WARNING" not in started.stdout
        _win(run)
        status = run_cli(run, "status")
        assert f"ANCHORS | {pinned} | 1 anchor(s), last e2" in status.stdout
        assert pinned.exists()
        # Another shell, another ASSAY_ANCHOR_DIR: the recorded file is still
        # the one audited and shown, and the mismatch is reported.
        monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors_b))
        audited = run_cli(run, "audit")
        assert "anchors intact (1)" in audited.stdout
        assert "anchor_env_mismatch" in audited.stdout
        assert "AUDIT | CLEAN" in audited.stdout  # information, not a verdict
        report = json.loads((run / ".assay" / "audit.json").read_text())
        assert report["anchor_file"] == str(pinned) and report["anchor_env_mismatch"] is True
        status = run_cli(run, "status")
        assert f"ANCHORS | {pinned} |" in status.stdout
        assert not anchors_b.exists() or not any(anchors_b.iterdir())
    finally:
        stop_run(run)


def test_an_unwritable_anchor_directory_refuses_the_start(tmp_path, monkeypatch):
    """Section 8.3: the chain heads and the seal go to the anchor directory,
    so a start refuses one it cannot write, before anything is written, and
    a resume refuses the same way."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where the anchor directory should be\n")
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(blocker / "anchors"))
    run = tmp_path / "unwritable"
    _prepare(run)
    try:
        started = _start(run)
        assert started.returncode == 2, started.stdout
        assert started.stderr == (
            f"ERROR | ANCHOR_DIR_UNWRITABLE | the anchor directory {blocker / 'anchors'} is not "
            "writable, and the run's chain heads and its seal go there\n"
            "NEXT | set ASSAY_ANCHOR_DIR to a directory this user can write, or give this user "
            "write permission on it, then start again\n"
        )
        assert not (run / ".assay").exists()  # nothing half-initialized left behind
        # A run started where the directory was writable refuses to resume
        # once it is not.
        anchors = tmp_path / "anchors"
        monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
        assert _start(run).returncode == 0
        assert run_cli(run, "stop").returncode == 0
        anchors.mkdir(exist_ok=True)
        anchors.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            resumed = _resume(run)
            assert resumed.returncode == 2, resumed.stdout
            assert f"ERROR | ANCHOR_DIR_UNWRITABLE | the anchor directory {anchors} is not writable" in resumed.stderr
        finally:
            anchors.chmod(stat.S_IRWXU)
        assert _resume(run).returncode == 0
    finally:
        stop_run(run)


def test_a_seal_the_daemon_could_not_write_is_an_audit_problem(tmp_path, monkeypatch):
    """A run in flight whose anchor directory became unwritable at seal time:
    the seal exists as the `anchor_failed` record carrying `seal`, the audit
    reads it as anchors DIVERGED, the TAMPER line says the seal could not be
    written, and the resume is refused while the directory stays
    unwritable."""
    anchors = tmp_path / "anchors"
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
    run = tmp_path / "unwritten"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        pinned = Path(_config(run)["anchor_file"])
        anchors.mkdir(exist_ok=True)
        anchors.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            registry = run / ".assay" / "registry.json"
            spec = json.loads(registry.read_text())
            spec["budget"] = {"actions": 999}
            registry.write_text(json.dumps(spec))
            refused = run_cli(run, "act", "NOOP", "--predict", "noop")
            assert refused.returncode == 5 and "ERROR | TAMPER_DETECTED | registry.json" in refused.stderr
            failed = _activity(run, "anchor_failed")
            assert len(failed) == 1 and failed[0]["event"] == 1 and failed[0]["seal"] == "tamper_detected"
            assert failed[0]["file"] == str(pinned) and not pinned.exists()
            status = run_cli(run, "status")
            assert "TAMPER | 1 tamper_detected record(s) in the activity log; the seal could not be written" in status.stdout
            assert "last write FAILED at e1" in status.stdout
            assert run_cli(run, "stop").returncode == 0
            audited = run_cli(run, "audit")
            assert (
                "AUDIT | INVALID FOR SCORING | events 2 (paid 1) | contiguous yes | chain intact | "
                "anchors DIVERGED (0)"
            ) in audited.stdout
            assert (
                f"AUDIT | problem: anchor: the seal at e1 could not be written to {pinned} "
                f"({failed[0]['error']}); the daemon found the run's files changed under it and the "
                "record ends there"
            ) in audited.stdout
            assert "AUDIT | TAMPER | 1 tamper_detected record(s) in the activity log; the seal could not be written" in audited.stdout
            resumed = _resume(run)
            assert resumed.returncode == 2 and "ERROR | ANCHOR_DIR_UNWRITABLE |" in resumed.stderr
        finally:
            anchors.chmod(stat.S_IRWXU)
    finally:
        stop_run(run)


def test_a_moved_anchor_file_refuses_the_resume_and_one_inside_the_run_is_no_evidence(tmp_path, monkeypatch):
    """Section 8.2: config.json's anchor file must be the one the environment
    names, else the resume refuses with ANCHOR_FILE_MISMATCH; a recorded
    file inside the run directory is anchors DIVERGED for the audit, while a
    directory the environment names differently stays information."""
    anchors = tmp_path / "anchors"
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
    run = tmp_path / "moved"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "stop").returncode == 0
        config = _config(run)
        recorded = Path(config["anchor_file"])
        # The agent rewrites config.json while the daemon is stopped, pointing
        # the anchors into a file it owns.
        inside = run / "my-anchors.jsonl"
        config["anchor_file"] = str(inside)
        (run / ".assay" / "config.json").write_text(json.dumps(config, sort_keys=True))
        audited = run_cli(run, "audit")
        assert audited.returncode == 0, audited.stderr
        assert "anchors DIVERGED (0)" in audited.stdout and "AUDIT | INVALID FOR SCORING" in audited.stdout
        assert (
            f"AUDIT | problem: anchor: the recorded anchor file {inside} lies inside the run directory, "
            "which the agent writes; its anchors are no evidence"
        ) in audited.stdout
        resumed = _resume(run)
        assert resumed.returncode == 2, resumed.stdout
        assert resumed.stderr == (
            f"ERROR | ANCHOR_FILE_MISMATCH | config.json records the anchor file {inside}, and the "
            f"environment names {recorded}\n"
            f"NEXT | if {inside.parent} is your anchor directory, set ASSAY_ANCHOR_DIR to it and start "
            "again; if the recorded path is not yours, treat the run as tampered: config.json was rewritten\n"
        )
        # The operator's own move: the recorded file in another directory of
        # theirs is a mismatch for this shell, and the one the shell names
        # resumes.
        config["anchor_file"] = str(recorded)
        (run / ".assay" / "config.json").write_text(json.dumps(config, sort_keys=True))
        monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(tmp_path / "elsewhere"))
        resumed = _resume(run)
        assert resumed.returncode == 2 and "ERROR | ANCHOR_FILE_MISMATCH |" in resumed.stderr
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout and "anchor_env_mismatch" in audited.stdout
        monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
        resumed = _resume(run)
        assert resumed.returncode == 0, resumed.stderr
    finally:
        stop_run(run)


def test_an_unreadable_anchor_file_is_reported_by_the_readers_and_refuses_the_start(tmp_path, monkeypatch):
    """Section 8.3: a line of the anchor file that is not one JSON object
    is a finding, never read around: status says so on the ANCHORS line,
    audit reads the anchors as DIVERGED, and the resume refuses with
    RECORD_CORRUPT naming the file and the line."""
    anchors = tmp_path / "anchors"
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
    run = tmp_path / "unreadable"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        _win(run)
        assert run_cli(run, "stop").returncode == 0
        pinned = Path(_config(run)["anchor_file"])
        good = pinned.read_text()
        pinned.write_text(good + "not json\n")
        problem = "line 2 is not JSON (Expecting value: line 1 column 1 (char 0))"
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        assert f"ANCHORS | {pinned} | unreadable: {problem}; `assay start` refuses until it is fixed" in status.stdout
        assert "INTEGRITY | anchor file" in status.stdout and "(RECORD_CORRUPT)" in status.stdout
        audited = run_cli(run, "audit")
        assert (
            "AUDIT | INVALID FOR SCORING | events 3 (paid 2) | contiguous yes | chain intact | "
            "anchors DIVERGED (0)"
        ) in audited.stdout
        assert f"AUDIT | problem: anchor: the anchor file {pinned} is unreadable: {problem}" in audited.stdout
        resumed = _resume(run)
        assert resumed.returncode == 4, resumed.stdout
        assert resumed.stderr == (
            f"ERROR | RECORD_CORRUPT | anchor file {pinned} is unreadable: {problem}; the run is refused "
            "and nothing is rewritten\n"
            "NEXT | the anchor file is the operator's: every line is one JSON object; fix or remove the "
            f"line in {pinned} by hand, then start again\n"
        )
        pinned.write_text(good)
        resumed = _resume(run)
        assert resumed.returncode == 0 and "completed run" in resumed.stdout
        assert "anchors intact (1)" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)


def test_run_without_the_key_uses_the_environment(tmp_path, monkeypatch):
    anchors = tmp_path / "anchors"
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
    run = tmp_path / "legacy"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        # A run recorded before the key existed: its config is edited with
        # the daemon stopped, since the daemon refuses a config.json changed
        # under it (docs/ARCHITECTURE.md section 8.3), and the resume loads
        # the config as a daemon serving such a run would.
        assert run_cli(run, "stop").returncode == 0
        config = _config(run)
        config.pop("anchor_file")
        (run / ".assay" / "config.json").write_text(json.dumps(config, sort_keys=True))
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        _win(run)
        status = run_cli(run, "status")
        assert f"ANCHORS | {anchors}" in status.stdout and "1 anchor(s)" in status.stdout
        audited = run_cli(run, "audit")
        assert "anchors intact (1)" in audited.stdout
        assert "anchor_env_mismatch" not in audited.stdout
    finally:
        stop_run(run)


def test_the_anchor_directory_check_reads_the_mode_without_side_effects(tmp_path):
    from assay.integrity import anchor_dir_writable

    target = tmp_path / "anchors" / "file.jsonl"
    assert anchor_dir_writable(target)
    (tmp_path / "anchors").mkdir()
    (tmp_path / "anchors").chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert not anchor_dir_writable(target) or os.geteuid() == 0
    finally:
        (tmp_path / "anchors").chmod(stat.S_IRWXU)
    assert anchor_dir_writable(target)
