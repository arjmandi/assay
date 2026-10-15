"""A legacy CRM (v1) and its successor, a customer hub (v2), as one local HTTP
service: the world of the legacy-api pack. openapi.json documents the
endpoints. What the documentation leaves out, and what it gets wrong, is for
the agent to find.

    python3 legacy_crm.py --port P --seed S --state DIR

The data comes from the seed and the clock is logical: a request to the API
moves it one second and the engine's WAIT moves it further. So the same
requests get the same answers, and a run replays. Every change to the hub is
written to DIR/hub.json for checks/score.py. The service stops when its stdin
closes, that is, when the daemon that started it is gone.

Endpoints under /__world/ answer only requests carrying the engine's internal
header. They advance the clock for WAIT and give the verdict the engine
reads; they never appear in the agent's actions.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

FIRST = ("Ada", "Bea", "Cal", "Dov", "Eli", "Fay", "Gus", "Hal", "Ivy", "Jun", "Kai", "Lea", "Max", "Nia", "Oto", "Pia")
LAST = ("Ames", "Bose", "Cruz", "Diaz", "Eder", "Ford", "Gale", "Hahn", "Ito", "Jain", "Kern", "Lund", "Moss", "Nash")
DOMAINS = ("example.com", "example.org", "example.net")
INTERNAL_HEADER = "X-Assay-Internal"
# The behaviour the documentation leaves out or gets wrong.
PER_PAGE_DEFAULT = 10
PER_PAGE_CAP = 25            # the documentation says 100
RATE_WINDOW = 10             # seconds of the logical clock
RATE_LIMIT = 6               # requests to /v1 within the window
IMPORT_BATCH_CAP = 20        # the documentation says 100


def customers(seed: int) -> list[dict[str, Any]]:
    """The legacy CRM's customers, in id order: some archived, some with an
    email stored with capitals or surrounding spaces."""
    rng = random.Random(f"legacy-crm-{seed}")
    numbers = sorted(rng.sample(range(1000, 10000), rng.randint(96, 124)))
    records = []
    for number in numbers:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        email = f"{first}.{last}{number % 97}@{rng.choice(DOMAINS)}".lower()
        roll = rng.random()
        if roll < 0.18:
            email = email.upper() if rng.random() < 0.4 else email[0].upper() + email[1:]
        elif roll < 0.28:
            email = rng.choice((f" {email}", f"{email} ", f" {email} "))
        records.append({
            "id": f"C{number}",
            "name": f"{first} {last}",
            "email": email,
            "status": "archived" if rng.random() < 0.15 else "active",
        })
    return records


def normalized(email: str) -> str:
    return email.strip().lower()


class World:
    """The two systems and the clock."""

    def __init__(self, seed: int, state: Path) -> None:
        self.legacy = customers(seed)
        self.by_id = {record["id"]: record for record in self.legacy}
        self.hub: dict[str, dict[str, str]] = {}
        self.clock = 0
        self.recent: list[int] = []
        self.state = state
        self._save()

    def _save(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        target = self.state / "hub.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"customers": sorted(self.hub.values(), key=lambda row: row["id"])}, indent=1) + "\n")
        temporary.replace(target)

    def verdict(self) -> bool:
        active = {record["id"]: record for record in self.legacy if record["status"] == "active"}
        if set(self.hub) != set(active):
            return False
        return all(
            row["name"] == active[ident]["name"] and row["email"] == normalized(active[ident]["email"])
            for ident, row in self.hub.items()
        )

    def tick(self, limited: bool) -> int | None:
        """One request's second; for /v1, the seconds to wait when the request
        is over the rate limit, else None."""
        self.clock += 1
        if not limited:
            return None
        window = [moment for moment in self.recent if moment > self.clock - RATE_WINDOW]
        self.recent = window
        if len(window) >= RATE_LIMIT:
            return window[0] + RATE_WINDOW - self.clock
        self.recent.append(self.clock)
        return None

    # -- v1, the legacy CRM ----------------------------------------------------

    def list_customers(self, query: dict[str, str]) -> tuple[int, Any, dict[str, str]]:
        try:
            page = int(query.get("page", "0"))
            per_page = int(query.get("per_page", str(PER_PAGE_DEFAULT)))
        except ValueError:
            return 400, {"error": "page and per_page are whole numbers"}, {}
        if page < 0 or per_page < 1 or per_page > 100:
            return 400, {"error": "page from 0, per_page from 1 to 100"}, {}
        status = query.get("status")
        if status is not None and status not in ("active", "archived"):
            return 400, {"error": "status is active or archived"}, {}
        rows = [record for record in self.legacy if status is None or record["status"] == status]
        size = min(per_page, PER_PAGE_CAP)
        chunk = rows[page * size : (page + 1) * size]
        return 200, {"data": [dict(record) for record in chunk], "page": page}, {}

    def get_customer(self, ident: str) -> tuple[int, Any, dict[str, str]]:
        record = self.by_id.get(ident)
        if record is None or record["status"] == "archived":
            return 404, {"error": f"no customer {ident}"}, {}
        return 200, dict(record), {}

    def count_customers(self) -> tuple[int, Any, dict[str, str]]:
        return 200, {"count": len(self.legacy)}, {}

    # -- v2, the customer hub ----------------------------------------------------

    def import_customers(self, body: Any) -> tuple[int, Any, dict[str, str]]:
        if not isinstance(body, dict) or not isinstance(body.get("customers"), list):
            return 400, {"error": "the body is {\"customers\": [...]}"}, {}
        batch = body["customers"]
        if len(batch) > IMPORT_BATCH_CAP:
            return 413, {"error": f"batch too large: at most {IMPORT_BATCH_CAP} customers per request"}, {}
        errors = []
        seen = set()
        for row in batch:
            if not isinstance(row, dict):
                errors.append({"field": "customer", "message": "each customer is an object"})
                continue
            ident = str(row.get("id", ""))
            for field in ("id", "name", "email"):
                if not isinstance(row.get(field), str) or not row[field]:
                    errors.append({"id": ident, "field": field, "message": "required"})
            email = row.get("email")
            if isinstance(email, str) and email and (email != normalized(email) or "@" not in email):
                errors.append({"id": ident, "field": "email", "message": "must be lowercase with no surrounding spaces"})
            if ident in self.hub or ident in seen:
                errors.append({"id": ident, "field": "id", "message": "already imported"})
            seen.add(ident)
        if errors:
            conflict = all(error["field"] == "id" and error["message"] == "already imported" for error in errors)
            return (409 if conflict else 422), {"errors": errors[:20], "imported": 0}, {}
        for row in batch:
            self.hub[row["id"]] = {"id": row["id"], "name": row["name"], "email": row["email"]}
        self._save()
        return 200, {"imported": len(batch), "total": len(self.hub)}, {}

    def count_imported(self) -> tuple[int, Any, dict[str, str]]:
        return 200, {"count": len(self.hub)}, {}

    def remove_imported(self, ident: str) -> tuple[int, Any, dict[str, str]]:
        if ident not in self.hub:
            return 404, {"error": f"{ident} is not in the hub"}, {}
        del self.hub[ident]
        self._save()
        return 200, {"removed": ident, "total": len(self.hub)}, {}


def handler_for(world: World) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's name
            return

        def _send(self, status: int, body: Any, headers: dict[str, str]) -> None:
            raw = json.dumps(body, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return None
            try:
                return json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return "not json"

        def _route(self, method: str) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = dict(urllib.parse.parse_qsl(parsed.query))
            internal = self.headers.get(INTERNAL_HEADER) == "1"
            if path == "/health":
                return self._send(200, {"ok": True}, {})
            if path.startswith("/__world/"):
                if not internal:
                    return self._send(404, {"error": "not found"}, {})
                if path == "/__world/clock" and method == "POST":
                    body = self._body()
                    world.clock += int((body or {}).get("advance", 0))
                    return self._send(200, {"clock": world.clock}, {})
                if path == "/__world/verdict":
                    return self._send(200, {"complete": world.verdict()}, {})
                return self._send(404, {"error": "not found"}, {})
            if not internal:
                wait = world.tick(limited=path.startswith("/v1/"))
                if wait is not None:
                    return self._send(429, {"error": "rate limit exceeded"}, {"Retry-After": str(wait)})
            parts = path.strip("/").split("/")
            if parts[:2] == ["v1", "customers"] and method == "GET":
                if len(parts) == 2:
                    return self._send(*world.list_customers(query))
                if len(parts) == 3 and parts[2] == "count":
                    return self._send(*world.count_customers())
                if len(parts) == 3:
                    return self._send(*world.get_customer(urllib.parse.unquote(parts[2])))
            if parts[:2] == ["v2", "import"]:
                if len(parts) == 2 and method == "POST":
                    return self._send(*world.import_customers(self._body()))
                if len(parts) == 3 and parts[2] == "count" and method == "GET":
                    return self._send(*world.count_imported())
                if len(parts) == 3 and method == "DELETE":
                    return self._send(*world.remove_imported(urllib.parse.unquote(parts[2])))
            return self._send(404, {"error": f"no route {method} {path}"}, {})

        def do_GET(self) -> None:  # noqa: N802 - the base class's name
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._route("DELETE")

        def do_PUT(self) -> None:  # noqa: N802
            self._route("PUT")

        def do_PATCH(self) -> None:  # noqa: N802
            self._route("PATCH")

    return Handler


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(argv)
    server = HTTPServer(("127.0.0.1", args.port), handler_for(World(args.seed, args.state)))

    def watch_stdin() -> None:
        sys.stdin.read()
        server.shutdown()

    threading.Thread(target=watch_stdin, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
