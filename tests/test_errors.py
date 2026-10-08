"""The error model (docs/ARCHITECTURE.md section 7.1): every `AssayError` the
kernel and the frame extra raise carries a code from the catalogue, named as a
string literal or an `errors.NAME` attribute (an AST walk in the pattern of
the vocabulary test); the catalogue's kind wins for a catalogued code and
decides the exit status; the error object round-trips; `docs/ERRORS.md` is
the rendered catalogue; and the command line speaks the voice: `ERROR | CODE
| message`, `NEXT | hint`, the exit status by kind, the error object alone
under `--json`."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ASSAY_CLI, FAKE_ADAPTER, run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
ROOTS = (REPO / "src" / "assay", REPO / "src" / "assay_grid")


def _assay_error_calls(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name == "AssayError":
            yield node


def _code_of(node: ast.Call) -> str | None:
    """The code a raise names: a string literal, or `errors.NAME`; None when
    the keyword is absent or has another shape."""
    for keyword in node.keywords:
        if keyword.arg != "code":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "errors"
        ):
            return value.attr
        return None
    return None


def test_every_raise_in_the_kernel_and_the_extra_names_a_catalogued_code():
    from assay import errors

    allowed = set(errors.BY_CODE) - {errors.UNSPECIFIED}
    offenders: list[str] = []
    sites = 0
    for root in ROOTS:
        for path in sorted(root.glob("*.py")):
            for node in _assay_error_calls(path):
                sites += 1
                code = _code_of(node)
                if code is None:
                    offenders.append(f"{path.name}:{node.lineno}: no code= literal or errors.NAME")
                elif code not in allowed:
                    offenders.append(f"{path.name}:{node.lineno}: code {code!r} is not in the catalogue")
    assert not offenders, "\n".join(offenders)
    assert sites >= 200


def test_the_catalogue_is_well_formed_and_every_name_is_an_attribute():
    from assay import errors

    codes = [entry.code for entry in errors.CATALOGUE]
    assert len(codes) == len(set(codes))
    for entry in errors.CATALOGUE:
        assert entry.code.isupper() and entry.code.replace("_", "").isalnum(), entry.code
        assert entry.kind in errors.KINDS, entry.code
        assert entry.meaning.endswith(".") and len(entry.meaning) < 220, entry.code
        assert getattr(errors, entry.code) == entry.code
    assert errors.EXIT_CODES == {"usage": 2, "refused": 2, "world": 3, "internal": 4, "invalid": 5}
    for name in (
        "BUDGET_EXHAUSTED", "LOCAL_REPLAY_DIVERGED", "REMOTE_LEASE_EXPIRED", "REMOTE_STATE_DIVERGED",
        "REMOTE_SESSION_EXPIRED_OR_UNAVAILABLE", "MODULE_DEMAND", "CHAIN_DIVERGED", "TAMPER_DETECTED",
        "UNKNOWN_OPERATION", "PROTOCOL_VERSION", "CLI_USAGE", "WORLD_ERROR", "INTERNAL", "UNSPECIFIED",
    ):
        assert name in errors.BY_CODE, name


def test_the_catalogues_kind_wins_and_the_keyword_applies_to_unspecified_alone():
    from assay.core import AssayError
    from assay.errors import exit_code

    assert AssayError("x").code == "UNSPECIFIED" and AssayError("x").kind == "refused"
    assert AssayError("x", kind="world").kind == "world"
    assert AssayError("x", kind="nonsense").kind == "refused"
    budget = AssayError("x", code="BUDGET_EXHAUSTED", kind="internal")
    assert budget.kind == "refused" and budget.hint is None
    assert AssayError("x", code="CHAIN_DIVERGED").kind == "invalid"
    assert AssayError("x", code="WORLD_ERROR").kind == "world"
    assert AssayError("x", code="INTERNAL").kind == "internal"
    assert AssayError("x", code="CLI_USAGE").kind == "usage"
    # A code outside the table (an adapter's own) keeps the kind it asks for.
    assert AssayError("x", code="MY_WORLD", kind="world").kind == "world"
    assert str(AssayError("the message", code="EVENT_GUARD", hint="next")) == "the message"
    assert [exit_code(kind) for kind in ("usage", "refused", "world", "internal", "invalid")] == [2, 2, 3, 4, 5]
    assert isinstance(AssayError("x"), RuntimeError)


def test_the_error_object_round_trips_and_wraps_a_bug():
    from assay.core import AssayError

    error = AssayError("guard failed", code="EVENT_GUARD", hint="rerun status")
    assert error.to_json() == {
        "code": "EVENT_GUARD", "kind": "refused", "message": "guard failed", "hint": "rerun status",
    }
    back = AssayError.from_json(error.to_json())
    assert (back.code, back.kind, back.message, back.hint) == ("EVENT_GUARD", "refused", "guard failed", "rerun status")
    plain = AssayError.from_json("an old daemon's string")
    assert plain.code == "UNSPECIFIED" and plain.message == "an old daemon's string"
    malformed = AssayError.from_json({"nope": 1})
    assert malformed.code == "PROTOCOL_MALFORMED" and malformed.kind == "internal"
    bug = AssayError.wrap(KeyError("timestamp"), hint="see the log")
    assert bug.code == "INTERNAL" and bug.message == "KeyError: 'timestamp'" and bug.hint == "see the log"
    assert AssayError.wrap(error) is error


def test_docs_errors_md_is_the_rendered_catalogue():
    from assay import errors

    assert (REPO / "docs" / "ERRORS.md").read_text() == errors.render()
    completed = subprocess.run(
        [sys.executable, "-m", "assay.errors", "--render"],
        capture_output=True, text=True, cwd=str(REPO / "src"), timeout=60,
    )
    assert completed.returncode == 0 and completed.stdout == errors.render()


def test_an_aggregate_count_past_the_int_digit_limit_is_refused():
    from assay.core import AssayError
    from assay.predictions import parse_claims

    digits = "1" * 4301
    for over, horizon in ((digits, "1"), ("1", digits)):
        with pytest.raises(AssayError) as caught:
            parse_claims(f"noop; agg ch x mean >= 1 over {over}a horizon {horizon}a on-fail advise")
        assert caught.value.code == "CLAIM_SYNTAX" and "digits" in str(caught.value)
    assert parse_claims("noop; agg ch x mean >= 1 over 999999999a horizon 100a on-fail advise")[1].over == 999_999_999


ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]

# A world that fails inside its step, once with a bare exception and once with
# a refusal of its own: both cross the adapter boundary as WORLD_ERROR.
BOOM_ADAPTER = '''
from assay.core import AssayError


class _Session:
    public_info = {}

    @property
    def observation(self):
        return {
            "state": "NOT_FINISHED", "levels_completed": 0, "win_levels": 1,
            "available_actions": ["BOOM", "REFUSE", "NOOP"], "data": {"n": 0},
        }

    def step(self, action, data, reasoning):
        if action == "BOOM":
            raise ValueError("the world exploded")
        if action == "REFUSE":
            raise AssayError("the world refuses this one", hint="ask it nicely")
        return self.observation


def factory(root, config):
    return _Session()
'''
BOOM_ACTIONS = [{"name": "BOOM", "params": {}}, {"name": "REFUSE", "params": {}}, {"name": "NOOP", "params": {}}]


def _prepare(run: Path) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}}))


def _start(run: Path, adapter: str = f"{FAKE_ADAPTER}:factory"):
    return run_cli(run, "start", "fake1", "--adapter", adapter, "--registry", str(run / "reg.json"))


def test_the_command_line_speaks_the_error_voice(tmp_path):
    """One line `ERROR | CODE | message`, `NEXT | hint` when there is one,
    the claims table after them, the exit status by kind, nothing on
    stdout; under `--json` the error object alone on stdout."""
    run = tmp_path / "voice"
    _prepare(run)
    # usage, exit 2, before any run exists: the argparse path.
    parsed = run_cli(run, "act")
    assert parsed.returncode == 2 and parsed.stdout == ""
    assert parsed.stderr.startswith("ERROR | CLI_USAGE | ")
    assert "NEXT | `assay --help` lists the commands" in parsed.stderr
    missing = run_cli(run, "status")
    assert missing.returncode == 2
    assert missing.stderr == f"ERROR | RUN_MISSING | {run} is not initialized; run `assay start WORLD_ID`\n"
    try:
        assert _start(run).returncode == 0
        # refused, exit 2, with the hint on its own line.
        guard = run_cli(run, "act", "NOOP", "--predict", "noop", "--at", "7")
        assert guard.returncode == 2 and guard.stdout == ""
        assert guard.stderr == (
            "ERROR | EVENT_GUARD | event guard failed: requested 7, current 0\n"
            "NEXT | rerun `assay status` and pass the current event to --at\n"
        )
        # The claims table follows the ERROR and NEXT lines.
        bare = run_cli(run, "act", "NOOP")
        assert bare.returncode == 2
        lines = bare.stderr.splitlines()
        assert lines[0] == "ERROR | PREDICTION_REQUIRED | an empty prediction predicts nothing; say what you expect"
        assert lines[1].startswith("NEXT | add --predict")
        assert lines[2] == 'PREDICTION CLAIMS | separate several with ";"'
    finally:
        stop_run(run)
    # world, exit 3: whatever the step raised, a bare exception or the
    # world's own refusal, crosses the adapter boundary as WORLD_ERROR, and
    # nothing is journaled for it.
    world = tmp_path / "world"
    world.mkdir()
    (world / "reg.json").write_text(json.dumps({"actions": BOOM_ACTIONS}))
    (tmp_path / "boom_adapter.py").write_text(BOOM_ADAPTER)
    try:
        assert _start(world, f"{tmp_path / 'boom_adapter.py'}:factory").returncode == 0
        boom = run_cli(world, "act", "BOOM", "--predict", "change")
        assert boom.returncode == 3 and boom.stdout == ""
        assert boom.stderr == (
            "ERROR | WORLD_ERROR | ValueError: the world exploded\n"
            "NEXT | the world refused or failed this action; no event was journaled for it\n"
        )
        refused = run_cli(world, "act", "REFUSE", "--predict", "change")
        assert refused.returncode == 3
        assert refused.stderr == "ERROR | WORLD_ERROR | the world refuses this one\nNEXT | ask it nicely\n"
        events = [json.loads(line) for line in (world / ".assay" / "events.jsonl").read_text().splitlines()]
        assert len(events) == 1
        assert not (world / ".assay" / "mutations.jsonl").exists()
        # --json: the error object alone, on stdout, the same exit status.
        machine = run_cli(world, "act", "BOOM", "--predict", "change", "--json")
        assert machine.returncode == 3 and machine.stderr == ""
        assert json.loads(machine.stdout) == {
            "code": "WORLD_ERROR",
            "kind": "world",
            "message": "ValueError: the world exploded",
            "hint": "the world refused or failed this action; no event was journaled for it",
        }
        usage = run_cli(world, "act", "--json")
        assert usage.returncode == 2 and usage.stderr == ""
        assert json.loads(usage.stdout)["code"] == "CLI_USAGE"
        bare = run_cli(world, "act", "NOOP", "--json")
        assert bare.returncode == 2 and bare.stderr == ""
        error = json.loads(bare.stdout)
        assert error["code"] == "PREDICTION_REQUIRED" and error["kind"] == "refused"
        assert error["message"].startswith("an empty prediction predicts nothing; say what you expect\n")
        assert run_cli(world, "act", "NOOP", "--predict", "noop").returncode == 0
    finally:
        stop_run(world)
    # internal, exit 4, as the error object too.
    crash = tmp_path / "crash"
    _prepare(crash)
    try:
        assert _start(crash).returncode == 0
        with (crash / ".assay" / "proposals.jsonl").open("a") as handle:
            handle.write('{"kind": "goal_proposed"}\n')
        broken = run_cli(crash, "status", "--json")
        assert broken.returncode == 4 and broken.stderr == ""
        error = json.loads(broken.stdout)
        assert error["code"] == "INTERNAL" and error["kind"] == "internal"
        assert error["message"].startswith("KeyError") and error["hint"].startswith("traceback in ")
    finally:
        stop_run(crash)


def test_the_activity_record_of_a_failed_command_carries_the_code(tmp_path):
    run = tmp_path / "activity"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "act", "NOOP", "--predict", "noop", "--at", "7").returncode == 2
        records = [
            json.loads(line) for line in (run / ".assay" / "activity.jsonl").read_text().splitlines()
        ]
        ended = [record for record in records if record["kind"] == "command_end" and record["status"] == "ERROR"]
        assert ended[-1]["code"] == "EVENT_GUARD" and ended[-1]["error_kind"] == "refused"
        assert ended[-1]["error"] == "event guard failed: requested 7, current 0"
    finally:
        stop_run(run)


def test_the_launcher_module_is_the_command_line(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(ASSAY_CLI), "--run-dir", str(tmp_path), "nope"],
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 2 and completed.stderr.startswith("ERROR | CLI_USAGE | ")
