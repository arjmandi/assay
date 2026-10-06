#!/usr/bin/env python3
"""e2_prepare.py: build the five E2 resume states from the archived sweep runs.

For each game: reconstruct NOTES.md at the cut with notes_at.py (pre- and
post-intervention transcripts listed below), then cut the run with resume_at.py,
adding the coverage-audit module to the pinned registry (advise mode) and
pointing the resumed run at a fresh anchor directory. Sources are read-only.

    PRO-LONG/.venv/bin/python3 tools/e2_prepare.py [--out /…/assay-runs/e2] [--force] [--only GAME]
    PRO-LONG/.venv/bin/python3 tools/e2_prepare.py --control [--only GAME]

--control builds the control arm, <game>-resume-ctrl: the same cut, journal,
reconstructed NOTES.md, verifiers and channels, but no coverage module. The
pinned registry stays the canonical bench/arcagi/registry_1500.json (its hash
is the archived sweep run's), nothing is pinned into .assay/modules/, and the
anchor directory is anchors/<game>-ctrl. The module arm's reconstructed notes
and notes_at report are reused byte for byte when they exist. After the build
the control is compared file by file with the module arm and the comparison is
appended to the control's provenance; anything beyond the registry, the
config.json hash and the module file is a failure.

Run with an interpreter that serves the kernel (numpy, pillow): resume_at.py
rebuilds dossier.json with the kernel's own code.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCES = Path("/Users/mohsenarjmandi/workspace/assay-runs/sweep1500")
TRANSCRIPTS = Path("/Users/mohsenarjmandi/workspace/assay-archive/story/raw/sweep1500")
MODULE = REPO / "bench" / "arcagi" / "modules" / "coverage_audit.py"
REGISTRY = REPO / "bench" / "arcagi" / "registry_e2_1500_coverage.json"
CANONICAL_REGISTRY = REPO / "bench" / "arcagi" / "registry_1500.json"

# What may differ between a control state and its module arm. Nothing else.
CONTROL_DIFFERS = {".assay/registry.json", ".assay/config.json"}
CONTROL_LACKS = {".assay/modules/coverage_audit.py"}

# game: (cut event, sessions before the operator's intervention, sessions after it)
# dc22 session 1 was API-billed and has no transcript; its state comes from the
# first whole-file Read in session 2. sk48 is cut at e111, the last paid action of
# session 1 (e110 is the ACTION6 probe carrying the proof, e112 opens session 2), so
# like the other four states it is cut at the end of the proving session.
CUTS = {
    "dc22": (634, [], ["s2"]),
    "s5i5": (273, ["s2"], ["s3"]),
    "sk48": (111, ["s1"], ["s2"]),
    "wa30": (990, ["s1"], ["s2"]),
    "bp35": (368, ["s1"], ["s2", "s3"]),
}


def transcripts(game: str, sessions: list[str]) -> list[str]:
    out = []
    for session in sessions:
        out += sorted(glob.glob(str(TRANSCRIPTS / f"{game}-{session}-agent-*.jsonl")))
    return out


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.name != ".DS_Store" and "__pycache__" not in path.parts:
            out[str(path.relative_to(root))] = path
    return out


def compare_trees(a: Path, b: Path) -> tuple[list[str], list[str], list[str]]:
    """(files only in a, files only in b, files in both whose bytes differ)."""
    fa, fb = tree_files(a), tree_files(b)
    only_a = sorted(set(fa) - set(fb))
    only_b = sorted(set(fb) - set(fa))
    differ = sorted(rel for rel in set(fa) & set(fb) if fa[rel].read_bytes() != fb[rel].read_bytes())
    return only_a, only_b, differ


def control_section(game: str, cut: int, dest: Path, module_dir: Path, anchor: Path,
                    notes_note: str) -> tuple[list[str], bool]:
    """Provenance lines for a control state and whether it is the module arm's
    byte-identical twin except for the registry, the hash and the module."""
    pinned = json.loads((dest / ".assay" / "registry.json").read_text())
    config = json.loads((dest / ".assay" / "config.json").read_text())
    modules_dir = dest / ".assay" / "modules"
    pinned_files = sorted(p.name for p in modules_dir.iterdir()) if modules_dir.is_dir() else []
    lines = [
        "",
        "## Control arm (no coverage module)",
        f"- this state is the CONTROL for {module_dir.name}: the same cut (e{cut}), the same journal,",
        "  the same reconstructed NOTES.md, the same verifiers and channels, resumed WITHOUT the",
        "  coverage-audit module. The two arms differ only in the registry, the registry hash and",
        "  the module file.",
        f"- pinned .assay/registry.json: the canonical {CANONICAL_REGISTRY.relative_to(REPO)} "
        f"(keys {sorted(pinned)}; `modules` present: {'modules' in pinned}, "
        f"`module_modes` present: {'module_modes' in pinned})",
        f"- config.json registry_hash: {config.get('registry_hash')} (the archived sweep run's hash, unchanged by the cut)",
        f"- .assay/modules/: {'absent' if not modules_dir.exists() else (pinned_files or 'empty')}",
        f"- anchor dir (fresh, control-specific): {anchor}",
        f"- notes: {notes_note}",
    ]
    ok = False
    if module_dir.is_dir():
        module_registry = json.loads((module_dir / ".assay" / "registry.json").read_text())
        module_config = json.loads((module_dir / ".assay" / "config.json").read_text())
        module_file = module_dir / ".assay" / "modules" / "coverage_audit.py"
        only_module, only_control, differ = compare_trees(module_dir, dest)
        ok = (set(only_module) == CONTROL_LACKS and not only_control and set(differ) == CONTROL_DIFFERS)
        lines += [
            f"- module arm: {module_dir}",
            f"    - registry_hash {module_config.get('registry_hash')}, modules {module_registry.get('modules')}, "
            f"module_modes {module_registry.get('module_modes')}",
            f"    - .assay/modules/coverage_audit.py sha256 "
            f"{sha256_path(module_file) if module_file.exists() else 'MISSING'}",
            "- byte comparison of the two run directories (every file, .DS_Store and __pycache__ ignored):",
            f"    - files only in the module arm: {only_module or 'none'}",
            f"    - files only in the control: {only_control or 'none'}",
            f"    - files present in both with different bytes: {differ or 'none'}",
            f"- byte-identical except the registry, the hash and the module: {'YES' if ok else 'NO (see above)'}",
        ]
    else:
        lines.append(f"- module arm {module_dir} not found: no byte comparison was possible")
    return lines, ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("/Users/mohsenarjmandi/workspace/assay-runs/e2"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--control", action="store_true",
                        help="build <game>-resume-ctrl: the same cut without the coverage module")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    suffix = "-resume-ctrl" if args.control else "-resume"
    failures = 0
    for game, (cut, pre, post) in CUTS.items():
        if args.only and game not in args.only:
            continue
        source = SOURCES / game
        dest = args.out / f"{game}{suffix}"
        work = args.out / f"{game}{suffix}.provenance"
        anchor = args.out / "anchors" / (f"{game}-ctrl" if args.control else game)
        module_work = args.out / f"{game}-resume.provenance"
        work.mkdir(parents=True, exist_ok=True)
        notes_out = work / "NOTES.at-cut.md"
        notes_report = work / "notes_at.report.json"
        notes_note = ""
        reused = (args.control and (module_work / "NOTES.at-cut.md").exists()
                  and (module_work / "notes_at.report.json").exists())
        if reused:
            # the control must carry the module arm's NOTES.md byte for byte
            shutil.copyfile(module_work / "NOTES.at-cut.md", notes_out)
            shutil.copyfile(module_work / "notes_at.report.json", notes_report)
            notes_rc = 0
            notes_note = (f"NOTES.at-cut.md (sha256 {sha256_path(notes_out)}) and notes_at.report.json reused "
                          f"byte for byte from {module_work}, notes_at.py was not rerun")
            print(f"== {game}: notes reused from {module_work.name}")
        else:
            cmd = [sys.executable, str(REPO / "tools" / "notes_at.py"), "--path", str(source / ".assay" / "NOTES.md"),
                   "--journal", str(source / ".assay" / "events.jsonl"), "--cut", str(cut),
                   "--out", str(notes_out), "--report", str(notes_report)]
            for item in transcripts(game, pre):
                cmd += ["--transcript", item]
            for item in transcripts(game, post):
                cmd += ["--post-transcript", item]
            notes = subprocess.run(cmd, capture_output=True, text=True)
            notes_rc = notes.returncode
            print(f"== {game}: notes_at exit {notes_rc}")
            if notes_rc != 0:
                print(notes.stderr[-600:])
            notes_note = f"reconstructed by notes_at.py in this build (exit {notes_rc})"
        resume = [sys.executable, str(REPO / "tools" / "resume_at.py"), str(source), str(cut), str(dest),
                  "--anchor-dir", str(anchor),
                  "--provenance", str(work / "RESUME_PROVENANCE.md"),
                  "--drop-unresolved"]
        if args.control:
            resume += ["--registry", str(CANONICAL_REGISTRY)]
        else:
            resume += ["--module", str(MODULE), "--module-mode", "coverage_audit=advise",
                       "--registry", str(REGISTRY)]
        if notes_rc == 0:
            resume += ["--notes", str(notes_out), "--notes-report", str(notes_report)]
        else:
            resume += ["--notes-fallback-level"]
        if notes_report.exists():
            before = json.loads(notes_report.read_text()).get("before")
            if before:  # the session boundary notes_at.py derived (post transcript start or e(cut+1))
                resume += ["--boundary", before]
        if args.force:
            resume.append("--force")
        done = subprocess.run(resume, capture_output=True, text=True)
        print(done.stdout.strip())
        if done.returncode != 0:
            failures += 1
            print(done.stderr[-1200:])
            continue
        if args.control:
            lines, ok = control_section(game, cut, dest, args.out / f"{game}-resume", anchor, notes_note)
            with (work / "RESUME_PROVENANCE.md").open("a") as handle:
                handle.write("\n".join(lines) + "\n")
            print(f"  control of {game}-resume: byte-identical except registry, hash and module: {'YES' if ok else 'NO'}")
            if not ok:
                failures += 1
                print("\n".join(lines[-4:]))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
