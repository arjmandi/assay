"""The Claude Code hooks (docs/ARCHITECTURE.md section 8.4), the operator's
side: the policy file `assay hooks install` writes, the hook entries it puts
into `<run dir>/.claude/settings.json`, the check that the installed hooks
can run, and the `tool_use` activity record `assay hooks post-tool-use`
appends.

The PreToolUse script is `hooks/pre_tool_use.py` at the repository root,
standard library only, run under the interpreter pinned at install; it reads
the policy file and the hook's JSON. The policy file (version 2) is one JSON
object: `run_dir`, `anchor_dir`, `token_file` (or null), `policy_file`,
`launcher` (absolute), `assay_value` (the `ASSAY` the operator had exported
at install, or null), `deny` (the regular expressions over a Bash command),
`protected` (the installation's own paths: the hook script, the launcher,
the interpreter as pinned and as resolved, the harness's package directory,
the interpreter's library directories), `python` (the interpreter the hook
command runs under, `sys.executable` as it is, so that `assay doctor` and
`hooks install --check` can tell when it is gone) and `version`. The
settings entries are one per matcher, `Bash`, `Write`, `Edit`, `MultiEdit`
and `NotebookEdit` for PreToolUse and those plus `mcp__assay__.*` for
PostToolUse, merged into the file as it is: only the entries whose command
text is the harness's own are replaced, and a `.claude` or `settings.json`
that is a symlink refuses the install.

The PostToolUse record is fixed in shape, `{"kind": "tool_use",
"tool_use_id", "session_id", "tool", "command_prefix", "end_event",
"timestamp"}`, appended under the file lock by `core.append_jsonl`;
`end_event` is the receipt's when the tool output carries one as the
outermost object's own field of an act, commit or reset document (the `EVENT
| e<id>` line of the prose, the `end_event` field of a `--json` receipt
document, or the record an `mcp__assay__` tool returned, under `receipt` as
the wire's result shape has it) and null otherwise. The join is the agent's own output, a hint the reader
checks against the journal, not an authority (section 7.5).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import subprocess
import sys
import sysconfig
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .core import AssayError, RunPaths, append_jsonl, read_json, require_run
from .integrity import anchor_dir

POLICY_VERSION = 2
PRE_TOOL_USE_MATCHERS: tuple[str, ...] = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit")
POST_TOOL_USE_MATCHERS: tuple[str, ...] = (*PRE_TOOL_USE_MATCHERS, "mcp__assay__.*")
# The PreToolUse script, at the repository root beside `src/`.
HOOK_SCRIPT = Path(__file__).resolve().parents[2] / "hooks" / "pre_tool_use.py"
COMMAND_PREFIX_LENGTH = 80
TOOL_USE = "tool_use"
EDITORS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
RECEIPT_KINDS = frozenset({"act", "commit", "reset"})
# The receipt line of a paid command, as `inspect.result_text` prints it.
EVENT_LINE = re.compile(r"^EVENT \| e(\d+) \|", re.MULTILINE)
# The harness's own hook entries, by their command text.
OWNED = re.compile(r"(?:pre_tool_use\.py|hooks post-tool-use) --policy ")
REINSTALL_HINT = "reinstall the hooks with `assay hooks install --policy FILE ...` from the operator's shell"
# The benign event the check sends through the installed hook.
PROBE: dict[str, Any] = {
    "session_id": "hooks-check",
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "true"},
    "tool_use_id": "hooks-check",
}


@dataclasses.dataclass(frozen=True, slots=True)
class HookPolicy:
    """The policy file, paths resolved."""

    run_dir: Path
    anchor_dir: Path
    token_file: Path | None
    policy_file: Path
    launcher: Path
    assay_value: str | None
    deny: tuple[str, ...]
    protected: tuple[Path, ...]
    python: Path
    version: int = POLICY_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "run_dir": str(self.run_dir),
            "anchor_dir": str(self.anchor_dir),
            "token_file": None if self.token_file is None else str(self.token_file),
            "policy_file": str(self.policy_file),
            "launcher": str(self.launcher),
            "assay_value": self.assay_value,
            "deny": list(self.deny),
            "protected": [str(path) for path in self.protected],
            "python": str(self.python),
        }

    @classmethod
    def from_json(cls, obj: Any) -> HookPolicy:
        """The policy a file holds; a ValueError names what does not fit."""
        if not isinstance(obj, Mapping):
            raise ValueError("the policy is not a JSON object")
        if obj.get("version") != POLICY_VERSION:
            raise ValueError(f"version {obj.get('version')!r} is not {POLICY_VERSION}")
        assay_value = obj.get("assay_value")
        if assay_value is not None and (not isinstance(assay_value, str) or not assay_value):
            raise ValueError("assay_value must be a non-empty string or null")
        return cls(
            run_dir=_path(obj, "run_dir"),
            anchor_dir=_path(obj, "anchor_dir"),
            token_file=_optional_path(obj, "token_file"),
            policy_file=_path(obj, "policy_file"),
            launcher=_path(obj, "launcher"),
            assay_value=assay_value,
            deny=_strings(obj, "deny"),
            protected=tuple(Path(item) for item in _strings(obj, "protected")),
            python=_path(obj, "python"),
        )


def _path(obj: Mapping[str, Any], key: str) -> Path:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return Path(value)


def _optional_path(obj: Mapping[str, Any], key: str) -> Path | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string or null")
    return Path(value)


def _strings(obj: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = obj.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return tuple(value)


def read_policy(path: Path) -> HookPolicy:
    """The policy file as written, or `HOOK_POLICY_INVALID`."""
    try:
        text = path.read_text()
    except OSError as error:
        raise AssayError(
            f"the hook policy file {path} cannot be read: {type(error).__name__}: {error}",
            code="HOOK_POLICY_INVALID",
            hint=REINSTALL_HINT,
        ) from error
    try:
        return HookPolicy.from_json(json.loads(text))
    except ValueError as error:
        raise AssayError(
            f"the hook policy file {path} is not a policy file: {error}",
            code="HOOK_POLICY_INVALID",
            hint=REINSTALL_HINT,
        ) from error


# --- the install ------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Installed:
    """What `hooks install` wrote: the settings file, the policy file, the
    number of hook entries, and the launcher it pinned."""

    settings: Path
    policy: Path
    entries: int
    launcher: Path


def default_launcher() -> Path | None:
    """The launcher to pin when `--launcher` is not given: the entry point
    running this command. Through `bin/assay` the interpreter runs
    `src/assay_cli.py`, so the launcher is the `bin/assay` beside that
    `src/`; an installed `assay` console script is itself; anything else
    (`python -m assay.cli`) pins nothing and the flag is required."""
    entry = Path(sys.argv[0]).resolve()
    if entry.name == "assay_cli.py":
        candidate = entry.parent.parent / "bin" / "assay"
        return candidate if candidate.is_file() else None
    if entry.name == "assay" and entry.is_file():
        return entry
    return None


def installation_paths(launcher: Path, python: Path) -> tuple[Path, ...]:
    """The paths the enforcement itself lives in, which the policy protects
    (section 8.4): the hook script, the launcher, the interpreter as pinned
    and as resolved, the harness's package directory (the checkout's `src/`,
    or the site-packages of an install), and the interpreter's library
    directories (the standard library and site-packages, where a planted
    module would load before the harness's own). Under one uid they are the
    agent's to rewrite; the policy refuses the writes it can see, and the
    separate-user setup (8.1) is what protects them."""
    found = [HOOK_SCRIPT, launcher, python, Path(os.path.realpath(python)), Path(__file__).resolve().parents[1]]
    scheme = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = scheme.get(key)
        if value:
            found.append(Path(value).resolve())
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return tuple(unique)


def hook_entry(matcher: str, command: str) -> dict[str, Any]:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}


def merge_hook_entries(
    settings: Mapping[str, Any], pre_command: str, post_command: str
) -> tuple[dict[str, Any], int]:
    """The settings with the harness's hook entries in place: every other
    key and every foreign hook kept as it is, the harness's own entries
    (by their command text) replaced. Returns the settings and the number
    of entries written."""
    merged: dict[str, Any] = dict(settings)
    hooks = merged.get("hooks")
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, Mapping):
        raise AssayError(
            f"the settings file's `hooks` is {type(hooks).__name__}, not an object",
            code="RECORD_CORRUPT",
            hint="fix .claude/settings.json by hand, then install again",
        )
    merged_hooks: dict[str, Any] = dict(hooks)
    written = 0
    for event, matchers, command in (
        ("PreToolUse", PRE_TOOL_USE_MATCHERS, pre_command),
        ("PostToolUse", POST_TOOL_USE_MATCHERS, post_command),
    ):
        existing = merged_hooks.get(event)
        if existing is None:
            existing = []
        if not isinstance(existing, list):
            raise AssayError(
                f"the settings file's `hooks.{event}` is {type(existing).__name__}, not a list",
                code="RECORD_CORRUPT",
                hint="fix .claude/settings.json by hand, then install again",
            )
        kept = [item for item in (_without_owned(entry) for entry in existing) if item is not None]
        ours = [hook_entry(matcher, command) for matcher in matchers]
        merged_hooks[event] = [*kept, *ours]
        written += len(ours)
    merged["hooks"] = merged_hooks
    return merged, written


def _without_owned(entry: Any) -> Any:
    """A hook entry with the harness's own commands removed; None when
    nothing else is left of it. Anything that is not an entry is kept."""
    if not isinstance(entry, Mapping):
        return entry
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return entry
    kept = [
        item
        for item in hooks
        if not (isinstance(item, Mapping) and isinstance(item.get("command"), str) and OWNED.search(item["command"]))
    ]
    if not kept:
        return None
    return {**entry, "hooks": kept}


def _write_json(target: Path, value: Any, *, mode: int) -> None:
    """One JSON document, written whole and renamed into place, with the
    mode on the temporary file before anything is in it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    os.chmod(target, mode)


def hook_commands(policy: HookPolicy) -> tuple[str, str]:
    """The two hook commands, each carrying the policy file's path."""
    pre = shlex.join([str(policy.python), str(HOOK_SCRIPT), "--policy", str(policy.policy_file)])
    post = shlex.join([str(policy.launcher), "hooks", "post-tool-use", "--policy", str(policy.policy_file)])
    return pre, post


def settings_path_of(paths: RunPaths) -> Path:
    return paths.root / ".claude" / "settings.json"


def _refuse_symlink(settings_path: Path) -> None:
    """The install writes through no symlink: a planted `.claude` or
    `settings.json` pointing elsewhere would put the entries where the
    agent chose (`PATH_INVALID`)."""
    for candidate in (settings_path.parent, settings_path):
        if candidate.is_symlink():
            raise AssayError(
                f"{candidate} is a symlink to {os.readlink(candidate)}, and the install writes nothing through one",
                code="PATH_INVALID",
                hint="replace it with a real directory or file, then install again",
            )


def install_hooks(
    paths: RunPaths,
    *,
    policy_file: Path,
    deny: Sequence[str],
    launcher: Path,
    token_file: Path | None,
) -> Installed:
    """Write the policy file (mode 0600) and the hook entries into the run
    directory's `.claude/settings.json`. The paths the policy pins are
    resolved, the interpreter excepted; `ASSAY` exported in this environment
    is pinned as the value the agent's `"$ASSAY"` is trusted to carry, and
    must be the launcher."""
    for pattern in deny:
        try:
            re.compile(pattern)
        except re.error as error:
            raise AssayError(f"--deny {pattern!r} does not compile: {error}", code="COMMAND_ARGS") from error
    if not HOOK_SCRIPT.is_file():
        raise AssayError(
            f"the hook script {HOOK_SCRIPT} is not there; the hooks ship in the repository, under hooks/",
            code="FILE_NOT_FOUND",
            hint="run the install from a checkout of the repository",
        )
    root = paths.root.resolve()
    if _inside(HOOK_SCRIPT, root):
        raise AssayError(
            f"the hook script {HOOK_SCRIPT} lies inside the run directory, which the agent writes",
            code="PATH_INVALID",
            hint="run the agent in a directory outside the checkout",
        )
    exported = os.environ.get("ASSAY") or None
    if exported is not None and Path(exported).expanduser().resolve() != launcher.resolve():
        raise AssayError(
            f"ASSAY is exported as {exported} and the launcher is {launcher}; the agent's "
            '"$ASSAY" would be trusted as the launcher while naming another program',
            code="COMMAND_ARGS",
            hint=f"export ASSAY={launcher} before the install, or pass --launcher {exported}",
        )
    python = Path(sys.executable)
    policy = HookPolicy(
        run_dir=root,
        anchor_dir=anchor_dir().resolve(),
        token_file=token_file,
        policy_file=policy_file,
        launcher=launcher,
        assay_value=exported,
        deny=tuple(deny),
        protected=installation_paths(launcher, python),
        python=python,
    )
    # The settings file is read and merged before anything is written, so
    # a file that cannot be merged refuses the install whole.
    settings_path = settings_path_of(paths)
    _refuse_symlink(settings_path)
    existing = read_json(settings_path, {})
    if not isinstance(existing, dict):
        raise AssayError(
            f"{settings_path} holds {type(existing).__name__}, not an object",
            code="RECORD_CORRUPT",
            hint="fix .claude/settings.json by hand, then install again",
        )
    pre, post = hook_commands(policy)
    merged, written = merge_hook_entries(existing, pre, post)
    _write_json(policy_file, policy.to_json(), mode=0o600)
    _write_json(settings_path, merged, mode=0o644)
    return Installed(settings=settings_path, policy=policy_file, entries=written, launcher=launcher)


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory)
    except ValueError:
        return False
    return True


