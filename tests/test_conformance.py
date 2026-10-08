"""The conformance audit (A8.4): the kernel imports nothing from a world and
names none, every adapter satisfies the session contract, the counter example
passes the README quickstart verbatim, the new-world template passes the
whole loop including the owner operations, and the kernel's session semantics
come from the adapter's declaration alone (docs/ARCHITECTURE.md section 2.2):
a world that declares nothing gets local semantics and nothing world-shaped,
a declared lease runs the timer and refuses the resume after it, a declared
fresh-unit reset no-op skips the world, a world without replay lives one
daemon long, and the replay hook receives the recorded transitions."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import re
import shlex
import signal
import sys
import time
from pathlib import Path

from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
KERNEL = REPO / "src" / "assay"
ADAPTERS = {
    "arcagi": REPO / "bench" / "arcagi" / "adapter.py",
    "factorio": REPO / "bench" / "factorio" / "adapter.py",
    "oolong": REPO / "bench" / "oolong" / "adapter.py",
    "counter": REPO / "examples" / "counter_world.py",
    "template": REPO / "examples" / "new_world" / "adapter.py",
}
# The test adapters, held to the same contract.
FAKES = {
    "fake": REPO / "tests" / "fake_adapter.py",
    "capability": REPO / "tests" / "capability_adapter.py",
}
# The worlds' names and their words: a benchmark's session, its scoring, its
# cache. None of them may appear in the kernel.
WORLD_NAMES = re.compile(
    r"\b(factorio|fle|oolong|arc|arc_agi|arcengine|arcagi|competition|scorecard|arcade)\b",
    re.IGNORECASE,
)
# The one line the rule allows, by name: the mode value config.json carried
# for a remote run before 1.2.0, kept so such a run still reads as remote.
ALLOWED_LINES = {("core.py", 'LEGACY_REMOTE_MODE = "competition"')}


def test_kernel_imports_nothing_from_a_world_or_the_extra_at_module_level():
    offenders = []
    for path in sorted(KERNEL.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:  # module level only; lazy imports inside functions are the hook
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in {"bench", "assay_grid", "PIL"}:
                    offenders.append(f"{path.name}: {name}")
    assert not offenders, offenders


def test_kernel_names_no_world():
    offenders = []
    for path in sorted(KERNEL.glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if (path.name, line.strip()) in ALLOWED_LINES:
                continue
            if WORLD_NAMES.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"conformance_{path.stem}_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_adapter_exposes_the_factory_contract():
    from assay.adapters import contract_problems

    for name, path in {**ADAPTERS, **FAKES}.items():
        module = _load(path)  # heavy world clients are imported inside the sessions, never at module level
        # The Adapter and Session protocols of assay.adapters, checked at
        # runtime by attribute, since a Protocol with a property is not
        # runtime-checkable; the inline checks below are the same contract.
        assert contract_problems(module) == [], name
        factory = getattr(module, "factory", None)
        assert callable(factory), name
        parameters = list(inspect.signature(factory).parameters)
        assert parameters == ["root", "config"], (name, parameters)
        # The session class the factory returns has the three required members.
        returns = inspect.signature(factory).return_annotation
        session_class = getattr(module, str(returns).split(".")[-1], None) or getattr(module, returns, None)
        if isinstance(session_class, type):
            assert isinstance(getattr(session_class, "observation", None), property), name
            assert callable(getattr(session_class, "step", None)), name
            step = list(inspect.signature(session_class.step).parameters)
            assert step == ["self", "action", "data", "reasoning"], (name, step)
            # The optional `session` is a property returning the capability
            # record, the optional `replay` takes the recorded transitions.
            declared = getattr(session_class, "session", None)
            if declared is not None:
                assert isinstance(declared, property), name
                shape = inspect.signature(declared.fget).return_annotation
                assert str(shape).split(".")[-1] == "SessionCapability", (name, shape)
            replay = getattr(session_class, "replay", None)
            if replay is not None:
                assert list(inspect.signature(replay).parameters) == ["self", "transitions"], name
            # The two reach-ins of 1.1.0 are declarations now: the remote
            # session's rules, and the tick ledger taken from the hook.
            if name == "arcagi":
                assert isinstance(declared, property), name
            if name == "factorio":
                assert callable(replay), name


def test_the_capability_record_validates_its_fields():
    import pytest

    from assay.adapters import LOCAL_SEMANTICS, SessionCapability, session_capability

    assert SessionCapability() == LOCAL_SEMANTICS
    assert LOCAL_SEMANTICS.to_json() == {
        "idle_lease_seconds": None, "reset_on_fresh_unit": "world", "replayable": True,
    }
    assert SessionCapability.from_json({"idle_lease_seconds": 900, "replayable": False}) == SessionCapability(
        idle_lease_seconds=900, replayable=False
    )
    for bad in (
        {"idle_lease_seconds": 0}, {"idle_lease_seconds": True}, {"idle_lease_seconds": "15m"},
        {"reset_on_fresh_unit": "skip"}, {"replayable": "no"}, {"lease": 3},
        {"idle_lease_seconds": 5}, {"idle_lease_seconds": 5, "replayable": True},
    ):
        with pytest.raises((TypeError, ValueError)):
            SessionCapability.from_json(bad)
    # A lease is declared by a world without replay: the kernel's one rule
    # for a lease is the refusal at resume, which contradicts replay.
    with pytest.raises(ValueError, match="cannot be rebuilt by replay; declare one or the other"):
        SessionCapability(idle_lease_seconds=5)

    class Declares:
        session = {"reset_on_fresh_unit": "noop"}

    class Misdeclares:
        session = "noop"

    assert session_capability(object()) is LOCAL_SEMANTICS
    assert session_capability(Declares()) == SessionCapability(reset_on_fresh_unit="noop")
    with pytest.raises(Exception) as caught:
        session_capability(Misdeclares())
    assert caught.value.code == "WORLD_ERROR"


def test_the_test_adapters_declaring_classes_fit_the_contract():
    """The factory's annotation names PlainSession, so the table check sees
    that class alone; the classes that declare, the one with the hook and
    the drifting one are held to the same shapes by name."""
    from assay.adapters import session_problems

    module = _load(FAKES["capability"])
    classes = (
        module.DeclaringSession, module.EnvDeclaredSession, module.HookedSession, module.DriftingSession,
    )
    for session_class in classes:
        assert session_problems(session_class) == [], session_class.__name__
    for session_class in (module.DeclaringSession, module.EnvDeclaredSession):
        declared = session_class.__dict__["session"]
        assert isinstance(declared, property), session_class.__name__
        shape = inspect.signature(declared.fget).return_annotation
        assert str(shape).split(".")[-1] == "SessionCapability", (session_class.__name__, shape)
    assert list(inspect.signature(module.HookedSession.replay).parameters) == ["self", "transitions"]
    assert isinstance(module.DriftingSession.__dict__["observation"], property)


def test_the_lease_duration_reads_singular_at_one():
    from assay.cli import _lease_duration

    assert [_lease_duration(n) for n in (1, 4, 60, 120, 900)] == [
        "1 second", "4 seconds", "1 minute", "2 minutes", "15 minutes",
    ]


# --- the semantics, through the real CLI and daemon --------------------------------

CAPABILITY_ADAPTER = FAKES["capability"]
CAPABILITY_REGISTRY = {
    "actions": [
        {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
        {"name": "NOOP", "params": {}},
    ],
    "budget": {"actions": 30},
}


def _prepare(tmp_path: Path, world: str) -> Path:
    run = tmp_path / world
    run.mkdir()
    (run / "reg.json").write_text(json.dumps(CAPABILITY_REGISTRY))
    return run


def _start(run: Path, world: str, *extra: str):
    return run_cli(
        run, "start", world, "--adapter", f"{CAPABILITY_ADAPTER}:factory",
        "--registry", str(run / "reg.json"), *extra,
    )


def _last_data(run: Path) -> dict:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return json.loads([line for line in lines if line.strip()][-1])["observation"]


def _daemon_gone(run: Path, seconds: float = 5.0) -> bool:
    """Whether no daemon identified as the run's is alive, waiting a moment
    for one that is on its way out."""
    from assay.broker import find_daemon
    from assay.core import RunPaths

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if find_daemon(RunPaths(run)) is None:
            return True
        time.sleep(0.05)
    return False


def _kill_daemon(run: Path) -> None:
    pid = json.loads((run / ".assay" / "broker.json").read_text())["pid"]
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"daemon {pid} did not exit")


def test_a_world_that_declares_nothing_gets_local_semantics(tmp_path):
    """No lease on any line, a reset on the fresh unit reaches the world,
    the journal replays through a fresh session on resume."""
    run = _prepare(tmp_path, "plain")
    try:
        started = _start(run, "plain")
        assert started.returncode == 0, started.stderr
        assert "STARTED | plain | local simulator | no action-idle lease | replay recovery enabled" in started.stdout
        assert "MODE | LOCAL SIMULATOR | no action-idle lease | exact replay recovery enabled" in started.stdout
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["mode"] == "local"
        assert config["session"] == {"idle_lease_seconds": None, "reset_on_fresh_unit": "world", "replayable": True}
        rewound = run_cli(run, "reset", "--because", "an opening reset")
        assert rewound.returncode == 0 and "OUTCOME | RESET" in rewound.stdout, rewound.stderr
        assert _last_data(run) == {"counter": 0, "resets": 1}
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        _kill_daemon(run)
        resumed = _start(run, "plain")
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED | plain | local simulator | replayed 2 paid actions" in resumed.stdout
        assert _last_data(run) == {"counter": 1, "resets": 1}
        assert "MODE | LOCAL SIMULATOR | no action-idle lease | exact replay recovery enabled" in resumed.stdout
    finally:
        stop_run(run)


def test_remote_mode_on_a_world_that_declares_nothing_means_no_lease(tmp_path):
    """The mode is the operator's word to the adapter; the rules are the
    declaration's, so a mismatch is harmless."""
    run = _prepare(tmp_path, "plain")
    try:
        started = _start(run, "plain", "--mode", "remote")
        assert started.returncode == 0, started.stderr
        assert "STARTED | plain | REMOTE | no action-idle lease | replay recovery enabled" in started.stdout
        assert "MODE | REMOTE | no action-idle lease | exact replay recovery enabled" in started.stdout
        assert json.loads((run / ".assay" / "config.json").read_text())["mode"] == "remote"
        assert run_cli(run, "act", "INC", "amount=2", "--predict", "change").returncode == 0
        _kill_daemon(run)
        resumed = _start(run, "plain")
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED | plain | REMOTE | replayed 1 paid actions" in resumed.stdout
        refused = _start(run, "plain", "--mode", "local")
        assert refused.returncode == 2 and "already owns a remote run" in refused.stderr
    finally:
        stop_run(run)


