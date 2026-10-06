#!/usr/bin/env python3
"""e3_prepare.py: build one E3 import run from a finished E1 gated run.

    tools/e3_prepare.py GAME [--source DIR] [--out-root DIR] [--registry FILE]
        [--adapter SPEC] [--cap N] [--python PATH] [--anchor-dir DIR]
        [--source-anchor-dir DIR] [--source-state-dir .assay|.arc] [--replace]
        [--keep-broker]
    tools/e3_prepare.py GAME --check

E3 asks whether an agent's OWN exported knowledge, imported FOREIGN by the
kernel exactly as shipped, changes how a fresh agent plays the same game. Per
game this tool:

1. refuses unless the source run is finished (journal state WIN, or paid
   actions at the registry cap), and never writes into the source directory:
   the kernel's `assay export` appends a knowledge_export record to the run's
   activity log, so the export runs on a byte-identical scratch copy that is
   deleted afterwards, and the source tree is hashed before and after;
2. runs `assay export --out <out-root>/exports/<game>-from-gated-s1/assay_knowledge.json`
   on that copy (the kernel's export: notes, Verified lines, verifier sources
   with their graded stats, model.py, rules.py, hazard tags, journal digest);
3. creates <out-root>/<game>-import-s1 with ONE kernel command,
   `assay start GAME --adapter ... --registry ... --import <artifact>`, the
   same registry and constitution the cold gated runs use. The import happens
   at run start by kernel design (a directory that already owns a run refuses
   --import), so the player's own later `assay start` resumes this run;
4. verifies with `assay status` that the FOREIGN block is present, that every
   file in the new run directory is either the kernel's start-time footprint or
   a byte-for-byte copy of an artifact entry, that the journal holds exactly the
   START event, and that `assay audit` is CLEAN;
5. stops the broker it started (the next `assay start` recovers by replaying 0
   paid actions) and writes <out-root>/<game>-import-s1.provenance/PROVENANCE.md.

No human-written notes, no hints: the only content that reaches the new run is
what the kernel's own import placed there. Standard library only. The kernel is
driven through src/assay_cli.py under --python (bin/assay execs exactly that
when a compatible python3 leads PATH; the repo denies bin/assay to agents).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "src" / "assay_cli.py"
DEFAULT_PYTHON = Path("/Users/mohsenarjmandi/workspace/PRO-LONG/.venv/bin/python3")
DEFAULT_SOURCES = Path("/Users/mohsenarjmandi/workspace/assay-runs/e1")
DEFAULT_SOURCE_ANCHORS = DEFAULT_SOURCES / "anchors"
DEFAULT_OUT = Path("/Users/mohsenarjmandi/workspace/assay-runs/e3")
ADAPTER = f"{REPO}/bench/arcagi/adapter.py:factory"
GAMES = {"ft09": 200, "tr87": 1500}
CHAIN_SEED = "assay-chain-v1"
KNOWLEDGE_FORMAT = 1
VERIFIER_CAP = 20  # the kernel exports at most this many verifier sources

# The kernel's start-time footprint inside .assay/ (what `assay start` plus one
# `status` and one `audit` write), and what the import places. Anything else
# in the new run directory is a failure: it came from somewhere else.
KERNEL_STATE_FILES = {
    "config.json", "registry.json", "events.jsonl", "activity.jsonl", "mutations.jsonl",
    "NOTES.md", "chain.json", "broker.json", "broker.log", "broker.token", "owner.json",
    "python", "run.lock", "dossier.json", "audit.json",
}
KERNEL_STATE_DIRS = {"images", "levels", "receipts", "recordings", "channels", "verifiers", "modules"}
IMPORT_STATE_FILES = {"PRIOR-NOTES.md", "imported/knowledge.json", "hazards.json"}
PRIOR_HEADER = "# PRIOR-NOTES (FOREIGN — imported knowledge)"


class Refusal(Exception):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def tree_snapshot(root: Path) -> dict[str, str]:
    """relative path -> sha256 for every regular file under root (symlinks as files)."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() or path.is_symlink():
            if path.is_symlink():
                out[str(path.relative_to(root))] = "symlink:" + os.readlink(path)
            else:
                out[str(path.relative_to(root))] = sha256_path(path)
    return out


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def recompute_chain(events_path: Path) -> tuple[int, str]:
    """The kernel's documented chain: head_0 = sha256(seed), head_n = sha256(head_{n-1} || line_n)."""
    head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
    last = -1
    for line in events_path.read_text().splitlines():
        if not line.strip():
            continue
        head = hashlib.sha256(head.encode() + line.encode()).hexdigest()
        last += 1
    return last, head


