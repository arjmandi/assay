from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import shutil
import sys
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NoReturn

from . import JOURNAL_SPEC, __version__
from .agenda import (
    grant_approval,
    grant_waiver,
    list_proposals,
    mint_owner_token,
    propose_goal,
    ratify_goal,
)
from .analysis import run_python
from .broker import (
    broker_gated,
    broker_install_module,
    broker_matches_latest_event,
    broker_ping,
    check_adapter_spec,
    find_daemon,
    is_remote_config,
    resolve_adapter_spec,
    reconcile_mutations,
    start_broker,
    stop_broker,
)
from .carryover import (
    binding_hash_of,
    export_knowledge,
    import_knowledge,
    registry_hash_of,
)
from .channels import channel_lines, declare_channel, known_channels, load_declared
from .integrity import anchor_line, anchor_status, audit, audit_lines, environment_anchor_file
from .model import (
    fit_lines,
    init_model,
    replay_model,
    solve_model,
)
from .modules import (
    ModuleView,
    active_modules,
    pin_external_modules,
    reconstruct_manifest,
    unlisted_lines,
)
from .sandbox import (
    FORCE_VARIABLE,
    PROCESS_ISOLATION_ONLY,
    check_imports,
    sandbox_mode,
    sandbox_text,
)
from .core import (
    LOCAL_MODE,
    REMOTE_MODE,
    AssayError,
    CommandStatus,
    RunPaths,
    append_jsonl,
    atomic_json,
    command_status,
    load_jsonl,
    normalize_game_id,
    now_iso,
    read_json,
    require_run,
    run_lock,
)
from .extras import require_kind
from .inspect import result_text, status_text, view_text
from .ops import (
    ACT,
    COMMIT,
    INSTALL_MODULE,
    RESET,
    ActRequest,
    CommitRequest,
    Operation,
    ReceiptResult,
    Req,
    ResetRequest,
)
from .predictions import claims_help
from .registry import gate_mode, load_registry_file, spend_reports, validate_registry
from .run import Run


class Parser(argparse.ArgumentParser):
    """argparse with the kernel's error voice, and an epilog that is built only
    when help is rendered, so listing the claim forms of an installed
    observation kind never imports that kind on an ordinary command."""

    lazy_epilog: Any = None

    def error(self, message: str) -> NoReturn:
        raise AssayError(message)

    def format_help(self) -> str:
        if self.lazy_epilog is not None:
            self.epilog = self.lazy_epilog()
        return super().format_help()


# --- the command-line surface -------------------------------------------------
#
# Two tables at the end of this module, in the order the command line lists
# them: `LIFECYCLE`, the commands that run before any run is loaded, and
# `COMMANDS`, the commands over the loaded run, each the client of a daemon
# operation of the wire table (`ops.py`) or an offline function. The parser
# is assembled from them, and `assay --help` and every sub-command's help
# render byte for byte as the hand-written parser rendered them
# (tests/test_cli_help.py).

LifecycleRun = Callable[[RunPaths, argparse.Namespace], int]
CommandRun = Callable[[RunPaths, Run, CommandStatus, argparse.Namespace], None]


@dataclasses.dataclass(frozen=True)
class Argument:
    """One `add_argument` call: the flags, then the keyword arguments exactly
    as the parser spells them."""

    flags: tuple[str, ...]
    options: Mapping[str, Any]


def arg(*flags: str, **options: Any) -> Argument:
    return Argument(flags, options)


@dataclasses.dataclass(frozen=True)
class Group:
    """A command that holds sub-commands (`assay channel declare`): its word,
    its help and the namespace field the chosen sub-command lands in."""

    name: str
    help: str
    dest: str


@dataclasses.dataclass(frozen=True)
class Lifecycle:
    """A command that runs before any run is loaded (start, stop, version,
    doctor) and returns the exit status."""

    name: str
    help: str
    run: LifecycleRun
    arguments: tuple[Argument, ...] = ()


@dataclasses.dataclass(frozen=True)
class Command:
    """A command over the loaded run: its identifier, its path on the command
    line (`channel declare`), its help, what it runs (the client of a daemon
    operation, or the function of an offline command), the daemon operation
    it is the client of, if any, its arguments, and the lazy epilog (the
    claims table, rendered only when help is)."""

    name: str
    path: str
    help: str
    run: CommandRun
    operation: Operation[Any, Any] | None = None
    arguments: tuple[Argument, ...] = ()
    epilog: Callable[[], str] | None = None


GROUPS: Mapping[str, Group] = {
    group.name: group
    for group in (
        Group(
            "channel", "declare and list registered channels (named readings)", "channel_command"
        ),
        Group(
            "model",
            "the general world-model tier: replay-fit is trust; fit models earn batching",
            "model_command",
        ),
        Group(
            "module", "behavior modules: list the active set, install one (owner)", "module_command"
        ),
        Group("goal", "the standing goal: propose revisions (agent), ratify (owner)", "goal_command"),
        Group("spend", "the external spend feed (the kernel cannot see the LLM bill)", "spend_command"),
    )
}


