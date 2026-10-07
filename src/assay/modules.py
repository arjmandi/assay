"""Behavior modules — declare → enforce → grade units.

A module is doctrine text + a trigger + a demand schema + a mode + telemetry:

    NAME: str                # unique, lowercase
    CONSTITUTION: str            # one paragraph of way-of-thinking text
    MODE: "advise"|"block"   # default; registry module_modes overrides per run
    trigger(view, pending) -> str | None      # advisory message when it fires
    demand(view, pending) -> dict[str,str] | None   # {field: why} — structural
    observe(view, event) -> None              # optional: learn from outcomes
    telemetry(view) -> dict                    # free counters

The shipping ladder is telemetry-first, teeth later: everything ships at
advise until an A/B shows blocking pays (owner law). Demands are always for
checkable structure (named, non-empty fields supplied via --declare or the
destructive-gate flags), never for confidence. Module code is pack-tier trust:
installed by the human at registration (`modules: [path.py]`) or by the owner
mid-run (`assay module install PATH --token TOK`), loaded daemon-side, never
writable by the agent. Installed files are pinned into `.assay/modules/` and
listed in `.assay/modules/manifest.json` with their sha256; a file in that
directory that is not listed, or whose hash no longer matches, is never loaded
and status says so. This is the sanctioned hot-add channel: the owner installs,
the install is journaled, nothing else in the directory counts.

TODO(owner: O6): the review offered the alternative of keeping the directory
glob and having the paper call the channel unguarded. The manifest is the
conservative default implemented here (the run loads only what the registry
or the owner installed); switching back is a one-line change in _load_external.

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
import hashlib
import shutil
import time
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
    CONSTITUTION = (
        "Spending long on one level without progress means manual probing has "
        "stopped paying; model the mechanics offline before spending more."
    )
    MODE = "advise"

    def trigger(self, view: JournalView, pending: Mapping[str, Any] | None) -> str | None:
        level_actions = _level_action_count(view.events)
        if view.registry is not None:
            # Registry runs own the general model tier.
            if level_actions >= 25:
                return (
                    f"{level_actions} paid actions on this level — stop manual "
                    "probing; model the mechanics offline (`assay python`, or the "
                    "`assay model` tier: replay-verified models earn batching rights)"
                )
            return None
        # No registry: the observation kind's own tier, if it has one.
        from .extras import kind_for

        kind = kind_for(view.events[-1]) if view.events else None
        if kind is not None:
            return kind.wall_spend_advice(level_actions)
        if level_actions >= 25:
            return (
                f"{level_actions} paid actions on this level — stop manual "
                "probing; re-read your notes, kill dead assumptions, and model the "
                "mechanics offline with `assay python` before spending more"
            )
        return None

    def demand(self, view: JournalView, pending: Mapping[str, Any] | None) -> None:
        return None

    def telemetry(self, view: JournalView) -> dict[str, Any]:
        return {"level_actions": _level_action_count(view.events)}


class _MissStreak:
    NAME = "miss_streak"
    CONSTITUTION = (
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
    CONSTITUTION = (
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
    CONSTITUTION = (
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
    CONSTITUTION = (
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
    CONSTITUTION = (
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


_CONTRACT = ("NAME", "CONSTITUTION", "MODE", "trigger", "demand", "telemetry")
MANIFEST_NAME = "manifest.json"


def modules_dir(paths: RunPaths) -> Path:
    return paths.state / "modules"


def manifest_path(paths: RunPaths) -> Path:
    return modules_dir(paths) / MANIFEST_NAME


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_object(module: Any, file: Path) -> Any:
    candidate = getattr(module, "MODULE", module)
    for attr in _CONTRACT:
        if not hasattr(candidate, attr):
            raise AssayError(
                f"module {file.name} lacks {attr!r}; the module contract is "
                "NAME, CONSTITUTION, MODE, trigger(), demand(), telemetry()"
            )
    return candidate


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


def install_module(paths: RunPaths, source: Path, token: str | None) -> dict[str, Any]:
    """Owner-authorized mid-run install: copy, check the contract, append to
    the manifest, journal `module_installed`. The sanctioned hot-add channel."""
    from .agenda import require_owner

    require_owner(paths, token)
    if not source.is_file():
        raise AssayError(f"module file not found: {source}")
    if source.suffix != ".py":
        raise AssayError("a module is one Python file (.py)")
    target = modules_dir(paths)
    target.mkdir(parents=True, exist_ok=True)
    entries = [entry for entry in load_manifest(paths) if entry.get("file") != source.name]
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
    append_jsonl(paths.activity, {"kind": "module_installed", **record})
    return record


def _reconstruct_manifest(paths: RunPaths) -> list[dict[str, Any]]:
    """A run started before the manifest existed: its pinned files are exactly
    the registry's `modules` list by basename. Rebuild the manifest from those
    files once, so such a run keeps its modules and anything else in the
    directory is unlisted."""
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


def module_inventory(paths: RunPaths) -> dict[str, Any]:
    """Listed and loadable files, and everything in the directory that is not:
    unlisted files and listed files whose hash changed."""
    target = modules_dir(paths)
    if not target.is_dir():
        return {"listed": [], "ignored": []}
    entries = load_manifest(paths)
    if not entries and not manifest_path(paths).exists():
        entries = _reconstruct_manifest(paths)
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


def _load_external(paths: RunPaths) -> list[Any]:
    loaded: list[Any] = []
    for entry in module_inventory(paths)["listed"]:
        file = entry["path"]
        with import_path(file, "assay_module") as module:
            loaded.append(_module_object(module, file))
    return loaded


def unlisted_lines(paths: RunPaths) -> list[str]:
    ignored = module_inventory(paths)["ignored"]
    if not ignored:
        return []
    return [
        f"MODULES | {len(ignored)} file(s) in .assay/modules ignored (not installed "
        f"through the registry or `assay module install`): {', '.join(ignored)}"
    ]


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
