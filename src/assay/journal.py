"""The record of a run.

One directory is one run, and its state lives under `.assay/`. The journal,
`events.jsonl`, holds one JSON line per event: event 0 is the first
observation, every later event is one paid action with the observation the
world returned, the prediction made before it and the grade of each outcome.
Nothing else is a source of truth: facts, misses and meters are views over
these lines.

The lines are hash-chained, head_0 = sha256("assay-chain-v1") and
head_n = sha256(head_{n-1} + line_n). The head is written beside the journal
after every append and anchored outside the run directory every 25 events and
on WIN, so an edit of the journal is detectable by `audit`. The line format is
the published `assay-journal-v1`: the keys and their spelling do not change.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import AssayError

Event = dict[str, Any]

JOURNAL_SPEC = "assay-journal-v1"
CHAIN_SEED = "assay-chain-v1"
ANCHOR_EVERY = 25


@dataclasses.dataclass(frozen=True)
class Paths:
    """Where a run keeps its files. The daemon writes all of them except the
    notes, which are the agent's."""

    root: Path

    @property
    def state(self) -> Path:
        return self.root / ".assay"

    @property
    def config(self) -> Path:
        return self.state / "config.json"

    @property
    def registry(self) -> Path:
        return self.state / "registry.json"

    @property
    def events(self) -> Path:
        return self.state / "events.jsonl"

    @property
    def chain(self) -> Path:
        return self.state / "chain.json"

    @property
    def states(self) -> Path:
        return self.state / "states.json"

    @property
    def programs(self) -> Path:
        """Agent-written verifiers and extractors, stored under their hash."""
        return self.state / "programs"

    @property
    def refusals(self) -> Path:
        return self.state / "refusals.jsonl"

    @property
    def notes(self) -> Path:
        return self.state / "NOTES.md"

    @property
    def images(self) -> Path:
        return self.state / "images"

    @property
    def daemon(self) -> Path:
        return self.state / "daemon.json"

    @property
    def log(self) -> Path:
        return self.state / "daemon.log"

    @property
    def socket(self) -> Path:
        # A Unix socket path is capped near 104 bytes, so it is a short name
        # under the temp directory, keyed by the run directory.
        digest = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:24]
        tail = Path(f"assay-{os.getuid()}") / f"{digest}.sock"
        candidate = Path(tempfile.gettempdir()) / tail
        return candidate if len(str(candidate).encode()) <= 90 else Path("/tmp") / tail

    @property
    def anchors(self) -> Path:
        """The anchor file, outside the run directory."""
        base = os.getenv("ASSAY_ANCHOR_DIR")
        directory = Path(base).expanduser() if base else Path.home() / ".assay" / "anchors"
        digest = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:24]
        return directory / f"{digest}.jsonl"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_notes(paths: Paths) -> str | None:
    """The agent's notes, or None when there are none. They are the agent's
    file, so nothing about their bytes is assumed."""
    try:
        return paths.notes.read_text(errors="replace")
    except OSError:
        return None


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as error:
        raise AssayError(f"corrupt JSON in {path}: {error}", code="RECORD_CORRUPT") from error


