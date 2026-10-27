"""Behavior modules: declare -> enforce -> grade units.

A module is constitution text + a trigger + a demand schema + a mode + telemetry,
the `Module` protocol below:

    NAME: str                # unique, lowercase
    CONSTITUTION: str            # one paragraph of way-of-thinking text
    MODE: "advise"|"block"   # default; registry module_modes overrides per run
    trigger(view, pending) -> str | None      # advisory message when it fires
    demand(view, pending) -> dict[str,str] | None   # {field: why}, structural
    observe(view, event) -> None              # optional: learn from results
    telemetry(view) -> dict                    # free counters

`view` is a `ModuleView` over the run: the events (read-only, as `Event`
records), the registry, the run paths for a module's own files, and the two
kernel methods a module records through, `record(kind, **fields)` and
`hazards()`. A module never writes a kernel file by path.

The shipping ladder is telemetry first, teeth later: every built-in ships with
MODE "advise", and a run that wants teeth sets `module_modes` in its registry,
because no A/B has yet shown that blocking pays. Demands are always for
checkable structure (named, non-empty fields supplied via --declare or the
destructive-gate flags), never for confidence. Module code is pack-tier trust:
installed by the human at registration (`modules: [path.py]`) or by the owner
mid-run (`assay module install PATH --token TOK`, the daemon operation
`install_module`), never writable by the agent. The modules load once per
process from the held manifest: in the daemon at `serve`, held for its life
and reloaded when the owner installs one; in the CLI lazily, for `assay
status` and `assay module list`; `assay start` loads the registered files
once to pin them. Installed files are pinned into `.assay/modules/` and listed in
`.assay/modules/manifest.json` with their sha256; a file in that directory
that is not listed, or whose hash no longer matches, is never loaded and
status says so. This is the sanctioned hot-add channel: the owner installs,
the install is journaled, nothing else in the directory counts.

The manifest, not a directory glob, decides what loads: `_load_external` reads
the manifest's listed files whose hash still matches and nothing else, so the
run loads only what the registry or the owner installed, and a file the agent
writes into the directory is not a module. A run started before the manifest
existed has it reconstructed from the registry's `modules` list once, at
`assay start`, never from a status call.

Built-ins (the standing nudge table plus the first structural module):

- wall_spend:     spend escalation on one progress unit (the spend-judgment
                  tier), at thresholds of paid actions on the unit
- miss_streak:    repeated world-model misses mean the notes story is wrong
                  (gamble misses counted apart)
- gamble_streak:  three missed milestone gambles on one unit: the next gamble
                  names its win-rule hypothesis (--declare win_rule=...)
- null_forensics: a predicted-change/observed-nothing verdict flags the raw
                  observation for inspection before the hypothesis is closed
- park_with_test: a reset should leave a re-entry test in the notes
- specificity:    a majority of coerced free-text outcomes earns nothing
- hazard:         effect-signature hazard tags: entered-loss-state
                  and milestone-drop transitions tag the action class; a tagged
                  class gets the worst-case + recovery declaration demand on
                  its next use. Tags are permanent for the run and export as
                  the distinguished carryover class.
- conclusion_gate: the coverage audit's demand keyed on the record rather
                  than on a declaration word: a reset that did not follow
                  GAME_OVER, a verdict the notes gained since the last audit
                  (a `STOP:` line by the Stop gate's rule, or a Plan or
                  heading verdict), credited only while owed; and at status
                  the newest verdict, by its text, with no paid action since
                  it appeared (the resume check).
- game_over_retry: a non-reset action after one was already refused inside
                  GAME_OVER owes a reason; only a reset continues the unit.
- coverage_audit: the coverage-audit protocol as standing machinery: which
                  available actions were never tried or never productive on
                  this progress unit, a stall, a halt on re-issuing the move
                  that just graded FALSE, a halt on a short loop of the same
                  failing move, and a conclusion gate that demands a coverage
                  audit when a declaration tags an impossibility or absence.
                  World-neutral: it reads the journal's own change signal. An
                  observation kind may add its own coverage (the frame world
                  adds the grid regions a point action never probed).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shutil
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from .core import (
    AssayError,
    RunPaths,
    append_jsonl,
    atomic_json,
    import_path,
    read_json,
)
from .extras import kind_for
from .meters import level_action_count, recent_predictions, specificity, unit_indices
from .records import Event, outcome_bucket
from .registry import MODULE_MODES, module_modes

if TYPE_CHECKING:
    from .run import Run


class Module(Protocol):
    """The module contract. `observe(view, event)` is optional and is called
    when the module defines it."""

    NAME: str
    CONSTITUTION: str
    MODE: str

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None: ...
    def demand(self, view: ModuleView, pending: Mapping[str, Any] | None) -> dict[str, str] | None: ...
    def telemetry(self, view: ModuleView) -> dict[str, Any]: ...


class ModuleView:
    """What a module may read: the journal (read-only), the registry and the
    run paths for its own files; and how it records: `record` persists a
    module-owned record through the kernel, `hazards` reads the hazard tags."""

    __slots__ = ("events", "registry", "paths")

    def __init__(self, run: Run) -> None:
        self.events: tuple[Event, ...] = tuple(run.events)
        self.registry: Mapping[str, Any] | None = (
            MappingProxyType(run.registry) if run.registry is not None else None
        )
        self.paths: RunPaths = run.paths

    def record(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Persist one module-owned record: an activity record of this kind,
        and for `hazard_tagged` the tag into `hazards.json` as well."""
        if kind == "hazard_tagged":
            tags = load_hazards(self.paths)
            tags.append(dict(fields))
            atomic_json(hazards_path(self.paths), tags)
        return append_jsonl(self.paths.activity, {"kind": kind, **fields})

    def hazards(self) -> list[dict[str, Any]]:
        return load_hazards(self.paths)


def hazards_path(paths: RunPaths) -> Path:
    return paths.state / "hazards.json"


def load_hazards(paths: RunPaths) -> list[dict[str, Any]]:
    value = read_json(hazards_path(paths), [])
    return value if isinstance(value, list) else []


def _unit(view: ModuleView) -> str:
    """What this world calls a progress unit in prose."""
    from .words import unit_noun

    return unit_noun(view.events[-1].win_levels) if view.events else "unit"


# The paid actions on one progress unit at which wall_spend speaks: at a
# threshold, not on every command past the first (it fired 1538 times in the
# 2026-10-25 Haiku arm and its advice was taken once).
WALL_SPEND_THRESHOLDS = (25, 50, 100, 200, 400, 800)


