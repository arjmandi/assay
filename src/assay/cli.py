from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import io
import json
import math
import os
import shutil
import sys
import time
import traceback
from collections.abc import Callable, Iterator, Mapping
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
    REPLAY_HINT,
    broker_gated,
    broker_install_module,
    broker_matches_latest_event,
    broker_ping,
    check_adapter_spec,
    find_daemon,
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
from .channels import channel_list_of, channel_list_text, declare_channel
from .integrity import anchor_line, anchor_status, audit, audit_lines, environment_anchor_file
from .model import (
    fit_lines,
    init_model,
    replay_model,
    solve_model,
)
from .modules import (
    module_list_of,
    module_list_text,
    pin_external_modules,
    reconstruct_manifest,
)
from .sandbox import (
    FORCE_VARIABLE,
    PROCESS_ISOLATION_ONLY,
    check_imports,
    sandbox_mode,
    sandbox_text,
)
from .adapters import SessionCapability, recorded_capability
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
    run_mode,
)
from .errors import exit_code
from .extras import require_kind
from .inspect import result_text, status_text, view_lines_text, view_of
from .live import split_step
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
    Step,
)
from .predictions import claims_help
from .registry import (
    gate_mode,
    load_registry_file,
    module_modes,
    parse_registry_action,
    require_registry,
    spend_reports,
    validate_registry,
)
from .run import Run
from .status import (
    idle_seconds,
    lease_left,
    lease_text,
    remaining_text,
    render_status,
    status_of,
)


USAGE_HINT = "`assay --help` lists the commands and `assay COMMAND --help` a command's flags"


class Parser(argparse.ArgumentParser):
    """argparse with the kernel's error voice (`CLI_USAGE`), and an epilog
    that is built only when help is rendered, so listing the claim forms of an
    installed observation kind never imports that kind on an ordinary
    command."""

    lazy_epilog: Any = None

    def error(self, message: str) -> NoReturn:
        raise AssayError(message, code="CLI_USAGE", hint=USAGE_HINT)

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


# Every command takes it (docs/ARCHITECTURE.md section 7.3): exactly one JSON
# document on stdout, the result record where the command has one, the lines
# as `{"lines": [...]}` where it has none, the error object on failure.
JSON_FLAG = arg(
    "--json",
    action="store_true",
    help="print the result record as one JSON document instead of the lines",
)


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
    for argument in (*arguments, JSON_FLAG):
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
    raise AssayError(f"unsupported command {args.command}", code="CLI_USAGE", hint=USAGE_HINT)


def _parse_declares(raw: list[str]) -> dict[str, str]:
    declares: dict[str, str] = {}
    for item in raw or ():
        field, separator, value = str(item).partition("=")
        if not separator or not field.strip():
            raise AssayError(f'declarations are --declare "field=value", got {item!r}', code="COMMAND_ARGS")
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
        f"--owner-token-file must point outside the run directory, got {target}",
        code="PATH_INVALID",
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


def _mode_word(mode: str) -> str:
    """The mode as the STARTED and RESUMED lines say it."""
    return "REMOTE" if mode == REMOTE_MODE else "local simulator"


def _lease_duration(seconds: int) -> str:
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds} second" if seconds == 1 else f"{seconds} seconds"


