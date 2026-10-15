"""A notebook as an MCP server over stdio, standard library only: the service
archetype's template. It speaks the stdio transport, one JSON-RPC message per
line. It starts with four notes chosen by the seed and keeps them in memory,
so every session starts the same and the same calls give the same answers,
which is why the template declares replay. It exits when its stdin closes.

Tools:
  add_note(title, body)   adds a note; refuses an empty title or one already used
  list_notes()            read-only: every note's id and title
  find_note(title)        read-only: whether a note with that title exists
  delete_note(id)         destructive: removes one note; refuses an id that does not exist
  count_notes()           read-only: how many notes there are
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from typing import Any

TITLES = ("ideas", "books", "travel", "recipes", "todo", "music", "garden", "work")
PROTOCOL_VERSION = "2025-06-18"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "add_note",
        "description": "Add a note to the notebook.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 80},
                "body": {"type": "string", "maxLength": 2000},
            },
            "required": ["title", "body"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "list_notes",
        "description": "List every note's id and title.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "find_note",
        "description": "Say whether a note with this title exists.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "delete_note",
        "description": "Remove one note by its id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "integer", "minimum": 1}}, "required": ["id"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "count_notes",
        "description": "Count the notes.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
]


class Notebook:
    def __init__(self, seed: int) -> None:
        titles = random.Random(f"notebook-{seed}").sample(TITLES, 4)
        self.notes = {index: {"id": index, "title": title, "body": ""} for index, title in enumerate(titles, 1)}
        self.next_id = len(titles) + 1

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "add_note":
            title = str(arguments.get("title", "")).strip()
            if not title:
                return _error("a note needs a title")
            if any(note["title"] == title for note in self.notes.values()):
                return _error(f"a note titled {title!r} already exists")
            note = {"id": self.next_id, "title": title, "body": str(arguments.get("body", ""))}
            self.notes[self.next_id] = note
            self.next_id += 1
            return _ok(f"added note {note['id']}", {"id": note["id"], "count": len(self.notes)})
        if name == "list_notes":
            listed = [{"id": note["id"], "title": note["title"]} for note in self.notes.values()]
            return _ok(f"{len(listed)} notes", {"notes": listed})
        if name == "find_note":
            title = str(arguments.get("title", ""))
            found = any(note["title"] == title for note in self.notes.values())
            return _ok(f"{title!r} {'found' if found else 'not found'}", {"found": found})
        if name == "delete_note":
            ident = arguments.get("id")
            if ident not in self.notes:
                return _error(f"no note {ident}")
            del self.notes[ident]
            return _ok(f"deleted note {ident}", {"count": len(self.notes)})
        if name == "count_notes":
            return _ok(f"{len(self.notes)} notes", {"count": len(self.notes)})
        raise KeyError(name)


def _ok(text: str, structured: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": False}


def _error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=0)
    notebook = Notebook(parser.parse_args(argv).seed)
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        method, ident = message.get("method"), message.get("id")
        if method is None:
            continue  # an answer to the server's own ping
        if ident is None:
            if method == "notifications/initialized":
                # A server may talk first: a log line and a ping the client must answer.
                _send({"jsonrpc": "2.0", "method": "notifications/message",
                       "params": {"level": "info", "data": "notebook ready"}})
                _send({"jsonrpc": "2.0", "id": "server-ping", "method": "ping"})
            continue
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "notebook", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            try:
                result = notebook.call(str(params.get("name")), dict(params.get("arguments") or {}))
            except KeyError:
                _send({"jsonrpc": "2.0", "id": ident,
                       "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}"}})
                continue
        elif method == "ping":
            result = {}
        else:
            _send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"no method {method}"}})
            continue
        _send({"jsonrpc": "2.0", "id": ident, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
