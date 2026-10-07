"""The sandbox (design note 3, section 8.5): what it refuses, what it still
lets through, and the fallback it reports.

Every refusal is asserted on the grading result: INVALID with the error the
sandbox raised, never merely a kernel that survived. The reading verifier that
fails under the sandbox passes under the forced fallback, which is what proves
the failure is the sandbox's and not the test's. The fallback is only ever
forced (`ASSAY_SANDBOX=process-isolation-only`); nothing here runs a profile
that could abort the loader."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ASSAY_CLI, FAKE_ADAPTER, run_cli, stop_run
from assay import sandbox
from assay.channels import channel_value, declare_channel
from assay.sandbox import (
    BWRAP,
    FORCE_VARIABLE,
    PROCESS_ISOLATION_ONLY,
    SANDBOX_EXEC,
    run_program,
    sandbox_mode,
    sandbox_text,
)
from assay.verifiers import admit_verifier, grade_verifier_claim

BEFORE = {
    "state": "NOT_FINISHED",
    "levels_completed": 0,
    "win_levels": 1,
    "available_actions": ["INC", "NOOP"],
    "data": {"counter": 0, "lamp": "off"},
}
AFTER = {**BEFORE, "data": {"counter": 1, "lamp": "off"}}
EVENT = {**BEFORE, "observation": BEFORE["data"]}

MODE = sandbox_mode()
# The errors a refusal surfaces as: EPERM from sandbox-exec, ENOENT from a
# bwrap namespace that does not hold the file, EAGAIN from RLIMIT_NPROC, and
# the socket errors of an unshared network.
REFUSALS = ("PermissionError", "FileNotFoundError", "BlockingIOError", "ConnectionRefusedError", "OSError")

jailed = pytest.mark.skipif(
    MODE == PROCESS_ISOLATION_ONLY,
    reason="process isolation only here: the filesystem and network refusals need sandbox-exec or bwrap",
)
linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the memory limit is Linux-only")
darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="the sandbox-exec profile is macOS")

READS_JOURNAL = """\
def verify(before, after):
    with open({events!r}) as handle:
        return True, handle.read().strip()
"""

WRITES_RUN_DIR = """\
def verify(before, after):
    with open({target!r}, "w") as handle:
        handle.write("written by a verifier")
    return True, "wrote"
"""

CONNECTS = """\
import socket

def verify(before, after):
    with socket.socket() as sock:
        sock.connect(("127.0.0.1", {port}))
    return True, "connected"
"""

FORKS = """\
import subprocess

def verify(before, after):
    subprocess.run(["true"], check=True)
    return True, "forked"
"""

ALLOCATES = """\
def verify(before, after):
    block = bytearray({size})
    return True, f"allocated {{len(block)}}"
"""

NUMPY = """\
import json
import numpy

def verify(before, after):
    counters = numpy.array([before["data"]["counter"], after["data"]["counter"]])
    return bool(counters[1] > counters[0]), json.dumps(numpy.diff(counters).tolist())
