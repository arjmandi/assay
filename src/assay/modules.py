"""Behavior modules — declare → enforce → grade units.

A module is doctrine text + a trigger + a demand schema + a mode + telemetry:

    NAME: str                # unique, lowercase
    DOCTRINE: str            # one paragraph of way-of-thinking text
    MODE: "advise"|"block"   # default; registry module_modes overrides per run
    trigger(view, pending) -> str | None      # advisory message when it fires
    demand(view, pending) -> dict[str,str] | None   # {field: why} — structural
    observe(view, event) -> None              # optional: learn from outcomes
    telemetry(view) -> dict                    # free counters

The shipping ladder is telemetry-first, teeth later: everything ships at
advise until an A/B shows blocking pays (owner law). Demands are always for
checkable structure (named, non-empty fields supplied via --declare or the
destructive-gate flags), never for confidence. Module code is pack-tier trust:
installed by the human at registration (`modules: [path.py]`), loaded
daemon-side, never writable by the agent mid-run (module files are pinned into
`.assay/modules/` at start).

Built-ins (the standing nudge table plus the first structural module):

- wall_spend    — spend escalation on one level (the spend-judgment tier)
- miss_streak   — repeated prediction misses mean the notes story is wrong
- null_forensics— a predicted-change/observed-nothing verdict flags the raw
                  observation for inspection before the hypothesis is closed
- park_with_test— a reset should leave a re-entry test in the notes
- sharpness     — a low sharp-claim ratio earns nothing
- hazard        — effect-signature hazard tags: entered-loss-state
                  and milestone-drop transitions tag the action class; a tagged
                  class gets the worst-case + recovery declaration demand on
                  its next use. Tags are permanent for the run and export as
                  the distinguished carryover class.
"""

from __future__ import annotations

import dataclasses
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .core import (
    AssayError,
    RunPaths,
    append_jsonl,
    atomic_json,
    import_path,
    read_json,
)

_MODES = ("off", "advise", "block")


@dataclasses.dataclass
class JournalView:
    """What a module may read: the journal, the registry, the run paths."""

    paths: RunPaths
    events: Sequence[Mapping[str, Any]]
    registry: Mapping[str, Any] | None


def hazards_path(paths: RunPaths) -> Path:
    return paths.state / "hazards.json"


def load_hazards(paths: RunPaths) -> list[dict[str, Any]]:
    value = read_json(hazards_path(paths), [])
    return value if isinstance(value, list) else []


def _level_action_count(events: Sequence[Mapping[str, Any]]) -> int:
    if not events:
        return 0
    completed = int(events[-1]["levels_completed"])
    count = 0
    for event in reversed(events):
        if int(event["levels_completed"]) != completed:
            break
        if event.get("counts_action"):
            count += 1
    return count


def _recent_predictions(
    events: Sequence[Mapping[str, Any]], window: int = 10
) -> tuple[int, int]:
    graded = [event for event in events if event.get("predict_ok") is not None]
    recent = graded[-window:]
    hits = sum(1 for event in recent if event["predict_ok"])
    return hits, len(recent)


class _WallSpend:
    NAME = "wall_spend"
    DOCTRINE = (
        "Spending long on one level without progress means manual probing has "
        "stopped paying; model the mechanics offline before spending more."
    )
    MODE = "advise"

    def trigger(self, view: JournalView, pending: Mapping[str, Any] | None) -> str | None:
        level_actions = _level_action_count(view.events)
        general = bool(view.events) and "frames" not in view.events[-1]
        if view.registry is not None:
            # Registry runs own the general model tier; point at it, not at the
            # grid rules tier (which registry runs refuse).
            if level_actions >= 25:
                return (
                    f"{level_actions} paid actions on this level — stop manual "
                    "probing; model the mechanics offline (`assay python`, or the "
                    "`assay model` tier: replay-verified models earn batching rights)"
                )
            return None
        if general:
            if level_actions >= 25:
                return (
                    f"{level_actions} paid actions on this level — stop manual "
                    "probing; re-read your notes, kill dead assumptions, and model the "
                    "mechanics offline with `assay python` before spending more"
                )
            return None
        if level_actions >= 40:
            return (
                f"{level_actions} paid actions on this level — stop manual probing; "
                "write rules.py for it (`assay rules help`), verify with `assay rules replay`, "
                "then `assay rules solve`"
            )
        if level_actions >= 25:
            return (
                f"{level_actions} paid actions on this level — re-read your notes, "
                "kill dead assumptions, and consider the rules.py tier (`assay rules help`)"
            )
        return None

    def demand(self, view: JournalView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: JournalView) -> dict[str, Any]:
        return {"level_actions": _level_action_count(view.events)}