def test_a_declared_fresh_unit_reset_noop_never_reaches_the_world(tmp_path):
    run = _prepare(tmp_path, "noop")
    try:
        assert _start(run, "noop").returncode == 0
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["session"]["reset_on_fresh_unit"] == "noop"
        # The opening reset: journaled and paid, answered by the kernel.
        rewound = run_cli(run, "reset", "--because", "an opening reset")
        assert rewound.returncode == 0 and "OUTCOME | RESET" in rewound.stdout, rewound.stderr
        assert _last_data(run) == {"counter": 0, "resets": 0}
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        # Not fresh any more: the world sees it.
        again = run_cli(run, "reset", "--because", "the unit is not fresh")
        assert again.returncode == 0 and _last_data(run) == {"counter": 0, "resets": 1}
        # Right after a reset the unit is fresh again: the kernel answers.
        third = run_cli(run, "reset", "--because", "fresh again")
        assert third.returncode == 0 and _last_data(run) == {"counter": 0, "resets": 1}
        # The replay applies the same rule, so the journal reproduces.
        _kill_daemon(run)
        resumed = _start(run, "noop")
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED | noop | local simulator | replayed 4 paid actions" in resumed.stdout
        assert _last_data(run) == {"counter": 0, "resets": 1}
    finally:
        stop_run(run)