"""

JOURNAL_LINE = '{"id": 0, "action": "START"}'


def _graded(paths, body: str) -> dict:
    source = paths.root / "check.py"
    source.write_text(body)
    claim = {"kind": "verify", "text": "verify:check.py", "path": "check.py"}
    admit_verifier(paths, claim)
    return grade_verifier_claim(paths, claim, BEFORE, AFTER)


def _refused(graded: dict) -> str:
    """The grading is INVALID because the sandbox raised, and says which error."""
    assert graded["invalid"] is True and graded["ok"] is False, graded
    actual = graded["actual"]
    assert actual.startswith("INVALID_CLAIM: verifier crashed"), actual
    assert any(error in actual for error in REFUSALS), actual
    return actual


def test_mode_is_the_platform_tool():
    if sys.platform == "darwin":
        assert MODE == SANDBOX_EXEC, sandbox_text(MODE)
    elif shutil.which(BWRAP):
        assert MODE == BWRAP, sandbox_text(MODE)
    else:
        assert MODE == PROCESS_ISOLATION_ONLY
        assert sandbox_text(MODE).startswith("process isolation only (no sandbox-exec or bwrap)")


@jailed
def test_reading_the_journal_is_refused(paths):
    # The path is computed here, from the run directory: the program no
    # longer receives one, and this is the file an agent would want.
    paths.events.write_text(JOURNAL_LINE + "\n")
    _refused(_graded(paths, READS_JOURNAL.format(events=str(paths.events))))


@jailed
def test_writing_the_run_directory_is_refused(paths):
    target = paths.root / "written-by-a-verifier.txt"
    _refused(_graded(paths, WRITES_RUN_DIR.format(target=str(target))))
    assert not target.exists()


@jailed
def test_opening_a_socket_is_refused(paths):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        _refused(_graded(paths, CONNECTS.format(port=port)))
        with pytest.raises(BlockingIOError):  # nothing reached the listener
            listener.accept()


def test_forking_is_refused(paths):
    # RLIMIT_NPROC 1 holds in every mode, the fallback included.
    _refused(_graded(paths, FORKS))


@linux_only
def test_allocating_past_the_limit_is_killed(paths):
    graded = _graded(paths, ALLOCATES.format(size=2 * sandbox.ADDRESS_SPACE_LIMIT))
    assert graded["invalid"] is True and graded["ok"] is False, graded
    assert "MemoryError" in graded["actual"], graded["actual"]


def test_numpy_and_json_import_inside_the_sandbox(paths):
    graded = _graded(paths, NUMPY)
    assert "invalid" not in graded, graded["actual"]
    assert graded["ok"] is True and graded["actual"] == "[1]"


@jailed
def test_an_extractor_cannot_read_the_journal(paths):
    paths.events.write_text(JOURNAL_LINE + "\n")
    (paths.root / "peek.py").write_text(
        f"def extract(obs):\n    return open({str(paths.events)!r}).read()\n"
    )
    declare_channel(paths, "peek", file="peek.py")
    ok, value = channel_value(paths, "peek", EVENT)
    assert ok is False and value.startswith("extractor crashed"), value
    assert any(error in value for error in REFUSALS), value


def test_the_reading_verifier_passes_under_the_forced_fallback(paths, monkeypatch):
    paths.events.write_text(JOURNAL_LINE + "\n")
    monkeypatch.setenv(FORCE_VARIABLE, PROCESS_ISOLATION_ONLY)
    monkeypatch.setattr(sandbox, "_DECISION", None)
    assert sandbox_mode() == PROCESS_ISOLATION_ONLY
    assert sandbox_text(PROCESS_ISOLATION_ONLY).startswith(
        "process isolation only (forced by ASSAY_SANDBOX)"
    )
    graded = _graded(paths, READS_JOURNAL.format(events=str(paths.events)))
    assert "invalid" not in graded, graded["actual"]
    assert graded["ok"] is True and graded["actual"] == JOURNAL_LINE


def test_run_program_copies_companions_and_rewrites_their_paths(tmp_path):
    source = tmp_path / "companion.py"
    source.write_text("VALUE = 7\n")
    program = (
        "import json, os, sys\n"
        "payload = json.loads(sys.stdin.read())\n"
        "path = payload['file']\n"
        "print(json.dumps({'path': path, 'cwd': os.getcwd(), 'text': open(path).read(),"
        " 'beside': sorted(os.listdir('.')), 'obs': payload['obs']}))\n"
    )
    payload = {"file": str(source), "obs": {"note": str(source)}}
    outcome = run_program(program, payload, timeout=10.0, companions=(source,))
    assert outcome["status"] == "ok", outcome
    result = outcome["result"]
    assert result["text"] == "VALUE = 7\n"
    assert result["path"] != str(source) and result["path"].startswith(result["cwd"])
    assert result["beside"] == ["companion.py", "program.py"]
    assert result["obs"] == {"note": result["path"]}  # a whole string equal to the path
    assert not Path(result["cwd"]).exists()  # the scratch directory is gone
    missing = run_program(program, payload, timeout=10.0, companions=(tmp_path / "absent.py",))
    assert missing["status"] == "invalid" and missing["reason"].startswith("file missing")


def test_run_program_status_records():
    assert run_program("print('{\"ok\": true}')", {}, timeout=10.0) == {
        "status": "ok",
        "result": {"ok": True},
    }
    crashed = run_program("raise SystemExit(3)", {}, timeout=10.0)
    assert crashed["status"] == "invalid" and crashed["reason"].startswith("crashed (exit 3)")
    assert run_program("pass", {}, timeout=10.0) == {"status": "invalid", "reason": "produced no output"}
    malformed = run_program("print('not json')", {}, timeout=10.0)
    assert malformed["reason"].startswith("produced malformed output")
    assert run_program("while True: pass", {}, timeout=1.0) == {
        "status": "invalid",
        "reason": "timed out after 1s",
    }


def test_limits_and_environment_inside():
    program = (
        "import json, os, resource, sys\n"
        "names = ('RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_NPROC')\n"
        "limits = {name: resource.getrlimit(getattr(resource, name)) for name in names}\n"
        "print(json.dumps({'limits': limits, 'isolated': sys.flags.isolated,"
        " 'env': sorted(key for key in os.environ if not key.startswith('__'))}))\n"
    )
    outcome = run_program(program, {}, timeout=7.5)
    assert outcome["status"] == "ok", outcome
    result = outcome["result"]
    assert result["limits"]["RLIMIT_CPU"] == [8, 9]
    assert result["limits"]["RLIMIT_FSIZE"] == [1 << 20, 1 << 20]
    assert result["limits"]["RLIMIT_NPROC"] == [1, 1]
    assert result["isolated"] == 1
    assert set(result["env"]) <= {"LC_CTYPE", *sandbox.THREAD_CAP}
    big = run_program("open('big.bin', 'wb').write(b'x' * (2 << 20))\nprint('{}')", {}, timeout=10.0)
    assert big["status"] == "invalid" and "File too large" in big["reason"], big


@darwin_only
def test_profile_lists_existing_paths_each_with_a_reason(tmp_path):
    scratch = Path(os.path.realpath(tmp_path))
    profile, omitted = sandbox.darwin_profile(scratch)
    lines = profile.splitlines()
    assert lines[:3] == ["(version 1)", "(deny default)", "(deny network*)"]
    for index, line in enumerate(lines):
        if line.startswith("(allow"):
            assert lines[index - 1].startswith("; "), line
    quoted = re.findall(r'\(allow [^ ]+ \((?:subpath|literal) "([^"]+)"\)\)', profile)
    assert quoted and all(os.path.exists(path) for path in quoted), quoted
    assert not any(os.path.exists(path) for path in omitted), omitted
    writes = re.findall(r'\(allow file-write\* \((subpath|literal) "([^"]+)"\)\)', profile)
    assert sorted(writes) == [("literal", "/dev/null"), ("subpath", str(scratch))]


def _cli(run: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ASSAY_CLI), "--run-dir", str(run), *args],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, **env},
    )


def _start_args(run: Path) -> list[str]:
    (run / "reg.json").write_text(
        json.dumps({"actions": [{"name": "NOOP", "params": {}}], "budget": {"actions": 5}})
    )
    return ["start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory", "--registry", str(run / "reg.json")]


def _config(run: Path) -> dict:
    return json.loads((run / ".assay" / "config.json").read_text())


def test_doctor_prints_the_mode_and_start_records_it(tmp_path):
    checked = run_cli(tmp_path, "doctor")
    assert checked.returncode == 0, checked.stderr
    level = "WARN" if MODE == PROCESS_ISOLATION_ONLY else "ok"
    assert f"DOCTOR | {level} | sandbox | {sandbox_text(MODE)}" in checked.stdout, checked.stdout
    assert "DOCTOR | ok | numpy and json import inside the sandbox" in checked.stdout
    forced = _cli(tmp_path, "doctor", **{FORCE_VARIABLE: PROCESS_ISOLATION_ONLY})
    assert forced.returncode == 0, forced.stderr
    assert (
        "DOCTOR | WARN | sandbox | process isolation only (forced by ASSAY_SANDBOX)"
        in forced.stdout
    ), forced.stdout
    run = tmp_path / "run"
    run.mkdir()
    try:
        started = run_cli(run, *_start_args(run))
        assert started.returncode == 0, started.stderr
        assert _config(run)["sandbox"] == MODE
    finally:
        stop_run(run)
    forced_run = tmp_path / "forced"
    forced_run.mkdir()
    try:
        started = _cli(forced_run, *_start_args(forced_run), **{FORCE_VARIABLE: PROCESS_ISOLATION_ONLY})
        assert started.returncode == 0, started.stderr
        assert _config(forced_run)["sandbox"] == PROCESS_ISOLATION_ONLY
    finally:
        stop_run(forced_run)
    other = tmp_path / "other"
    other.mkdir()
    refused = _cli(other, *_start_args(other), **{FORCE_VARIABLE: "off"})
    assert refused.returncode == 2
    assert "ASSAY_SANDBOX" in refused.stderr and "process-isolation-only" in refused.stderr
    assert not (other / ".assay" / "config.json").exists()
