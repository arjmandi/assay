"""Carryover: export a run's earned knowledge; import it FOREIGN.

What the record says transfers and what rots (n=5): verifier-graded mechanics
and CODE ARTIFACTS transfer perfectly; inherited prose plans were stale or
wrong every time. Carryover therefore ships code + graded statistics + notes,
and the import discipline demotes everything:

- imported Verified lines land in .assay/PRIOR-NOTES.md under a FOREIGN header,
  demoted to Assumed until a CURRENT-run event id supports them;
- imported verifier sources land in imported_verifiers/ as candidate files;
  they earn standing only by being named in a prediction (`verify:`) and
  graded again;
- an imported model NEVER carries batching rights: the fit record does not
  travel; rights are re-earned by `assay model replay` on the current journal;
- HAZARD TAGS are the one distinguished class (the carve-out the rationality
  check forced): on a matching registration they import ACTIVE: the
  declaration demand fires before the hazard does, which is the whole point;
  on a non-matching registration they import inactive and are listed foreign.

The journal digest v1 is per-actuator outcome statistics replayed from the
journal: attempts, split miss rates, invalid counts, plus the journal's
id-range and content hash so provenance is checkable.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core import AssayError, RunPaths, append_jsonl, atomic_json, read_json
from .modules import hazards_path, load_hazards

if TYPE_CHECKING:
    from .run import Run

KNOWLEDGE_FORMAT = 1
_NOTES_CAP = 100_000
_VERIFIER_CAP = 20
_SOURCE_CAP = 20_000
PRIOR_NOTES = "PRIOR-NOTES.md"


def registry_hash_of(registry: Mapping[str, Any] | None) -> str | None:
    if registry is None:
        return None
    return hashlib.sha256(
        json.dumps(registry, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def binding_hash_of(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "game_id": config.get("game_id"),
                "seed": config.get("seed"),
                "mode": config.get("mode"),
                "adapter": config.get("adapter"),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _journal_digest(run: Run) -> dict[str, Any]:
    events = run.events
    per_action: dict[str, dict[str, int]] = {}
    for event in events:
        if not event.counts_action:
            continue
        slot = per_action.setdefault(
            str(event.action),
            {
                "attempts": 0,
                "graded": 0,
                "wm_graded": 0,
                "wm_missed": 0,
                "gamble_graded": 0,
                "gamble_held": 0,
                "invalid": 0,
            },
        )
        slot["attempts"] += 1
        for item in event.grade:
            if item.kind in {"note", "coerced"} or item.machine:
                continue
            if item.invalid or item.ungradable:
                slot["invalid"] += 1
                continue
            slot["graded"] += 1
            bucket = str(item.bucket or "world_model")
            if bucket == "gamble":
                slot["gamble_graded"] += 1
                if item.ok:
                    slot["gamble_held"] += 1
            elif bucket == "world_model":
                slot["wm_graded"] += 1
                if not item.ok:
                    slot["wm_missed"] += 1
    try:
        journal_sha: str | None = hashlib.sha256(run.paths.events.read_bytes()).hexdigest()
    except FileNotFoundError:
        journal_sha = None
    return {
        "per_action": per_action,
        "events": len(events),
        "paid": sum(1 for event in events if event.counts_action),
        "journal_sha256": journal_sha,
        "final_state": str(events[-1].state) if events else None,
        "levels_completed": int(events[-1].levels_completed) if events else 0,
    }


def _verified_lines(notes: str) -> list[str]:
    """Lines of the Verified section that cite event ids (eNN)."""
    lines: list[str] = []
    in_verified = False
    for line in notes.splitlines():
        if line.lstrip().startswith("#"):
            in_verified = "verified" in line.lower()
            continue
        if in_verified and line.strip() and re.search(r"\be\d+\b", line):
            lines.append(line.strip())
    return lines


def export_knowledge(run: Run, out: Path | None = None) -> Path:
    from .model import fit_path, model_source
    from .verifiers import load_stats

    paths = run.paths
    config = run.config
    registry = run.registry
    try:
        notes = paths.notes.read_text()[:_NOTES_CAP]
    except FileNotFoundError:
        notes = ""
    verifier_stats = load_stats(paths)
    verifiers: list[dict[str, Any]] = []
    if paths.verifiers.is_dir():
        for file in sorted(paths.verifiers.glob("*.py"))[:_VERIFIER_CAP]:
            verifiers.append(
                {
                    "hash": file.stem,
                    "source": file.read_text()[:_SOURCE_CAP],
                    "stats": verifier_stats.get(file.stem, {}),
                }
            )
    model_entry = None
    if model_source(paths).exists():
        fit = read_json(fit_path(paths), None)
        model_entry = {
            "source": model_source(paths).read_text()[:_SOURCE_CAP],
            # Informational only: rights NEVER travel (re-fit on import).
            "last_fit_summary": {
                key: fit.get(key)
                for key in ("fit", "graded", "missed", "promotion")
            }
            if isinstance(fit, dict)
            else None,
        }
    knowledge = {
        "knowledge_format": KNOWLEDGE_FORMAT,
        "exported_at": time.time(),
        "game_id": config.get("game_id"),
        "registry_hash": config.get("registry_hash") or registry_hash_of(registry),
        "binding_hash": config.get("binding_hash") or binding_hash_of(config),
        "digest": _journal_digest(run),
        "notes": notes,
        "verified_lines": _verified_lines(notes),
        "hazards": load_hazards(paths),
        "verifiers": verifiers,
        "model": model_entry,
    }
    target = out or (paths.root / "assay_knowledge.json")
    _refuse_state_target(paths, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(knowledge, indent=1, sort_keys=True) + "\n")
    append_jsonl(
        paths.activity,
        {
            "kind": "knowledge_export",
            "path": str(target),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "verifiers": len(verifiers),
            "hazards": len(load_hazards(paths)),
        },
    )
    return target


def _refuse_state_target(paths: RunPaths, target: Path) -> None:
    """A kernel command never writes under the run's `.assay` or `.claude`
    where the agent says (docs/ARCHITECTURE.md section 8.4): the export's
    `--out` is refused there, hooks or no hooks, since the journal lives
    under one and the session's hooks under the other."""
    resolved = (target if target.is_absolute() else Path.cwd() / target).resolve()
    for directory in (paths.state, paths.root / ".claude"):
        try:
            resolved.relative_to(directory.resolve())
        except ValueError:
            continue
        raise AssayError(
            f"--out must not point under {directory.name}, got {target}",
            code="PATH_INVALID",
            hint="export the knowledge to a file in the run directory outside .assay and .claude, or elsewhere",
        )