def prefix_head(events_path: Path, upto: int) -> str | None:
    head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
    lines = [line for line in events_path.read_text().splitlines() if line.strip()]
    if upto >= len(lines):
        return None
    for line in lines[: upto + 1]:
        head = hashlib.sha256(head.encode() + line.encode()).hexdigest()
    return head


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Kernel:
    """Runs the kernel CLI (src/assay_cli.py) under one interpreter."""

    def __init__(self, python: Path, anchor_dir: Path):
        # absolute, NOT resolved: a venv's python3 is a symlink, and resolving it
        # would leave the venv's site-packages (numpy, pillow, arc_agi) behind.
        self.python = python.absolute()
        self.anchor_dir = anchor_dir
        self.log: list[dict] = []
        probe = subprocess.run(
            [str(self.python), "-c", "import sys; assert sys.version_info >= (3, 12); import numpy, PIL"],
            capture_output=True, text=True)
        if probe.returncode != 0:
            raise Refusal(f"{self.python} does not serve the kernel (python 3.12+, numpy, pillow): {probe.stderr.strip()[-300:]}")

    def env(self, anchor_dir: Path | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = f"{self.python.parent}:{env.get('PATH', '')}"
        env["ASSAY_ANCHOR_DIR"] = str(anchor_dir or self.anchor_dir)
        env["_ZO_DOCTOR"] = "0"
        return env

    def run(self, run_dir: Path, *args: str, anchor_dir: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [str(self.python), str(CLI), "--run-dir", str(run_dir), *args]
        done = subprocess.run(cmd, capture_output=True, text=True, env=self.env(anchor_dir), timeout=600)
        self.log.append({"argv": cmd, "exit": done.returncode, "anchor_dir": str(anchor_dir or self.anchor_dir)})
        if check and done.returncode != 0:
            raise Refusal(f"`assay {' '.join(args)}` failed in {run_dir} (exit {done.returncode}):\n{done.stdout}\n{done.stderr}")
        return done

    @staticmethod
    def shown(run_dir: Path, *args: str) -> str:
        """The command as an operator types it (bin/assay execs python3 src/assay_cli.py)."""
        return "assay --run-dir " + " ".join([str(run_dir), *args])


def source_facts(source: Path, state_name: str) -> dict:
    state = source / state_name
    if not source.is_dir():
        raise Refusal(f"source run directory does not exist: {source}")
    if not state.is_dir():
        hint = ""
        if state_name == ".assay" and (source / ".arc").is_dir():
            hint = " (it has .arc, the pre-rename state dir; pass --source-state-dir .arc)"
        raise Refusal(f"source has no {state_name}/ state directory: {source}{hint}")
    config = json.loads((state / "config.json").read_text())
    events = read_jsonl(state / "events.jsonl")
    if not events:
        raise Refusal(f"source journal is empty: {state / 'events.jsonl'}")
    for index, event in enumerate(events):
        if event.get("id") != index:
            raise Refusal(f"source journal is not contiguous at line {index + 1}")
    registry = json.loads((state / "registry.json").read_text()) if (state / "registry.json").exists() else None
    cap = None
    if isinstance(registry, dict):
        cap = (registry.get("budget") or {}).get("actions")
    last = events[-1]
    paid = sum(1 for event in events if event.get("counts_action"))
    chain = json.loads((state / "chain.json").read_text()) if (state / "chain.json").exists() else {}
    recomputed_id, recomputed_head = recompute_chain(state / "events.jsonl")
    broker = json.loads((state / "broker.json").read_text()) if (state / "broker.json").exists() else {}
    verifier_dir = state / "verifiers"
    verifier_files = sorted(p.name for p in verifier_dir.glob("*.py")) if verifier_dir.is_dir() else []
    return {
        "dir": str(source), "state_dir": state_name,
        "game_id": config.get("game_id"), "harness": config.get("harness"), "adapter": config.get("adapter"),
        "registry_hash": config.get("registry_hash"), "binding_hash": config.get("binding_hash"),
        "created_at": config.get("created_at"), "mode": config.get("mode"),
        "events": len(events), "paid": paid, "final_state": last.get("state"),
        "levels_completed": last.get("levels_completed"), "win_levels": last.get("win_levels"),
        "cap": cap, "journal_sha256": sha256_path(state / "events.jsonl"),
        "chain_stored": chain, "chain_recomputed": {"event_id": recomputed_id, "head": recomputed_head},
        "chain_consistent": chain.get("event_id") == recomputed_id and chain.get("head") == recomputed_head,
        "broker_status": broker.get("status"), "broker_pid": broker.get("pid"),
        "notes_present": (state / "NOTES.md").exists(),
        "verifier_sources": len(verifier_files), "model_py": (source / "model.py").exists(),
        "rules_py": (source / "rules.py").exists(),
        "hazards": len(read_jsonl_or_json(state / "hazards.json")),
    }


def read_jsonl_or_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def finished(facts: dict) -> tuple[bool, str]:
    if facts["final_state"] == "WIN":
        return True, f"journal state WIN at e{facts['events'] - 1}, {facts['paid']} paid actions"
    cap = facts["cap"]
    if cap is not None and facts["paid"] >= int(cap):
        return True, f"budget exhausted: {facts['paid']} paid actions at cap {cap}, state {facts['final_state']}"
    remaining = "unknown" if cap is None else int(cap) - facts["paid"]
    return False, (f"not finished: state {facts['final_state']}, {facts['paid']} paid actions, "
                   f"cap {cap}, budget remaining {remaining}")


def source_anchor_report(source: Path, anchor_root: Path, events_path: Path) -> dict:
    digest = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:24]
    file = anchor_root / f"{digest}.jsonl"
    if not file.exists():
        return {"file": str(file), "present": False}
    anchors = read_jsonl(file)
    if not anchors:
        return {"file": str(file), "present": True, "count": 0}
    latest = anchors[-1]
    replay = prefix_head(events_path, int(latest["event_id"]))
    return {"file": str(file), "present": True, "count": len(anchors), "latest_event": latest.get("event_id"),
            "latest_head": latest.get("head"), "intact": replay == latest.get("head")}


def export_from_copy(kernel: Kernel, source: Path, state_name: str, work: Path, artifact: Path) -> dict:
    copy = work / "source-copy"
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(source, copy, symlinks=True)
    renamed = False
    if state_name != ".assay":
        (copy / state_name).rename(copy / ".assay")
        renamed = True
    scratch_anchors = work / "scratch-anchors"
    audit = kernel.run(copy, "audit", anchor_dir=scratch_anchors)
    exported = kernel.run(copy, "export", "--out", str(artifact), anchor_dir=scratch_anchors)
    shutil.rmtree(copy)
    shutil.rmtree(scratch_anchors, ignore_errors=True)
    return {
        "copy": str(copy), "state_dir_renamed_to_assay": renamed,
        "audit_command": Kernel.shown(copy, "audit"), "audit_stdout": audit.stdout.strip(),
        "export_command": Kernel.shown(copy, "export", "--out", str(artifact)),
        "export_stdout": exported.stdout.strip(),
    }


def artifact_summary(artifact: Path) -> dict:
    raw = artifact.read_bytes()
    knowledge = json.loads(raw)
    if knowledge.get("knowledge_format") != KNOWLEDGE_FORMAT:
        raise Refusal(f"artifact has knowledge_format {knowledge.get('knowledge_format')}, expected {KNOWLEDGE_FORMAT}")
    digest = knowledge.get("digest") or {}
    return {
        "path": str(artifact), "sha256": sha256_bytes(raw), "bytes": len(raw),
        "game_id": knowledge.get("game_id"), "registry_hash": knowledge.get("registry_hash"),
        "binding_hash": knowledge.get("binding_hash"),
        "digest": {k: digest.get(k) for k in ("events", "paid", "final_state", "levels_completed", "journal_sha256")},
        "notes_chars": len(knowledge.get("notes") or ""),
        "verified_lines": len(knowledge.get("verified_lines") or []),
        "verifiers": [str(v.get("hash")) for v in knowledge.get("verifiers") or []],
        "model": bool((knowledge.get("model") or {}).get("source")) if isinstance(knowledge.get("model"), dict) else False,
        "rules": bool(knowledge.get("rules")),
        "hazards": len(knowledge.get("hazards") or []),
        "_knowledge": knowledge,
    }


def classify_tree(run_dir: Path, knowledge: dict) -> tuple[dict[str, str], list[str]]:
    """Every file in the new run dir: kernel-start, import (byte-checked), or unexpected."""
    classes: dict[str, str] = {}
    problems: list[str] = []
    expected_root: dict[str, str] = {}
    for entry in knowledge.get("verifiers") or []:
        if entry.get("source"):
            expected_root[f"imported_verifiers/{str(entry.get('hash', 'unknown'))[:16]}.py"] = str(entry["source"])
    model = knowledge.get("model")
    if isinstance(model, dict) and model.get("source"):
        expected_root["imported_model.py"] = str(model["source"])
    if knowledge.get("rules"):
        expected_root["imported_rules.py"] = str(knowledge["rules"])
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(run_dir))
        if rel.startswith(".assay/"):
            inner = rel[len(".assay/"):]
            top = inner.split("/", 1)[0]
            if inner in IMPORT_STATE_FILES:
                classes[rel] = "import"
            elif inner in KERNEL_STATE_FILES or (top in KERNEL_STATE_DIRS and "/" in inner):
                classes[rel] = "kernel-start"
            else:
                classes[rel] = "UNEXPECTED"
                problems.append(f"unexpected file in state dir: {rel}")
        elif rel in expected_root:
            if path.read_text() == expected_root[rel]:
                classes[rel] = "import (bytes match artifact)"
            else:
                classes[rel] = "UNEXPECTED (differs from artifact)"
                problems.append(f"{rel} differs from the artifact entry")
        else:
            classes[rel] = "UNEXPECTED"
            problems.append(f"unexpected file at run root: {rel}")
    for rel in expected_root:
        if rel not in classes:
            problems.append(f"artifact entry not placed by the import: {rel}")
    return classes, problems


