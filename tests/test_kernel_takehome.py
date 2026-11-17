"""The kernel-takehome pack (worlds/recipes/kernel-takehome) over a fake take-home
and a fake evaluator that keep the real command lines and JSON shapes: a
kernel's score is the number of instruction bundles it emits. The pack is
copied, prepared from the fakes with no network, and driven through the real
CLI and daemon, so nothing here touches the upstream repositories."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
PACK_SOURCE = REPO / "worlds" / "recipes" / "kernel-takehome"
BASELINE = 100

KERNEL = """class KernelBuilder:
    def __init__(self):
        self.instrs = []
        self.scratch_ptr = 0

    def build_kernel(self, forest_height, n_nodes, batch_size, rounds):
        self.instrs = [{{"alu": []}} for _ in range({bundles})]
"""
BROKEN = "raise RuntimeError('the kernel does not build')\n"
WRONG = KERNEL.format(bundles=50).replace('{"alu": []}', '{"alu": [], "marker": ["bad"]}')
SLOW = "import time\ntime.sleep(60)\n" + KERNEL.format(bundles=10)

PROBLEM = "N_ROWS = 4\n"
FROZEN = "N_CORES = 1\n"
README = "A fake take-home.\n"
SUBMISSION = '''import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from perf_takehome import KernelBuilder


def cycles():
    kb = KernelBuilder()
    kb.build_kernel(10, 1, 256, 16)
    return len(kb.instrs)


class Thresholds(unittest.TestCase):
    def test_kernel_correctness(self):
        assert cycles() > 0

    def test_under_eighty(self):
        assert cycles() < 80


if __name__ == "__main__":
    unittest.main()
'''
EVAL_BUILD = '''import importlib.util
import json
import sys

program, takehome, out = sys.argv[1:4]
sys.path.insert(0, takehome)
spec = importlib.util.spec_from_file_location("evolved_kernel", program)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
builder = module.KernelBuilder()
builder.build_kernel(10, 1, 256, 16)
with open(out, "w") as handle:
    json.dump({"instrs": builder.instrs, "scratch_words": builder.scratch_ptr}, handle)
'''
EVAL_SCORE = '''import json
import sys

instrs_path, takehome, trials, out = sys.argv[1:5]
with open(instrs_path) as handle:
    instrs = json.load(handle)["instrs"]
result = {"ok": False, "cycles": None, "trials": []}
if any("marker" in bundle for bundle in instrs):
    result["trials"].append({"correct": False, "cycles": len(instrs)})
    result["error"] = "Incorrect output values on trial 0"
else:
    result["ok"] = True
    result["cycles"] = len(instrs)
    result["trials"] = [{"correct": True, "cycles": len(instrs)} for _ in range(int(trials))]
with open(out, "w") as handle:
    json.dump(result, handle)
'''


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _upstreams(tmp_path: Path) -> tuple[Path, Path]:
    takehome = tmp_path / "fake-takehome"
    _write(takehome / "perf_takehome.py", KERNEL.format(bundles=BASELINE))
    _write(takehome / "problem.py", PROBLEM)
    _write(takehome / "Readme.md", README)
    _write(takehome / "tests" / "frozen_problem.py", FROZEN)
    _write(takehome / "tests" / "submission_tests.py", SUBMISSION)
    wrapper = tmp_path / "fake-wrapper"
    evaluator = wrapper / "integrations" / "openevolve-takehome"
    _write(evaluator / "eval_build.py", EVAL_BUILD)
    _write(evaluator / "eval_score.py", EVAL_SCORE)
    _write(evaluator / "vendor" / "takehome" / "problem.py", PROBLEM)
    _write(evaluator / "vendor" / "takehome" / "frozen_problem.py", FROZEN)
    return takehome, wrapper


def _prepare(pack: Path, takehome: Path, wrapper: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(pack / "prepare.py"), "--takehome", str(takehome), "--wrapper", str(wrapper), *extra],
        capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    target = tmp_path / "pack"
    shutil.copytree(PACK_SOURCE, target, ignore=shutil.ignore_patterns("__pycache__", "upstream", "task"))
    constants = json.loads((target / "kernel.json").read_text())
    constants["baseline_cycles"] = BASELINE
    (target / "kernel.json").write_text(json.dumps(constants))
    takehome, wrapper = _upstreams(tmp_path)
    done = _prepare(target, takehome, wrapper, "--allow-unpinned")
    assert done.returncode == 0, done.stderr
    return target


def _tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _last_data(run: Path) -> dict:
    lines = [line for line in (run / ".assay" / "events.jsonl").read_text().splitlines() if line.strip()]
    return json.loads(lines[-1])["observation"]


def _frontier(run: Path) -> list[dict]:
    path = run / ".assay" / "world_state" / "frontier.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_prepare_builds_the_task_tree_and_converges(pack, tmp_path):
    assert sorted(path.name for path in (pack / "task").iterdir()) == ["Readme.md", "perf_takehome.py", "problem.py", "tests"]
    manifest = json.loads((pack / "upstream" / "manifest.json").read_text())
    assert sorted(manifest["task"]) == [
        "Readme.md", "perf_takehome.py", "problem.py", "tests/frozen_problem.py", "tests/submission_tests.py",
    ]
    before = _tree(pack / "task"), (pack / "upstream" / "manifest.json").read_bytes()
    takehome, wrapper = tmp_path / "fake-takehome", tmp_path / "fake-wrapper"
    assert _prepare(pack, takehome, wrapper, "--allow-unpinned").returncode == 0
    assert (_tree(pack / "task"), (pack / "upstream" / "manifest.json").read_bytes()) == before


def test_prepare_refuses_an_unpinned_tree_and_a_differing_evaluator(pack, tmp_path):
    takehome, wrapper = tmp_path / "fake-takehome", tmp_path / "fake-wrapper"
    unpinned = _prepare(pack, takehome, wrapper)
    assert unpinned.returncode == 2 and unpinned.stderr.startswith("ERROR | "), unpinned.stderr
    assert "pinned" in unpinned.stderr
    vendored = wrapper / "integrations" / "openevolve-takehome" / "vendor" / "takehome" / "frozen_problem.py"
    vendored.write_text("N_CORES = 8\n")
    differing = _prepare(pack, takehome, wrapper, "--allow-unpinned")
    assert differing.returncode == 2 and "differs" in differing.stderr, differing.stderr


def test_evaluations_through_the_cli_and_the_frontier_record(pack, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    program = run / "work" / "perf_takehome.py"
    try:
        started = run_cli(run, "start", "kernel1", "--adapter", f"{pack / 'adapter.py'}:factory",
                          "--registry", str(pack / "registry.json"), "--owner-token-file", str(tmp_path / "kernel.token"))
        assert started.returncode == 0, started.stderr
        assert "no replay recovery" in started.stdout
        first = _last_data(run)
        assert {key: first[key] for key in first if key not in ("last_result", "code_sha256")} == {
            "baseline_cycles": BASELINE, "best_cycles": BASELINE, "cycles": None, "evaluations": 0,
            "failure_stage": None, "gain": None, "improved": False, "refusals": 0, "rules_clean": True,
            "valid": None, "workdir": "work",
        }
        assert len(first["code_sha256"]) == 64
        for name in ("best_cycles", "valid", "rules_clean", "refusals"):
            assert run_cli(run, "state", "declare", name, "--path", name).returncode == 0

        program.write_text(KERNEL.format(bundles=70))
        better = run_cli(run, "act", "EVALUATE", "label=seventy-bundles", "--predict", "ch best_cycles = 70; ch valid = true")
        assert better.returncode == 0 and "RESULT | PREDICTED" in better.stdout, better.stdout + better.stderr
        data = _last_data(run)
        assert (data["evaluations"], data["valid"], data["cycles"], data["best_cycles"]) == (1, True, 70, 70)
        assert (data["improved"], data["gain"], data["failure_stage"]) == (True, 30, None)

        program.write_text(WRONG)
        wrong = run_cli(run, "act", "EVALUATE", "--params", '{"label": "wrong output"}', "--predict", "ch best_cycles = 70; ch valid = false")
        assert wrong.returncode == 0 and "RESULT | PREDICTED" in wrong.stdout, wrong.stdout + wrong.stderr
        data = _last_data(run)
        assert (data["evaluations"], data["valid"], data["cycles"], data["best_cycles"]) == (2, False, None, 70)
        assert (data["improved"], data["gain"], data["failure_stage"]) == (False, None, "incorrect_output")

        program.write_text(BROKEN)
        assert run_cli(run, "act", "EVALUATE", "label=broken-build", "--predict", "ch best_cycles = 70").returncode == 0
        assert _last_data(run)["failure_stage"] == "build_error"

        tests = run / "work" / "tests" / "submission_tests.py"
        original = tests.read_text()
        tests.write_text(original + "\n# loosened\n")
        program.write_text(KERNEL.format(bundles=60))
        refused = run_cli(run, "act", "EVALUATE", "label=with-edited-tests", "--predict", "ch rules_clean = false; ch refusals delta = 1")
        assert refused.returncode == 0 and "RESULT | PREDICTED" in refused.stdout, refused.stdout + refused.stderr
        data = _last_data(run)
        assert data["rules_clean"] is False and data["evaluations"] == 3 and data["refusals"] == 1
        assert data["last_result"]["status"] == "refused"
        assert "changed tests/submission_tests.py" in data["last_result"]["detail"]
        assert len(_frontier(run)) == 3

        tests.write_text(original)
        assert run_cli(run, "act", "EVALUATE", "label=sixty-bundles", "--predict", "ch best_cycles = 60").returncode == 0
        data = _last_data(run)
        assert (data["evaluations"], data["cycles"], data["best_cycles"], data["gain"]) == (4, 60, 60, 10)

        lines = _frontier(run)
        assert [line["n"] for line in lines] == [1, 2, 3, 4]
        assert [line["label"] for line in lines] == ["seventy-bundles", "wrong output", "broken-build", "sixty-bundles"]
        assert [line["valid"] for line in lines] == [True, False, False, True]
        assert [line["cycles"] for line in lines] == [70, None, None, 60]
        assert [line["improved"] for line in lines] == [True, False, False, True]
        assert [line["frontier_before"] for line in lines] == [100, 70, 70, 70]
        assert [line["best_cycles"] for line in lines] == [70, 70, 70, 60]
        assert [line["failure_stage"] for line in lines] == [None, "incorrect_output", "build_error", None]
        assert lines[0]["error"] is None and "does not build" in lines[2]["error"] and len(lines[2]["error"]) <= 300
        assert lines[0]["trials"] == [{"correct": True, "cycles": 70}] * 2
        assert lines[1]["trials"] == [{"correct": False, "cycles": 50}]
        assert lines[0]["started_at"] <= lines[0]["at"] and lines[0]["at"].endswith("Z")
        state = run / ".assay" / "world_state"
        assert (state / "best.py").read_text() == KERNEL.format(bundles=60)
        assert (state / "candidates" / "2.py").read_text() == WRONG
        assert sorted(path.name for path in (state / "candidates").iterdir()) == ["1.py", "2.py", "3.py", "4.py"]
    finally:
        stop_run(run)


def test_a_build_past_its_timeout_is_a_failed_evaluation(pack, tmp_path):
    constants = json.loads((pack / "kernel.json").read_text())
    constants["build_timeout_seconds"] = 2
    (pack / "kernel.json").write_text(json.dumps(constants))
    run = tmp_path / "slow"
    run.mkdir()
    try:
        started = run_cli(run, "start", "kernel2", "--adapter", f"{pack / 'adapter.py'}:factory",
                          "--registry", str(pack / "registry.json"), "--owner-token-file", str(tmp_path / "slow.token"))
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "state", "declare", "best_cycles", "--path", "best_cycles").returncode == 0
        (run / "work" / "perf_takehome.py").write_text(SLOW)
        done = run_cli(run, "act", "EVALUATE", "label=sleeps", "--predict", "ch best_cycles = 100")
        assert done.returncode == 0, done.stdout + done.stderr
        line = _frontier(run)[0]
        assert (line["valid"], line["failure_stage"], line["best_cycles"]) == (False, "build_timeout", 100)
    finally:
        stop_run(run)


def test_the_clean_rescore_reports_cycles_thresholds_and_rules(pack, tmp_path):
    score = pack / "checks" / "score.py"
    candidate = _write(tmp_path / "candidate.py", KERNEL.format(bundles=70))
    done = subprocess.run([sys.executable, str(score), str(candidate), "--json"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout)
    assert (result["valid"], result["cycles"], result["normalized"]) == (True, 70, round(1363 / 70, 4))
    assert result["rules"] == {"n_cores_is_1": True, "problem_unchanged": True, "tests_unchanged": True}
    assert result["submission_tests"]["thresholds"] == {"test_kernel_correctness": True, "test_under_eighty": True}
    text = subprocess.run([sys.executable, str(score), str(candidate)], capture_output=True, text=True, timeout=120)
    assert text.stdout.startswith("SCORE | valid | cycles 70 | normalized "), text.stdout

    slow = _write(tmp_path / "slow.py", KERNEL.format(bundles=90))
    done = subprocess.run([sys.executable, str(score), str(slow), "--json"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0
    assert json.loads(done.stdout)["submission_tests"]["thresholds"]["test_under_eighty"] is False

    wrong = _write(tmp_path / "wrong.py", WRONG)
    failed = subprocess.run([sys.executable, str(score), str(wrong), "--json"], capture_output=True, text=True, timeout=120)
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["failure_stage"] == "incorrect_output"

    run = tmp_path / "run"
    _write(run / "work" / "perf_takehome.py", KERNEL.format(bundles=95))
    _write(run / ".assay" / "world_state" / "best.py", KERNEL.format(bundles=65))
    by_run = subprocess.run([sys.executable, str(score), "--run", str(run), "--json"], capture_output=True, text=True, timeout=120)
    assert json.loads(by_run.stdout)["cycles"] == 65

    missing = subprocess.run([sys.executable, str(score), str(tmp_path / "none.py")], capture_output=True, text=True, timeout=60)
    assert missing.returncode == 2 and missing.stderr.startswith("ERROR | ")


def test_the_rescore_catches_a_candidate_that_rewrites_the_tests(pack, tmp_path):
    tamper = KERNEL.format(bundles=70) + (
        "\nimport os\n"
        "_tests = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests', 'submission_tests.py')\n"
        "if os.path.isfile(_tests):\n"
        "    open(_tests, 'a').write('# changed\\n')\n"
    )
    candidate = _write(tmp_path / "tamper.py", tamper)
    done = subprocess.run([sys.executable, str(pack / "checks" / "score.py"), str(candidate), "--json"],
                          capture_output=True, text=True, timeout=120)
    result = json.loads(done.stdout)
    assert done.returncode == 1 and result["rules"]["tests_unchanged"] is False, done.stdout