def test_a_declared_lease_runs_the_timer_and_refuses_the_resume_after_it(tmp_path):
    from capability_adapter import LEASE_SECONDS

    run = _prepare(tmp_path, "lease")
    try:
        started = _start(run, "lease", "--mode", "remote")
        assert started.returncode == 0, started.stderr
        assert (
            f"STARTED | lease | REMOTE | single run | ~{LEASE_SECONDS}s action-idle lease | no replay recovery"
            in started.stdout
        )
        remaining = re.compile(
            r"^MODE \| REMOTE \| about [1-4]s action-idle remaining \| exact replay recovery unavailable$", re.M
        )
        assert remaining.search(started.stdout), started.stdout
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["session"] == {"idle_lease_seconds": LEASE_SECONDS, "reset_on_fresh_unit": "world", "replayable": False}
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        # Within the lease: the live daemon is kept and the time left is said.
        resumed = _start(run, "lease")
        assert resumed.returncode == 0, resumed.stderr
        assert re.search(r"^RESUMED \| lease \| REMOTE \| about [1-4]s action-idle remaining$", resumed.stdout, re.M)
        time.sleep(LEASE_SECONDS + 0.5)
        status = run_cli(run, "status")
        assert "MODE | REMOTE | expired/unavailable | exact replay recovery unavailable" in status.stdout
        expired = _start(run, "lease")
        assert expired.returncode == 5, expired.stdout
        assert (
            f"ERROR | REMOTE_LEASE_EXPIRED | no live action was recorded for at least {LEASE_SECONDS} seconds; "
            "the world's action-idle lease has run out and the run is not recoverable"
        ) in expired.stderr
        assert "NEXT | preserve this directory and use a fresh one for another run" in expired.stderr
    finally:
        stop_run(run)


