"""The sandbox: the one place agent-authored code runs (design note 3, 8.5).

Verifiers (`verifiers.run_verifier`), state extractors
(`states._run_extractor`) and the world model (`model._run_sandbox`) hand
`run_program` the source of their runner (the program), the JSON payload the
runner reads from stdin, and the files the runner loads (the companions: the
stored verifier or extractor, `model.py` and the extractor files of the
model's declared states). The program and its companions are copied into a
fresh scratch directory, every payload string naming a companion is rewritten
to the copy, and the program runs from there as `python -I` (isolated: no
PYTHONPATH, no user site, the working directory off the path; not `-S`, which
would drop site-packages and numpy with them) with an empty environment, the
scratch directory as the working directory, the payload on stdin and one JSON
line on stdout. Nothing the program receives names the run directory. The
kernel reads at most OUTPUT_CAP bytes of stdout and of stderr and kills the
program past either, so a program cannot push gigabytes into the daemon
within its CPU budget.

The process limits are set by a prelude the program starts with, inside the
interpreter and before any agent code, because the sandbox tools create the
process (bwrap forks a helper) before the interpreter starts: RLIMIT_CPU at
the timeout rounded up, or the caller's `cpu_seconds` (the hard limit one
second later; SIGXCPU is handled in Python, so a runaway that leaves the
handler in place ends with a message rather than a core-dump signal, which
macOS would report in a dialog; a program that resets the handler or calls
os.abort can still die by one, and the wall clock is the backstop either
way), RLIMIT_FSIZE 1 MB, RLIMIT_NPROC 1 (no fork, no spawn), on Linux
RLIMIT_AS 512 MB (macOS refuses an address-space limit, so the memory limit
is Linux-only), and a one-thread cap for BLAS (on Linux RLIMIT_NPROC counts
threads, and numpy's OpenBLAS creates its pool at import). The wall clock is
the caller's timeout, enforced by the kernel process with SIGKILL.

Three modes, decided once per process by `sandbox_mode()`:

- `sandbox-exec` (macOS): the deny-default profile of `darwin_profile`, whose
  rules were measured on Darwin 25 and are OS-version dependent, so they are
  kept in one place with one comment per rule.
- `bwrap` (Linux): `bwrap --unshare-net --unshare-pid --die-with-parent` with
  read-only binds of the same places and a writable scratch directory.
  Written from the design on macOS and run on the ubuntu cells of CI.
- `process-isolation-only`: the limits alone, when the tool is missing, when
  the probe failed, or when `ASSAY_SANDBOX=process-isolation-only` forces it
  (the tests force the fallback this way; `assay start` refuses any other
  value, and here any other value is ignored, so grading never raises).
  Before the probe every allowed path is checked to exist; the probe then
  runs `python -I -c "import json"` under the profile once per process, and
  a probe that exits by a signal (the loader aborting under a profile that
  no longer fits the OS) settles the fallback for the rest of the process.
  A profile that no longer fits therefore costs each probing process one
  failure, never one per grading: the daemon probes once in its life, and so
  do `assay start` and each `assay doctor`. `assay doctor` prints the mode
  and the reason for a fallback, `assay start` records the mode it found in
  `config.json`, and the daemon records its own in `broker.json`.

What the macOS profile does not close, measured: `file-read-metadata` is
unfiltered, so a program learns the existence, size and mtime of any path;
`sysctl-read` answers what every process may ask, and the process-argument
keys (`kern.procargs`, `kern.procargs2`) are denied by name only, since the
MIB form of that read bypasses the sandbox's sysctl filter in XNU, so the
daemon's arguments and exec-time environment stay readable by agent code
under the daemon's uid, as by any process of that uid. A daemon under its
own uid (ONBOARDING, the separate-user section) is the boundary for that.
"""

from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SANDBOX_EXEC = "sandbox-exec"
BWRAP = "bwrap"
PROCESS_ISOLATION_ONLY = "process-isolation-only"
FORCE_VARIABLE = "ASSAY_SANDBOX"  # set to process-isolation-only to force the fallback
NO_TOOL = "no sandbox-exec or bwrap"
FORCED = f"forced by {FORCE_VARIABLE}"
MEMORY_NOTE = "the memory limit is Linux-only"