def verify_import(kernel: Kernel, run_dir: Path, game: str, art: dict, status_out: str, audit_out: str) -> tuple[dict, list[str]]:
    knowledge = art["_knowledge"]
    problems: list[str] = []
    facts: dict = {}
    foreign = [line for line in status_out.splitlines() if line.startswith("FOREIGN |")]
    facts["foreign_lines"] = foreign
    if not any(f"imported knowledge from {game}" in line for line in foreign):
        problems.append("status lacks the FOREIGN header line")
    if art["notes_chars"] and not any("PRIOR-NOTES.md" in line and "Assumed here" in line for line in foreign):
        problems.append("status lacks the FOREIGN prior-notes line")
    if art["verifiers"] and not any("verifier candidate(s) in imported_verifiers/" in line for line in foreign):
        problems.append("status lacks the FOREIGN verifier line")
    if art["model"] and not any("imported_model.py carries NO batching rights" in line for line in foreign):
        problems.append("status lacks the FOREIGN model line")
    prior = run_dir / ".assay" / "PRIOR-NOTES.md"
    notes = str(knowledge.get("notes") or "")
    if notes.strip():
        if not prior.exists():
            problems.append("PRIOR-NOTES.md missing")
        else:
            text = prior.read_text()
            first = text.splitlines()[0] if text else ""
            facts["prior_notes_header"] = first
            if first != PRIOR_HEADER:
                problems.append(f"PRIOR-NOTES.md header is {first!r}")
            if "DEMOTED TO ASSUMED" not in text:
                problems.append("PRIOR-NOTES.md lacks the demotion sentence")
            if f"sha {art['sha256'][:12]}" not in text:
                problems.append("PRIOR-NOTES.md does not cite the artifact sha")
            if not text.endswith(notes):
                problems.append("PRIOR-NOTES.md body is not the artifact's notes")
    elif prior.exists():
        problems.append("PRIOR-NOTES.md present although the artifact has no notes")
    events = read_jsonl(run_dir / ".assay" / "events.jsonl")
    facts["events"] = len(events)
    if len(events) != 1 or events[0].get("action") != "START" or events[0].get("counts_action"):
        problems.append(f"journal should hold exactly the START event, has {len(events)}")
    activity = read_jsonl(run_dir / ".assay" / "activity.jsonl")
    imports = [rec for rec in activity if rec.get("kind") == "knowledge_import"]
    facts["knowledge_import_records"] = imports
    if len(imports) != 1:
        problems.append(f"expected one knowledge_import record, found {len(imports)}")
    elif imports[0].get("sha256") != art["sha256"]:
        problems.append("knowledge_import record sha256 differs from the artifact")
    stored = run_dir / ".assay" / "imported" / "knowledge.json"
    if not stored.exists():
        problems.append(".assay/imported/knowledge.json missing")
    elif json.loads(stored.read_text()) != knowledge:
        problems.append(".assay/imported/knowledge.json differs from the artifact")
    template = f"# Notes — {game}\n\n## Verified (cite event ids)\n\n## Assumed / open questions\n\n## Plan\n"
    kernel_notes = run_dir / ".assay" / "NOTES.md"
    facts["notes_is_kernel_template"] = kernel_notes.exists() and kernel_notes.read_text() == template
    if not facts["notes_is_kernel_template"]:
        problems.append(".assay/NOTES.md is not the kernel's empty template")
    config = json.loads((run_dir / ".assay" / "config.json").read_text())
    facts["registry_hash"] = config.get("registry_hash")
    facts["matching_registration"] = bool(imports) and bool(imports[0].get("matching_registration"))
    facts["audit_line"] = audit_out.strip().splitlines()[0] if audit_out.strip() else ""
    if not facts["audit_line"].startswith("AUDIT | CLEAN"):
        problems.append(f"audit is not CLEAN: {facts['audit_line']}")
    classes, tree_problems = classify_tree(run_dir, knowledge)
    facts["tree"] = classes
    problems.extend(tree_problems)
    return facts, problems


