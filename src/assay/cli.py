from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
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
    find_daemon,
    is_remote_config,
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
from .evidence import render_event
from .inspect import export_history, result_text, status_text, view_text
from .live import execute_action, execute_solve_plan, execute_steps, reset_level
from .perception import build_scene_dossier
from .predictions import CLAIMS_HELP
from .registry import gate_optional, load_registry_file
from .rules import RULES_HELP, init_rules, replay_rules, solve_rules


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise AssayError(message)


def _parser() -> Parser:
    parser = Parser(
        prog="assay", description="ASSAY referee harness: look, predict, act, compare"
    )
    parser.add_argument("--run-dir", default=".", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser(
        "start", help="start or resume the one persistent run"
    )
    start.add_argument("game_id")
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

    status = commands.add_parser(
        "status",
        help="full picture: progress, image, actions, recent results, notes",
    )
    status.add_argument("--history", type=int, default=8)

    view = commands.add_parser(
        "view", help="inspect the rendered board, exact pixels, diffs, and animation"
    )
    view.add_argument("--event", type=int)
    view.add_argument(
        "--grid", action="store_true", help="print the complete exact 0-f grid"
    )
    view.add_argument(
        "--frames", action="store_true", help="show causal animation frames"
    )
    view.add_argument(
        "--crop", metavar="R0:R1,C0:C1", help="print an exact half-open crop"
    )
    view.add_argument("--history", type=int, default=0)
    view.add_argument("--export", type=Path, metavar="FILE.npz")

    act = commands.add_parser(
        "act",
        help="take one action with a prediction; the result is graded against it",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLAIMS_HELP,
    )
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
        help="run a prediction-checked batch or a solve-plan; halts on the first miss",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLAIMS_HELP,
    )
    commit.add_argument(
        "plan",
        nargs="?",
        help="solve-plan from `assay rules solve`, e.g. @.assay/plan.json",
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
        "reset", help="pay one action to rewind only the current level"
    )
    reset.add_argument(
        "--because", help="why the current board is worth abandoning (required unless GAME_OVER)"
    )
    reset.add_argument("--at", type=int, dest="at_event")

    python = commands.add_parser(
        "python",
        help="run offline Python with grids, history, perception, BFS, and A* preloaded",
    )
    python.add_argument("source", nargs="?")
    python.add_argument("--file", type=Path)

    rules = commands.add_parser(
        "rules",
        help="optional executable-rules tier: write rules.py, replay-verify, search",
    )
    subcommands = rules.add_subparsers(dest="rules_command", required=True)
    subcommands.add_parser("help", help="print the compact rules.py contract")
    subcommands.add_parser("init", help="create a rules.py template without overwriting")
    subcommands.add_parser(
        "replay", help="check rules.py against every recorded transition"
    )
    solve = subcommands.add_parser(
        "solve", help="A* search rules.py for a level plan with per-step predictions"
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
    channel_commands.add_parser("list", help="list registered channels")

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


def _crop(value: str | None) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    try:
        rows, columns = value.split(",", 1)
        top, bottom = (int(item) for item in rows.split(":", 1))
        left, right = (int(item) for item in columns.split(":", 1))
        return top, bottom, left, right
    except (ValueError, TypeError):
        raise AssayError(
            "crop format is R0:R1,C0:C1, using half-open bounds"
        ) from None


def _action_token(action: str, coordinates: list[str], general: bool = False) -> str:
    if general:
        # Registry runs: `assay act NAME pname=value ...` — the extra tokens are
        # typed parameters; case is preserved (values may be case-sensitive).
        return " ".join([action, *coordinates])
    token = action.upper()
    if coordinates:
        if token != "ACTION6" or len(coordinates) != 2:
            raise AssayError(
                "coordinates are only accepted as `assay act ACTION6 X Y`"
            )
        try:
            token = f"ACTION6:{int(coordinates[0])},{int(coordinates[1])}"
        except ValueError:
            raise AssayError(
                "ACTION6 coordinates must be integers: `assay act ACTION6 X Y`"
            ) from None
    return token


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
    paths.root.mkdir(parents=True, exist_ok=True)
    mode = str(args.mode or os.getenv("ASSAY_MODE", LOCAL_MODE)).lower()
    if mode not in {LOCAL_MODE, REMOTE_MODE}:
        raise AssayError(
            f"ASSAY_MODE must be {LOCAL_MODE!r} or {REMOTE_MODE!r}, got {mode!r}"
        )
    config: dict[str, Any] = {
        "game_id": requested,
        "seed": args.seed,
        "mode": mode,
        "adapter": args.adapter,
        "registry": registry_spec is not None,
        "created_at": now_iso(),
        "harness": "assay",
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
        if "frames" in event:
            render_event(paths, event)
            build_scene_dossier(paths, [event])
        if getattr(args, "import_knowledge", None) is not None:
            summary = import_knowledge(paths, args.import_knowledge)
            print(
                f"IMPORTED | knowledge from {summary['source_game']} | "
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
    if registry_spec is not None and gate_optional(registry_spec):
        print(
            "USE | gate: optional — `assay act` runs with or without --predict "
            "(an unpredicted act is journaled UNGATED; the audit marks the run "
            "invalid for scoring); parameters go as `assay act NAME pname=value ...`; "
            "schemas are in REGISTRY above, semantics are never given — learn them by acting"
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


def _print_replay(result: dict[str, Any]) -> None:
    pixels = (
        "render() not defined"
        if result["pixel_checks"] == 0
        else f"exact pixels {result['pixel_matches']}/{result['pixel_checks']}"
    )
    print(
        f"REPLAY | {result['status']} | {result['explained']}/{result['transitions']} "
        f"transitions explained | {pixels}"
    )
    mismatch = result.get("first_mismatch")
    if mismatch:
        print(
            f"FIRST MISMATCH | e{mismatch['event']} {mismatch['action']} | {mismatch['detail']}"
        )
        if "expected" in mismatch:
            print(f"  expected observe: {mismatch['expected']}")
            print(f"  actual observe:   {mismatch['actual']}")
    for gap in result.get("gaps", ()):
        print(f"GAP | {gap}")
    if result["status"] == "HISTORY_FIT":
        print("NOTE | fits recorded history; unseen mechanics can still differ")


def _print_solve(result: dict[str, Any]) -> None:
    print(
        f"SOLVE | {result['status']} | nodes {result['nodes']} | frontier {result['frontier']} "
        f"| {result['elapsed_seconds']:.3f}s | unknown edges {result['unknown_edges']} "
        f"| dead pruned {result['dead_pruned']}"
    )
    if result.get("replay_status") != "HISTORY_FIT":
        print(
            f"REPLAY | {result['replay_status']} | {len(result.get('replay_gaps', ()))} gaps "
            "— the plan trusts rules that history has not verified"
        )
    if result.get("actions"):
        print("ACTIONS | " + " ".join(result["actions"]))
        print(f"PLAN | {result['plan']} — execute with `assay commit @.assay/plan.json`")
    elif result["status"] == "NO_PLAN_IN_MODEL":
        print(
            "NOTE | no goal state reachable inside rules.py; actions() or step() are "
            "too narrow, or the level needs something unmodeled"
        )
    if result["status"] in {"TIME_LIMIT", "NODE_LIMIT"}:
        print(
            "NOTE | a bound was reached; not evidence that no solution exists "
            "(--seconds / --max-nodes raise it)"
        )
    if result.get("unknown_edges") and not result.get("actions"):
        print(
            f"NOTE | {result['unknown_edges']} Unknown edge(s) were skipped during "
            "search; extending step() may open routes"
        )


def main() -> None:
    try:
        args = _parser().parse_args()
        paths = RunPaths(Path(args.run_dir).resolve())
        if args.command == "start":
            try:
                with run_lock(paths):
                    _start(paths, args)
            except AssayError:
                _discard_empty_state(paths)
                raise
            raise SystemExit(0)
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
                if args.command == "rules"
                else args.command,
            ),
        ):
            if args.command == "status":
                print(status_text(paths, history=args.history))
            elif args.command == "view":
                print(
                    view_text(
                        paths,
                        event_id=args.event,
                        grid=args.grid,
                        frames=args.frames,
                        crop=_crop(args.crop),
                        history=args.history,
                    )
                )
                if args.export:
                    destination = (
                        args.export
                        if args.export.is_absolute()
                        else paths.root / args.export
                    )
                    print(f"EXPORTED | {export_history(paths, destination)}")
            elif args.command == "act":
                general = read_json(paths.registry, None) is not None
                token = _action_token(args.action, args.coordinates, general)
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
                        "commit takes either @plan.json (from `assay rules solve` on "
                        "grid runs, `assay model solve` on registry runs) or one or "
                        'more --step "ACTION :: claims"'
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
                else:
                    receipt = (
                        execute_solve_plan(paths, args.plan, at_event=args.at_event)
                        if args.plan
                        else execute_steps(paths, args.step, at_event=args.at_event)
                    )
                print(result_text(paths, receipt))
            elif args.command == "reset":
                if read_json(paths.registry, None) is not None:
                    receipt = broker_gated(
                        paths,
                        {
                            "op": "gated_reset",
                            "because": args.because,
                            "at_event": args.at_event,
                        },
                    )
                else:
                    receipt = reset_level(
                        paths,
                        because=args.because,
                        at_event=args.at_event,
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
                    declared = load_declared(paths)
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
                    print("MODULES | active (name, mode, origin)")
                    for item, mode in active_modules(paths, registry):
                        print(f"  {item.NAME} | {mode} | {origins.get(item.NAME, 'built-in')}")
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
                print(f"EXPORTED | {target} — import with `assay start GAME --import {target.name}`")
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
                if read_json(paths.registry, None) is not None:
                    raise AssayError(
                        "the rules tier applies to grid runs; this registry run "
                        "has none — model mechanics with `assay python` instead"
                    )
                if args.rules_command == "help":
                    print(RULES_HELP)
                elif args.rules_command == "init":
                    print(f"CREATED | {init_rules(paths)}")
                    print(RULES_HELP)
                elif args.rules_command == "replay":
                    result = replay_rules(paths)
                    atomic_json(paths.verification, result)
                    append_jsonl(paths.activity, {"kind": "rules_replay", **result})
                    _print_replay(result)
                elif args.rules_command == "solve":
                    result = solve_rules(
                        paths, seconds=args.seconds, max_nodes=args.max_nodes
                    )
                    append_jsonl(paths.activity, {"kind": "rules_solve", **result})
                    _print_solve(result)
                else:
                    raise AssayError(
                        f"unsupported rules command {args.rules_command}"
                    )
            else:
                raise AssayError(f"unsupported command {args.command}")
        raise SystemExit(0)
    except AssayError as error:
        print(f"ERROR | {error}", file=sys.stderr)
        raise SystemExit(2)