FILE_SIZE_LIMIT = 1 << 20  # RLIMIT_FSIZE: 1 MB per file the program creates
ADDRESS_SPACE_LIMIT = 512 << 20  # RLIMIT_AS, Linux only
OUTPUT_CAP = 1 << 20  # bytes of stdout, and of stderr, read before the program is killed
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_PROGRAM = "import json"
ENTRY_NAME = "program.py"
STDERR_TAIL = 300  # the longest tail a caller renders
STDOUT_TAIL = 160
PACKAGE_DIRECTORY_NAMES = ("site-packages", "dist-packages")
PATH_FILTERS = ("literal", "subpath")

# The thread cap, set in the process environment by the prelude before any
# import: numpy's OpenBLAS creates its pool when the library loads, and on
# Linux RLIMIT_NPROC counts threads, so without the cap `import numpy` would
# fail under the limit. Accelerate (macOS) and MKL read the other two.
THREAD_CAP = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}

_PRELUDE = """\
import os as _os, resource as _resource, signal as _signal
for _name, _soft, _hard in {limits!r}:
    _resource.setrlimit(getattr(_resource, _name), (_soft, _hard))
_signal.signal(_signal.SIGXCPU, lambda *_, _os=_os: (_os.write(2, {message!r}), _os._exit(1)))
_os.environ.update({threads!r})
del _os, _resource, _signal, _name, _soft, _hard
"""

# A profile rule: the action (allow or deny), the operation, its filters as
# (kind, value) pairs (none for an unfiltered rule), and the measured reason.
Rule = tuple[str, str, tuple[tuple[str, str], ...], str]

_DECISION: tuple[str, str | None] | None = None


def process_limits(timeout: float, cpu_seconds: int | None = None) -> list[tuple[str, int, int]]:
    """(resource name, soft, hard) for one run: the CPU limit is the timeout
    rounded up unless the caller sets its own, the others are fixed; the
    address-space limit is Linux-only."""
    cpu = max(1, math.ceil(timeout)) if cpu_seconds is None else max(1, int(cpu_seconds))
    limits = [
        ("RLIMIT_CPU", cpu, cpu + 1),
        ("RLIMIT_FSIZE", FILE_SIZE_LIMIT, FILE_SIZE_LIMIT),
        ("RLIMIT_NPROC", 1, 1),
    ]
    if sys.platform.startswith("linux"):
        limits.append(("RLIMIT_AS", ADDRESS_SPACE_LIMIT, ADDRESS_SPACE_LIMIT))
    return limits


def _prelude(timeout: float, cpu_seconds: int | None) -> str:
    limits = process_limits(timeout, cpu_seconds)
    cpu = limits[0][1]
    return _PRELUDE.format(
        limits=limits,
        message=f"CPU limit of {cpu} seconds exceeded\n".encode(),
        threads=THREAD_CAP,
    )


