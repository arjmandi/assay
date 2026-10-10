"""Executable verifier outcomes: agent-authored, content-hashed, sandboxed.

An outcome `verify:<relative/path.py>` names a file that defines

    def verify(before, after) -> tuple[bool, str]

where `before`/`after` are the observation JSON objects and the returned str
is the mandatory counter-fact ("actual"). At prediction time the file is read,
sha256-hashed, and copied to `.assay/verifiers/<hash>.py`; the hash is journaled
on the prediction. At grading time the stored copy runs in the sandbox
(`sandbox.run_program`: a scratch copy, `python -I`, an empty environment, the
process limits, no path into the run directory, no network, no fork; the
observations on stdin, one JSON line `{"ok": bool, "actual": str}` on stdout,
5s CPU and wall limits).
Crash, timeout, or malformed output grades as INVALID_CLAIM: not a miss, its
own counter, and it halts a containing batch.

Discrimination telemetry: after grading on (before, after) the verifier also
runs on the identity transition (before, before); both verdicts are journaled
and per-hash counters {graded, passed, failed, invalid, identity_same_verdict}
are kept in `.assay/verifiers/stats.json`. A verifier is VACUOUS when, over
five or more gradings, its identity verdict equalled its real verdict every
time: it does not use the transition. Status says so and its passes are
excluded from the capability meter. A verifier that never failed is an
advisory, not the flag.

The stats file is versioned, not migrated. A file written from 1.2.0 on is
`{"rule": "identity", "verifiers": {<hash>: counters}}`, the marker set at the
run's first grading. A file recorded before that is the flat
`{<hash>: counters}` of the never-failed rule (graded >= 5 and failed == 0),
and that rule keeps applying to its run, so the published run directories
render exactly as they did.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from .core import AssayError, RunPaths, append_jsonl, atomic_json, read_json
from .sandbox import run_program
from .records import Event, Grade, Outcome

VERIFY_TIMEOUT_SECONDS = 5.0
VACUOUS_MIN_GRADED = 5
RULE_IDENTITY = "identity"  # from 1.2.0: the identity verdict matched every time
RULE_NEVER_FAILED = "never_failed"  # before 1.2.0: five gradings, no failure
_COUNTERS = ("graded", "passed", "failed", "invalid", "identity_same_verdict")

_RUNNER = """\
import importlib.util, json, sys
payload = json.loads(sys.stdin.read())
spec = importlib.util.spec_from_file_location("verifier", payload["verifier_path"])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ok, actual = module.verify(payload["before"], payload["after"])
sys.stdout.write("\\n" + json.dumps({"ok": bool(ok), "actual": str(actual)}) + "\\n")
"""


def admit_verifier(paths: RunPaths, outcome: Outcome) -> Outcome:
    """Admission at prediction time: read, hash, store, journal. Runs before any spend;
    returns the outcome carrying its verifier hash."""
    reference = str(outcome.path or "")
    candidate = Path(reference)
    if candidate.is_absolute():
        raise AssayError(
            f"verify: takes a path relative to the run directory, got {reference!r}",
            code="PATH_INVALID",
        )
    source = paths.root / candidate
    try:
        source.resolve().relative_to(paths.root.resolve())
    except ValueError as error:
        raise AssayError(
            "verifier file must stay inside the run directory",
            code="PATH_INVALID",
        ) from error
    try:
        body = source.read_bytes()
    except (FileNotFoundError, IsADirectoryError) as error:
        raise AssayError(f"verifier file not found: {reference}", code="FILE_NOT_FOUND") from error
    digest = hashlib.sha256(body).hexdigest()
    paths.verifiers.mkdir(parents=True, exist_ok=True)
    stored = paths.verifiers / f"{digest}.py"
    if not stored.exists():
        stored.write_bytes(body)
    append_jsonl(
        paths.activity,
        {"kind": "verifier_admitted", "hash": digest, "source": reference},
    )
    return outcome.updated(verifier_hash=digest)


def _invalid_reason(outcome: Mapping[str, Any], timeout: float) -> str:
    """The journaled words for a run the sandbox refused, as they have always
    read: the kind of failure in the verifier's own terms."""
    kind = outcome.get("kind")
    if kind == "timeout":
        return f"verifier timed out after {timeout:g}s"
    if kind == "crash":
        return f"verifier crashed (exit {outcome['exit']}): {str(outcome['tail'])[:200]}"
    if kind == "no_output":
        return "verifier produced no output"
    if kind == "malformed":
        return f"malformed verifier output: {str(outcome['output'])[:120]!r}"
    return f"verifier {outcome['reason']}"


