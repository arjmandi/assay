#!/usr/bin/env python3
"""render_prompt.py: fill a tools/prompts/*.md template.

    render_prompt.py TEMPLATE --game ft09 --cap 200 --levels 6 --run-dir DIR \
        --registry FILE --constitution FILE [--repo DIR] [--runs-root DIR] [--out FILE]

Placeholders: {{GAME}} {{CAP}} {{LEVELS}} {{RUN_DIR}} {{REGISTRY}} {{CONSTITUTION}}
{{CONSTITUTION_NAME}} {{ASSAY}} {{ADAPTER}} {{REPO}} {{RUNS_ROOT}}. Unfilled
placeholders are an error. Nothing game-specific beyond the level count is ever
inserted. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("template", type=Path)
    parser.add_argument("--game", required=True)
    parser.add_argument("--cap", required=True, type=int)
    parser.add_argument("--levels", required=True, type=int)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--constitution", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--runs-root", type=Path, default=Path("/Users/mohsenarjmandi/workspace/assay-runs"))
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    values = {
        "GAME": args.game, "CAP": str(args.cap), "LEVELS": str(args.levels),
        "RUN_DIR": str(args.run_dir.resolve()), "REGISTRY": str(args.registry.resolve()),
        "CONSTITUTION": str(args.constitution.resolve()), "CONSTITUTION_NAME": args.constitution.name,
        "ASSAY": str(repo / "bin" / "assay"),
        "ADAPTER": args.adapter or f"{repo}/bench/arcagi/adapter.py:factory",
        "REPO": str(repo), "RUNS_ROOT": str(args.runs_root.resolve()),
    }
    text = args.template.read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if leftover:
        print(f"unfilled placeholders: {sorted(set(leftover))}", file=sys.stderr)
        return 2
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