def import_knowledge(run: Run, source: Path) -> dict[str, Any]:
    """Import at run start. Everything lands FOREIGN; see the module docstring."""
    try:
        raw = source.read_text()
    except OSError as error:
        raise AssayError(f"unreadable knowledge file {source}: {error}", code="KNOWLEDGE_INVALID") from error
    sha = hashlib.sha256(raw.encode()).hexdigest()
    try:
        knowledge = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AssayError(f"knowledge file is not valid JSON: {error}", code="KNOWLEDGE_INVALID") from error
    if not isinstance(knowledge, dict) or knowledge.get("knowledge_format") != KNOWLEDGE_FORMAT:
        raise AssayError(
            f"unsupported knowledge format (this build reads {KNOWLEDGE_FORMAT})",
            code="KNOWLEDGE_INVALID",
        )
    paths = run.paths
    config = run.config
    imported_dir = paths.state / "imported"
    imported_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(imported_dir / "knowledge.json", knowledge)

    matching = (
        knowledge.get("game_id") == config.get("game_id")
        and knowledge.get("registry_hash") == config.get("registry_hash")
        and knowledge.get("registry_hash") is not None
    )

    notes = str(knowledge.get("notes") or "")
    if notes.strip():
        header = (
            "# PRIOR-NOTES (FOREIGN: imported knowledge)\n\n"
            f"Source: {knowledge.get('game_id')} | exported journal "
            f"{ (knowledge.get('digest') or {}).get('events') } events | sha {sha[:12]}\n\n"
            "EVERY 'Verified' line below is DEMOTED TO ASSUMED here: trust it only\n"
            "after a CURRENT-run event id supports it. Graded mechanics and code\n"
            "transfer; prose plans rot: re-derive the plan from the live frame.\n\n"
            "---\n\n"
        )
        (paths.state / PRIOR_NOTES).write_text(header + notes)

    verifier_dir = paths.root / "imported_verifiers"
    imported_verifiers = 0
    for entry in knowledge.get("verifiers") or []:
        if not isinstance(entry, Mapping) or not entry.get("source"):
            continue
        verifier_dir.mkdir(parents=True, exist_ok=True)
        (verifier_dir / f"{str(entry.get('hash', 'unknown'))[:16]}.py").write_text(
            str(entry["source"])
        )
        imported_verifiers += 1

    model_entry = knowledge.get("model")
    if isinstance(model_entry, Mapping) and model_entry.get("source"):
        (paths.root / "imported_model.py").write_text(str(model_entry["source"]))

    hazards_imported = hazards_foreign = 0
    incoming = [tag for tag in knowledge.get("hazards") or [] if isinstance(tag, Mapping)]
    if incoming:
        tags = load_hazards(paths)
        existing = {(tag["action_class"], tag["signature"]) for tag in tags}
        for tag in incoming:
            key = (str(tag.get("action_class")), str(tag.get("signature")))
            if key in existing:
                continue
            record = {
                "action_class": key[0],
                "signature": key[1],
                "evidence_event": tag.get("evidence_event"),
                "origin": "import",
                # THE CARVE-OUT: on a matching registration the demand imports
                # ACTIVE: it must fire before the hazard does, not after.
                "active": bool(matching),
            }
            tags.append(record)
            existing.add(key)
            if matching:
                hazards_imported += 1
            else:
                hazards_foreign += 1
        atomic_json(hazards_path(paths), tags)

    verified = knowledge.get("verified_lines") or []
    summary = {
        "kind": "knowledge_import",
        "sha256": sha,
        "path": str(source),
        "source_game": knowledge.get("game_id"),
        "matching_registration": matching,
        "prior_notes": bool(notes.strip()),
        "verifiers": imported_verifiers,
        "hazards_active": hazards_imported,
        "hazards_foreign_inactive": hazards_foreign,
        "model_source": bool(model_entry),
        "verified_lines_demoted": len(verified) if isinstance(verified, list) else 0,
    }
    append_jsonl(paths.activity, summary)
    return summary


