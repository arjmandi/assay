"""The `Status` record (docs/ARCHITECTURE.md section 7.4) and its renderer.

The rule: every fact a status line prints is a field of the record, so
`render_status` derives the lines and nothing is stored pre-rendered, with
two exceptions, the module advisory lines and the observation kind's own
lines, which are produced by code the kernel does not own. The blocks are
small frozen records, each optional (None when the run has nothing for it:
no registry, no model, no hazards), built once by `status_of` from the held
run and the small files beside the journal, and rendered in the order the
status always printed. `assay status` prints `render_status(status)`; with
`--json` it prints `status.to_json()`. The replay gate over the 25 published
run directories holds the rendering byte for byte against the kernel the
campaigns ran on.

Under a token budget (section 7.6: the registry's `status_budget`, or
`BRIEF_BUDGET` for `assay status --brief`) `fit_status` drops the
lowest-value blocks first, over the record's fields, and the renderer
prints the fitted record as it prints any other, then one TRUNCATED line
naming what was dropped. `estimated_tokens` is the estimate every surface
reports beside what it prints: characters over four, no tokenizer.

The renderers of the lines other commands print too (the budget line, the
anchor line, the states block, the history lines) live beside their facts
in `registry`, `integrity`, `states` and `evidence`, and this module calls
them with the record's fields.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .agenda import agenda_text, emergence_meter, emergence_text, list_proposals, standing_goal
from .aggregates import meter as aggregate_meter
from .carryover import foreign_facts, foreign_text
from .states import StateReadings, state_readings, states_text
from .adapters import SessionCapability, recorded_capability
from .core import REMOTE_MODE, AssayError, load_jsonl, read_json, run_mode
from .evidence import RecentLine, history_text, recent_lines
from .extras import kind_for
from .integrity import (
    anchor_status,
    anchor_text,
    tamper_summary,
    tamper_text,
    ungated_events,
    ungated_permitted,
)
from .meters import level_action_count, recent_predictions, specificity
from .model import batching_rights, fit_path, model_source
from .modules import advisory_lines, ignored_modules, ignored_text, load_hazards
from .records import Event, plain_fields
from .registry import (
    budget_text,
    gate_mode,
    gate_text,
    notes_cap,
    registry_lines,
    spend_reports,
    zero_prior,
)
from .textobs import pretty_cuts, pretty_lines
from .words import progress_label, unit_line_label, unit_noun

if TYPE_CHECKING:
    from .run import Run

OBSERVATION_LINES = 48
NOTES_LINES = 120
NOTES_LINE_WIDTH = 240
# The token budget `assay status --brief` fits (the registry's
# `status_budget` when that is smaller), and the least a budget keeps of
# each block it cuts (docs/ARCHITECTURE.md section 7.6).
BRIEF_BUDGET = 1500
NOTES_LINES_KEPT = 4
OBSERVATION_LINES_KEPT = 8
HISTORY_LINES_KEPT = 4


# --- the blocks ------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RunBlock:
    """The STATUS line: the world id, the last event, the progress as
    completed units, the total and the label the world uses for them
    (`words.progress_label`), the paid actions and the state."""

    world: str
    event: int
    progress_completed: int
    progress_total: int
    progress_label: str
    paid: int
    state: str


@dataclasses.dataclass(frozen=True, slots=True)
class ModeBlock:
    """The MODE line: `local` or `remote`, the action-idle lease the world
    declared in seconds (None without one), the seconds left on it when the
    status was built (0 once it ran out, None without a lease or when the
    run's timestamps do not say), and whether the world replays. The lease
    and the replay come from the session declaration recorded in config.json
    (`adapters.recorded_capability`), not from the mode."""

    mode: str
    idle_lease_seconds: int | None
    lease_seconds: int | None
    replayable: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ObservationBlock:
    """The dict observation as recorded, the number of pretty-printed lines
    the renderer shows at most, how many it leaves out, and whether a status
    budget dropped the tail (the renderer then shows the head alone; it
    keeps both ends around a middle cut otherwise)."""

    observation: Any
    max_lines: int
    omitted_lines: int
    tail_dropped: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class ActionsBlock:
    """The ACTIONS line of a dict world: what the observation advertises, or
    the registered names (None without a registry)."""

    advertised: tuple[str, ...]
    registered: tuple[str, ...] | None


@dataclasses.dataclass(frozen=True, slots=True)
class KindBlock:
    """The observation kind's own status lines (the frame extra prints the
    image path and the advertised-actions line), rendered by the kind."""

    name: str
    lines: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class RegistryAction:
    name: str
    params: dict[str, dict[str, Any]]
    destructive: bool
    approval: bool
    liveness: str | None
    description: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class RegistryBlock:
    """The REGISTRY lines: the actions with their schemas, flags and
    descriptions (withheld under zero_prior), the gate and the mode note;
    the action budget cap is the budget block's."""

    actions: tuple[RegistryAction, ...]
    gate: str
    mode_note: str | None
    zero_prior: bool

    def as_registry(self, budget_cap: int | None) -> dict[str, Any]:
        """The canonical registry shape `registry_lines` renders, with the
        budget block's cap for the header."""
        actions: list[dict[str, Any]] = []
        for action in self.actions:
            item: dict[str, Any] = {"name": action.name, "params": dict(action.params)}
            if action.destructive:
                item["destructive"] = True
            if action.approval:
                item["approval"] = True
            if action.liveness is not None:
                item["liveness"] = action.liveness
            if action.description is not None:
                item["description"] = action.description
            actions.append(item)
        registry: dict[str, Any] = {"actions": actions, "gate": self.gate, "zero_prior": self.zero_prior}
        if budget_cap is not None:
            registry["budget"] = {"actions": budget_cap}
        if self.mode_note is not None:
            registry["mode_note"] = self.mode_note
        return registry


