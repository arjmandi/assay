from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

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
    LOCAL_MODE,
    REMOTE_MODE,
    broker_gated,
    broker_matches_latest_event,
    broker_observe,
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
from .channels import declare_channel, known_channels, load_declared
from .integrity import anchor_line, anchor_status, audit, audit_lines, environment_anchor_file
from .model import (
    fit_lines,
    init_model,
    replay_model,
    solve_model,
)
from .modules import (
    active_modules,
    install_module,
    load_manifest,
    pin_external_modules,
    unlisted_lines,
)
from .core import (
    AssayError,
    RunPaths,
    append_event,
    append_jsonl,
    atomic_json,
    command_status,
    load_events,
    load_jsonl,
    make_event,
    normalize_game_id,
    now_iso,
    read_json,
    require_run,
    run_lock,
)
from .extras import kind_for, require_kind
from .inspect import result_text, status_text, view_text
from .live import execute_action, execute_steps, reset_level
from .predictions import claims_help
from .registry import gate_mode, load_registry_file


class Parser(argparse.ArgumentParser):
    """argparse with the kernel's error voice, and an epilog that is built only
    when help is rendered, so listing the claim forms of an installed
    observation kind never imports that kind on an ordinary command."""

    lazy_epilog: Any = None

    def error(self, message: str) -> None:
        raise AssayError(message)

    def format_help(self) -> str:
        if self.lazy_epilog is not None:
            self.epilog = self.lazy_epilog()
        return super().format_help()


