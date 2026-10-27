#!/usr/bin/env python3
"""play: the only way the baseline player reaches its game (no ASSAY).

    play.py status           state, level, actions used and left, available actions
    play.py look             the same, the current grid as 64 rows of hex digits, frame.png
    play.py act ACTIONn      one action; ACTION6 takes a cell: play.py act ACTION6 X Y
    play.py act ... --quiet  the result line alone, without the grid
    play.py reset            restart the current level (it counts as an action)
    play.py history [N]      the last N actions (default 20)

Every act and reset counts against the game's action cap. The game server is
the operator's: its socket is named by ARC_PLAY_SOCKET. Standard library only.
"""

from __future__ import annotations

import json
import os
import socket
import sys

USAGE = __doc__.split("\n\n")[1]


def request(payload: dict[str, object]) -> dict[str, object]:
    path = os.environ.get("ARC_PLAY_SOCKET")
    if not path:
        raise SystemExit("ERROR | ARC_PLAY_SOCKET is not set; the operator's game server is not reachable")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        try:
            connection.connect(path)
        except OSError as error:
            raise SystemExit(f"ERROR | the game server does not answer ({error}); stop and say so") from None
        connection.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b'{"ok": false, "text": "no answer"}')


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    words = [word for word in argv if word != "--quiet"]
    if not words or words[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    command, rest = words[0], words[1:]
    if command == "status":
        payload: dict[str, object] = {"op": "observe"}
    elif command == "look":
        payload = {"op": "observe", "grid": True}
    elif command == "reset":
        payload = {"op": "act", "action": "RESET", "quiet": quiet}
    elif command == "act" and rest:
        data = None
        if len(rest) == 3:
            try:
                data = {"x": int(rest[1]), "y": int(rest[2])}
            except ValueError:
                print("ERROR | X and Y are integers: play.py act ACTION6 X Y", file=sys.stderr)
                return 2
        elif len(rest) != 1:
            print("ERROR | play.py act ACTIONn, or play.py act ACTION6 X Y", file=sys.stderr)
            return 2
        payload = {"op": "act", "action": rest[0], "data": data, "quiet": quiet}
    elif command == "history":
        payload = {"op": "history", "count": int(rest[0]) if rest else 20}
    else:
        print(USAGE)
        return 2
    reply = request(payload)
    text = str(reply.get("text", ""))
    if reply.get("ok"):
        print(text)
        return 0
    print(f"ERROR | {text}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
