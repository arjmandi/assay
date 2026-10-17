"""The ARC-AGI-3 pack: the benchmark's own adapter (bench/arcagi/adapter.py),
with the ARC API key read from a file when the environment names none. The
key downloads a public game once into the local cache
(`~/.cache/assay/arcade`); after that a run plays offline. Start it from an
operator's shell, with the game id as the world id:

    assay start ft09 --adapter <this directory>/adapter.py:factory \\
        --registry <this directory>/registry.json --owner-token-file <tokens>/ft09.token

The key file is `~/assay/tokens/arc_api_key`, or the file ASSAY_ARC_KEY_FILE
names. The daemon reads it, so the key reaches neither the player nor the
journal (the registry lists ARC_API_KEY under `secrets`).
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
KEY_NAME = "ARC_API_KEY"
KEY_FILE_VARIABLE = "ASSAY_ARC_KEY_FILE"
DEFAULT_KEY_FILE = Path("~/assay/tokens/arc_api_key")


def key_file(environ: Mapping[str, str] = os.environ) -> Path:
    return Path(environ.get(KEY_FILE_VARIABLE) or DEFAULT_KEY_FILE).expanduser()


def load_key(environ: MutableMapping[str, str] = os.environ) -> str | None:
    """Put the key from the key file into `environ` when it names none; the
    place the key came from, or None when there is none."""
    if environ.get(KEY_NAME):
        return "environment"
    path = key_file(environ)
    try:
        key = path.read_text().strip()
    except OSError:
        return None
    if not key:
        return None
    environ[KEY_NAME] = key
    return str(path)


def _benchmark_adapter() -> Path:
    """bench/arcagi/adapter.py of the checkout or plugin this pack sits in,
    else of the one the daemon runs from."""
    import assay

    for root in (HERE.parents[2], Path(assay.__file__).resolve().parents[2]):
        candidate = root / "bench" / "arcagi" / "adapter.py"
        if candidate.is_file():
            return candidate
    raise RuntimeError("bench/arcagi/adapter.py is not found beside this pack or the ASSAY it runs under")


def _load() -> ModuleType:
    path = _benchmark_adapter()
    spec = importlib.util.spec_from_file_location("assay_pack_arcagi", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{path} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def factory(root: Path, config: Mapping[str, Any]) -> object:
    # The session is the benchmark adapter's ArcSession, which the contract
    # checks there; `object` here names no class of this module.
    load_key()
    return _load().factory(root, config)