def stop_broker(run_dir: Path) -> dict:
    broker_file = run_dir / ".assay" / "broker.json"
    descriptor = json.loads(broker_file.read_text()) if broker_file.exists() else {}
    pid = descriptor.get("pid")
    if not isinstance(pid, int) or not pid_alive(pid):
        return {"pid": pid, "was_alive": False}
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and pid_alive(pid):
        time.sleep(0.1)
    return {"pid": pid, "was_alive": True, "stopped": not pid_alive(pid)}


def redact_token(text: str) -> tuple[str, str | None]:
    found = re.search(r"OWNER TOKEN \| (\S+) ", text)
    if not found:
        return text, None
    return text.replace(found.group(1), "[stored in OWNER_TOKEN, outside the run dir]"), found.group(1)


def write_provenance(prov: Path, record: dict) -> None:
    src, art, imp = record["source"], record["artifact"], record["import"]
    v = record["verification"]
    lines = [
        f"# E3 import run provenance: {record['game']}",
        "",
        f"Written {record['written']} by tools/e3_prepare.py (assay {record['assay_commit']}, branch {record['assay_branch']}).",
        f"Interpreter: {record['python']} ({record['python_version']}).",
        "",
        "## Source run (read-only, never modified)",
        "",
        f"- directory: `{src['dir']}` (state dir `{src['state_dir']}`, harness `{src['harness']}`, adapter `{src['adapter']}`)",
        f"- game {src['game_id']}, mode {src['mode']}, created {src['created_at']}",
        f"- registry_hash {src['registry_hash']}",
        f"- binding_hash {src['binding_hash']}",
        f"- journal: {src['events']} events, {src['paid']} paid actions, final state {src['final_state']}, "
        f"levels {src['levels_completed']}/{src['win_levels']}, cap {src['cap']}, sha256 {src['journal_sha256']}",
        f"- finished rule: {record['finished_reason']}",
        f"- chain head (stored {src['state_dir']}/chain.json): e{src['chain_stored'].get('event_id')} {src['chain_stored'].get('head')}",
        f"- chain head recomputed from the journal with the documented formula: e{src['chain_recomputed']['event_id']} "
        f"{src['chain_recomputed']['head']} (consistent: {src['chain_consistent']})",
        f"- source anchors: {json.dumps(record['source_anchors'])}",
        f"- source tree: {record['source_files']} files, snapshot sha256 {record['source_snapshot_sha256']} before, "
        f"{record['source_snapshot_sha256_after']} after (unchanged: {record['source_unchanged']}); the per-file list is in source_snapshot.json",
        f"- source contents the export reads: NOTES.md present {src['notes_present']}, verifier sources {src['verifier_sources']} "
        f"(kernel exports at most {VERIFIER_CAP}), model.py {src['model_py']}, rules.py {src['rules_py']}, hazard tags {src['hazards']}",
        "",
        "## Export (on a byte-identical scratch copy, deleted afterwards)",
        "",
        "The kernel's `assay export` appends a knowledge_export record to the run's activity log and every",
        "command appends command_start/command_end records, so the export ran on a copy of the source.",
        f"- copy: `{record['export']['copy']}` (state dir renamed .arc to .assay: {record['export']['state_dir_renamed_to_assay']})",
        f"- `{record['export']['audit_command']}`",
        f"  -> {record['export']['audit_stdout'].splitlines()[0] if record['export']['audit_stdout'] else ''}",
        "  (anchors are keyed by the run root path, so a copy reports anchors none; the source's own anchors are above)",
        f"- `{record['export']['export_command']}`",
        f"  -> {record['export']['export_stdout']}",
        "",
        "## Export artifact",
        "",
        f"- path: `{art['path']}`",
        f"- sha256 {art['sha256']} ({art['bytes']} bytes), knowledge_format {KNOWLEDGE_FORMAT}",
        f"- game_id {art['game_id']}, registry_hash {art['registry_hash']}, binding_hash {art['binding_hash']}",
        f"- journal digest: {json.dumps(art['digest'])}",
        f"- notes: {art['notes_chars']} chars, {art['verified_lines']} Verified lines citing event ids (demoted to Assumed on import)",
        f"- verifier sources: {len(art['verifiers'])} (of {src['verifier_sources']} in the source; kernel cap {VERIFIER_CAP}, lexical order)",
        f"- model.py: {art['model']}, rules.py: {art['rules']}, hazard tags: {art['hazards']}",
        "",
        "## Import run",
        "",
        f"- directory: `{imp['run_dir']}`",
        f"- `{imp['start_command']}` (ASSAY_ANCHOR_DIR={imp['anchor_dir']})",
        f"- start output (owner token redacted, kept 0600 in this provenance dir):",
        "",
        *[f"      {line}" for line in imp["start_stdout_redacted"].splitlines() if line.startswith(("IMPORTED", "STARTED", "OWNER", "RECOVERED", "RESUMED"))],
        "",
        f"- registry_hash of the new run {v['registry_hash']}; matching registration (hazard carve-out applies): {v['matching_registration']}",
        f"- knowledge_import record: {json.dumps(v['knowledge_import_records'])}",
        "",
        "## Verification after the import",
        "",
        f"- `{imp['status_command']}` FOREIGN block:",
        "",
        *[f"      {line}" for line in v["foreign_lines"]],
        "",
        f"- PRIOR-NOTES.md header: `{v.get('prior_notes_header', '(no notes imported)')}`, body is the artifact's notes byte for byte",
        f"- journal events: {v['events']} (START only, no paid action)",
        f"- .assay/NOTES.md is the kernel's empty template: {v['notes_is_kernel_template']}",
        f"- `{imp['audit_command']}` -> {v['audit_line']}",
        f"- broker: {json.dumps(record['broker'])} (the player's `assay start` recovers by replaying 0 paid actions)",
        "",
        "## Imported items (every file in the run directory, classified)",
        "",
        "Every file is the kernel's start-time footprint or a byte-for-byte copy of an artifact entry.",
        "",
        *[f"- `{rel}`: {cls}" for rel, cls in v["tree"].items() if not rel.startswith(".assay/images/") and not rel.startswith(".assay/levels/")],
        f"- `.assay/images/*`, `.assay/levels/*`: {sum(1 for rel in v['tree'] if rel.startswith(('.assay/images/', '.assay/levels/')))} kernel-start render files",
        "",
        f"Problems: {record['problems'] or 'none'}",
        "",
    ]
    prov.mkdir(parents=True, exist_ok=True)
    (prov / "PROVENANCE.md").write_text("\n".join(lines))
    (prov / "provenance.json").write_text(json.dumps({k: v for k, v in record.items() if k != "artifact_knowledge"}, indent=1, sort_keys=True) + "\n")