def foreign_facts(run: Run) -> dict[str, Any] | None:
    """The FOREIGN block's facts for an importing run, or None: the source
    world and its digest, whether the prior notes are beside the journal,
    the count of verifier candidates (None without the directory), whether a
    model was imported, and the imported hazard tags still active."""
    paths = run.paths
    knowledge = read_json(paths.state / "imported" / "knowledge.json", None)
    if not isinstance(knowledge, dict):
        return None
    digest = knowledge.get("digest") or {}
    candidates = paths.root / "imported_verifiers"
    return {
        "world": knowledge.get("game_id"),
        "paid": digest.get("paid"),
        "final_state": digest.get("final_state"),
        "prior_notes": (paths.state / PRIOR_NOTES).exists(),
        "verifier_candidates": len(list(candidates.glob("*.py"))) if candidates.is_dir() else None,
        "imported_model": (paths.root / "imported_model.py").exists(),
        "active_hazards": sum(
            1 for tag in load_hazards(paths) if tag.get("origin") == "import" and tag.get("active")
        ),
    }


def foreign_text(
    world: Any,
    paid: Any,
    final_state: Any,
    prior_notes: bool,
    verifier_candidates: int | None,
    imported_model: bool,
    active_hazards: int,
) -> list[str]:
    lines = [
        f"FOREIGN | imported knowledge from world {world} "
        f"({paid} paid actions, final {final_state}); "
        "everything below is demoted until re-earned here"
    ]
    if prior_notes:
        lines.append(
            f"FOREIGN | prior notes: .assay/{PRIOR_NOTES} (Verified lines are Assumed here)"
        )
    if verifier_candidates is not None:
        lines.append(
            f"FOREIGN | {verifier_candidates} verifier candidate(s) in imported_verifiers/; they "
            "earn standing only by being named in a prediction and graded again"
        )
    if imported_model:
        lines.append(
            "FOREIGN | imported_model.py carries NO batching rights; re-earn via "
            "`assay model replay` on this run's journal"
        )
    if active_hazards:
        lines.append(
            f"FOREIGN | {active_hazards} imported hazard tag(s) ACTIVE (declaration demand "
            "retained on import, the carve-out)"
        )
    return lines


def foreign_lines(run: Run) -> list[str]:
    """The status FOREIGN block for an importing run."""
    facts = foreign_facts(run)
    return [] if facts is None else foreign_text(**facts)