def test_a_world_without_replay_lives_one_daemon_long(tmp_path):
    run = _prepare(tmp_path, "single")
    try:
        started = _start(run, "single", "--mode", "remote")
        assert started.returncode == 0, started.stderr
        assert "STARTED | single | REMOTE | single run | no action-idle lease | no replay recovery" in started.stdout
        assert "MODE | REMOTE | no action-idle lease | exact replay recovery unavailable" in started.stdout
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        resumed = _start(run, "single")
        assert resumed.returncode == 0, resumed.stderr
        assert "RESUMED | single | REMOTE | no action-idle lease" in resumed.stdout
        assert run_cli(run, "stop").returncode == 0
        gone = _start(run, "single")
        assert gone.returncode == 5, gone.stdout
        assert (
            "ERROR | REMOTE_SESSION_UNAVAILABLE | the run's environment owner is gone and the "
            "world declared no replay, so the run cannot be reconstructed"
        ) in gone.stderr
        refused = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert refused.returncode == 2
        assert "NEXT | the world declared no replay, so the run cannot be reconstructed" in refused.stderr
    finally:
        stop_run(run)


def test_a_daemon_refuses_to_serve_under_a_changed_declaration(tmp_path, monkeypatch):
    """The command line routes a resume by the record in config.json; a
    daemon applying another live declaration would replay nothing where a
    replay is expected. The daemon holds the live declaration against the
    record before READY and refuses, naming the fields."""
    from capability_adapter import DECLARATION_VARIABLE

    monkeypatch.delenv(DECLARATION_VARIABLE, raising=False)
    run = _prepare(tmp_path, "env")
    try:
        started = _start(run, "env")
        assert started.returncode == 0, started.stderr
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["session"] == {"idle_lease_seconds": None, "reset_on_fresh_unit": "world", "replayable": True}
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        _kill_daemon(run)
        monkeypatch.setenv(DECLARATION_VARIABLE, json.dumps({"replayable": False}))
        refused = _start(run, "env")
        assert refused.returncode == 2, refused.stdout
        assert (
            "ERROR | DECLARATION_CHANGED | the adapter's session declaration changed since the "
            "run started: replayable recorded true, declared false"
        ) in refused.stderr
        assert (
            "NEXT | the run continues only under the declaration recorded in config.json; "
            "restore the adapter's, or start another run in a fresh directory"
        ) in refused.stderr
        assert json.loads((run / ".assay" / "broker.json").read_text())["status"] == "ERROR"
        assert _daemon_gone(run)
        monkeypatch.setenv(DECLARATION_VARIABLE, json.dumps({"reset_on_fresh_unit": "noop"}))
        refused = _start(run, "env")
        assert refused.returncode == 2
        assert 'reset_on_fresh_unit recorded "world", declared "noop"' in refused.stderr
        assert _daemon_gone(run)
        # Under the recorded declaration again, the resume replays.
        monkeypatch.delenv(DECLARATION_VARIABLE)
        resumed = _start(run, "env")
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED | env | local simulator | replayed 1 paid actions" in resumed.stdout
        assert _last_data(run) == {"counter": 1, "resets": 0}
    finally:
        stop_run(run)


