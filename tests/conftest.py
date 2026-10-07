"""Shared test plumbing for the ASSAY test suite.

Unit tests import the assay package directly; end-to-end tests drive the real
CLI (and therefore the real broker subprocess) with the adapters in this
directory. Nothing a test does leaves the pytest temp root: anchors and caches
are redirected there for the whole session, and any daemon still serving a
directory under it is stopped when the session ends.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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


def _daemons_under(root: Path) -> list[int]:
    """Pids of every daemon whose --run-dir lies under root."""
    try:
        listing = subprocess.run(
            ["ps", "-ww", "-eo", "pid=,command="], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[int] = []
    for line in listing.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit() or "broker_server" not in parts[1]:
            continue
        marker = "--run-dir "
        if marker not in parts[1]:
            continue
        run_dir = parts[1].split(marker, 1)[1].strip()
        if run_dir.startswith(str(root)):
            found.append(int(parts[0]))
    return found


@pytest.fixture(scope="session", autouse=True)
def _isolated_session(tmp_path_factory: pytest.TempPathFactory):
    """Anchors and caches under the temp root, never under the home directory,
    and no daemon outlives the session."""
    root = tmp_path_factory.getbasetemp()
    previous = {key: os.environ.get(key) for key in ("ASSAY_ANCHOR_DIR", "XDG_CACHE_HOME")}
    os.environ["ASSAY_ANCHOR_DIR"] = str(root / "anchors")
    os.environ["XDG_CACHE_HOME"] = str(root / "cache")
    try:
        yield
    finally:
        for pid in _daemons_under(root):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _daemons_under(root):
            time.sleep(0.1)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def paths(tmp_path: Path):
    from assay.core import RunPaths

    run_paths = RunPaths(tmp_path)
    run_paths.state.mkdir(parents=True, exist_ok=True)
    return run_paths


def event_of(**overrides: Any) -> Any:
    """A valid dict-world event with every required key, for the unit tests:
    the overrides replace the defaults, and an optional key given here is
    present on the line. `grade` may be given as Grade records; `frames` as
    rows, which makes a frame-world event."""
    from assay.records import Event, Grade

    raw: dict[str, Any] = {
        "id": 1,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "action": "INC",
        "data": {"amount": 1},
        "counts_action": True,
        "state": "NOT_FINISHED",
        "levels_completed": 0,
        "level_before": 0,
        "win_levels": 1,
        "available_actions": ["INC", "NOOP"],
        "note": "",
        "observation": {"counter": 0},
        **overrides,
    }
    if "grade" in raw and raw["grade"] is None:
        # No grade on the line: the journal never carries a null one.
        del raw["grade"]
    if raw.get("grade") is not None:
        # A dict grade given with only the keys a test looks at gets the
        # journal's other required keys around them.
        raw["grade"] = [
            item.to_json()
            if isinstance(item, Grade)
            else {"text": str(item.get("kind", "")), "actual": "", "bucket": "world_model", **item}
            for item in raw["grade"]
        ]
    if raw.get("frames") is not None:
        raw["frames"] = [list(frame) for frame in raw["frames"]]
        raw.setdefault("n_frames", len(raw["frames"]))
        if "observation" not in overrides:
            raw.pop("observation", None)
    return Event.from_json(raw)


def journal_head(paths: Any) -> str:
    """The chain head over a journal file, by the rule of
    verify/JOURNAL_SPEC.md section 4, kept here so the tests hold the kernel
    to the spec and not to itself."""
    import hashlib

    head = hashlib.sha256(b"assay-chain-v1").hexdigest()
    try:
        lines = paths.events.read_text().splitlines()
    except FileNotFoundError:
        return head
    for line in lines:
        if line.strip():
            head = hashlib.sha256(head.encode() + line.encode()).hexdigest()
    return head


def run_of(paths: Any, events: Sequence[Any] = (), registry: dict[str, Any] | None = None) -> Any:
    """An in-memory run over these events, for the unit tests: nothing is
    read from disk and the loader is not involved."""
    from assay.run import Run

    return Run(
        paths=paths,
        config={"game_id": "test", "mode": "local"},
        registry=registry,
        events=list(events),
    )