class _MissStreak:
    NAME = "miss_streak"
    DOCTRINE = (
        "Repeated prediction misses mean the mechanics story in NOTES.md is "
        "wrong; fix the story before spending more actions."
    )
    MODE = "advise"

    def trigger(self, view: JournalView, pending: Mapping[str, Any] | None) -> str | None:
        hits, total = _recent_predictions(view.events)
        misses = total - hits
        if misses >= 3:
            return (
                f"{misses} of the last {total} predictions missed — the mechanics "
                "story in NOTES.md is wrong; fix it before spending more actions"
            )
        return None

    def demand(self, view: JournalView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: JournalView) -> dict[str, Any]:
        hits, total = _recent_predictions(view.events)
        return {"recent_hits": hits, "recent_graded": total}


class _NullForensics:
    NAME = "null_forensics"
    DOCTRINE = (
        "A predicted-change that observed nothing is a null result: inspect the "
        "raw observation before the hypothesis may be closed."
    )
    MODE = "advise"

    def trigger(self, view: JournalView, pending: Mapping[str, Any] | None) -> str | None:
        for event in reversed(list(view.events)):
            if event.get("predict_ok") is None:
                continue
            if event["predict_ok"]:
                return None
            for item in event.get("grade") or ():
                if item.get("kind") == "change" and not item.get("ok"):
                    return (
                        f"e{event['id']} predicted change and observed nothing — "
                        "run `assay view` on it and read the raw observation before "
                        "closing that hypothesis"
                    )
            return None
        return None

    def demand(self, view: JournalView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: JournalView) -> dict[str, Any]:
        return {}


class _ParkWithTest:
    NAME = "park_with_test"
    DOCTRINE = (
        "Abandoning a board is only safe if the notes carry a test that would "
        "re-open the abandoned line; park hypotheses with their test."
    )
    MODE = "advise"

    def trigger(self, view: JournalView, pending: Mapping[str, Any] | None) -> str | None:
        if pending and pending.get("kind") == "reset":
            return (
                "resetting — record in NOTES.md what would have to be true to "
                "revisit this board (park the line WITH its test)"
            )
        return None

    def demand(self, view: JournalView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: JournalView) -> dict[str, Any]:
        return {}


class _Sharpness:
    NAME = "sharpness"
    DOCTRINE = (
        "Coerced free-text claims are excluded from every meter and promotion; "
        "vagueness earns nothing."
    )
    MODE = "advise"

    def trigger(self, view: JournalView, pending: Mapping[str, Any] | None) -> str | None:
        graded = coerced = 0
        for event in view.events:
            for item in event.get("grade") or ():
                kind = str(item.get("kind", ""))
                if kind == "note" or item.get("machine"):
                    continue
                graded += 1
                if kind == "coerced":
                    coerced += 1
        if graded >= 20 and coerced * 2 > graded:
            return (
                f"sharpness is {graded - coerced}/{graded} — over half your claims "
                "are coerced free text; they earn nothing. State checkable claims."
            )
        return None

    def demand(self, view: JournalView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: JournalView) -> dict[str, Any]:
        return {}