# --- the check ------------------------------------------------------------------------


def installed_hooks(paths: RunPaths) -> tuple[Path, Path] | None:
    """The hook script and the policy file the run directory's settings
    name in the harness's own PreToolUse entry, or None when no hooks are
    installed there."""
    settings = read_json(settings_path_of(paths), None)
    if not isinstance(settings, dict):
        return None
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return None
    for entry in hooks.get("PreToolUse") or []:
        if not isinstance(entry, dict):
            continue
        for item in entry.get("hooks") or []:
            command = item.get("command") if isinstance(item, dict) else None
            if not isinstance(command, str) or not OWNED.search(command):
                continue
            try:
                words = shlex.split(command)
                return Path(words[1]), Path(words[words.index("--policy") + 1])
            except (ValueError, IndexError):
                continue
    return None


def _check_failed(problem: str) -> AssayError:
    return AssayError(problem, code="HOOK_CHECK_FAILED", hint=REINSTALL_HINT)


def check_install(paths: RunPaths) -> str:
    """Verify that the hooks installed in a run directory can run: the
    settings name the harness's entry, the policy file reads, the pinned
    interpreter, the script and the launcher are there, and the interpreter
    runs the script on a benign event and allows it. Returns the text of
    the ok line; `HOOK_CHECK_FAILED` names what is wrong. A hook that cannot
    start is a non-blocking error to Claude Code, so without this check the
    mechanism would fail open without a word."""
    installed = installed_hooks(paths)
    if installed is None:
        raise _check_failed(f"no hooks are installed in {paths.root} (no harness entry in .claude/settings.json)")
    script, policy_file = installed
    policy = read_policy(policy_file)
    problems: list[str] = []
    if not policy.python.is_file() or not os.access(policy.python, os.X_OK):
        problems.append(f"the pinned interpreter {policy.python} is not an executable file")
    if not script.is_file():
        problems.append(f"the hook script {script} is not there")
    if not policy.launcher.is_file() or not os.access(policy.launcher, os.X_OK):
        problems.append(f"the launcher {policy.launcher} is not an executable file")
    if problems:
        raise _check_failed("; ".join(problems))
    try:
        completed = subprocess.run(
            [str(policy.python), str(script), "--policy", str(policy_file)],
            input=json.dumps({**PROBE, "cwd": str(policy.run_dir)}),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _check_failed(f"the hook does not run: {type(error).__name__}: {error}") from error
    if completed.returncode != 0 or completed.stdout or completed.stderr:
        said = (completed.stderr or completed.stdout).strip()
        raise _check_failed(f"the hook did not allow a benign event (exit {completed.returncode}): {said}")
    return f"policy {policy_file}; interpreter {policy.python} runs {script}; launcher {policy.launcher}"


# --- the PostToolUse record --------------------------------------------------------


def _receipt_end_event(document: Mapping[str, Any]) -> int | None:
    """The `end_event` of a receipt document: the outermost object when it
    is an act, commit or reset record, or the one under its `receipt` key,
    the wire's result shape; nothing nested elsewhere counts."""
    for candidate in (document, document.get("receipt")):
        if isinstance(candidate, Mapping) and candidate.get("kind") in RECEIPT_KINDS:
            value = candidate.get("end_event")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def end_event_of(response: Any) -> int | None:
    """The receipt's `end_event` in a tool response, when the output carries
    one: a receipt document (a `--json` receipt, the record an
    `mcp__assay__` tool returned, whole or inside a text block), or the
    last `EVENT | e<id>` line of the prose; None otherwise."""
    if isinstance(response, Mapping):
        found = _receipt_end_event(response)
        if found is not None:
            return found
        for key in ("content", "stdout", "text", "result", "output"):
            if key in response:
                found = end_event_of(response[key])
                if found is not None:
                    return found
        return None
    if isinstance(response, list):
        for item in response:
            found = end_event_of(item)
            if found is not None:
                return found
        return None
    if isinstance(response, str):
        return _end_event_in_text(response)
    return None


def _end_event_in_text(text: str) -> int | None:
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            document = json.loads(stripped)
        except ValueError:
            document = None
        if isinstance(document, Mapping):
            found = _receipt_end_event(document)
            if found is not None:
                return found
    for line in stripped.splitlines():
        candidate = line.strip()
        if candidate.startswith("{"):
            try:
                document = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(document, Mapping):
                found = _receipt_end_event(document)
                if found is not None:
                    return found
    matches = EVENT_LINE.findall(text)
    return int(matches[-1]) if matches else None


def command_prefix_of(tool: str, tool_input: Mapping[str, Any]) -> str:
    """The first 80 characters of the Bash command, the file path for the
    editors, the tool name for everything else (the `mcp__assay__` tools)."""
    text = tool
    if tool == "Bash":
        command = tool_input.get("command")
        text = command if isinstance(command, str) else ""
    elif tool in EDITORS:
        path = tool_input.get("file_path")
        if not isinstance(path, str):
            path = tool_input.get("notebook_path")
        text = path if isinstance(path, str) else ""
    return text[:COMMAND_PREFIX_LENGTH]


def _malformed(reason: str) -> AssayError:
    return AssayError(
        f"the hook input {reason}",
        code="HOOK_INPUT_MALFORMED",
        hint="Claude Code sends the PostToolUse event as one JSON object on stdin, with tool_name, tool_input, tool_use_id and session_id",
    )


def tool_use_record(policy: HookPolicy, raw: str) -> dict[str, Any]:
    """The `tool_use` record of one PostToolUse event, from the hook's JSON
    text; `HOOK_INPUT_MALFORMED` when the text is not the event."""
    try:
        payload = json.loads(raw) if raw.strip() else None
    except ValueError as error:
        raise _malformed(f"is not JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise _malformed("is not a JSON object")
    tool = payload.get("tool_name")
    tool_use_id = payload.get("tool_use_id")
    session_id = payload.get("session_id")
    tool_input = payload.get("tool_input")
    for name, value in (("tool_name", tool), ("tool_use_id", tool_use_id), ("session_id", session_id)):
        if not isinstance(value, str) or not value:
            raise _malformed(f"lacks {name}")
    if not isinstance(tool_input, Mapping):
        raise _malformed("lacks tool_input")
    assert isinstance(tool, str) and isinstance(tool_use_id, str) and isinstance(session_id, str)
    return {
        "kind": TOOL_USE,
        "tool_use_id": tool_use_id,
        "session_id": session_id,
        "tool": tool,
        "command_prefix": command_prefix_of(tool, tool_input),
        "end_event": end_event_of(payload.get("tool_response")),
    }


def record_tool_use(policy: HookPolicy, raw: str) -> dict[str, Any]:
    """Append the `tool_use` record to the run's activity log, under the
    file lock, and return it as written (with its timestamp)."""
    record = tool_use_record(policy, raw)
    paths = RunPaths(policy.run_dir)
    require_run(paths)
    return append_jsonl(paths.activity, record)
