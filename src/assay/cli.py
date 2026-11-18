"""The command line: the surface an agent, or a person, operates a run through.

The operator starts and stops a run. The agent begins at `status`. The paid
commands (`act`, `commit`, `reset`) and `state declare` go to the daemon; the
others read the record. A refusal is one line, `ERROR | CODE | message`, then
`NEXT | hint` when there is a next step, and exit status 2.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, NoReturn

from . import AssayError, __version__, analysis, daemon, diagnose, grammar, journal, show, states
from .journal import Paths, Run
from .world import load_registry, registry_hash, resolve_adapter

NOTES = """\
# Notes: {world}

## Verified (cite event ids)

## Assumed / open questions

## Plan
"""
USE = (
    'USE | every `assay act` needs --predict "<outcomes>"; parameters go as '
    "`assay act NAME pname=value ...` (an object or an array as `--params '{...}'`); "
    "schemas are in REGISTRY above, semantics are never given: learn them by acting"
)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise AssayError(message, code="COMMAND_ARGS", hint="`assay --help` lists the commands")


def _json(raw: str, flag: str) -> Any:
    """The JSON a flag carries: the text itself, or the content of `@FILE`."""
    try:
        return json.loads(Path(raw[1:]).expanduser().read_text() if raw.startswith("@") else raw)
    except (OSError, ValueError) as error:
        raise AssayError(f"{flag} is not readable JSON: {error}", code="COMMAND_ARGS") from None


def _paid(paths: Paths, op: str, args: dict[str, Any], steps: int = 1) -> None:
    try:
        print(daemon.call(paths, op, args, timeout=60.0 + 30.0 * steps))
    except AssayError as error:
        if error.code == "DAEMON_UNAVAILABLE" and Run.load(paths).last["state"] == "WIN":
            raise AssayError("the goal is already reached; this run is complete", code="RUN_COMPLETE") from None
        raise


# --- the operator's commands -----------------------------------------------------


def start(paths: Paths, args: argparse.Namespace) -> None:
    """Start a fresh run here, or resume the one this directory holds."""
    if paths.config.exists():
        run = Run.load(paths)
        if run.config["world_id"] != args.world_id:
            raise AssayError(
                f"this directory holds the run {run.config['world_id']!r}; one directory is one run",
                code="RUN_EXISTS",
                hint="use a fresh directory for another run",
            )
        if run.events and run.last["state"] == "WIN":
            print(f"COMPLETE | {args.world_id} | the goal is reached; nothing to resume")
        elif daemon.alive(paths):
            print(f"RESUMED | {args.world_id} | the daemon was running")
        else:
            daemon.spawn(paths)
            print(f"RESUMED | {args.world_id} | replayed {len(run.events) - 1} recorded action(s)")
    else:
        if not args.adapter or not args.registry:
            raise AssayError(
                "a fresh run needs --adapter FILE.py and --registry FILE.json",
                code="COMMAND_ARGS",
                hint="assay start WORLD_ID --adapter FILE.py --registry FILE.json",
            )
        registry = load_registry(Path(args.registry))
        config = {
            "world_id": args.world_id,
            "seed": args.seed,
            "adapter": resolve_adapter(args.adapter, Path.cwd()),
            "registry_hash": registry_hash(registry),
            "created_at": journal.now(),
            "version": __version__,
        }
        paths.state.mkdir(parents=True, exist_ok=True)
        journal.write_json(paths.config, config)
        journal.write_json(paths.registry, registry)
        try:
            daemon.spawn(paths)
        except AssayError:
            if not paths.events.exists():
                shutil.rmtree(paths.state, ignore_errors=True)  # a start that failed leaves no run behind
            raise
        if not paths.notes.exists():
            paths.notes.write_text(NOTES.format(world=args.world_id))
        print(f"STARTED | {args.world_id}")
    print(show.status(Run.load(paths)))
    print(USE)


def stop(paths: Paths, args: argparse.Namespace) -> None:
    Run.load(paths)
    print("STOPPED | `assay start WORLD_ID` resumes the run" if daemon.stop(paths) else "STOPPED | no daemon was running")


# --- the agent's commands --------------------------------------------------------


def status(paths: Paths, args: argparse.Namespace) -> None:
    print(show.status(Run.load(paths), count=args.history))


def view(paths: Paths, args: argparse.Namespace) -> None:
    flags = {"full": args.full, "frames": args.frames, "crop": args.crop}
    print(show.view(Run.load(paths), args.event, count=args.history, flags=flags))


def act(paths: Paths, args: argparse.Namespace) -> None:
    if args.params is not None and args.tokens:
        raise AssayError("pass the parameters as pname=value tokens or as --params, not both", code="COMMAND_ARGS")
    params = None if args.params is None else _json(args.params, "--params")
    if params is not None and not isinstance(params, dict):
        raise AssayError("--params takes one JSON object", code="COMMAND_ARGS")
    request = {"action": args.action, "tokens": args.tokens, "params": params, "predict": args.predict}
    _paid(paths, "act", {**request, "because": args.because})


def commit(paths: Paths, args: argparse.Namespace) -> None:
    steps: list[Any] = []
    for raw in args.step:
        text = raw.strip()
        if text.startswith(("@", "{", "[")):
            loaded = _json(text, "--step")
            steps += loaded if isinstance(loaded, list) else [loaded]
        else:
            action, _, predict = text.partition("::")
            tokens = action.split()
            steps.append({"action": tokens[0] if tokens else "", "tokens": tokens[1:], "predict": predict.strip()})
    if not all(isinstance(step, dict) and isinstance(step.get("action"), str) for step in steps):
        raise AssayError(
            "a step is \"NAME pname=value :: outcomes\" or the object {action, params, predict}",
            code="COMMAND_ARGS",
        )
    _paid(paths, "commit", {"steps": steps}, steps=len(steps))


def reset(paths: Paths, args: argparse.Namespace) -> None:
    _paid(paths, "reset", {"because": args.because})


def state_declare(paths: Paths, args: argparse.Namespace) -> None:
    print(daemon.call(paths, "declare", {"name": args.name, "path": args.path, "file": args.file}))


def state_list(paths: Paths, args: argparse.Namespace) -> None:
    run = Run.load(paths)
    print("\n".join(states.lines(run, run.last, fresh=args.read)))
    for name, spec in sorted(states.declared(paths).items()):
        print(f"  {name}: {spec['form']} {spec.get('path') or spec['source']}")


def python(paths: Paths, args: argparse.Namespace) -> None:
    if bool(args.source) == bool(args.file):
        raise AssayError("give exactly one of a source argument or --file", code="COMMAND_ARGS")
    analysis.run_python(Run.load(paths), args.source or Path(args.file).read_text())


def audit(paths: Paths, args: argparse.Namespace) -> None:
    Run.load(paths)
    print("\n".join(journal.audit(paths)[1]))


def diagnosis(paths: Paths, args: argparse.Namespace) -> None:
    print(diagnose.report(Run.load(paths)))


# --- the parser ------------------------------------------------------------------


def parser() -> Parser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-dir", default=argparse.SUPPRESS, metavar="DIR", help="the run directory (default: here)")
    top = Parser(prog="assay", description=(__doc__ or "").split("\n\n")[0], parents=[common])
    top.add_argument("--version", action="version", version=f"assay {__version__} ({journal.JOURNAL_SPEC})")
    commands = top.add_subparsers(dest="command", required=True, parser_class=Parser)

    def add(name: str, function: Any, summary: str, into: Any = commands, **options: Any) -> Parser:
        command: Parser = into.add_parser(name, help=summary, description=summary, parents=[common], **options)
        command.set_defaults(function=function)
        return command

    command = add("start", start, "operator: start a run here, or resume the one this directory holds")
    command.add_argument("world_id", help="a label for the run; an adapter may read it to pick an instance")
    command.add_argument("--adapter", metavar="FILE.py", help="the world's adapter: factory(root, config)")
    command.add_argument("--registry", metavar="FILE.json", help="the world's contract: actions, budget, goal")
    command.add_argument("--seed", type=int, default=0, help="passed to the adapter in its config")
    add("stop", stop, "operator: stop the run's daemon; `start` resumes it")

    command = add("status", status, "the whole picture: world, registry, budget, states, meters, notes")
    command.add_argument("--history", type=int, default=8, metavar="N", help="recent events to list")

    command = add("view", view, "one event of the record: cause, prediction, grades, what changed, observation")
    command.add_argument("--event", type=int, metavar="N", help="the event id (default: the last)")
    command.add_argument("--history", type=int, default=0, metavar="N", help="also list the N events up to it")
    command.add_argument("--full", action="store_true", help="the whole observation, or the whole grid")
    command.add_argument("--frames", action="store_true", help="frame worlds: every animation frame")
    command.add_argument("--crop", metavar="R0:R1,C0:C1", help="frame worlds: an exact half-open crop of the grid")

    command = add(
        "act", act, "take one action with a prediction; code grades it against what happens",
        epilog=grammar.HELP, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    command.add_argument("action")
    command.add_argument("tokens", nargs="*", metavar="pname=value")
    command.add_argument("--params", metavar="JSON", help="the parameters as one JSON object, or @FILE holding one")
    command.add_argument("--predict", help="what this action will do, in the grammar below")
    command.add_argument("--because", help="a short reason, kept on the event as its note")

    command = add("commit", commit, "run a batch, each step with its own prediction; halts at the first miss")
    command.add_argument(
        "--step", action="append", default=[], metavar='"NAME pname=value :: outcomes"',
        help="one step; repeat in order. Also a JSON object {action, params, predict}, a JSON list, or @FILE",
    )

    command = add("reset", reset, "pay one action to rewind the current progress unit")
    command.add_argument("--because", help="why this state is worth abandoning (not needed after GAME_OVER)")

    state = add("state", None, "addressable states: named readings of the observation")
    kinds = state.add_subparsers(dest="state_command", required=True, parser_class=Parser)
    command = add("declare", state_declare, "declare a state, once: a dotted path or an extractor program", into=kinds)
    command.add_argument("name")
    command.add_argument("--path", help="dotted keys into a dict observation, e.g. counters.red")
    command.add_argument("--file", help="an extractor file: def extract(obs) -> value, run in the sandbox")
    command = add("list", state_list, "every state with its current reading", into=kinds)
    command.add_argument("--read", action="store_true", help="run each extractor now instead of showing its last graded reading")

    command = add("python", python, "offline Python over the record: history, deltas and search preloaded")
    command.add_argument("source", nargs="?")
    command.add_argument("--file")

    add("audit", audit, "recompute the record's integrity: ids, chain, anchors, ungated events")
    add("diagnose", diagnosis, "read the record against the five layers of reasoning: where to steer")
    return top


def main() -> None:
    try:
        args = parser().parse_args()
        paths = Paths(Path(getattr(args, "run_dir", None) or os.getenv("ASSAY_RUN_DIR") or ".").resolve())
        args.function(paths, args)
    except AssayError as error:
        print(error.text(), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
