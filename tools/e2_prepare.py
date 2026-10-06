#!/usr/bin/env python3
"""e2_prepare.py: build the five E2 resume states from the archived sweep runs.

For each game: reconstruct NOTES.md at the cut with notes_at.py (pre- and
post-intervention transcripts listed below), then cut the run with resume_at.py,
adding the coverage-audit module to the pinned registry (advise mode) and
pointing the resumed run at a fresh anchor directory. Sources are read-only.

    PRO-LONG/.venv/bin/python3 tools/e2_prepare.py [--out /…/assay-runs/e2] [--force] [--only GAME]

Run with an interpreter that serves the kernel (numpy, pillow): resume_at.py
rebuilds dossier.json with the kernel's own code.
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCES = Path("/Users/mohsenarjmandi/workspace/assay-runs/sweep1500")
TRANSCRIPTS = Path("/Users/mohsenarjmandi/workspace/assay-archive/story/raw/sweep1500")
MODULE = REPO / "bench" / "arcagi" / "modules" / "coverage_audit.py"
REGISTRY = REPO / "bench" / "arcagi" / "registry_e2_1500_coverage.json"

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("/Users/mohsenarjmandi/workspace/assay-runs/e2"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    failures = 0
    for game, (cut, pre, post) in CUTS.items():
        if args.only and game not in args.only:
            continue
        source = SOURCES / game
        dest = args.out / f"{game}-resume"
        work = args.out / f"{game}-resume.provenance"
        work.mkdir(parents=True, exist_ok=True)
        notes_out = work / "NOTES.at-cut.md"
        notes_report = work / "notes_at.report.json"
        cmd = [sys.executable, str(REPO / "tools" / "notes_at.py"), "--path", str(source / ".assay" / "NOTES.md"),
               "--journal", str(source / ".assay" / "events.jsonl"), "--cut", str(cut),
               "--out", str(notes_out), "--report", str(notes_report)]
        for item in transcripts(game, pre):
            cmd += ["--transcript", item]
        for item in transcripts(game, post):
            cmd += ["--post-transcript", item]
        notes = subprocess.run(cmd, capture_output=True, text=True)
        print(f"== {game}: notes_at exit {notes.returncode}")
        if notes.returncode != 0:
            print(notes.stderr[-600:])
        resume = [sys.executable, str(REPO / "tools" / "resume_at.py"), str(source), str(cut), str(dest),
                  "--anchor-dir", str(args.out / "anchors" / game),
                  "--module", str(MODULE), "--module-mode", "coverage_audit=advise",
                  "--registry", str(REGISTRY), "--provenance", str(work / "RESUME_PROVENANCE.md"),
                  "--drop-unresolved"]
        if notes.returncode == 0:
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
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