def _parser() -> Parser:
    """The command line, assembled from the two tables: one sub-parser per
    command in their order, with the flags and the help text each spells; a
    path of two words puts the command under its group."""
    parser = Parser(
        prog="assay", description="ASSAY referee harness: look, predict, act, compare"
    )
    parser.add_argument("--run-dir", default=".", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    for lifecycle in LIFECYCLE:
        _add_command(commands, lifecycle.name, lifecycle.help, lifecycle.arguments)
    holders: dict[str, Any] = {}
    for command in COMMANDS:
        words = command.path.split()
        if len(words) == 1:
            _add_command(commands, command.path, command.help, command.arguments, command.epilog)
            continue
        group = GROUPS[words[0]]
        if group.name not in holders:
            holder = commands.add_parser(group.name, help=group.help)
            holders[group.name] = holder.add_subparsers(dest=group.dest, required=True)
        _add_command(holders[group.name], words[1], command.help, command.arguments, command.epilog)
    return parser


def _add_command(
    commands: Any,
    name: str,
    text: str,
    arguments: tuple[Argument, ...],
    epilog: Callable[[], str] | None = None,
) -> None:
    options: dict[str, Any] = {"help": text}
    if epilog is not None:
        options["formatter_class"] = argparse.RawDescriptionHelpFormatter
    parser = commands.add_parser(name, **options)
    parser.lazy_epilog = epilog
    for argument in arguments:
        parser.add_argument(*argument.flags, **argument.options)


def command_of(args: argparse.Namespace) -> Command:
    """The command a parsed command line names: for a group, the
    sub-command's (`channel declare`)."""
    path = str(args.command)
    group = GROUPS.get(path)
    if group is not None:
        path = f"{group.name} {getattr(args, group.dest)}"
    for command in COMMANDS:
        if command.path == path:
            return command
    raise AssayError(f"unsupported command {args.command}")


def _parse_declares(raw: list[str]) -> dict[str, str]:
    declares: dict[str, str] = {}
    for item in raw or ():
        field, separator, value = str(item).partition("=")
        if not separator or not field.strip():
            raise AssayError(f'declarations are --declare "field=value", got {item!r}')
        declares[field.strip()] = value.strip()
    return declares


def _owner_token_file(paths: RunPaths, args: argparse.Namespace) -> Path | None:
    """Where the owner token goes instead of stdout, if anywhere: the flag,
    else ASSAY_OWNER_TOKEN_FILE, else nowhere (printed once, as before). The
    file must lie outside the run directory, which the agent reads freely."""
    raw = getattr(args, "owner_token_file", None) or os.getenv("ASSAY_OWNER_TOKEN_FILE")
    if not raw:
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = target.resolve()
    try:
        target.relative_to(paths.root.resolve())
    except ValueError:
        return target
    raise AssayError(
        f"--owner-token-file must point outside the run directory, got {target}"
    )


def _write_owner_token(target: Path, token: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(token + "\n")
    os.chmod(target, 0o600)


def _write_notes(paths: RunPaths, game_id: str) -> None:
    if paths.notes.exists():
        return
    paths.notes.write_text(
        f"# Notes: {game_id}\n"
        "\n"
        "## Verified (cite event ids)\n"
        "\n"
        "## Assumed / open questions\n"
        "\n"
        "## Plan\n"
    )


def _remote_idle_seconds(run: Run) -> float:
    mutations = run.mutations
    timestamp = mutations[-1].timestamp if mutations else run.config.get("created_at")
    if not timestamp:
        return 0.0
    try:
        then = dt.datetime.fromisoformat(str(timestamp))
        if then.tzinfo is None:
            then = then.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (dt.datetime.now(dt.timezone.utc) - then).total_seconds())
    except ValueError:
        return 0.0


def _start(paths: RunPaths, args: argparse.Namespace) -> None:
    """`assay start`: resume the run this directory owns, or start a fresh
    one. Every refusal happens before anything is spawned or written."""
    forced_sandbox = os.getenv(FORCE_VARIABLE)
    if forced_sandbox is not None and forced_sandbox != PROCESS_ISOLATION_ONLY:
        raise AssayError(
            f"{FORCE_VARIABLE} must be {PROCESS_ISOLATION_ONLY!r} (the forced fallback) "
            f"or unset, got {forced_sandbox!r}"
        )
    requested = normalize_game_id(args.game_id)
    registry_spec = load_registry_file(args.registry) if args.registry is not None else None
    existing = read_json(paths.config)
    if isinstance(existing, dict):
        _resume(paths, args, existing, requested, registry_spec)
    else:
        _fresh_start(paths, args, requested, registry_spec)


def _check_resume(
    paths: RunPaths,
    args: argparse.Namespace,
    existing: dict[str, Any],
    requested: str,
    registry_spec: dict[str, Any] | None,
) -> None:
    """The refusals of a resume, and the interpreter warning."""
    if getattr(args, "import_knowledge", None) is not None:
        raise AssayError(
            "knowledge imports happen at run start; this directory already "
            "owns a run"
        )
    if read_json(paths.registry, None) is None:
        raise AssayError(
            "this directory owns a run without a registry, from before 1.2.0: "
            "it can be inspected (status, view, audit) but not resumed"
        )
    if registry_spec is not None and registry_spec != read_json(paths.registry):
        raise AssayError(
            "this directory already owns a run with a different registry; "
            "the registry cannot change in place"
        )
    if existing.get("game_id") != requested:
        raise AssayError(
            f"this directory already owns {existing.get('game_id')}; use a fresh directory for {requested}"
        )
    existing_mode = str(existing.get("mode", LOCAL_MODE)).lower()
    if args.mode is not None and args.mode != existing_mode:
        raise AssayError(
            f"this directory already owns a {existing_mode} run; mode cannot be changed in place"
        )
    recorded_python = existing.get("python")
    if isinstance(recorded_python, str) and recorded_python != sys.executable:
        print(
            f"WARNING | interpreter changed: the run started with {recorded_python}, "
            f"this resume uses {sys.executable}; the daemon inherits this one, so "
            "set ASSAY_PYTHON to the original if the adapter's dependencies live there"
        )


def _resume(
    paths: RunPaths,
    args: argparse.Namespace,
    existing: dict[str, Any],
    requested: str,
    registry_spec: dict[str, Any] | None,
) -> None:
    _check_resume(paths, args, existing, requested, registry_spec)
    # A run pinned before the manifest existed has it rebuilt once, here
    # and never from a status call.
    reconstruct_manifest(paths)
    # Strict: a contiguity problem or a diverged chain refuses the resume
    # with CHAIN_DIVERGED and rewrites nothing.
    run = Run.load(paths, strict=True)
    # Orphan recovery (a spend the daemon journaled in mutations.jsonl
    # before anyone appended its event) runs here and only here, and only
    # once the daemon is confirmed dead or absent. While the daemon lives,
    # a mutation without an event is a step in flight: recovering it from
    # another process would double-count it the moment the daemon appends
    # its own graded event.
    recovered = 0
    owner = read_json(paths.broker, {})
    if owner.get("status") == "FINISHED":
        if find_daemon(paths) is None:
            recovered = reconcile_mutations(run)
        print(f"RESUMED | {requested} | completed run")
    elif is_remote_config(existing):
        _resume_remote(paths, run, requested)
    else:
        recovered = _resume_local(paths, run, requested)
    if recovered:
        print(f"JOURNAL | recovered {recovered} paid action(s) into timeline")
    print(status_text(run))


def _resume_remote(paths: RunPaths, run: Run, requested: str) -> None:
    idle = _remote_idle_seconds(run)
    if idle >= 15 * 60:
        raise AssayError(
            "REMOTE_LEASE_EXPIRED | no live action was recorded for at least 15 minutes. The remote competition run is not recoverable; preserve this directory and use a fresh one"
        )
    if not broker_ping(paths):
        if find_daemon(paths) is None:
            # Real spends against the remote world: journal them
            # before refusing, so the record is complete.
            reconcile_mutations(run)
        raise AssayError(
            "the remote competition owner is unavailable and cannot be reconstructed; preserve this directory and use a fresh one"
        )
    if not broker_matches_latest_event(run):
        raise AssayError(
            "REMOTE_STATE_DIVERGED | the live remote observation differs from the append-only timeline; stop using this run"
        )
    print(
        f"RESUMED | {requested} | REMOTE competition | action-idle lease about {max(0, 15 - int(idle // 60))}m"
    )


def _resume_local(paths: RunPaths, run: Run, requested: str) -> int:
    """Resume a local run: a daemon that answers is kept, a dead or absent
    one is replaced after the orphaned spends are recovered. Returns the
    recovered count."""
    recovered = 0
    restarted = False
    if not broker_ping(paths):
        daemon = find_daemon(paths)
        if daemon is not None and paths.socket.exists():
            # Alive, identified as ours, socket present, not answering:
            # it is inside a step (slow world) or hung. Killing it here
            # could leave a paid action applied and unjournaled.
            started_at = read_json(paths.broker, {}).get("started_at")
            when = (
                dt.datetime.fromtimestamp(float(started_at), dt.timezone.utc).isoformat(
                    timespec="seconds"
                )
                if isinstance(started_at, (int, float))
                else "unknown time"
            )
            raise AssayError(
                f"the environment owner is busy or hung (pid {daemon.pid}, "
                f"started {when}); wait and rerun `assay start`, or run "
                "`assay stop` (it exits after the current step)"
            )
        if daemon is not None:
            # Alive but unreachable (its socket is gone): stop it
            # cleanly before replaying a fresh one.
            result = stop_broker(paths)
            if not result["stopped"]:
                raise AssayError(
                    f"the environment owner (pid {daemon.pid}) is "
                    f"{result['reason']}; wait and rerun `assay start`"
                )
        # Confirmed dead or absent: safe to recover orphaned spends.
        recovered = reconcile_mutations(run)
        paths.socket.unlink(missing_ok=True)
        start_broker(paths)
        restarted = True
    if not broker_matches_latest_event(run):
        raise AssayError(
            "LOCAL_REPLAY_DIVERGED | reconstructed simulator state differs from the latest timeline event"
        )
    verb = "RECOVERED" if restarted else "RESUMED"
    print(
        f"{verb} | {requested} | local simulator | replayed {len(run.events) - 1} paid actions"
    )
    return recovered


def _fresh_start(
    paths: RunPaths,
    args: argparse.Namespace,
    requested: str,
    registry_spec: dict[str, Any] | None,
) -> None:
    if registry_spec is None:
        raise AssayError(
            "a fresh run needs --registry FILE: the registry names the actions, "
            "their parameter schemas and the action budget (examples/example_registry.json "
            "is the smallest one)"
        )
    orphan = find_daemon(paths)
    if orphan is not None:
        raise AssayError(
            f"a live environment owner (pid {orphan.pid}) still serves this "
            "directory but its run state is missing (was `.assay` removed by "
            "hand?); run `assay stop` here first, then start again"
        )
    token_file = _owner_token_file(paths, args)
    mode = str(args.mode or os.getenv("ASSAY_MODE", LOCAL_MODE)).lower()
    if mode not in {LOCAL_MODE, REMOTE_MODE}:
        raise AssayError(
            f"ASSAY_MODE must be {LOCAL_MODE!r} or {REMOTE_MODE!r}, got {mode!r}"
        )
    adapter_spec: str | None = None
    if args.adapter:
        # Validated before anything is spawned or written: the file must exist
        # (run directory first, then the working directory) and the module
        # must import in this interpreter, the one that will serve the daemon.
        adapter_spec = resolve_adapter_spec(str(args.adapter), paths.root)
        check_adapter_spec(adapter_spec, paths.root)
    paths.root.mkdir(parents=True, exist_ok=True)
    config = _fresh_config(paths, requested, args.seed, mode, adapter_spec, registry_spec)
    paths.state.mkdir(parents=True, exist_ok=True)
    atomic_json(paths.config, config)
    atomic_json(paths.registry, registry_spec)
    (paths.state / "python").write_text(sys.executable + "\n")
    owner_token = mint_owner_token(paths)
    try:
        run = _open_fresh_run(paths, args, requested, registry_spec, owner_token, token_file)
    except Exception:
        stop_broker(paths)
        shutil.rmtree(paths.state, ignore_errors=True)
        raise
    _print_started(paths, run, requested, mode, registry_spec, owner_token, token_file)


def _fresh_config(
    paths: RunPaths,
    requested: str,
    seed: int,
    mode: str,
    adapter_spec: str | None,
    registry_spec: dict[str, Any],
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "game_id": requested,
        "seed": seed,
        "mode": mode,
        "adapter": adapter_spec,
        "registry": True,
        "created_at": now_iso(),
        "harness": "assay",
        "harness_version": __version__,
        "journal_spec": JOURNAL_SPEC,
        "python": sys.executable,
        "sandbox": sandbox_mode(),
    }
    config["binding_hash"] = binding_hash_of(config)
    config["registry_hash"] = registry_hash_of(registry_spec)
    # Pinned at start so every later command, whatever its environment,
    # anchors to and audits the same file.
    config["anchor_file"] = str(environment_anchor_file(paths))
    return config


def _open_fresh_run(
    paths: RunPaths,
    args: argparse.Namespace,
    requested: str,
    registry_spec: dict[str, Any],
    owner_token: str | None,
    token_file: Path | None,
) -> Run:
    """The files of a fresh run, then the daemon; a refusal must leave no
    half-initialized run behind, which the caller sees to."""
    if owner_token is not None and token_file is not None:
        _write_owner_token(token_file, owner_token)
    _write_notes(paths, requested)
    # Module files are loaded once here to check their contract; a
    # refusal must leave no half-initialized run behind.
    pin_external_modules(paths, registry_spec)
    run = Run.load(paths, strict=True)
    if getattr(args, "import_knowledge", None) is not None:
        summary = import_knowledge(run, args.import_knowledge)
        print(
            f"IMPORTED | knowledge from source world {summary['source_game']} | "
            f"{summary['verifiers']} verifier candidate(s) | hazards active "
            f"{summary['hazards_active']} (foreign-inactive "
            f"{summary['hazards_foreign_inactive']}) | everything FOREIGN, "
            "demoted until re-earned (see status)"
        )
    # Event 0 is the daemon's: it takes the first observation, writes
    # public_info into config.json and appends START before it reports
    # READY. This process wrote config.json, the registry copy, the owner
    # file and the notes before the spawn and never writes config.json
    # after it; the status below is read from the journal the daemon
    # opened.
    start_broker(paths)
    return Run.load(paths, strict=False)


def _print_started(
    paths: RunPaths,
    run: Run,
    requested: str,
    mode: str,
    registry_spec: dict[str, Any],
    owner_token: str | None,
    token_file: Path | None,
) -> None:
    if owner_token is not None and token_file is not None:
        print(
            f"OWNER TOKEN | written to {token_file} (mode 0600) | only its hash is "
            "stored in the run; pass the file's content as --token for "
            "ratifications, approvals and waivers (the agent proposes, never "
            "self-ratifies)"
        )
    elif owner_token is not None:
        print(
            f"OWNER TOKEN | {owner_token} | printed once, only its hash is stored; "
            "the launcher/owner keeps it OUTSIDE the run directory (ratifications, "
            "approvals, waivers require it; the agent proposes, never self-ratifies)"
        )
    if mode == REMOTE_MODE:
        print(
            f"STARTED | {requested} | REMOTE competition | single run | ~15m action-idle lease | no replay recovery"
        )
    else:
        print(
            f"STARTED | {requested} | local simulator | competition accounting | replay recovery enabled"
        )
    if not anchor_status(paths, run.config)["writable"]:
        print(
            f"WARNING | {anchor_line(paths, run.config)} | set ASSAY_ANCHOR_DIR to a writable "
            "directory before the first anchor is due"
        )
    print(status_text(run))
    print(_use_line(registry_spec))


def _use_line(registry_spec: dict[str, Any]) -> str:
    if gate_mode(registry_spec) == "optional":
        return (
            "USE | gate: optional; `assay act` runs with or without --predict "
            "(an unpredicted act is journaled UNGATED; the audit marks the run "
            "invalid for scoring); parameters go as `assay act NAME pname=value ...`; "
            "schemas are in REGISTRY above, semantics are never given: learn them by acting"
        )
    if gate_mode(registry_spec) == "off":
        return (
            "USE | gate: off; `assay act NAME pname=value ...` with no --predict "
            "(predictions are not accepted on this run and nothing is graded; every "
            "paid action is journaled UNGATED and the audit marks the run invalid "
            "for scoring); schemas are in REGISTRY above, semantics are never given: "
            "learn them by acting"
        )
    return (
        'USE | every `assay act` needs --predict "<claims>"; parameters go as '
        "`assay act NAME pname=value ...`; schemas are in REGISTRY above, "
        "semantics are never given: learn them by acting"
    )


def _doctor(paths: RunPaths) -> int:
    """Everything a new user hits in the first hour, checked in one place and
    reported in one voice. Exit 2 when something FAILs, else 0."""
    lines: list[tuple[str, str]] = []

    def note(level: str, text: str) -> None:
        lines.append((level, text))

    version = ".".join(str(part) for part in sys.version_info[:3])
    note("ok", f"assay {__version__} | journal spec {JOURNAL_SPEC}")
    note("ok" if sys.version_info >= (3, 12) else "FAIL", f"python {version} at {sys.executable}")
    pinned = os.getenv("ASSAY_PYTHON")
    if pinned:
        note("ok", f"ASSAY_PYTHON={pinned}")
    for name, module, low, high, needed_by in (
        ("numpy", "numpy", 2, 3, "the kernel"),
        ("pillow", "PIL", 10, 13, "frame worlds only (pip install 'assay-harness[grid]')"),
    ):
        try:
            loaded = __import__(module)
            major = int(str(loaded.__version__).split(".")[0])
            note("ok" if low <= major < high else "FAIL", f"{name} {loaded.__version__}")
        except ImportError:
            note(
                "FAIL" if needed_by == "the kernel" else "WARN",
                f"{name} is not importable by {sys.executable}; needed by {needed_by}",
            )
    mode = sandbox_mode()
    note("WARN" if mode == PROCESS_ISOLATION_ONLY else "ok", f"sandbox | {sandbox_text(mode)}")
    importable, reason = check_imports(("numpy", "json"))
    note(
        "ok" if importable else "WARN",
        "numpy and json import inside the sandbox"
        if importable
        else f"numpy and json do not import inside the sandbox: {reason}",
    )
    socket_path = str(paths.socket)
    note(
        "ok" if len(socket_path.encode()) <= 100 else "FAIL",
        f"socket path {socket_path} ({len(socket_path.encode())} bytes, limit about 104)",
    )
    config = read_json(paths.config, None) if paths.config.exists() else None
    if not isinstance(config, dict):
        note("ok", f"no run in {paths.root} (`assay start` creates one)")
        status = anchor_status(paths, None)
        note(
            "ok" if status["writable"] else "WARN",
            f"anchor directory {status['file'].parent} "
            f"{'writable' if status['writable'] else 'NOT WRITABLE (set ASSAY_ANCHOR_DIR)'}",
        )
    else:
        events = load_jsonl(paths.events)
        last = events[-1] if events else None
        note(
            "ok",
            f"run {config.get('game_id')} | mode {config.get('mode')} | {len(events)} events"
            + (f" | last e{last['id']} {last.get('state')}" if last else ""),
        )
        recorded_python = config.get("python")
        if isinstance(recorded_python, str) and recorded_python != sys.executable:
            note("WARN", f"run started with {recorded_python}, this shell uses {sys.executable}")
        if config.get("registry"):
            status = anchor_status(paths, config)
            note(
                "ok" if status["writable"] and not status["failed"] else "WARN",
                anchor_line(paths, config)[len("ANCHORS | "):],
            )
        daemon = find_daemon(paths)
        descriptor = read_json(paths.broker, {})
        descriptor_status = descriptor.get("status") if isinstance(descriptor, dict) else None
        if daemon is not None:
            answering = broker_ping(paths)
            note(
                "ok" if answering else "WARN",
                f"daemon pid {daemon.pid} alive, identified, "
                f"{'answering' if answering else 'not answering (busy or hung)'}",
            )
        elif descriptor_status in {"FINISHED", "STOPPED"}:
            note("ok", f"daemon not running (broker.json says {descriptor_status})")
        else:
            note("WARN", f"daemon not running (broker.json says {descriptor_status}); `assay start` resumes")
        # The mode recorded at the run's creation against the daemon's own
        # (broker.json, written before READY) and this shell's, as for the
        # interpreter: a difference is a run graded under another jail.
        recorded_sandbox = config.get("sandbox")
        live_sandbox = descriptor.get("sandbox") if isinstance(descriptor, dict) else None
        if isinstance(recorded_sandbox, str):
            if daemon is not None and isinstance(live_sandbox, str) and live_sandbox != recorded_sandbox:
                note(
                    "WARN",
                    f"sandbox recorded at the run's creation is {recorded_sandbox}, "
                    f"the daemon runs with {live_sandbox}",
                )
            if recorded_sandbox != mode:
                note(
                    "WARN",
                    f"sandbox recorded at the run's creation is {recorded_sandbox}, "
                    f"this shell decides {mode}",
                )
        adapter = config.get("adapter")
        if adapter:
            try:
                resolved = resolve_adapter_spec(str(adapter), paths.root)
                check_adapter_spec(resolved, paths.root)
                note("ok", f"adapter {resolved} imports")
            except AssayError as error:
                note("FAIL", f"adapter: {error}")
        else:
            note("WARN", "no adapter recorded")
        if paths.registry.exists():
            try:
                spec = validate_registry(read_json(paths.registry))
                note("ok", f"registry valid, {len(spec['actions'])} actions")
            except AssayError as error:
                note("FAIL", f"registry: {error}")
    for level, text in lines:
        print(f"DOCTOR | {level} | {text}")
    failed = sum(1 for level, _ in lines if level == "FAIL")
    warned = sum(1 for level, _ in lines if level == "WARN")
    print(f"DOCTOR | {'FAIL' if failed else 'ok'} | {failed} failure(s), {warned} warning(s)")
    return 2 if failed else 0


def _discard_empty_state(paths: RunPaths) -> None:
    """A refused fresh start leaves `.assay/` holding nothing but the lock
    file run_lock created; remove it so the directory is as it was."""
    try:
        leftovers = [item.name for item in paths.state.iterdir()]
    except FileNotFoundError:
        return
    if leftovers == [paths.lock.name]:
        shutil.rmtree(paths.state, ignore_errors=True)


def _stop(paths: RunPaths) -> None:
    """Stop the daemon cleanly. Works without run state (an orphaned daemon
    after a hand-deleted `.assay`) because identity comes from the process
    table, not from broker.json."""
    result = stop_broker(paths)
    if result["stopped"]:
        print(
            f"STOPPED | environment owner pid {result['pid']} exited after "
            f"{result.get('elapsed', 0.0):.2f}s | `assay start` resumes the run"
        )
        return
    if result["pid"] is None:
        print(f"STOP | {result['reason']} for {paths.root}")
        return
    raise AssayError(
        f"the environment owner (pid {result['pid']}) is {result['reason']}; "
        "rerun `assay stop` after it, or wait"
    )


def start_command(paths: RunPaths, args: argparse.Namespace) -> int:
    try:
        with run_lock(paths):
            _start(paths, args)
    except AssayError:
        _discard_empty_state(paths)
        raise
    return 0


def stop_command(paths: RunPaths, args: argparse.Namespace) -> int:
    if paths.state.is_dir():
        with run_lock(paths):
            _stop(paths)
    else:
        # No run state here (an orphaned daemon after a hand-deleted
        # `.assay`): stop without creating state as a side effect.
        _stop(paths)
    return 0


def version_command(paths: RunPaths, args: argparse.Namespace) -> int:
    print(
        f"assay {__version__} | journal spec {JOURNAL_SPEC} | python "
        f"{'.'.join(str(part) for part in sys.version_info[:3])} at {sys.executable}"
    )
    return 0


def doctor_command(paths: RunPaths, args: argparse.Namespace) -> int:
    return _doctor(paths)


# --- the commands over the loaded run ------------------------------------------


def status_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    print(status_text(run, history=args.history))


def view_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    events = run.events
    flags = {"grid": args.grid, "frames": args.frames, "crop": args.crop}
    print(view_text(run, event_id=args.event, history=args.history, flags=flags))
    export = args.export
    if export:
        destination = export if export.is_absolute() else paths.root / export
        print(
            f"EXPORTED | {require_kind(events[-1] if events else None, 'export').export_history(run, destination)}"
        )


def _paid(
    paths: RunPaths,
    status: CommandStatus,
    operation: Operation[Req, ReceiptResult],
    request: Req,
    *,
    steps: int = 1,
) -> None:
    """A paid command through the daemon, the gate enforced where the
    session lives; the run is reloaded for the receipt, since the daemon
    appended what the client does not hold."""
    receipt = broker_gated(paths, operation, request, steps=steps)
    status.run = Run.load(paths, strict=False)
    print(result_text(status.run, receipt))


def act_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    # `assay act NAME pname=value ...`: the extra tokens are typed
    # parameters; case is preserved (values may be case-sensitive).
    request = ActRequest(
        action_token=" ".join([args.action, *args.params]),
        predict=args.predict,
        because=args.because,
        at_event=args.at_event,
        declares=_parse_declares(args.declare),
    )
    _paid(paths, status, ACT, request)


def commit_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    if bool(args.plan) == bool(args.step):
        raise AssayError(
            "commit takes either @plan.json (from `assay model solve`) "
            'or one or more --step "NAME pname=value :: claims"'
        )
    request = CommitRequest(
        plan=args.plan,
        steps=tuple(args.step),
        at_event=args.at_event,
        declares=_parse_declares(args.declare),
    )
    _paid(paths, status, COMMIT, request, steps=max(1, len(args.step)))


def reset_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    request = ResetRequest(
        because=args.because,
        at_event=args.at_event,
        declares=_parse_declares(args.declare),
    )
    _paid(paths, status, RESET, request)


def channel_declare(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    spec = declare_channel(run, args.name, path=args.path, file=args.file)
    print(
        f"CHANNEL | declared {args.name} ({spec['form']}); claims "
        f'like `ch {args.name} = V` now parse and grade'
    )


def channel_list(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    declared = load_declared(paths)
    events = run.events
    if events:
        print("\n".join(channel_lines(run, events[-1], fresh=args.read)))
    else:
        print("CHANNELS | " + " · ".join(known_channels(run)))
    for name, spec in sorted(declared.items()):
        detail = spec.get("path") or spec.get("hash", "")[:12]
        print(f"  {name}: {spec['form']} {detail}")


def model_init(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    print(f"CREATED | {init_model(paths)}; declare CHANNELS, define next()")


def model_replay(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    record = replay_model(run)
    print("\n".join(fit_lines(record)))


def model_solve(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    result = solve_model(
        run,
        args.to,
        seconds=args.seconds,
        max_nodes=args.max_nodes,
        max_depth=args.max_depth,
    )
    if result["actions"]:
        print(
            f"SOLVE | plan found | {len(result['actions'])} steps | "
            f"nodes {result['nodes']}"
        )
        print("ACTIONS | " + " -> ".join(result["actions"]))
        print(
            "PLAN | .assay/model_plan.json; execute with "
            "`assay commit @.assay/model_plan.json` (needs replay-fit "
            "promotion on the current journal)"
        )
    else:
        print(
            f"SOLVE | no plan inside the model | nodes {result['nodes']}; "
            "actions() or next() are too narrow, or the goal needs "
            "something unmodeled"
        )


def module_list(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    origins = {
        str(entry.get("name")): str(entry.get("origin")) for entry in run.manifest
    }
    view = ModuleView(run)
    print("MODULES | active (name, mode, origin), constitution, telemetry")
    for item, mode in active_modules(run):
        print(f"  {item.NAME} | {mode} | {origins.get(item.NAME, 'built-in')}")
        print(f"    constitution: {item.CONSTITUTION}")
        try:
            telemetry = item.telemetry(view)
        except Exception as error:  # noqa: BLE001 - a module's counters never break the listing
            telemetry = {"error": f"{type(error).__name__}: {error}"}
        print(f"    telemetry: {json.dumps(telemetry, sort_keys=True, default=str)}")
    for line in unlisted_lines(run):
        print(line)


def module_install(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    # The owner's install runs in the daemon, against the hash and
    # the manifest it holds; it refuses without a live daemon.
    record = broker_install_module(paths, args.path, args.token)
    print(
        f"MODULE | installed {record['name']} from {record['source']} "
        f"(sha256 {record['sha256'][:12]}) | journaled | active from the "
        "next action"
    )


def goal_propose(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    record = propose_goal(run, args.text, args.because)
    print(
        f"GOAL | proposal #{record['id']} journaled, awaiting owner "
        "ratification (`assay goal ratify ID --token ...`)"
    )


def goal_list(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    proposals = list_proposals(run)
    if not proposals:
        print("GOAL | no proposals")
    for entry in proposals:
        print(
            f"  #{entry['id']} [{entry['status']}] {entry['text']}"
            + (f"; {entry['because']}" if entry.get("because") else "")
        )


def goal_ratify(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    proposal = ratify_goal(run, args.id, args.token)
    print(
        f"GOAL | ratified #{args.id}: {proposal['text']}; status now "
        "re-presents it as the standing goal"
    )


def export_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    target = export_knowledge(run, args.out)
    print(f"EXPORTED | {target}; import with `assay start WORLD_ID --import {target.name}`")


def spend_report(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    append_jsonl(
        paths.activity,
        {
            "kind": "spend_report",
            "id": args.report_id,
            "usd": args.usd,
            "tokens": args.tokens,
        },
    )
    usd, tokens = spend_reports(load_jsonl(paths.activity))
    print(f"SPEND | recorded | cumulative ${usd:.2f} | {tokens} tokens")


def audit_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    print("\n".join(audit_lines(audit(run))))


def approve_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    grant_approval(run, args.action, args.token)
    print(
        f"APPROVED | one use of {args.action.upper()} granted "
        "(expires in 10 minutes, consumed on use)"
    )


def waive_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    grant_waiver(run, args.action, args.token, args.because or "")
    print(f"WAIVED | rehearsal quota for {args.action.upper()} (journaled)")


def python_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    if bool(args.source) == bool(args.file):
        raise AssayError(
            "provide exactly one Python source argument or --file"
        )
    source = (
        args.source if args.source is not None else args.file.read_text()
    )
    run_python(run, source)


# --- the entry point -----------------------------------------------------------


def main() -> None:
    try:
        args = _parser().parse_args()
        paths = RunPaths(Path(args.run_dir).resolve())
        os.environ["ASSAY_RUN_DIR"] = str(paths.root)
        raise SystemExit(_run(paths, args))
    except AssayError as error:
        print(f"ERROR | {error}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as error:  # noqa: BLE001 - one error voice, traceback saved
        raise SystemExit(_report_internal_error(error))


def _run(paths: RunPaths, args: argparse.Namespace) -> int:
    """One command, from the tables: a lifecycle command runs before any run
    is loaded and returns the exit status; every other command loads the run
    once, lenient (the readers report what they find; only start and the
    daemon load strict), and runs under the lock with its activity record."""
    for lifecycle in LIFECYCLE:
        if args.command == lifecycle.name:
            return lifecycle.run(paths, args)
    command = command_of(args)
    require_run(paths)
    with run_lock(paths):
        run = Run.load(paths, strict=False)
        with command_status(run, args.command) as status:
            command.run(paths, run, status, args)
    return 0


def _report_internal_error(error: BaseException) -> int:
    """Anything that is not an AssayError is a bug or a corrupt file. Print one
    line in the usual voice and save the traceback where the user can find it,
    instead of a bare Python traceback with no pointer."""
    saved = "no run directory here, so the traceback was not saved"
    try:
        run_dir = Path(os.environ.get("ASSAY_RUN_DIR") or ".").resolve()
        state = run_dir / ".assay"
        if state.is_dir():
            target = state / "last_error.txt"
            target.write_text(traceback.format_exc())
            saved = f"traceback in {target}"
    except OSError:
        pass
    print(
        f"ERROR | internal: {type(error).__name__}: {str(error)[:300]} ({saved})",
        file=sys.stderr,
    )
    return 2


# --- the tables, in the order the command line lists them --------------------

LIFECYCLE: tuple[Lifecycle, ...] = (
    Lifecycle(
        "start",
        "start or resume the one persistent run",
        start_command,
        arguments=(
            arg(
                "game_id",
                metavar="world_id",
                help="a label for this run, kept as given: up to 64 characters with no whitespace, "
                "control characters or path separators. A benchmark adapter may read it to pick the instance",
            ),
            arg("--seed", type=int, default=0, help=argparse.SUPPRESS),
            arg(
                "--adapter",
                help="world adapter factory: module:factory or /path/file.py:factory",
            ),
            arg(
                "--registry",
                type=Path,
                help="JSON file registering the actions, their parameter schemas and the action "
                "budget; required for a fresh run, optional on resume (the pinned one is used)",
            ),
            arg(
                "--mode",
                choices=(LOCAL_MODE, REMOTE_MODE),
                help="local simulator (default), or expiring remote competition validation",
            ),
            arg(
                "--import",
                dest="import_knowledge",
                type=Path,
                metavar="KNOWLEDGE.json",
                help="import a prior run's exported knowledge (lands FOREIGN, demoted)",
            ),
            arg(
                "--owner-token-file",
                type=Path,
                metavar="PATH",
                help="write the owner token to this file (mode 0600, outside the run "
                "directory) instead of printing it; ASSAY_OWNER_TOKEN_FILE does the same",
            ),
        ),
    ),
    Lifecycle(
        "stop",
        "stop this run's environment owner (the daemon) cleanly; "
        "`assay start` resumes the run later",
        stop_command,
    ),
    Lifecycle(
        "version",
        "the harness version, the journal spec it writes, the interpreter",
        version_command,
    ),
    Lifecycle(
        "doctor",
        "check the interpreter, dependencies, anchors, socket path, run "
        "state, daemon, adapter and registry; works with or without a run here",
        doctor_command,
    ),
)

COMMANDS: tuple[Command, ...] = (
    Command(
        "status",
        "status",
        "full picture: progress, image, actions, recent results, notes",
        status_command,
        arguments=(arg("--history", type=int, default=8),),
    ),
    Command(
        "view",
        "view",
        "inspect one event: the observation, the delta since the previous one, history",
        view_command,
        arguments=(
            arg("--event", type=int),
            arg("--history", type=int, default=0),
            # Frame worlds only; inert on a dict run, which says so.
            arg(
                "--grid", action="store_true", help="frame worlds: print the complete exact 0-f grid"
            ),
            arg(
                "--frames", action="store_true", help="frame worlds: show causal animation frames"
            ),
            arg("--crop", metavar="R0:R1,C0:C1", help="frame worlds: print an exact half-open crop"),
            arg(
                "--export", type=Path, metavar="FILE.npz", help="frame worlds: export the grid history"
            ),
        ),
    ),
    Command(
        "act",
        "act",
        "take one action with a prediction; the result is graded against it",
        act_command,
        operation=ACT,
        arguments=(
            arg("action"),
            arg("params", nargs="*", metavar="pname=value", help=argparse.SUPPRESS),
            arg(
                "--predict",
                required=False,
                help='what this action does, e.g. "change; ch counter delta = 1" (see below); required '
                "unless the registry sets gate: optional",
            ),
            arg("--because", help="short reason for choosing this action"),
            arg("--at", type=int, dest="at_event"),
            arg(
                "--declare",
                action="append",
                default=[],
                metavar='"field=value"',
                help="structural declaration a gate or module demanded "
                '(e.g. --declare "worst_case=..." --declare "recovery=...")',
            ),
        ),
        epilog=claims_help,
    ),
    Command(
        "commit",
        "commit",
        "run a prediction-checked batch or a model plan; halts on the first miss",
        commit_command,
        operation=COMMIT,
        arguments=(
            arg(
                "plan",
                nargs="?",
                help="a plan file, e.g. @.assay/model_plan.json from `assay model solve`",
            ),
            arg(
                "--step",
                action="append",
                default=[],
                metavar='"ACTION :: CLAIMS"',
                help="one action with its own prediction; repeat in execution order",
            ),
            arg("--at", type=int, dest="at_event"),
            arg(
                "--declare",
                action="append",
                default=[],
                metavar='"field=value"',
                help="structural declaration a module demanded for a step in this batch",
            ),
        ),
        epilog=claims_help,
    ),
    Command(
        "reset",
        "reset",
        "pay one action to rewind the current progress unit",
        reset_command,
        operation=RESET,
        arguments=(
            arg(
                "--because", help="why the current state is worth abandoning (required unless GAME_OVER)"
            ),
            arg("--at", type=int, dest="at_event"),
            arg(
                "--declare",
                action="append",
                default=[],
                metavar='"field=value"',
                help="structural declaration a module demanded for this reset "
                '(e.g. --declare "impossible=..." --declare "coverage_audit=...")',
            ),
        ),
    ),
    Command(
        "python",
        "python",
        "run offline Python with the history, deltas, BFS and A* preloaded",
        python_command,
        arguments=(arg("source", nargs="?"), arg("--file", type=Path)),
    ),
    Command(
        "channel_declare",
        "channel declare",
        "register a named reading of the observation",
        channel_declare,
        arguments=(
            arg("name"),
            arg("--path", help="dotted keys into the dict observation, e.g. counters.red"),
            arg("--file", help="extractor file: def extract(obs) -> value (sandboxed)"),
        ),
    ),
    Command(
        "channel_list",
        "channel list",
        "list registered channels with their current readings",
        channel_list,
        arguments=(
            arg(
                "--read",
                action="store_true",
                help="compute extractor channels fresh (runs each extractor sandboxed) "
                "instead of showing the last graded reading",
            ),
        ),
    ),
    Command("model_init", "model init", "create a model.py template", model_init),
    Command(
        "model_replay",
        "model replay",
        "grade model.py's declared channels over every recorded transition",
        model_replay,
    ),
    Command(
        "model_solve",
        "model solve",
        "search the model for a plan to a channel target",
        model_solve,
        arguments=(
            arg("--to", required=True, metavar='"ch NAME = V"', help="the goal reading"),
            arg("--seconds", type=float, default=15.0),
            arg("--max-nodes", type=int, default=100_000),
            arg("--max-depth", type=int, default=40),
        ),
    ),
    Command(
        "module_list",
        "module list",
        "active modules with mode and origin, plus ignored files",
        module_list,
    ),
    Command(
        "module_install",
        "module install",
        "owner: install a module file mid-run (journaled, manifest-pinned)",
        module_install,
        operation=INSTALL_MODULE,
        arguments=(arg("path", type=Path), arg("--token")),
    ),
    Command(
        "goal_propose",
        "goal propose",
        "propose a standing-goal revision (journaled, owner ratifies)",
        goal_propose,
        arguments=(arg("text"), arg("--because")),
    ),
    Command("goal_list", "goal list", "list goal proposals and their status", goal_list),
    Command(
        "goal_ratify",
        "goal ratify",
        "owner: ratify a proposal by id (requires the owner token)",
        goal_ratify,
        arguments=(arg("id", type=int), arg("--token")),
    ),
    Command(
        "export",
        "export",
        "export this run's earned knowledge for a future import",
        export_command,
        arguments=(arg("--out", type=Path),),
    ),
    Command(
        "spend_report",
        "spend report",
        "post cumulative usage (idempotent by --id; last entry wins)",
        spend_report,
        arguments=(
            arg("--usd", type=float, required=True),
            arg("--tokens", type=int, default=0),
            arg("--id", dest="report_id", required=True),
        ),
    ),
    Command(
        "audit", "audit", "recompute journal integrity: chain, anchors, ungated events", audit_command
    ),
    Command(
        "approve",
        "approve",
        "owner: grant one use of an approval-gated action",
        approve_command,
        arguments=(arg("action"), arg("--token")),
    ),
    Command(
        "waive",
        "waive",
        "owner: waive a live actuator's rehearsal quota (journaled)",
        waive_command,
        arguments=(arg("action"), arg("--token"), arg("--because")),
    ),
)