class _WallSpend:
    NAME = "wall_spend"
    CONSTITUTION = (
        "Spending long on one progress unit without progress is a cue to check the "
        "model offline against the journal before the next batch of probes."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        # A batch consults once per step against the same journal: the
        # steps before this one count, or a batch that crosses a threshold
        # passes it silently (202 of 448 crossings on 2026-10-26).
        step = (pending or {}).get("step")
        level_actions = level_action_count(view.events) + (step if isinstance(step, int) else 0)
        if level_actions in WALL_SPEND_THRESHOLDS:
            return (
                f"{level_actions} paid actions on this {_unit(view)}; check the model "
                "offline against the journal before the next batch of probes "
                "(`assay python`, or the `assay model` tier: replay-verified models "
                "earn batching rights)"
            )
        return None

    def demand(self, view: ModuleView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        return {"level_actions": level_action_count(view.events)}


def bucket_misses(events: Sequence[Event], window: int = 10) -> dict[str, tuple[int, int]]:
    """Per bucket, (missed, graded) over the last `window` graded events that
    carry an outcome of that bucket: a world-model miss and a gamble miss
    are counted apart, so a run of missed level+1 gambles does not read as a
    wrong mechanics story, nor the reverse."""
    counts: dict[str, tuple[int, int]] = {}
    for bucket in ("world_model", "gamble"):
        graded = [
            event for event in events
            if event.predict_ok is not None and any(item.bucket == bucket for item in event.grade)
        ][-window:]
        missed = sum(
            1 for event in graded if any(item.bucket == bucket and not item.ok for item in event.grade)
        )
        counts[bucket] = (missed, len(graded))
    return counts


class _MissStreak:
    NAME = "miss_streak"
    CONSTITUTION = (
        "Repeated world-model misses mean the mechanics story in NOTES.md is "
        "wrong; fix the story before spending more actions. Missed gambles are "
        "a different signal (the gamble streak)."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        missed, graded = bucket_misses(view.events)["world_model"]
        if missed >= 3:
            return (
                f"{missed} of the last {graded} world-model predictions missed: the mechanics "
                "story in NOTES.md is wrong; fix it before spending more actions"
            )
        return None

    def demand(self, view: ModuleView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        hits, total = recent_predictions(view.events)
        counts = bucket_misses(view.events)
        return {
            "recent_hits": hits,
            "recent_graded": total,
            "world_model_missed": counts["world_model"][0],
            "gamble_missed": counts["gamble"][0],
        }


GAMBLE_STREAK_MIN = 3


def _gamble_outcomes(pending: Mapping[str, Any] | None) -> bool:
    """Does the pending action predict a milestone (a gamble outcome)?"""
    for outcome in (pending or {}).get("outcomes") or ():
        kind = getattr(outcome, "kind", None)
        channel = getattr(outcome, "channel", None)
        if isinstance(kind, str) and outcome_bucket(kind, channel) == "gamble":
            return True
    return False


def gamble_misses_on_unit(events: Sequence[Event]) -> int:
    return sum(
        1
        for index in unit_indices(events)
        for item in events[index].grade
        if item.bucket == "gamble" and not item.ok
    )


class _GambleStreak:
    NAME = "gamble_streak"
    CONSTITUTION = (
        "Three missed milestone gambles on one progress unit mean the win rule is "
        "a guess: name the win-rule hypothesis the next gamble tests. A gamble the "
        "module holds can still be issued as `change`, which costs nothing extra."
    )
    MODE = "advise"

    def _held(self, view: ModuleView, pending: Mapping[str, Any] | None) -> int | None:
        if not pending or pending.get("kind") not in ("act", "commit") or not _gamble_outcomes(pending):
            return None
        declares = {str(key).lower(): value for key, value in (pending.get("declares") or {}).items()}
        if str(declares.get("win_rule", "")).strip():
            return None
        missed = gamble_misses_on_unit(view.events)
        return missed if missed >= GAMBLE_STREAK_MIN else None

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        missed = self._held(view, pending)
        if missed is None:
            return None
        return (
            f"{missed} milestone gambles missed on this {_unit(view)}: name the win-rule "
            'hypothesis this one tests (--declare "win_rule=...") or predict `change` instead'
        )

    def demand(
        self, view: ModuleView, pending: Mapping[str, Any] | None
    ) -> dict[str, str] | None:
        missed = self._held(view, pending)
        if missed is None:
            return None
        return {
            "win_rule": (
                f"{missed} milestone gambles missed on this {_unit(view)}: the hypothesis about "
                "what completes it that this prediction tests"
            )
        }

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        return {"gamble_missed_on_unit": gamble_misses_on_unit(view.events)}


class _NullForensics:
    NAME = "null_forensics"
    CONSTITUTION = (
        "A predicted-change that observed nothing is a null result: inspect the "
        "raw observation before the hypothesis may be closed."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        for event in reversed(view.events):
            if event.predict_ok is None:
                continue
            if event.predict_ok:
                return None
            for item in event.grade:
                if item.kind == "change" and not item.ok:
                    return (
                        f"e{event.id} predicted change and observed nothing; "
                        "run `assay view` on it and read the raw observation before "
                        "closing that hypothesis"
                    )
            return None
        return None

    def demand(self, view: ModuleView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        return {}


class _ParkWithTest:
    NAME = "park_with_test"
    CONSTITUTION = (
        "Abandoning a state is only safe if the notes carry a test that would "
        "re-open the abandoned line; park hypotheses with their test."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        if pending and pending.get("kind") == "reset":
            return (
                "resetting: record in NOTES.md what would have to be true to "
                "revisit this state (park the line WITH its test)"
            )
        return None

    def demand(self, view: ModuleView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        return {}


class _Specificity:
    NAME = "specificity"
    CONSTITUTION = (
        "Coerced free-text outcomes are excluded from every meter and promotion; "
        "vagueness earns nothing."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        # The one count the PREDICTIONS line prints, over the same grades: the
        # advisory's N/M is the PREDICTIONS line's at this moment.
        counts = specificity(view.events)
        if counts.graded >= 20 and counts.coerced * 2 > counts.graded:
            return (
                f"specificity is {counts.specific}/{counts.graded}: over half the graded "
                "outcomes are coerced free text; they earn nothing. State checkable outcomes."
            )
        return None

    def demand(self, view: ModuleView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        return {}


class _Hazard:
    NAME = "hazard"
    CONSTITUTION = (
        "Hazards are effect signatures, not action identities: an action class "
        "whose graded result entered a loss state or dropped banked progress "
        "gets a worst-case + recovery declaration demand on its next use. The "
        "demand is cheap and structural; it never bans."
    )
    MODE = "advise"

    def _tagged_classes(self, view: ModuleView) -> dict[str, dict[str, Any]]:
        return {
            str(tag["action_class"]): tag
            for tag in view.hazards()
            if tag.get("active", True)
        }

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        demands = self.demand(view, pending)
        if demands:
            tag = self._tagged_classes(view).get(str((pending or {}).get("name")))
            evidence = f" (evidence e{tag['evidence_event']})" if tag and tag.get(
                "evidence_event") is not None else ""
            return (
                f"{(pending or {}).get('name')} matches a recorded hazard signature "
                f"{tag.get('signature') if tag else ''}{evidence}; declare "
                "--declare worst_case=... and --declare recovery=... with this use"
            )
        return None

    def demand(
        self, view: ModuleView, pending: Mapping[str, Any] | None
    ) -> dict[str, str] | None:
        if not pending or pending.get("kind") not in {"act", "commit"}:
            return None
        tag = self._tagged_classes(view).get(str(pending.get("name")))
        if tag is None:
            return None
        declares = pending.get("declares") or {}
        missing = {
            field: f"hazard signature {tag.get('signature')} recorded for this action class"
            for field in ("worst_case", "recovery")
            if not str(declares.get(field, "")).strip()
        }
        return missing or None

    def observe(self, view: ModuleView, event: Event) -> None:
        if not event.counts_action:
            return
        signature = None
        if str(event.state) == "GAME_OVER":
            signature = "entered_loss_state"
        elif event.level_before is not None and event.levels_completed < event.level_before:
            signature = "milestone_dropped"
        if signature is None:
            return
        action_class = str(event.action)
        if any(
            tag["action_class"] == action_class and tag["signature"] == signature
            for tag in view.hazards()
        ):
            return
        view.record(
            "hazard_tagged",
            action_class=action_class,
            signature=signature,
            evidence_event=int(event.id),
            origin="observed",
            active=True,
        )

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        tags = view.hazards()
        return {"tags": len(tags), "imported": sum(1 for tag in tags if tag.get("origin") == "import")}


# --- coverage audit -----------------------------------------------------------

COVERAGE_DEAD_MIN_TRIES = 6   # tried this often and never productive: believed dead
COVERAGE_STALL_WINDOW = 8     # this many consecutive non-productive paid actions: a stall
COVERAGE_LOOP_MIN = 3         # this many identical failing moves in a row: a loop
# Declaration keys that tag an impossibility or absence conclusion, plus the
# words looked for in a `conclusion=` value. Deliberately unambiguous.
COVERAGE_SENTINELS = frozenset(
    {"impossible", "unsolvable", "unwinnable", "absent", "missing", "dead_end", "give_up"}
)


def _event_changed(events: Sequence[Event], index: int) -> bool:
    """Did paid event `index` change the world? The graded change or noop
    outcome when one exists, else the settled observation compared with the
    previous event's: the object for dict worlds, and the observation kind's
    own signal for its shape (the settled frame, for frame worlds). Progress
    always counts as change."""
    event = events[index]
    if event.level_advanced:
        return True
    for item in event.grade:
        if item.kind == "change":
            return bool(item.ok)
        if item.kind == "noop":
            return not item.ok
    if index == 0:
        return False
    previous = events[index - 1]
    kind = kind_for(event)
    if kind is not None:
        return kind.changed(previous, event)
    return event.observation != previous.observation


def _params_key(params: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(params, Mapping):
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in params.items()))


def _move_of(event: Event) -> tuple[str, tuple[tuple[str, str], ...]]:
    return str(event.action).upper(), _params_key(event.data)


def _pending_move(pending: Mapping[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return str(pending.get("name", "")).upper(), _params_key(pending.get("params"))


def _format_move(move: tuple[str, tuple[tuple[str, str], ...]]) -> str:
    name, params = move
    if params:
        return name + "(" + ",".join(f"{key}={value}" for key, value in params) + ")"
    return name


def _advertised(events: Sequence[Event]) -> list[str]:
    """The names the last event advertises, as the registry spells them."""
    last = events[-1]
    kind = kind_for(last)
    if kind is not None:
        names = [name.upper() for name in kind.advertised_names(last)]
    else:
        names = [str(value).upper() for value in last.available_actions]
    return sorted(set(names))


def coverage_ledger(events: Sequence[Event]) -> dict[str, Any]:
    """The coverage facts for the current progress unit."""
    indices = unit_indices(events)
    paid = [index for index in indices if events[index].counts_action]
    plays: dict[str, list[int]] = {}
    for index in paid:
        name = str(events[index].action).upper()
        record = plays.setdefault(name, [0, 0])
        record[0] += 1
        if _event_changed(events, index):
            record[1] += 1
    advertised = _advertised(events)
    untried = [name for name in advertised if plays.get(name, (0, 0))[0] == 0]
    dead = [
        name
        for name in advertised
        if plays.get(name, (0, 0))[0] >= COVERAGE_DEAD_MIN_TRIES and plays[name][1] == 0
    ]
    tail = paid[-COVERAGE_STALL_WINDOW:]
    stalled = (
        len(tail) == COVERAGE_STALL_WINDOW
        and str(events[-1].state) == "NOT_FINISHED"
        and not any(_event_changed(events, index) for index in tail)
    )
    return {
        "unit": events[-1].levels_completed,
        "paid_on_unit": len(paid),
        "paid_indices": paid,
        "advertised": advertised,
        "untried": untried,
        "dead": dead,
        "stalled": stalled,
    }


def _gap_phrase(events: Sequence[Event], ledger: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if ledger["dead"]:
        parts.append(f"never-productive actions [{', '.join(ledger['dead'])}]")
    if ledger["untried"]:
        parts.append(f"untried actions [{', '.join(ledger['untried'])}]")
    kind = kind_for(events[-1])
    extra = getattr(kind, "coverage_gap", None) if kind is not None else None
    if callable(extra):
        phrase = extra(events, ledger["paid_indices"])
        if phrase:
            parts.append(phrase)
    if not parts and len(ledger["advertised"]) <= 1:
        # One advertised action: every action is "tried" from the first
        # status, so the action ledger says nothing about coverage.
        return (
            "unexercised: one action is advertised, so the action ledger cannot judge "
            "coverage; enumerate the objects and states the conclusion rests on and "
            "probe the ones the record never touched."
        )
    if not parts:
        return (
            "coverage looks saturated: every available action has been productive. "
            "If the conclusion still holds, name the unstated premise it rests on "
            "(the observation itself is the classic one)."
        )
    return "unexercised: " + "; ".join(parts) + "."


def _is_conclusion(pending: Mapping[str, Any] | None) -> bool:
    if not pending:
        return False
    declares = {str(key).lower(): str(value).lower() for key, value in (pending.get("declares") or {}).items()}
    if set(declares) & COVERAGE_SENTINELS:
        return True
    conclusion = declares.get("conclusion", "")
    return any(word in conclusion for word in COVERAGE_SENTINELS)


class _CoverageAudit:
    NAME = "coverage_audit"
    CONSTITUTION = (
        "Provably unsolvable is a property of your model, not of the world. "
        "Before you conclude that a progress unit is impossible or that something "
        "is absent, every rule that conclusion rests on must have been exercised "
        "by a graded transition in the regime where the conclusion needs it. "
        "Consistency with the record is not evidence: the record may never have "
        "visited the region your conclusion depends on. Enumerate the load-bearing "
        "rules, check which the journal exercised, and buy cheap probes for the "
        "gaps before the conclusion stands."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        events = list(view.events)
        if len(events) < 2:
            return None
        ledger = coverage_ledger(events)
        paid = [event for event in events if event.counts_action]
        if pending and pending.get("kind") in ("act", "commit") and paid:
            move = _pending_move(pending)
            # The loop (the stronger condition) before the single re-issue, or
            # the loop message could never fire.
            tail = paid[-COVERAGE_LOOP_MIN:]
            if (
                len(tail) == COVERAGE_LOOP_MIN
                and all(_move_of(event) == move for event in tail)
                and all(event.predict_ok is False for event in tail)
            ):
                return (
                    f"{_format_move(move)} has missed {COVERAGE_LOOP_MIN} times in a row, "
                    f"you are looping. Stop repeating it. {_gap_phrase(events, ledger)}"
                )
            last = paid[-1]
            if last.predict_ok is False and _move_of(last) == move:
                return (
                    f"re-issuing {_format_move(move)} unmodified, it just graded FALSE. "
                    f"Halt: revise the model or probe an unexercised rule. "
                    f"{_gap_phrase(events, ledger)}"
                )
        if _is_conclusion(pending):
            return (
                "impossibility or absence conclusion detected, run the COVERAGE AUDIT "
                "before it stands. Enumerate the rules the conclusion load-bears on and "
                "cite the graded events that exercised each. " + _gap_phrase(events, ledger)
            )
        if pending is None:
            if ledger["paid_on_unit"] < 3:
                return None
            if ledger["stalled"]:
                note = (
                    f" STALL: the last {COVERAGE_STALL_WINDOW} actions changed nothing. "
                    f"{_gap_phrase(events, ledger)}"
                )
            elif notes_verdicts(_notes_text(view.paths)):
                # At a stop (a verdict in the notes) the regions and objects
                # the record never touched belong on the status line.
                note = f" AT A STOP: {_gap_phrase(events, ledger)}"
            else:
                note = ""
            head = f"coverage {_unit(view)} {ledger['unit'] + 1}: "
            if len(ledger["advertised"]) <= 1:
                kind = kind_for(events[-1])
                extra = getattr(kind, "coverage_objects", None) if kind is not None else None
                objects = extra(events, ledger["paid_indices"]) if callable(extra) else None
                return (
                    head
                    + f"one action is advertised ({', '.join(ledger['advertised']) or 'none'}), "
                    "so untried and no-op-only say nothing here"
                    + (f"; {objects}" if objects else "")
                    + f".{note}"
                )
            return (
                head
                + f"untried [{', '.join(ledger['untried']) or 'none'}], "
                f"no-op-only [{', '.join(ledger['dead']) or 'none'}].{note}"
            )
        if pending.get("kind") == "reset" and ledger["stalled"]:
            return (
                f"resetting under a stall. {_gap_phrase(events, ledger)} Probe these "
                f"before treating the {_unit(view)} as impossible."
            )
        return None

    def demand(
        self, view: ModuleView, pending: Mapping[str, Any] | None
    ) -> dict[str, str] | None:
        if not pending:
            return None
        declares = {str(key).lower(): value for key, value in (pending.get("declares") or {}).items()}
        if _is_conclusion(pending) and not str(declares.get("coverage_audit", "")).strip():
            return {
                "coverage_audit": (
                    "before an impossibility or absence conclusion, enumerate the load-bearing "
                    "rules and cite the graded event ids that exercised each, and probe any "
                    "unexercised rule first"
                )
            }
        if pending.get("kind") in ("act", "commit"):
            paid = [event for event in view.events if event.counts_action]
            if (
                paid
                and paid[-1].predict_ok is False
                and _move_of(paid[-1]) == _pending_move(pending)
                and not str(declares.get("revised", "")).strip()
            ):
                return {
                    "revised": (
                        "the identical previous prediction graded FALSE, declare what you "
                        "changed or choose a different action or region"
                    )
                }
        return None

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        events = list(view.events)
        if not events:
            return {}
        ledger = coverage_ledger(events)
        telemetry = {
            "unit": ledger["unit"],
            "paid_on_unit": ledger["paid_on_unit"],
            "untried": len(ledger["untried"]),
            "dead": len(ledger["dead"]),
            "stalled": ledger["stalled"],
        }
        kind = kind_for(events[-1])
        extra = getattr(kind, "coverage_telemetry", None) if kind is not None else None
        if callable(extra):
            telemetry.update(extra(events, ledger["paid_indices"]))
        return telemetry


# --- conclusion gate ------------------------------------------------------------
#
# The coverage audit's conclusion gate keys on a declaration word, and a
# player that concludes in prose never says one. This module keys the same
# demand on the record instead: a reset of a progress unit that did not end
# in GAME_OVER, and a verdict the notes gained since the last audit: a
# `STOP:` line anywhere (read by the operator's Stop gate's own rule, so the
# module and the gate agree on what a declaration is), or a Plan line or a
# heading that says parked, blocked, dead end, stop or impossible and is not a
# numbered step, a disclaimer or a placeholder. At status it also checks the
# resume: the newest verdict, by its text, with no paid action since it
# appeared.

VERDICT = re.compile(
    r"\b(parked|parking|blocked|dead[ -]?end|stop|stopped|stopping|unsolvable|impossible|"
    r"unreachable|unfinishable|unwinnable|give up|gave up|abandon|abandoned)\b",
    re.IGNORECASE,
)
# Lines the verdict words appear in without being a verdict (2026-10-26:
# "Stop predicting change blindly", "Blocked moves: ACTION1 e124", "no stop
# verdict", "If a dead end...", "1. probe the bar", "Earlier STOP withdrawn").
NOT_A_VERDICT = re.compile(
    r"^(?:\d+\s*[.)]|step\s+\d+\b)"
    r"|\b(?:no|not|never)\s+(?:\w+\s+){0,2}"
    r"(?:stop|stopped|stopping|park|parked|verdict|dead[ -]?end|blocked|impossible|unsolvable)\b"
    r"|\b(?:don't|do not|without|unless|if|avoid|before)\b[^.;]{0,30}"
    r"\b(?:stop|stopped|stopping|park|parked|verdict|dead[ -]?end|blocked|impossible|unsolvable)\b"
    r"|\bstop\s+\w+ing\b"
    r"|\bblocked\s+(?:by|moves?|cells?|paths?|walls?|steps?)\b|\b(?:moves?|steps?|paths?)\s+(?:is\s+|are\s+)?blocked\b"
    r"|\b(?:withdrawn|retracted|cancelled|canceled|superseded)\b",
    re.IGNORECASE,
)
# A heading that only labels the STOP block below it ("## STOP", "## Stop block").
STOP_LABEL = re.compile(r"^#+\s*stop(?:\s+blocks?)?\s*:?\s*$", re.IGNORECASE)
# The operator's Stop gate's rule for a `STOP:` line (hooks/stop_gate.py
# `stop_reason`): the same placeholder pattern, kept equal by a test.
STOP_DECLARATION = "STOP:"
STOP_PLACEHOLDER = re.compile(
    r"^(?:none|n/?a|-+|\(.*|not\s+(?:stopped|applicable|yet)\b.*|no\s+stop\b.*)\.?$",
    re.IGNORECASE,
)
UNTESTED = re.compile(
    r"\b(untested|not yet tested|never tested|not tested|unexercised|unprobed|untried)\b",
    re.IGNORECASE,
)
AUDITED = "conclusion_audited"
PROBED = "verdict_probed"
AUDIT_FIELD = "coverage_audit"
AUDIT_WHY = (
    "name the rules it rests on with the graded event ids that exercised each, "
    "what the record never exercised in the regime it needs, and the next "
    "cheapest probe (buy it unless you say why not)"
)


def _notes_text(paths: RunPaths) -> str:
    try:
        return paths.notes.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def stop_reason(line: str) -> str | None:
    """The reason of a `STOP:` line by the Stop gate's rule: the text after
    `STOP:`, empty for a bare one, None for any other line and for a
    placeholder."""
    stripped = line.strip()
    if not stripped.startswith(STOP_DECLARATION):
        return None
    reason = stripped[len(STOP_DECLARATION):].strip()
    return None if STOP_PLACEHOLDER.match(reason) else reason


def stop_lines(text: str) -> list[str]:
    """The notes' stop declarations, as the Stop gate counts them."""
    return [reason for reason in map(stop_reason, text.splitlines()) if reason]


def notes_verdicts(text: str) -> list[str]:
    """The verdict lines of the notes, in order: every `STOP:` line the Stop
    gate counts (under any heading), and the Plan-section lines and headings
    that say parked, blocked, dead end, stop or impossible, without the
    numbered steps, disclaimers, placeholders and the STOP block's own label."""
    found: list[str] = []
    in_plan = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(STOP_DECLARATION):
            reason = stop_reason(stripped)
            if reason:
                found.append(f"{STOP_DECLARATION} {reason}")
            continue
        if stripped.startswith("#"):
            in_plan = stripped.lstrip("#").strip().lower().startswith("plan")
            if VERDICT.search(stripped) and not STOP_LABEL.match(stripped) and not NOT_A_VERDICT.search(
                stripped.lstrip("#").strip()
            ):
                found.append(stripped)
            continue
        body = stripped.lstrip("-* ").strip()
        if in_plan and VERDICT.search(body) and not NOT_A_VERDICT.search(body):
            found.append(stripped)
    return found


def newest_verdict(text: str) -> str | None:
    """The verdict the resume check keys on: the newest `STOP:` line when
    the notes hold one, else the newest verdict line."""
    verdicts = notes_verdicts(text)
    stops = [verdict for verdict in verdicts if verdict.startswith(STOP_DECLARATION)]
    found = stops or verdicts
    return found[-1] if found else None


def notes_untested(text: str, limit: int = 3) -> list[str]:
    """The notes' own lines that name something untested, shortened."""
    found = [line.strip().lstrip("-* ").strip() for line in text.splitlines() if UNTESTED.search(line)]
    return [line if len(line) <= 120 else line[:117] + "..." for line in found[:limit]]


def _fingerprint(verdicts: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(verdicts).encode("utf-8")).hexdigest()[:16]


def _records(paths: RunPaths, kind: str) -> list[dict[str, Any]]:
    """The activity records of one kind, oldest first; lines without the
    kind's name are skipped unparsed."""
    try:
        lines = paths.activity.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found: list[dict[str, Any]] = []
    for line in lines:
        if kind not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("kind") == kind:
            found.append(record)
    return found


def _last_audited(paths: RunPaths) -> str | None:
    """The fingerprint of the notes' verdicts at the last audit, from the
    module's own activity records."""
    records = _records(paths, AUDITED)
    value = records[-1].get("fingerprint") if records else None
    return str(value) if value is not None else None


def _verdict_key(verdict: str | None) -> str | None:
    return None if verdict is None else _fingerprint([" ".join(verdict.split())])


def _audited_now(pending: Mapping[str, Any] | None) -> bool:
    declares = {str(key).lower(): value for key, value in ((pending or {}).get("declares") or {}).items()}
    return bool(str(declares.get(AUDIT_FIELD, "")).strip())


def _shorten(text: str, limit: int = 100) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


class _ConclusionGate:
    NAME = "conclusion_gate"
    CONSTITUTION = (
        "A stop, a park or a reset with budget left is a conclusion, whatever word "
        "it is written in, and it carries the coverage audit: the rules it rests on "
        "with their event ids, what the record never exercised, and the next "
        "cheapest probe. A verdict inherited from an earlier session is Assumed "
        "until a graded probe confirms it."
    )
    MODE = "advise"

    def _reset_without_game_over(self, view: ModuleView, pending: Mapping[str, Any] | None) -> bool:
        return bool(
            pending
            and pending.get("kind") == "reset"
            and view.events
            and str(view.events[-1].state) != "GAME_OVER"
        )

    def _new_verdict(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        if not pending or pending.get("kind") not in ("act", "commit"):
            return None
        verdicts = notes_verdicts(_notes_text(view.paths))
        if not verdicts or _fingerprint(verdicts) == _last_audited(view.paths):
            return None
        return verdicts[-1]

    def _demanded(self, view: ModuleView, pending: Mapping[str, Any] | None) -> bool:
        """Would this action owe an audit, with or without one declared?"""
        return self._reset_without_game_over(view, pending) or self._new_verdict(view, pending) is not None

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        if pending is None:
            return self._resume_check(view)
        if _audited_now(pending):
            if self._demanded(view, pending):
                return None
            return (
                f"no audit demanded: this {AUDIT_FIELD} text is not recorded (an audit counts when a "
                "reset that did not follow GAME_OVER, or a verdict new to the notes, asks for one)"
            )
        unit = _unit(view)
        if self._reset_without_game_over(view, pending):
            return (
                f"a reset of a {unit} that did not end in GAME_OVER abandons a line of play, "
                f"and that is a conclusion: {AUDIT_WHY} "
                f'(--declare "{AUDIT_FIELD}=...")'
            )
        verdict = self._new_verdict(view, pending)
        if verdict is not None:
            return (
                f"NOTES.md holds a verdict the record has not audited ({_shorten(verdict)!r}): "
                f'before relying on it, {AUDIT_WHY} (--declare "{AUDIT_FIELD}=...")'
            )
        return None

    def _resume_check(self, view: ModuleView) -> str | None:
        """The newest verdict, by its text, with no paid action since it
        appeared: the start of a session that inherited it, or a session
        about to end. Keyed on the verdict's text rather than the notes'
        time, so an edit elsewhere in the notes does not re-arm it."""
        text = _notes_text(view.paths)
        verdict = newest_verdict(text)
        paid = [event for event in view.events if event.counts_action]
        key = _verdict_key(verdict)
        if verdict is None or key is None or not paid:
            return None
        if any(record.get("key") == key for record in _records(view.paths, PROBED)):
            return None
        untested = notes_untested(text)
        named = (
            "the notes name these untested: " + "; ".join(untested)
            if untested
            else "the notes name nothing untested: list what the verdict never exercised"
        )
        return (
            f"RESUME CHECK: the verdict in NOTES.md ({_shorten(verdict)!r}) has had 0 graded "
            f"probes since e{paid[-1].id}; {named}. Treat it as Assumed and buy the cheapest probe "
            "before you build on it or stop on it."
        )

    def demand(
        self, view: ModuleView, pending: Mapping[str, Any] | None
    ) -> dict[str, str] | None:
        if not pending or _audited_now(pending):
            return None
        if self._reset_without_game_over(view, pending):
            return {AUDIT_FIELD: f"this reset abandons the line of play with budget left: {AUDIT_WHY}"}
        verdict = self._new_verdict(view, pending)
        if verdict is not None:
            return {AUDIT_FIELD: f"NOTES.md gained a verdict ({_shorten(verdict, 60)!r}): {AUDIT_WHY}"}
        return None

    def observe(self, view: ModuleView, event: Event) -> None:
        text = _notes_text(view.paths)
        if event.counts_action:
            key = _verdict_key(newest_verdict(text))
            if key is not None and not any(record.get("key") == key for record in _records(view.paths, PROBED)):
                view.record(PROBED, key=key, event=int(event.id))
        if not str(event.declares.get(AUDIT_FIELD, "")).strip():
            return
        # An audit counts only while one was owed: a reset that did not
        # follow GAME_OVER, or verdicts the last audit did not cover. A
        # volunteered text, or the copy a batch carries onto every step, is
        # not recorded.
        events = view.events
        prior = events[-2] if len(events) > 1 and events[-1].id == event.id else (events[-1] if events else None)
        reset_owed = str(event.action).upper() == "RESET" and prior is not None and str(prior.state) != "GAME_OVER"
        fingerprint = _fingerprint(notes_verdicts(text))
        verdict_owed = bool(notes_verdicts(text)) and fingerprint != _last_audited(view.paths)
        if reset_owed or verdict_owed:
            view.record(AUDITED, event=int(event.id), fingerprint=fingerprint)

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        verdicts = notes_verdicts(_notes_text(view.paths))
        return {
            "verdicts": len(verdicts),
            "audited": bool(verdicts) and _fingerprint(verdicts) == _last_audited(view.paths),
        }


# --- a non-reset action inside GAME_OVER -----------------------------------------
#
# After GAME_OVER the world takes only a reset; an action it refuses there
# is not a probe. On 2026-10-26 one run issued 34 of them in a row.


def refused_in_game_over(view: ModuleView) -> int:
    """The act and commit commands refused since the last event, when that
    event ended the unit in GAME_OVER."""
    if not view.events or str(view.events[-1].state) != "GAME_OVER":
        return 0
    last = int(view.events[-1].id)
    return sum(
        1
        for record in _records(view.paths, "command_end")
        if record.get("command") in ("act", "commit") and record.get("status") == "ERROR" and record.get("event") == last
    )


class _GameOverRetry:
    NAME = "game_over_retry"
    CONSTITUTION = (
        "After GAME_OVER only a reset continues the progress unit. An action the world "
        "refuses there buys nothing and tests nothing: reset, and say what the reset tests."
    )
    MODE = "advise"

    def _held(self, view: ModuleView, pending: Mapping[str, Any] | None) -> int | None:
        if not pending or pending.get("kind") not in ("act", "commit"):
            return None
        refused = refused_in_game_over(view)
        return refused if refused >= 1 else None

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        refused = self._held(view, pending)
        if refused is None:
            return None
        return (
            f"the {_unit(view)} ended in GAME_OVER at e{view.events[-1].id} and {refused} non-reset "
            "action(s) were refused since: only `assay reset` continues it"
        )

    def demand(
        self, view: ModuleView, pending: Mapping[str, Any] | None
    ) -> dict[str, str] | None:
        refused = self._held(view, pending)
        if refused is None:
            return None
        return {
            "game_over": (
                f"the {_unit(view)} ended in GAME_OVER at e{view.events[-1].id} and {refused} non-reset "
                "action(s) were refused since; only `assay reset` continues it. Say why this "
                "action is not a reset"
            )
        }

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        return {"refused_in_game_over": refused_in_game_over(view)}


BUILTINS: tuple[Module, ...] = (
    _WallSpend(),
    _MissStreak(),
    _NullForensics(),
    _ParkWithTest(),
    _Specificity(),
    _Hazard(),
    _CoverageAudit(),
    _ConclusionGate(),
    _GambleStreak(),
    _GameOverRetry(),
)


_CONTRACT = ("NAME", "CONSTITUTION", "MODE", "trigger", "demand", "telemetry")
MANIFEST_NAME = "manifest.json"


def modules_dir(paths: RunPaths) -> Path:
    return paths.state / "modules"


def manifest_path(paths: RunPaths) -> Path:
    return modules_dir(paths) / MANIFEST_NAME


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_object(module: Any, file: Path) -> Module:
    candidate = getattr(module, "MODULE", module)
    for attr in _CONTRACT:
        if not hasattr(candidate, attr):
            raise AssayError(
                f"module {file.name} lacks {attr!r}; the module contract is "
                "NAME, CONSTITUTION, MODE, trigger(), demand(), telemetry()",
                code="MODULE_CONTRACT",
            )
    loaded: Module = candidate
    return loaded


def module_name_of(file: Path) -> str:
    """Load a module file once to read its NAME and check its contract."""
    with import_path(file, "assay_module") as module:
        return str(_module_object(module, file).NAME)


def _check_name(name: str, file: Path, taken: Mapping[str, str]) -> None:
    builtin = {module.NAME for module in BUILTINS}
    if name in builtin:
        raise AssayError(
            f"module file {file.name} declares NAME {name!r}, which is a built-in; "
            "external modules need their own name",
            code="MODULE_CONTRACT",
        )
    if name in taken and taken[name] != file.name:
        raise AssayError(
            f"module file {file.name} declares NAME {name!r}, already provided by "
            f"{taken[name]}",
            code="MODULE_CONTRACT",
        )


def load_manifest(paths: RunPaths) -> list[dict[str, Any]]:
    value = read_json(manifest_path(paths), None)
    if isinstance(value, dict) and isinstance(value.get("modules"), list):
        return [entry for entry in value["modules"] if isinstance(entry, dict)]
    return []


def unadmitted_entries(
    manifest: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any] | None,
    activity: Iterable[Mapping[str, Any]],
) -> set[str]:
    """The files of the manifest entries neither source of authority admits
    (docs/ARCHITECTURE.md sections 6.4 and 8.2): the pinned registry's
    `modules` list, which names a source by the pinned file's name (the
    basename `pin_external_modules` and `reconstruct_manifest` key by), or a
    `module_installed` activity record carrying the entry's hash. `Run.load`
    holds them as `run.unadmitted`; the inventory never loads them."""
    registered = {
        Path(entry).name
        for entry in ((registry or {}).get("modules") or ())
        if isinstance(entry, str)
    }
    installed = {
        record.get("sha256")
        for record in activity
        if record.get("kind") == "module_installed" and isinstance(record.get("sha256"), str)
    }
    return {
        str(entry.get("file"))
        for entry in manifest
        if entry.get("file") not in registered and entry.get("sha256") not in installed
    }


def _write_manifest(paths: RunPaths, entries: Sequence[Mapping[str, Any]]) -> None:
    atomic_json(manifest_path(paths), {"version": 1, "modules": list(entries)})


def pin_external_modules(paths: RunPaths, registry: Mapping[str, Any] | None) -> None:
    """Copy registered module files into .assay/modules/ at start and write the
    manifest (pack-tier trust: human-installed, never agent-writable). Each
    file is loaded once here to check its contract and its NAME against the
    built-ins and the other entries, before any spend."""
    if not registry or not registry.get("modules"):
        return
    target = modules_dir(paths)
    target.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    taken: dict[str, str] = {}
    for entry in registry["modules"]:
        source = Path(entry)
        if not source.is_absolute():
            source = paths.root / source
        if not source.exists():
            raise AssayError(f"registered module file not found: {entry}", code="FILE_NOT_FOUND")
        pinned = target / source.name
        shutil.copy2(source, pinned)
        name = module_name_of(pinned)
        _check_name(name, pinned, taken)
        taken[name] = pinned.name
        entries.append(
            {
                "name": name,
                "file": pinned.name,
                "source": str(source),
                "sha256": _sha256(pinned),
                "installed_at": time.time(),
                "origin": "registry",
            }
        )
    _write_manifest(paths, entries)


def install_module(run: Run, source: Path, token: str | None) -> dict[str, Any]:
    """Owner-authorized mid-run install, run by the daemon on the run it
    holds: the token against the held hash, copy, check the contract, append
    to the held manifest and write it, journal `module_installed`, and drop
    the held module set so the next `active_modules` reloads it. The
    sanctioned hot-add channel."""
    from .agenda import require_owner

    require_owner(run, token)
    paths = run.paths
    if not source.is_file():
        raise AssayError(f"module file not found: {source}", code="FILE_NOT_FOUND")
    if source.suffix != ".py":
        raise AssayError("a module is one Python file (.py)", code="MODULE_CONTRACT")
    target = modules_dir(paths)
    target.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in run.manifest if entry.get("file") != source.name]
    taken = {str(entry["name"]): str(entry["file"]) for entry in entries}
    pinned = target / source.name
    shutil.copy2(source, pinned)
    try:
        name = module_name_of(pinned)
        _check_name(name, pinned, taken)
    except AssayError:
        pinned.unlink(missing_ok=True)
        raise
    record = {
        "name": name,
        "file": pinned.name,
        "source": str(source.resolve()),
        "sha256": _sha256(pinned),
        "installed_at": time.time(),
        "origin": "install",
    }
    entries.append(record)
    _write_manifest(paths, entries)
    run.manifest = entries
    run.unadmitted.discard(pinned.name)
    run.modules = None
    append_jsonl(paths.activity, {"kind": "module_installed", **record})
    return record


def reconstruct_manifest(paths: RunPaths) -> list[dict[str, Any]]:
    """A run started before the manifest existed: its pinned files are exactly
    the registry's `modules` list by basename. `assay start` rebuilds the
    manifest from those files once, so such a run keeps its modules and
    anything else in the directory is unlisted. A run with a manifest, or
    with no pinned files, is left alone."""
    if manifest_path(paths).exists() or not modules_dir(paths).is_dir():
        return []
    registry = read_json(paths.registry, None)
    wanted = {
        Path(entry).name
        for entry in ((registry or {}).get("modules") or ())
        if isinstance(entry, str)
    }
    entries: list[dict[str, Any]] = []
    taken: dict[str, str] = {}
    for file in sorted(modules_dir(paths).glob("*.py")):
        if file.name not in wanted:
            continue
        try:
            name = module_name_of(file)
            _check_name(name, file, taken)
        except AssayError:
            continue
        taken[name] = file.name
        entries.append(
            {
                "name": name,
                "file": file.name,
                "source": None,
                "sha256": _sha256(file),
                "installed_at": None,
                "origin": "registry",
                "reconstructed": True,
            }
        )
    if entries:
        _write_manifest(paths, entries)
    return entries


def module_inventory(run: Run) -> dict[str, Any]:
    """Listed and loadable files, and everything in the directory that is not:
    unlisted files, listed files whose hash changed, and the entries the
    run holds as unadmitted (neither the pinned registry nor a
    `module_installed` record vouches for them, section 8.2). Reads the held
    manifest and the directory; writes nothing."""
    target = modules_dir(run.paths)
    if not target.is_dir():
        return {"listed": [], "ignored": []}
    entries = run.manifest
    listed: list[dict[str, Any]] = []
    ignored: list[str] = []
    by_file = {str(entry.get("file")): entry for entry in entries}
    for file in sorted(target.glob("*.py")):
        entry = by_file.get(file.name)
        if entry is None:
            ignored.append(f"{file.name} (not in the manifest)")
        elif file.name in run.unadmitted:
            ignored.append(
                f"{file.name} (manifest entry not admitted: neither registered nor installed)"
            )
        elif _sha256(file) != entry.get("sha256"):
            ignored.append(f"{file.name} (modified since install)")
        else:
            listed.append({**entry, "path": file})
    for entry in entries:
        if not (target / str(entry.get("file"))).exists():
            ignored.append(f"{entry.get('file')} (listed but missing)")
    return {"listed": listed, "ignored": ignored}


def _load_external(run: Run) -> list[Module]:
    loaded: list[Module] = []
    for entry in module_inventory(run)["listed"]:
        file = entry["path"]
        with import_path(file, "assay_module") as module:
            loaded.append(_module_object(module, file))
    return loaded


def ignored_modules(run: Run) -> list[str]:
    """The files in `.assay/modules` the manifest does not cover, each with
    its reason."""
    ignored: list[str] = module_inventory(run)["ignored"]
    return ignored


def ignored_text(ignored: Sequence[str]) -> list[str]:
    if not ignored:
        return []
    return [
        f"MODULES | {len(ignored)} file(s) in .assay/modules ignored (not installed "
        f"through the registry or `assay module install`): {', '.join(ignored)}"
    ]


def unlisted_lines(run: Run) -> list[str]:
    return ignored_text(ignored_modules(run))


@dataclasses.dataclass(frozen=True, slots=True)
class ModuleEntry:
    name: str
    mode: str
    origin: str
    constitution: str
    telemetry: dict[str, Any]


@dataclasses.dataclass(frozen=True, slots=True)
class ModuleList:
    """What `assay module list` knows: the active modules with their mode,
    origin, constitution and telemetry, and the ignored files."""

    modules: tuple[ModuleEntry, ...]
    ignored: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "modules": [dataclasses.asdict(entry) for entry in self.modules],
            "ignored": list(self.ignored),
        }


def module_list_of(run: Run) -> ModuleList:
    origins = {str(entry.get("name")): str(entry.get("origin")) for entry in run.manifest}
    view = ModuleView(run)
    entries: list[ModuleEntry] = []
    for item, mode in active_modules(run):
        try:
            telemetry = item.telemetry(view)
        except Exception as error:  # noqa: BLE001 - a module's counters never break the listing
            telemetry = {"error": f"{type(error).__name__}: {error}"}
        entries.append(
            ModuleEntry(
                name=str(item.NAME),
                mode=str(mode),
                origin=origins.get(item.NAME, "built-in"),
                constitution=str(item.CONSTITUTION),
                # As JSON data: a counter a module keeps in another type is
                # rendered through `str`, as the listing always printed it.
                telemetry=json.loads(json.dumps(telemetry, sort_keys=True, default=str)),
            )
        )
    return ModuleList(modules=tuple(entries), ignored=tuple(ignored_modules(run)))


def module_list_text(listing: ModuleList) -> list[str]:
    lines = ["MODULES | active (name, mode, origin), constitution, telemetry"]
    for entry in listing.modules:
        lines.append(f"  {entry.name} | {entry.mode} | {entry.origin}")
        lines.append(f"    constitution: {entry.constitution}")
        lines.append(f"    telemetry: {json.dumps(entry.telemetry, sort_keys=True)}")
    lines.extend(ignored_text(listing.ignored))
    return lines


def active_modules(run: Run) -> list[tuple[Module, str]]:
    """(module, effective_mode) for every non-off module, loaded once per run
    object from the held manifest."""
    if run.modules is None:
        modes = module_modes(run.registry)
        output: list[tuple[Module, str]] = []
        for module in (*BUILTINS, *_load_external(run)):
            mode = str(modes.get(module.NAME, module.MODE))
            if mode not in MODULE_MODES:
                mode = module.MODE
            if mode != "off":
                output.append((module, mode))
        run.modules = output
    return list(run.modules)


def consult_modules(run: Run, pending: Mapping[str, Any] | None) -> list[str]:
    """Run every active module against a pending action. Block-mode unmet
    demands REFUSE (structural, satisfiable via --declare); advise-mode
    triggers return advisory lines."""
    view = ModuleView(run)
    lines: list[str] = []
    for module, mode in active_modules(run):
        demands = module.demand(view, pending)
        if demands and mode == "block":
            wanted = " ".join(f'--declare "{field}=<text>"' for field in sorted(demands))
            reasons = "; ".join(f"{field}: {why}" for field, why in sorted(demands.items()))
            raise AssayError(
                f"MODULE {module.NAME} | declaration demanded before this action: {reasons}",
                code="MODULE_DEMAND",
                hint=(
                    f"repeat the command with {wanted} (one flag per field); any named, "
                    "non-empty text unlocks the action"
                ),
            )
        message = module.trigger(view, pending)
        if message:
            lines.append(f"MODULE {module.NAME} | {message}")
    return lines


def observe_result(run: Run, event: Event) -> None:
    """Let modules learn from a graded result (hazard tagging etc.)."""
    view = ModuleView(run)
    for module, _ in active_modules(run):
        observe = getattr(module, "observe", None)
        if callable(observe):
            observe(view, event)


def advisory_lines(run: Run) -> list[str]:
    """Status-time advisories (no pending action), the old nudge surface."""
    view = ModuleView(run)
    lines: list[str] = []
    for module, _ in active_modules(run):
        message = module.trigger(view, None)
        if message:
            lines.append(f"MODULE {module.NAME} | {message}")
    return lines
