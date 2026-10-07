"""The run model (docs/ARCHITECTURE.md section 6.3): one object, loaded once
per process, that every function below the entry points receives.

`Run.load(paths, strict=...)` reads the configuration, the pinned registry, the
journal (every line into an `Event`, hashed as it goes), `chain.json`, the
mutation log, the module manifest and the owner hash, and never writes. What it
finds about the record goes into `run.integrity`: a line that does not decode,
a contiguity problem, and a chain file that is intact, absent, behind by the
one line a crash leaves (its `event_id` one below the last id and its head
equal to the head of that prefix), malformed, or diverged. `serve` and `start`
load strict: a malformed line, a contiguity problem or a diverged chain raises
`CHAIN_DIVERGED` and nothing is rewritten, so the evidence of an edit stays on
disk. The readers (`status`, `audit`, `view` and the rest) load lenient and
report the finding; a line that does not decode ends the journal there, with
the events before it kept. An absent or crash-behind chain is held as the
recomputed head and written by the next `run.append`.

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
from typing import TYPE_CHECKING, Any, Protocol

from .core import (
    AssayError,
    RunPaths,
    append_jsonl,
    atomic_json,
    load_jsonl,
    read_json,
    require_run,
)
from .integrity import ANCHOR_EVERY, _advance, anchor_file, chain_over, chain_path
from .records import Event, Mutation

if TYPE_CHECKING:
    from .modules import Module

CHAIN_INTACT = "intact"
CHAIN_ABSENT = "absent"
CHAIN_BEHIND = "behind"
CHAIN_DIVERGED = "DIVERGED"


class _Hasher(Protocol):
    def update(self, data: bytes, /) -> None: ...
    def hexdigest(self) -> str: ...


@dataclasses.dataclass(frozen=True, slots=True)
class Integrity:
    """What the loader found about the record: a line that does not decode,
    the journal's contiguity, and the stored chain against the recomputed
    head. `chain_problem` is set whenever the chain is not intact or absent,
    in the words the audit reports."""

    contiguous: bool = True
    problem: str | None = None
    chain: str = CHAIN_ABSENT
    chain_problem: str | None = None
    malformed: str | None = None

    @property
    def refused(self) -> str | None:
        """The reason a strict load refuses, or None."""
        if self.malformed is not None:
            return self.malformed
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


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _file_digest(path: Any) -> str:
    value = read_json(path, None)
    return "absent" if value is None else _digest(value)


def _refusal(reason: str) -> AssayError:
    return AssayError(
        f"CHAIN_DIVERGED | {reason}; the run is refused and nothing is rewritten, "
        "so the record stays as it was found"
    )


@dataclasses.dataclass
class Run:
    paths: RunPaths
    config: dict[str, Any]
    registry: dict[str, Any] | None = None
    events: list[Event] = dataclasses.field(default_factory=list)
    heads: list[str] = dataclasses.field(default_factory=list)
    chain_head: str = dataclasses.field(default_factory=lambda: chain_over(()))
    chain_event: int = -1
    journal_bytes: int = 0
    mutations: list[Mutation] = dataclasses.field(default_factory=list)
    mutations_bytes: int = 0
    mutations_hash: _Hasher = dataclasses.field(default_factory=hashlib.sha256)
    manifest: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    modules: list[tuple[Module, str]] | None = None
    owner_hash: str | None = None
    opened_at: float = dataclasses.field(default_factory=time.time)
    integrity: Integrity = dataclasses.field(default_factory=Integrity)
    stored_chain: dict[str, Any] | None = None

    @property
    def mutations_digest(self) -> str:
        """The sha256 of the mutation log's bytes as held."""
        return self.mutations_hash.hexdigest()

    # -- loading -----------------------------------------------------------------

    @classmethod
    def load(cls, paths: RunPaths, *, strict: bool) -> Run:
        from .modules import load_manifest
        from .registry import load_registry

        config = require_run(paths)
        journal = _load_journal(paths, strict=strict)
        if strict and journal.integrity.refused is not None:
            raise _refusal(journal.integrity.refused)
        mutations, mutations_bytes, mutations_hash = _load_mutations(paths)
        owner = read_json(paths.state / "owner.json", None)
        owner_hash = (
            str(owner["sha256"]) if isinstance(owner, dict) and owner.get("sha256") else None
        )
        return cls(
            paths=paths,
            config=config,
            registry=load_registry(paths),
            events=journal.events,
            heads=journal.heads,
            chain_head=journal.head,
            chain_event=len(journal.events) - 1,
            journal_bytes=journal.size,
            mutations=mutations,
            mutations_bytes=mutations_bytes,
            mutations_hash=mutations_hash,
            manifest=load_manifest(paths),
            owner_hash=owner_hash,
            integrity=journal.integrity,
            stored_chain=journal.stored,
        )

    # -- the one writer ----------------------------------------------------------

    def append(self, pending: Event) -> Event:
        """Append one event: the id is the held count, the line is written
        under the file lock with fsync, the head advances with the exact line,
        `chain.json` follows, and the head is anchored when due."""
        event = pending.updated(id=len(self.events))
        line = json.dumps(event.to_json(), separators=(",", ":"), sort_keys=True)
        _append_line(self.paths.events, line)
        self.chain_head = _advance(self.chain_head, line)
        self.chain_event = event.id
        self.heads.append(self.chain_head)
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
        """The daemon's write-ahead spend record, appended before the event;
        the held digest advances with the exact bytes written."""
        line = json.dumps(mutation.to_json(), separators=(",", ":"), sort_keys=True)
        _append_line(self.paths.mutations, line)
        self.mutations.append(mutation)
        written = (line + "\n").encode()
        self.mutations_bytes += len(written)
        self.mutations_hash.update(written)

    # -- verification (section 8.3; the daemon wires it in #20) ---------------------

    def verify_disk(self) -> list[Tamper]:
        """Every difference between the files and the held copies: the
        journal's length and recomputed head, the mutation log's length and
        digest, the registry, the configuration and the module manifest."""
        found: list[Tamper] = []
        try:
            journal = self.paths.events.read_bytes()
        except FileNotFoundError:
            journal = b""
        head = chain_over(journal.decode().splitlines())
        if len(journal) != self.journal_bytes:
            found.append(Tamper("events.jsonl length", str(self.journal_bytes), str(len(journal))))
        if head != self.chain_head:
            found.append(Tamper("events.jsonl head", self.chain_head, head))
        try:
            mutations = self.paths.mutations.read_bytes()
        except FileNotFoundError:
            mutations = b""
        if len(mutations) != self.mutations_bytes:
            found.append(Tamper("mutations.jsonl length", str(self.mutations_bytes), str(len(mutations))))
        digest = hashlib.sha256(mutations).hexdigest()
        if digest != self.mutations_digest:
            found.append(Tamper("mutations.jsonl digest", self.mutations_digest, digest))
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


