"""Shared plumbing. The end-to-end tests drive the real command line, and so
the real daemon, on the counter world and on a small frame world. Nothing a
test does leaves the pytest temp root, and every daemon a test started is
stopped when the test ends.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
COUNTER = REPO / "worlds" / "counter"
GRID_ADAPTER = Path(__file__).resolve().parent / "grid_adapter.py"

sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session", autouse=True)
def _isolated(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Anchors go under the temp root, never under the home directory."""
    os.environ["ASSAY_ANCHOR_DIR"] = str(tmp_path_factory.getbasetemp() / "anchors")
    yield


class World:
    """One run directory and the command line on it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "assay.cli", "--run-dir", str(self.root), *args],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(SRC)},
        )

    def ok(self, *args: str) -> str:
        """The output of a command that must succeed."""
        done = self.cli(*args)
        assert done.returncode == 0, done.stderr + done.stdout
        return done.stdout

    def refused(self, *args: str) -> str:
        """The refusal of a command that must be refused."""
        done = self.cli(*args)
        assert done.returncode == 2, done.stdout
        return done.stderr

    def start(self, adapter: Path, registry: dict[str, Any] | Path, world_id: str = "demo") -> str:
        if isinstance(registry, dict):
            (self.root / "registry.json").write_text(json.dumps(registry))
            registry = self.root / "registry.json"
        return self.ok("start", world_id, "--adapter", str(adapter), "--registry", str(registry))

    def events(self) -> list[dict[str, Any]]:
        lines = (self.root / ".assay" / "events.jsonl").read_text().splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def write(self, name: str, text: str) -> None:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    """A fresh run directory; its daemon, if one was started, is stopped."""
    run = World(tmp_path / "run")
    yield run
    run.cli("stop")


@pytest.fixture
def counter(world: World) -> World:
    """A started run on the counter world."""
    world.start(COUNTER / "adapter.py", COUNTER / "registry.json")
    return world
