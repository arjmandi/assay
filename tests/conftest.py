"""Shared test plumbing for the ASSAY test suite.

Unit tests import the assay package directly; end-to-end tests drive the real
CLI (and therefore the real broker subprocess) with the fake adapter in this
directory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"
ASSAY_CLI = SRC_DIR / "assay_cli.py"
FAKE_ADAPTER = TESTS_DIR / "fake_adapter.py"

sys.path.insert(0, str(SRC_DIR))


def run_cli(run_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real CLI as a subprocess against a run directory."""
    return subprocess.run(
        [sys.executable, str(ASSAY_CLI), "--run-dir", str(run_dir), *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


def stop_run(run_dir: Path) -> None:
    """Terminate a run's broker process, if one is still alive."""
    from assay.broker import stop_broker
    from assay.core import RunPaths

    stop_broker(RunPaths(Path(run_dir)))


@pytest.fixture
def paths(tmp_path: Path):
    from assay.core import RunPaths

    run_paths = RunPaths(tmp_path)
    run_paths.state.mkdir(parents=True, exist_ok=True)
    return run_paths
