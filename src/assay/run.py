"""The run model (docs/ARCHITECTURE.md section 6.3): one object, loaded once
per process, that every function below the entry points receives.

`Run.load(paths, strict=...)` reads the configuration, the pinned registry, the
journal (every line into an `Event`, hashed as it goes), `chain.json`, the
mutation log, the module manifest, the owner hash, the anchor file's seal and
the activity log's `liveness_waived` and `module_installed` records, and never
writes. What it finds about the record goes into `run.integrity`: a line that
does not decode, a contiguity problem, a chain file that is intact, absent,
behind by the one line a crash leaves (its `event_id` one below the last id
and its head equal to the head of that prefix), malformed, or diverged, and a
sealed anchor file (section 8.3: the daemon found a file changed under it and
ended the record there). `serve` and `start` load strict: a malformed line, a
contiguity problem or a diverged chain raises `CHAIN_DIVERGED`, a seal
`RUN_SEALED`, and nothing is rewritten, so the evidence of an edit stays on
disk. The readers (`status`, `audit`, `view` and the rest) load lenient and
report the finding; a line that does not decode ends the journal there, with
the events before it kept. An absent or crash-behind chain is held as the
recomputed head and written by the next `run.append`.

The waivers (`run.waivers`) are the `liveness_waived` records, rebuilt at every
load; the approvals (`run.approvals`) exist in the memory of the daemon that
granted them and nowhere else (section 7.2). The manifest the run holds is the
file as written, which `verify_disk` compares; the entries neither the pinned
registry nor a `module_installed` record admits are `run.unadmitted`, held but
never loaded (section 6.4).

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
from .integrity import (
    ANCHOR_EVERY,
    _advance,
    anchor_file,
    chain_over,
    chain_over_bytes,
    chain_path,
    seal_finding,
    seal_of,
)
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
    the journal's contiguity, the stored chain against the recomputed head,
    and a sealed anchor file. `chain_problem` is set whenever the chain is
    not intact or absent, in the words the audit reports; `sealed` is the
    seal's finding (section 8.3), after the journal's own problems."""

    contiguous: bool = True
    problem: str | None = None
    chain: str = CHAIN_ABSENT
    chain_problem: str | None = None
    malformed: str | None = None
    sealed: str | None = None

    @property
    def refused(self) -> str | None:
        """The reason a strict load refuses, or None."""
        if self.malformed is not None:
            return self.malformed
        if not self.contiguous:
            return self.problem
        if self.chain == CHAIN_DIVERGED:
            return self.chain_problem
        if self.sealed is not None:
            return self.sealed
        return None

    @property
    def refused_code(self) -> str | None:
        """The code of that refusal: `CHAIN_DIVERGED` for the journal's own
        problems, `RUN_SEALED` for the seal, None when nothing refuses."""
        if self.refused is None:
            return None
        if self.malformed is None and self.contiguous and self.chain != CHAIN_DIVERGED:
            return "RUN_SEALED"
        return "CHAIN_DIVERGED"


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
        f"{reason}; the run is refused and nothing is rewritten, "
        "so the record stays as it was found",
        code="CHAIN_DIVERGED",
        hint="`assay audit` names the problem; the run can be inspected (status, view, audit) but not resumed",
    )


