"""Journal integrity: rolling hash chain, anchoring, the ungated-event audit,
and secrets redaction at the journal boundary.

The chain makes the journal TAMPER-EVIDENT under stated conditions, not
unforgeable, and says so out loud: head_0 = sha256("assay-chain-v1"),
head_n = sha256(head_{n-1} || line_n). The daemon advances the held head with
every line `run.append` writes, updates `.assay/chain.json` after each append
and anchors the head OUTSIDE the run directory
(`~/.assay/anchors/<run-digest>.jsonl`, override with ASSAY_ANCHOR_DIR) every
ANCHOR_EVERY events and on WIN (`run.Run.append`). `assay audit` recomputes
everything from the journal and reports:

- chain intact / diverged (and against the anchors),
- event-id contiguity,
- UNGATED events: a paid non-RESET event carrying no prediction and no grade
  (the definition the archived bypass audits pre-registered). An ungated event
  INVALIDATES THE RUN FOR SCORING and demotes all trust earned after it: the
  gate refuses model-plan batching once one exists.

Secrets redaction: values of registered secret env vars (plus the kernel's
default list) never enter the journal; every agent-supplied string field is
filtered at the boundary before it is written.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core import AssayError, RunPaths, atomic_json, load_jsonl
from .records import Event, read_int, read_str

if TYPE_CHECKING:
    from .run import Run

ANCHOR_EVERY = 25
CHAIN_SEED = "assay-chain-v1"
# The seal the daemon writes into the anchor file on a tamper (section 8.3).
TAMPER_SEAL = "tamper_detected"
# Env names whose values are always redacted, registered or not.
DEFAULT_SECRET_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
_MIN_SECRET_LENGTH = 8  # never redact trivially short values ("1", "true", ...)


def chain_path(paths: RunPaths) -> Path:
    return paths.state / "chain.json"


def anchor_dir() -> Path:
    """The anchor directory the environment names right now."""
    configured = os.getenv("ASSAY_ANCHOR_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".assay" / "anchors"


def environment_anchor_file(paths: RunPaths) -> Path:
    digest = hashlib.sha256(str(paths.root.resolve()).encode()).hexdigest()[:24]
    return anchor_dir() / f"{digest}.jsonl"


def recorded_anchor_file(config: Mapping[str, Any] | None) -> Path | None:
    """The anchor file pinned in config.json at start, or None for a run that
    predates the key."""
    if isinstance(config, Mapping) and isinstance(config.get("anchor_file"), str):
        return Path(config["anchor_file"])
    return None


def anchor_file(paths: RunPaths, config: Mapping[str, Any] | None) -> Path:
    """Where this run's chain heads go: the file recorded in config.json at
    start, so a later shell with a different ASSAY_ANCHOR_DIR still anchors and
    audits against the same file. Runs without the key use the environment."""
    recorded = recorded_anchor_file(config)
    return recorded if recorded is not None else environment_anchor_file(paths)


def anchor_dir_writable(target: Path) -> bool:
    """Whether the anchor file's directory can be written now, without side
    effects: the nearest existing ancestor must be a writable directory (a
    file in the way is the common failure)."""
    ancestor = target.parent
    while not ancestor.exists() and ancestor.parent != ancestor:
        ancestor = ancestor.parent
    return ancestor.is_dir() and os.access(ancestor, os.W_OK)


def require_anchor_dir(target: Path) -> None:
    """`assay start` refuses an anchor directory it cannot write (section
    8.3): the run's chain heads and its seal go there, and a seal that
    cannot be written is a detection nobody sees."""
    if anchor_dir_writable(target):
        return
    raise AssayError(
        f"the anchor directory {target.parent} is not writable, and the run's chain heads "
        "and its seal go there",
        code="ANCHOR_DIR_UNWRITABLE",
        hint=(
            "set ASSAY_ANCHOR_DIR to a directory this user can write, or give this user "
            "write permission on it, then start again"
        ),
    )


def check_anchor_file(paths: RunPaths, config: Mapping[str, Any] | None) -> None:
    """The recorded anchor file against the one the environment names
    (section 8.2): a run whose config.json points the anchors elsewhere does
    not start, since one write to config.json while the daemon was stopped
    would otherwise move the seal into a file the agent owns. A run that
    predates the key has nothing recorded and anchors to the environment."""
    recorded = recorded_anchor_file(config)
    if recorded is None:
        return
    expected = environment_anchor_file(paths)
    if recorded.resolve() == expected.resolve():
        return
    raise AssayError(
        f"config.json records the anchor file {recorded}, and the environment names {expected}",
        code="ANCHOR_FILE_MISMATCH",
        hint=(
            f"if {recorded.parent} is your anchor directory, set ASSAY_ANCHOR_DIR to it and "
            "start again; if the recorded path is not yours, treat the run as tampered: "
            "config.json was rewritten"
        ),
    )


def read_anchors(target: Path) -> tuple[list[dict[str, Any]], str | None]:
    """The anchor file's records, and the problem when it cannot be read as
    one JSON object per line (the line named), in which case no record is
    returned: an unreadable anchor file is a finding for the readers and a
    refusal for a start (section 8.3), never a file read around."""
    if not target.exists():
        # Absent, or under a path that is not a directory: no anchors yet.
        return [], None
    try:
        text = target.read_text()
    except OSError as error:
        return [], f"cannot be read: {type(error).__name__}: {error}"
    records: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as error:
            return [], f"line {number} is not JSON ({error})"
        if not isinstance(value, dict):
            return [], f"line {number} is not an object"
        records.append(value)
    return records, None


def anchor_status(paths: RunPaths, config: Mapping[str, Any] | None) -> dict[str, Any]:
    """The anchor line's facts: file, the count of plain anchors, the last
    anchored event, the last failed write (if newer than the last anchor),
    whether the directory can be written now, the sealed event when the
    file carries a seal (section 8.3), and the problem when the file cannot
    be read."""
    target = anchor_file(paths, config)
    anchors, unreadable = read_anchors(target)
    plain = [record for record in anchors if not record.get("seal")]
    seal = seal_of(anchors)
    last_event: int | None = None
    if plain and isinstance(plain[-1].get("event_id"), int):
        last_event = int(plain[-1]["event_id"])
    failed_event: int | None = None
    failed_error: str | None = None
    for record in load_jsonl(paths.activity):
        if record.get("kind") != "anchor_failed":
            continue
        event = record.get("event")
        if last_event is None or (isinstance(event, int) and event > last_event):
            failed_event = event if isinstance(event, int) else None
            failed_error = str(record.get("error"))
    return {
        "file": target,
        "count": len(plain),
        "last_event": last_event,
        "failed_event": failed_event,
        "failed_error": failed_error,
        "writable": anchor_dir_writable(target),
        "sealed": None if seal is None else seal.event_id,
        "unreadable": unreadable,
    }


def anchor_text(
    file: str,
    count: int,
    last_event: int | None,
    failed_event: int | None,
    failed_error: str | None,
    writable: bool,
    sealed: int | None = None,
    unreadable: str | None = None,
) -> str:
    """The ANCHORS line from its facts: the file, the count and the last
    anchored event, the last failed write (its event and error) when newer
    than the last anchor, whether the directory can be written now, the
    sealed event on a sealed run, or the problem of a file that cannot be
    read."""
    line = f"ANCHORS | {file} | "
    if unreadable is not None:
        return line + f"unreadable: {unreadable}; `assay start` refuses until it is fixed"
    line += (
        f"{count} anchor(s), last e{last_event}"
        if count
        else "none yet (every 25 events and on WIN)"
    )
    if sealed is not None:
        line += f" | sealed at e{sealed}"
    if failed_error is not None:
        line += f" | last write FAILED at e{failed_event}: {failed_error}"
    elif not writable:
        line += " | directory NOT WRITABLE, heads stay chain-only until fixed"
    return line


def anchor_line(paths: RunPaths, config: Mapping[str, Any] | None) -> str:
    status = anchor_status(paths, config)
    return anchor_text(
        str(status["file"]),
        status["count"],
        status["last_event"],
        status["failed_event"],
        status["failed_error"],
        status["writable"],
        status["sealed"],
        status["unreadable"],
    )


def tamper_summary(activity: Iterable[Mapping[str, Any]], sealed: bool) -> tuple[int, str]:
    """The standing `tamper_detected` records of the activity log, counted,
    and the state of their seal: `sealed` while the anchor file carries it,
    `unwritten` when the daemon could not write it (an `anchor_failed`
    record carrying `seal`), `lifted` when the operator removed it by hand.
    Informational, beside the verdict (section 8.3)."""
    count = 0
    unwritten = False
    for record in activity:
        kind = record.get("kind")
        if kind == "tamper_detected":
            count += 1
        elif kind == "anchor_failed" and record.get("seal"):
            unwritten = True
    state = "sealed" if sealed else "unwritten" if unwritten else "lifted"
    return count, state


def tamper_text(records: int, state: str) -> str:
    """The TAMPER line from its facts."""
    tail = {
        "sealed": "the anchor file is sealed",
        "unwritten": "the seal could not be written",
        "lifted": "the seal was lifted by hand",
    }[state]
    return f"TAMPER | {records} tamper_detected record(s) in the activity log; {tail}"


@dataclasses.dataclass(frozen=True, slots=True)
class Seal:
    """The sealing record of the anchor file (docs/ARCHITECTURE.md section
    8.3), as the daemon writes it on a tamper: the held count and head, and
    the reason. The line it lands on carries the stamp every anchor line
    carries, which `from_json` leaves alone."""

    event_id: int
    head: str
    seal: str

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> Seal:
        record = "seal"
        return cls(
            event_id=read_int(obj, record, "event_id"),
            head=read_str(obj, record, "head"),
            seal=read_str(obj, record, "seal"),
        )

    def to_json(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "head": self.head, "seal": self.seal}


def seal_of(anchors: Sequence[Mapping[str, Any]]) -> Seal | None:
    """The seal that governs an anchor file (section 8.3): the earliest
    well-formed record carrying `seal`, by event id, or None. A seal says
    the journal ends exactly at its event with its head; the earliest one is
    the first point past which nothing is the daemon's record."""
    seals: list[Seal] = []
    for record in anchors:
        if not isinstance(record, Mapping) or not record.get("seal"):
            continue
        try:
            seals.append(Seal.from_json(record))
        except (TypeError, KeyError):
            continue
    if not seals:
        return None
    return min(seals, key=lambda seal: seal.event_id)