def _under(path: str, roots: Sequence[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in roots)


def _interpreter_prefixes() -> list[str]:
    """The real paths of the interpreter's prefix and base prefix (a venv and
    the interpreter behind it). sandbox-exec matches resolved paths, and a
    uv-managed interpreter sits behind a symlink, so unresolved paths deny
    libpython and the loader aborts."""
    prefixes: list[str] = []
    for prefix in (sys.prefix, sys.base_prefix):
        real = os.path.realpath(prefix)
        if real not in prefixes:
            prefixes.append(real)
    return prefixes


def package_directories() -> list[str]:
    """The package directories of the interpreter's module search path that
    lie outside its prefixes, as real paths: the entries of `sys.path` that
    exist, carry a `site-packages` or `dist-packages` component, and are not
    under the prefix or base prefix. Measured need, beyond the note's two
    prefixes: `uv run --with numpy` keeps numpy in uv's archive directory and
    reaches it through a `.pth` file, and a Homebrew interpreter keeps its
    site-packages outside the framework prefix, so with the prefixes alone
    `import numpy` fails in the sandbox with ModuleNotFoundError. A source
    tree an editable install puts on the path has no such component and is
    not admitted, so a run directory inside a checkout stays unreadable."""
    prefixes = _interpreter_prefixes()
    found: list[str] = []
    for entry in sys.path:
        if not entry:
            continue
        real = os.path.realpath(entry)
        if not os.path.isdir(real) or _under(real, prefixes) or _under(real, found):
            continue
        if any(part in PACKAGE_DIRECTORY_NAMES for part in real.split(os.sep)):
            found.append(real)
    return found


def _exec_filters() -> tuple[tuple[str, str], ...]:
    """What may be exec'd: the interpreter as named and as resolved (the
    profile matches resolved paths, and the executable of a venv is a
    symlink), and the executables under its prefix and base prefix, because
    the python.org framework's bin/python is a stub that spawns
    Resources/Python.app/Contents/MacOS/Python in place, and the macOS CI
    cells run that build. No shell lives under either prefix."""
    executable = sys.executable
    real = os.path.realpath(executable)
    literals = ((executable,) if real == executable else (executable, real))
    return tuple(("literal", path) for path in literals) + tuple(
        ("subpath", prefix) for prefix in _interpreter_prefixes()
    )


def darwin_rules(scratch: Path) -> list[Rule]:
    """The rules of the macOS profile after `(deny default)` and
    `(deny network*)`, in profile order, where a later rule wins. Measured on
    Darwin 25 (macOS 26) with the kernel's interpreter and numpy by running
    `import json, numpy` with each entry removed in turn; each reason records
    what the measurement showed. Design note 3, section 8.5, lists them."""
    rules: list[Rule] = [
        ("allow", "process-exec", _exec_filters(),
         "sandbox-exec execs the interpreter in place, and the python.org framework stub "
         "spawns the Python.app executable under the base prefix in place; nothing outside "
         "the interpreter's prefixes may be exec'd, a shell included; without it execvp fails "
         "with EPERM"),
        ("allow", "file-read-metadata", (),
         "stat and path lookup everywhere, no content; without it execvp fails before main"),
        ("allow", "sysctl-read", (),
         "interpreter start reads sysctl (hw and kern keys); without it PermissionError at start"),
        ("deny", "sysctl-read", (("sysctl-name-prefix", "kern.procargs"),),
         "the arguments and exec-time environment of any process, the daemon's included, which "
         "carry what the operator's shell exported; after the allow, since the later rule wins. "
         "Measured: this closes the named route (sysctlbyname, the sysctl tool) only; the MIB "
         "form of the same read passes under every filter, while kern.boottime by MIB is "
         "denied, so XNU's procargs handler does not consult the filter at all, and the read "
         "stays open to any process of the daemon's uid, as the OS allows it; the separate uid "
         "of section 8.1 is the boundary that closes it"),
        ("allow", "file-read*", (("literal", "/"),),
         "the root directory itself; without it the dynamic loader aborts before main"),
    ]
    for prefix in _interpreter_prefixes():
        rules.append((
            "allow", "file-read*", (("subpath", prefix),),
            "the interpreter's prefix: binary, libpython, standard library, pyvenv.cfg, "
            "site-packages; without it the loader aborts or pyvenv.cfg is denied",
        ))
    for directory in package_directories():
        rules.append((
            "allow", "file-read*", (("subpath", directory),),
            "a package directory of the interpreter's search path outside its prefixes "
            "(uv's archive, Homebrew's site-packages); without it numpy is not found",
        ))
    rules += [
        ("allow", "file-read*", (("subpath", "/usr/lib"),),
         "system libraries outside the shared cache; libffi's trampoline table is here and "
         "numpy under a Homebrew interpreter aborts without it"),
        ("allow", "file-read*", (("subpath", "/usr/share"),),
         "shared data of the system libraries (ICU data; not zoneinfo, which resolves under "
         "/private/var/db/timezone and is denied); measured: not exercised by interpreter "
         "start or numpy; kept per the note"),
        ("allow", "file-read*", (("subpath", "/System"),),
         "frameworks and the dyld shared cache; measured: not exercised by interpreter start "
         "or numpy under the profile, the cache being mapped before it applies; kept per the note"),
        ("allow", "file-read*", (("subpath", "/private/var/db/dyld"),),
         "the dyld cache directory of older macOS layouts; absent on Darwin 25 and then "
         "omitted; kept per the note"),
        ("allow", "file-read*", (("literal", "/dev/null"),),
         "the null device, read by the standard library; kept per the note"),
        ("allow", "file-write*", (("literal", "/dev/null"),),
         "the null device, written by the standard library; kept per the note"),
        ("allow", "file-read*", (("literal", "/dev/urandom"),),
         "entropy for os.urandom beyond getentropy; kept per the note"),
        ("allow", "file-read*", (("subpath", str(scratch)),),
         "the scratch directory: the program, its companions, its working directory"),
        ("allow", "file-write*", (("subpath", str(scratch)),),
         "the scratch directory is the one place the program may write"),
    ]
    return rules


def _profile_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def darwin_profile(scratch: Path) -> tuple[str, list[str]]:
    """The sandbox-exec profile for one run and the allowed paths it omits
    because they do not exist on this machine. Deny by default, the network
    denied by name, then the measured rules with their reasons as profile
    comments; a rule whose every path is absent is left out."""
    lines = ["(version 1)", "(deny default)", "(deny network*)"]
    omitted: list[str] = []
    for action, operation, filters, why in darwin_rules(scratch):
        kept = []
        for kind, value in filters:
            if kind in PATH_FILTERS and value != str(scratch) and not os.path.exists(value):
                omitted.append(value)
                continue
            kept.append((kind, value))
        if filters and not kept:
            continue
        lines.append(f"; {why}")
        rendered = "".join(f' ({kind} "{_profile_text(value)}")' for kind, value in kept)
        lines.append(f"({action} {operation}{rendered})")
    return "\n".join(lines) + "\n", omitted


def _load_bearing_paths() -> list[str]:
    """The allowed paths without which the interpreter cannot start under the
    profile, measured: the root directory and the prefixes. A machine missing
    one of them gets the fallback without a probe."""
    return ["/", *_interpreter_prefixes()]


def linux_read_only_places() -> list[tuple[str, str]]:
    """The places bwrap binds read-only, as (path, why): the macOS list
    translated, the loader and the libraries where Linux keeps them, and the
    package directories. Written from the design on macOS; run on the ubuntu
    cells of CI."""
    places = [
        (prefix, "the interpreter's prefix: binary, libpython, standard library, site-packages")
        for prefix in _interpreter_prefixes()
    ]
    places += [
        (prefix, "the interpreter's prefix as named, where it is a symlink to the real one")
        for prefix in (sys.prefix, sys.base_prefix)
        if prefix != os.path.realpath(prefix)
    ]
    places += [
        (directory, "a package directory of the search path outside the prefixes")
        for directory in package_directories()
    ]
    places += [
        ("/lib", "the dynamic loader and libc on systems that keep them here"),
        ("/lib64", "the 64-bit loader path the interpreter's ELF header names"),
        ("/usr/lib", "system libraries"),
        ("/usr/lib64", "system libraries on 64-bit layouts"),
        ("/usr/share", "shared data of the system libraries (zoneinfo)"),
        ("/etc/ld.so.cache", "the loader's library cache, so multiarch directories resolve"),
    ]
    seen: set[str] = set()
    unique = []
    for path, why in places:
        if path not in seen and os.path.exists(path):
            seen.add(path)
            unique.append((path, why))
    return unique


def bwrap_command(command: list[str], scratch: Path) -> list[str]:
    """`bwrap --unshare-net --unshare-pid --die-with-parent` (the sandboxed
    process dies with its wrapper, which the kernel kills on the wall clock),
    the read-only binds (a symlinked place is recreated as the symlink), a
    fresh /proc and a minimal /dev (null, urandom), the writable scratch
    directory as the working directory, then the command."""
    wrapped = [BWRAP, "--unshare-net", "--unshare-pid", "--die-with-parent"]
    for path, _why in linux_read_only_places():
        if os.path.islink(path):
            wrapped += ["--symlink", os.readlink(path), path]
        else:
            wrapped += ["--ro-bind", path, path]
    wrapped += [
        "--proc", "/proc",
        "--dev", "/dev",
        "--bind", str(scratch), str(scratch),
        "--chdir", str(scratch),
        "--",
    ]
    return wrapped + command


def wrap_command(mode: str, command: list[str], scratch: Path) -> list[str]:
    """The command as the mode runs it."""
    if mode == SANDBOX_EXEC:
        profile, _omitted = darwin_profile(scratch)
        return [SANDBOX_EXEC, "-p", profile, *command]
    if mode == BWRAP:
        return bwrap_command(command, scratch)
    return command


def _probe(mode: str) -> tuple[str, str | None]:
    """Check the allowed paths, then run the probe program once under the
    mode. (mode, None) when the interpreter started and exited cleanly; the
    fallback with the reason otherwise. An exit by a signal is the loader
    aborting under a profile that no longer fits the OS, and settles the
    fallback for this process."""
    with tempfile.TemporaryDirectory(prefix="assay-sandbox-probe-") as raw:
        scratch = Path(os.path.realpath(raw))
        if mode == SANDBOX_EXEC:
            _profile, omitted = darwin_profile(scratch)
            missing = [path for path in omitted if path in _load_bearing_paths()]
            if missing:
                return PROCESS_ISOLATION_ONLY, (
                    f"the {mode} profile cannot be built: {missing[0]} is missing"
                )
        command = wrap_command(mode, [sys.executable, "-I", "-c", PROBE_PROGRAM], scratch)
        try:
            completed = subprocess.run(  # noqa: S603 - the probe of the sandbox tool
                command,
                capture_output=True,
                cwd=scratch,
                env={},
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return PROCESS_ISOLATION_ONLY, f"the {mode} probe hung for {PROBE_TIMEOUT_SECONDS:g}s"
        except OSError as error:
            return PROCESS_ISOLATION_ONLY, f"{mode} could not be started: {error}"
    if completed.returncode == 0:
        return mode, None
    if completed.returncode < 0:
        return PROCESS_ISOLATION_ONLY, (
            f"the {mode} probe died with signal {-completed.returncode}: "
            "the profile no longer fits this OS"
        )
    tail = completed.stderr.decode(errors="replace").strip().splitlines()
    detail = tail[-1][:STDERR_TAIL] if tail else "no stderr"
    return PROCESS_ISOLATION_ONLY, f"the {mode} probe failed (exit {completed.returncode}): {detail}"


def forced_mode() -> str | None:
    """The fallback when `ASSAY_SANDBOX=process-isolation-only` is set, else
    None. `assay start` refuses any other value before anything is written;
    here another value is ignored, so grading never raises."""
    return PROCESS_ISOLATION_ONLY if os.getenv(FORCE_VARIABLE) == PROCESS_ISOLATION_ONLY else None


def _decide() -> tuple[str, str | None]:
    if forced_mode() is not None:
        return PROCESS_ISOLATION_ONLY, FORCED
    if sys.platform == "darwin" and shutil.which(SANDBOX_EXEC):
        return _probe(SANDBOX_EXEC)
    if sys.platform.startswith("linux") and shutil.which(BWRAP):
        return _probe(BWRAP)
    return PROCESS_ISOLATION_ONLY, NO_TOOL


def sandbox_mode() -> str:
    """One of `sandbox-exec`, `bwrap`, `process-isolation-only`, decided once
    per process (the probe runs at most once)."""
    global _DECISION
    if _DECISION is None:
        _DECISION = _decide()
    return _DECISION[0]


def sandbox_text(mode: str) -> str:
    """The words after `sandbox | ` in `assay doctor`: the mode, or for the
    fallback `process isolation only (<why>)`, and on macOS that the memory
    limit is Linux-only."""
    if mode == PROCESS_ISOLATION_ONLY:
        reason = _DECISION[1] if _DECISION is not None and _DECISION[0] == mode else None
        text = f"process isolation only ({reason or NO_TOOL})"
    else:
        text = mode
    if sys.platform == "darwin":
        text += f", {MEMORY_NOTE}"
    return text


def check_imports(modules: Sequence[str]) -> tuple[bool, str | None]:
    """Whether the modules import inside the sandbox as it runs here (for
    `assay doctor`: numpy and json, what a verifier needs). (True, None) or
    (False, the reason)."""
    completed = run_program(
        "import " + ", ".join(modules) + "\nprint('{}')\n", {}, timeout=PROBE_TIMEOUT_SECONDS
    )
    if completed["status"] == "ok":
        return True, None
    return False, str(completed["reason"])


def _copy_companions(scratch: Path, companions: Sequence[str | Path]) -> dict[str, str]:
    """Copy each companion into scratch under its own name. Returns the
    rewriting map: the path as the caller gave it and its real path, both to
    the copy."""
    copies: dict[str, str] = {}
    taken: dict[str, str] = {ENTRY_NAME: "the program"}
    for item in companions:
        given = str(item)
        source = Path(item).resolve()
        if str(source) in copies:
            copies[given] = copies[str(source)]
            continue
        if source.name in taken and taken[source.name] != str(source):
            raise ValueError(f"two companions named {source.name}")
        target = scratch / source.name
        shutil.copyfile(source, target)
        taken[source.name] = str(source)
        copies[given] = copies[str(source)] = str(target)
    return copies


def _feed(stream: Any, payload: bytes) -> None:
    """Write the payload to the program's stdin from its own thread, so a
    program that fills its stdout before reading its stdin cannot stall the
    kernel; a killed program breaks the pipe, which ends the write."""
    try:
        stream.write(payload)
        stream.close()
    except (BrokenPipeError, OSError):
        pass


def _capture(process: subprocess.Popen[bytes], timeout: float) -> tuple[str | None, bytes, bytes]:
    """Read stdout and stderr up to OUTPUT_CAP bytes each until both close
    and the program exits, within the wall clock. Returns (failure, stdout,
    stderr): failure is None, "timeout", or the stream that passed the cap;
    on a failure the program has been killed."""
    assert process.stdout is not None and process.stderr is not None
    streams = {"stdout": process.stdout, "stderr": process.stderr}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    failure: str | None = None
    with selectors.DefaultSelector() as selector:
        for name, stream in streams.items():
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map() and failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timeout"
                break
            for key, _events in selector.select(remaining):
                chunk = os.read(key.fd, 1 << 16)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[key.data] += chunk
                if len(buffers[key.data]) > OUTPUT_CAP:
                    failure = key.data
                    break
    if failure is None:
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            failure = "timeout"
    if failure is not None:
        process.kill()
        process.wait()
    for stream in streams.values():
        stream.close()
    return failure, bytes(buffers["stdout"]), bytes(buffers["stderr"])


def _invalid(kind: str, reason: str, **detail: Any) -> dict[str, Any]:
    return {"status": "invalid", "kind": kind, "reason": reason, **detail}


def run_program(
    program: str,
    payload: Mapping[str, Any],
    *,
    timeout: float,
    companions: Sequence[str | Path] = (),
    cpu_seconds: int | None = None,
) -> dict[str, Any]:
    """Run `program` (Python source) once in the sandbox with `payload` on
    stdin, `companions` copied beside it and the payload's paths rewritten to
    the copies. `{"status": "ok", "result": <the last JSON line>}`, or
    `{"status": "invalid", "kind": <why>, "reason": <words>, ...}` with the
    kind one of `missing` (a companion), `timeout`, `output` (the cap, with
    `stream`), `crash` (with `exit` and `tail`, the last line of stderr),
    `no_output` or `malformed` (with `output`, the last line of stdout); the
    reason carries no noun, so the caller names its program in its own
    words. Never raises for what the program does."""
    mode = sandbox_mode()
    with tempfile.TemporaryDirectory(prefix="assay-sandbox-") as raw:
        scratch = Path(os.path.realpath(raw))
        try:
            copies = _copy_companions(scratch, companions)
        except (OSError, ValueError) as error:
            return _invalid("missing", f"file missing: {error}")
        entry = scratch / ENTRY_NAME
        entry.write_text(_prelude(timeout, cpu_seconds) + program)
        # Every JSON string equal to a companion's path becomes the copy's
        # path: the replacement is done on the encoded text, quotes included,
        # so only whole string values match and the observations are not
        # walked.
        text = json.dumps(payload)
        for source, copy in copies.items():
            text = text.replace(json.dumps(source), json.dumps(copy))
        command = wrap_command(mode, [sys.executable, "-I", str(entry)], scratch)
        process = subprocess.Popen(  # noqa: S603 - the sandboxed run itself
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=scratch,
            env={},
        )
        feeder = threading.Thread(target=_feed, args=(process.stdin, text.encode()), daemon=True)
        feeder.start()
        failure, stdout, stderr = _capture(process, timeout)
        feeder.join(timeout=1.0)
    if failure == "timeout":
        return _invalid("timeout", f"timed out after {timeout:g}s")
    if failure is not None:
        stream = "output" if failure == "stdout" else "error output"
        return _invalid("output", f"produced more than {OUTPUT_CAP >> 20} MB of {stream}", stream=failure)
    if process.returncode != 0:
        lines = stderr.decode(errors="replace").strip().splitlines()
        tail = lines[-1][:STDERR_TAIL] if lines else "no stderr"
        return _invalid(
            "crash", f"crashed (exit {process.returncode}): {tail}", exit=process.returncode, tail=tail
        )
    lines = [line for line in stdout.decode(errors="replace").splitlines() if line.strip()]
    if not lines:
        return _invalid("no_output", "produced no output")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError:
        last = lines[-1][:STDOUT_TAIL]
        return _invalid("malformed", f"malformed output: {last!r}", output=last)
    return {"status": "ok", "result": result}
