"""The operator protocol of design note 3 (docs/ARCHITECTURE.md section 8.6),
run on the counter example the way the benchmark protocols describe it: the
operator starts the run with the owner token written to a file outside the run
directory, the agent's session begins in the run directory with the daemon
already up and its first command is `status`, the token never enters the
agent's output, and ratification is the operator's."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import ASSAY_CLI, run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
COUNTER_WORLD = REPO / "examples" / "counter_world.py"
COUNTER_REGISTRY = REPO / "examples" / "example_registry.json"


def agent_command(run: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """One command of the agent's session: run from the run directory, with no
    `--run-dir` and nothing of the operator's shell."""
    return subprocess.run(
        [sys.executable, str(ASSAY_CLI), *args],
        cwd=run,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_operator_starts_the_run_and_the_agent_never_sees_the_token(tmp_path):
    run = tmp_path / "demo"
    run.mkdir()
    # Outside the run directory, and outside anything the agent's session reads.
    token_file = tmp_path / "operator" / "counterdemo.token"
    try:
        # The operator's shell.
        started = run_cli(
            run, "start", "counterdemo",
            "--adapter", f"{COUNTER_WORLD}:factory",
            "--registry", str(COUNTER_REGISTRY),
            "--owner-token-file", str(token_file),
        )
        assert started.returncode == 0, started.stderr
        assert "STARTED | counterdemo" in started.stdout
        assert f"OWNER TOKEN | written to {token_file} (mode 0600)" in started.stdout
        token = token_file.read_text().strip()
        assert len(token) >= 20 and token not in started.stdout

        # The agent's session: the run directory, the daemon already up, status first.
        transcript: list[subprocess.CompletedProcess[str]] = []
        status = agent_command(run, "status")
        transcript.append(status)
        assert status.returncode == 0, status.stderr
        assert (
            "STATUS | counterdemo | event 0 | progress 1/1 | paid actions 0 | NOT_FINISHED"
            in status.stdout
        )
        acted = agent_command(run, "act", "INC", "amount=1", "--predict", "change")
        transcript.append(acted)
        assert acted.returncode == 0, acted.stderr
        assert "RESULT | PREDICTED" in acted.stdout
        proposed = agent_command(
            run, "goal", "propose", "reach 3 in two more actions",
            "--because", "INC moves the counter by its amount",
        )
        transcript.append(proposed)
        assert proposed.returncode == 0, proposed.stderr
        assert "GOAL | proposal #1 journaled, awaiting owner ratification" in proposed.stdout
        activity = [
            json.loads(line)
            for line in (run / ".assay" / "activity.jsonl").read_text().splitlines()
        ]
        assert any(
            record.get("kind") == "goal_proposed" and record.get("id") == 1
            for record in activity
        )
        listed = agent_command(run, "goal", "list")
        transcript.append(listed)
        assert listed.returncode == 0, listed.stderr
        assert "#1 [pending] reach 3 in two more actions; INC moves the counter by its amount" in listed.stdout
        refused = agent_command(run, "goal", "ratify", "1")
        transcript.append(refused)
        assert refused.returncode == 2
        assert "owner authority required" in refused.stderr
        # The token is in none of the agent's output, nor is the line that carries it.
        for completed in transcript:
            assert token not in completed.stdout and token not in completed.stderr
            assert "OWNER TOKEN" not in completed.stdout and "OWNER TOKEN" not in completed.stderr

        # Ratification is the operator's, with the file's content.
        ratified = run_cli(run, "goal", "ratify", "1", "--token", token)
        assert ratified.returncode == 0, ratified.stderr
        assert "GOAL | ratified #1: reach 3 in two more actions" in ratified.stdout
    finally:
        stop_run(run)