@dataclasses.dataclass(frozen=True, slots=True)
class BudgetBlock:
    cap: int | None
    spent: int
    remaining: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class GateBlock:
    """The registry's gate mode and the count of the paid actions it
    permitted without a prediction; the GATE line is printed under the two
    control arms (`optional`, `off`) and not under `required`."""

    mode: str
    unpredicted: int


@dataclasses.dataclass(frozen=True, slots=True)
class Proposal:
    id: int
    text: str


@dataclasses.dataclass(frozen=True, slots=True)
class AgendaBlock:
    goal_text: str
    goal_source: str
    achieved: bool
    pending: tuple[Proposal, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ForeignBlock:
    """The imported-knowledge facts of a warm-started run."""

    world: Any
    paid: Any
    final_state: Any
    prior_notes: bool
    verifier_candidates: int | None
    imported_model: bool
    active_hazards: int


@dataclasses.dataclass(frozen=True, slots=True)
class ModelBlock:
    """The MODEL line: whether `model.py` was ever replayed and, if so, its
    fit and the batching rights it earned."""

    replayed: bool
    fit: float | None
    graded: int | None
    rights: bool | None
    reason: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class HazardTag:
    action_class: str
    signature: str


@dataclasses.dataclass(frozen=True, slots=True)
class HazardsBlock:
    """Every active hazard tag; the renderer shows four."""

    active: tuple[HazardTag, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class SpendBlock:
    usd: float
    cap: float | None
    tokens: int


@dataclasses.dataclass(frozen=True, slots=True)
class AggregatesBlock:
    open: int
    held: int
    failed: int
    abandoned: int


@dataclasses.dataclass(frozen=True, slots=True)
class IntegrityBlock:
    """The INTEGRITY lines: the ungated events no control arm permitted, what
    the lenient load found about the record, and the standing refusal of a
    live daemon that found a file changed under it."""

    ungated: tuple[int, ...]
    refused: str | None
    refused_code: str | None
    daemon_refusal: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class TamperBlock:
    """The TAMPER line: the standing `tamper_detected` records of the
    activity log and the state of their seal (`sealed`, `unwritten`,
    `lifted`); present only when there is at least one (section 8.3)."""

    records: int
    state: str


@dataclasses.dataclass(frozen=True, slots=True)
class AnchorsBlock:
    """The ANCHORS line: the file, the count of plain anchors, the last
    anchored event, the last failed write (its event and error) when newer
    than the last anchor, whether the directory can be written now, the
    sealed event on a sealed run, and the problem of a file that cannot be
    read."""

    file: str
    count: int
    last_event: int | None
    failed_event: int | None
    failed_error: str | None
    writable: bool
    sealed: int | None
    unreadable: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class EmergenceBlock:
    verifiers: int
    states: int
    model_replays: int
    goal_proposals: int


@dataclasses.dataclass(frozen=True, slots=True)
class UnitBlock:
    """The progress-unit line: the paid actions on the current unit and the
    recent prediction window."""

    paid: int
    hits: int
    total: int


@dataclasses.dataclass(frozen=True, slots=True)
class PredictionsBlock:
    """The PREDICTIONS line's meters."""

    world_model_graded: int
    world_model_missed: int
    gamble_graded: int
    gamble_missed: int
    specific: int
    graded: int
    invalid: int


@dataclasses.dataclass(frozen=True, slots=True)
class VerifierStat:
    digest: str
    graded: int
    failed: int
    vacuous: bool


@dataclasses.dataclass(frozen=True, slots=True)
class VacuousBlock:
    """The verifiers the stats file flags: the vacuous ones under the rule
    the file is under, and the never-failed advisory of the identity rule."""

    rule: str
    verifiers: tuple[VerifierStat, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class NotesBlock:
    """The notes file: its path, text (None when missing), size, the cap the
    registry sets, the renderer's limits (the lines it shows at most, keeping
    both ends, and the width it cuts a line to), the demotion banner's
    facts: the unit whose archive the notes are compared with, and whether
    they changed since it ended, and whether a status budget dropped the
    tail (the renderer then shows the head alone)."""

    path: str
    text: str | None
    size: int | None
    cap: int | None
    max_lines: int
    line_width: int
    archived_unit: int | None
    changed_since_archive: bool | None
    tail_dropped: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class Status:
    """What `assay status` knows, in the order it prints."""

    run: RunBlock
    mode: ModeBlock
    observation: ObservationBlock | None
    actions: ActionsBlock | None
    kind: KindBlock | None
    registry: RegistryBlock | None
    budget: BudgetBlock | None
    gate: GateBlock | None
    agenda: AgendaBlock | None
    ignored_modules: tuple[str, ...]
    foreign: ForeignBlock | None
    states: StateReadings | None
    model: ModelBlock | None
    hazards: HazardsBlock | None
    spend: SpendBlock | None
    aggregates: AggregatesBlock | None
    mis_references: int
    integrity: IntegrityBlock | None
    tamper: TamperBlock | None
    anchors: AnchorsBlock | None
    emergence: EmergenceBlock | None
    unit: UnitBlock
    predictions: PredictionsBlock | None
    vacuous: VacuousBlock | None
    advisories: tuple[str, ...]
    recent: tuple[RecentLine, ...]
    notes: NotesBlock

    def to_json(self) -> dict[str, Any]:
        return plain_fields(self)


# --- the builder ------------------------------------------------------------------


def status_of(run: Run, *, history: int = 8) -> Status:
    """The record, built once from the held run and the small files beside
    the journal."""
    events = run.events
    if not events:
        raise AssayError("timeline is empty", code="TIMELINE_EMPTY")
    event = events[-1]
    kind = kind_for(event)
    registry = run.registry
    paid = sum(1 for item in events if item.counts_action)
    hits, total = recent_predictions(events)
    predictions, vacuous = _predictions_blocks(run)
    anchors = _anchors_block(run) if registry else None
    return Status(
        run=RunBlock(
            world=str(run.config.get("game_id", "unknown")),
            event=event.id,
            progress_completed=int(event.levels_completed),
            progress_total=int(event.win_levels),
            progress_label=progress_label(event.win_levels),
            paid=paid,
            state=str(event.state),
        ),
        mode=_mode_block(run),
        observation=None if kind is not None else _observation_block(event),
        actions=None if kind is not None else _actions_block(event, registry),
        kind=None if kind is None else KindBlock(kind.name, tuple(kind.status_head_lines(run, event))),
        registry=_registry_block(registry) if registry else None,
        budget=_budget_block(registry, paid) if registry else None,
        gate=_gate_block(registry, events) if registry else None,
        agenda=_agenda_block(run) if registry else None,
        ignored_modules=tuple(ignored_modules(run)) if registry else (),
        foreign=_foreign_block(run) if registry else None,
        states=state_readings(run, event) if registry else None,
        model=_model_block(run) if registry else None,
        hazards=_hazards_block(run) if registry else None,
        spend=_spend_block(run, registry) if registry else None,
        aggregates=_aggregates_block(run) if registry else None,
        mis_references=_mis_references(run) if registry else 0,
        integrity=_integrity_block(run) if registry else None,
        tamper=_tamper_block(run, anchors) if registry else None,
        anchors=anchors,
        emergence=EmergenceBlock(**emergence_meter(run)) if registry else None,
        unit=UnitBlock(paid=level_action_count(events), hits=hits, total=total),
        predictions=predictions,
        vacuous=vacuous,
        advisories=tuple(advisory_lines(run)) if registry else (),
        recent=tuple(recent_lines(events, history)),
        notes=_notes_block(run, event),
    )


def _mode_block(run: Run) -> ModeBlock:
    capability = recorded_capability(run.config)
    return ModeBlock(
        mode=run_mode(run.config),
        idle_lease_seconds=capability.idle_lease_seconds,
        lease_seconds=lease_left(capability, idle_seconds(run)),
        replayable=capability.replayable,
    )


def idle_seconds(run: Run) -> float | None:
    """Seconds since the last paid action, or since the run's creation
    before the first; None when the timestamps do not say."""
    import datetime as dt

    mutations = run.mutations
    last = mutations[-1].timestamp if mutations else run.config.get("created_at")
    if not last:
        return None
    try:
        then = dt.datetime.fromisoformat(str(last))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (dt.datetime.now(dt.timezone.utc) - then).total_seconds())


def lease_left(capability: SessionCapability, idle: float | None) -> int | None:
    """The seconds left on the declared action-idle lease: None without a
    lease or when the idle time is unknown, 0 once it ran out."""
    lease = capability.idle_lease_seconds
    if lease is None or idle is None:
        return None
    return 0 if idle >= lease else math.ceil(lease - idle)


def lease_text(idle_lease_seconds: int | None) -> str:
    """The declared lease as the start line says it: `~15m action-idle
    lease`, or `no action-idle lease`."""
    if idle_lease_seconds is None:
        return "no action-idle lease"
    if idle_lease_seconds % 60 == 0:
        return f"~{idle_lease_seconds // 60}m action-idle lease"
    return f"~{idle_lease_seconds}s action-idle lease"


def remaining_text(idle_lease_seconds: int | None, lease_seconds: int | None) -> str:
    """The lease field of the MODE and RESUMED lines: no lease, the time left
    on it, that it ran out, or that the timestamps do not say."""
    if idle_lease_seconds is None:
        return "no action-idle lease"
    if lease_seconds is None:
        return "unknown"
    if lease_seconds <= 0:
        return "expired/unavailable"
    if lease_seconds >= 60:
        return f"about {math.ceil(lease_seconds / 60)}m action-idle remaining"
    return f"about {lease_seconds}s action-idle remaining"


def _observation_block(event: Event) -> ObservationBlock:
    _, omitted, _ = pretty_cuts(event.observation, OBSERVATION_LINES)
    return ObservationBlock(
        observation=event.observation,
        max_lines=OBSERVATION_LINES,
        omitted_lines=omitted,
    )


def _actions_block(event: Event, registry: Mapping[str, Any] | None) -> ActionsBlock:
    return ActionsBlock(
        advertised=tuple(str(value) for value in event.available_actions),
        registered=tuple(item["name"] for item in registry.get("actions", ())) if registry else None,
    )


def _registry_block(registry: Mapping[str, Any]) -> RegistryBlock:
    hidden = zero_prior(registry)
    actions = tuple(
        RegistryAction(
            name=str(item["name"]),
            params={pname: dict(schema) for pname, schema in item.get("params", {}).items()},
            destructive=bool(item.get("destructive")),
            approval=bool(item.get("approval")),
            liveness=item.get("liveness"),
            description=None if hidden or not item.get("description") else str(item["description"]),
        )
        for item in registry.get("actions", ())
    )
    note = registry.get("mode_note")
    return RegistryBlock(
        actions=actions,
        gate=gate_mode(registry),
        mode_note=str(note) if note else None,
        zero_prior=hidden,
    )


def _budget_block(registry: Mapping[str, Any], spent: int) -> BudgetBlock:
    cap = (registry.get("budget") or {}).get("actions")
    remaining = None if cap is None else max(0, int(cap) - spent)
    return BudgetBlock(cap=cap, spent=spent, remaining=remaining)


def _gate_block(registry: Mapping[str, Any], events: Sequence[Event]) -> GateBlock:
    return GateBlock(mode=gate_mode(registry), unpredicted=len(ungated_permitted(events)))


def _agenda_block(run: Run) -> AgendaBlock:
    goal = standing_goal(run)
    events = run.events
    return AgendaBlock(
        goal_text=str(goal["text"]),
        goal_source=str(goal["source"]),
        achieved=bool(events) and str(events[-1].state) == "WIN",
        pending=tuple(
            Proposal(id=int(item["id"]), text=str(item["text"]))
            for item in list_proposals(run)
            if item["status"] == "pending"
        ),
    )


def _foreign_block(run: Run) -> ForeignBlock | None:
    facts = foreign_facts(run)
    return None if facts is None else ForeignBlock(**facts)


def _model_block(run: Run) -> ModelBlock | None:
    paths = run.paths
    if not model_source(paths).exists():
        return None
    fit = read_json(fit_path(paths), None)
    if not isinstance(fit, dict):
        return ModelBlock(replayed=False, fit=None, graded=None, rights=None, reason=None)
    rights, reason = batching_rights(run)
    return ModelBlock(
        replayed=True,
        fit=float(fit.get("fit", 0)),
        graded=int(fit.get("graded", 0)),
        rights=rights,
        reason=None if rights else reason,
    )


def _hazards_block(run: Run) -> HazardsBlock | None:
    active = [tag for tag in load_hazards(run.paths) if tag.get("active", True)]
    if not active:
        return None
    return HazardsBlock(
        active=tuple(HazardTag(str(tag["action_class"]), str(tag["signature"])) for tag in active)
    )


def _spend_block(run: Run, registry: Mapping[str, Any]) -> SpendBlock | None:
    usd, tokens = spend_reports(load_jsonl(run.paths.activity))
    cap = (registry.get("budget") or {}).get("usd")
    if not (usd or cap):
        return None
    return SpendBlock(usd=usd, cap=None if cap is None else float(cap), tokens=tokens)


def _aggregates_block(run: Run) -> AggregatesBlock | None:
    counts = aggregate_meter(run)
    if not any(counts.values()):
        return None
    return AggregatesBlock(**counts)


def _mis_references(run: Run) -> int:
    return sum(
        1
        for record in load_jsonl(run.paths.activity)
        if record.get("kind") == "mis_reference"
    )


def _integrity_block(run: Run) -> IntegrityBlock:
    events = run.events
    # Ungated events a control arm permitted are counted on the GATE line;
    # the INTEGRITY line is for the ones no mode permitted.
    permitted = ungated_permitted(events)
    flagged = tuple(event_id for event_id in ungated_events(events) if event_id not in permitted)
    return IntegrityBlock(
        ungated=flagged,
        refused=run.integrity.refused,
        refused_code=run.integrity.refused_code,
        daemon_refusal=_daemon_refusal(run),
    )


def _daemon_refusal(run: Run) -> str | None:
    """The standing refusal of a live daemon that found a file changed under
    it (docs/ARCHITECTURE.md section 8.3): a registry, configuration, chain,
    owner, mutation-log or manifest edit leaves no trace in the journal the
    readers load, so the fact comes from the daemon's held state. None
    without a daemon, or while it is inside a step."""
    from .broker import broker_state

    try:
        state = broker_state(run.paths)
    except (AssayError, KeyError):
        return None
    return state.tampered


def _anchors_block(run: Run) -> AnchorsBlock:
    status = anchor_status(run.paths, run.config)
    return AnchorsBlock(
        file=str(status["file"]),
        count=int(status["count"]),
        last_event=status["last_event"],
        failed_event=status["failed_event"],
        failed_error=status["failed_error"],
        writable=bool(status["writable"]),
        sealed=status["sealed"],
        unreadable=status["unreadable"],
    )


def _tamper_block(run: Run, anchors: AnchorsBlock | None) -> TamperBlock | None:
    """The standing tamper_detected records, with the state of their seal;
    None when the activity log carries none."""
    sealed = anchors is not None and anchors.sealed is not None
    records, state = tamper_summary(load_jsonl(run.paths.activity), sealed)
    return TamperBlock(records=records, state=state) if records else None


def _predictions_blocks(run: Run) -> tuple[PredictionsBlock | None, VacuousBlock | None]:
    """The prediction meters: split miss rates, specificity, the invalid count, and
    the verifiers flagged VACUOUS under the rule the run's stats file is
    under, with the never-failed advisory. None until something is
    graded."""
    from .verifiers import (
        load_stats,
        never_failed_hashes,
        stats_entries,
        stats_rule,
        vacuous_hashes,
    )

    stats = load_stats(run.paths)
    rule = stats_rule(stats)
    entries = stats_entries(stats)
    vacuous = vacuous_hashes(stats)
    meter = specificity(run.events)
    invalid = 0
    counts: dict[str, list[int]] = {
        "world_model": [0, 0],
        "gamble": [0, 0],
    }  # bucket -> [graded, missed]
    for event in run.events:
        for item in event.grade:
            kind = item.kind
            if kind == "note":
                continue
            if item.invalid or item.ungradable:
                invalid += 1
                continue
            if kind == "coerced":
                continue  # excluded from the capability meter
            if item.machine:
                continue  # kernel-generated predictions never meter the agent
            if item.verifier and item.ok and (
                item.excluded_from_meter or item.verifier_hash in vacuous
            ):
                continue  # vacuous verifier passes earn nothing
            bucket = str(
                item.bucket
                or ("gamble" if kind in {"win", "level_up"} else "world_model")
            )
            slot = counts.setdefault(bucket, [0, 0])
            slot[0] += 1
            if not item.ok:
                slot[1] += 1
    if not meter.graded:
        return None, None
    block = PredictionsBlock(
        world_model_graded=counts["world_model"][0],
        world_model_missed=counts["world_model"][1],
        gamble_graded=counts["gamble"][0],
        gamble_missed=counts["gamble"][1],
        specific=meter.specific,
        graded=meter.graded,
        invalid=invalid,
    )
    verifiers = [
        VerifierStat(
            digest=digest,
            graded=int(entries.get(digest, {}).get("graded", 0)),
            failed=int(entries.get(digest, {}).get("failed", 0)),
            vacuous=True,
        )
        for digest in sorted(vacuous)
    ]
    verifiers.extend(
        VerifierStat(
            digest=digest,
            graded=int(entries.get(digest, {}).get("graded", 0)),
            failed=int(entries.get(digest, {}).get("failed", 0)),
            vacuous=False,
        )
        for digest in sorted(never_failed_hashes(stats))
    )
    return block, VacuousBlock(rule=rule, verifiers=tuple(verifiers))


def _notes_block(run: Run, event: Event) -> NotesBlock:
    paths = run.paths
    try:
        text: str | None = paths.notes.read_text()
    except FileNotFoundError:
        text = None
    archived_unit: int | None = None
    changed: bool | None = None
    completed = int(event.levels_completed)
    if completed > 0 and str(event.state) != "WIN":
        archive = paths.state / "levels" / f"level-{completed}.md"
        if archive.exists() and text is not None:
            archived_unit = completed
            changed = paths.notes.stat().st_mtime > archive.stat().st_mtime
    return NotesBlock(
        path=str(paths.notes),
        text=text,
        size=None if text is None else len(text),
        cap=notes_cap(run.registry) if run.registry else None,
        max_lines=NOTES_LINES,
        line_width=NOTES_LINE_WIDTH,
        archived_unit=archived_unit,
        changed_since_archive=changed,
    )


# --- the budget -------------------------------------------------------------------


def estimated_tokens(text: str) -> int:
    """The estimate a surface reports beside what it prints
    (docs/ARCHITECTURE.md section 7.6): characters over four, no
    tokenizer."""
    return len(text) // 4


def truncated_text(dropped: Sequence[str], budget: int) -> str:
    """The one line a budget appends, naming what it dropped in the order
    dropped (section 7.6)."""
    return (
        f"TRUNCATED | {', '.join(dropped)} dropped to fit {budget} tokens; "
        "assay status --json carries them all"
    )


def fit_status(status: Status, budget: int) -> tuple[Status, tuple[str, ...]]:
    """The record under the budget (section 7.6): the lowest-value blocks
    dropped first, in this order and each as far as it goes, until the
    rendering fits with its TRUNCATED line: the notes tail (the head that
    fits stays, NOTES_LINES_KEPT lines at least), the observation tail (the
    head, OBSERVATION_LINES_KEPT lines at least), the registry descriptions
    (the action lines stay), the history beyond HISTORY_LINES_KEPT lines.
    The drops are over the record's fields, so the renderer prints the
    fitted record as it prints any other; a budget no drop can meet leaves
    all four dropped and the rest printing. Returns the fitted record and
    the names of what was dropped, in order (the record itself and nothing
    when it fits)."""
    if _fits(status, (), budget):
        return status, ()
    fitted = status
    dropped: list[str] = []
    for name, drop in _DROPS:
        reduced = drop(fitted, [*dropped, name], budget)
        if reduced is None:
            continue
        fitted, dropped = reduced, [*dropped, name]
        if _fits(fitted, dropped, budget):
            break
    return fitted, tuple(dropped)


def _fits(status: Status, dropped: Sequence[str], budget: int) -> bool:
    return estimated_tokens(_rendered(status, dropped, budget)) <= budget


def _largest_fit(
    keep: Callable[[int], Status], least: int, most: int, dropped: Sequence[str], budget: int
) -> Status:
    """The record keeping the most lines in least..most that fits the
    budget, or `least` lines when none does. Fewer lines render shorter,
    so the search is binary."""
    best: Status | None = None
    low, high = least, most
    while low <= high:
        middle = (low + high) // 2
        candidate = keep(middle)
        if _fits(candidate, dropped, budget):
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return keep(least) if best is None else best


def _without_notes_tail(status: Status, dropped: Sequence[str], budget: int) -> Status | None:
    notes = status.notes
    if notes.text is None:
        return None
    shown = min(len(notes.text.splitlines()), notes.max_lines)
    if shown <= NOTES_LINES_KEPT:
        return None

    def keep(count: int) -> Status:
        return dataclasses.replace(
            status, notes=dataclasses.replace(notes, max_lines=count, tail_dropped=True)
        )

    return _largest_fit(keep, NOTES_LINES_KEPT, shown - 1, dropped, budget)


def _without_observation_tail(
    status: Status, dropped: Sequence[str], budget: int
) -> Status | None:
    block = status.observation
    if block is None:
        return None
    total, _, _ = pretty_cuts(block.observation, block.max_lines)
    shown = min(total, block.max_lines)
    if shown <= OBSERVATION_LINES_KEPT:
        return None

    def keep(count: int) -> Status:
        return dataclasses.replace(
            status,
            observation=dataclasses.replace(
                block, max_lines=count, omitted_lines=total - count, tail_dropped=True
            ),
        )

    return _largest_fit(keep, OBSERVATION_LINES_KEPT, shown - 1, dropped, budget)


def _without_descriptions(status: Status, dropped: Sequence[str], budget: int) -> Status | None:
    registry = status.registry
    if registry is None or not any(action.description for action in registry.actions):
        return None
    actions = tuple(dataclasses.replace(action, description=None) for action in registry.actions)
    return dataclasses.replace(status, registry=dataclasses.replace(registry, actions=actions))


def _without_history(status: Status, dropped: Sequence[str], budget: int) -> Status | None:
    if len(status.recent) <= HISTORY_LINES_KEPT:
        return None
    return dataclasses.replace(status, recent=status.recent[-HISTORY_LINES_KEPT:])


# The drops in the order a budget takes them, each named as the TRUNCATED
# line names it.
_DROPS: tuple[tuple[str, Callable[[Status, Sequence[str], int], Status | None]], ...] = (
    ("notes tail", _without_notes_tail),
    ("observation tail", _without_observation_tail),
    ("registry descriptions", _without_descriptions),
    ("history beyond four lines", _without_history),
)


# --- the renderer -----------------------------------------------------------------


def render_status(status: Status, *, budget: int | None = None) -> str:
    """Today's status lines, from the record's fields alone; under a token
    budget (section 7.6) the lines of the fitted record, then the TRUNCATED
    line naming what it dropped."""
    return status_within(status, budget)[0]


def status_within(status: Status, budget: int | None) -> tuple[str, tuple[str, ...]]:
    """The status rendered under the budget (today's lines when there is
    none), and the blocks the budget dropped, in the order dropped."""
    if budget is None:
        return "\n".join(_status_lines(status)), ()
    fitted, dropped = fit_status(status, budget)
    return _rendered(fitted, dropped, budget), dropped


def _rendered(status: Status, dropped: Sequence[str], budget: int) -> str:
    lines = _status_lines(status)
    if dropped:
        lines.append(truncated_text(dropped, budget))
    return "\n".join(lines)


def _status_lines(status: Status) -> list[str]:
    run = status.run
    total = run.progress_total
    lines = [
        f"STATUS | {run.world} | event {run.event} | {run.progress_label} "
        f"{min(total, run.progress_completed + 1)}/{total} | paid actions {run.paid} | {run.state}",
        mode_text(status.mode),
    ]
    if status.kind is not None:
        lines.extend(status.kind.lines)
    else:
        if status.observation is not None:
            block = status.observation
            lines.extend(
                observation_text(block.observation, block.max_lines, head_only=block.tail_dropped)
            )
        if status.actions is not None:
            lines.append(actions_text(status.actions.advertised, status.actions.registered))
    if status.registry is not None:
        cap = status.budget.cap if status.budget is not None else None
        lines.extend(registry_lines(status.registry.as_registry(cap)))
    if status.budget is not None:
        lines.append(budget_text(status.budget.spent, status.budget.cap))
    if status.gate is not None and status.gate.mode != "required":
        lines.append(gate_text(status.gate.mode, status.gate.unpredicted))
    if status.agenda is not None:
        agenda = status.agenda
        lines.extend(
            agenda_text(
                agenda.goal_text,
                agenda.goal_source,
                agenda.achieved,
                [(item.id, item.text) for item in agenda.pending],
            )
        )
    lines.extend(ignored_text(status.ignored_modules))
    if status.foreign is not None:
        lines.extend(foreign_text(**dataclasses.asdict(status.foreign)))
    if status.states is not None:
        lines.extend(states_text(status.states))
    if status.model is not None:
        lines.append(model_text(status.model))
    if status.hazards is not None:
        lines.append(hazards_text(status.hazards))
    if status.spend is not None:
        lines.append(spend_text(status.spend))
    if status.aggregates is not None:
        counts = status.aggregates
        lines.append(
            f"AGGREGATES | open {counts.open} | held {counts.held} | "
            f"failed {counts.failed} | abandoned {counts.abandoned}"
        )
    if status.mis_references:
        lines.append(
            f"MIS-REFERENCE | {status.mis_references} prediction(s) named unregistered states "
            "(refused free; the grounding meter)"
        )
    if status.integrity is not None:
        lines.extend(integrity_text(status.integrity))
    if status.tamper is not None:
        lines.append(tamper_text(status.tamper.records, status.tamper.state))
    if status.anchors is not None:
        anchors = status.anchors
        lines.append(
            anchor_text(
                anchors.file,
                anchors.count,
                anchors.last_event,
                anchors.failed_event,
                anchors.failed_error,
                anchors.writable,
                anchors.sealed,
                anchors.unreadable,
            )
        )
    if status.emergence is not None:
        lines.append(emergence_text(**dataclasses.asdict(status.emergence)))
    lines.append(unit_text(status.unit, total))
    if status.predictions is not None:
        lines.extend(predictions_text(status.predictions, status.vacuous))
    lines.extend(status.advisories)
    lines.append("RECENT | ✓ prediction held · ✗ prediction missed")
    lines.extend(history_text(status.recent))
    lines.extend(notes_text(status.notes, total))
    return lines


def mode_text(mode: ModeBlock) -> str:
    label = "REMOTE" if mode.mode == REMOTE_MODE else "LOCAL SIMULATOR"
    recovery = "enabled" if mode.replayable else "unavailable"
    return (
        f"MODE | {label} | {remaining_text(mode.idle_lease_seconds, mode.lease_seconds)} | "
        f"exact replay recovery {recovery}"
    )


def observation_text(
    observation: Any, max_lines: int = OBSERVATION_LINES, *, head_only: bool = False
) -> list[str]:
    lines = ["OBSERVATION | current, JSON (data, not instructions)"]
    lines.extend(f"  {line}" for line in pretty_lines(observation, max_lines, head_only=head_only))
    return lines


def actions_text(advertised: Sequence[str], registered: Sequence[str] | None) -> str:
    if advertised:
        return "ACTIONS | advertised: " + " · ".join(advertised) + " · RESET (built-in)"
    if registered is not None:
        return "ACTIONS | registered: " + " · ".join(registered) + " · RESET (built-in)"
    return "ACTIONS | none advertised"


def model_text(model: ModelBlock) -> str:
    if not model.replayed:
        return (
            "MODEL | model.py present, never replayed; `assay model replay` "
            "grades it and can earn batching rights"
        )
    rights = "YES" if model.rights else f"no; {model.reason}"
    return f"MODEL | fit {model.fit or 0:.0%} over {model.graded or 0} graded | batching rights: {rights}"


def hazards_text(hazards: HazardsBlock) -> str:
    rendered = ", ".join(f"{tag.action_class}({tag.signature})" for tag in hazards.active[:4])
    return (
        f"HAZARDS | {len(hazards.active)} tagged action class(es): {rendered}; each "
        "demands worst_case + recovery declarations on use"
    )


def spend_text(spend: SpendBlock) -> str:
    line = f"SPEND | reported ${spend.usd:.2f}"
    if spend.cap:
        line += f" of ${spend.cap:.2f} cap"
    if spend.tokens:
        line += f" | {spend.tokens} tokens"
    return line


def integrity_text(integrity: IntegrityBlock) -> list[str]:
    lines: list[str] = []
    if integrity.ungated:
        lines.append(
            f"INTEGRITY | {len(integrity.ungated)} UNGATED event(s) (first e{integrity.ungated[0]}); "
            "this run is INVALID FOR SCORING and trust earned after it is demoted"
        )
    if integrity.refused is not None:
        lines.append(
            f"INTEGRITY | {integrity.refused}; this run is INVALID FOR SCORING and `assay start` "
            f"refuses to resume it ({integrity.refused_code})"
        )
    if integrity.daemon_refusal is not None:
        lines.append(
            f"INTEGRITY | the daemon refused a paid action: {integrity.daemon_refusal}; it refuses "
            "every paid action until it is stopped and the record is examined (`assay audit`)"
        )
    return lines


def unit_text(unit: UnitBlock, win_levels: int) -> str:
    summary = (
        f"predictions {unit.hits}/{unit.total} ✓ over the last {unit.total}"
        if unit.total
        else "no graded predictions yet"
    )
    return (
        f"{unit_line_label(win_levels)} | {unit.paid} paid actions this "
        f"{unit_noun(win_levels)} | {summary}"
    )


def predictions_text(block: PredictionsBlock, vacuous: VacuousBlock | None) -> list[str]:
    from .verifiers import RULE_IDENTITY

    def rate(graded: int, missed: int) -> str:
        if not graded:
            return "0/0"
        return f"{missed}/{graded} ({100 * missed / graded:.1f}%)"

    lines = [
        f"PREDICTIONS | world-model misses {rate(block.world_model_graded, block.world_model_missed)} | "
        f"gamble misses {rate(block.gamble_graded, block.gamble_missed)} | "
        f"specificity {block.specific}/{block.graded} ({100 * block.specific / block.graded:.0f}%) | "
        f"invalid {block.invalid}"
    ]
    if vacuous is None:
        return lines
    for entry in vacuous.verifiers:
        if not entry.vacuous:
            lines.append(
                f"VERIFIER | {entry.digest[:12]} graded {entry.graded}, "
                "never failed (advisory, not a flag)"
            )
        elif vacuous.rule == RULE_IDENTITY:
            lines.append(
                f"VACUOUS | verifier {entry.digest[:12]} graded {entry.graded}, "
                "identity verdict matched the real verdict every time; it does not "
                "use the transition, its passes are excluded from the meter"
            )
        else:
            # The never-failed rule's line, byte for byte, for the runs
            # recorded under it: the replay gate compares the published runs
            # against it.
            lines.append(
                f"VACUOUS | verifier {entry.digest[:12]} graded {entry.graded} "
                "failed 0: a verifier that never fails proves nothing; its passes "
                "are excluded from the meter"
            )
    return lines


def _bounded(
    items: list[str], limit: int, *, preserve_ends: bool = False, head: bool = False
) -> list[str]:
    if len(items) <= limit:
        return items
    omitted = len(items) - limit
    if head:
        return items[:limit] + [f"  … {omitted} more"]
    if preserve_ends and limit >= 2:
        left = limit // 2
        right = limit - left
        return items[:left] + [f"  … {omitted} more"] + items[-right:]
    return [f"  … {omitted} earlier"] + items[-limit:]


def notes_text(notes: NotesBlock, win_levels: int) -> list[str]:
    lines: list[str] = []
    if notes.archived_unit is not None and notes.changed_since_archive is False:
        lines.append(
            f"NOTES | unchanged since {unit_noun(win_levels)} {notes.archived_unit} ended: "
            f"earlier Verified lines are only Assumed on this {unit_noun(win_levels)} "
            "until re-tested"
        )
    if notes.text is None:
        lines.append("NOTES | missing; create .assay/NOTES.md and keep it current")
        return lines
    content = [f"  {line[:notes.line_width]}" for line in notes.text.splitlines()]
    shown, total = notes.max_lines, len(content)
    if total <= shown:
        extent = "shown in full"
    elif notes.tail_dropped:
        extent = f"the first {shown} of {total} lines"
    else:
        extent = f"{shown} of {total} lines"
    lines.append(f"NOTES | {notes.path} (edit the file directly; {extent})")
    lines.extend(_bounded(content, shown, preserve_ends=True, head=notes.tail_dropped))
    cap = notes.cap
    size = notes.size or 0
    if cap is not None and size > 2 * cap:
        lines.append(
            f"NOTES | {size} chars, OVER TWICE the {cap}-char cap; paid actions "
            "refuse until trimmed (one page is the contract)"
        )
    elif cap is not None and size > cap:
        lines.append(
            f"NOTES | {size} chars exceeds the {cap}-char cap; trim toward one "
            "page; the block engages at 2× the cap"
        )
    return lines
