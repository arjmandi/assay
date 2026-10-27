"""The game server of the Claude Code baseline: one ARC-AGI-3 public game held
open for a player that has no ASSAY, only `play.py`.

It drives the same local engine through the same wrapper as the ASSAY runs
(`bench/arcagi/adapter.py`, `ArcSession`), so the game answers exactly as it
did there, and it keeps the rules those runs played under that belong to the
game, not to the harness: every act and every reset counts against the cap; a
reset on a level with no action since it began is answered with the current
frame and not sent to the engine (the engine would rewind the whole game, as
ASSAY's local session also prevents); in GAME_OVER only a reset is sent; an
action the frame does not offer is refused without counting. There are no
predictions, no journal gate, no modules, no Stop gate and no manual.

Every counted action is written to `<journal dir>/.arc/events.jsonl`, outside
the player's directory, in the fields `bench/arcagi/rhae.py` reads (id,
action, data, counts_action, state, levels_completed, level_before,
win_levels), with the settled frame as hex rows. A restarted server replays
that journal, so the game survives a crash of its own process. After each
action the settled frame is rendered to `frame.png` in the player's
directory, with the palette ASSAY renders with.

usage: game_server.py --game ID --socket PATH --journal DIR --run-dir DIR [--cap 1500]
One request per connection: a JSON line in, a JSON line out
({"ok": bool, "text": str}); ops observe, act, reset, history, shutdown.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import socketserver
import sys
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parent / "adapter.py"
ACTIONS = tuple(f"ACTION{n}" for n in range(1, 8))
# ASSAY's rendering palette (src/assay_grid/render.py), so the baseline sees
# the same image.
PALETTE = np.asarray(
    [
        (0, 0, 0), (0, 116, 217), (255, 65, 54), (46, 204, 64), (255, 220, 0), (170, 170, 170),
        (240, 18, 190), (255, 133, 27), (127, 219, 255), (135, 12, 37), (255, 255, 255), (85, 85, 85),
        (177, 13, 201), (127, 255, 212), (57, 204, 204), (255, 110, 199),
    ],
    dtype=np.uint8,
)


def load_adapter() -> Any:
    spec = importlib.util.spec_from_file_location("arc_baseline_adapter", ADAPTER)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {ADAPTER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def state_of(observed: Any) -> str:
    state = getattr(observed, "state", None)
    return str(getattr(state, "value", state))


def settled(observed: Any) -> np.ndarray:
    return np.asarray(observed.frame[-1], dtype=np.int16)


def hex_rows(grid: np.ndarray) -> list[str]:
    return ["".join("0123456789abcdef"[int(value)] for value in row) for row in grid]


class Game:
    def __init__(self, game_id: str, journal: Path, run_dir: Path, cap: int) -> None:
        adapter = load_adapter()
        self.game_id = game_id
        self.run_dir = run_dir
        self.cap = cap
        self.events_path = journal / ".arc" / "events.jsonl"
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.session = adapter.ArcSession(journal / "engine", {"game_id": game_id, "seed": 0})
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.observed = self.session.observation
        if self.events_path.exists():
            self._replay()
        else:
            self._record("START", None, self.observed, counts=False, level_before=0)
        self._render()

    # ---- the journal -----------------------------------------------------------------------------
    def _replay(self) -> None:
        recorded = [json.loads(line) for line in self.events_path.read_text().splitlines() if line.strip()]
        for event in recorded:
            if event["counts_action"] and event.get("sent", True):
                self.observed = self.session.step(event["action"], event.get("data"), None)
            if event["levels_completed"] != self.observed.levels_completed:
                raise SystemExit(f"replay diverged at e{event['id']}")
        self.events = recorded

    def _record(self, action: str, data: dict[str, int] | None, observed: Any, *, counts: bool,
                level_before: int, sent: bool = True) -> dict[str, Any]:
        event = {
            "id": len(self.events),
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "action": action,
            "data": data,
            "counts_action": counts,
            "sent": sent,
            "state": state_of(observed),
            "levels_completed": int(observed.levels_completed),
            "level_before": level_before,
            "win_levels": int(observed.win_levels),
            "available_actions": [int(value) for value in observed.available_actions],
            "n_frames": len(observed.frame),
            "frame": hex_rows(settled(observed)),
        }
        self.events.append(event)
        with self.events_path.open("a") as handle:
            handle.write(json.dumps(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def _render(self) -> None:
        grid = settled(self.observed)
        image = Image.fromarray(PALETTE[grid].astype(np.uint8), mode="RGB")
        image = image.resize((grid.shape[1] * 8, grid.shape[0] * 8), Image.Resampling.NEAREST)
        image.save(self.run_dir / "frame.png")

    # ---- what the player sees ----------------------------------------------------------------------
    @property
    def paid(self) -> int:
        return sum(1 for event in self.events if event["counts_action"])

    def available(self) -> list[str]:
        return [f"ACTION{value}" for value in self.observed.available_actions] + ["RESET"]

    def status_line(self) -> str:
        done = int(self.observed.levels_completed)
        total = int(self.observed.win_levels)
        level = f"level {min(done + 1, total)} of {total} ({done} completed)"
        return (
            f"STATE {state_of(self.observed)} | {level} | actions {self.paid} used, "
            f"{max(0, self.cap - self.paid)} left of {self.cap}"
        )

    def available_line(self) -> str:
        names = " ".join(self.available())
        note = " (ACTION6 takes a cell: x the column, y the row, 0 to 63)" if "ACTION6" in names else ""
        return f"AVAILABLE {names}{note}"

    def grid_text(self) -> str:
        rows = hex_rows(settled(self.observed))
        return "GRID (64 rows top to bottom, one hex digit per cell, x left to right)\n" + "\n".join(rows)

    def look(self) -> str:
        return "\n".join([self.status_line(), self.available_line(), self.grid_text(), "IMAGE frame.png"])

    def act(self, action: str, data: dict[str, int] | None, quiet: bool) -> tuple[bool, str]:
        action = action.upper()
        state = state_of(self.observed)
        if state == "WIN":
            return False, "the game is won; there is nothing left to play"
        if self.paid >= self.cap:
            return False, f"the action cap of {self.cap} is reached"
        if action not in (*ACTIONS, "RESET"):
            return False, f"unknown action {action}; {self.available_line()}"
        if action != "RESET" and state == "GAME_OVER":
            return False, "GAME_OVER: only a reset continues (play.py reset); nothing was sent"
        if action != "RESET" and action not in self.available():
            return False, f"{action} is not available now; {self.available_line()}"
        if action == "ACTION6":
            if data is None or not all(0 <= data.get(key, -1) <= 63 for key in ("x", "y")):
                return False, "ACTION6 takes a cell: play.py act ACTION6 X Y, each 0 to 63"
        else:
            data = None
        level_before = int(self.observed.levels_completed)
        last = next((event for event in reversed(self.events) if event["counts_action"]), None)
        fresh = last is None or last["action"] == "RESET" or last["levels_completed"] > last["level_before"]
        if action == "RESET" and fresh and state != "GAME_OVER":
            # The level has not been played since it began: the engine would
            # rewind the whole game, so the current frame answers.
            event = self._record(action, None, self.observed, counts=True, level_before=level_before, sent=False)
        else:
            self.observed = self.session.step(action, data, None)
            event = self._record(action, data, self.observed, counts=True, level_before=level_before)
        self._render()
        notes = []
        if event["levels_completed"] > level_before:
            notes.append(f"LEVEL COMPLETED: {event['levels_completed']} of {event['win_levels']}")
        if event["state"] in ("WIN", "GAME_OVER"):
            notes.append(event["state"])
        head = (
            f"e{event['id']} {action}{'' if data is None else ' ' + str(data['x']) + ' ' + str(data['y'])} | "
            f"frames {event['n_frames']} | {self.status_line()}"
        )
        lines = [head, *notes]
        if not quiet:
            lines += [self.available_line(), self.grid_text(), "IMAGE frame.png"]
        return True, "\n".join(lines)

    def history(self, count: int) -> str:
        rows = [
            f"e{event['id']} {event['action']}"
            + ("" if not event.get("data") else f" {event['data']['x']} {event['data']['y']}")
            + f" | levels {event['levels_completed']} | {event['state']}"
            for event in self.events[1:][-count:]
        ]
        return "\n".join(rows) if rows else "no actions yet"


def serve(args: argparse.Namespace) -> None:
    game = Game(args.game, Path(args.journal), Path(args.run_dir), args.cap)
    socket_path = Path(args.socket)
    if socket_path.exists():
        socket_path.unlink()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            try:
                request = json.loads(self.rfile.readline() or b"null")
                op = request.get("op") if isinstance(request, dict) else None
                with game.lock:
                    if op == "observe":
                        reply = {"ok": True, "text": game.look() if request.get("grid") else
                                 "\n".join([game.status_line(), game.available_line()])}
                    elif op == "act":
                        data = request.get("data")
                        ok, text = game.act(str(request.get("action", "")), data, bool(request.get("quiet")))
                        reply = {"ok": ok, "text": text}
                    elif op == "history":
                        reply = {"ok": True, "text": game.history(int(request.get("count") or 20))}
                    elif op == "shutdown":
                        reply = {"ok": True, "text": "stopping"}
                        threading.Thread(target=server.shutdown, daemon=True).start()
                    else:
                        reply = {"ok": False, "text": f"unknown op {op!r}"}
            except Exception as error:  # noqa: BLE001 - the player sees one error line
                reply = {"ok": False, "text": f"{type(error).__name__}: {error}"}
            self.wfile.write((json.dumps(reply) + "\n").encode())

    server = socketserver.ThreadingUnixStreamServer(str(socket_path), Handler)
    print(f"SERVING {args.game} on {socket_path}", flush=True)
    with server:
        server.serve_forever()
    socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="game_server.py")
    parser.add_argument("--game", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cap", type=int, default=1500)
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