class _Hazard:
    NAME = "hazard"
    DOCTRINE = (
        "Hazards are effect signatures, not action identities: an action class "
        "whose graded outcome entered a loss state or dropped banked progress "
        "gets a worst-case + recovery declaration demand on its next use. The "
        "demand is cheap and structural; it never bans."
    )
    MODE = "advise"

    def _tagged_classes(self, view: JournalView) -> dict[str, dict[str, Any]]:
        return {
            str(tag["action_class"]): tag
            for tag in load_hazards(view.paths)
            if tag.get("active", True)
        }

    def trigger(self, view: JournalView, pending: Mapping[str, Any] | None) -> str | None:
        demands = self.demand(view, pending)
        if demands:
            tag = self._tagged_classes(view).get(str((pending or {}).get("name")))
            evidence = f" (evidence e{tag['evidence_event']})" if tag and tag.get(
                "evidence_event") is not None else ""
            return (
                f"{(pending or {}).get('name')} matches a recorded hazard signature "
                f"{tag.get('signature') if tag else ''}{evidence} — declare "
                "--declare worst_case=... and --declare recovery=... with this use"
            )
        return None

    def demand(
        self, view: JournalView, pending: Mapping[str, Any] | None
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

    def observe(self, view: JournalView, event: Mapping[str, Any]) -> None:
        if not event.get("counts_action"):
            return
        signature = None
        if str(event.get("state")) == "GAME_OVER":
            signature = "entered_loss_state"
        else:
            level_before = event.get("level_before")
            if level_before is not None and int(event["levels_completed"]) < int(
                level_before
            ):
                signature = "milestone_dropped"
        if signature is None:
            return
        action_class = str(event["action"])
        tags = load_hazards(view.paths)
        if any(
            tag["action_class"] == action_class and tag["signature"] == signature
            for tag in tags
        ):
            return
        tag = {
            "action_class": action_class,
            "signature": signature,
            "evidence_event": int(event["id"]),
            "origin": "observed",
            "active": True,
        }
        tags.append(tag)
        atomic_json(hazards_path(view.paths), tags)
        append_jsonl(view.paths.activity, {"kind": "hazard_tagged", **tag})

    def telemetry(self, view: JournalView) -> dict[str, Any]:
        tags = load_hazards(view.paths)
        return {"tags": len(tags), "imported": sum(1 for tag in tags if tag.get("origin") == "import")}


BUILTINS = (
    _WallSpend(),
    _MissStreak(),
    _NullForensics(),
    _ParkWithTest(),
    _Sharpness(),
    _Hazard(),
)


def pin_external_modules(paths: RunPaths, registry: Mapping[str, Any] | None) -> None:
    """Copy registered module files into .assay/modules/ at start (pack-tier
    trust: human-installed, never agent-writable mid-run)."""
    if not registry or not registry.get("modules"):
        return
    target = paths.state / "modules"
    target.mkdir(parents=True, exist_ok=True)
    for entry in registry["modules"]:
        source = Path(entry)
        if not source.is_absolute():
            source = paths.root / source
        if not source.exists():
            raise AssayError(f"registered module file not found: {entry}")
        shutil.copy2(source, target / source.name)


def _load_external(paths: RunPaths) -> list[Any]:
    target = paths.state / "modules"
    loaded: list[Any] = []
    if not target.is_dir():
        return loaded
    for file in sorted(target.glob("*.py")):
        with import_path(file, "assay_module") as module:
            candidate = getattr(module, "MODULE", module)
            for attr in ("NAME", "DOCTRINE", "MODE", "trigger", "demand", "telemetry"):
                if not hasattr(candidate, attr):
                    raise AssayError(
                        f"module {file.name} lacks {attr!r}; the module contract is "
                        "NAME, DOCTRINE, MODE, trigger(), demand(), telemetry()"
                    )
            loaded.append(candidate)
    return loaded


def active_modules(
    paths: RunPaths, registry: Mapping[str, Any] | None
) -> list[tuple[Any, str]]:
    """(module, effective_mode) for every non-off module."""
    modes = (registry or {}).get("module_modes") or {}
    output: list[tuple[Any, str]] = []
    for module in (*BUILTINS, *_load_external(paths)):
        mode = str(modes.get(module.NAME, module.MODE))
        if mode not in _MODES:
            mode = module.MODE
        if mode != "off":
            output.append((module, mode))
    return output


def consult_modules(
    paths: RunPaths,
    registry: Mapping[str, Any] | None,
    events: Sequence[Mapping[str, Any]],
    pending: Mapping[str, Any] | None,
) -> list[str]:
    """Run every active module against a pending action. Block-mode unmet
    demands REFUSE (structural, satisfiable via --declare); advise-mode
    triggers return advisory lines."""
    view = JournalView(paths=paths, events=events, registry=registry)
    lines: list[str] = []
    for module, mode in active_modules(paths, registry):
        demands = module.demand(view, pending)
        if demands and mode == "block":
            wanted = ", ".join(f"--declare {field}=..." for field in sorted(demands))
            reasons = "; ".join(f"{field}: {why}" for field, why in sorted(demands.items()))
            raise AssayError(
                f"MODULE {module.NAME} | declaration demanded before this action: "
                f"{wanted} ({reasons}) — the demand is structural; it never bans"
            )
        message = module.trigger(view, pending)
        if message:
            lines.append(f"MODULE {module.NAME} | {message}")
    return lines


def observe_outcome(
    paths: RunPaths,
    registry: Mapping[str, Any] | None,
    events: Sequence[Mapping[str, Any]],
    event: Mapping[str, Any],
) -> None:
    """Let modules learn from a graded outcome (hazard tagging etc.)."""
    view = JournalView(paths=paths, events=events, registry=registry)
    for module, _ in active_modules(paths, registry):
        observe = getattr(module, "observe", None)
        if callable(observe):
            observe(view, event)


def advisory_lines(
    paths: RunPaths,
    registry: Mapping[str, Any] | None,
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Status-time advisories (no pending action) — the old nudge surface."""
    view = JournalView(paths=paths, events=events, registry=registry)
    lines: list[str] = []
    for module, _ in active_modules(paths, registry):
        message = module.trigger(view, None)
        if message:
            lines.append(f"MODULE {module.NAME} | {message}")
    return lines