def _append_line(path: Any, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@dataclasses.dataclass(frozen=True, slots=True)
class _Journal:
    events: list[Event]
    heads: list[str]
    head: str
    size: int
    integrity: Integrity
    stored: dict[str, Any] | None


def _stored_chain(paths: RunPaths) -> tuple[dict[str, Any] | None, str | None]:
    """`chain.json` as stored: (the record, None) when it is well formed,
    (None, None) when absent, (None, the malformation) otherwise. Well formed
    is `{event_id: int, head: str}`; other keys are allowed."""
    path = chain_path(paths)
    if not path.exists():
        return None, None
    try:
        stored = read_json(path, None)
    except AssayError as error:
        return None, f"chain.json is not valid JSON ({error})"
    if not isinstance(stored, dict):
        return None, f"chain.json holds {type(stored).__name__}, not an object"
    event_id = stored.get("event_id")
    head = stored.get("head")
    if isinstance(event_id, bool) or not isinstance(event_id, int) or not isinstance(head, str):
        return None, (
            "chain.json is malformed: expected {event_id: int, head: str}, got "
            f"event_id {type(event_id).__name__} and head {type(head).__name__}"
        )
    return stored, None


def _load_journal(paths: RunPaths, *, strict: bool) -> _Journal:
    """Every line into an Event, the head after each line, the file's length,
    the integrity findings, and `chain.json` as stored. A line that does not
    decode is a finding: the lenient reader keeps the events before it and
    treats the journal as ending there; the strict one refuses."""
    try:
        data = paths.events.read_bytes()
    except FileNotFoundError:
        data = b""
    stored, chain_malformed = _stored_chain(paths)
    stored_id = int(stored["event_id"]) if stored is not None else None
    head = chain_over(())
    heads: list[str] = []
    prefix_head: str | None = None
    events: list[Event] = []
    problem: str | None = None
    malformed: str | None = None
    size = 0
    for number, raw in enumerate(data.decode().splitlines(keepends=True), 1):
        line = raw.rstrip("\r\n")
        if not line.strip():
            size += len(raw.encode())
            continue
        try:
            event = Event.from_json(json.loads(line))
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            reason = (
                f"missing key {error}" if isinstance(error, KeyError) else str(error)
            )
            malformed = f"line {number} malformed: {reason}"
            if strict:
                raise _refusal(malformed) from error
            break
        head = _advance(head, line)
        index = len(events)
        if problem is None and event.id != index:
            problem = f"event timeline is not contiguous at line {index + 1}"
        if stored_id is not None and index == stored_id:
            prefix_head = head
        events.append(event)
        heads.append(head)
        size += len(raw.encode())
    last_id = len(events) - 1
    chain = CHAIN_ABSENT
    chain_problem: str | None = None
    if chain_malformed is not None:
        chain = CHAIN_DIVERGED
        chain_problem = f"chain: {chain_malformed}"
    elif stored is not None:
        assert stored_id is not None
        mismatch = (
            f"chain: stored head at e{stored_id} does not match "
            f"the recomputed journal head at e{last_id}"
        )
        if stored_id == last_id and stored["head"] == head:
            chain = CHAIN_INTACT
        elif stored_id == last_id - 1 and prefix_head == stored["head"]:
            # The one line a crash leaves: the event was appended, the chain
            # file was not; the next append repairs it.
            chain = CHAIN_BEHIND
            chain_problem = mismatch
        else:
            chain = CHAIN_DIVERGED
            chain_problem = mismatch
    integrity = Integrity(
        contiguous=problem is None,
        problem=problem,
        chain=chain,
        chain_problem=chain_problem,
        malformed=malformed,
    )
    return _Journal(events, heads, head, size, integrity, stored)


def _load_mutations(paths: RunPaths) -> tuple[list[Mutation], int, _Hasher]:
    hasher = hashlib.sha256()
    try:
        data = paths.mutations.read_bytes()
    except FileNotFoundError:
        return [], 0, hasher
    hasher.update(data)
    return [Mutation.from_json(item) for item in load_jsonl(paths.mutations)], len(data), hasher
