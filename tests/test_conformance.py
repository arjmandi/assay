"""The conformance audit (A8.4): the kernel imports nothing from a world and
names none, every adapter satisfies the session contract, the counter example
passes the README quickstart verbatim, and the new-world template passes the
whole loop including the owner operations."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import re
import shlex
import sys
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
WORLD_NAMES = re.compile(r"\b(factorio|fle|oolong|arc|arc_agi|arcengine|arcagi)\b", re.IGNORECASE)


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

    for name, path in ADAPTERS.items():
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