def test_a_resume_stops_the_daemon_it_started_when_the_world_drifts_from_the_journal(tmp_path):
    """A replay that reproduces every recorded step, then a live observation
    that differs from the last event: the resume refuses with
    LOCAL_REPLAY_DIVERGED and stops the daemon it started, so nothing spends
    on a world the journal does not describe."""
    run = _prepare(tmp_path, "drift")
    try:
        started = _start(run, "drift")
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        assert _last_data(run) == {"counter": 1, "resets": 0}
        _kill_daemon(run)
        resumed = _start(run, "drift")
        assert resumed.returncode == 5, resumed.stdout
        assert (
            "ERROR | LOCAL_REPLAY_DIVERGED | reconstructed simulator state differs from the "
            "latest timeline event"
        ) in resumed.stderr
        assert _daemon_gone(run)
        assert json.loads((run / ".assay" / "broker.json").read_text())["status"] == "STOPPED"
        refused = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert refused.returncode == 2 and "ERROR | DAEMON_UNAVAILABLE |" in refused.stderr
    finally:
        stop_run(run)


def test_the_replay_hook_receives_the_recorded_transitions(tmp_path):
    run = _prepare(tmp_path, "hook")
    try:
        assert _start(run, "hook").returncode == 0
        assert not (run / "replayed.json").exists()  # never on a fresh run
        assert run_cli(run, "act", "INC", "amount=2", "--predict", "change").returncode == 0
        assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        _kill_daemon(run)
        resumed = _start(run, "hook")
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED | hook | local simulator | replayed 2 paid actions" in resumed.stdout
        lines = (run / ".assay" / "mutations.jsonl").read_text().splitlines()
        mutations = [json.loads(line) for line in lines if line.strip()]
        received = json.loads((run / "replayed.json").read_text())
        assert received == [
            {"action": item["action"], "params": item["data"], "observation": item["observation"]}
            for item in mutations
        ]
        assert [item["action"] for item in received] == ["INC", "NOOP"]
        assert received[0]["params"] == {"amount": 2}
        assert received[0]["observation"]["data"] == {"counter": 2, "resets": 0}
    finally:
        stop_run(run)


def test_counter_quickstart_verbatim_from_the_readme(tmp_path):
    """The README quickstart, command by command."""
    readme = (REPO / "README.md").read_text()
    quickstart = readme[readme.index("## Quickstart"):]
    block = quickstart[quickstart.index("```bash") + 7 : quickstart.index("```", quickstart.index("```bash") + 7)]
    commands = [line.strip() for line in block.splitlines() if line.strip().startswith('"$ASSAY"')]
    assert len(commands) == 6, block  # start, act, act, channel declare, act, audit
    run = tmp_path / "demo"
    run.mkdir()

    def argv(command: str) -> list[str]:
        text = command.replace("<repo>", str(REPO)).replace('"$ASSAY"', "").replace("\\", "")
        text = text.split("#")[0]
        return shlex.split(text)

    try:
        # The start command spans three lines in the README; rebuild it.
        start_block = block[block.index('"$ASSAY" start') : block.index('"$ASSAY" act')]
        started = run_cli(run, *argv(start_block))
        assert started.returncode == 0, started.stderr
        assert "STARTED | counterdemo" in started.stdout
        acted = run_cli(run, *argv(commands[1]))
        assert acted.returncode == 0 and "OUTCOME | PREDICTED" in acted.stdout
        missed = run_cli(run, *argv(commands[2]))
        assert missed.returncode == 0 and "OUTCOME | SURPRISE" in missed.stdout
        declared = run_cli(run, *argv(commands[3]))
        assert declared.returncode == 0 and "CHANNEL | declared counter" in declared.stdout
        won = run_cli(run, *argv(commands[4]))
        assert won.returncode == 0 and "OUTCOME | GAME_COMPLETE" in won.stdout
        audited = run_cli(run, *argv(commands[5]))
        assert audited.returncode == 0 and "AUDIT | CLEAN" in audited.stdout
    finally:
        stop_run(run)


