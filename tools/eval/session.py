"""The session mode: one run the operator already started, played by one
host session after another until the run says it is over.

    python3 -m tools.eval session --run-dir DIR [--player claude|codex|opencode]
        [--model M] [--hours H] [--max-sessions N] [--max-usd USD] [--prompt FILE]
        [--state-dir DIR] [--owner-token-file FILE] [--assay CMD] [--strip NAME]
        [--no-stop-gate] [--dry-run] [-- COMMAND ...]

The batch mode (`--jobs`) starts the runs of an experiment itself. This mode
takes the run that `/assay:attach`, or an operator's `assay start`, left
waiting for its player, and does what the operator would do by hand
overnight. Before each session:

1. `assay --run-dir RUN start WORLD`, which answers RESUMED while the daemon
   is up and replays the journal when it is gone. A world without replay
   cannot come back, and the mode ends START_FAILED.
2. The player's session, a subprocess in the run directory with the prompt
   (`PLAYER.md` by default) as its argument. Its environment is the runner's
   without the names the pinned registry lists under `secrets` and without
   ASSAY_OWNER_TOKEN_FILE, so the world's credentials stay in the daemon's
   environment and the player has no route around the adapter. `ASSAY` names
   the launcher, and `ASSAY_STOP_GATE` the Stop gate's state file.

When a session ends, the runner reads the run with the Stop gate's own rules
(`hooks/stop_gate.py`, loaded by path):

- WIN, the action cap or the dollar cap: the run is over, `assay stop` takes
  the daemon down, and the mode ends with that outcome.
- An owner action the session left pending (an approval or a waiver): the
  mode ends OWNER_NEEDED and leaves the daemon up. The owner acts, and the
  same command resumes.
- A stop the player declared in `.assay/NOTES.md`: SELF_STOP, daemon up.
- Otherwise the next session starts, up to `--max-sessions` launches, the
  `--hours` of wall clock and the `--max-usd` of this invocation. A session
  a usage limit cut short pauses the runner and does not count. Two sessions
  in a row that exit non-zero without a paid action end the mode
  PLAYER_FAILING.

For the `claude` host the runner merges the Stop gate into the run
directory's `.claude/settings.json` before the first session. Codex and
OpenCode get no gate in this version, and their templates are untested; for
them the relaunch is the gate. Every session's record carries the host and
its `--version`. A `claude -p` session's bill (its report's dollars and
tokens) is posted to the run with `assay spend report`, so `assay status`
shows it and a registry's dollar cap applies.

A relaunched session's prompt gains an operator's note when the owner acted
since the previous session: an approval still held by the daemon (it lasts
600 seconds and one act), a waiver, a ratified goal.

The state directory (default `<run dir>.sessions`, beside the run directory,
outside the player's): `sessions.json` (the ledger, one record per session),
`s<n>.prompt.md`, `s<n>.start.log`, `s<n>.player.out`, `s<n>.player.err`,
`status.json`, `stop_gate.json`, `runner.lock`, and `STOP` (create it to end
the mode after the current session). SIGINT and SIGTERM end the current
session at once.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fcntl
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import IO, Any

from . import EvalError
from .results import player_report, session_tokens
from .runner import DEFAULT_LAUNCHER, KILL_GRACE_SECONDS, REPO, looks_rate_limited, now

GATE_SCRIPT = REPO / "hooks" / "stop_gate.py"
# The gate's entry in the settings, by its command text.
GATE_ENTRY = re.compile(r"stop_gate\.py'? --run-dir ")
# The kernel's PreToolUse entry and the policy file it names.
POLICY_ENTRY = re.compile(r"pre_tool_use\.py'? --policy ")
PROMPT_NAME = "PLAYER.md"
STATE_SUFFIX = ".sessions"
TOKEN_VARIABLES = ("ASSAY_OWNER_TOKEN_FILE",)
VERSION_TIMEOUT_SECONDS = 60
START_TIMEOUT_SECONDS = 900
STOP_TIMEOUT_SECONDS = 120
SPEND_TIMEOUT_SECONDS = 120
APPROVAL_SECONDS = 600
FINISHED = ("WIN", "CAP", "USD_CAP")
NOTE_HEADER = "A note from the operator for this session (the session runner wrote it; it is not from the world):"


@dataclasses.dataclass(frozen=True)
class Host:
    """A player's host as an argv template: `{prompt}` is the prompt's text
    as one argument, `{options}` the model and dollar flags when given."""

    name: str
    command: tuple[str, ...]
    model: tuple[str, ...]
    usd: tuple[str, ...] = ()
    gate: bool = False
    tested: bool = False


HOSTS: dict[str, Host] = {
    # The prompt follows `-p` directly: the tool lists are variadic and
    # would take a prompt placed after them.
    "claude": Host(
        name="claude",
        command=(
            "claude", "-p", "{prompt}", "{options}", "--output-format", "json",
            "--permission-mode", "acceptEdits",
            "--allowedTools", "Bash", "Read", "Glob", "Grep", "Write(./**)", "Edit(./**)",
            "--disallowedTools", "WebFetch", "WebSearch", "Agent",
        ),
        model=("--model", "{model}"),
        usd=("--max-budget-usd", "{usd}"),
        gate=True,
        tested=True,
    ),
    # The sandbox writes the workspace; its network is on so that the
    # launcher reaches the daemon's socket. Untested in this version.
    "codex": Host(
        name="codex",
        command=(
            "codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write",
            "-c", "sandbox_workspace_write.network_access=true", "{options}", "{prompt}",
        ),
        model=("--model", "{model}"),
    ),
    # `--auto` answers the permission requests a headless run would reject.
    # Untested in this version.
    "opencode": Host(
        name="opencode",
        command=("opencode", "run", "--format", "json", "--auto", "{options}", "{prompt}"),
        model=("--model", "{model}"),
    ),
}


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


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Any, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _seconds_since(stamp: Any) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        then = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - then).total_seconds()


def _clock(stamp: str) -> str:
    """An activity timestamp as a time of day, UTC."""
    try:
        then = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return "an unrecorded time"
    if then.tzinfo is not None:
        then = then.astimezone(dt.timezone.utc)
    return then.strftime("%H:%M:%S UTC")


def owner_note(activity: list[dict[str, Any]], since: int, *, daemon_kept: bool) -> list[str]:
    """What the owner did that the next session should know: an approval
    the daemon still holds (granted less than 600 seconds ago and not used,
    on a daemon the start kept), and the waivers and ratifications since
    record `since`."""
    held: dict[str, str] = {}
    lines: list[str] = []
    for index, record in enumerate(activity):
        kind = record.get("kind")
        action = str(record.get("action") or "").upper()
        if kind == "approval_granted" and action:
            held[action] = str(record.get("timestamp") or "")
        elif kind == "approval_used" and action:
            held.pop(action, None)
        elif index < since:
            continue
        elif kind == "liveness_waived" and action:
            lines.append(f"the owner waived the rehearsal quota of {action}, because: {record.get('because')}")
        elif kind == "goal_ratified":
            lines.append(f"the owner ratified goal proposal #{record.get('id')}: {record.get('text')}")
    approvals: list[str] = []
    if daemon_kept:
        for action, stamp in held.items():
            age = _seconds_since(stamp)
            if age is not None and age >= APPROVAL_SECONDS:
                continue
            approvals.append(
                f"the owner granted one use of {action} at {_clock(stamp)}; an approval is used up by "
                f"one act and expires {APPROVAL_SECONDS} seconds after it is granted"
            )
    return approvals + lines


class SessionRunner:
    def __init__(
        self,
        run_dir: Path,
        *,
        host: str,
        command: list[str] | None = None,
        model: str | None = None,
        prompt: Path | None = None,
        state_dir: Path | None = None,
        launcher: list[str] | None = None,
        owner_token_file: Path | None = None,
        max_sessions: int = 5,
        hours: float = 8.0,
        max_usd: float | None = None,
        strip: list[str] | None = None,
        stop_gate: bool = True,
        poll_seconds: float = 5.0,
        pause_minutes: float = 30.0,
    ):
        self.run_dir = Path(run_dir).expanduser().resolve()
        if host not in HOSTS and command is None:
            raise EvalError(f"--player {host} is not one of {', '.join(sorted(HOSTS))}")
        self.host = HOSTS.get(host)
        self.custom = list(command) if command else None
        self.model = model
        self.prompt = Path(prompt).expanduser().resolve() if prompt else self.run_dir / PROMPT_NAME
        self.state = (
            Path(state_dir).expanduser().resolve() if state_dir
            else self.run_dir.with_name(self.run_dir.name + STATE_SUFFIX)
        )
        self.launcher = list(launcher) if launcher else self.pinned_launcher()
        self.launcher_text = shlex.join(self.launcher)
        default_token = Path.home() / "assay" / "tokens" / f"{self.run_dir.name}.token"
        self.token_file = owner_token_file or (default_token if default_token.is_file() else None)
        self.max_sessions = max_sessions
        self.hours = hours
        self.max_usd = max_usd
        self.strip = list(strip or [])
        self.gate_wanted = stop_gate and self.custom is None and self.host is not None and self.host.gate
        self.poll_seconds = poll_seconds
        self.pause_minutes = pause_minutes
        self.gate = load_gate()
        self.signalled: int | None = None
        self.version = ""
        self._lock: IO[str] | None = None
        self.host_name = host if self.custom is None else self.custom_name()

    def custom_name(self) -> str:
        """A player command's name for the record: its program, or the
        script an interpreter runs."""
        words = self.fill(self.custom or [], prompt="", session=0)
        name = Path(words[0]).name if words else "custom"
        if name.startswith("python") and len(words) > 1 and not words[1].startswith("-"):
            name = Path(words[1]).name
        return name

    # ---- places ---------------------------------------------------------------
    def file(self, session: int, suffix: str) -> Path:
        return self.state / f"s{session}.{suffix}"

    @property
    def ledger_path(self) -> Path:
        return self.state / "sessions.json"

    @property
    def status_path(self) -> Path:
        return self.state / "status.json"

    @property
    def gate_state(self) -> Path:
        return self.state / "stop_gate.json"

    @property
    def stop_file(self) -> Path:
        return self.state / "STOP"

    @property
    def settings_path(self) -> Path:
        return self.run_dir / ".claude" / "settings.json"

    # ---- the run -----------------------------------------------------------------
    def config(self) -> dict[str, Any]:
        config = _read_json(self.run_dir / ".assay" / "config.json")
        if not isinstance(config, dict) or not isinstance(config.get("game_id"), str):
            raise EvalError(
                f"{self.run_dir} holds no started run; start one first (/assay:attach, or `assay start`)"
            )
        return config

    def view(self) -> Any:
        return self.gate.load_view(self.run_dir)

    def policy_file(self) -> Path | None:
        """The hook policy the run's PreToolUse entry names, if any."""
        settings = _read_json(self.settings_path)
        hooks = settings.get("hooks") if isinstance(settings, dict) else None
        entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            for item in entry.get("hooks") or [] if isinstance(entry, dict) else []:
                command = item.get("command") if isinstance(item, dict) else None
                if isinstance(command, str) and POLICY_ENTRY.search(command):
                    try:
                        words = shlex.split(command)
                        return Path(words[words.index("--policy") + 1])
                    except (ValueError, IndexError):
                        continue
        return None

    def pinned_launcher(self) -> list[str]:
        """The launcher the run's hooks pin (the player's `"$ASSAY"` must
        be that one), else `bin/assay` of this checkout."""
        policy_file = self.policy_file()
        policy = _read_json(policy_file) if policy_file else None
        if isinstance(policy, dict):
            for key in ("assay_value", "launcher"):
                value = policy.get(key)
                if isinstance(value, str) and value:
                    return [value]
        return [str(DEFAULT_LAUNCHER)]

    def secret_names(self) -> list[str]:
        registry = _read_json(self.run_dir / ".assay" / "registry.json")
        names = registry.get("secrets") if isinstance(registry, dict) else None
        listed = [name for name in names if isinstance(name, str)] if isinstance(names, list) else []
        return sorted({*listed, *TOKEN_VARIABLES, *self.strip})

    def player_environment(self) -> tuple[dict[str, str], list[str]]:
        """The runner's environment without the world's secrets and the
        owner token's variable, with `ASSAY` and `ASSAY_STOP_GATE` set; and
        the names it removed (never their values)."""
        names = set(self.secret_names())
        env = {key: value for key, value in os.environ.items() if key not in names}
        env["ASSAY"] = self.launcher_text
        if self.gate_wanted:
            env[self.gate.STATE_VARIABLE] = str(self.gate_state)
        else:
            env.pop(self.gate.STATE_VARIABLE, None)
        return env, sorted(name for name in names if name in os.environ)

    # ---- the host ------------------------------------------------------------------
    def binary(self) -> str:
        if self.custom is not None:
            return self.fill(self.custom, prompt="", session=0)[0]
        assert self.host is not None
        return self.host.command[0]

    def host_version(self) -> str:
        """The first line `HOST --version` prints. A named host that does not
        run is refused before anything is touched."""
        argv = [self.binary(), "--version"]
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, timeout=VERSION_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            if self.custom is not None:
                raise EvalError(f"the player's command `{argv[0]}` is not found") from None
            raise EvalError(f"the host `{argv[0]}` is not on PATH; install it, or pass --player") from None
        except (OSError, subprocess.TimeoutExpired) as error:
            raise EvalError(f"`{shlex.join(argv)}` did not run: {error}") from None
        lines = (done.stdout.strip() or done.stderr.strip()).splitlines()
        first = lines[0].strip() if lines else ""
        if done.returncode != 0:
            if self.custom is not None:
                return first or f"unknown (exit {done.returncode})"
            raise EvalError(f"the host does not run: `{shlex.join(argv)}` exited {done.returncode}: {first}")
        return first

    def fill(self, template: list[str], *, prompt: str, session: int, usd: float | None = None) -> list[str]:
        """The argv of a template: `{options}` expands to the model and
        dollar flags, the other placeholders to their values."""
        values = {
            "prompt": prompt, "prompt_file": str(self.file(session, "prompt.md")), "run_dir": str(self.run_dir),
            "model": self.model or "", "assay": self.launcher_text, "repo": str(REPO), "python": sys.executable,
            "session": str(session), "usd": "" if usd is None else f"{usd:.2f}",
        }
        options: list[str] = []
        if self.host is not None and self.custom is None:
            if self.model:
                options += list(self.host.model)
            if usd is not None:
                options += list(self.host.usd)
        argv: list[str] = []
        for argument in template:
            if argument == "{options}":
                argv += [re.sub(r"\{([a-z_]+)\}", lambda match: values[match.group(1)], option) for option in options]
            else:
                argv.append(re.sub(r"\{([a-z_]+)\}", lambda match: values[match.group(1)], argument))
        return argv

    def player_argv(self, session: int, prompt: str, usd: float | None) -> list[str]:
        if self.custom is not None:
            return self.fill(self.custom, prompt=prompt, session=session)
        assert self.host is not None
        return self.fill(list(self.host.command), prompt=prompt, session=session, usd=usd)

    def player_text(self, session: int, usd: float | None) -> str:
        """The player's command as a shell line, the prompt shown as the
        file it is written to."""
        marker = "\0PROMPT\0"
        prompt_file = shlex.quote(str(self.file(session, "prompt.md")))
        text = " ".join(
            f'"$(cat {prompt_file})"' if argument == marker else shlex.quote(argument)
            for argument in self.player_argv(session, marker, usd)
        )
        return text + (f" < {prompt_file}" if self.custom is not None else "")

    # ---- the Stop gate ---------------------------------------------------------------
    def gate_command(self) -> str:
        return shlex.join([sys.executable, str(GATE_SCRIPT), "--run-dir", str(self.run_dir)])

    def install_gate(self) -> None:
        """Merge the gate's Stop entry into the run directory's settings:
        every other key and entry kept, an earlier gate entry replaced, no
        write through a symlink."""
        for candidate in (self.settings_path.parent, self.settings_path):
            if candidate.is_symlink():
                raise EvalError(f"{candidate} is a symlink, and the gate writes through none")
        settings = _read_json(self.settings_path) if self.settings_path.exists() else {}
        if not isinstance(settings, dict):
            raise EvalError(f"{self.settings_path} does not hold a JSON object; fix it by hand")
        hooks = settings.get("hooks", {})
        stop = hooks.get("Stop", []) if isinstance(hooks, dict) else None
        if not isinstance(stop, list):
            raise EvalError(f"{self.settings_path}: `hooks` or `hooks.Stop` has the wrong type; fix it by hand")
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
        ours = {"hooks": [{"type": "command", "command": self.gate_command(), "timeout": 60}]}
        _write_json(self.settings_path, {**settings, "hooks": {**hooks, "Stop": [*kept, ours]}})

    def write_launch(self, session: int, view: Any) -> dict[str, int]:
        """The counts at the session's launch, for the gate and the ledger."""
        counts = {
            "activity": len(view.activity) if view else 0,
            "declarations": len(self.gate.declarations(view.notes)) if view else 0,
            "paid": view.paid if view else 0,
        }
        _write_json(self.gate_state, {
            "version": self.gate.STATE_VERSION, "launch": {"session": session, "at": now(), **counts},
            "blocks": 0, "paid_at_last_block": None, "decisions": [],
        })
        return counts

    # ---- the ledger and the status ----------------------------------------------------
    def ledger(self) -> list[dict[str, Any]]:
        data = _read_json(self.ledger_path)
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []

    def append_ledger(self, record: dict[str, Any]) -> None:
        _write_json(self.ledger_path, [*self.ledger(), record])

    def write_status(self, **fields: Any) -> None:
        current = _read_json(self.status_path)
        base = current if isinstance(current, dict) else {}
        _write_json(self.status_path, {
            **base, "updated": now(), "run_dir": str(self.run_dir), "state_dir": str(self.state),
            "host": self.host_name, "host_version": self.version, "model": self.model, "pid": os.getpid(),
            **fields,
        })

    def acquire_lock(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        handle = (self.state / "runner.lock").open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            holder = handle.read().strip()
            handle.close()
            raise EvalError(
                f"another session runner holds {self.state / 'runner.lock'}"
                + (f" ({holder})" if holder else "") + "; one runner per run"
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid {os.getpid()} since {now()}\n")
        handle.flush()
        self._lock = handle

    def release_lock(self) -> None:
        if self._lock is not None:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    # ---- one session ---------------------------------------------------------------------
    def launcher_command(self, *words: str) -> list[str]:
        return [*self.launcher, "--run-dir", str(self.run_dir), *words]

    def start_run(self, session: int, world: str) -> str:
        """The operator's start before the session; its first line."""
        command = self.launcher_command("start", world)
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=START_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise EvalError(f"`assay start` did not run: {error}") from None
        self.file(session, "start.log").write_text(f"$ {shlex.join(command)}\n{done.stdout}{done.stderr}")
        lines = (done.stdout + done.stderr).strip().splitlines()
        if done.returncode != 0:
            said = [line for line in lines if line.startswith("ERROR")] or lines[-1:]
            raise EvalError(f"`assay start` exited {done.returncode}: " + (said[0] if said else "no output"))
        return next((line for line in lines if line.startswith(("RESUMED", "RECOVERED", "STARTED"))), lines[0] if lines else "")

    def stop_daemon(self) -> str:
        command = self.launcher_command("stop")
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=STOP_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"`assay stop` failed: {error}"
        return (done.stdout + done.stderr).strip().splitlines()[0] if (done.stdout + done.stderr).strip() else ""

    def post_spend(self, session: int, report: dict[str, Any] | None, started: str) -> str | None:
        """The session's bill posted to the run, when its report has one."""
        cost = (report or {}).get("total_cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            return None
        tokens = session_tokens(report)
        ident = (report or {}).get("session_id")
        report_id = f"{self.host_name}-{ident}" if isinstance(ident, str) and ident else f"{self.host_name}-s{session}-{started}"
        command = self.launcher_command(
            "spend", "report", "--usd", f"{float(cost):.6f}", "--tokens", str(tokens["total"] if tokens else 0),
            "--id", report_id,
        )
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=SPEND_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"not posted: {error}"
        said = (done.stdout + done.stderr).strip().splitlines()
        return said[0] if said else f"exit {done.returncode}"

    def prompt_for(self, session: int, start_line: str, view: Any) -> str:
        try:
            text = self.prompt.read_text()
        except OSError as error:
            raise EvalError(f"the prompt {self.prompt} cannot be read: {error}") from None
        previous = self.ledger()
        since = int(previous[-1].get("launch", {}).get("activity", 0)) if previous else 0
        lines = owner_note(list(view.activity) if view else [], since, daemon_kept=start_line.startswith("RESUMED"))
        if lines:
            text = text.rstrip("\n") + "\n\n" + NOTE_HEADER + "\n" + "".join(f"- {line}\n" for line in lines)
        return text

    def _kill(self, proc: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + KILL_GRACE_SECONDS
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()

    def play(self, session: int, world: str, deadline: float, usd: float | None) -> dict[str, Any]:
        """One session: the start, the launch counts, the player until it
        exits (or the wall clock or a signal ends it), and its record."""
        start_line = self.start_run(session, world)
        view = self.view()
        prompt = self.prompt_for(session, start_line, view)
        prompt_file = self.file(session, "prompt.md")
        prompt_file.write_text(prompt)
        counts = self.write_launch(session, view)
        env, stripped = self.player_environment()
        argv = self.player_argv(session, prompt, usd)
        started, clock = now(), time.monotonic()
        with self.file(session, "player.out").open("wb") as out, self.file(session, "player.err").open("wb") as err:
            stdin: Any = prompt_file.open("rb") if self.custom is not None else subprocess.DEVNULL
            try:
                proc = subprocess.Popen(
                    argv, cwd=self.run_dir, env=env, stdin=stdin, stdout=out, stderr=err, start_new_session=True,
                )
            except OSError as error:
                raise EvalError(f"the player could not start: {error}") from None
            finally:
                if stdin is not subprocess.DEVNULL:
                    stdin.close()
            paid = counts["paid"]
            self.write_status(state="running", session=session, player_pid=proc.pid, outcome=None)
            print(
                f"LAUNCH | session {session} | pid {proc.pid} | {start_line.split(' | ')[0] or 'started'} | "
                f"paid actions {paid} so far",
                flush=True,
            )
            timed_out = False
            interrupted = False
            while proc.poll() is None:
                if self.signalled is not None:
                    interrupted = True
                    self._kill(proc)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._kill(proc)
                    break
                time.sleep(self.poll_seconds)
            code = int(proc.wait())
        stdout = self.file(session, "player.out").read_text(errors="replace")
        stderr = self.file(session, "player.err").read_text(errors="replace")
        report = player_report(stdout)
        after = self.view()
        tokens = session_tokens(report)
        cost = (report or {}).get("total_cost_usd")
        record: dict[str, Any] = {
            "session": session, "host": self.host_name, "host_version": self.version, "model": self.model,
            "started": started, "ended": now(), "wall_seconds": round(time.monotonic() - clock, 1),
            "exit_code": code, "timed_out": timed_out, "interrupted": interrupted,
            "rate_limited": looks_rate_limited(report, stderr[-4000:], code) and not interrupted,
            "start": start_line, "command": self.player_text(session, usd), "prompt_file": str(prompt_file),
            "stripped": stripped, "launch": counts, "paid_at_exit": after.paid if after else 0,
            "state_at_exit": after.state if after else None,
            "dollars": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
            "tokens": tokens, "report": report,
            "stdout_tail": stdout[-4000:] if report is None else None, "stderr_tail": stderr[-4000:],
        }
        gate_state = _read_json(self.gate_state) if self.gate_wanted else None
        record["gate"] = gate_state.get("decisions") if isinstance(gate_state, dict) else None
        record["spend"] = self.post_spend(session, report, started)
        return record

    # ---- the loop -------------------------------------------------------------------------
    def owner_lines(self, pending: list[dict[str, str]]) -> list[str]:
        token = str(self.token_file) if self.token_file else "<your token file>"
        lines = []
        for item in pending:
            action = item.get("action") or "<ACTION>"
            lines.append(f"OWNER | {item.get('error') or item.get('code')}")
            if item.get("owner") == "approve":
                lines.append(
                    f"OWNER | to grant one use: {self.launcher_text} --run-dir {shlex.quote(str(self.run_dir))} "
                    f"approve {action} --token-file {shlex.quote(token)}"
                )
            else:
                lines.append(
                    f"OWNER | to waive the rehearsal: {self.launcher_text} --run-dir {shlex.quote(str(self.run_dir))} "
                    f"waive {action} --token-file {shlex.quote(token)} --because \"<why it is safe now>\""
                )
        return lines

    def check(self) -> str:
        """The refusals before anything is launched; returns the world id."""
        world = str(self.config()["game_id"])
        if not self.prompt.is_file():
            raise EvalError(
                f"no prompt at {self.prompt}: write the player's prompt (skills/attach/start.md), or pass --prompt FILE"
            )
        self.version = self.host_version()
        return world

    def warnings(self) -> list[str]:
        found = []
        if self.custom is None and self.host is not None and not self.host.tested:
            found.append(
                f"WARN | the {self.host.name} template is untested in this version; read the first session's "
                f"output in {self.state}"
            )
        if self.custom is None and self.host is not None and self.host.gate and self.policy_file() is None:
            found.append(
                "WARN | no PreToolUse hooks are installed in the run directory, so the player's tools "
                "are not policed (`assay hooks install`, skills/attach/start.md)"
            )
        if self.custom is None and self.host is not None and not self.host.gate:
            found.append(f"WARN | the {self.host.name} host gets no Stop gate in this version; the relaunch is its gate")
        return found

    def dry_run(self) -> int:
        world = self.check()
        _, stripped = self.player_environment()
        print(
            f"DRY RUN | session mode | run {self.run_dir} | world {world} | host {self.host_name} {self.version} | "
            f"model {self.model or 'the host default'} | up to {self.max_sessions} session(s) in {self.hours:g}h"
            + (f" | up to ${self.max_usd:.2f}" if self.max_usd is not None else "")
        )
        for line in self.warnings():
            print(line)
        print(f"  {shlex.join(self.launcher_command('start', world))}")
        session = len(self.ledger()) + 1
        gate = f" {self.gate.STATE_VARIABLE}={shlex.quote(str(self.gate_state))}" if self.gate_wanted else ""
        print(
            f"  (cd {shlex.quote(str(self.run_dir))} && ASSAY={shlex.quote(self.launcher_text)}{gate} "
            f"{self.player_text(session, self.max_usd)})"
        )
        print(f"  kept from the player's environment: {', '.join(stripped) or 'nothing set'}")
        if self.gate_wanted:
            print(f"  the Stop gate in {self.settings_path}: {self.gate_command()}")
        print(f"DRY RUN | state {self.state} | nothing written")
        return 0

    def run(self) -> int:
        world = self.check()
        self.acquire_lock()
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def on_signal(signum: int, _frame: Any) -> None:
            self.signalled = signum

        try:
            for sig in previous:
                signal.signal(sig, on_signal)
            if self.gate_wanted:
                self.install_gate()
            print(
                f"SESSIONS | {self.run_dir} | world {world} | host {self.host_name} {self.version} | model "
                f"{self.model or 'the host default'} | up to {self.max_sessions} session(s) in {self.hours:g}h"
                + (f" or ${self.max_usd:.2f}" if self.max_usd is not None else "") + f" | state {self.state}",
                flush=True,
            )
            for line in self.warnings():
                print(line, flush=True)
            if self.gate_wanted:
                print(f"GATE | the Stop gate is in {self.settings_path}", flush=True)
            deadline = time.monotonic() + self.hours * 3600.0
            launched = 0
            spent = 0.0
            failing = 0
            outcome: str | None = None
            detail = ""
            pending: list[dict[str, str]] = []
            relaunch: int | None = None
            while outcome is None:
                view = self.view()
                verdict = self.gate.finished(view) if view else None
                if verdict:
                    outcome = verdict
                elif self.signalled is not None or self.stop_file.exists():
                    outcome = "STOPPED"
                    if self.stop_file.exists():
                        detail = f"{self.stop_file} is there; delete it before the next launch"
                elif launched >= self.max_sessions:
                    outcome = "SESSIONS_EXHAUSTED"
                elif time.monotonic() >= deadline:
                    outcome = "TIME_EXHAUSTED"
                elif self.max_usd is not None and spent >= self.max_usd:
                    outcome = "USD_EXHAUSTED"
                if outcome is not None:
                    break
                if relaunch is not None:
                    print(
                        f"RELAUNCH | session {relaunch} ended without WIN, the cap, an owner action or a "
                        "stop declaration",
                        flush=True,
                    )
                    relaunch = None
                session = len(self.ledger()) + 1
                usd_left = None if self.max_usd is None else round(self.max_usd - spent, 2)
                try:
                    record = self.play(session, world, deadline, usd_left)
                except EvalError as error:
                    outcome, detail = "START_FAILED", str(error)
                    break
                after = self.view()
                pending = self.gate.pending_owner_actions(after.activity, record["launch"]["activity"]) if after else []
                said = self.gate.declarations(after.notes) if after else []
                new_paid = record["paid_at_exit"] - record["launch"]["paid"]
                if record["rate_limited"]:
                    record["ended_because"] = "RATE_LIMITED"
                else:
                    launched += 1
                    spent += record["dollars"] or 0.0
                    failing = failing + 1 if record["exit_code"] != 0 and new_paid <= 0 else 0
                    if after is not None and self.gate.finished(after):
                        record["ended_because"] = self.gate.finished(after)
                    elif record["interrupted"]:
                        record["ended_because"] = outcome = "STOPPED"
                    elif pending:
                        record["ended_because"] = outcome = "OWNER_NEEDED"
                    elif len(said) > record["launch"]["declarations"]:
                        record["ended_because"] = outcome = "SELF_STOP"
                        detail = said[-1]
                    elif record["timed_out"]:
                        record["ended_because"] = outcome = "TIME_EXHAUSTED"
                    elif failing >= 2:
                        record["ended_because"] = outcome = "PLAYER_FAILING"
                        detail = f"exit {record['exit_code']} twice with no paid action; read {self.file(session, 'player.err')}"
                    else:
                        record["ended_because"] = "UNFINISHED"
                record["owner_pending"] = pending
                record["declaration"] = said[-1] if len(said) > record["launch"]["declarations"] else None
                self.append_ledger(record)
                cost = f"${record['dollars']:.2f}" if record["dollars"] is not None else "cost not reported"
                print(
                    f"END | session {session} | exit {record['exit_code']} | {record['wall_seconds'] / 60:.1f} min | "
                    f"paid actions {record['launch']['paid']} to {record['paid_at_exit']} | {cost} | "
                    f"{record['ended_because']}",
                    flush=True,
                )
                if record["rate_limited"]:
                    print(
                        f"PAUSE | a usage limit was signalled; the runner waits {self.pause_minutes:g} min, "
                        "and the session does not count",
                        flush=True,
                    )
                    self.write_status(state="paused", session=session, player_pid=None)
                    resume_at = time.monotonic() + self.pause_minutes * 60.0
                    while time.monotonic() < min(resume_at, deadline):
                        if self.signalled is not None or self.stop_file.exists():
                            break
                        time.sleep(min(self.poll_seconds, 5.0))
                elif record["ended_because"] == "UNFINISHED":
                    relaunch = session
            return self.finish(outcome, detail, pending, launched, spent)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            self.release_lock()

    def finish(self, outcome: str, detail: str, pending: list[dict[str, str]], launched: int, spent: float) -> int:
        view = self.view()
        if outcome in FINISHED:
            said = self.stop_daemon()
            daemon = "the daemon has finished" if outcome == "WIN" else f"the daemon is stopped ({said})"
        elif outcome == "START_FAILED":
            daemon = "no session was played; the detail says why"
        else:
            daemon = "the daemon is left up; the same command resumes the run"
        if outcome == "OWNER_NEEDED":
            for line in self.owner_lines(pending):
                print(line, flush=True)
        if outcome == "SELF_STOP":
            print(f"DECLARED | {detail}", flush=True)
        elif detail:
            print(f"DETAIL | {detail}", flush=True)
        cap = view.cap if view and view.cap is not None else "no cap"
        paid = view.paid if view else 0
        self.write_status(
            state="ended", outcome=outcome, detail=detail, owner_pending=pending, player_pid=None,
            sessions=launched, dollars=round(spent, 6), paid=paid, ended=now(),
        )
        print(
            f"OUTCOME | {outcome} | {launched} session(s) | paid actions {paid}/{cap} | "
            f"${spent:.2f} reported by the host | {daemon}",
            flush=True,
        )
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.eval session",
        description="Play one run the operator already started, one host session after another: relaunch on exit, "
        "end at WIN or the cap, pause at an owner action or a declared stop. A player command after `--` replaces "
        "the host's template (placeholders {prompt}, {prompt_file}, {run_dir}, {model}, {assay}, {repo}, {python}, "
        "{session}); it also gets the prompt on stdin.",
    )
    parser.add_argument("--run-dir", type=Path, required=True, help="the run directory (/assay:attach started it)")
    parser.add_argument("--player", default="claude", choices=sorted(HOSTS), help="the host (default: claude)")
    parser.add_argument("--model", help="the model, as the host names it (default: the host's own)")
    parser.add_argument("--hours", type=float, default=8.0, metavar="H", help="wall clock for this invocation (default 8)")
    parser.add_argument("--max-sessions", type=int, default=5, metavar="N", help="launches in this invocation (default 5)")
    parser.add_argument("--max-usd", type=float, metavar="USD", help="the host's bill for this invocation, as its reports say it")
    parser.add_argument("--prompt", type=Path, metavar="FILE", help="the player's prompt (default: <run dir>/PLAYER.md)")
    parser.add_argument("--state-dir", type=Path, metavar="DIR", help="the ledger and the logs (default: <run dir>.sessions)")
    parser.add_argument(
        "--owner-token-file", type=Path, metavar="FILE",
        help="named in the owner commands the runner prints, never read (default: ~/assay/tokens/<run name>.token when it exists)",
    )
    parser.add_argument("--assay", metavar="CMD", help="the launcher (default: the one the run's hooks pin, else bin/assay)")
    parser.add_argument("--strip", action="append", default=[], metavar="NAME", help="another variable to keep from the player")
    parser.add_argument("--no-stop-gate", action="store_true", help="do not install the Stop gate")
    parser.add_argument("--dry-run", action="store_true", help="print what would run; write nothing")
    parser.add_argument("--poll-seconds", type=float, default=5.0, metavar="S")
    parser.add_argument("--pause-minutes", type=float, default=30.0, metavar="M", help="the wait after a usage-limit signal")
    return parser


def main(argv: list[str]) -> int:
    separated = "--" in argv
    command: list[str] = []
    if separated:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1:]
    args = _parser().parse_args(argv)
    try:
        if args.max_sessions < 1:
            raise EvalError("--max-sessions must be at least 1")
        if args.hours <= 0:
            raise EvalError("--hours must be positive")
        if args.max_usd is not None and args.max_usd <= 0:
            raise EvalError("--max-usd must be positive")
        if separated and not command:
            raise EvalError("`--` must be followed by the player's command")
        launcher = shlex.split(args.assay) if args.assay else None
        if args.assay is not None and not launcher:
            raise EvalError("--assay must name a command")
        runner = SessionRunner(
            args.run_dir, host=args.player, command=command or None, model=args.model, prompt=args.prompt,
            state_dir=args.state_dir, launcher=launcher, owner_token_file=args.owner_token_file,
            max_sessions=args.max_sessions, hours=args.hours, max_usd=args.max_usd, strip=args.strip,
            stop_gate=not args.no_stop_gate, poll_seconds=args.poll_seconds, pause_minutes=args.pause_minutes,
        )
        if args.dry_run:
            return runner.dry_run()
        return runner.run()
    except EvalError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
