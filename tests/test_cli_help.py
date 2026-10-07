"""The rendered help of every command, pinned. `assay --help`, `assay act
--help` and the rest are what the agent and the operator read, so the parser
assembled from the operation table (#24) must render them byte for byte as
the hand-written parser did. The fixture was rendered from that parser at
b1fb581 with COLUMNS=80 under Python 3.14; 3.13 renders the same bytes, and
3.12 differs in one place that is argparse's own, the fold of the root usage
line after the command list, which the test folds the same way on both
sides."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cli_help.json"
_USAGE_FOLD = re.compile(r"\}\n\s+\.\.\.")


def _rendered_help() -> dict[str, str]:
    from assay.cli import _parser

    rendered: dict[str, str] = {}

    def walk(parser: argparse.ArgumentParser, path: str) -> None:
        rendered[path] = parser.format_help()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    walk(sub, f"{path} {name}".strip())

    walk(_parser(), "")
    return rendered


def test_every_command_renders_the_pinned_help(monkeypatch):
    # The claims table under `act` and `commit` lists the frame forms when
    # the extra imports; the fixture was rendered with it installed.
    pytest.importorskip("PIL")
    monkeypatch.setenv("COLUMNS", "80")
    expected = json.loads(FIXTURE.read_text())
    rendered = _rendered_help()
    assert sorted(rendered) == sorted(expected)
    for path, text in expected.items():
        assert _USAGE_FOLD.sub("} ...", rendered[path]) == _USAGE_FOLD.sub("} ...", text), path
