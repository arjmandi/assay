#!/usr/bin/env python3
"""resume_at.py: cut an archived ASSAY run back to one event and prepare a
resumable copy of it (the E2 "resume at the proof" states).

    resume_at.py SOURCE_RUN_DIR EVENT_ID DEST_DIR --anchor-dir DIR \
        (--notes FILE | --notes-fallback-level) [--module FILE]... \
        [--module-mode NAME=MODE]... [--registry FILE] [--provenance FILE]

SOURCE is never written. DEST is a copy in which every artifact is cut back to
the prefix e0..EVENT_ID (inclusive):

  events.jsonl, mutations.jsonl, activity.jsonl, receipts/   the journal proper
  images/, levels/                                            rendered frames, archived notes
  verifiers/ (+ stats.json recomputed), channels/ + channels.json, hazards.json
  dossier.json rebuilt with the kernel, chain.json recomputed with the kernel's
  own chain code, audit.json and the broker files removed
  run-root files the agent created after the cut removed; files it later
  modified restored from the kernel's content-addressed copies where possible
  NOTES.md installed from --notes (see notes_at.py) or the level archive

Optionally the pinned registry gains external modules (--module, --module-mode):
the registry is normally immutable in place, so this edit is recorded in the
provenance together with the old and new registry_hash (config.json is updated
to match, as a fresh start would have written it). --registry names the file the
resume command will pass; it must canonicalize to the edited pinned registry.

ANCHORS. The kernel anchors chain heads outside the run directory, keyed by the
run path. The resumed run MUST be driven with ASSAY_ANCHOR_DIR pointing at the
fresh directory given here, so no anchor from the source run (or any other) can
be compared against the shortened journal and report divergence. The provenance
file records the exact environment and start command.

The provenance file is written OUTSIDE the run directory by default (the agent
may list its run directory and must not learn how the state was made).

Run with an interpreter that serves the kernel (Python 3.12+, numpy, pillow).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from assay.carryover import registry_hash_of  # noqa: E402
from assay.core import RunPaths, atomic_json, read_json  # noqa: E402
from assay.integrity import anchor_file, chain_path, compute_chain  # noqa: E402
from assay.modules import pin_external_modules  # noqa: E402
from assay.registry import load_registry_file, validate_registry  # noqa: E402

LAUNCHER_ARTIFACTS = ("player.err", "player.out", "player.log", "player_result.json")
RECEIPT = re.compile(r"-e(\d+)\.json$")
IMAGE = re.compile(r"^event-(\d+)-")
LEVEL = re.compile(r"^level-(\d+)\.md$")
PAID_KINDS = {"act", "commit", "reset"}


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_ts(value: str) -> dt.datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    when = dt.datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def raw_lines(path: Path) -> list[bytes]:
    """Lines with their terminators, so written prefixes are byte-identical."""
    if not path.exists():
        return []
    data = path.read_bytes()
    lines = data.split(b"\n")
    out = [line + b"\n" for line in lines[:-1]]
    if lines[-1]:
        out.append(lines[-1])  # unterminated last line
    return [line for line in out if line.strip()]


def write_lines(path: Path, lines: list[bytes]) -> None:
    path.write_bytes(b"".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("event_id", type=int)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--anchor-dir", required=True, type=Path,
                        help="FRESH directory for ASSAY_ANCHOR_DIR when the copy is resumed")
    parser.add_argument("--notes", type=Path, help="NOTES.md content to install (output of notes_at.py)")
    parser.add_argument("--notes-report", type=Path, help="notes_at.py JSON report, quoted in the provenance")
    parser.add_argument("--notes-fallback-level", action="store_true",
                        help="install .assay/levels/level-<levels_completed>.md as NOTES.md (a documented deviation)")
    parser.add_argument("--module", action="append", default=[], type=Path,
                        help="external module to add to the pinned registry and pin into .assay/modules/")
    parser.add_argument("--module-mode", action="append", default=[], metavar="NAME=MODE")
    parser.add_argument("--registry", type=Path,
                        help="registry file the resume command passes; must equal the edited pinned registry")
    parser.add_argument("--adapter", help="adapter spec for the documented start command (default: config.json)")
    parser.add_argument("--provenance", type=Path,
                        help="where to write RESUME_PROVENANCE.md (default <dest>.provenance/RESUME_PROVENANCE.md)")
    parser.add_argument("--boundary", help="ISO timestamp of the first post-cut instant for the run-root file census "
                        "(default: the timestamp of event EVENT_ID+1). Pass the start of the first "
                        "post-intervention session when one exists, so files that session created before "
                        "its first paid action are removed too")
    parser.add_argument("--drop-unresolved", action="store_true",
                        help="remove run-root files that existed at the cut but were modified later and have no "
                        "pre-cut copy (their content is post-cut); otherwise they are kept and flagged")
    parser.add_argument("--force", action="store_true", help="replace an existing DEST")
    args = parser.parse_args(argv)

    source: Path = args.source.resolve()
    dest: Path = args.dest.resolve()
    if not (source / ".assay" / "events.jsonl").exists():
        parser.error(f"{source} holds no .assay/events.jsonl")
    if dest.exists():
        if not args.force:
            parser.error(f"{dest} exists (pass --force to replace it)")
        if dest == source or source in dest.parents:
            parser.error("refusing to replace the source")
        shutil.rmtree(dest)
    if dest == source or source in dest.parents or dest in source.parents:
        parser.error("DEST must be outside SOURCE")
    if not args.notes and not args.notes_fallback_level:
        parser.error("give --notes FILE (from notes_at.py) or --notes-fallback-level")

    notes_lines: list[str] = []
    n = args.event_id

    # ---- read the source journal (read-only) ---------------------------------
    source_state = source / ".assay"
    event_lines = raw_lines(source_state / "events.jsonl")
    if not 0 <= n < len(event_lines):
        parser.error(f"event {n} is outside the journal (0..{len(event_lines) - 1})")
    events = [json.loads(line) for line in event_lines]
    cut = events[n]
    assert int(cut["id"]) == n, "event ids must equal line positions"
    t_cut = parse_ts(cut["timestamp"])
    next_event = parse_ts(events[n + 1]["timestamp"]) if n + 1 < len(events) else None
    boundary = next_event
    boundary_note = f"the timestamp of e{n + 1}"
    if args.boundary:
        given = parse_ts(args.boundary)
        if given < t_cut:
            parser.error("--boundary is before the cut event")
        if next_event is None or given < next_event:
            boundary, boundary_note = given, "given with --boundary (start of the first post-intervention session)"
    boundary_ts = boundary.timestamp() if boundary else float("inf")
    levels_at = int(cut["levels_completed"])
    mutation_cut = cut.get("mutation_id")
    source_journal_sha = sha256_path(source_state / "events.jsonl")

    # ---- census of run-root files on the SOURCE (birthtime survives nothing) --
    created_after: list[str] = []
    modified_after: list[str] = []
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in (".assay", "__pycache__")]
        for name in files:
            if name == ".DS_Store":
                continue
            path = Path(root) / name
            rel = str(path.relative_to(source))
            stat = path.stat()
            birth = getattr(stat, "st_birthtime", stat.st_mtime)
            if birth > boundary_ts:
                created_after.append(rel)
            elif stat.st_mtime > boundary_ts:
                modified_after.append(rel)

    # ---- copy ----------------------------------------------------------------
    shutil.copytree(source, dest, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    paths = RunPaths(dest)
    state = paths.state

    # ---- journal files -------------------------------------------------------
    write_lines(state / "events.jsonl", event_lines[: n + 1])
    mutation_lines = raw_lines(state / "mutations.jsonl")
    kept_mutations = []
    for line in mutation_lines:
        record = json.loads(line)
        if mutation_cut is not None and int(record.get("mutation_id", 0)) <= int(mutation_cut):
            kept_mutations.append(line)
    write_lines(state / "mutations.jsonl", kept_mutations)

    activity_lines = raw_lines(state / "activity.jsonl")
    activity = [json.loads(line) for line in activity_lines]
    cut_index = None
    paid_index = None
    for index, record in enumerate(activity):
        if record.get("kind") in PAID_KINDS and record.get("end_event") == n:
            paid_index = index
    if paid_index is not None:
        cut_index = paid_index
        for index in range(paid_index + 1, len(activity)):
            if activity[index].get("kind") == "command_end":
                cut_index = index
                break
        activity_rule = (f"cut after the command_end that closed the {activity[paid_index]['kind']} "
                         f"producing e{n} (record {cut_index + 1} of {len(activity)})")
    else:
        limit = t_cut + dt.timedelta(seconds=2)
        cut_index = max((i for i, r in enumerate(activity) if parse_ts(r["timestamp"]) <= limit), default=-1)
        activity_rule = f"no paid record names e{n}; cut by timestamp <= e{n} + 2 s (record {cut_index + 1})"
    kept_activity = activity[: cut_index + 1]
    write_lines(state / "activity.jsonl", activity_lines[: cut_index + 1])

    # ---- receipts, images, level archives -----------------------------------
    removed = {"receipts": 0, "images": 0, "levels": [], "verifiers": 0, "channels": 0, "hazards": 0}
    if paths.receipts.is_dir():
        for file in paths.receipts.iterdir():
            found = RECEIPT.search(file.name)
            if found and int(found.group(1)) > n:
                file.unlink()
                removed["receipts"] += 1
    images = state / "images"
    if images.is_dir():
        for file in images.iterdir():
            found = IMAGE.match(file.name)
            if found and int(found.group(1)) > n:
                file.unlink()
                removed["images"] += 1
    levels = state / "levels"
    if levels.is_dir():
        for file in levels.iterdir():
            found = LEVEL.match(file.name)
            if found and int(found.group(1)) > levels_at:
                file.unlink()
                removed["levels"].append(file.name)

    # ---- verifiers: keep what the prefix admitted, recompute stats ----------
    kept_events = events[: n + 1]
    admitted = {}
    for record in kept_activity:
        if record.get("kind") == "verifier_admitted":
            admitted[str(record.get("hash"))] = str(record.get("source"))
    graded_hashes = {
        str(grade.get("verifier_hash"))
        for event in kept_events for grade in (event.get("grade") or [])
        if grade.get("kind") == "verify" and grade.get("verifier_hash")
    }
    keep_verifiers = set(admitted) | graded_hashes
    if paths.verifiers.is_dir():
        for file in paths.verifiers.glob("*.py"):
            if file.stem not in keep_verifiers:
                file.unlink()
                removed["verifiers"] += 1
        cache = paths.verifiers / "__pycache__"
        if cache.is_dir():
            shutil.rmtree(cache)
    stats: dict[str, dict[str, int]] = {}
    for event in kept_events:
        for grade in event.get("grade") or []:
            if grade.get("kind") != "verify":
                continue
            entry = stats.setdefault(str(grade.get("verifier_hash")),
                                     {"graded": 0, "passed": 0, "failed": 0, "invalid": 0,
                                      "identity_same_verdict": 0})
            if grade.get("invalid"):
                entry["invalid"] += 1
                continue
            entry["graded"] += 1
            entry["passed" if grade.get("ok") else "failed"] += 1
            if grade.get("identity_verdict") == grade.get("ok"):
                entry["identity_same_verdict"] += 1
    if stats:
        atomic_json(paths.verifier_stats, stats)
    elif paths.verifier_stats.exists():
        paths.verifier_stats.unlink()

    # ---- channels ------------------------------------------------------------
    declared = {}
    for record in kept_activity:
        if record.get("kind") == "channel_declared":
            declared[str(record.get("channel"))] = str(record.get("hash") or "")
    channels_json = state / "channels.json"
    channels_dir = state / "channels"
    if channels_json.exists():
        table = read_json(channels_json, {})
        kept_table = {name: spec for name, spec in table.items() if name in declared}
        removed["channels"] = len(table) - len(kept_table)
        atomic_json(channels_json, kept_table)
        keep_hashes = {str(spec.get("hash")) for spec in kept_table.values() if spec.get("hash")}
        if channels_dir.is_dir():
            for file in channels_dir.glob("*.py"):
                if file.stem not in keep_hashes:
                    file.unlink()

    # ---- hazards, proposals ---------------------------------------------------
    hazards = state / "hazards.json"
    if hazards.exists():
        tags = read_json(hazards, [])
        kept_tags = [tag for tag in tags if int(tag.get("evidence_event", 0)) <= n]
        removed["hazards"] = len(tags) - len(kept_tags)
        atomic_json(hazards, kept_tags)
    proposals = state / "proposals.jsonl"
    if proposals.exists():
        kept = [line for line in raw_lines(proposals)
                if parse_ts(json.loads(line)["timestamp"]).timestamp() < boundary_ts]
        write_lines(proposals, kept)
    untouched = [name for name in ("goal.json", "approvals.json", "waivers.json", "aggregates.json",
                                   "model_fit.json", "model_plan.json", "imported")
                 if (state / name).exists()]

    # ---- caches and broker files ----------------------------------------------
    for name in ("audit.json", "broker.json", "broker.token", "broker.log", "python"):
        (state / name).unlink(missing_ok=True)
    dossier_note = "absent"
    if paths.dossier.exists():
        if "frames" in kept_events[-1]:
            try:
                from assay.perception import build_scene_dossier
                build_scene_dossier(paths, kept_events)
                dossier_note = "rebuilt from the truncated journal with assay.perception.build_scene_dossier"
            except Exception as error:  # noqa: BLE001 - a cache; absence is safe
                paths.dossier.unlink(missing_ok=True)
                dossier_note = f"removed (rebuild failed: {type(error).__name__}: {error})"
        else:
            paths.dossier.unlink(missing_ok=True)
            dossier_note = "removed (no grid frames)"

    # ---- chain ---------------------------------------------------------------
    last_id, head = compute_chain(paths)
    assert last_id == n, (last_id, n)
    atomic_json(chain_path(paths), {"event_id": last_id, "head": head})

    # ---- run-root files ---------------------------------------------------------
    for rel in created_after:
        (dest / rel).unlink(missing_ok=True)
    for name in LAUNCHER_ARTIFACTS:
        if (dest / name).exists():
            (dest / name).unlink()
            notes_lines.append(f"launcher artifact removed: {name}")
    restored: list[str] = []
    unresolved: list[str] = []
    source_by_rel: dict[str, str] = {}
    for record in kept_activity:  # the LAST admission of a source path wins
        if record.get("kind") == "verifier_admitted":
            source_by_rel[str(record.get("source"))] = ("verifiers", str(record.get("hash")))
        elif record.get("kind") == "channel_declared" and record.get("source"):
            source_by_rel[str(record.get("source"))] = ("channels", str(record.get("hash")))
    for rel in modified_after:
        hit = source_by_rel.get(rel)
        copy = (state / hit[0] / f"{hit[1]}.py") if hit else None
        if copy is not None and copy.exists():
            (dest / rel).write_bytes(copy.read_bytes())
            restored.append(f"{rel} <- .assay/{hit[0]}/{hit[1][:12]}….py")
        elif args.drop_unresolved:
            (dest / rel).unlink(missing_ok=True)
            unresolved.append(f"{rel} (REMOVED: post-cut content, no pre-cut copy)")
        else:
            unresolved.append(f"{rel} (KEPT with post-cut content)")
    for root, dirs, files in os.walk(dest, topdown=False):
        for d in dirs:
            path = Path(root) / d
            if path.name != ".assay" and not any(path.iterdir()):
                path.rmdir()

    # ---- NOTES.md ------------------------------------------------------------
    if args.notes:
        paths.notes.write_text(args.notes.read_text())
        notes_source = f"installed from {args.notes} (notes_at.py reconstruction)"
    else:
        archive = levels / f"level-{levels_at}.md"
        if archive.exists():
            paths.notes.write_text(archive.read_text())
            notes_source = (f"DEVIATION: installed .assay/levels/level-{levels_at}.md, the notes as archived "
                            f"when level {levels_at} completed, not the notes at e{n}")
        else:
            paths.notes.unlink(missing_ok=True)
            notes_source = "DEVIATION: no reconstruction and no level archive; NOTES.md removed"
    notes_sha = sha256_path(paths.notes) if paths.notes.exists() else None

    # ---- registry edit (modules) ---------------------------------------------
    registry_before = read_json(paths.registry, None)
    hash_before = registry_hash_of(registry_before)
    registry_after = registry_before
    if args.module or args.module_mode:
        if not isinstance(registry_before, dict):
            parser.error("the source run has no pinned registry; modules need one")
        edited = json.loads(json.dumps(registry_before))
        modules = list(edited.get("modules") or [])
        for module in args.module:
            module_path = str(module.resolve())
            if module_path not in modules:
                modules.append(module_path)
        if modules:
            edited["modules"] = modules
        modes = dict(edited.get("module_modes") or {})
        for item in args.module_mode:
            name, _, mode = item.partition("=")
            modes[name] = mode
        if modes:
            edited["module_modes"] = modes
        registry_after = validate_registry(edited)
        atomic_json(paths.registry, registry_after)
        pin_external_modules(paths, registry_after)
        config = read_json(paths.config, {})
        config["registry_hash"] = registry_hash_of(registry_after)
        atomic_json(paths.config, config)
    hash_after = registry_hash_of(registry_after)
    registry_check = "not checked (no --registry)"
    if args.registry:
        spec = load_registry_file(args.registry)
        if spec != registry_after:
            print("ERROR: --registry does not canonicalize to the pinned registry", file=sys.stderr)
            print(json.dumps({"file": spec, "pinned": registry_after}, indent=1, sort_keys=True), file=sys.stderr)
            return 1
        registry_check = f"{args.registry} canonicalizes to the pinned registry (ok)"
    module_digests = {str(m): sha256_path(m) for m in args.module}

    # ---- anchors -------------------------------------------------------------
    anchor_dir: Path = args.anchor_dir.resolve()
    anchor_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ASSAY_ANCHOR_DIR"] = str(anchor_dir)
    fresh_anchor = anchor_file(paths)
    anchor_warning = ""
    if fresh_anchor.exists():
        anchor_warning = f"WARNING: {fresh_anchor} already exists; the anchor dir is not fresh"
    default_anchor = Path.home() / ".assay" / "anchors" / fresh_anchor.name
    if default_anchor.exists():
        anchor_warning += f" WARNING: {default_anchor} exists for this run path"

    # ---- provenance ----------------------------------------------------------
    config = read_json(paths.config, {})
    game = config.get("game_id")
    adapter = args.adapter or config.get("adapter")
    registry_for_cmd = str(args.registry.resolve()) if args.registry else "<registry file equal to .assay/registry.json>"
    truncated_sha = sha256_path(state / "events.jsonl")
    paid = sum(1 for event in kept_events if event.get("counts_action"))
    provenance = args.provenance or (dest.parent / f"{dest.name}.provenance" / "RESUME_PROVENANCE.md")
    provenance.parent.mkdir(parents=True, exist_ok=True)
    notes_report = ""
    if args.notes_report and args.notes_report.exists():
        notes_report = args.notes_report.read_text()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# Resume provenance: {dest.name}",
        "",
        f"Prepared {now} by tools/resume_at.py (assay repo, branch exp/2026-10).",
        "This file lives outside the run directory on purpose.",
        "",
        "## Source (read-only, unchanged)",
        f"- run dir: {source}",
        f"- game: {game}, seed {config.get('seed')}, mode {config.get('mode')}",
        f"- source events.jsonl: {len(events)} events (e0..e{len(events) - 1}), sha256 {source_journal_sha}",
        f"- source final state: {events[-1]['state']} at {events[-1]['levels_completed']}/{events[-1]['win_levels']} levels",
        "",
        "## Cut point",
        f"- kept prefix: e0..e{n} inclusive ({n + 1} events, {paid} paid actions)",
        f"- e{n}: {cut['action']} {json.dumps(cut.get('data'))} at {cut['timestamp']}, "
        f"levels_completed {levels_at}/{cut['win_levels']}, state {cut['state']}",
        f"- e{n} note: {json.dumps(cut.get('note'))}",
        f"- boundary (first future instant, {boundary_note}): {boundary.isoformat() if boundary else 'none (e' + str(n) + ' is the final event)'}",
        f"- next event e{n + 1} at: {next_event.isoformat() if next_event else 'none'}",
        f"- truncated events.jsonl: {n + 1} lines, sha256 {truncated_sha}",
        f"- chain.json recomputed with assay.integrity.compute_chain: event_id {last_id}, head {head}",
        "",
        "## What was cut or rebuilt",
        f"- mutations.jsonl: kept mutation_id <= {mutation_cut} ({len(kept_mutations)} of {len(mutation_lines)})",
        f"- activity.jsonl: {activity_rule}; later offline commands (status, python, audit) are dropped with it",
        f"- receipts removed: {removed['receipts']}, images removed: {removed['images']}, "
        f"level archives removed: {removed['levels'] or 'none'}",
        f"- verifiers: kept {len(keep_verifiers)} content-addressed copies admitted or graded in the prefix, "
        f"removed {removed['verifiers']}; stats.json recomputed from the prefix's grade records "
        f"({len(stats)} hashes)",
        f"- channels: kept {len(declared)} declared in the prefix, removed {removed['channels']} entries",
        f"- hazards: removed {removed['hazards']} tags with evidence after e{n}",
        f"- dossier.json: {dossier_note}",
        "- removed: audit.json, broker.json, broker.token, broker.log, python",
        f"- state files left as copied (none expected): {untouched or 'none'}",
        f"- run-root files created after the boundary, removed: {len(created_after)}",
        *(f"    - {rel}" for rel in sorted(created_after)[:60]),
        *(["    - …"] if len(created_after) > 60 else []),
        f"- run-root files that existed at the cut but were modified later, restored from the kernel's "
        f"content-addressed copy of the last pre-cut admission: {len(restored)}",
        *(f"    - {item}" for item in restored),
        f"- existed at the cut, modified later, no pre-cut copy: {unresolved or 'none'}",
        *(f"- {item}" for item in notes_lines),
        "",
        "## NOTES.md",
        f"- {notes_source}",
        f"- installed NOTES.md sha256: {notes_sha}",
        *(["", "notes_at.py report:", "```json", notes_report.rstrip(), "```"] if notes_report else []),
        "",
        "## Registry (normally immutable in place; edited only when modules are added)",
        f"- registry_hash before: {hash_before}",
        f"- registry_hash after:  {hash_after}",
        f"- modules added: {[str(m.resolve()) for m in args.module] or 'none'}",
        *(f"    - sha256 {digest}  {path}" for path, digest in module_digests.items()),
        f"- module_modes set: {args.module_mode or 'none'}",
        *(["- the module files were pinned into .assay/modules/ exactly as `assay start` would have done"]
          if args.module else ["- no module added, nothing pinned into .assay/modules/"]),
        f"- config.json registry_hash updated to match: {hash_before != hash_after}",
        f"- {registry_check}",
        "",
        "## How to resume (required environment)",
        "The run MUST be resumed with ASSAY_ANCHOR_DIR pointing at a fresh directory: the kernel keys",
        "anchors by run path and compares the journal prefix against the latest anchored head, so any",
        "stale anchor for this path would report DIVERGED against the shortened journal.",
        "",
        "```bash",
        f"export ASSAY_ANCHOR_DIR={anchor_dir}",
        "# python3 on PATH must serve the kernel AND import arc_agi (see tools/run_player.sh)",
        f"cd {dest}",
        f"{REPO}/bin/assay start {game} --adapter {adapter} --registry {registry_for_cmd}",
        "# expected: RECOVERED | <game> | local simulator | replayed <paid> paid actions, then STATUS",
        "```",
        f"- anchor file this run will write: {fresh_anchor}",
        *([f"- {anchor_warning.strip()}"] if anchor_warning.strip() else ["- anchor dir was empty for this run path (fresh)"]),
        "",
        "## Caveats",
        "- `assay start` replays the kept mutations through the adapter and refuses with LOCAL_REPLAY_DIVERGED",
        "  if the cached game or engine no longer reproduces a recorded observation.",
        "- The agent's own files in the run root are restored only where the kernel kept a pre-cut copy;",
        "  anything listed as UNRESOLVED above must be judged by the operator before launch.",
        *(["- The pinned module file is readable inside .assay/modules/ like any module the kernel pins."]
          if args.module else []),
    ]
    provenance.write_text("\n".join(lines) + "\n")
    print(f"prepared {dest}")
    print(f"  prefix e0..e{n} ({paid} paid), chain head {head[:16]}…, journal sha256 {truncated_sha[:16]}…")
    print(f"  removed: {removed}, run-root created-after {len(created_after)}, restored {len(restored)}, unresolved {unresolved}")
    print(f"  registry_hash {hash_before[:12] if hash_before else None}… -> {hash_after[:12] if hash_after else None}…; {registry_check}")
    print(f"  provenance: {provenance}")
    if anchor_warning:
        print("  " + anchor_warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
