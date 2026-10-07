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
import time
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
from assay.verifiers import VERIFY_TIMEOUT_SECONDS, admit_verifier, grade_verifier_claim

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
# The errno texts a refusal surfaces as: EPERM from sandbox-exec, ENOENT from a
# bwrap namespace that does not hold the file, EAGAIN from RLIMIT_NPROC, and
# the socket errors of an unshared network (a loopback nobody listens on, or
# no route at all). A bare exception class name would also accept an
# unrelated error, so none is listed.
REFUSALS = (
    "Operation not permitted",
    "No such file or directory",
    "Resource temporarily unavailable",
    "Connection refused",
    "Network is unreachable",
)

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

EXECS = """\
import os

def verify(before, after):
    os.execv({binary!r}, [{binary!r}, "-c", "echo shell"] if {binary!r}.endswith("sh") else [{binary!r}])
    return True, "never reached"
"""

READS_PROCARGS_BY_NAME = """\
import ctypes
import ctypes.util
import json

def verify(before, after):
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    size = ctypes.c_size_t(1 << 16)
    buffer = ctypes.create_string_buffer(size.value)
    code = libc.sysctlbyname(b"kern.procargs2", buffer, ctypes.byref(size), None, 0)
    got = buffer.raw[: size.value] if code == 0 else b""
    return True, json.dumps({"code": code, "errno": ctypes.get_errno() if code else 0, "bytes": len(got)})
"""

