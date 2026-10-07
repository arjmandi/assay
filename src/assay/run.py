"""The run model (docs/ARCHITECTURE.md section 6.3): one object, loaded once
per process, that every function below the entry points receives.

`Run.load(paths, strict=...)` reads the configuration, the pinned registry, the
journal (every line into an `Event`, hashed as it goes), `chain.json`, the
mutation log, the module manifest and the owner hash, and never writes. What it
finds about the record goes into `run.integrity`: a contiguity problem, and a
chain file that is intact, absent, behind by the lines a crash left (its
`event_id` strictly less than the last id and its head equal to the head of
that prefix), or diverged. `serve` and `start` load strict: a contiguity
problem or a diverged chain raises `CHAIN_DIVERGED` and nothing is rewritten,
so the evidence of an edit stays on disk. The readers (`status`, `audit`,
`view` and the rest) load lenient and report the finding. An absent or
crash-behind chain is held as the recomputed head and written by the next
`run.append`.

`run.append(pending)` is the one writer of `events.jsonl` while a daemon lives
(the one append without a daemon is `reconcile` at start): it assigns
`id = len(events)`, serializes, appends under the file lock with fsync,
advances the held head with the exact line written, writes `chain.json`,
anchors when due (every 25 events and on WIN), and appends to `events`.

The small files beside the journal (the activity log, `channels.json`, the
readings cache, the verifier statistics, hazards, aggregates, the agenda
files, the notes) stay on disk and are read on demand: each has one writer.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from typing import Any

from .core import (
    AssayError,
    RunPaths,
    append_jsonl,
    atomic_json,
    load_jsonl,
    read_json,
    require_run,
)
from .integrity import ANCHOR_EVERY, CHAIN_SEED, _advance, anchor_file, chain_path
from .records import Event, Mutation

CHAIN_INTACT = "intact"
CHAIN_ABSENT = "absent"
CHAIN_BEHIND = "behind"
CHAIN_DIVERGED = "DIVERGED"


@dataclasses.dataclass(frozen=True, slots=True)
class Integrity:
    """What the loader found about the record: the journal's contiguity and
    the stored chain against the recomputed head."""

    contiguous: bool = True
    problem: str | None = None
    chain: str = CHAIN_ABSENT
    chain_problem: str | None = None

    @property
    def refused(self) -> str | None:
        """The reason a strict load refuses, or None."""
        if not self.contiguous:
            return self.problem
        if self.chain == CHAIN_DIVERGED:
            return self.chain_problem
        return None


@dataclasses.dataclass(frozen=True, slots=True)
class Tamper:
    """One difference between a file on disk and the held copy."""

    what: str
    expected: str
    found: str


def _seed_head() -> str:
    return hashlib.sha256(CHAIN_SEED.encode()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _file_digest(path: Any) -> str:
    value = read_json(path, None)
    return "absent" if value is None else _digest(value)


@dataclasses.dataclass
class Run:
    paths: RunPaths
    config: dict[str, Any]
    registry: dict[str, Any] | None = None
    events: list[Event] = dataclasses.field(default_factory=list)
    chain_head: str = dataclasses.field(default_factory=_seed_head)
    chain_event: int = -1
    journal_bytes: int = 0
    mutations: list[Mutation] = dataclasses.field(default_factory=list)
    mutations_bytes: int = 0
    manifest: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    modules: list[tuple[Any, str]] | None = None
    owner_hash: str | None = None
    opened_at: float = dataclasses.field(default_factory=time.time)
    integrity: Integrity = dataclasses.field(default_factory=Integrity)
    stored_chain: dict[str, Any] | None = None

    # -- loading -----------------------------------------------------------------

    @classmethod
    def load(cls, paths: RunPaths, *, strict: bool) -> Run:
        from .modules import load_manifest
        from .registry import load_registry

        config = require_run(paths)
        events, head, journal_bytes, integrity, stored = _load_journal(paths)
        if strict and integrity.refused is not None:
            raise AssayError(
                f"CHAIN_DIVERGED | {integrity.refused}; the run is refused and nothing is "
                "rewritten, so the record stays as it was found"
            )
        mutations, mutations_bytes = _load_mutations(paths)
        owner = read_json(paths.state / "owner.json", None)
        owner_hash = (
            str(owner["sha256"]) if isinstance(owner, dict) and owner.get("sha256") else None
        )
        return cls(
            paths=paths,
            config=config,
            registry=load_registry(paths),
            events=events,
            chain_head=head,
            chain_event=len(events) - 1,
            journal_bytes=journal_bytes,
            mutations=mutations,
            mutations_bytes=mutations_bytes,
            manifest=load_manifest(paths),
            owner_hash=owner_hash,
            integrity=integrity,
            stored_chain=stored,
        )

    # -- the one writer ----------------------------------------------------------

    def append(self, pending: Event) -> Event:
        """Append one event: the id is the held count, the line is written
        under the file lock with fsync, the head advances with the exact line,
        `chain.json` follows, and the head is anchored when due."""
        event = pending.updated(id=len(self.events))
        line = json.dumps(event.to_json(), separators=(",", ":"), sort_keys=True)
        self.paths.state.mkdir(parents=True, exist_ok=True)
        with self.paths.events.open("a") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except ImportError:
                pass
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.chain_head = _advance(self.chain_head, line)
        self.chain_event = event.id
        self.stored_chain = {"event_id": event.id, "head": self.chain_head}
        atomic_json(chain_path(self.paths), self.stored_chain)
        self.integrity = dataclasses.replace(self.integrity, chain=CHAIN_INTACT, chain_problem=None)
        self.events.append(event)
        self.journal_bytes += len(line.encode()) + 1
        if event.state == "WIN" or (event.id > 0 and event.id % ANCHOR_EVERY == 0):
            self._anchor(event.id)
        return event

    def _anchor(self, event_id: int) -> None:
        target = anchor_file(self.paths, self.config)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            append_jsonl(
                target,
                {"event_id": event_id, "head": self.chain_head, "run": str(self.paths.root)},
            )
        except OSError as error:
            # An unanchorable filesystem degrades to chain-only integrity. The
            # spend is never failed over it, but the failure is journaled to
            # activity and shown in status, never swallowed.
            append_jsonl(
                self.paths.activity,
                {
                    "kind": "anchor_failed",
                    "event": event_id,
                    "file": str(target),
                    "error": f"{type(error).__name__}: {error}",
                },
            )

    def record_mutation(self, mutation: Mutation) -> None:
        """The daemon's write-ahead spend record, appended before the event."""
        line = json.dumps(mutation.to_json(), separators=(",", ":"), sort_keys=True)
        append_jsonl(self.paths.mutations, mutation.to_json())
        self.mutations.append(mutation)
        self.mutations_bytes += len(line.encode()) + 1

    # -- verification (section 8.3; the daemon wires it in #20) ---------------------

    def verify_disk(self) -> list[Tamper]:
        """Every difference between the files and the held copies: the
        journal's length and recomputed head, the mutation log, the registry,
        the configuration and the module manifest."""
        found: list[Tamper] = []
        try:
            journal = self.paths.events.read_bytes()
        except FileNotFoundError:
            journal = b""
        head = _seed_head()
        for line in journal.decode().splitlines():
            if line.strip():
                head = _advance(head, line)
        if len(journal) != self.journal_bytes:
            found.append(Tamper("events.jsonl length", str(self.journal_bytes), str(len(journal))))
        if head != self.chain_head:
            found.append(Tamper("events.jsonl head", self.chain_head, head))
        try:
            mutations = len(self.paths.mutations.read_bytes())
        except FileNotFoundError:
            mutations = 0
        if mutations != self.mutations_bytes:
            found.append(Tamper("mutations.jsonl length", str(self.mutations_bytes), str(mutations)))
        for what, held, path in (
            ("registry.json", self.registry, self.paths.registry),
            ("config.json", self.config, self.paths.config),
        ):
            expected = "absent" if held is None else _digest(held)
            actual = _file_digest(path)
            if actual != expected:
                found.append(Tamper(what, expected, actual))
        from .modules import load_manifest

        manifest = load_manifest(self.paths)
        if manifest != self.manifest:
            found.append(Tamper("modules/manifest.json", _digest(self.manifest), _digest(manifest)))
        return found


