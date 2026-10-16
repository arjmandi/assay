"""The flaky-test pack: the command-world engine over recipe.json beside this
file, in the worktree mode, with the repo archetype's world script and the
project in project/. The same stub serves every pack and every world the
builder writes; only this docstring changes. Start it from an operator's
shell:

    assay start flaky1 --adapter <this directory>/adapter.py:factory \\
        --registry <this directory>/registry.json --owner-token-file <tokens>/flaky1.token
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _engine_directory() -> Path:
    """The directory holding command_world.py: one of this file's parents or
    its archetypes/ child (a pack or a template in the catalog), else the
    worlds/ of the ASSAY checkout or plugin the daemon runs from, else
    ASSAY_WORLDS."""
    import assay

    candidates = [place for parent in HERE.parents[:3] for place in (parent, parent / "archetypes")]
    candidates.append(Path(assay.__file__).resolve().parents[2] / "worlds" / "archetypes")
    if os.environ.get("ASSAY_WORLDS"):
        candidates.append(Path(os.environ["ASSAY_WORLDS"]).expanduser() / "archetypes")
    for candidate in candidates:
        if (candidate / "command_world.py").is_file():
            return candidate
    raise RuntimeError(
        "command_world.py was not found: run the daemon from an ASSAY checkout or plugin, "
        "or set ASSAY_WORLDS to the worlds/ directory of one"
    )


sys.path.insert(0, str(_engine_directory()))
from command_world import CommandWorld, load_recipe  # noqa: E402

RECIPE = load_recipe(HERE / "recipe.json")


def factory(root: Path, config: dict[str, Any]) -> CommandWorld:
    return CommandWorld(root, config, RECIPE)
