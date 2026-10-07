"""The owner token can be delivered to a file outside the run directory
instead of the starting terminal, which in every benchmark protocol so far was
the agent's own."""

from __future__ import annotations

import json
import re
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


def _start(run: Path, *extra: str):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"), *extra,
    )


def test_token_file_replaces_the_printed_token(tmp_path):
    run = tmp_path / "filed"
    _prepare(run)
    token_file = tmp_path / "keys" / "owner.token"
    try:
        started = _start(run, "--owner-token-file", str(token_file))
        assert started.returncode == 0, started.stderr
        assert f"OWNER TOKEN | written to {token_file} (mode 0600)" in started.stdout
        assert not re.search(r"OWNER TOKEN \| \S{20,} —", started.stdout)
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        token = token_file.read_text().strip()
        assert len(token) >= 20 and token not in started.stdout
        proposed = run_cli(run, "goal", "propose", "win faster", "--because", "test")
        assert proposed.returncode == 0
        assert run_cli(run, "goal", "ratify", "1", "--token", "wrong").returncode == 2
        ratified = run_cli(run, "goal", "ratify", "1", "--token", token)
        assert ratified.returncode == 0, ratified.stderr
    finally:
        stop_run(run)


def test_environment_variable_form_and_default_unchanged(tmp_path, monkeypatch):
    run = tmp_path / "env"
    _prepare(run)
    token_file = tmp_path / "owner.token"
    monkeypatch.setenv("ASSAY_OWNER_TOKEN_FILE", str(token_file))
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        assert "written to" in started.stdout and token_file.exists()
    finally:
        stop_run(run)
    monkeypatch.delenv("ASSAY_OWNER_TOKEN_FILE")
    run2 = tmp_path / "default"
    _prepare(run2)
    try:
        started = _start(run2)
        assert started.returncode == 0, started.stderr
        assert re.search(r"OWNER TOKEN \| \S{20,} — printed once", started.stdout)
    finally:
        stop_run(run2)


def test_token_file_inside_the_run_is_refused(tmp_path):
    run = tmp_path / "inside"
    _prepare(run)
    started = _start(run, "--owner-token-file", str(run / "owner.token"))
    assert started.returncode == 2
    assert "outside the run directory" in started.stderr
    assert not (run / "owner.token").exists()
    assert not (run / ".assay").exists()