def prepare(args: argparse.Namespace) -> int:
    game = args.game
    cap = args.cap if args.cap is not None else GAMES.get(game)
    if cap is None:
        raise Refusal(f"unknown game {game}: pass --cap and --registry")
    registry = args.registry or (REPO / "bench" / "arcagi" / f"registry_e1_gated_{cap}.json")
    if not Path(registry).exists():
        raise Refusal(f"registry not found: {registry}")
    source = (args.source or (DEFAULT_SOURCES / f"{game}-gated-s1")).resolve()
    out_root = args.out_root.resolve()
    run_dir = out_root / f"{game}-import-s1"
    prov = out_root / f"{game}-import-s1.provenance"
    export_dir = out_root / "exports" / f"{game}-from-gated-s1"
    artifact = export_dir / "assay_knowledge.json"
    anchor_dir = (args.anchor_dir or (out_root / "anchors")).resolve()
    kernel = Kernel(args.python, anchor_dir)

    facts = source_facts(source, args.source_state_dir)
    if facts["game_id"] != game:
        raise Refusal(f"source owns {facts['game_id']}, not {game}")
    done, reason = finished(facts)
    if not done:
        raise Refusal(f"source run {source} is {reason}; E3 imports only from a finished run")
    if facts["broker_pid"] and facts["broker_status"] not in ("FINISHED", None) and pid_alive(int(facts["broker_pid"])):
        raise Refusal(f"source broker pid {facts['broker_pid']} is still alive (status {facts['broker_status']})")

    if run_dir.exists():
        if not args.replace:
            raise Refusal(f"{run_dir} already exists; remove it or pass --replace (only a run with no paid action is replaced)")
        existing = read_jsonl(run_dir / ".assay" / "events.jsonl")
        if any(event.get("counts_action") for event in existing):
            raise Refusal(f"{run_dir} has paid actions; refusing to replace a played run")
        stop_broker(run_dir)
        shutil.rmtree(run_dir)
        if prov.exists():
            shutil.rmtree(prov)
    if artifact.exists() and not args.replace:
        raise Refusal(f"{artifact} already exists; remove it or pass --replace")

    snapshot = tree_snapshot(source)
    snapshot_sha = sha256_bytes(json.dumps(snapshot, sort_keys=True).encode())
    source_anchors = source_anchor_report(source, args.source_anchor_dir, source / args.source_state_dir / "events.jsonl")

    export_dir.mkdir(parents=True, exist_ok=True)
    work = export_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        export = export_from_copy(kernel, source, args.source_state_dir, work, artifact)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    after = tree_snapshot(source)
    after_sha = sha256_bytes(json.dumps(after, sort_keys=True).encode())
    if after != snapshot:
        changed = sorted(set(snapshot) ^ set(after) | {k for k in snapshot if k in after and snapshot[k] != after[k]})
        raise Refusal(f"source tree changed during export: {changed[:10]}")
    art = artifact_summary(artifact)
    if art["game_id"] != game:
        raise Refusal(f"artifact game_id {art['game_id']} is not {game}")
    if art["digest"]["journal_sha256"] != facts["journal_sha256"]:
        raise Refusal("artifact journal_sha256 differs from the source journal")

    prov.mkdir(parents=True, exist_ok=True)
    anchor_dir.mkdir(parents=True, exist_ok=True)
    start_args = ["start", game, "--adapter", args.adapter, "--registry", str(Path(registry).resolve()), "--import", str(artifact)]
    started = kernel.run(run_dir, *start_args, check=False)
    redacted, token = redact_token(started.stdout)
    if token:
        token_file = prov / "OWNER_TOKEN"
        token_file.write_text(token + "\n")
        token_file.chmod(0o600)
    (prov / "start.stdout.txt").write_text(redacted + ("\n--- stderr ---\n" + started.stderr if started.stderr.strip() else ""))
    if started.returncode != 0 or "IMPORTED |" not in started.stdout or "STARTED |" not in started.stdout:
        raise Refusal(f"`assay start --import` did not import (exit {started.returncode}); see {prov / 'start.stdout.txt'}")
    status = kernel.run(run_dir, "status")
    audit = kernel.run(run_dir, "audit")
    (prov / "status.txt").write_text(status.stdout)
    (prov / "audit.txt").write_text(audit.stdout)
    verification, problems = verify_import(kernel, run_dir, game, art, status.stdout, audit.stdout)
    if not verification["matching_registration"] and not args.allow_registry_mismatch:
        # E3 proper imports a run played under the SAME registry (the kernel's
        # matching_registration, which also decides whether hazard tags arrive
        # active). A mismatch means the source is not the gated run this
        # design names: undo the start and refuse.
        stop_broker(run_dir)
        shutil.rmtree(run_dir)
        raise Refusal(
            f"artifact registry_hash {art['registry_hash']} does not match the import run's "
            f"{verification['registry_hash']} (the source was not played under {registry}); "
            "pass --allow-registry-mismatch only for a stand-in verification"
        )
    broker = {"kept": True} if args.keep_broker else stop_broker(run_dir)
    if not args.keep_broker:
        again = kernel.run(run_dir, "status", check=False)
        broker["status_after_stop"] = again.returncode == 0 and "FOREIGN |" in again.stdout
        if not broker["status_after_stop"]:
            problems.append("status failed after the broker stop")
    final_snapshot = tree_snapshot(source)
    unchanged = final_snapshot == snapshot
    if not unchanged:
        problems.append("source tree changed after the import step")

    record = {
        "game": game, "written": now(), "python": str(kernel.python),
        "python_version": subprocess.run([str(kernel.python), "-c", "import sys; print(sys.version.split()[0])"], capture_output=True, text=True).stdout.strip(),
        "assay_commit": subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip() or "unknown",
        "assay_branch": subprocess.run(["git", "-C", str(REPO), "branch", "--show-current"], capture_output=True, text=True).stdout.strip() or "unknown",
        "source": facts, "finished_reason": reason, "source_anchors": source_anchors,
        "source_files": len(snapshot), "source_snapshot_sha256": snapshot_sha,
        "source_snapshot_sha256_after": sha256_bytes(json.dumps(final_snapshot, sort_keys=True).encode()),
        "source_unchanged": unchanged,
        "export": export, "artifact": {k: v for k, v in art.items() if k != "_knowledge"},
        "import": {
            "run_dir": str(run_dir), "anchor_dir": str(anchor_dir), "registry": str(Path(registry).resolve()),
            "adapter": args.adapter, "start_command": Kernel.shown(run_dir, *start_args),
            "start_stdout_redacted": redacted, "status_command": Kernel.shown(run_dir, "status"),
            "audit_command": Kernel.shown(run_dir, "audit"), "owner_token_stored": bool(token),
        },
        "verification": verification, "broker": broker, "problems": problems, "kernel_calls": kernel.log,
    }
    (prov / "source_snapshot.json").write_text(json.dumps(snapshot, indent=0, sort_keys=True) + "\n")
    write_provenance(prov, record)
    (prov / "tree.json").write_text(json.dumps(verification.get("tree", {}), indent=1, sort_keys=True) + "\n")
    print(f"E3 PREPARE | {game} | source {source} ({reason})")
    print(f"E3 PREPARE | artifact {artifact} sha256 {art['sha256'][:16]}... | notes {art['notes_chars']} chars, "
          f"{art['verified_lines']} Verified lines, {len(art['verifiers'])} verifiers, model {art['model']}, hazards {art['hazards']}")
    for line in verification["foreign_lines"]:
        print(f"E3 PREPARE | {line}")
    print(f"E3 PREPARE | {verification['audit_line']}")
    print(f"E3 PREPARE | source unchanged {unchanged} | broker {broker} | provenance {prov / 'PROVENANCE.md'}")
    if problems:
        print("E3 PREPARE | PROBLEMS:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"E3 PREPARE | READY | {run_dir}")
    return 0


def check(args: argparse.Namespace) -> int:
    out_root = args.out_root.resolve()
    run_dir = out_root / f"{args.game}-import-s1"
    prov = out_root / f"{args.game}-import-s1.provenance"
    problems = []
    if not (run_dir / ".assay" / "imported" / "knowledge.json").exists():
        problems.append("no .assay/imported/knowledge.json (the run was not prepared with --import)")
    events = read_jsonl(run_dir / ".assay" / "events.jsonl")
    if len(events) != 1:
        problems.append(f"journal has {len(events)} events, a prepared run has exactly the START event")
    if not (prov / "PROVENANCE.md").exists():
        problems.append("no PROVENANCE.md")
    else:
        try:
            recorded = json.loads((prov / "provenance.json").read_text())
            if recorded.get("problems"):
                problems.append(f"provenance recorded problems: {recorded['problems']}")
        except (OSError, json.JSONDecodeError):
            problems.append("provenance.json unreadable")
    config = json.loads((run_dir / ".assay" / "config.json").read_text()) if (run_dir / ".assay" / "config.json").exists() else {}
    if config.get("game_id") != args.game:
        problems.append(f"run dir owns {config.get('game_id')!r}, not {args.game}")
    if problems:
        print(f"E3 CHECK | {args.game} | NOT READY: " + "; ".join(problems))
        return 1
    print(f"E3 CHECK | {args.game} | READY | {run_dir} | START only, FOREIGN import present, provenance written")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game")
    parser.add_argument("--source", type=Path, help="finished source run dir (default: assay-runs/e1/<game>-gated-s1)")
    parser.add_argument("--source-state-dir", default=".assay", choices=(".assay", ".arc"),
                        help="state dir name inside the source (.arc only for pre-rename archives; renamed in the scratch copy)")
    parser.add_argument("--source-anchor-dir", type=Path, default=DEFAULT_SOURCE_ANCHORS,
                        help="where the source run anchored (read-only check)")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--registry", type=Path, help="registry for the import run (default: registry_e1_gated_<cap>.json)")
    parser.add_argument("--adapter", default=ADAPTER)
    parser.add_argument("--cap", type=int, help="action cap (default from the game table)")
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON, help="interpreter that serves the kernel and the adapter")
    parser.add_argument("--anchor-dir", type=Path, help="ASSAY_ANCHOR_DIR for the import run (default: <out-root>/anchors, same as the job)")
    parser.add_argument("--replace", action="store_true", help="replace an existing import run that has no paid action")
    parser.add_argument("--keep-broker", action="store_true", help="leave the broker running after preparation")
    parser.add_argument("--allow-registry-mismatch", action="store_true",
                        help="accept an artifact exported under a different registry (stand-in verification only)")
    parser.add_argument("--check", action="store_true", help="only check that the import run is prepared and untouched")
    args = parser.parse_args(argv)
    try:
        if args.check:
            return check(args)
        if not args.python.exists():
            raise Refusal(f"interpreter not found: {args.python}")
        return prepare(args)
    except Refusal as error:
        print(f"E3 PREPARE | REFUSED | {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