def run_verifier(
    paths: RunPaths,
    digest: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    timeout: float = VERIFY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a stored verifier once in the sandbox. Returns a status record."""
    stored = paths.verifiers / f"{digest}.py"
    if not stored.exists():
        return {"status": "invalid", "reason": f"stored verifier {digest[:12]} missing"}
    payload = {"before": before, "after": after, "verifier_path": str(stored.resolve())}
    outcome = run_program(_RUNNER, payload, timeout=timeout, companions=(stored,))
    if outcome["status"] != "ok":
        return {"status": "invalid", "reason": _invalid_reason(outcome, timeout)}
    result = outcome["result"]
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
    """The stats file as written, in whichever shape (see `stats_rule`)."""
    value = read_json(paths.verifier_stats, {})
    return value if isinstance(value, dict) else {}


def stats_rule(stats: Mapping[str, Any]) -> str:
    """Which vacuity rule a stats file is under. The marker is written at a
    run's first grading from 1.2.0 on; a file with counters and no marker was
    recorded under the never-failed rule, which keeps applying to that run; a
    file with nothing in it belongs to a run that has not graded yet."""
    if stats.get("rule") == RULE_IDENTITY:
        return RULE_IDENTITY
    if any(isinstance(entry, Mapping) for entry in stats.values()):
        return RULE_NEVER_FAILED
    return RULE_IDENTITY


def stats_entries(stats: Mapping[str, Any]) -> Mapping[str, Any]:
    """The per-hash counters, whichever shape the file has."""
    if stats.get("rule") == RULE_IDENTITY:
        entries = stats.get("verifiers")
        return entries if isinstance(entries, Mapping) else {}
    return stats


def is_vacuous(entry: Mapping[str, Any], rule: str) -> bool:
    """One verifier's counters against the rule in force for its run."""
    graded = int(entry.get("graded", 0))
    if graded < VACUOUS_MIN_GRADED:
        return False
    if rule == RULE_NEVER_FAILED:
        return int(entry.get("failed", 0)) == 0
    return int(entry.get("identity_same_verdict", 0)) == graded


def vacuous_hashes(stats: Mapping[str, Any]) -> set[str]:
    rule = stats_rule(stats)
    return {
        digest
        for digest, entry in stats_entries(stats).items()
        if isinstance(entry, Mapping) and is_vacuous(entry, rule)
    }


def never_failed_hashes(stats: Mapping[str, Any]) -> set[str]:
    """The advisory of the identity rule: graded five or more times, never
    failed, and not vacuous. Under the never-failed rule that set is the
    vacuous set itself, already flagged, so it is empty there."""
    if stats_rule(stats) != RULE_IDENTITY:
        return set()
    return {
        digest
        for digest, entry in stats_entries(stats).items()
        if isinstance(entry, Mapping)
        and int(entry.get("graded", 0)) >= VACUOUS_MIN_GRADED
        and int(entry.get("failed", 0)) == 0
        and not is_vacuous(entry, RULE_IDENTITY)
    }


def observation_view(event: Event) -> dict[str, Any]:
    """The observation JSON object a verifier receives for one event."""
    view: dict[str, Any] = {
        "state": str(event.state),
        "levels_completed": int(event.levels_completed),
        "win_levels": int(event.win_levels),
        "available_actions": list(event.available_actions),
    }
    if event.frames is not None:
        view["frames"] = event.frames
    else:
        view["data"] = event.observation
    return view


def grade_verifier_outcome(
    paths: RunPaths,
    outcome: Outcome,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    timeout: float = VERIFY_TIMEOUT_SECONDS,
) -> Grade:
    """Grade one verify outcome, run the identity probe, update the counters.
    The vacuity flag follows the rule the run's stats file is under."""
    digest = str(outcome.verifier_hash or "")
    stats = load_stats(paths)
    rule = stats_rule(stats)
    if rule == RULE_IDENTITY:
        stats["rule"] = RULE_IDENTITY  # the run's first grading writes the marker
        entries = stats.get("verifiers")
        if not isinstance(entries, dict):
            entries = stats["verifiers"] = {}
    else:
        entries = stats  # a run recorded before 1.2.0 keeps its flat file
    entry = entries.setdefault(digest, dict.fromkeys(_COUNTERS, 0))
    result = run_verifier(paths, digest, before, after, timeout=timeout)
    if result["status"] != "ok":
        entry["invalid"] += 1
        atomic_json(paths.verifier_stats, stats)
        return Grade.of(
            outcome,
            ok=False,
            actual=f"INVALID_CLAIM: {result['reason']}",
            invalid=True,
            verifier=True,
        )
    identity = run_verifier(paths, digest, before, before, timeout=timeout)
    identity_verdict: bool | Literal["invalid"] = (
        bool(identity["ok"]) if identity["status"] == "ok" else "invalid"
    )
    entry["graded"] += 1
    entry["passed" if result["ok"] else "failed"] += 1
    if identity_verdict == result["ok"]:
        entry["identity_same_verdict"] += 1
    vacuous = is_vacuous(entry, rule)
    atomic_json(paths.verifier_stats, stats)
    return Grade.of(
        outcome,
        ok=bool(result["ok"]),
        actual=str(result["actual"]),
        verifier=True,
        identity_verdict=identity_verdict,
        excluded_from_meter=bool(vacuous and result["ok"]),
    )