def _parser() -> Parser:
    parser = Parser(
        prog="assay", description="ASSAY referee harness: look, predict, act, compare"
    )
    parser.add_argument("--run-dir", default=".", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser(
        "start", help="start or resume the one persistent run"
    )
    start.add_argument("game_id", metavar="world_id", help="a label for this run, 2 to 16 characters of a-z and 0-9; a benchmark adapter may read it to pick the instance")
    start.add_argument("--seed", type=int, default=0, help=argparse.SUPPRESS)
    start.add_argument(
        "--adapter",
        help="world adapter factory: module:factory or /path/file.py:factory",
    )
    start.add_argument(
        "--registry",
        type=Path,
        help="JSON file registering general actions, parameter schemas, and an action budget",
    )
    start.add_argument(
        "--mode",
        choices=(LOCAL_MODE, REMOTE_MODE),
        help="local simulator (default), or expiring remote competition validation",
    )
    start.add_argument(
        "--import",
        dest="import_knowledge",
        type=Path,
        metavar="KNOWLEDGE.json",
        help="import a prior run's exported knowledge (lands FOREIGN, demoted)",
    )
    start.add_argument(
        "--owner-token-file",
        type=Path,
        metavar="PATH",
        help="write the owner token to this file (mode 0600, outside the run "
        "directory) instead of printing it; ASSAY_OWNER_TOKEN_FILE does the same",
    )

    commands.add_parser(
        "stop",
        help="stop this run's environment owner (the daemon) cleanly; "
        "`assay start` resumes the run later",
    )

    commands.add_parser(
        "doctor",
        help="check the interpreter, dependencies, anchors, socket path, run "
        "state, daemon, adapter and registry; works with or without a run here",
    )

    status = commands.add_parser(
        "status",
        help="full picture: progress, image, actions, recent results, notes",
    )
    status.add_argument("--history", type=int, default=8)

    view = commands.add_parser(
        "view", help="inspect one event: the observation, the delta since the previous one, history"
    )
    view.add_argument("--event", type=int)
    view.add_argument("--history", type=int, default=0)
    # Frame worlds only; inert on a dict run, which says so.
    view.add_argument(
        "--grid", action="store_true", help="frame worlds: print the complete exact 0-f grid"
    )
    view.add_argument(
        "--frames", action="store_true", help="frame worlds: show causal animation frames"
    )
    view.add_argument(
        "--crop", metavar="R0:R1,C0:C1", help="frame worlds: print an exact half-open crop"
    )
    view.add_argument(
        "--export", type=Path, metavar="FILE.npz", help="frame worlds: export the grid history"
    )

    act = commands.add_parser(
        "act",
        help="take one action with a prediction; the result is graded against it",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    act.lazy_epilog = claims_help
    act.add_argument("action")
    act.add_argument("coordinates", nargs="*", help=argparse.SUPPRESS)
    act.add_argument(
        "--predict",
        required=False,
        help='what this action does, e.g. "move 12,5 1,0" (see below); required '
        "unless the registry sets gate: optional",
    )
    act.add_argument("--because", help="short reason for choosing this action")
    act.add_argument("--at", type=int, dest="at_event")
    act.add_argument(
        "--declare",
        action="append",
        default=[],
        metavar='"field=value"',
        help="structural declaration a gate or module demanded "
        '(e.g. --declare "worst_case=..." --declare "recovery=...")',
    )

    commit = commands.add_parser(
        "commit",
        help="run a prediction-checked batch or a model plan; halts on the first miss",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commit.lazy_epilog = claims_help
    commit.add_argument(
        "plan",
        nargs="?",
        help="a plan file, e.g. @.assay/model_plan.json from `assay model solve`",
    )
    commit.add_argument(
        "--step",
        action="append",
        default=[],
        metavar='"ACTION :: CLAIMS"',
        help="one action with its own prediction; repeat in execution order",
    )
    commit.add_argument("--at", type=int, dest="at_event")
    commit.add_argument(
        "--declare",
        action="append",
        default=[],
        metavar='"field=value"',
        help="structural declaration a module demanded for a step in this batch",
    )

    reset = commands.add_parser(
        "reset", help="pay one action to rewind the current progress unit"
    )
    reset.add_argument(
        "--because", help="why the current state is worth abandoning (required unless GAME_OVER)"
    )
    reset.add_argument("--at", type=int, dest="at_event")
    reset.add_argument(
        "--declare",
        action="append",
        default=[],
        metavar='"field=value"',
        help="structural declaration a module demanded for this reset "
        '(e.g. --declare "impossible=..." --declare "coverage_audit=...")',
    )

    python = commands.add_parser(
        "python",
        help="run offline Python with the history, deltas, BFS and A* preloaded",
    )
    python.add_argument("source", nargs="?")
    python.add_argument("--file", type=Path)

    # Frame worlds without a registry: the executable-rules tier. Declared
    # here, handled by the frame-world extra at run time.
    rules = commands.add_parser(
        "rules",
        help="frame worlds, no registry: the executable-rules tier (rules.py, replay-verify, search)",
    )
    subcommands = rules.add_subparsers(dest="rules_command", required=True)
    subcommands.add_parser("help", help="print the compact rules.py contract")
    subcommands.add_parser("init", help="create a rules.py template without overwriting")
    subcommands.add_parser(
        "replay", help="check rules.py against every recorded transition"
    )
    solve = subcommands.add_parser(
        "solve", help="A* search rules.py for a plan to the next progress unit, with per-step predictions"
    )
    solve.add_argument("--seconds", type=float, default=15.0)
    solve.add_argument("--max-nodes", type=int, default=250_000)

    channel = commands.add_parser(
        "channel", help="declare and list registered channels (named readings)"
    )
    channel_commands = channel.add_subparsers(dest="channel_command", required=True)
    channel_declare = channel_commands.add_parser(
        "declare", help="register a named reading of the observation"
    )
    channel_declare.add_argument("name")
    channel_declare.add_argument(
        "--path", help="dotted keys into the dict observation, e.g. counters.red"
    )
    channel_declare.add_argument(
        "--file", help="extractor file: def extract(obs) -> value (sandboxed)"
    )
    channel_list = channel_commands.add_parser(
        "list", help="list registered channels with their current readings"
    )
    channel_list.add_argument(
        "--read",
        action="store_true",
        help="compute extractor channels fresh (runs each extractor sandboxed) "
        "instead of showing the last graded reading",
    )

    model = commands.add_parser(
        "model",
        help="the general world-model tier: replay-fit is trust; fit models earn batching",
    )
    model_commands = model.add_subparsers(dest="model_command", required=True)
    model_commands.add_parser("init", help="create a model.py template")
    model_commands.add_parser(
        "replay", help="grade model.py's declared channels over every recorded transition"
    )
    model_solve = model_commands.add_parser(
        "solve", help="search the model for a plan to a channel target"
    )
    model_solve.add_argument(
        "--to", required=True, metavar='"ch NAME = V"', help="the goal reading"
    )
    model_solve.add_argument("--seconds", type=float, default=15.0)
    model_solve.add_argument("--max-nodes", type=int, default=100_000)
    model_solve.add_argument("--max-depth", type=int, default=40)

    module = commands.add_parser(
        "module", help="behavior modules: list the active set, install one (owner)"
    )
    module_commands = module.add_subparsers(dest="module_command", required=True)
    module_commands.add_parser(
        "list", help="active modules with mode and origin, plus ignored files"
    )
    module_install = module_commands.add_parser(
        "install", help="owner: install a module file mid-run (journaled, manifest-pinned)"
    )
    module_install.add_argument("path", type=Path)
    module_install.add_argument("--token")

    goal = commands.add_parser(
        "goal", help="the standing goal: propose revisions (agent), ratify (owner)"
    )
    goal_commands = goal.add_subparsers(dest="goal_command", required=True)
    goal_propose = goal_commands.add_parser(
        "propose", help="propose a standing-goal revision (journaled, owner ratifies)"
    )
    goal_propose.add_argument("text")
    goal_propose.add_argument("--because")
    goal_commands.add_parser("list", help="list goal proposals and their status")
    goal_ratify = goal_commands.add_parser(
        "ratify", help="owner: ratify a proposal by id (requires the owner token)"
    )
    goal_ratify.add_argument("id", type=int)
    goal_ratify.add_argument("--token")

    export = commands.add_parser(
        "export", help="export this run's earned knowledge for a future import"
    )
    export.add_argument("--out", type=Path)

    spend = commands.add_parser(
        "spend", help="the external spend feed (the kernel cannot see the LLM bill)"
    )
    spend_commands = spend.add_subparsers(dest="spend_command", required=True)
    spend_report = spend_commands.add_parser(
        "report", help="post cumulative usage (idempotent by --id; last entry wins)"
    )
    spend_report.add_argument("--usd", type=float, required=True)
    spend_report.add_argument("--tokens", type=int, default=0)
    spend_report.add_argument("--id", dest="report_id", required=True)

    commands.add_parser(
        "audit", help="recompute journal integrity: chain, anchors, ungated events"
    )

    approve = commands.add_parser(
        "approve", help="owner: grant one use of an approval-gated action"
    )
    approve.add_argument("action")
    approve.add_argument("--token")

    waive = commands.add_parser(
        "waive", help="owner: waive a live actuator's rehearsal quota (journaled)"
    )
    waive.add_argument("action")
    waive.add_argument("--token")
    waive.add_argument("--because")
    return parser


def _parse_declares(raw: list[str]) -> dict[str, str]:
    declares: dict[str, str] = {}
    for item in raw or ():
        field, separator, value = str(item).partition("=")
        if not separator or not field.strip():
            raise AssayError(f'declarations are --declare "field=value", got {item!r}')
        declares[field.strip()] = value.strip()
    return declares


def _action_token(paths: RunPaths, action: str, coordinates: list[str], general: bool) -> str:
    if general:
        # Registry runs: `assay act NAME pname=value ...` — the extra tokens are
        # typed parameters; case is preserved (values may be case-sensitive).
        return " ".join([action, *coordinates])
    events = load_events(paths)
    kind = require_kind(events[-1] if events else None, "a run without a registry")
    return kind.legacy_action_token(action, coordinates)


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
        f"# Notes — {game_id}\n"
        "\n"
        "## Verified (cite event ids)\n"
        "\n"
        "## Assumed / open questions\n"
        "\n"
        "## Plan\n"
    )


def _remote_idle_seconds(paths: RunPaths, config: dict[str, Any]) -> float:
    mutations = load_jsonl(paths.mutations)
    timestamp = (
        mutations[-1].get("timestamp") if mutations else config.get("created_at")
    )
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
    requested = normalize_game_id(args.game_id)
    registry_spec = (
        load_registry_file(args.registry) if args.registry is not None else None
    )
    existing = read_json(paths.config)
    if isinstance(existing, dict):
        if getattr(args, "import_knowledge", None) is not None:
            raise AssayError(
                "knowledge imports happen at run start; this directory already "
                "owns a run"
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
        # Orphan recovery (a spend the daemon journaled in mutations.jsonl
        # before anyone appended its event) runs here and only here, and only
        # once the daemon is confirmed dead or absent. While the daemon lives,
        # a mutation without an event is a step in flight: recovering it from
        # another process would double-count it the moment the daemon appends
        # its own graded event. The numbered-action path is the one exception
        # (its daemon never writes events, the CLI is the only writer).
        recovered = 0
        owner = read_json(paths.broker, {})
        if owner.get("status") == "FINISHED":
            if find_daemon(paths) is None:
                recovered = reconcile_mutations(paths)
            print(f"RESUMED | {requested} | completed run")
            if recovered:
                print(f"JOURNAL | recovered {recovered} paid action(s) into timeline")
            print(status_text(paths))
            return
        if is_remote_config(existing):
            idle = _remote_idle_seconds(paths, existing)
            if idle >= 15 * 60:
                raise AssayError(
                    "REMOTE_LEASE_EXPIRED | no live action was recorded for at least 15 minutes. The remote competition run is not recoverable; preserve this directory and use a fresh one"
                )
            if not broker_ping(paths):
                if find_daemon(paths) is None:
                    # Real spends against the remote world: journal them
                    # before refusing, so the record is complete.
                    reconcile_mutations(paths)
                raise AssayError(
                    "the remote competition owner is unavailable and cannot be reconstructed; preserve this directory and use a fresh one"
                )
            if not broker_matches_latest_event(paths):
                raise AssayError(
                    "REMOTE_STATE_DIVERGED | the live remote observation differs from the append-only timeline; stop using this run"
                )
            print(
                f"RESUMED | {requested} | REMOTE competition | action-idle lease about {max(0, 15 - int(idle // 60))}m"
            )
        else:
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
                recovered = reconcile_mutations(paths)
                paths.socket.unlink(missing_ok=True)
                start_broker(paths)
                restarted = True
            elif read_json(paths.registry, None) is None:
                recovered = reconcile_mutations(paths)
            if not broker_matches_latest_event(paths):
                raise AssayError(
                    "LOCAL_REPLAY_DIVERGED | reconstructed simulator state differs from the latest timeline event"
                )
            verb = "RECOVERED" if restarted else "RESUMED"
            print(
                f"{verb} | {requested} | local simulator | replayed {len(load_events(paths)) - 1} paid actions"
            )
        if recovered:
            print(f"JOURNAL | recovered {recovered} paid action(s) into timeline")
        print(status_text(paths))
        return

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
    config: dict[str, Any] = {
        "game_id": requested,
        "seed": args.seed,
        "mode": mode,
        "adapter": adapter_spec,
        "registry": registry_spec is not None,
        "created_at": now_iso(),
        "harness": "assay",
        "python": sys.executable,
    }
    config["binding_hash"] = binding_hash_of(config)
    if registry_spec is not None:
        config["registry_hash"] = registry_hash_of(registry_spec)
        # Pinned at start so every later command, whatever its environment,
        # anchors to and audits the same file.
        config["anchor_file"] = str(environment_anchor_file(paths))
    paths.state.mkdir(parents=True, exist_ok=True)
    atomic_json(paths.config, config)
    if registry_spec is not None:
        atomic_json(paths.registry, registry_spec)
    (paths.state / "python").write_text(sys.executable + "\n")
    # Owner authority exists for registry runs; the numbered-action path stays
    # unchanged (no token line in its start output).
    owner_token = mint_owner_token(paths) if registry_spec is not None else None
    try:
        if owner_token is not None and token_file is not None:
            _write_owner_token(token_file, owner_token)
        if registry_spec is not None:
            # Module files are loaded once here to check their contract; a
            # refusal must leave no half-initialized run behind.
            pin_external_modules(paths, registry_spec)
        start_broker(paths)
        observation, public_info = broker_observe(paths)
        config["public_info"] = public_info
        atomic_json(paths.config, config)
        event = append_event(
            paths,
            make_event(observation, "START", None, None, note="initial observation"),
        )
        _write_notes(paths, requested)
        kind = kind_for(event)
        if kind is not None:
            kind.after_record(paths, event, [event])
        if getattr(args, "import_knowledge", None) is not None:
            summary = import_knowledge(paths, args.import_knowledge)
            print(
                f"IMPORTED | knowledge from source world {summary['source_game']} | "
                f"{summary['verifiers']} verifier candidate(s) | hazards active "
                f"{summary['hazards_active']} (foreign-inactive "
                f"{summary['hazards_foreign_inactive']}) | everything FOREIGN, "
                "demoted until re-earned (see status)"
            )
    except Exception:
        stop_broker(paths)
        shutil.rmtree(paths.state, ignore_errors=True)
        raise
    if owner_token is not None and token_file is not None:
        print(
            f"OWNER TOKEN | written to {token_file} (mode 0600) — only its hash is "
            "stored in the run; pass the file's content as --token for "
            "ratifications, approvals and waivers (the agent proposes, never "
            "self-ratifies)"
        )
    elif owner_token is not None:
        print(
            f"OWNER TOKEN | {owner_token} — printed once, only its hash is stored; "
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
    if registry_spec is not None and not anchor_status(paths)["writable"]:
        print(
            f"WARNING | {anchor_line(paths)} | set ASSAY_ANCHOR_DIR to a writable "
            "directory before the first anchor is due"
        )
    print(status_text(paths))
    if registry_spec is not None and gate_mode(registry_spec) == "optional":
        print(
            "USE | gate: optional — `assay act` runs with or without --predict "
            "(an unpredicted act is journaled UNGATED; the audit marks the run "
            "invalid for scoring); parameters go as `assay act NAME pname=value ...`; "
            "schemas are in REGISTRY above, semantics are never given — learn them by acting"
        )
    elif registry_spec is not None and gate_mode(registry_spec) == "off":
        print(
            "USE | gate: off — `assay act NAME pname=value ...` with no --predict "
            "(predictions are not accepted on this run and nothing is graded; every "
            "paid action is journaled UNGATED and the audit marks the run invalid "
            "for scoring); schemas are in REGISTRY above, semantics are never given "
            "— learn them by acting"
        )
    elif registry_spec is not None:
        print(
            'USE | every `assay act` needs --predict "<claims>"; parameters go as '
            "`assay act NAME pname=value ...`; schemas are in REGISTRY above, "
            "semantics are never given — learn them by acting"
        )
    else:
        print(
            'USE | open IMAGE first; every `assay act` needs --predict "<claims>" '
            "(`assay act --help` lists the claim forms)"
        )


def _doctor(paths: RunPaths) -> int:
    """Everything a new user hits in the first hour, checked in one place and
    reported in one voice. Exit 2 when something FAILs, else 0."""
    lines: list[tuple[str, str]] = []

    def note(level: str, text: str) -> None:
        lines.append((level, text))

    version = ".".join(str(part) for part in sys.version_info[:3])
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
    socket_path = str(paths.socket)
    note(
        "ok" if len(socket_path.encode()) <= 100 else "FAIL",
        f"socket path {socket_path} ({len(socket_path.encode())} bytes, limit about 104)",
    )
    config = read_json(paths.config, None) if paths.config.exists() else None
    if not isinstance(config, dict):
        note("ok", f"no run in {paths.root} (`assay start` creates one)")
        status = anchor_status(paths)
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
            status = anchor_status(paths)
            note(
                "ok" if status["writable"] and not status["failed"] else "WARN",
                anchor_line(paths)[len("ANCHORS | "):],
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
        adapter = config.get("adapter")
        if adapter:
            try:
                resolved = resolve_adapter_spec(str(adapter), paths.root)
                check_adapter_spec(resolved, paths.root)
                note("ok", f"adapter {resolved} imports")
            except AssayError as error:
                note("FAIL", f"adapter: {error}")
        else:
            note("WARN", "no adapter recorded (a numbered-action run)")
        if paths.registry.exists():
            try:
                from .registry import validate_registry

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


def main() -> None:
    try:
        args = _parser().parse_args()
        paths = RunPaths(Path(args.run_dir).resolve())
        os.environ["ASSAY_RUN_DIR"] = str(paths.root)
        if args.command == "start":
            try:
                with run_lock(paths):
                    _start(paths, args)
            except AssayError:
                _discard_empty_state(paths)
                raise
            raise SystemExit(0)
        if args.command == "doctor":
            raise SystemExit(_doctor(paths))
        if args.command == "stop":
            if paths.state.is_dir():
                with run_lock(paths):
                    _stop(paths)
            else:
                # No run state here (an orphaned daemon after a hand-deleted
                # `.assay`): stop without creating state as a side effect.
                _stop(paths)
            raise SystemExit(0)

        require_run(paths)
        with (
            run_lock(paths),
            command_status(
                paths,
                f"rules {args.rules_command}"
                if getattr(args, "rules_command", None)
                else args.command,
            ),
        ):
            if args.command == "status":
                print(status_text(paths, history=args.history))
            elif args.command == "view":
                events = load_events(paths)
                flags = {"grid": args.grid, "frames": args.frames, "crop": args.crop}
                print(view_text(paths, event_id=args.event, history=args.history, flags=flags))
                export = args.export
                if export:
                    destination = export if export.is_absolute() else paths.root / export
                    print(
                        f"EXPORTED | {require_kind(events[-1] if events else None, 'export').export_history(paths, destination)}"
                    )
            elif args.command == "act":
                general = read_json(paths.registry, None) is not None
                token = _action_token(paths, args.action, args.coordinates, general)
                if general:
                    # Daemon-side gate: enforcement where the session lives.
                    receipt = broker_gated(
                        paths,
                        {
                            "op": "gated_act",
                            "action_token": token,
                            "predict": args.predict,
                            "because": args.because,
                            "at_event": args.at_event,
                            "declares": _parse_declares(args.declare),
                        },
                    )
                else:
                    receipt = execute_action(
                        paths,
                        token,
                        predict=args.predict,
                        because=args.because,
                        at_event=args.at_event,
                    )
                print(result_text(paths, receipt))
            elif args.command == "commit":
                if bool(args.plan) == bool(args.step):
                    raise AssayError(
                        "commit takes either @plan.json (from `assay model solve` on "
                        "registry runs, `assay rules solve` on frame runs without one) "
                        'or one or more --step "ACTION :: claims"'
                    )
                if read_json(paths.registry, None) is not None:
                    receipt = broker_gated(
                        paths,
                        {
                            "op": "gated_commit",
                            "plan": args.plan,
                            "steps": args.step,
                            "at_event": args.at_event,
                            "declares": _parse_declares(args.declare),
                        },
                        steps=max(1, len(args.step)),
                    )
                elif args.plan:
                    events = load_events(paths)
                    kind = require_kind(events[-1] if events else None, "a plan commit without a registry")
                    receipt = kind.execute_plan(paths, args.plan, args.at_event)
                else:
                    receipt = execute_steps(paths, args.step, at_event=args.at_event)
                print(result_text(paths, receipt))
            elif args.command == "reset":
                if read_json(paths.registry, None) is not None:
                    receipt = broker_gated(
                        paths,
                        {
                            "op": "gated_reset",
                            "because": args.because,
                            "at_event": args.at_event,
                            "declares": _parse_declares(args.declare),
                        },
                    )
                else:
                    receipt = reset_level(
                        paths,
                        because=args.because,
                        at_event=args.at_event,
                        declares=_parse_declares(args.declare),
                    )
                print(result_text(paths, receipt))
            elif args.command == "channel":
                if args.channel_command == "declare":
                    spec = declare_channel(
                        paths, args.name, path=args.path, file=args.file
                    )
                    print(
                        f"CHANNEL | declared {args.name} ({spec['form']}) — claims "
                        f'like `ch {args.name} = V` now parse and grade'
                    )
                else:
                    from .channels import channel_lines

                    declared = load_declared(paths)
                    events = load_events(paths)
                    if events:
                        print("\n".join(channel_lines(paths, events[-1], fresh=args.read)))
                    else:
                        print("CHANNELS | " + " · ".join(known_channels(paths)))
                    for name, spec in sorted(declared.items()):
                        detail = spec.get("path") or spec.get("hash", "")[:12]
                        print(f"  {name}: {spec['form']} {detail}")
            elif args.command == "model":
                if args.model_command == "init":
                    print(f"CREATED | {init_model(paths)} — declare CHANNELS, define next()")
                elif args.model_command == "replay":
                    record = replay_model(paths)
                    print("\n".join(fit_lines(record)))
                else:
                    result = solve_model(
                        paths,
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
                        print("ACTIONS | " + " → ".join(result["actions"]))
                        print(
                            "PLAN | .assay/model_plan.json — execute with "
                            "`assay commit @.assay/model_plan.json` (needs replay-fit "
                            "promotion on the current journal)"
                        )
                    else:
                        print(
                            f"SOLVE | no plan inside the model | nodes {result['nodes']} "
                            "— actions() or next() are too narrow, or the goal needs "
                            "something unmodeled"
                        )
            elif args.command == "module":
                if args.module_command == "install":
                    record = install_module(paths, args.path, args.token)
                    print(
                        f"MODULE | installed {record['name']} from {record['source']} "
                        f"(sha256 {record['sha256'][:12]}) | journaled | active from the "
                        "next action"
                    )
                else:
                    registry = read_json(paths.registry, None)
                    origins = {
                        str(entry.get("name")): str(entry.get("origin"))
                        for entry in load_manifest(paths)
                    }
                    from .modules import JournalView

                    view = JournalView(paths=paths, events=load_events(paths), registry=registry)
                    print("MODULES | active (name, mode, origin), constitution, telemetry")
                    for item, mode in active_modules(paths, registry):
                        print(f"  {item.NAME} | {mode} | {origins.get(item.NAME, 'built-in')}")
                        print(f"    constitution: {item.CONSTITUTION}")
                        try:
                            telemetry = item.telemetry(view)
                        except Exception as error:  # noqa: BLE001 - a module's counters never break the listing
                            telemetry = {"error": f"{type(error).__name__}: {error}"}
                        print(f"    telemetry: {json.dumps(telemetry, sort_keys=True, default=str)}")
                    for line in unlisted_lines(paths):
                        print(line)
            elif args.command == "goal":
                if args.goal_command == "propose":
                    record = propose_goal(paths, args.text, args.because)
                    print(
                        f"GOAL | proposal #{record['id']} journaled — awaiting owner "
                        "ratification (`assay goal ratify ID --token ...`)"
                    )
                elif args.goal_command == "list":
                    proposals = list_proposals(paths)
                    if not proposals:
                        print("GOAL | no proposals")
                    for item in proposals:
                        print(
                            f"  #{item['id']} [{item['status']}] {item['text']}"
                            + (f" — {item['because']}" if item.get("because") else "")
                        )
                else:
                    proposal = ratify_goal(paths, args.id, args.token)
                    print(
                        f"GOAL | ratified #{args.id}: {proposal['text']} — status now "
                        "re-presents it as the standing goal"
                    )
            elif args.command == "export":
                target = export_knowledge(paths, args.out)
                print(f"EXPORTED | {target} — import with `assay start WORLD_ID --import {target.name}`")
            elif args.command == "spend":
                append_jsonl(
                    paths.activity,
                    {
                        "kind": "spend_report",
                        "id": args.report_id,
                        "usd": args.usd,
                        "tokens": args.tokens,
                    },
                )
                from .registry import spend_reports

                usd, tokens = spend_reports(load_jsonl(paths.activity))
                print(f"SPEND | recorded | cumulative ${usd:.2f} | {tokens} tokens")
            elif args.command == "audit":
                print("\n".join(audit_lines(audit(paths))))
            elif args.command == "approve":
                grant_approval(paths, args.action, args.token)
                print(
                    f"APPROVED | one use of {args.action.upper()} granted "
                    "(expires in 10 minutes, consumed on use)"
                )
            elif args.command == "waive":
                grant_waiver(paths, args.action, args.token, args.because or "")
                print(f"WAIVED | rehearsal quota for {args.action.upper()} (journaled)")
            elif args.command == "python":
                if bool(args.source) == bool(args.file):
                    raise AssayError(
                        "provide exactly one Python source argument or --file"
                    )
                source = (
                    args.source if args.source is not None else args.file.read_text()
                )
                run_python(paths, source)
            elif args.command == "rules":
                events = load_events(paths)
                require_kind(events[-1] if events else None, "the rules tier").cli_handle(paths, args)
            else:
                raise AssayError(f"unsupported command {args.command}")
        raise SystemExit(0)
    except AssayError as error:
        print(f"ERROR | {error}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as error:  # noqa: BLE001 - one error voice, traceback saved
        raise SystemExit(_report_internal_error(error))


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
