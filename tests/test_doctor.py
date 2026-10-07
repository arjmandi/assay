"""`assay doctor`, the interpreter recorded at start and checked on resume, and
the launcher's ASSAY_PYTHON pin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "bin" / "assay"
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


def test_doctor_without_a_run(tmp_path):
    checked = run_cli(tmp_path, "doctor")
    assert checked.returncode == 0, checked.stderr
    out = checked.stdout
    assert f"DOCTOR | ok | python {sys.version_info.major}.{sys.version_info.minor}" in out
    assert "DOCTOR | ok | numpy 2." in out and "DOCTOR | ok | pillow 1" in out
    assert "DOCTOR | ok | socket path" in out
    assert "no run in" in out
    assert "anchor directory" in out
    assert out.strip().endswith("0 failure(s), 0 warning(s)")
    assert not (tmp_path / ".assay").exists()


def test_doctor_on_a_run_and_after_stop(tmp_path):
    run = tmp_path / "doc"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        assert _config(run)["python"] == sys.executable
        checked = run_cli(run, "doctor")
        assert checked.returncode == 0, checked.stderr
        out = checked.stdout
        assert "DOCTOR | ok | run fake1 | mode local | 1 events | last e0 NOT_FINISHED" in out
        assert "alive, identified, answering" in out
        assert f"DOCTOR | ok | adapter {FAKE_ADAPTER}:factory imports" in out
        assert "DOCTOR | ok | registry valid, 2 actions" in out
        assert "anchor(s)" in out or "none yet" in out
        assert run_cli(run, "stop").returncode == 0
        checked = run_cli(run, "doctor")
        assert checked.returncode == 0
        assert "daemon not running (broker.json says STOPPED)" in checked.stdout
        # A broken adapter path in the config is a FAIL with the reason.
        config = _config(run)
        config["adapter"] = "/nowhere/world.py:factory"
        (run / ".assay" / "config.json").write_text(json.dumps(config, sort_keys=True))
        checked = run_cli(run, "doctor")
        assert checked.returncode == 2
        assert "DOCTOR | FAIL | adapter: adapter file not found" in checked.stdout
        assert "1 failure(s)" in checked.stdout
    finally:
        stop_run(run)


def test_resume_warns_when_the_interpreter_changed(tmp_path):
    run = tmp_path / "drift"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        config = _config(run)
        config["python"] = "/some/other/venv/bin/python"
        (run / ".assay" / "config.json").write_text(json.dumps(config, sort_keys=True))
        checked = run_cli(run, "doctor")
        assert "DOCTOR | WARN | run started with /some/other/venv/bin/python" in checked.stdout
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        assert "WARNING | interpreter changed: the run started with /some/other/venv/bin/python" in resumed.stdout
        assert "set ASSAY_PYTHON" in resumed.stdout
    finally:
        stop_run(run)


def test_launcher_honors_assay_python(tmp_path):
    env = {**os.environ, "ASSAY_PYTHON": sys.executable}
    checked = subprocess.run(
        [str(LAUNCHER), "--run-dir", str(tmp_path), "doctor"],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert checked.returncode == 0, checked.stderr
    assert f"python {sys.version_info.major}.{sys.version_info.minor}" in checked.stdout
    assert f"at {sys.executable}" in checked.stdout
    assert f"ASSAY_PYTHON={sys.executable}" in checked.stdout
    # A pin that fails the fingerprint is an error, never a silent fallback.
    env["ASSAY_PYTHON"] = "/bin/false"
    refused = subprocess.run(
        [str(LAUNCHER), "--run-dir", str(tmp_path), "doctor"],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert refused.returncode == 126
    assert "ASSAY_PYTHON=/bin/false is not a Python 3.12+ with numpy 2.x" in refused.stderr