def seal_finding(seal: Seal, heads: Sequence[str]) -> tuple[str, str | None]:
    """What a seal says, and what the journal does against it: a sealed event
    beyond the journal, a prefix that no longer matches the sealed head, or
    lines past the seal; None when the journal ends exactly at the sealed
    event with the sealed head. The audit and the loader share it, so they
    agree."""
    sealed = seal.event_id
    last = len(heads) - 1
    what = (
        f"sealed at e{sealed} ({seal.seal}): the daemon found the run's files changed "
        "under it and the record ends there"
    )
    if sealed > last:
        ends = "is empty" if last < 0 else f"ends at e{last}"
        return what, f"the sealed event e{sealed} is beyond the journal, which {ends}"
    if heads[sealed] != seal.head:
        return what, (
            f"the journal prefix at e{sealed} no longer matches the sealed head; "
            "the journal changed after it was sealed"
        )
    if last > sealed:
        return what, (
            f"the journal continues past the seal to e{last}, lines the daemon that "
            "sealed it never wrote"
        )
    return what, None


def _advance(head: str, line: str) -> str:
    return hashlib.sha256(head.encode() + line.encode()).hexdigest()


def chain_over(lines: Iterable[str]) -> str:
    """The head over these raw journal lines, blank lines skipped; the seed's
    hash over none. The run holds the head after every line it loads
    (`run.heads`), so nothing below the entry points reads the journal to
    recompute it; `verify_disk` is the one reader that does."""
    head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
    for line in lines:
        if line.strip():
            head = _advance(head, line)
    return head


