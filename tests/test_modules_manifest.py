"""External modules load only through the manifest: registered at start or
installed by the owner. A file dropped into .assay/modules by hand, or a pinned
file edited afterwards, is ignored and reported, never loaded."""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
COVERAGE = REPO / "bench" / "arcagi" / "modules" / "coverage_audit.py"
ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]

ROGUE = '''
class _Rogue:
    NAME = "rogue"
    CONSTITUTION = "a module nobody installed"
    MODE = "advise"

    def trigger(self, view, pending):
        return "rogue fired"

    def demand(self, view, pending):
        return None

    def telemetry(self, view):
        return {}


MODULE = _Rogue()
'''

CLASHER = ROGUE.replace('NAME = "rogue"', 'NAME = "hazard"')


def _prepare(run: Path, modules: list[str] | None = None) -> None:
    run.mkdir()
    spec = {"actions": ACTIONS, "budget": {"actions": 30}}
    if modules:
        spec["modules"] = modules
    (run / "reg.json").write_text(json.dumps(spec))


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _token(stdout: str) -> str:
    found = re.search(r"OWNER TOKEN \| (\S+) —", stdout)
    assert found, stdout
    return found.group(1)


def _manifest(run: Path) -> dict:
    return json.loads((run / ".assay" / "modules" / "manifest.json").read_text())


def test_registry_modules_are_pinned_by_manifest_and_strangers_ignored(tmp_path):
    run = tmp_path / "manifest"
    _prepare(run, modules=[str(COVERAGE)])
    try:
        assert _start(run).returncode == 0
        entries = _manifest(run)["modules"]
        assert [entry["name"] for entry in entries] == ["coverage_audit"]
        assert entries[0]["origin"] == "registry" and len(entries[0]["sha256"]) == 64
        # A file written into the directory by hand never loads.
        (run / ".assay" / "modules" / "rogue.py").write_text(ROGUE)
        for _ in range(3):
            assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        status = run_cli(run, "status")
        assert "rogue fired" not in status.stdout
        assert "MODULES | 1 file(s) in .assay/modules ignored" in status.stdout
        assert "rogue.py (not in the manifest)" in status.stdout
        assert "MODULE coverage_audit |" in status.stdout  # the listed one runs
        listed = run_cli(run, "module", "list")
        assert "coverage_audit | advise | registry" in listed.stdout
        assert "rogue.py (not in the manifest)" in listed.stdout
        # Editing the pinned copy breaks its hash: ignored and reported.
        pinned = run / ".assay" / "modules" / "coverage_audit.py"
        pinned.write_text(pinned.read_text() + "\n# edited\n")
        status = run_cli(run, "status")
        assert "MODULE coverage_audit |" not in status.stdout
        assert "coverage_audit.py (modified since install)" in status.stdout
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)


def test_owner_install_is_the_sanctioned_channel(tmp_path):
    run = tmp_path / "install"
    _prepare(run)
    (tmp_path / "rogue.py").write_text(ROGUE)
    (tmp_path / "clasher.py").write_text(CLASHER)
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        token = _token(started.stdout)
        denied = run_cli(run, "module", "install", str(tmp_path / "rogue.py"))
        assert denied.returncode == 2 and "owner authority" in denied.stderr
        assert not (run / ".assay" / "modules" / "rogue.py").exists()
        wrong = run_cli(run, "module", "install", str(tmp_path / "rogue.py"), "--token", "nope")
        assert wrong.returncode == 2
        clash = run_cli(run, "module", "install", str(tmp_path / "clasher.py"), "--token", token)
        assert clash.returncode == 2 and "built-in" in clash.stderr
        assert not (run / ".assay" / "modules" / "clasher.py").exists()
        installed = run_cli(run, "module", "install", str(tmp_path / "rogue.py"), "--token", token)
        assert installed.returncode == 0, installed.stderr
        assert "MODULE | installed rogue" in installed.stdout
        entries = _manifest(run)["modules"]
        assert entries[-1]["name"] == "rogue" and entries[-1]["origin"] == "install"
        activity = (run / ".assay" / "activity.jsonl").read_text()
        assert '"kind":"module_installed"' in activity and '"name":"rogue"' in activity
        acted = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert acted.returncode == 0 and "MODULE rogue | rogue fired" in acted.stdout
        listed = run_cli(run, "module", "list")
        assert "rogue | advise | install" in listed.stdout
        assert "ignored" not in listed.stdout
    finally:
        stop_run(run)


def test_run_without_a_manifest_reconstructs_it_from_the_registry(tmp_path):
    """A run started before 1.1.0 pinned files without a manifest. Its
    registry names them, so the manifest is rebuilt from the pinned copies and
    the module keeps running. Anything else in the directory stays unlisted."""
    run = tmp_path / "legacy"
    _prepare(run, modules=[str(COVERAGE)])
    try:
        assert _start(run).returncode == 0
        (run / ".assay" / "modules" / "manifest.json").unlink()
        (run / ".assay" / "modules" / "rogue.py").write_text(ROGUE)
        for _ in range(3):
            assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        status = run_cli(run, "status")
        assert "MODULE coverage_audit |" in status.stdout
        assert "rogue fired" not in status.stdout
        assert "rogue.py (not in the manifest)" in status.stdout
        entries = _manifest(run)["modules"]
        assert entries[0]["name"] == "coverage_audit" and entries[0]["reconstructed"] is True
    finally:
        stop_run(run)


def test_registry_module_with_a_builtin_name_is_refused_at_start(tmp_path):
    run = tmp_path / "clash"
    (tmp_path / "clasher.py").write_text(CLASHER)
    _prepare(run, modules=[str(tmp_path / "clasher.py")])
    started = _start(run)
    assert started.returncode == 2
    assert "built-in" in started.stderr
    assert not (run / ".assay").exists()  # nothing half-initialized left behind
