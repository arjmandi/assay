"""The distribution: pyproject names the package and its entry point, the
version has one source, the daemon is a module the installed package can
spawn, and the inline script metadata of the launcher stays in step."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_pyproject_names_the_package_and_its_entry_point():
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    project = data["project"]
    assert project["name"] == "assay-harness"
    assert project["requires-python"] == ">=3.12"
    assert project["scripts"] == {"assay": "assay.cli:main"}
    assert any(dep.startswith("numpy") for dep in project["dependencies"])
    assert not any(dep.startswith("pillow") for dep in project["dependencies"])
    assert any(dep.startswith("pillow") for dep in project["optional-dependencies"]["grid"])
    assert data["tool"]["setuptools"]["packages"] == ["assay", "assay_grid"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "assay.__version__"}


def test_version_has_one_source():
    import assay

    assert re.fullmatch(r"\d+\.\d+\.\d+(\.dev\d+)?", assay.__version__)
    # The launcher's inline metadata agrees with the kernel's floor.
    inline = (REPO / "src" / "assay_cli.py").read_text()
    assert 'requires-python = ">=3.12"' in inline
    assert '"numpy>=2.0,<3"' in inline


def test_daemon_is_a_package_module():
    completed = subprocess.run(
        [sys.executable, "-c", "import assay.broker_server as m; print(m.main.__name__)"],
        capture_output=True, text=True, cwd=str(REPO / "src"), timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "main"
    assert not (REPO / "src" / "broker_server.py").exists()
    source = (REPO / "src" / "assay" / "broker.py").read_text()
    assert '"-m", "assay.broker_server"' in source
