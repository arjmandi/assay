"""The ARC-AGI-3 pack (worlds/recipes/arc-agi-3): its manifest names files
that exist, its registry and pins stay those of the benchmark's adapter it
wraps, and the key comes from the key file only when the environment names
none. Nothing here downloads or plays a game."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "worlds" / "recipes" / "arc-agi-3"

_spec = importlib.util.spec_from_file_location("arc_pack_adapter", PACK / "adapter.py")
pack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pack)


def _pins(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line and not line.startswith("#")]


def test_the_manifest_names_what_the_pack_holds():
    manifest = json.loads((PACK / "pack.json").read_text())
    assert manifest["name"] == PACK.name
    assert (PACK / manifest["adapter"].split(":")[0]).is_file()
    assert (PACK / manifest["registry"]).is_file()
    assert (PACK / manifest["python"]["requirements"]).is_file()
    assert manifest["world_id"]["default"] in manifest["world_id"]["values"]
    assert len(manifest["world_id"]["values"]) == 25
    assert pack._benchmark_adapter() == REPO / "bench" / "arcagi" / "adapter.py"


def test_the_pack_plays_the_benchmark_world():
    registry = json.loads((PACK / "registry.json").read_text())
    benchmark = json.loads((REPO / "bench" / "arcagi" / "registry_200.json").read_text())
    assert registry["actions"] == benchmark["actions"]
    assert registry["budget"] == benchmark["budget"] and registry["secrets"] == ["ARC_API_KEY"]
    assert registry["goal"]["text"]
    assert _pins(PACK / "requirements.txt") == _pins(REPO / "bench" / "arcagi" / "requirements.txt")


def test_the_key_comes_from_the_file_only_when_the_environment_names_none(tmp_path):
    key = tmp_path / "arc_api_key"
    key.write_text("  file-key-0123456789\n")
    environ = {pack.KEY_FILE_VARIABLE: str(key)}
    assert pack.load_key(environ) == str(key)
    assert environ[pack.KEY_NAME] == "file-key-0123456789"
    environ = {pack.KEY_FILE_VARIABLE: str(key), pack.KEY_NAME: "env-key"}
    assert pack.load_key(environ) == "environment" and environ[pack.KEY_NAME] == "env-key"
    assert pack.load_key({pack.KEY_FILE_VARIABLE: str(tmp_path / "missing")}) is None
    (tmp_path / "empty").write_text("\n")
    environ = {pack.KEY_FILE_VARIABLE: str(tmp_path / "empty")}
    assert pack.load_key(environ) is None and pack.KEY_NAME not in environ
    assert pack.key_file({}) == Path("~/assay/tokens/arc_api_key").expanduser()


def test_the_factory_loads_the_key_then_hands_over_to_the_benchmark_adapter(tmp_path, monkeypatch):
    key = tmp_path / "arc_api_key"
    key.write_text("file-key-0123456789\n")
    monkeypatch.setenv(pack.KEY_FILE_VARIABLE, str(key))
    monkeypatch.delenv(pack.KEY_NAME, raising=False)
    seen = {}

    class Benchmark:
        @staticmethod
        def factory(root, config):
            seen["key"] = pack.os.environ.get(pack.KEY_NAME)
            return ("session", root, dict(config))

    monkeypatch.setattr(pack, "_load", lambda: Benchmark)
    assert pack.factory(tmp_path, {"game_id": "ft09"}) == ("session", tmp_path, {"game_id": "ft09"})
    assert seen["key"] == "file-key-0123456789"
