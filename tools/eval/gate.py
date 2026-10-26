"""The Stop gate as both runners install it: the gate's module loaded by
path (`hooks/stop_gate.py`, so a runner applies the gate's own rules) and its
Stop entry merged into a run directory's `.claude/settings.json`.

The session runner (`session.py`) installs it for every `claude` player; the
batch runner (`runner.py`) installs it when the job file asks (`stop_gate`).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from . import EvalError

REPO = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO / "hooks" / "stop_gate.py"
# The gate's entry in the settings, by its command text.
GATE_ENTRY = re.compile(r"stop_gate\.py'? --run-dir ")


def load_gate() -> ModuleType:
    """The Stop gate's module, loaded by path: the runner applies its rules."""
    name = "assay_stop_gate"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, GATE_SCRIPT)
    if spec is None or spec.loader is None:
        raise EvalError(f"the Stop gate {GATE_SCRIPT} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def gate_command(run_dir: Path) -> str:
    return shlex.join([sys.executable, str(GATE_SCRIPT), "--run-dir", str(run_dir)])


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def install_stop_gate(settings_path: Path, command: str) -> None:
    """Merge the gate's Stop entry into the run directory's settings: every
    other key and entry kept, an earlier gate entry replaced, no write
    through a symlink."""
    for candidate in (settings_path.parent, settings_path):
        if candidate.is_symlink():
            raise EvalError(f"{candidate} is a symlink, and the gate writes through none")
    settings = _read_json(settings_path) if settings_path.exists() else {}
    if not isinstance(settings, dict):
        raise EvalError(f"{settings_path} does not hold a JSON object; fix it by hand")
    hooks = settings.get("hooks", {})
    stop = hooks.get("Stop", []) if isinstance(hooks, dict) else None
    if not isinstance(stop, list):
        raise EvalError(f"{settings_path}: `hooks` or `hooks.Stop` has the wrong type; fix it by hand")
    kept = []
    for entry in stop:
        items = entry.get("hooks") if isinstance(entry, dict) else None
        if not isinstance(items, list):
            kept.append(entry)
            continue
        mine = [
            item for item in items
            if isinstance(item, dict) and isinstance(item.get("command"), str) and GATE_ENTRY.search(item["command"])
        ]
        rest = [item for item in items if item not in mine]
        if rest:
            kept.append({**entry, "hooks": rest})
    ours = {"hooks": [{"type": "command", "command": command, "timeout": 60}]}
    _write_json(settings_path, {**settings, "hooks": {**hooks, "Stop": [*kept, ours]}})