PRINTS_PAST_THE_CAP = """\
import sys
import time

def verify(before, after):
    sys.stdout.write("x" * (2 << 20))
    sys.stdout.flush()
    time.sleep(30)
    return True, "never reached"
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
        # A stock Ubuntu 24.04 has bwrap on PATH and AppArmor refusing it a
        # user namespace: then the probe fails and the doctor line says so.
        probed, reason = sandbox._probe(BWRAP)
        assert probed == MODE, (probed, reason, sandbox_text(MODE))
        if MODE != BWRAP:
            assert MODE == PROCESS_ISOLATION_ONLY
            assert "the bwrap probe failed" in sandbox_text(MODE), sandbox_text(MODE)
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


@jailed
@pytest.mark.parametrize("binary", ["/bin/sh", "/bin/ls"])
def test_exec_outside_the_interpreter_is_refused(paths, binary):
    # sandbox-exec allows process-exec on the interpreter and its prefixes
    # alone; under bwrap no /bin is bound, so neither binary is there.
    _refused(_graded(paths, EXECS.format(binary=binary)))


@darwin_only
@jailed
def test_the_process_argument_keys_are_denied_by_name(paths):
    # The named route to another process's arguments and environment: the
    # deny after the unfiltered allow wins. The MIB route is the OS's own
    # same-uid policy and stays open (sandbox.py says so where the rule is).
    graded = _graded(paths, READS_PROCARGS_BY_NAME)
    assert "invalid" not in graded, graded["actual"]
    result = json.loads(graded["actual"])
    # Darwin 25 answers the denied read with EPERM; another macOS answers
    # EINVAL (the runners did): either way the call fails and not one byte
    # of the parent's arguments or environment comes back.
    assert result["code"] != 0 and result["errno"] in (1, 22), result
    assert result["bytes"] == 0, result


def test_output_past_the_cap_is_refused_at_once(paths):
    started = time.monotonic()
    graded = _graded(paths, PRINTS_PAST_THE_CAP)
    elapsed = time.monotonic() - started
    assert graded["invalid"] is True and graded["ok"] is False, graded
    assert graded["actual"] == "INVALID_CLAIM: verifier produced more than 1 MB of output", graded
    assert elapsed < 2 * VERIFY_TIMEOUT_SECONDS  # killed at the cap, not at the wall clock


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
    assert crashed == {
        "status": "invalid",
        "kind": "crash",
        "reason": "crashed (exit 3): no stderr",
        "exit": 3,
        "tail": "no stderr",
    }
    assert run_program("pass", {}, timeout=10.0) == {
        "status": "invalid",
        "kind": "no_output",
        "reason": "produced no output",
    }
    assert run_program("print('not json')", {}, timeout=10.0) == {
        "status": "invalid",
        "kind": "malformed",
        "reason": "malformed output: 'not json'",
        "output": "not json",
    }
    assert run_program("while True: pass", {}, timeout=1.0) == {
        "status": "invalid",
        "kind": "timeout",
        "reason": "timed out after 1s",
    }


def test_limits_and_environment_inside():
    program = (
        "import json, os, resource, sys\n"
        "names = ('RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_NPROC')\n"
        "limits = {name: resource.getrlimit(getattr(resource, name)) for name in names}\n"
        "print(json.dumps({'limits': limits, 'isolated': sys.flags.isolated, 'cwd': os.getcwd(),"
        " 'env': {key: value for key, value in os.environ.items() if not key.startswith('__')}}))\n"
    )
    outcome = run_program(program, {}, timeout=7.5)
    assert outcome["status"] == "ok", outcome
    result = outcome["result"]
    assert result["limits"]["RLIMIT_CPU"] == [8, 9]
    assert result["limits"]["RLIMIT_FSIZE"] == [1 << 20, 1 << 20]
    assert result["limits"]["RLIMIT_NPROC"] == [1, 1]
    assert result["isolated"] == 1
    # The environment is empty but for the thread cap, the locale coercion,
    # and under bwrap the PWD its --chdir exports, which must be the scratch
    # directory the program runs in.
    extra = set(result["env"]) - {"LC_CTYPE", *sandbox.THREAD_CAP}
    assert extra <= ({"PWD"} if MODE == BWRAP else set()), result["env"]
    if "PWD" in result["env"]:
        assert result["env"]["PWD"] == result["cwd"]
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
    quoted = re.findall(r'\((?:subpath|literal) "([^"]+)"\)', profile)
    assert quoted and all(os.path.exists(path) for path in quoted), quoted
    assert not any(os.path.exists(path) for path in omitted), omitted
    writes = re.findall(r'\(allow file-write\* \((subpath|literal) "([^"]+)"\)\)', profile)
    assert sorted(writes) == [("literal", "/dev/null"), ("subpath", str(scratch))]
    assert "mach-lookup" not in profile
    execs = [line for line in lines if line.startswith("(allow process-exec")]
    assert len(execs) == 1 and "process-exec*" not in execs[0]
    assert f'(literal "{sys.executable}")' in execs[0]
    assert f'(literal "{os.path.realpath(sys.executable)}")' in execs[0]
    allowed_exec = {
        ("literal", sys.executable),
        ("literal", os.path.realpath(sys.executable)),
        ("subpath", os.path.realpath(sys.prefix)),
        ("subpath", os.path.realpath(sys.base_prefix)),
    }
    assert set(re.findall(r'\((literal|subpath) "([^"]+)"\)', execs[0])) <= allowed_exec, execs[0]
    denies = [line for line in lines if line.startswith("(deny ")]
    assert denies == [
        "(deny default)",
        "(deny network*)",
        '(deny sysctl-read (sysctl-name-prefix "kern.procargs"))',
    ]
    assert lines.index(denies[-1]) > lines.index("(allow sysctl-read)")


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
        broker = json.loads((run / ".assay" / "broker.json").read_text())
        assert broker["status"] == "READY" and broker["sandbox"] == MODE
        checked = run_cli(run, "doctor")
        assert checked.returncode == 0, checked.stderr
        assert "sandbox recorded at the run's creation" not in checked.stdout
        # A run created under another mode: doctor says so, twice (the
        # daemon's mode and this shell's both differ from the record).
        config = _config(run)
        config["sandbox"] = "another-jail"
        (run / ".assay" / "config.json").write_text(json.dumps(config, sort_keys=True))
        checked = run_cli(run, "doctor")
        assert (
            f"DOCTOR | WARN | sandbox recorded at the run's creation is another-jail, the daemon runs with {MODE}"
            in checked.stdout
        ), checked.stdout
        assert (
            f"DOCTOR | WARN | sandbox recorded at the run's creation is another-jail, this shell decides {MODE}"
            in checked.stdout
        ), checked.stdout
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
    # The same refusal on a resume, before the resume branch runs.
    resumed = _cli(forced_run, "start", "fake1", **{FORCE_VARIABLE: "off"})
    assert resumed.returncode == 2 and "ASSAY_SANDBOX" in resumed.stderr
