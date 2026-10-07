"""The sandbox: the one place agent-authored code runs (design note 3, 8.5).

Verifiers (`verifiers.run_verifier`), channel extractors
(`channels._run_extractor`) and the world model (`model._run_sandbox`) hand
`run_program` the source of their runner (the program), the JSON payload the
runner reads from stdin, and the files the runner loads (the companions: the
stored verifier or extractor, `model.py` and the extractor files of the
model's declared channels). The program and its companions are copied into a
fresh scratch directory, every payload string naming a companion is rewritten
to the copy, and the program runs from there as `python -I` (isolated: no
PYTHONPATH, no user site, the working directory off the path; not `-S`, which
would drop site-packages and numpy with them) with an empty environment, the
scratch directory as the working directory, the payload on stdin and one JSON
line on stdout. Nothing the program receives names the run directory.

The process limits are set by a prelude the program starts with, inside the
interpreter and before any agent code, because the sandbox tools create the
process (bwrap forks a helper) before the interpreter starts: RLIMIT_CPU at
the timeout rounded up (the hard limit one second later; SIGXCPU is handled
in Python and ends the process with a message, so a runaway never dies by a
core-dump signal, which macOS would report in a dialog; the wall clock is the
backstop), RLIMIT_FSIZE 1 MB, RLIMIT_NPROC 1 (no fork, no spawn), on Linux
RLIMIT_AS 512 MB (macOS refuses an address-space limit, so the memory limit
is Linux-only), and a one-thread cap for BLAS (on Linux RLIMIT_NPROC counts
threads, and numpy's OpenBLAS creates its pool at import). The wall clock is
the caller's timeout, enforced by the kernel process with SIGKILL.

Three modes, decided once per process by `sandbox_mode()`:

- `sandbox-exec` (macOS): the deny-default profile of `darwin_profile`, whose
  allow list was measured on Darwin 25 and is OS-version dependent, so it is
  kept in one place with one comment per entry.
- `bwrap` (Linux): `bwrap --unshare-net --unshare-pid --die-with-parent` with
  read-only binds of the same places and a writable scratch directory.
  Written from the design; the module was developed on macOS.
- `process-isolation-only`: the limits alone, when the tool is missing, when
  the probe failed, or when `ASSAY_SANDBOX=process-isolation-only` forces it
  (the tests force the fallback this way; any other value is ignored). Before
  the probe every allowed path is checked to exist; the probe then runs
  `python -I -c "import json"` under the profile once per process, and a
  probe that exits by a signal (the loader aborting under a profile that no
  longer fits the OS) settles the fallback for the rest of the process, so a
  user sees at most one crash dialog per daemon lifetime, never one per
  grading. `assay doctor` prints the mode and the reason for a fallback, and
  `assay start` records the mode in `config.json`.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
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
PROBE_TIMEOUT_SECONDS = 30.0
PROBE_PROGRAM = "import json"
ENTRY_NAME = "program.py"
STDERR_TAIL = 200
PACKAGE_DIRECTORY_NAMES = ("site-packages", "dist-packages")

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

_DECISION: tuple[str, str | None] | None = None


def process_limits(timeout: float) -> list[tuple[str, int, int]]:
    """(resource name, soft, hard) for one run: the CPU limit is the timeout
    rounded up, the others are fixed; the address-space limit is Linux-only."""
    cpu = max(1, math.ceil(timeout))
    limits = [
        ("RLIMIT_CPU", cpu, cpu + 1),
        ("RLIMIT_FSIZE", FILE_SIZE_LIMIT, FILE_SIZE_LIMIT),
        ("RLIMIT_NPROC", 1, 1),
    ]
    if sys.platform.startswith("linux"):
        limits.append(("RLIMIT_AS", ADDRESS_SPACE_LIMIT, ADDRESS_SPACE_LIMIT))
    return limits


def _prelude(timeout: float) -> str:
    limits = process_limits(timeout)
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


def darwin_allow_list(scratch: Path) -> list[tuple[str, str | None, str | None, str]]:
    """The allow list of the macOS profile as (operation, filter, path, why),
    in profile order. Measured on Darwin 25 (macOS 26) with the kernel's
    interpreter and numpy by running `import json, numpy` with each entry
    removed in turn; each `why` records what the measurement showed. Every
    entry of design note 3, section 8.5, is here, plus the package directories
    of `package_directories` (the one measured addition, with its reason
    there)."""
    rules: list[tuple[str, str | None, str | None, str]] = [
        ("process-exec*", None, None,
         "sandbox-exec execs the interpreter in place; without it execvp fails with EPERM"),
        ("file-read-metadata", None, None,
         "stat and path lookup everywhere, no content; without it execvp fails before main"),
        ("sysctl-read", None, None,
         "interpreter start reads sysctl (hw and kern keys); without it PermissionError at start"),
        ("mach-lookup", None, None,
         "libinfo (getpwuid, expanduser with HOME unset) goes through opendirectoryd; "
         "measured: interpreter start and numpy do not need it; kept per the note"),
        ("file-read*", "literal", "/",
         "the root directory itself; without it the dynamic loader aborts before main"),
    ]
    for prefix in _interpreter_prefixes():
        rules.append((
            "file-read*", "subpath", prefix,
            "the interpreter's prefix: binary, libpython, standard library, pyvenv.cfg, "
            "site-packages; without it the loader aborts or pyvenv.cfg is denied",
        ))
    for directory in package_directories():
        rules.append((
            "file-read*", "subpath", directory,
            "a package directory of the interpreter's search path outside its prefixes "
            "(uv's archive, Homebrew's site-packages); without it numpy is not found",
        ))
    rules += [
        ("file-read*", "subpath", "/usr/lib",
         "system libraries outside the shared cache; libffi's trampoline table is here and "
         "numpy under a Homebrew interpreter aborts without it"),
        ("file-read*", "subpath", "/usr/share",
         "shared data of the system libraries (ICU, zoneinfo by symlink); "
         "measured: not exercised by interpreter start or numpy; kept per the note"),
        ("file-read*", "subpath", "/System",
         "frameworks and the dyld shared cache; "
         "measured: not exercised by interpreter start or numpy; kept per the note"),
        ("file-read*", "subpath", "/private/var/db/dyld",
         "the dyld cache directory of older macOS layouts; absent on Darwin 25 and then "
         "omitted; kept per the note"),
        ("file-read*", "literal", "/dev/null",
         "the null device, read by the standard library; kept per the note"),
        ("file-write*", "literal", "/dev/null",
         "the null device, written by the standard library; kept per the note"),
        ("file-read*", "literal", "/dev/urandom",
         "entropy for os.urandom beyond getentropy; kept per the note"),
        ("file-read*", "subpath", str(scratch),
         "the scratch directory: the program, its companions, its working directory"),
        ("file-write*", "subpath", str(scratch),
         "the scratch directory is the one place the program may write"),
    ]
    return rules


def _profile_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def darwin_profile(scratch: Path) -> tuple[str, list[str]]:
    """The sandbox-exec profile for one run and the allowed paths it omits
    because they do not exist on this machine. Deny by default, the network
    denied by name, then the measured allow list with its reasons as profile
    comments."""
    lines = ["(version 1)", "(deny default)", "(deny network*)"]
    omitted: list[str] = []
    for operation, kind, path, why in darwin_allow_list(scratch):
        if path is not None and path != str(scratch) and not os.path.exists(path):
            omitted.append(path)
            continue
        lines.append(f"; {why}")
        if kind is None:
            lines.append(f"(allow {operation})")
        else:
            lines.append(f'(allow {operation} ({kind} "{_profile_text(str(path))}"))')
    return "\n".join(lines) + "\n", omitted


def _load_bearing_paths() -> list[str]:
    """The allowed paths without which the interpreter cannot start under the
    profile: the root, the prefixes, the system libraries and frameworks. A
    machine missing one of them gets the fallback without a probe."""
    return ["/", *_interpreter_prefixes(), "/usr/lib", "/System"]


def linux_read_only_places() -> list[tuple[str, str]]:
    """The places bwrap binds read-only, as (path, why): the macOS list
    translated, the loader and the libraries where Linux keeps them, and the
    package directories. Written from the design; untested on macOS, where
    the module was developed."""
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
    """`bwrap --unshare-net --unshare-pid --die-with-parent`, the read-only
    binds (a symlinked place is recreated as the symlink), a fresh /proc and a
    minimal /dev (null, urandom), the writable scratch directory as the
    working directory, then the command."""
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
    None. `assay start` refuses any other value before anything is spent;
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
    outcome = run_program(
        "import " + ", ".join(modules) + "\nprint('{}')\n", {}, timeout=PROBE_TIMEOUT_SECONDS
    )
    if outcome["status"] == "ok":
        return True, None
    return False, str(outcome["reason"])


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


def run_program(
    program: str,
    payload: Mapping[str, Any],
    *,
    timeout: float,
    companions: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Run `program` (Python source) once in the sandbox with `payload` on
    stdin, `companions` copied beside it and the payload's paths rewritten to
    the copies. `{"status": "ok", "result": <the last JSON line>}` or
    `{"status": "invalid", "reason": <why>}`, the reason without a noun, for
    the caller to name its program. Never raises for what the program does."""
    mode = sandbox_mode()
    with tempfile.TemporaryDirectory(prefix="assay-sandbox-") as raw:
        scratch = Path(os.path.realpath(raw))
        try:
            copies = _copy_companions(scratch, companions)
        except (OSError, ValueError) as error:
            return {"status": "invalid", "reason": f"file missing: {error}"}
        entry = scratch / ENTRY_NAME
        entry.write_text(_prelude(timeout) + program)
        # Every JSON string equal to a companion's path becomes the copy's
        # path: the replacement is done on the encoded text, quotes included,
        # so only whole string values match and the observations are not
        # walked.
        text = json.dumps(payload)
        for source, copy in copies.items():
            text = text.replace(json.dumps(source), json.dumps(copy))
        command = wrap_command(mode, [sys.executable, "-I", str(entry)], scratch)
        try:
            completed = subprocess.run(  # noqa: S603 - the sandboxed run itself
                command,
                input=text.encode(),
                capture_output=True,
                cwd=scratch,
                env={},
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"status": "invalid", "reason": f"timed out after {timeout:g}s"}
    if completed.returncode != 0:
        stderr = completed.stderr.decode(errors="replace").strip().splitlines()
        tail = stderr[-1][:STDERR_TAIL] if stderr else "no stderr"
        return {
            "status": "invalid",
            "reason": f"crashed (exit {completed.returncode}): {tail}",
        }
    lines = [
        line
        for line in completed.stdout.decode(errors="replace").splitlines()
        if line.strip()
    ]
    if not lines:
        return {"status": "invalid", "reason": "produced no output"}
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"status": "invalid", "reason": f"produced malformed output: {lines[-1][:120]!r}"}
    return {"status": "ok", "result": result}
