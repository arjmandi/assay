"""Anchors are pinned in config.json at start, shown in status, and a failed
anchor write or a changed ASSAY_ANCHOR_DIR is visible instead of silent."""

from __future__ import annotations

import json
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


def _config(run: Path) -> dict:
    return json.loads((run / ".assay" / "config.json").read_text())


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


def test_unwritable_anchor_directory_is_visible(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where the anchor directory should be\n")
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(blocker / "anchors"))
    run = tmp_path / "unwritable"
    _prepare(run)
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        assert "WARNING | ANCHORS |" in started.stdout
        assert "NOT WRITABLE" in started.stdout
        _win(run)  # the anchor write fails on WIN; the spend never does
        activity = (run / ".assay" / "activity.jsonl").read_text()
        assert '"kind":"anchor_failed"' in activity
        status = run_cli(run, "status")
        assert "last write FAILED at e2" in status.stdout
        audited = run_cli(run, "audit")
        assert "anchors none (0)" in audited.stdout
        assert "chain intact" in audited.stdout
    finally:
        stop_run(run)


def test_run_without_the_key_uses_the_environment(tmp_path, monkeypatch):
    anchors = tmp_path / "anchors"
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
    run = tmp_path / "legacy"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        # A run recorded before the key existed.
        config = _config(run)
        config.pop("anchor_file")
        (run / ".assay" / "config.json").write_text(json.dumps(config, sort_keys=True))
        _win(run)
        status = run_cli(run, "status")
        assert f"ANCHORS | {anchors}" in status.stdout and "1 anchor(s)" in status.stdout
        audited = run_cli(run, "audit")
        assert "anchors intact (1)" in audited.stdout
        assert "anchor_env_mismatch" not in audited.stdout
    finally:
        stop_run(run)
