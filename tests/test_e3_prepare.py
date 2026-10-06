"""tools/e3_prepare.py driven against the fake adapter through the real CLI and
broker: it refuses an unfinished source, treats WIN or a spent budget as
finished, exports from a copy and never changes the source tree, creates the
import run with the kernel's own `start --import`, verifies the FOREIGN block
and the import footprint, stops the broker, and writes the provenance.
tools/jobs_e3.json and tools/e3_launch.sh are checked for shape and dry run."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ASSAY_CLI, FAKE_ADAPTER, stop_run

REPO = Path(__file__).resolve().parents[1]
E3 = REPO / "tools" / "e3_prepare.py"
ORCH = REPO / "tools" / "night_orchestrator.py"
ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]
UP = (
    "def verify(before, after):\n"
    '    return after["data"]["counter"] > before["data"]["counter"], "counter up"\n'
)
NOTES = (
    "# Notes — fake1\n\n## Verified (cite event ids)\n"
    "- INC adds its amount to the counter (e1)\n\n## Assumed / open questions\n\n## Plan\n- reach 3\n"
)


def _cli(run_dir: Path, *args: str, anchors: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "ASSAY_ANCHOR_DIR": str(anchors)}
    return subprocess.run([sys.executable, str(ASSAY_CLI), "--run-dir", str(run_dir), *args],
                          capture_output=True, text=True, timeout=180, env=env)


def _registry(path: Path, budget: int) -> Path:
    path.write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": budget}}))
    return path


def _source(tmp: Path, name: str, budget: int, amounts: list[int]) -> tuple[Path, Path]:
    """A fake1 run played by the test (the agent's role): verifier, notes, acts."""
    run = tmp / name
    run.mkdir()
    registry = _registry(tmp / f"{name}.registry.json", budget)
    anchors = tmp / "anchors"
    started = _cli(run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory", "--registry", str(registry), anchors=anchors)
    assert started.returncode == 0, started.stderr
    (run / "checks").mkdir()
    (run / "checks" / "up.py").write_text(UP)
    for amount in amounts:
        acted = _cli(run, "act", "INC", f"amount={amount}", "--predict", "change; verify:checks/up.py", anchors=anchors)
        assert acted.returncode == 0, acted.stdout + acted.stderr
    (run / ".assay" / "NOTES.md").write_text(NOTES)
    stop_run(run)
    return run, registry


def _snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _prepare(tmp: Path, source: Path, registry: Path, cap: int, *extra: str):
    out = tmp / "e3"
    cmd = [sys.executable, str(E3), "fake1", "--source", str(source), "--out-root", str(out),
           "--registry", str(registry), "--adapter", f"{FAKE_ADAPTER}:factory", "--cap", str(cap),
           "--python", sys.executable, "--source-anchor-dir", str(tmp / "anchors"), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300), out


def test_refuses_an_unfinished_source_and_writes_nothing(tmp_path):
    source, registry = _source(tmp_path, "src", budget=10, amounts=[1])
    before = _snapshot(source)
    done, out = _prepare(tmp_path, source, registry, 10)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "REFUSED" in done.stderr and "not finished" in done.stderr
    assert "budget remaining 9" in done.stderr
    assert not (out / "fake1-import-s1").exists()
    assert not (out / "exports" / "fake1-from-gated-s1" / "assay_knowledge.json").exists()
    assert _snapshot(source) == before


def test_prepares_from_a_win_never_touches_the_source_and_writes_provenance(tmp_path):
    source, registry = _source(tmp_path, "src", budget=10, amounts=[2, 2])
    events = [json.loads(l) for l in (source / ".assay" / "events.jsonl").read_text().splitlines() if l.strip()]
    assert events[-1]["state"] == "WIN"
    before = _snapshot(source)
    chain_head = json.loads((source / ".assay" / "chain.json").read_text())["head"]

    done, out = _prepare(tmp_path, source, registry, 10)
    assert done.returncode == 0, done.stdout + done.stderr
    run = out / "fake1-import-s1"
    prov = out / "fake1-import-s1.provenance"
    artifact = out / "exports" / "fake1-from-gated-s1" / "assay_knowledge.json"
    try:
        # the source is byte-for-byte what it was, and no scratch copy remains
        assert _snapshot(source) == before
        assert not (out / "exports" / "fake1-from-gated-s1" / "work").exists()
        # the artifact is the kernel's export of the source
        knowledge = json.loads(artifact.read_text())
        assert knowledge["game_id"] == "fake1" and knowledge["digest"]["final_state"] == "WIN"
        assert knowledge["verified_lines"] == ["- INC adds its amount to the counter (e1)"]
        assert len(knowledge["verifiers"]) == 1
        sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        # provenance: source, chain head, artifact sha, imported items, no problems
        text = (prov / "PROVENANCE.md").read_text()
        assert str(source) in text and chain_head in text and sha in text
        assert "journal state WIN at e2" in text
        assert "`imported_verifiers/" in text and "bytes match artifact" in text
        assert "`.assay/PRIOR-NOTES.md`: import" in text
        assert "Problems: none" in text
        record = json.loads((prov / "provenance.json").read_text())
        assert record["problems"] == [] and record["source_unchanged"] is True
        assert record["verification"]["matching_registration"] is True
        assert record["source_anchors"]["intact"] is True  # WIN anchored the source
        assert record["broker"]["stopped"] is True and record["broker"]["status_after_stop"] is True
        token = (prov / "OWNER_TOKEN").read_text().strip()
        assert token and token not in text and token not in (prov / "provenance.json").read_text()
        assert (prov / "OWNER_TOKEN").stat().st_mode & 0o077 == 0
        # the run dir: kernel start plus the kernel's import, nothing else
        prior = (run / ".assay" / "PRIOR-NOTES.md").read_text()
        assert prior.startswith("# PRIOR-NOTES (FOREIGN — imported knowledge)")
        assert "DEMOTED TO ASSUMED" in prior and prior.endswith(NOTES)
        assert json.loads((run / ".assay" / "imported" / "knowledge.json").read_text()) == knowledge
        root_files = sorted(p.name for p in run.iterdir())
        assert root_files == [".assay", "imported_verifiers"]
        assert len(list((run / "imported_verifiers").glob("*.py"))) == 1
        new_events = [l for l in (run / ".assay" / "events.jsonl").read_text().splitlines() if l.strip()]
        assert len(new_events) == 1
        # the broker is down, status still shows the FOREIGN block (what the player sees)
        pid = json.loads((run / ".assay" / "broker.json").read_text()).get("pid")
        try:
            os.kill(int(pid), 0)
            alive = True
        except (ProcessLookupError, TypeError):
            alive = False
        assert not alive
        status = _cli(run, "status", anchors=out / "anchors")
        assert status.returncode == 0, status.stderr
        assert "FOREIGN | imported knowledge from fake1 (2 paid actions, final WIN)" in status.stdout
        assert "FOREIGN | prior notes: .assay/PRIOR-NOTES.md (Verified lines are Assumed here)" in status.stdout
        assert "FOREIGN | 1 verifier candidate(s) in imported_verifiers/" in status.stdout
        # --check passes, a second prepare refuses, --replace rebuilds
        checked = subprocess.run([sys.executable, str(E3), "fake1", "--out-root", str(out), "--check"],
                                 capture_output=True, text=True, timeout=60)
        assert checked.returncode == 0 and "READY" in checked.stdout, checked.stdout + checked.stderr
        again, _ = _prepare(tmp_path, source, registry, 10)
        assert again.returncode == 2 and "already exists" in again.stderr
        replaced, _ = _prepare(tmp_path, source, registry, 10, "--replace")
        assert replaced.returncode == 0, replaced.stdout + replaced.stderr
        assert _snapshot(source) == before
    finally:
        stop_run(run)


def test_budget_exhausted_without_win_counts_as_finished(tmp_path):
    source, registry = _source(tmp_path, "src", budget=2, amounts=[1, 1])
    done, out = _prepare(tmp_path, source, registry, 2)
    try:
        assert done.returncode == 0, done.stdout + done.stderr
        text = (out / "fake1-import-s1.provenance" / "PROVENANCE.md").read_text()
        assert "budget exhausted: 2 paid actions at cap 2, state NOT_FINISHED" in text
    finally:
        stop_run(out / "fake1-import-s1")


def test_jobs_e3_shape_and_orchestrator_dry_run(tmp_path):
    jobs = json.loads((REPO / "tools" / "jobs_e3.json").read_text())
    assert [j["id"] for j in jobs] == ["e3-ft09-import-s1", "e3-tr87-import-s1"]
    for job, cap in zip(jobs, (200, 1500)):
        assert job["cap"] == cap and job["arm"] == "import" and job["seed"] == 1
        assert job["registry"] == f"bench/arcagi/registry_e1_gated_{cap}.json"
        registry = json.loads((REPO / job["registry"]).read_text())
        assert registry["budget"]["actions"] == cap and registry["gate"] == "required"
        assert job["constitution"] == "CONSTITUTION.md"
        assert job["prompt_template"] == "tools/prompts/e1_player.md"
        assert job["run_dir"] == f"/Users/mohsenarjmandi/workspace/assay-runs/e3/{job['game']}-import-s1"
        assert job["source_run"] == f"/Users/mohsenarjmandi/workspace/assay-runs/e1/{job['game']}-gated-s1"
        assert job["anchor_dir"] == "/Users/mohsenarjmandi/workspace/assay-runs/e3/anchors"
    done = subprocess.run([sys.executable, str(ORCH), "--jobs", str(REPO / "tools" / "jobs_e3.json"),
                           "--state-dir", str(tmp_path), "--dry-run", "--no-caffeinate",
                           "--player", str(REPO / "tools" / "fake_player.sh"), "--max-concurrency", "2",
                           "--running-flag", str(tmp_path / "flag")], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "2 jobs, max concurrency 2" in done.stdout
    prompt = (tmp_path / "dryrun" / "e3-ft09-import-s1.s1.prompt.md").read_text()
    assert "registry_e1_gated_200.json" in prompt and "/assay-runs/e3/ft09-import-s1" in prompt
    # the prompt is the E1 player prompt unchanged: no hint about imported knowledge
    # beyond the run directory's own name (the only substituted text containing "import")
    e1 = (REPO / "tools" / "prompts" / "e1_player.md").read_text()
    stripped = prompt.replace(jobs[0]["run_dir"], "")
    for word in ("FOREIGN", "PRIOR-NOTES", "import", "carryover", "knowledge"):
        assert word not in e1 and word not in stripped
    launch = (REPO / "tools" / "e3_launch.sh").read_text()
    assert "--check" in launch and "jobs_e3.json" in launch and "E3_RUNNING" in launch