def write_json(path: Path, value: Any) -> None:
    """Write the file whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n")
    os.replace(temporary, path)


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    _append(path, json.dumps({"timestamp": now(), **record}, separators=(",", ":"), sort_keys=True))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    records = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise AssayError(f"corrupt line {number} in {path}: {error}", code="RECORD_CORRUPT") from error
    return records


# --- events ---------------------------------------------------------------------


def make_event(
    observed: Mapping[str, Any],
    action: str,
    data: Mapping[str, Any] | None,
    previous: Event | None,
    note: str = "",
) -> Event:
    """The event of one observation (`world.observe` gives its shape). The id
    is assigned when the event is appended."""
    event: Event = {
        "timestamp": now(),
        "action": action,
        "data": dict(data) if data else None,
        "counts_action": action != "START",
        "state": observed["state"],
        "levels_completed": observed["levels_completed"],
        "level_before": None if previous is None else previous["levels_completed"],
        "win_levels": observed["win_levels"],
        "available_actions": list(observed["available_actions"]),
        "note": note,
    }
    if "frames" in observed:
        event["frames"] = observed["frames"]
        event["n_frames"] = len(observed["frames"])
    else:
        event["observation"] = observed["data"]
    return event


def observation_of(event: Event) -> dict[str, Any]:
    """The observation an event recorded, in the shape the adapter gave it:
    what a replay compares and what a verifier or an extractor receives."""
    view: dict[str, Any] = {
        "state": event["state"],
        "levels_completed": event["levels_completed"],
        "win_levels": event["win_levels"],
        "available_actions": list(event["available_actions"]),
    }
    if "frames" in event:
        view["frames"] = event["frames"]
    else:
        view["data"] = event.get("observation")
    return view


def is_paid(event: Event) -> bool:
    return bool(event.get("counts_action"))


def advanced(event: Event) -> bool:
    """Whether this event completed a progress unit."""
    before = event.get("level_before")
    return before is not None and event["levels_completed"] > before


def is_ungated(event: Event) -> bool:
    """A paid action other than a reset that carries no prediction and no
    grade. The gate never writes one; the audit proves it of every line."""
    if not is_paid(event) or event["action"] == "RESET":
        return False
    return not (event.get("predict") or event.get("predict_ok") is not None or event.get("grade"))


def grades(event: Event) -> list[dict[str, Any]]:
    return list(event.get("grade") or ())


# --- the chain -------------------------------------------------------------------


def seed() -> str:
    return hashlib.sha256(CHAIN_SEED.encode()).hexdigest()


def advance(head: str, line: str) -> str:
    return hashlib.sha256(head.encode() + line.encode()).hexdigest()


def _heads(lines: Sequence[str]) -> list[str]:
    """The head after each journal line."""
    heads, head = [], seed()
    for line in lines:
        head = advance(head, line)
        heads.append(head)
    return heads


def _journal_lines(paths: Paths) -> list[str]:
    try:
        return [line for line in paths.events.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        return []


# --- the run ---------------------------------------------------------------------


@dataclasses.dataclass
class Run:
    """A run as every process reads it: where it lives, how it was started,
    the registry pinned at start, and every event. The daemon is the one
    process that appends."""

    paths: Paths
    config: dict[str, Any]
    registry: dict[str, Any]
    events: list[Event]
    head: str

    @classmethod
    def load(cls, paths: Paths) -> Run:
        config = read_json(paths.config)
        registry = read_json(paths.registry)
        if not isinstance(config, dict) or not isinstance(registry, dict):
            raise AssayError(
                f"{paths.root} holds no run",
                code="RUN_MISSING",
                hint="the operator starts one here with `assay start WORLD_ID --adapter FILE.py --registry FILE.json`",
            )
        lines = _journal_lines(paths)
        events = [dict(event) for event in read_jsonl(paths.events)]
        heads = _heads(lines)
        return cls(paths, config, registry, events, heads[-1] if heads else seed())

    def check_chain(self) -> None:
        """Refuse a journal that no longer matches its stored head. A head one
        line behind is a crash between the two writes, which the next append
        repairs."""
        heads = _heads(_journal_lines(self.paths))
        problem = _contiguity(self.events)
        if problem is None and heads:
            stored = _stored_head(self.paths)
            last = stored.get("last_id")
            if not isinstance(last, int) or not max(0, len(heads) - 2) <= last < len(heads) or heads[last] != stored.get("head"):
                problem = "the journal does not match the head stored in chain.json"
        if problem is not None:
            raise AssayError(problem, code="CHAIN_DIVERGED", hint="`assay audit` reports what differs")

    def append(self, event: Event) -> Event:
        """Assign the next id, write the line, advance the head, anchor."""
        event = {"id": len(self.events), **event}
        if not event["id"]:
            self.paths.anchors.unlink(missing_ok=True)  # a new run in this directory starts its own anchors
        line = json.dumps(event, separators=(",", ":"), sort_keys=True)
        _append(self.paths.events, line)
        self.head = advance(self.head, line)
        self.events.append(event)
        write_json(self.paths.chain, {"last_id": event["id"], "head": self.head})
        if (event["id"] and event["id"] % ANCHOR_EVERY == 0) or event["state"] == "WIN":
            try:
                append_jsonl(self.paths.anchors, {"event_id": event["id"], "head": self.head})
            except OSError:
                pass  # an unwritable anchor directory leaves the run chain-only
        return event

    @property
    def last(self) -> Event:
        return self.events[-1]

    @property
    def paid(self) -> int:
        return sum(1 for event in self.events if is_paid(event))

    @property
    def cap(self) -> int | None:
        cap = (self.registry.get("budget") or {}).get("actions")
        return None if cap is None else int(cap)


# --- the audit -------------------------------------------------------------------


def _stored_head(paths: Paths) -> dict[str, Any]:
    """`chain.json`, or nothing when it is missing or is not what the daemon writes."""
    try:
        stored = read_json(paths.chain)
    except AssayError:
        return {}
    return stored if isinstance(stored, dict) else {}


def _contiguity(events: Sequence[Event]) -> str | None:
    for index, event in enumerate(events):
        if event.get("id") != index:
            return f"line {index + 1} carries id {event.get('id')!r}, expected {index}"
    return None


def audit(paths: Paths) -> tuple[bool, list[str]]:
    """Recompute the record's integrity from the files: ids contiguous, the
    chain against its stored head and its last anchor, no ungated event. Returns
    whether the record is clean, and the lines of the report. The pinned
    registry is held against the hash recorded at start as well."""
    lines = _journal_lines(paths)
    problems: list[str] = []
    events: list[Event] = []
    for number, line in enumerate(lines, 1):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            problems.append(f"journal: line {number} is not JSON")
            break
    contiguity = _contiguity(events) if not problems else "the journal does not parse"
    if contiguity is not None and not problems:
        problems.append(f"contiguity: {contiguity}")
    heads = _heads(lines)
    stored = _stored_head(paths)
    if heads and stored.get("last_id") == len(heads) - 1 and stored.get("head") == heads[-1]:
        chain = "intact"
    else:
        chain = "DIVERGED" if stored else "absent"
        problems.append("chain: the journal does not match the head stored in chain.json")
    from .world import registry_hash

    config, registry = read_json(paths.config, {}), read_json(paths.registry, {})
    if registry_hash(registry) != config.get("registry_hash"):
        problems.append("registry: the pinned registry does not match the hash recorded at start")
    try:
        anchors = read_jsonl(paths.anchors)
    except (AssayError, OSError):
        anchors = []
        problems.append(f"anchor: {paths.anchors} cannot be read")
    anchored = "none"
    if anchors:
        latest = anchors[-1]
        target = latest.get("event_id")
        if isinstance(target, int) and target < len(heads) and heads[target] == latest.get("head"):
            anchored = "intact"
        else:
            anchored = "DIVERGED"
            problems.append("anchor: the journal no longer matches the head anchored outside the run directory")
    ungated = [event["id"] for event in events if is_ungated(event)] if contiguity is None else []
    clean = not problems and not ungated
    paid = sum(1 for event in events if is_paid(event))
    report = [
        f"AUDIT | {'CLEAN' if clean else 'INVALID FOR SCORING'} | events {len(events)} (paid {paid}) | "
        f"contiguous {'yes' if contiguity is None else 'NO'} | chain {chain} | "
        f"anchors {anchored} ({len(anchors)})"
    ]
    if ungated:
        report.append(f"AUDIT | UNGATED events {ungated[:8]}: paid actions that carry no prediction")
    report.extend(f"AUDIT | problem: {problem}" for problem in problems)
    return clean, report