def _start(paths: RunPaths, args: argparse.Namespace) -> None:
    """`assay start`: resume the run this directory owns, or start a fresh
    one. Every refusal happens before anything is spawned or written."""
    forced_sandbox = os.getenv(FORCE_VARIABLE)
    if forced_sandbox is not None and forced_sandbox != PROCESS_ISOLATION_ONLY:
        raise AssayError(
            f"{FORCE_VARIABLE} must be {PROCESS_ISOLATION_ONLY!r} (the forced fallback) "
            f"or unset, got {forced_sandbox!r}",
            code="COMMAND_ARGS",
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
            "this directory already owns a run, and knowledge imports happen at run start",
            code="RESUME_REFUSED",
            hint="import into a fresh directory with `assay start WORLD_ID --import FILE ...`",
        )
    if read_json(paths.registry, None) is None:
        raise AssayError(
            "this directory owns a run without a registry, from before 1.2.0",
            code="REGISTRY_MISSING",
            hint="it can be inspected (status, view, audit) but not resumed; start a new run in a fresh directory",
        )
    pinned = read_json(paths.registry)
    if isinstance(pinned, dict) and "module_modes" in pinned:
        # A copy pinned under a module's old name compares under its current one.
        pinned = {**pinned, "module_modes": module_modes(pinned)}
    if registry_spec is not None and registry_spec != pinned:
        raise AssayError(
            "this directory already owns a run with a different registry, which cannot change in place",
            code="RESUME_REFUSED",
            hint="resume without --registry (the pinned one is used), or start a new run in a fresh directory",
        )
    if existing.get("game_id") != requested:
        raise AssayError(
            f"this directory already owns {existing.get('game_id')}",
            code="RESUME_REFUSED",
            hint=f"use a fresh directory for {requested}",
        )
    existing_mode = run_mode(existing)
    if args.mode is not None and args.mode != existing_mode:
        raise AssayError(
            f"this directory already owns a {existing_mode} run, and the mode cannot change in place",
            code="RESUME_REFUSED",
            hint=f"resume without --mode, or use a fresh directory for a {args.mode} run",
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
    # The session rules the world declared at the run's start (docs/ARCHITECTURE.md
    # section 2.2): a world without replay is resumed only through its live daemon.
    capability = recorded_capability(existing)
    if owner.get("status") == "FINISHED":
        if find_daemon(paths) is None:
            recovered = reconcile_mutations(run)
        print(f"RESUMED | {requested} | completed run")
    elif not capability.replayable:
        _resume_remote(paths, run, requested, capability)
    else:
        recovered = _resume_local(paths, run, requested)
    if recovered:
        print(f"JOURNAL | recovered {recovered} paid action(s) into timeline")
    print(status_text(run))


REMOTE_HINT = "preserve this directory and use a fresh one for another run"


def _resume_remote(
    paths: RunPaths, run: Run, requested: str, capability: SessionCapability
) -> None:
    """Resume a run whose world declared no replay: the session lives one
    daemon long, so the daemon must still be alive and answering, within the
    action-idle lease the world declared when it declared one, and its live
    observation must match the last event; nothing is ever reconstructed."""
    idle = idle_seconds(run)
    lease = capability.idle_lease_seconds
    if lease is not None and idle is not None and idle >= lease:
        raise AssayError(
            f"no live action was recorded for at least {_lease_duration(lease)}; the "
            "world's action-idle lease has run out and the run is not recoverable",
            code="REMOTE_LEASE_EXPIRED",
            hint=REMOTE_HINT,
        )
    if not broker_ping(paths):
        if find_daemon(paths) is None:
            # Real spends against the world: journal them before refusing,
            # so the record is complete.
            reconcile_mutations(run)
        raise AssayError(
            "the run's environment owner is gone and the world declared no replay, "
            "so the run cannot be reconstructed",
            code="REMOTE_SESSION_UNAVAILABLE",
            hint=REMOTE_HINT,
        )
    if not broker_matches_latest_event(run):
        raise AssayError(
            "the live observation differs from the append-only timeline",
            code="REMOTE_STATE_DIVERGED",
            hint="stop using this run; " + REMOTE_HINT,
        )
    print(
        f"RESUMED | {requested} | {_mode_word(run_mode(run.config))} | "
        f"{remaining_text(lease, lease_left(capability, idle))}"
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
                f"the environment owner is busy or hung (pid {daemon.pid}, started {when})",
                code="DAEMON_BUSY",
                hint="wait and rerun `assay start`, or run `assay stop` (it exits after the current step)",
            )
        if daemon is not None:
            # Alive but unreachable (its socket is gone): stop it
            # cleanly before replaying a fresh one.
            result = stop_broker(paths)
            if not result["stopped"]:
                raise AssayError(
                    f"the environment owner (pid {daemon.pid}) is {result['reason']}",
                    code="DAEMON_BUSY",
                    hint="wait and rerun `assay start`",
                )
        # Confirmed dead or absent: safe to recover orphaned spends.
        recovered = reconcile_mutations(run)
        paths.socket.unlink(missing_ok=True)
        # The adapter dry-imported in this interpreter before the spawn, as at
        # a fresh start, so a missing dependency is the same ADAPTER_SPEC
        # refusal here and not a daemon that never comes up.
        adapter = run.config.get("adapter")
        if adapter:
            check_adapter_spec(str(adapter), paths.root)
        start_broker(paths)
        restarted = True
    if not broker_matches_latest_event(run):
        if restarted:
            # The daemon this resume started holds a world the journal does
            # not describe; left READY it would spend on it under the old
            # record.
            stop_broker(paths)
        raise AssayError(
            "reconstructed simulator state differs from the latest timeline event",
            code="LOCAL_REPLAY_DIVERGED",
            hint=REPLAY_HINT,
        )
    verb = "RECOVERED" if restarted else "RESUMED"
    print(
        f"{verb} | {requested} | {_mode_word(run_mode(run.config))} | "
        f"replayed {len(run.events) - 1} paid actions"
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
            "their parameter schemas and the action budget",
            code="COMMAND_ARGS",
            hint="pass --registry FILE; examples/example_registry.json is the smallest one",
        )
    orphan = find_daemon(paths)
    if orphan is not None:
        raise AssayError(
            f"a live environment owner (pid {orphan.pid}) still serves this "
            "directory but its run state is missing (was `.assay` removed by hand?)",
            code="DAEMON_ORPHANED",
            hint="run `assay stop` here first, then start again",
        )
    token_file = _owner_token_file(paths, args)
    mode = str(args.mode or os.getenv("ASSAY_MODE", LOCAL_MODE)).lower()
    if mode not in {LOCAL_MODE, REMOTE_MODE}:
        raise AssayError(
            f"ASSAY_MODE must be {LOCAL_MODE!r} or {REMOTE_MODE!r}, got {mode!r}",
            code="COMMAND_ARGS",
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
    # The session rules the world declared, recorded by the daemon before
    # READY (docs/ARCHITECTURE.md section 2.2); the mode is the operator's
    # word to the adapter and decides nothing here.
    capability = recorded_capability(run.config)
    lease = lease_text(capability.idle_lease_seconds)
    if capability.replayable:
        print(f"STARTED | {requested} | {_mode_word(mode)} | {lease} | replay recovery enabled")
    else:
        print(
            f"STARTED | {requested} | {_mode_word(mode)} | single run | {lease} | no replay recovery"
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
                "ok" if status["writable"] and status["failed_error"] is None else "WARN",
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
        f"the environment owner (pid {result['pid']}) is {result['reason']}",
        code="DAEMON_BUSY",
        hint="rerun `assay stop` after it, or wait",
    )


@contextlib.contextmanager
def _lifecycle_status(paths: RunPaths, command: str) -> Iterator[None]:
    """The activity record of start and stop, the commands that run before
    any run is loaded: `command_start` and `command_end` like the other
    commands', with the code and the kind of a refusal, written when the
    run state existed before the command, so a refused resume (a diverged
    chain or replay, a remote lease) leaves its record. A fresh start, which
    creates the state, writes none: a refused one leaves nothing behind."""
    existed = paths.state.is_dir()
    started = time.monotonic()
    record: dict[str, Any] = {
        "command": command,
        "risk": "offline",
        "started_at": now_iso(),
        "pid": os.getpid(),
    }
    if existed:
        append_jsonl(paths.activity, {"kind": "command_start", "status": "RUNNING", **record})
    try:
        yield
    except Exception as error:
        if existed:
            failure = AssayError.wrap(error)
            record.update(
                status="ERROR",
                finished_at=now_iso(),
                elapsed_seconds=time.monotonic() - started,
                error=failure.message[:500],
                code=failure.code,
                error_kind=failure.kind,
            )
            append_jsonl(paths.activity, {"kind": "command_end", **record})
        raise
    else:
        if existed:
            record.update(
                status="FINISHED",
                finished_at=now_iso(),
                elapsed_seconds=time.monotonic() - started,
            )
            append_jsonl(paths.activity, {"kind": "command_end", **record})


def start_command(paths: RunPaths, args: argparse.Namespace) -> int:
    with _lifecycle_status(paths, "start"):
        try:
            with run_lock(paths):
                _start(paths, args)
        except AssayError:
            _discard_empty_state(paths)
            raise
    return 0


def stop_command(paths: RunPaths, args: argparse.Namespace) -> int:
    with _lifecycle_status(paths, "stop"):
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
    record = status_of(run, history=args.history)
    _emit(args, record, render_status(record))


def view_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    events = run.events
    flags = {"grid": args.grid, "frames": args.frames, "crop": args.crop}
    view = view_of(run, event_id=args.event, history=args.history, flags=flags)
    export = args.export
    if export:
        destination = export if export.is_absolute() else paths.root / export
        exported = require_kind(events[-1] if events else None, "export").export_history(run, destination)
        view = dataclasses.replace(view, exported=str(exported))
    _emit(args, view, view_lines_text(view))


# The result record the running command emitted under --json, at most one,
# printed by `_machine_run` once the command returns.
_RESULT: list[Any] = []


def _emit(args: argparse.Namespace, record: Any, text: str) -> None:
    """The one printer of a result record (docs/ARCHITECTURE.md section
    7.3): the record, held for the one JSON document under `--json`, its
    rendering printed otherwise."""
    if getattr(args, "json", False):
        _RESULT.append(record)
    else:
        print(text)


def _document(value: Any) -> str:
    """One JSON document, compact like the journal's lines; a leaf that is
    not a number JSON has (a NaN) is a bug, not a document."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _paid(
    paths: RunPaths,
    status: CommandStatus,
    operation: Operation[Req, ReceiptResult],
    request: Req,
    args: argparse.Namespace,
    *,
    steps: int = 1,
) -> None:
    """A paid command through the daemon, the gate enforced where the
    session lives; the run is reloaded for the receipt, since the daemon
    appended what the client does not hold."""
    receipt = broker_gated(paths, operation, request, steps=steps)
    status.run = Run.load(paths, strict=False)
    _emit(args, receipt, result_text(status.run, receipt))


def act_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    # `assay act NAME pname=value ...`: the extra tokens are typed
    # parameters, parsed here against the pinned registry into the name and
    # the parameters object the wire carries (the daemon validates the
    # object again before any spend); values keep the case the agent typed.
    name, params = parse_registry_action(" ".join([args.action, *args.params]), require_registry(run))
    request = ActRequest(
        action=name,
        params=params,
        predict=args.predict,
        because=args.because,
        at_event=args.at_event,
        declares=_parse_declares(args.declare),
    )
    _paid(paths, status, ACT, request, args)


def commit_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    if bool(args.plan) == bool(args.step):
        raise AssayError(
            "commit takes either @plan.json (from `assay model solve`) "
            'or one or more --step "NAME pname=value :: claims"',
            code="COMMAND_ARGS",
        )
    steps: list[Step] = []
    if args.step:
        registry = require_registry(run)
        for raw in args.step:
            token, predict = split_step(raw)
            name, params = parse_registry_action(token, registry)
            steps.append(Step(action=name, params=params, predict=predict))
    request = CommitRequest(
        plan=args.plan,
        steps=tuple(steps),
        at_event=args.at_event,
        declares=_parse_declares(args.declare),
    )
    _paid(paths, status, COMMIT, request, args, steps=max(1, len(steps)))


def reset_command(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    request = ResetRequest(
        because=args.because,
        at_event=args.at_event,
        declares=_parse_declares(args.declare),
    )
    _paid(paths, status, RESET, request, args)


def channel_declare(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    spec = declare_channel(run, args.name, path=args.path, file=args.file)
    print(
        f"CHANNEL | declared {args.name} ({spec['form']}); claims "
        f'like `ch {args.name} = V` now parse and grade'
    )


def channel_list(paths: RunPaths, run: Run, status: CommandStatus, args: argparse.Namespace) -> None:
    listing = channel_list_of(run, fresh=args.read)
    _emit(args, listing, "\n".join(channel_list_text(listing)))


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
    listing = module_list_of(run)
    _emit(args, listing, "\n".join(module_list_text(listing)))


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
    if not math.isfinite(args.usd):
        raise AssayError(f"--usd must be a finite number, got {args.usd}", code="COMMAND_ARGS")
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
    report = audit(run)
    _emit(args, report, "\n".join(audit_lines(report)))


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
            "provide exactly one Python source argument or --file",
            code="COMMAND_ARGS",
        )
    source = (
        args.source if args.source is not None else args.file.read_text()
    )
    run_python(run, source)


# --- the entry point -----------------------------------------------------------


def _wants_json(argv: list[str]) -> bool:
    """Whether the command line asks for the JSON document: the flag before
    a `--`, decided before the parse so a line argparse refuses still
    answers in the form it asked for."""
    head = argv[: argv.index("--")] if "--" in argv else argv
    return "--json" in head


def main() -> None:
    machine = _wants_json(sys.argv[1:])
    try:
        args = _parser().parse_args()
        paths = RunPaths(Path(args.run_dir).resolve())
        os.environ["ASSAY_RUN_DIR"] = str(paths.root)
        raise SystemExit(_machine_run(paths, args) if machine else _run(paths, args))
    except AssayError as error:
        raise SystemExit(_report_error(error, machine=machine))
    except Exception as error:  # noqa: BLE001 - one error voice, traceback saved
        raise SystemExit(_report_internal_error(error, machine=machine))


def _machine_run(paths: RunPaths, args: argparse.Namespace) -> int:
    """`--json`: the command runs with its lines captured; the result record
    it emitted is the document, or, for a command without one, the lines as
    `{"lines": [...]}`. A refusal leaves the lines unprinted: the error
    object is the only output."""
    _RESULT.clear()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = _run(paths, args)
    document = _RESULT[-1].to_json() if _RESULT else {"lines": buffer.getvalue().splitlines()}
    print(_document(document))
    return code


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


def _report_error(error: AssayError, *, machine: bool) -> int:
    """The error voice (docs/ARCHITECTURE.md section 7.1): `ERROR | CODE |
    message` on stderr, `NEXT | hint` when the error names a next step, then
    the error's detail (the claims table); under `--json` the error object
    alone, on stdout. The exit status follows the kind."""
    if machine:
        print(_document(error.to_json()))
    else:
        head, _, tail = error.message.partition("\n")
        print(f"ERROR | {error.code} | {head}", file=sys.stderr)
        if error.hint:
            print(f"NEXT | {error.hint}", file=sys.stderr)
        if tail:
            print(tail, file=sys.stderr)
        if error.detail:
            print(error.detail, file=sys.stderr)
    return exit_code(error.kind)


def _report_internal_error(error: BaseException, *, machine: bool) -> int:
    """Anything that is not an AssayError is a bug or a corrupt file: `INTERNAL`,
    one line in the usual voice with the traceback saved where the user can
    find it, instead of a bare Python traceback with no pointer."""
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
    failure = AssayError(
        f"{type(error).__name__}: {str(error)[:300]}",
        code="INTERNAL",
        hint=f"{saved}; report it with the command that produced it",
    )
    return _report_error(failure, machine=machine)


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
                help="local simulator (default) or remote; the adapter reads the mode and "
                "declares the session rules the kernel applies",
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
        "audit",
        "audit",
        "recompute journal integrity: chain, anchors, ungated events",
        audit_command,
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
