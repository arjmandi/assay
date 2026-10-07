"""Behavior modules: declare -> enforce -> grade units.

A module is constitution text + a trigger + a demand schema + a mode + telemetry,
the `Module` protocol below:

    NAME: str                # unique, lowercase
    CONSTITUTION: str            # one paragraph of way-of-thinking text
    MODE: "advise"|"block"   # default; registry module_modes overrides per run
    trigger(view, pending) -> str | None      # advisory message when it fires
    demand(view, pending) -> dict[str,str] | None   # {field: why}, structural
    observe(view, event) -> None              # optional: learn from outcomes
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

- wall_spend:     spend escalation on one progress unit (the spend-judgment tier)
- miss_streak:    repeated prediction misses mean the notes story is wrong
- null_forensics: a predicted-change/observed-nothing verdict flags the raw
                  observation for inspection before the hypothesis is closed
- park_with_test: a reset should leave a re-entry test in the notes
- sharpness:      a low sharp-claim ratio earns nothing
- hazard:         effect-signature hazard tags: entered-loss-state
                  and milestone-drop transitions tag the action class; a tagged
                  class gets the worst-case + recovery declaration demand on
                  its next use. Tags are permanent for the run and export as
                  the distinguished carryover class.
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

import hashlib
import shutil
import time
from collections.abc import Mapping, Sequence
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
from .meters import level_action_count, recent_predictions, sharpness, unit_indices
from .records import Event
from .registry import MODULE_MODES

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


class _WallSpend:
    NAME = "wall_spend"
    CONSTITUTION = (
        "Spending long on one progress unit without progress means manual probing "
        "has stopped paying; model the mechanics offline before spending more."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        level_actions = level_action_count(view.events)
        if level_actions >= 25:
            return (
                f"{level_actions} paid actions on this {_unit(view)}; stop manual "
                "probing; model the mechanics offline (`assay python`, or the "
                "`assay model` tier: replay-verified models earn batching rights)"
            )
        return None

    def demand(self, view: ModuleView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        return {"level_actions": level_action_count(view.events)}


class _MissStreak:
    NAME = "miss_streak"
    CONSTITUTION = (
        "Repeated prediction misses mean the mechanics story in NOTES.md is "
        "wrong; fix the story before spending more actions."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        hits, total = recent_predictions(view.events)
        misses = total - hits
        if misses >= 3:
            return (
                f"{misses} of the last {total} predictions missed: the mechanics "
                "story in NOTES.md is wrong; fix it before spending more actions"
            )
        return None

    def demand(self, view: ModuleView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: ModuleView) -> dict[str, Any]:
        hits, total = recent_predictions(view.events)
        return {"recent_hits": hits, "recent_graded": total}


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


class _Sharpness:
    NAME = "sharpness"
    CONSTITUTION = (
        "Coerced free-text claims are excluded from every meter and promotion; "
        "vagueness earns nothing."
    )
    MODE = "advise"

    def trigger(self, view: ModuleView, pending: Mapping[str, Any] | None) -> str | None:
        # The one count the CLAIMS line prints, over the agent's own claims:
        # a model-plan step's machine prediction is never its vagueness.
        counts = sharpness(view.events)
        graded = counts.agent_graded
        if graded >= 20 and counts.coerced * 2 > graded:
            return (
                f"sharpness is {graded - counts.coerced}/{graded}: over half your claims "
                "are coerced free text; they earn nothing. State checkable claims."
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
        "whose graded outcome entered a loss state or dropped banked progress "
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
    from .extras import kind_for

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
    from .extras import kind_for

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
    from .extras import kind_for

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
            note = (
                f" STALL: the last {COVERAGE_STALL_WINDOW} actions changed nothing. "
                f"{_gap_phrase(events, ledger)}"
                if ledger["stalled"]
                else ""
            )
            return (
                f"coverage {_unit(view)} {ledger['unit'] + 1}: "
                f"untried [{', '.join(ledger['untried']) or 'none'}], "
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
                    "before an impossibility or absence claim, enumerate the load-bearing "
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
        from .extras import kind_for

        kind = kind_for(events[-1])
        extra = getattr(kind, "coverage_telemetry", None) if kind is not None else None
        if callable(extra):
            telemetry.update(extra(events, ledger["paid_indices"]))
        return telemetry


BUILTINS: tuple[Module, ...] = (
    _WallSpend(),
    _MissStreak(),
    _NullForensics(),
    _ParkWithTest(),
    _Sharpness(),
    _Hazard(),
    _CoverageAudit(),
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
                "NAME, CONSTITUTION, MODE, trigger(), demand(), telemetry()"
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
            "external modules need their own name"
        )
    if name in taken and taken[name] != file.name:
        raise AssayError(
            f"module file {file.name} declares NAME {name!r}, already provided by "
            f"{taken[name]}"
        )


def load_manifest(paths: RunPaths) -> list[dict[str, Any]]:
    value = read_json(manifest_path(paths), None)
    if isinstance(value, dict) and isinstance(value.get("modules"), list):
        return [entry for entry in value["modules"] if isinstance(entry, dict)]
    return []


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
            raise AssayError(f"registered module file not found: {entry}")
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
        raise AssayError(f"module file not found: {source}")
    if source.suffix != ".py":
        raise AssayError("a module is one Python file (.py)")
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
    unlisted files and listed files whose hash changed. Reads the held
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


def unlisted_lines(run: Run) -> list[str]:
    ignored = module_inventory(run)["ignored"]
    if not ignored:
        return []
    return [
        f"MODULES | {len(ignored)} file(s) in .assay/modules ignored (not installed "
        f"through the registry or `assay module install`): {', '.join(ignored)}"
    ]


def active_modules(run: Run) -> list[tuple[Module, str]]:
    """(module, effective_mode) for every non-off module, loaded once per run
    object from the held manifest."""
    if run.modules is None:
        modes = (run.registry or {}).get("module_modes") or {}
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
            wanted = ", ".join(f"--declare {field}=..." for field in sorted(demands))
            reasons = "; ".join(f"{field}: {why}" for field, why in sorted(demands.items()))
            raise AssayError(
                f"MODULE {module.NAME} | declaration demanded before this action: "
                f"{wanted} ({reasons}); the demand is structural; it never bans"
            )
        message = module.trigger(view, pending)
        if message:
            lines.append(f"MODULE {module.NAME} | {message}")
    return lines


def observe_outcome(run: Run, event: Event) -> None:
    """Let modules learn from a graded outcome (hazard tagging etc.)."""
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