def _load_journal(
    paths: RunPaths,
) -> tuple[list[Event], str, int, Integrity, dict[str, Any] | None]:
    """Every line into an Event, the head recomputed over the raw lines, the
    file's length, the integrity findings, and `chain.json` as stored."""
    try:
        data = paths.events.read_bytes()
    except FileNotFoundError:
        data = b""
    stored = read_json(chain_path(paths), None)
    if not isinstance(stored, dict):
        stored = None
    stored_id = int(stored.get("event_id", -2)) if stored is not None else None
    head = _seed_head()
    prefix_head: str | None = None
    events: list[Event] = []
    problem: str | None = None
    for line in data.decode().splitlines():
        if not line.strip():
            continue
        head = _advance(head, line)
        index = len(events)
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as error:
            raise AssayError(
                f"corrupt JSONL in {paths.events} line {index + 1}: {error}"
            ) from error
        event = Event.from_json(obj)
        if problem is None and event.id != index:
            problem = f"event timeline is not contiguous at line {index + 1}"
        if stored_id is not None and index == stored_id:
            prefix_head = head
        events.append(event)
    last_id = len(events) - 1
    chain = CHAIN_ABSENT
    chain_problem: str | None = None
    if stored is not None:
        if stored_id == last_id and stored.get("head") == head:
            chain = CHAIN_INTACT
        elif stored_id is not None and stored_id < last_id and prefix_head == stored.get("head"):
            chain = CHAIN_BEHIND
        else:
            chain = CHAIN_DIVERGED
            chain_problem = (
                f"chain: stored head at e{stored.get('event_id')} does not match "
                f"the recomputed journal head at e{last_id}"
            )
    integrity = Integrity(
        contiguous=problem is None, problem=problem, chain=chain, chain_problem=chain_problem
    )
    return events, head, len(data), integrity, stored


def _load_mutations(paths: RunPaths) -> tuple[list[Mutation], int]:
    try:
        size = paths.mutations.stat().st_size
    except FileNotFoundError:
        return [], 0
    return [Mutation.from_json(item) for item in load_jsonl(paths.mutations)], size