def chain_over_bytes(data: bytes) -> str:
    """The same head over a journal's raw bytes, for `verify_disk`: the lines
    split as the loader splits them (a newline, a carriage return, or both),
    blank lines skipped, each hashed as the bytes it is. The file is never
    decoded, which halves the cost over the largest journals, and a byte that
    is not UTF-8 changes the head like any other edit."""
    head = hashlib.sha256(CHAIN_SEED.encode()).hexdigest()
    for line in data.splitlines():
        if line.strip():
            head = hashlib.sha256(head.encode() + line).hexdigest()
    return head


def redact(text: str | None, extra_names: Sequence[str] = ()) -> str | None:
    """Replace secret env values with [REDACTED:<NAME>] before journaling."""
    if not text:
        return text
    for name in (*DEFAULT_SECRET_ENV, *extra_names):
        value = os.getenv(name)
        if value and len(value) >= _MIN_SECRET_LENGTH and value in text:
            text = text.replace(value, f"[REDACTED:{name}]")
    return text


def redact_mapping(
    value: Mapping[str, Any] | None, extra_names: Sequence[str] = ()
) -> dict[str, Any] | None:
    """Redact every string leaf of an agent-supplied mapping (reasoning etc.)."""
    if value is None:
        return None

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return redact(node, extra_names)
        if isinstance(node, Mapping):
            return {key: _walk(item) for key, item in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    output: dict[str, Any] = _walk(dict(value))
    return output


def ungated_events(events: Sequence[Event]) -> list[int]:
    """Paid non-RESET events with no prediction and no grade: the ungated-event
    rule of verify/JOURNAL_SPEC.md section 6, the one the independent checker
    (verify/assay_verify.py) applies over the same journal fields."""
    return [event.id for event in events if event.ungated]


def first_ungated(events: Sequence[Event]) -> int | None:
    flagged = ungated_events(events)
    return flagged[0] if flagged else None


def ungated_permitted(events: Sequence[Event]) -> dict[int, str]:
    """The ungated events a control arm permitted, keyed by id, with the mode
    whose marker the event carries (`gate_optional` or `gate_off`). The mode
    permits exactly what it marks: an ungated event without a marker was not
    permitted, whatever the registry says. Such events stay ungated and the
    run stays invalid for scoring; they are only counted apart."""
    permitted: dict[int, str] = {}
    for event in events:
        if not event.ungated:
            continue
        if event.gate_optional:
            permitted[event.id] = "optional"
        elif event.gate_off:
            permitted[event.id] = "off"
    return permitted


@dataclasses.dataclass(frozen=True, slots=True)
class AuditReport:
    """What `assay audit` recomputes from the artifacts (docs/ARCHITECTURE.md
    section 7.3): the report `.assay/audit.json` holds, typed; `to_json()`
    is the file's shape."""

    computed_at: float
    events: int
    paid: int
    mutations: int
    contiguous: bool
    chain: str
    anchors: str
    anchor_count: int
    anchor_file: str
    anchor_env_mismatch: bool
    ungated: tuple[int, ...]
    ungated_permitted: tuple[int, ...]
    ungated_permitted_by: tuple[str, ...]
    recovered_orphans: tuple[int, ...]
    recovered_without_prediction: tuple[int, ...]
    mutations_pending: tuple[int, ...]
    invalid_for_scoring: bool
    problems: tuple[str, ...]
    tamper_records: int
    tamper_state: str | None

    def to_json(self) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            output[field.name] = list(value) if isinstance(value, tuple) else value
        return output


def audit(run: Run) -> AuditReport:
    """Recompute integrity from the artifacts; write .assay/audit.json; return it."""
    paths = run.paths
    events = run.events
    problems: list[str] = []
    malformed = run.integrity.malformed
    if malformed is not None:
        problems.append(f"journal: {malformed}")
    contiguous = run.integrity.contiguous
    if not contiguous:
        problems.append(f"contiguity: {run.integrity.problem}")
    # The loader's finding, in the audit's words: a chain behind by a crash's
    # one line is tolerated by a start and repaired by the next append, and
    # reads DIVERGED here, as it always has, until then.
    if run.integrity.chain == "absent":
        chain_state = "absent"
    elif run.integrity.chain == "intact":
        chain_state = "intact"
    else:
        chain_state = "DIVERGED"
        problems.append(run.integrity.chain_problem or "chain: stored head does not match")
    anchor_target = anchor_file(paths, run.config)
    anchors, unreadable = read_anchors(anchor_target)
    anchor_state = "none"
    recorded = recorded_anchor_file(run.config)
    anchor_env_mismatch = (
        recorded is not None and environment_anchor_file(paths) != recorded
    )
    if anchor_env_mismatch and recorded is not None:
        problems.append(
            f"anchor_env_mismatch: ASSAY_ANCHOR_DIR names {environment_anchor_file(paths).parent} "
            f"but this run anchors to {recorded.parent} (recorded at start); the "
            "recorded file was audited"
        )
    # A file that cannot be read is no anchor file (section 8.3): the
    # readers say so, and a start refuses until it is fixed.
    if unreadable is not None:
        anchor_state = "DIVERGED"
        problems.append(
            f"anchor: the anchor file {anchor_target} is unreadable: {unreadable}; "
            "`assay start` refuses until it is fixed"
        )
    # An anchor file inside the run directory is one the agent writes: its
    # heads anchor nothing (section 8.2).
    if recorded is not None and _inside(recorded, paths.root):
        anchor_state = "DIVERGED"
        problems.append(
            f"anchor: the recorded anchor file {recorded} lies inside the run directory, "
            "which the agent writes; its anchors are no evidence"
        )
    plain_anchors = [record for record in anchors if not record.get("seal")]
    if plain_anchors:
        latest = plain_anchors[-1]
        target = int(latest["event_id"])
        if target < len(run.heads):
            anchor_state = (
                "intact" if run.heads[target] == latest["head"] else "DIVERGED"
            )
            if anchor_state == "DIVERGED":
                problems.append(
                    f"anchor: journal prefix at e{target} no longer matches the "
                    "externally anchored head; the journal changed after anchoring"
                )
        else:
            anchor_state = "DIVERGED"
            problems.append("anchor: anchored event id beyond the journal")
    # A sealed anchor is the end of the journal (section 8.3): the daemon
    # found a file changed under it and refused to go on, so the record is
    # invalid for scoring until the operator lifts the seal, whatever
    # chain.json says, and any change after the seal is named.
    seal = seal_of(anchors)
    if seal is not None:
        anchor_state = "DIVERGED"
        what, against = seal_finding(seal, run.heads)
        problems.append(
            f"anchor: {what}; the run is invalid for scoring until the operator "
            f"removes the sealing line from {anchor_target}"
        )
        if against is not None:
            problems.append(f"anchor: {against}")
    # A seal the daemon could not write (an unwritable anchor directory at
    # the moment of the tamper) stands as its `anchor_failed` record: the
    # same end of the record, with nothing in the anchor file to lift.
    activity = load_jsonl(paths.activity)
    for record in activity:
        if record.get("kind") == "anchor_failed" and record.get("seal"):
            anchor_state = "DIVERGED"
            problems.append(
                f"anchor: the seal at e{record.get('event')} could not be written to "
                f"{record.get('file')} ({record.get('error')}); the daemon found the run's "
                "files changed under it and the record ends there"
            )
    tamper_records, tamper_state = tamper_summary(activity, seal is not None)
    ungated = ungated_events(events)
    # The control arms (gate: optional, gate: off) permit bare acts; they are
    # counted apart, with the mode that permitted them, and stay ungated.
    permitted_modes = ungated_permitted(events)
    journaled = {event.mutation_id for event in events if event.mutation_id is not None}
    pending = [
        mutation.mutation_id
        for mutation in run.mutations
        if mutation.mutation_id not in journaled
    ]
    recovered = [
        event.id
        for event in events
        if "recovered from broker mutation journal" in str(event.note or "")
    ]
    # A recovered event carries its prediction and grade from 1.2.0 on
    # (section 6.5) and is gated by its fields like any other. One without
    # them was recovered from a record that predates 1.2.0 or belonged to a
    # model-plan step: UNGATED like any other, and the audit says why. A
    # control arm's bare act recovered with its marker is counted with the
    # permitted ones instead.
    flagged = set(ungated)
    recovered_without_prediction = [
        event_id
        for event_id in recovered
        if event_id in flagged and event_id not in permitted_modes
    ]
    report = AuditReport(
        computed_at=time.time(),
        events=len(events),
        paid=sum(1 for event in events if event.counts_action),
        mutations=len(run.mutations),
        contiguous=contiguous,
        chain=chain_state,
        anchors=anchor_state,
        anchor_count=len(plain_anchors),
        anchor_file=str(anchor_target),
        anchor_env_mismatch=anchor_env_mismatch,
        ungated=tuple(ungated),
        ungated_permitted=tuple(sorted(permitted_modes)),
        ungated_permitted_by=tuple(sorted(set(permitted_modes.values()))),
        recovered_orphans=tuple(recovered),
        recovered_without_prediction=tuple(recovered_without_prediction),
        mutations_pending=tuple(pending),
        invalid_for_scoring=bool(ungated) or not contiguous or malformed is not None
        or chain_state == "DIVERGED" or anchor_state == "DIVERGED",
        problems=tuple(problems),
        tamper_records=tamper_records,
        tamper_state=tamper_state if tamper_records else None,
    )
    atomic_json(paths.state / "audit.json", report.to_json())
    return report


def _inside(path: Path, root: Path) -> bool:
    """Whether the path lies under the run directory."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def audit_lines(report: AuditReport) -> list[str]:
    verdict = "INVALID FOR SCORING" if report.invalid_for_scoring else "CLEAN"
    lines = [
        f"AUDIT | {verdict} | events {report.events} (paid {report.paid}) | "
        f"contiguous {'yes' if report.contiguous else 'NO'} | "
        f"chain {report.chain} | anchors {report.anchors} ({report.anchor_count})",
    ]
    if report.ungated:
        lines.append(
            f"AUDIT | UNGATED events {list(report.ungated[:8])}; the run is invalid "
            "for scoring and trust earned after the first one is demoted"
        )
    if report.ungated_permitted:
        modes = ", ".join(f"`gate: {mode}`" for mode in report.ungated_permitted_by or ("optional",))
        lines.append(
            f"AUDIT | {len(report.ungated_permitted)} of them permitted by "
            f"{modes} (control arm), still invalid for scoring"
        )
    if report.recovered_orphans:
        lines.append(
            f"AUDIT | recovered orphan events {list(report.recovered_orphans[:8])} "
            "(spend journaled by the broker; CLI died before recording)"
        )
    if report.recovered_without_prediction:
        without = report.recovered_without_prediction
        lines.append(
            f"AUDIT | {len(without)} of them recovered without its prediction: the record "
            "predates 1.2.0 or was a model-plan step; counted UNGATED above"
        )
    if report.mutations_pending:
        pending = report.mutations_pending
        lines.append(
            f"AUDIT | {len(pending)} spend(s) in the mutation journal not yet in the "
            f"timeline {list(pending[:8])} (a step in flight, or a crash between spend and "
            "record; `assay start` recovers them once the daemon is gone)"
        )
    for problem in report.problems:
        lines.append(f"AUDIT | problem: {problem}")
    if report.tamper_records and report.tamper_state is not None:
        lines.append("AUDIT | " + tamper_text(report.tamper_records, report.tamper_state))
    return lines