def test_new_world_template_end_to_end(tmp_path):
    adapter = ADAPTERS["template"]
    module = _load(adapter)
    registry = REPO / "examples" / "new_world" / "registry.json"
    run = tmp_path / "vault"
    run.mkdir()
    token_file = tmp_path / "owner.token"
    code1 = module.room_code(0, 1)
    code2 = module.room_code(0, 2)
    try:
        started = run_cli(
            run, "start", "vault1", "--adapter", f"{adapter}:factory",
            "--registry", str(registry), "--owner-token-file", str(token_file),
        )
        assert started.returncode == 0, started.stderr
        assert "REGISTRY | 7 registered actions" in started.stdout
        assert "AGENDA | goal (registry): open both doors and enter the second room" in started.stdout
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["public_info"]["rooms"] == 2
        token = token_file.read_text().strip()
        # Channels, claims and a refusal through the observation.
        assert run_cli(run, "channel", "declare", "dial", "--path", "dial").returncode == 0
        assert run_cli(run, "channel", "declare", "door", "--path", "door").returncode == 0
        assert run_cli(run, "channel", "declare", "refusals", "--path", "refusals").returncode == 0
        turned = run_cli(run, "act", "TURN", "delta=1", "--predict", "ch dial delta = 1")
        assert turned.returncode == 0 and "OUTCOME | PREDICTED" in turned.stdout
        assert "CHANNELS | dial: 0 -> 1" in turned.stdout
        refused = run_cli(run, "act", "OPEN", "--predict", "ch refusals delta = 1; ch door = locked")
        assert refused.returncode == 0 and "OUTCOME | PREDICTED" in refused.stdout, refused.stdout
        # Dial to the code with a batch (under the hand cap), open, enter: room 1.
        delta = (code1 - 1) % 10
        steps = []
        while delta > 0:
            step = min(3, delta)
            steps.append(f"TURN delta={step} :: ch dial delta = {step}")
            delta -= step
        for chunk in (steps[i:i + 3] for i in range(0, len(steps), 3)):
            batched = run_cli(run, "commit", *sum((["--step", s] for s in chunk), []))
            assert batched.returncode == 0, batched.stderr
        opened = run_cli(run, "act", "OPEN", "--predict", "ch door = open")
        assert opened.returncode == 0 and "OUTCOME | PREDICTED" in opened.stdout, opened.stdout
        entered = run_cli(run, "act", "ENTER", "--predict", "level+1")
        assert entered.returncode == 0 and "OUTCOME | LEVEL_COMPLETE" in entered.stdout
        assert "unit 1 complete" not in entered.stdout and "level 1 complete" in entered.stdout
        # Owner operations: approval for DRILL, waiver for SIREN, the destructive gate.
        denied = run_cli(run, "act", "DRILL", "--predict", "ch door = open")
        assert denied.returncode == 2 and "approval-gated" in denied.stderr
        assert run_cli(run, "approve", "DRILL", "--token", token).returncode == 0
        drilled = run_cli(run, "act", "DRILL", "--predict", "ch door = open")
        assert drilled.returncode == 0 and "OUTCOME | PREDICTED" in drilled.stdout
        quota = run_cli(run, "act", "SIREN", "volume=0.5", "--predict", "noop")
        assert quota.returncode == 2 and "rehearsal quota" in quota.stderr
        assert run_cli(run, "waive", "SIREN", "--token", token, "--because", "template demo").returncode == 0
        assert run_cli(run, "act", "SIREN", "volume=0.5", "--predict", "noop").returncode == 0
        armed = run_cli(run, "act", "ALARM", "--predict", "change")
        assert armed.returncode == 2 and "worst case and recovery" in armed.stderr
        alarmed = run_cli(run, "act", "ALARM", "--predict", "change",
                          "--declare", "worst_case=this room ends", "--declare", "recovery=reset")
        assert alarmed.returncode == 0 and "OUTCOME | GAME_OVER" in alarmed.stdout
        rewound = run_cli(run, "reset")
        assert rewound.returncode == 0 and "OUTCOME | RESET" in rewound.stdout
        # Room 2 to the WIN, then finalize's file and a clean audit.
        delta = code2 % 10
        while delta > 0:
            step = min(3, delta)
            assert run_cli(run, "act", "TURN", f"delta={step}", "--predict", "change").returncode == 0
            delta -= step
        assert run_cli(run, "act", "OPEN", "--predict", "ch door = open").returncode == 0
        won = run_cli(run, "act", "ENTER", "--predict", "win; level+1")
        assert won.returncode == 0 and "OUTCOME | GAME_COMPLETE" in won.stdout
        summary = json.loads((run / ".assay" / "new_world_summary.json").read_text())
        assert summary["rooms"] == 2 and summary["refusals"] == 1
        status = run_cli(run, "status")
        assert "STATUS | vault1 |" in status.stdout and "level 2/2" in status.stdout and "WIN" in status.stdout
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout and "chain intact" in audited.stdout
        resumed = run_cli(run, "start", "vault1", "--adapter", f"{adapter}:factory", "--registry", str(registry))
        assert resumed.returncode == 0 and "completed run" in resumed.stdout
    finally:
        stop_run(run)