def _sealed_refusal(reason: str, target: Any) -> AssayError:
    """The strict load's refusal over a sealed anchor file (section 8.3):
    the remedy is the operator's, in their own file outside the run."""
    return AssayError(
        f"{reason}; the run is refused and nothing is rewritten",
        code="RUN_SEALED",
        hint=(
            f"the anchor file is the operator's: remove the sealing line from {target} by "
            "hand to resume; the tamper_detected activity record stays as the record of "
            "what happened"
        ),
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
    unadmitted: set[str] = dataclasses.field(default_factory=set)
    modules: list[tuple[Module, str]] | None = None
    owner_hash: str | None = None
    waivers: set[str] = dataclasses.field(default_factory=set)
    approvals: dict[str, float] = dataclasses.field(default_factory=dict)
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
        from .agenda import load_waivers
        from .modules import load_manifest, unadmitted_entries
        from .registry import load_registry

        config = require_run(paths)
        journal = _load_journal(paths, strict=strict)
        if strict and journal.integrity.refused is not None:
            raise _refusal(journal.integrity.refused)
        # The seal comes after the journal's own problems: a sealed run whose
        # journal was also edited is refused for the edit first.
        target = anchor_file(paths, config)
        integrity = dataclasses.replace(journal.integrity, sealed=_sealed(target, journal.heads))
        if strict and integrity.sealed is not None:
            raise _sealed_refusal(integrity.sealed, target)
        mutations, mutations_bytes, mutations_hash = _load_mutations(paths)
        owner_hash = _owner_hash_of(read_json(paths.state / "owner.json", None))
        registry = load_registry(paths)
        manifest = load_manifest(paths)
        # The activity log is read here for the two facts the run holds
        # from it, the waivers and the manifest's installs, and not kept.
        activity = load_jsonl(paths.activity)
        return cls(
            paths=paths,
            config=config,
            registry=registry,
            events=journal.events,
            heads=journal.heads,
            chain_head=journal.head,
            chain_event=len(journal.events) - 1,
            journal_bytes=journal.size,
            mutations=mutations,
            mutations_bytes=mutations_bytes,
            mutations_hash=mutations_hash,
            manifest=manifest,
            unadmitted=unadmitted_entries(manifest, registry, activity),
            owner_hash=owner_hash,
            waivers=load_waivers(activity),
            integrity=integrity,
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

    def _anchor(self, event_id: int, *, seal: str | None = None) -> None:
        target = anchor_file(self.paths, self.config)
        record: dict[str, Any] = {"event_id": event_id, "head": self.chain_head}
        if seal is None:
            record["run"] = str(self.paths.root)
        else:
            record["seal"] = seal
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            append_jsonl(target, record)
        except OSError as error:
            # An unanchorable filesystem degrades to chain-only integrity. The
            # spend is never failed over it, but the failure is journaled to
            # activity and shown in status, never swallowed.
            failure: dict[str, Any] = {
                "kind": "anchor_failed",
                "event": event_id,
                "file": str(target),
                "error": f"{type(error).__name__}: {error}",
            }
            if seal is not None:
                failure["seal"] = seal
            append_jsonl(self.paths.activity, failure)

    def seal(self, reason: str) -> None:
        """The sealing record of section 8.3, `{"event_id": <held count - 1>,
        "head": <held head>, "seal": reason}`, appended to the anchor file by
        the daemon when a file changed under it: from here on the readers
        treat the journal as ending at the held event, whatever `chain.json`
        says, until the operator removes the line from their own file."""
        self._anchor(self.chain_event, seal=reason)

    def record_mutation(self, mutation: Mutation) -> None:
        """The daemon's write-ahead spend record, appended before the event;
        the held digest advances with the exact bytes written."""
        line = json.dumps(mutation.to_json(), separators=(",", ":"), sort_keys=True)
        _append_line(self.paths.mutations, line)
        self.mutations.append(mutation)
        written = (line + "\n").encode()
        self.mutations_bytes += len(written)
        self.mutations_hash.update(written)

    # -- verification (section 8.3; the daemon calls it before every spend) ---------

    def verify_disk(self) -> list[Tamper]:
        """Every difference between the files and the held copies: the
        journal's length and recomputed head (over the raw bytes, never
        decoded), the mutation log's length and digest, `chain.json` against
        the stored record, the registry, the configuration, `owner.json`
        against the held hash, and the module manifest. A file that cannot
        be read or parsed is a difference too, reported as `unreadable`."""
        found: list[Tamper] = []
        journal = _read_bytes(self.paths.events)
        if isinstance(journal, Exception):
            found.append(Tamper("events.jsonl", self.chain_head, f"unreadable: {journal}"))
        else:
            if len(journal) != self.journal_bytes:
                found.append(Tamper("events.jsonl length", str(self.journal_bytes), str(len(journal))))
            head = chain_over_bytes(journal)
            if head != self.chain_head:
                found.append(Tamper("events.jsonl head", self.chain_head, head))
        mutations = _read_bytes(self.paths.mutations)
        if isinstance(mutations, Exception):
            found.append(Tamper("mutations.jsonl", self.mutations_digest, f"unreadable: {mutations}"))
        else:
            if len(mutations) != self.mutations_bytes:
                found.append(
                    Tamper("mutations.jsonl length", str(self.mutations_bytes), str(len(mutations)))
                )
            digest = hashlib.sha256(mutations).hexdigest()
            if digest != self.mutations_digest:
                found.append(Tamper("mutations.jsonl digest", self.mutations_digest, digest))
        for what, held, path in (
            ("registry.json", self.registry, self.paths.registry),
            ("config.json", self.config, self.paths.config),
        ):
            expected = "absent" if held is None else _digest(held)
            stored = _read_json(path)
            if isinstance(stored, Exception):
                found.append(Tamper(what, expected, f"unreadable: {stored}"))
                continue
            actual = "absent" if stored is None else _digest(stored)
            if actual != expected:
                found.append(Tamper(what, expected, actual))
        # The chain file the next append would rewrite from the held head:
        # a replaced head or a deleted file is a difference, not a repair.
        chain = _read_json(chain_path(self.paths))
        if isinstance(chain, Exception):
            found.append(Tamper("chain.json", _chain_text(self.stored_chain), f"unreadable: {chain}"))
        elif chain != self.stored_chain:
            found.append(Tamper("chain.json", _chain_text(self.stored_chain), _chain_text(chain)))
        # The owner hash, which the owner operations are checked against.
        owner = _read_json(self.paths.state / "owner.json")
        expected_owner = self.owner_hash or "absent"
        if isinstance(owner, Exception):
            found.append(Tamper("owner.json", expected_owner, f"unreadable: {owner}"))
        else:
            actual_owner = _owner_hash_of(owner) or "absent"
            if actual_owner != expected_owner:
                found.append(Tamper("owner.json", expected_owner, actual_owner))
        from .modules import load_manifest

        try:
            manifest = load_manifest(self.paths)
        except (OSError, AssayError) as error:
            found.append(Tamper("modules/manifest.json", _digest(self.manifest), f"unreadable: {error}"))
        else:
            if manifest != self.manifest:
                found.append(Tamper("modules/manifest.json", _digest(self.manifest), _digest(manifest)))
        return found


def _read_bytes(path: Any) -> bytes | Exception:
    """The file's bytes, empty when absent, or the error that kept it from
    being read."""
    try:
        return bytes(path.read_bytes())
    except FileNotFoundError:
        return b""
    except OSError as error:
        return error


def _read_json(path: Any) -> Any:
    """The file's JSON value, None when absent, or the error that kept it
    from being read or parsed."""
    try:
        return read_json(path, None)
    except (OSError, AssayError) as error:
        return error


def _owner_hash_of(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("sha256"):
        return str(value["sha256"])
    return None


def _sealed(target: Any, heads: list[str]) -> str | None:
    """The finding of a sealed anchor file (section 8.3), or None: what the
    seal says and, when the journal changed after it, how. An anchor file
    that cannot be read is no seal; the audit reports it."""
    try:
        anchors = load_jsonl(target) if target.exists() else []
    except (OSError, AssayError):
        return None
    seal = seal_of(anchors)
    if seal is None:
        return None
    what, against = seal_finding(seal, heads)
    return f"anchor file {what}" + ("" if against is None else f"; {against}")


def _chain_text(value: Any) -> str:
    """A chain record for a difference line: `e<id> <head>` when well formed,
    `absent` when there is none, else the digest of whatever is there."""
    if value is None:
        return "absent"
    if isinstance(value, dict) and isinstance(value.get("event_id"), int) and isinstance(value.get("head"), str):
        return f"e{value['event_id']} {value['head']}"
    return _digest(value)


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
    decode (as JSON, as a record, or as UTF-8) is a finding: the lenient
    reader keeps the events before it and treats the journal as ending
    there; the strict one refuses."""
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
    for number, raw in enumerate(data.splitlines(keepends=True), 1):
        try:
            line = raw.decode().rstrip("\r\n")
        except UnicodeDecodeError as error:
            # A byte that is not UTF-8 is a malformed line like any other:
            # the readers report it, the strict load refuses.
            malformed = f"line {number} malformed: {error}"
            if strict:
                raise _refusal(malformed) from error
            break
        if not line.strip():
            size += len(raw)
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
        size += len(raw)
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
