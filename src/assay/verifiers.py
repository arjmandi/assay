"""Executable verifier claims: agent-authored, content-hashed, sandboxed.

A claim `verify:<relative/path.py>` names a file that defines

    def verify(before, after) -> tuple[bool, str]

where `before`/`after` are the observation JSON objects and the returned str
is the mandatory counter-fact ("actual"). At claim time the file is read,
sha256-hashed, and copied to `.assay/verifiers/<hash>.py`; the hash is journaled
on the prediction. At grading time the stored copy runs in a subprocess
(`python3 -I`, fresh tmpdir cwd, empty environment, observations on stdin, one
JSON line `{"ok": bool, "actual": str}` on stdout, 5s CPU and wall limits).
Crash, timeout, or malformed output grades as INVALID_CLAIM — not a miss, its
own counter, and it halts a containing batch.

Discrimination telemetry: after grading on (before, after) the verifier also
runs on the identity transition (before, before); both verdicts are journaled
and per-hash counters {graded, passed, failed, invalid, identity_same_verdict}
are kept in `.assay/verifiers/stats.json`. A verifier with graded>=5 and
failed==0 is flagged VACUOUS in status and its passes are excluded from the
capability meter.
"""

from __future__ import annotations

import hashlib
import json
import resource
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .core import AssayError, RunPaths, append_jsonl, atomic_json, read_json

VERIFY_TIMEOUT_SECONDS = 5.0
VACUOUS_MIN_GRADED = 5

_RUNNER = """\
import importlib.util, json, sys
payload = json.loads(sys.stdin.read())
spec = importlib.util.spec_from_file_location("verifier", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ok, actual = module.verify(payload["before"], payload["after"])
sys.stdout.write("\\n" + json.dumps({"ok": bool(ok), "actual": str(actual)}) + "\\n")
"""


def admit_verifier(paths: RunPaths, claim: dict[str, Any]) -> None:
    """Claim-time admission: read, hash, store, journal. Runs before any spend."""
    reference = str(claim.get("path", ""))
    candidate = Path(reference)
    if candidate.is_absolute():
        raise AssayError(
            f"verify: takes a path relative to the run directory, got {reference!r}"
        )
    source = paths.root / candidate
    try:
        source.resolve().relative_to(paths.root.resolve())
    except ValueError as error:
        raise AssayError(
            "verifier file must stay inside the run directory"
        ) from error
    try:
        body = source.read_bytes()
    except (FileNotFoundError, IsADirectoryError) as error:
        raise AssayError(f"verifier file not found: {reference}") from error
    digest = hashlib.sha256(body).hexdigest()
    paths.verifiers.mkdir(parents=True, exist_ok=True)
    stored = paths.verifiers / f"{digest}.py"
    if not stored.exists():
        stored.write_bytes(body)
    claim["verifier_hash"] = digest
    append_jsonl(
        paths.activity,
        {"kind": "verifier_admitted", "hash": digest, "source": reference},
    )


def run_verifier(
    paths: RunPaths,
    digest: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    timeout: float = VERIFY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a stored verifier once, sandboxed. Returns a status record."""
    stored = paths.verifiers / f"{digest}.py"
    if not stored.exists():
        return {"status": "invalid", "reason": f"stored verifier {digest[:12]} missing"}
    payload = json.dumps({"before": before, "after": after}).encode()
    cpu_seconds = max(1, int(timeout))

    def _limits() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    with tempfile.TemporaryDirectory(prefix="assay-verify-") as scratch:
        try:
            completed = subprocess.run(  # noqa: S603 - deliberate sandboxed run
                [sys.executable, "-I", "-c", _RUNNER, str(stored.resolve())],
                input=payload,
                capture_output=True,
                cwd=scratch,
                env={},
                timeout=timeout,
                preexec_fn=_limits,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "invalid",
                "reason": f"verifier timed out after {timeout:g}s",
            }
    if completed.returncode != 0:
        stderr = completed.stderr.decode(errors="replace").strip().splitlines()
        tail = stderr[-1][:200] if stderr else "no stderr"
        return {
            "status": "invalid",
            "reason": f"verifier crashed (exit {completed.returncode}): {tail}",
        }
    lines = [
        line
        for line in completed.stdout.decode(errors="replace").splitlines()
        if line.strip()
    ]
    if not lines:
        return {"status": "invalid", "reason": "verifier produced no output"}
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {
            "status": "invalid",
            "reason": f"malformed verifier output: {lines[-1][:120]!r}",
        }
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("ok"), bool)
        or not isinstance(result.get("actual"), str)
    ):
        return {
            "status": "invalid",
            "reason": 'verifier output must be one JSON line {"ok": bool, "actual": str}',
        }
    return {"status": "ok", "ok": result["ok"], "actual": result["actual"]}


def load_stats(paths: RunPaths) -> dict[str, Any]:
    value = read_json(paths.verifier_stats, {})
    return value if isinstance(value, dict) else {}


def vacuous_hashes(stats: Mapping[str, Any]) -> set[str]:
    return {
        digest
        for digest, entry in stats.items()
        if isinstance(entry, Mapping)
        and int(entry.get("graded", 0)) >= VACUOUS_MIN_GRADED
        and int(entry.get("failed", 0)) == 0
    }


def observation_view(event: Mapping[str, Any]) -> dict[str, Any]:
    """The observation JSON object a verifier receives for one event."""
    view: dict[str, Any] = {
        "state": str(event["state"]),
        "levels_completed": int(event["levels_completed"]),
        "win_levels": int(event["win_levels"]),
        "available_actions": list(event["available_actions"]),
    }
    if "frames" in event:
        view["frames"] = event["frames"]
    else:
        view["data"] = event["observation"]
    return view


def grade_verifier_claim(
    paths: RunPaths,
    claim: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    timeout: float = VERIFY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Grade one verify claim, run the identity probe, update the counters."""
    digest = str(claim.get("verifier_hash", ""))
    record: dict[str, Any] = {**dict(claim), "verifier": True}
    stats = load_stats(paths)
    entry = stats.setdefault(
        digest,
        {"graded": 0, "passed": 0, "failed": 0, "invalid": 0, "identity_same_verdict": 0},
    )
    result = run_verifier(paths, digest, before, after, timeout=timeout)
    if result["status"] != "ok":
        entry["invalid"] += 1
        atomic_json(paths.verifier_stats, stats)
        return {
            **record,
            "ok": False,
            "invalid": True,
            "actual": f"INVALID_CLAIM: {result['reason']}",
        }
    identity = run_verifier(paths, digest, before, before, timeout=timeout)
    identity_verdict: Any = identity["ok"] if identity["status"] == "ok" else "invalid"
    entry["graded"] += 1
    entry["passed" if result["ok"] else "failed"] += 1
    if identity_verdict == result["ok"]:
        entry["identity_same_verdict"] += 1
    vacuous = (
        entry["graded"] >= VACUOUS_MIN_GRADED and entry["failed"] == 0
    )
    atomic_json(paths.verifier_stats, stats)
    graded = {
        **record,
        "ok": bool(result["ok"]),
        "actual": str(result["actual"]),
        "identity_verdict": identity_verdict,
    }
    if vacuous and result["ok"]:
        graded["excluded_from_meter"] = True
    return graded
