"""The report of one run: what the run verified, as a Markdown document for
a person and a JSON record for a tool.

    python3 -m tools.eval report --run-dir DIR [--out FILE] [--sessions DIR]
        [--check CMD] [--items N] [--assay CMD | --no-audit]

The document says how the run ended, whether its record is intact, which
predictions held and on which events, what the world reported where they
missed, how the player's own Verified lines stand against the journal, what
the run never exercised, and what its sessions cost. `/assay:report` runs
it and tells the user what it says. It is called report, not export,
because `assay export` already names the knowledge file a later run imports.

Its facts about the world come from the journal alone (`events.jsonl`, the
public contract of verify/JOURNAL_SPEC.md), so anyone holding the run
directory gets the same facts. Its verdict on the record is the kernel's own:
the report runs `assay audit --json` through the launcher, which checks the
chain, the contiguity, the ungated rule and the anchors outside the run
directory, and reads the head the kernel stored in `chain.json`. When the
audit cannot run (`--no-audit`, or a directory that is no run of this
kernel's) it reads the last `audit.json`, and says so. The meters are counted
as `assay status` counts its PREDICTIONS line, and the coverage as the
kernel's coverage audit counts it. Every other source is named in the section
that uses it, because the chain covers none of them:

- the pinned registry (`registry.json`): the goal and the caps;
- the player's notes (`NOTES.md`): its Verified lines, its open questions,
  its stop declarations;
- the declared states (`channels.json`);
- the activity log (`activity.jsonl`): the refusals, the owner's acts, the
  spend reports;
- the session runner's ledger and status (`<run dir>.sessions/`): the
  hosts, the sessions, their bills, the Stop gate's decisions.

The verifier meter applies the identity rule of 1.2.0. A run recorded under
the earlier never-failed rule can show a different count there than its own
status line.

It imports nothing from `assay`; the kernel runs as a subprocess, as the
operator's launcher. Apart from the audit's own `audit.json`, it writes
nothing in the run directory. The
document goes to `<run dir>.report.md` and the record beside it, as
`<run dir>.report.json`, unless `--out` names another file, which may not lie
under the run's `.assay` or `.claude`. `--check CMD` runs an external
checker, such as a pack's `checks/score.py`, with `{run_dir}` in CMD replaced
by the run directory, and records its exit status and output. Exit status 0
is a pass.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import shlex
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from . import EvalError
from .runner import DEFAULT_LAUNCHER
from .session import STATE_SUFFIX, load_gate

REPORT_VERSION = 1
AUDIT_TIMEOUT_SECONDS = 300
STATE_DIRS = (".assay", ".arc")
DOCUMENT_SUFFIX = ".report.md"
RECORD_SUFFIX = ".report.json"
DEFAULT_ITEMS = 25
CHECK_TIMEOUT_SECONDS = 600
CHECK_OUTPUT_LIMIT = 4000
# The coverage audit's thresholds, as the kernel sets them (src/assay/modules.py).
DEAD_MIN_TRIES = 6
STALL_WINDOW = 8
# The identity rule of the verifier meter (verify/PREDICTION_GRAMMAR.md).
VACUOUS_MIN_GRADED = 5
# The outcomes that name no particular result: `change`, and free text
# graded as `change`.
UNSPECIFIC = frozenset({"change", "coerced"})
BUILT_IN_STATES = frozenset({"goal", "level"})
# The declaration keys, and the words in a `conclusion=` value, that mark an
# impossibility or absence conclusion, as the kernel's coverage audit reads
# them (src/assay/modules.py).
CONCLUSIONS = frozenset({"impossible", "unsolvable", "unwinnable", "absent", "missing", "dead_end", "give_up"})
CITATION = re.compile(r"\be(\d+)\b")
NOT_VERIFIED = re.compile(r"\b(?:un|not )verified")
# The characters that make links, images, HTML, code spans, emphasis and
# table cells in Markdown, escaped in the player's prose.
ACTIVE = re.compile(r"([\\`\[\]<>|*_])")
FORM_NAMES = {"level_up": "level+1", "coerced": "free text, graded as change"}


# --- loading ---------------------------------------------------------------------


def find_state(run_dir: Path) -> Path:
    """The run's state directory, `.assay` or its historical alias `.arc`."""
    for name in STATE_DIRS:
        candidate = run_dir / name
        if (candidate / "events.jsonl").is_file():
            return candidate
    raise EvalError(f"no journal in {run_dir}: expected .assay/events.jsonl")


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _instant(stamp: Any) -> dt.datetime | None:
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        return dt.datetime.fromtimestamp(float(stamp), dt.timezone.utc)
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def _when(stamp: Any) -> str:
    moment = _instant(stamp)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC") if moment else "an unrecorded time"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _whole(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


# --- the journal -------------------------------------------------------------------


def grades(event: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = event.get("grade")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _invalid(grade: Mapping[str, Any]) -> bool:
    return bool(grade.get("invalid") or grade.get("ungradable"))


def event_id(event: Mapping[str, Any], index: int) -> int:
    """The event's id, or its position when the id is not a number (the
    audit reports that as a contiguity problem)."""
    value = event.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else index


def action_text(event: Mapping[str, Any], limit: int = 24) -> str:
    """The action as the kernel's lines show it: the name, then each
    parameter as key=value, long values cut."""
    name = str(event.get("action"))
    data = event.get("data")
    if not isinstance(data, Mapping) or not data:
        return name
    parts = []
    for key, value in data.items():
        shown = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        if len(shown) > limit:
            shown = shown[: limit - 3] + "..."
        parts.append(f"{key}={shown}")
    return name + " " + " ".join(parts)


def form_of(grade: Mapping[str, Any]) -> str:
    """An outcome's form, its text with the value left out: what the run
    tested about an action, whatever value each prediction named."""
    kind = str(grade.get("kind") or "")
    state = grade.get("channel")
    if kind == "channel_eq":
        return f"ch {state} = V"
    if kind == "channel_delta":
        sign = grade.get("sign")
        return f"ch {state} delta sign {sign}" if sign else f"ch {state} delta {grade.get('op') or '='} V"
    if kind == "channel_cross":
        return f"ch {state} crosses V"
    if kind == "verify":
        return "verify:" + str(grade.get("path") or str(grade.get("verifier_hash") or "")[:12])
    if kind == "aggregate":
        return f"aggregate {grade.get('stat')}(ch {state})"
    return FORM_NAMES.get(kind, kind or "unknown")


def vacuous_verifiers(events: Sequence[Mapping[str, Any]]) -> set[str]:
    """The verifiers whose identity verdict, on the unchanged observation,
    matched the real verdict on every one of five or more gradings."""
    counts: dict[str, list[int]] = {}
    for event in events:
        for grade in grades(event):
            if not grade.get("verifier") or _invalid(grade):
                continue
            slot = counts.setdefault(str(grade.get("verifier_hash") or ""), [0, 0])
            slot[0] += 1
            identity = grade.get("identity_verdict")
            if isinstance(identity, bool) and identity == bool(grade.get("ok")):
                slot[1] += 1
    return {digest for digest, (graded, same) in counts.items() if graded >= VACUOUS_MIN_GRADED and same == graded}


def meter_class(grade: Mapping[str, Any], vacuous: set[str]) -> str:
    """How `assay status` counts one grade: a note, an invalid outcome, free
    text, the kernel's own prediction, a vacuous verifier's pass, or its
    bucket (`world_model`, `gamble`, or another)."""
    kind = grade.get("kind")
    if kind == "note":
        return "note"
    if _invalid(grade):
        return "invalid"
    if kind == "coerced":
        return "coerced"
    if grade.get("machine"):
        return "machine"
    if grade.get("verifier") and grade.get("ok") and (
        grade.get("excluded_from_meter") or str(grade.get("verifier_hash") or "") in vacuous
    ):
        return "vacuous"
    return str(grade.get("bucket") or ("gamble" if kind in ("win", "level_up") else "world_model"))


def meter(events: Sequence[Mapping[str, Any]], vacuous: set[str]) -> dict[str, Any]:
    """The PREDICTIONS line's counts, over every grade of the run."""
    buckets: dict[str, list[int]] = {"world_model": [0, 0], "gamble": [0, 0]}
    other = [0, 0]
    graded = coerced = invalid = machine = vacuous_passes = 0
    for event in events:
        for grade in grades(event):
            kind = meter_class(grade, vacuous)
            if kind == "note":
                continue
            graded += 1
            if kind == "invalid":
                invalid += 1
            elif kind == "coerced":
                coerced += 1
            elif kind == "machine":
                machine += 1
            elif kind == "vacuous":
                vacuous_passes += 1
            else:
                slot = buckets.get(kind, other)
                slot[0] += 1
                slot[1] += 0 if grade.get("ok") else 1
    return {
        "world_model": {"graded": buckets["world_model"][0], "missed": buckets["world_model"][1]},
        "gamble": {"graded": buckets["gamble"][0], "missed": buckets["gamble"][1]},
        "other": {"graded": other[0], "missed": other[1]},
        "graded": graded,
        "specific": graded - coerced,
        "coerced": coerced,
        "invalid": invalid,
        "machine": machine,
        "vacuous_passes": vacuous_passes,
        "vacuous_verifiers": sorted(vacuous),
    }


def meter_line(counts: Mapping[str, Any]) -> str | None:
    """The PREDICTIONS line, as `assay status` prints it."""
    if not counts["graded"]:
        return None

    def rate(slot: Mapping[str, int]) -> str:
        if not slot["graded"]:
            return "0/0"
        return f"{slot['missed']}/{slot['graded']} ({100 * slot['missed'] / slot['graded']:.1f}%)"

    return (
        f"PREDICTIONS | world-model misses {rate(counts['world_model'])} | "
        f"gamble misses {rate(counts['gamble'])} | "
        f"specificity {counts['specific']}/{counts['graded']} ({100 * counts['specific'] / counts['graded']:.0f}%) | "
        f"invalid {counts['invalid']}"
    )


def changed(events: Sequence[Mapping[str, Any]], index: int) -> bool:
    """The coverage audit's signal: did paid event `index` change the world?
    Progress always counts; then the graded `change` or `noop` outcome; else
    the settled frame, or the observation, against the previous event's."""
    event = events[index]
    before = event.get("level_before")
    if isinstance(before, int) and int(event.get("levels_completed") or 0) > before:
        return True
    for grade in grades(event):
        if grade.get("kind") == "change":
            return bool(grade.get("ok"))
        if grade.get("kind") == "noop":
            return not grade.get("ok")
    if index == 0:
        return False
    previous = events[index - 1]
    frames, prior = event.get("frames"), previous.get("frames")
    if isinstance(frames, list) and isinstance(prior, list):
        return (frames[-1] if frames else None) != (prior[-1] if prior else None)
    return event.get("observation") != previous.get("observation")


def advertised(event: Mapping[str, Any]) -> list[str]:
    """The action names an event advertises, as the registry spells them: a
    frame world's bare ids carry the ACTION prefix."""
    values = event.get("available_actions")
    if not isinstance(values, list):
        return []
    names = []
    for value in values:
        if event.get("frames") is not None:
            try:
                names.append(f"ACTION{int(value)}")
                continue
            except (TypeError, ValueError):
                pass
        names.append(str(value).upper())
    return names


def actions_view(events: Sequence[Mapping[str, Any]], vacuous: set[str]) -> dict[str, dict[str, Any]]:
    """Per action: its paid uses, how many changed the world, and per outcome
    form the events where it held, missed or could not be graded, with the
    first event of each distinct text."""
    view: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        if not event.get("counts_action"):
            continue
        number = event_id(event, index)
        slot = view.setdefault(str(event.get("action")), {"paid": 0, "changed": 0, "forms": {}})
        slot["paid"] += 1
        slot["changed"] += 1 if changed(events, index) else 0
        for grade in grades(event):
            kind = meter_class(grade, vacuous)
            if kind == "note":
                continue
            entry = slot["forms"].setdefault(
                form_of(grade), {"held": [], "missed": [], "invalid": [], "texts": {}, "counted_as": []}
            )
            if kind == "invalid":
                entry["invalid"].append(number)
            elif grade.get("ok"):
                entry["held"].append(number)
            else:
                entry["missed"].append(number)
            entry["texts"].setdefault(str(grade.get("text") or ""), number)
            if kind not in entry["counted_as"]:
                entry["counted_as"].append(kind)
    return view


def misses(events: Sequence[Mapping[str, Any]], vacuous: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every outcome that missed, with what the world reported instead, and
    every outcome that could not be graded, with the reason."""
    missed: list[dict[str, Any]] = []
    ungraded: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        for grade in grades(event):
            kind = meter_class(grade, vacuous)
            if kind == "note" or (grade.get("ok") and kind != "invalid"):
                continue
            item = {
                "event": event_id(event, index),
                "action": action_text(event),
                "outcome": str(grade.get("text") or ""),
                "actual": str(grade.get("actual") or ""),
                "counted_as": kind,
            }
            (ungraded if kind == "invalid" else missed).append(item)
    return missed, ungraded


def coverage(events: Sequence[Mapping[str, Any]], vacuous: set[str], declared: Iterable[str] | None) -> dict[str, Any]:
    """What the run never exercised, over the whole run and, as the kernel's
    coverage audit reads it, on the progress unit where the run stands."""
    plays: dict[str, list[int]] = {}
    specific: set[str] = set()
    named: set[str] = set()
    for index, event in enumerate(events):
        for grade in grades(event):
            if grade.get("channel") is not None and not _invalid(grade):
                named.add(str(grade["channel"]))
        if not event.get("counts_action"):
            continue
        name = str(event.get("action")).upper()
        slot = plays.setdefault(name, [0, 0])
        slot[0] += 1
        slot[1] += 1 if changed(events, index) else 0
        for grade in grades(event):
            if grade.get("kind") not in UNSPECIFIC | {"note"} and meter_class(grade, vacuous) != "invalid":
                specific.add(name)
    offered = sorted({name for event in events for name in advertised(event)})
    taken = sorted(name for name in plays if name != "RESET")
    whole = {
        "advertised": offered,
        "untried": [name for name in offered if name not in plays],
        "dead": [name for name in taken if plays[name][0] >= DEAD_MIN_TRIES and plays[name][1] == 0],
        "unspecific": [name for name in taken if name not in specific],
        "plays": {name: {"paid": count, "changed": productive} for name, (count, productive) in sorted(plays.items())},
    }
    # The progress unit where the run stands: the tail of the journal with
    # the last event's progress, as the kernel's coverage ledger takes it.
    last = events[-1]
    completed = last.get("levels_completed")
    tail: list[int] = []
    for index in range(len(events) - 1, -1, -1):
        if events[index].get("levels_completed") != completed:
            break
        tail.append(index)
    paid = [index for index in reversed(tail) if events[index].get("counts_action")]
    unit_plays: dict[str, list[int]] = {}
    for index in paid:
        slot = unit_plays.setdefault(str(events[index].get("action")).upper(), [0, 0])
        slot[0] += 1
        slot[1] += 1 if changed(events, index) else 0
    names = sorted(set(advertised(last)))
    recent = paid[-STALL_WINDOW:]
    unit = {
        "unit": int(completed or 0),
        "paid": len(paid),
        "advertised": names,
        "untried": [name for name in names if unit_plays.get(name, [0, 0])[0] == 0],
        "dead": [
            name for name in names
            if unit_plays.get(name, [0, 0])[0] >= DEAD_MIN_TRIES and unit_plays[name][1] == 0
        ],
        "stalled": len(recent) == STALL_WINDOW
        and str(last.get("state")) == "NOT_FINISHED"
        and not any(changed(events, index) for index in recent),
    }
    states = None
    if declared is not None:
        states = [name for name in sorted(set(declared)) if name not in named and name not in BUILT_IN_STATES]
    return {"run": whole, "unit": unit, "states_never_graded": states}


def declarations_view(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the player declared with its actions (`--declare`, journaled on
    the event): every conclusion of impossibility or absence in full, with
    the coverage audit it gave, and how often each other key was declared."""
    conclusions: list[dict[str, Any]] = []
    keys: dict[str, int] = {}
    for index, event in enumerate(events):
        declares = event.get("declares")
        if not isinstance(declares, Mapping) or not declares:
            continue
        lowered = {str(key).lower(): str(value) for key, value in declares.items()}
        concluded = sorted(set(lowered) & CONCLUSIONS) or [
            key for key in ("conclusion",) if any(word in lowered.get(key, "").lower() for word in CONCLUSIONS)
        ]
        if concluded:
            conclusions.append({
                "event": event_id(event, index),
                "action": action_text(event),
                "conclusion": {key: lowered[key] for key in concluded},
                "coverage_audit": lowered.get("coverage_audit"),
            })
        for key in lowered:
            keys[key] = keys.get(key, 0) + 1
    return {"conclusions": conclusions, "keys": dict(sorted(keys.items()))}


# --- the notes ---------------------------------------------------------------------


def sections(notes: str) -> dict[str, list[str]]:
    """The notes' lines under their headings, by kind: `verified` for a
    heading that says verified (not unverified), `open` for one that says
    assumed or open."""
    found: dict[str, list[str]] = {"verified": [], "open": [], "headings": []}
    current: str | None = None
    for line in notes.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            if "verified" in heading and not NOT_VERIFIED.search(heading):
                current = "verified"
                found["headings"].append(stripped)
            elif "assumed" in heading or "open" in heading:
                current = "open"
            else:
                current = None
            continue
        # A stop declaration is not a claim about the world, wherever it sits.
        if current is not None and stripped and not stripped.startswith("STOP:"):
            found[current].append(re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", stripped))
    return found


# How a Verified line stands, each with its singular and plural verb.
VERDICTS = {
    "held": ("cites only held outcomes", "cite only held outcomes"),
    "missed": ("cites a missed outcome", "cite a missed outcome"),
    "beyond": ("cites an event the journal does not hold", "cite an event the journal does not hold"),
    "ungraded": ("cites no graded outcome", "cite no graded outcome"),
    "partial": ("cites held outcomes and events not graded in full", "cite held outcomes and events not graded in full"),
    "none": ("cites no event", "cite no event"),
}


def citation(event: Mapping[str, Any] | None) -> dict[str, Any]:
    """How one cited event graded: its held, missed and ungraded outcomes."""
    if event is None:
        return {"status": "not in the journal"}
    if not event.get("counts_action"):
        return {"status": "not a paid action"}
    items = [grade for grade in grades(event) if grade.get("kind") != "note"]
    held = sum(1 for grade in items if grade.get("ok") and not _invalid(grade))
    missed = sum(1 for grade in items if not grade.get("ok") and not _invalid(grade))
    ungraded = sum(1 for grade in items if _invalid(grade))
    if not items:
        status = "ungraded"
    elif missed:
        status = "missed" if not held else f"{held} held, {missed} missed"
    elif ungraded:
        status = "not graded in full"
    else:
        status = "held"
    return {"status": status, "held": held, "missed": missed, "ungraded": ungraded}


def check_verified(lines: Sequence[str], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each Verified line with the events it cites and how each one graded."""
    by_id = {event_id(event, index): event for index, event in enumerate(events)}
    checked = []
    for line in lines:
        cited: list[int] = []
        for match in CITATION.finditer(line):
            value = int(match.group(1))
            if value not in cited:
                cited.append(value)
        cites = [{"event": value, **citation(by_id.get(value))} for value in cited]
        if not cites:
            verdict = "none"
        elif any(item["status"] == "not in the journal" for item in cites):
            verdict = "beyond"
        elif any(item.get("missed") for item in cites):
            verdict = "missed"
        elif all(item["status"] == "held" for item in cites):
            verdict = "held"
        elif any(item.get("held") for item in cites):
            verdict = "partial"
        else:
            verdict = "ungraded"
        checked.append({"line": line, "cites": cites, "verdict": verdict, "says": VERDICTS[verdict][0]})
    return checked


# --- the operator's records ------------------------------------------------------------


def activity_view(activity: Sequence[Mapping[str, Any]], gate: ModuleType) -> dict[str, Any]:
    """The refusals by code, the owner's acts, and the spend reports."""
    refusals: dict[str, dict[str, Any]] = {}
    owner: list[dict[str, Any]] = []
    for record in activity:
        kind = record.get("kind")
        code = record.get("code")
        if kind == "command_end" and isinstance(code, str) and code:
            slot = refusals.setdefault(code, {"count": 0, "first": str(record.get("error") or "")})
            slot["count"] += 1
        elif kind in ("approval_granted", "approval_used", "liveness_waived", "goal_ratified", "owner_refused"):
            owner.append({key: record.get(key) for key in ("kind", "timestamp", "action", "because", "id", "text", "operation")})
    latest: dict[str, Mapping[str, Any]] = {}
    for record in activity:
        if record.get("kind") == "spend_report" and record.get("id"):
            latest[str(record["id"])] = record
    tokens = sum(_whole(record.get("tokens")) or 0 for record in latest.values())
    return {
        "refusals": dict(sorted(refusals.items())),
        "owner": owner,
        "spend": {"usd": gate.reported_spend(activity), "tokens": tokens, "reports": len(latest)},
    }


def sessions_view(state: Path) -> dict[str, Any] | None:
    """The session runner's ledger and status, or None without a ledger."""
    ledger = load_gate().read_json(state / "sessions.json")
    if not isinstance(ledger, list):
        return None
    status = load_gate().read_json(state / "status.json")
    # Each session's record carries the gate's last decisions, the earlier
    # sessions' among them; each decision is counted once, in the session
    # whose wall clock holds it.
    decisions: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for record in ledger:
        listed = record.get("gate") if isinstance(record, Mapping) else None
        for decision in listed if isinstance(listed, list) else []:
            if isinstance(decision, Mapping):
                key = json.dumps(decision, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    decisions.append(decision)
    rows: list[dict[str, Any]] = []
    for record in ledger:
        if not isinstance(record, Mapping):
            continue
        start, end = _instant(record.get("started")), _instant(record.get("ended"))
        inside = [
            decision for decision in decisions
            if start is not None and end is not None
            and (moment := _instant(decision.get("at"))) is not None and start <= moment <= end
        ]
        launch = _mapping(record.get("launch"))
        tokens = _mapping(record.get("tokens"))
        rows.append({
            "session": record.get("session"),
            "host": record.get("host"),
            "host_version": record.get("host_version"),
            "model": record.get("model"),
            "started": record.get("started"),
            "wall_seconds": _number(record.get("wall_seconds")) or 0.0,
            "paid_from": launch.get("paid"),
            "paid_to": record.get("paid_at_exit"),
            "dollars": _number(record.get("dollars")),
            "tokens": tokens.get("total"),
            "ended_because": record.get("ended_because"),
            "blocks": sum(1 for decision in inside if decision.get("allow") is False),
            "rate_limited": bool(record.get("rate_limited")),
        })
    dollars = [row["dollars"] for row in rows if row["dollars"] is not None]
    tokens_seen = [int(row["tokens"]) for row in rows if isinstance(row["tokens"], int)]
    return {
        "ledger": str(state / "sessions.json"),
        "rows": rows,
        "totals": {
            "sessions": len(rows),
            "wall_seconds": round(sum(row["wall_seconds"] for row in rows), 1),
            "dollars": round(sum(dollars), 6) if dollars else None,
            "tokens": sum(tokens_seen) if tokens_seen else None,
            "blocks": sum(row["blocks"] for row in rows),
        },
        "status": status if isinstance(status, Mapping) else None,
    }


def kernel_audit(
    run_dir: Path, state: Path, events: Sequence[Mapping[str, Any]], launcher: list[str] | None,
) -> dict[str, Any] | None:
    """The kernel's audit of the run: `assay audit --json` through the
    launcher, else the last `audit.json` it wrote, with whether that covers
    the whole journal (computed after its last event, over as many events);
    None when there is neither."""
    audit: Any = None
    source = None
    if launcher:
        try:
            done = subprocess.run([*launcher, "--run-dir", str(run_dir), "audit", "--json"], capture_output=True,
                                  text=True, timeout=AUDIT_TIMEOUT_SECONDS, check=False)
            audit = json.loads(done.stdout) if done.returncode == 0 else None
            source = "assay audit, run for this report"
        except (OSError, subprocess.TimeoutExpired, ValueError):
            audit = None
    if not isinstance(audit, Mapping):
        audit = load_gate().read_json(state / "audit.json")
        source = "the last .assay/audit.json"
    if not isinstance(audit, Mapping):
        return None
    computed = _instant(audit.get("computed_at"))
    last = _instant(events[-1].get("timestamp"))
    return {
        "source": source,
        "computed_at": computed.isoformat() if computed else None,
        "current": bool(computed and last and computed >= last) and audit.get("events") == len(events),
        "verdict": "INVALID FOR SCORING" if audit.get("invalid_for_scoring") else "CLEAN",
        "events": audit.get("events"),
        "paid": audit.get("paid"),
        "contiguous": audit.get("contiguous"),
        "ungated": list(audit.get("ungated") or []),
        "chain": audit.get("chain"),
        "anchors": audit.get("anchors"),
        "anchor_count": audit.get("anchor_count"),
        "problems": list(audit.get("problems") or []),
    }


def integrity(
    run_dir: Path, state: Path, events: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
    launcher: list[str] | None,
) -> dict[str, Any]:
    """The kernel's verdict on the record, the head it stored, the spec."""
    chain = _mapping(load_gate().read_json(state / "chain.json"))
    audit = kernel_audit(run_dir, state, events, launcher)
    return {
        "verdict": audit["verdict"] if audit else "NOT AUDITED",
        "audit": audit,
        "head": chain.get("head") if isinstance(chain.get("head"), str) else None,
        "head_event": chain.get("event_id"),
        "spec": str(config.get("journal_spec") or "assay-journal-v1"),
    }


def run_check(command: str, run_dir: Path) -> dict[str, Any]:
    """An external checker's verdict: its exit status (0 is a pass) and its
    output, as a JSON object when it prints one."""
    argv = [word.replace("{run_dir}", str(run_dir)) for word in shlex.split(command)]
    if not argv:
        raise EvalError("--check names no command")
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=CHECK_TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise EvalError(f"the check `{argv[0]}` is not on PATH") from None
    except subprocess.TimeoutExpired:
        return {"command": argv, "exit_code": None, "passed": False, "result": None,
                "output": f"timed out after {CHECK_TIMEOUT_SECONDS} seconds"}
    result = None
    text = done.stdout.strip()
    if text.startswith("{"):
        try:
            value = json.loads(text)
        except ValueError:
            value = None
        result = value if isinstance(value, dict) else None
    return {
        "command": argv,
        "exit_code": done.returncode,
        "passed": done.returncode == 0,
        "result": result,
        "output": None if result is not None else (done.stdout + done.stderr)[-CHECK_OUTPUT_LIMIT:],
    }


# --- the record -----------------------------------------------------------------------


def ending(
    events: Sequence[Mapping[str, Any]],
    view: Any,
    gate: ModuleType,
    activity: Sequence[Mapping[str, Any]],
    said: Sequence[str],
    sessions: Mapping[str, Any] | None,
    ledger_last: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """How the run stands, by the gate's rules: WIN, a cap, a declared stop,
    an owner action, or unfinished. The runner's ledger, when there is one,
    says why the last session ended; without it the notes and the activity
    log stand in."""
    last = events[-1]
    finished = gate.finished(view)
    at = None
    if finished == "WIN":
        at = next((event_id(event, index) for index, event in enumerate(events) if event.get("state") == "WIN"), None)
    declaration = None
    pending: list[Any] = []
    if ledger_last is not None:
        declaration = ledger_last.get("declaration")
        pending = list(ledger_last.get("owner_pending") or [])
        because = ledger_last.get("ended_because")
    else:
        declaration = said[-1] if said else None
        pending = gate.pending_owner_actions(activity)
        because = None
    if finished is not None:
        outcome = finished
        why = gate.FINISHED[finished]
    elif because == "SELF_STOP" or (ledger_last is None and declaration):
        outcome, why = "SELF_STOP", f"the player declared a stop: {declaration}"
    elif because == "OWNER_NEEDED" or (ledger_last is None and pending):
        outcome = "OWNER_NEEDED"
        why = "the owner must act: " + "; ".join(gate.owner_text(item) for item in pending)
    else:
        outcome, why = "UNFINISHED", "the run is not over: no WIN, no cap, no declared stop, no owner action pending"
    status = sessions.get("status") if sessions else None
    return {
        "outcome": outcome,
        "why": why,
        "state": str(last.get("state", "")),
        "progress": [int(last.get("levels_completed") or 0), int(last.get("win_levels") or 0)],
        "paid": view.paid,
        "cap": view.cap,
        "usd": view.usd,
        "usd_cap": view.usd_cap,
        "win_at": at,
        "declaration": declaration if outcome == "SELF_STOP" else None,
        "declarations": list(said),
        "owner_pending": pending,
        "runner": {key: status.get(key) for key in ("state", "outcome", "detail", "updated")} if status else None,
    }


def build(
    run_dir: Path, *, sessions_dir: Path | None = None, check: str | None = None, launcher: list[str] | None = None,
) -> dict[str, Any]:
    """The report's record of one run directory. `launcher` runs the
    kernel's audit; without one the report reads the last audit file."""
    run_dir = run_dir.resolve()
    state = find_state(run_dir)
    gate = load_gate()
    events = gate.read_jsonl(state / "events.jsonl")
    if not events:
        raise EvalError(f"the journal in {state} holds no events")
    registry = _mapping(gate.read_json(state / "registry.json"))
    budget = _mapping(registry.get("budget"))
    goal = registry.get("goal")
    config = _mapping(gate.read_json(state / "config.json"))
    activity = gate.read_jsonl(state / "activity.jsonl")
    notes = _text(state / "NOTES.md")
    states = gate.read_json(state / "channels.json")
    view = gate.RunView(
        state=str(events[-1].get("state", "")),
        paid=sum(1 for event in events if event.get("counts_action")),
        cap=_whole(budget.get("actions")),
        usd=gate.reported_spend(activity),
        usd_cap=_number(budget.get("usd")),
        activity=tuple(activity),
        notes=notes or "",
    )
    vacuous = vacuous_verifiers(events)
    counts = meter(events, vacuous)
    missed, ungraded = misses(events, vacuous)
    found = sections(notes or "")
    said = gate.declarations(notes or "")
    ledger_dir = sessions_dir or run_dir.with_name(run_dir.name + STATE_SUFFIX)
    sessions = sessions_view(ledger_dir)
    raw = gate.read_json(ledger_dir / "sessions.json") if sessions else None
    last_session = raw[-1] if isinstance(raw, list) and raw and isinstance(raw[-1], Mapping) else None
    return {
        "report_version": REPORT_VERSION,
        "written": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run": {
            "dir": str(run_dir),
            "name": run_dir.name,
            "state_dir": state.name,
            "world": config.get("game_id"),
            "harness_version": config.get("harness_version"),
            "journal_spec": config.get("journal_spec"),
            "created": config.get("created_at"),
            "goal": goal.get("text") if isinstance(goal, Mapping) else goal,
            "first_event": events[0].get("timestamp"),
            "last_event": events[-1].get("timestamp"),
            "events": len(events),
        },
        "ending": ending(events, view, gate, activity, said, sessions, last_session),
        "integrity": integrity(run_dir, state, events, config, launcher),
        "predictions": {
            "meter": counts,
            "line": meter_line(counts),
            "actions": actions_view(events, vacuous),
            "missed": missed,
            "ungraded": ungraded,
        },
        "notes": None if notes is None else {
            "has_verified": bool(found["headings"]),
            "verified": check_verified(found["verified"], events),
            "open": found["open"],
            "declarations": said,
        },
        "coverage": coverage(events, vacuous, states.keys() if isinstance(states, Mapping) else None),
        "declarations": declarations_view(events),
        "activity": activity_view(activity, gate),
        "sessions": sessions,
        "check": run_check(check, run_dir) if check else None,
    }


# --- the document -------------------------------------------------------------------


def _code(text: Any, limit: int = 160) -> str:
    """Inline code, which renders nothing inside it."""
    value = " ".join(str(text).split())
    if len(value) > limit:
        value = value[: limit - 3] + "..."
    if not value:
        return "(empty)"
    fence = "`"
    while fence in value:
        fence += "`"
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _fenced(text: str, language: str = "") -> list[str]:
    """A code block whose fence no line of the text can close."""
    fence = "```"
    while fence in text:
        fence += "`"
    return [fence + language, text.rstrip("\n"), fence]


def _prose(text: Any, limit: int = 300) -> str:
    """The player's prose, one line, with the characters escaped that would
    make links, images, HTML or code of it."""
    value = " ".join(str(text).split())
    if len(value) > limit:
        value = value[: limit - 3] + "..."
    return ACTIVE.sub(r"\\\1", value)


def ids(values: Iterable[int], limit: int) -> str:
    """Event ids, consecutive ones run together: e1, e2, e5-e9."""
    runs: list[list[int]] = []
    for value in sorted(set(values)):
        if runs and value == runs[-1][1] + 1:
            runs[-1][1] = value
        else:
            runs.append([value, value])
    parts: list[str] = []
    for low, high in runs:
        if high - low >= 2:
            parts.append(f"e{low}-e{high}")
        else:
            parts.extend(f"e{value}" for value in range(low, high + 1))
    if len(parts) > limit:
        parts = parts[:limit] + [f"and {len(parts) - limit} more"]
    return ", ".join(parts)


def _names(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "none"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _money(value: float | None) -> str:
    return "not reported" if value is None else f"${value:.2f}"


def _anchors(audit: Mapping[str, Any]) -> str:
    if audit["anchors"] == "none":
        return "no head anchored outside the run directory yet"
    return f"anchors {audit['anchors']} ({audit['anchor_count']})"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _held_missed(counts: Mapping[str, Any]) -> tuple[int, int]:
    held = missed = 0
    for bucket in ("world_model", "gamble", "other"):
        held += counts[bucket]["graded"] - counts[bucket]["missed"]
        missed += counts[bucket]["missed"]
    return held, missed


def summary_lines(record: Mapping[str, Any]) -> list[str]:
    """The short form, for the command line."""
    end = record["ending"]
    held, missed = _held_missed(record["predictions"]["meter"])
    cap = end["cap"] if end["cap"] is not None else "no cap"
    spend = record["activity"]["spend"]
    return [
        f"REPORT | {end['outcome']} | progress {end['progress'][0]}/{end['progress'][1]} | paid actions {end['paid']}/{cap} | "
        f"{record['integrity']['verdict']} | held {held}, missed {missed} | "
        + (f"{_money(spend['usd'])} reported to the run" if spend["reports"] else "no spend reported"),
    ]


def _cap_text(end: Mapping[str, Any]) -> str:
    return f"of the cap of {end['cap']}" if end["cap"] is not None else "(no action cap is registered)"


def _head(record: Mapping[str, Any], items: int) -> list[str]:
    run = record["run"]
    out = [f"# ASSAY report: {run['world'] or run['name']}", ""]
    identity = [f"Run `{run['name']}` ({run['dir']})"]
    if run["harness_version"]:
        identity.append(f"harness {run['harness_version']}")
    identity.append(f"journal spec {record['integrity']['spec']}")
    out.append(", ".join(identity) + ".")
    if run["goal"]:
        out.append(f"The goal, from the pinned registry: {_prose(run['goal'])}.")
    out.append(
        f"Recorded from {_when(run['first_event'])} to {_when(run['last_event'])}. "
        f"Report written {_when(record['written'])}."
    )
    return out


def _summary(record: Mapping[str, Any], items: int) -> list[str]:
    end = record["ending"]
    verdict = record["integrity"]
    audit = verdict["audit"]
    counts = record["predictions"]["meter"]
    cover = record["coverage"]
    check = record["check"]
    sessions = record["sessions"]
    spend = record["activity"]["spend"]
    held, missed = _held_missed(counts)
    out = ["", "## Summary", ""]
    where = end["outcome"]
    if end["outcome"] == "WIN" and end["win_at"] is not None:
        where = f"WIN at e{end['win_at']}"
    elif end["outcome"] == "SELF_STOP":
        where = f"SELF_STOP, declared: {_prose(end['declaration'], 120).rstrip('.')}"
    out.append(
        f"- **How it ended:** {where}. Progress {end['progress'][0]}/{end['progress'][1]}, "
        f"{_plural(end['paid'], 'paid action')} {_cap_text(end)}."
    )
    if audit is None:
        out.append("- **Integrity:** not audited: `assay audit` could not run here and no audit file is there.")
    else:
        head = f"; chain head `{verdict['head'][:16]}`" if verdict["head"] else ""
        stale = ", from an audit older than the journal" if not audit["current"] else ""
        out.append(f"- **Integrity:** {audit['verdict']} by the kernel's audit{head}; {_anchors(audit)}{stale}.")
    left_out = counts["coerced"] + counts["machine"] + counts["vacuous_passes"]
    out.append(
        f"- **Predictions:** {_plural(held, 'outcome')} held and {missed} missed on {_plural(end['paid'], 'paid action')}, "
        "counted as `assay status` counts them"
        + (f"; {left_out} more left out of that count" if left_out else "")
        + (f"; {counts['invalid']} could not be graded" if counts["invalid"] else "")
        + "."
    )
    gaps = []
    if cover["run"]["untried"]:
        gaps.append(f"never taken: {_names(cover['run']['untried'])}")
    if cover["run"]["dead"]:
        gaps.append(f"taken {DEAD_MIN_TRIES} times or more and never productive: {_names(cover['run']['dead'])}")
    if cover["run"]["unspecific"]:
        gaps.append(f"never given a specific outcome: {_names(cover['run']['unspecific'])}")
    if cover["states_never_graded"]:
        gaps.append(f"declared states never graded: {_names(cover['states_never_graded'])}")
    out.append(
        "- **Never exercised:** "
        + ("; ".join(gaps) + "." if gaps else "nothing the coverage audit looks for: every advertised action was taken "
           "and given a specific outcome.")
    )
    if check is not None:
        out.append(f"- **The external check:** {'passed' if check['passed'] else 'failed'} (exit status {check['exit_code']}).")
    if sessions is not None:
        totals = sessions["totals"]
        out.append(
            f"- **Cost:** {_plural(totals['sessions'], 'session')}, {totals['wall_seconds'] / 60:.1f} min, "
            f"{_money(totals['dollars'])} reported by the host"
            + (f", {totals['tokens']:,} tokens" if totals["tokens"] is not None else "")
            + "."
        )
    elif spend["reports"]:
        out.append(f"- **Cost:** {_money(spend['usd'])} reported to the run, {spend['tokens']:,} tokens.")
    else:
        out.append("- **Cost:** nothing was reported to the run.")
    return out


def _how_it_ended(record: Mapping[str, Any], items: int) -> list[str]:
    end = record["ending"]
    check = record["check"]
    out = ["", "## How it ended", ""]
    at = f" at e{end['win_at']}" if end["win_at"] is not None else ""
    out.append(f"{end['outcome']}{at}. {_sentence(_prose(end['why']))}.")
    out.append("")
    out.append(
        f"The journal's last event is e{record['run']['events'] - 1}, in state {end['state']}, with progress "
        f"{end['progress'][0]}/{end['progress'][1]} and {_plural(end['paid'], 'paid action')} {_cap_text(end)}."
    )
    if end["usd_cap"] is not None:
        out.append(f"The reported spend is {_money(end['usd'])} of the dollar cap of {_money(end['usd_cap'])}.")
    if end["declarations"] and not (end["outcome"] == "SELF_STOP" and len(end["declarations"]) == 1):
        out += ["", "The player's stop declarations, from its notes (the chain does not cover the notes):", ""]
        out += [f"- STOP: {_prose(line)}" for line in end["declarations"][-items:]]
    if end["outcome"] == "SELF_STOP":
        out += ["", _against_the_gaps(record["coverage"]["unit"])]
    if end["owner_pending"]:
        out += ["", "Pending for the owner:", ""]
        out += [f"- {_prose(item.get('error') or item.get('code'))}" for item in end["owner_pending"]]
    if end["runner"]:
        runner = end["runner"]
        out += ["", f"The session runner's last word: {runner.get('state')}, outcome {runner.get('outcome')}"
                + (f" ({_prose(runner.get('detail'))})" if runner.get("detail") else "") + "."]
    if check is not None:
        out += ["", "### The external check", ""]
        verdict = "passed" if check["passed"] else "failed"
        status = check["exit_code"] if check["exit_code"] is not None else "none"
        out.append(f"{_code(shlex.join(check['command']), 400)} {verdict} (exit status {status}).")
        if check["result"] is not None:
            out += ["", *_fenced(json.dumps(check["result"], indent=2, sort_keys=True)[:CHECK_OUTPUT_LIMIT], "json")]
        elif check["output"]:
            out += ["", *_fenced(check["output"])]
    return out


def _against_the_gaps(unit: Mapping[str, Any]) -> str:
    """A declared stop read against what the unit where it stopped never
    exercised."""
    where = f"on the progress unit where the run stopped (unit {unit['unit'] + 1})"
    gaps = []
    if unit["untried"]:
        gaps.append(f"never taken: {_names(unit['untried'])}")
    if unit["dead"]:
        gaps.append(f"taken {DEAD_MIN_TRIES} times or more and never productive: {_names(unit['dead'])}")
    if gaps:
        text = (
            f"Before the declaration stands, read it against what the record never tested {where}. "
            + "; ".join(_sentence(gap) for gap in gaps) + ". A conclusion that rests on an untested rule "
            "is untested."
        )
    else:
        text = (
            f"Every advertised action was taken {where}, and each changed the world at least once. "
            "If the declaration still holds, it rests on a premise the actions do not show; name it."
        )
    return text + " The section Never exercised has the rest."


def _integrity(record: Mapping[str, Any], items: int) -> list[str]:
    verdict = record["integrity"]
    audit = verdict["audit"]
    out = ["", "## Integrity", ""]
    if audit is None:
        out.append(
            "Not audited: `assay audit` could not run on this directory and it holds no audit file. The audit "
            "checks the chain, the contiguity of the ids, the ungated rule (verify/JOURNAL_SPEC.md sections 4 to 6) "
            "and the heads anchored outside the run directory."
        )
    else:
        out.append(
            f"The kernel's audit ({audit['source']}, {_when(audit['computed_at'])}"
            + ("" if audit["current"] else "; older than the journal's last event, so rerun `assay audit`")
            + f"): **{audit['verdict']}**. {audit['events']} events, {audit['paid']} paid; contiguous "
            f"{'yes' if audit['contiguous'] else 'NO'}; {len(audit['ungated'])} ungated paid events; chain "
            f"{audit['chain']}; {_anchors(audit)}."
        )
        if audit["ungated"]:
            out.append(f"- ungated: {ids(audit['ungated'], items)}. One ungated event invalidates the run for scoring.")
        for problem in audit["problems"][:items]:
            out.append(f"- problem: {_prose(problem)}")
    if verdict["head"]:
        event = f"after e{verdict['head_event']}" if verdict["head_event"] is not None else "as stored"
        out += ["", f"The chain head the kernel stored (`.assay/chain.json`, {event}): `{verdict['head']}`."]
    out += [
        "",
        "To check this run again, or a copy of it: `assay --run-dir <run dir> audit`, and compare the copy's "
        "`.assay/chain.json` head with the one above.",
    ]
    return out


def _form_line(form: str, entry: Mapping[str, Any], items: int) -> str:
    total = len(entry["held"]) + len(entry["missed"])
    line = f"- {_code(form)}: held {len(entry['held'])} of {total}"
    if entry["held"]:
        line += f", at {ids(entry['held'], items)}"
    if entry["missed"]:
        line += f"; missed at {ids(entry['missed'], items)}"
    if entry["invalid"]:
        line += f"; not graded at {ids(entry['invalid'], items)}"
    left_out = [kind for kind in entry["counted_as"] if kind in ("machine", "vacuous", "coerced")]
    if left_out:
        line += f" ({', '.join(left_out)}, left out of the meter)"
    examples = list(entry["texts"].items())[:3]
    if examples and not (len(examples) == 1 and examples[0][0] == form):
        line += ". For example " + ", ".join(f"{_code(text)} (e{event})" for text, event in examples)
    return line + "."


def _what_held(record: Mapping[str, Any], items: int) -> list[str]:
    predictions = record["predictions"]
    counts = predictions["meter"]
    out = ["", "## What held", ""]
    out.append(
        "Every outcome the code graded on a paid action, by action and by form (the outcome's text with its "
        "value left out). A held outcome is a fact the world confirmed at that event. From the journal."
    )
    if predictions["line"]:
        out += ["", _code(predictions["line"], 400)]
    excluded = []
    if counts["coerced"]:
        excluded.append(f"{_plural(counts['coerced'], 'free-text outcome')} graded as change")
    if counts["machine"]:
        excluded.append(f"{_plural(counts['machine'], 'outcome')} the kernel predicted from a model")
    if counts["vacuous_passes"]:
        excluded.append(
            f"{counts['vacuous_passes']} passes of vacuous verifiers ({_names([value[:12] for value in counts['vacuous_verifiers']])})"
        )
    if excluded:
        out += ["", "Left out of the meter, as `assay status` leaves them out: " + "; ".join(excluded) + "."]
    ordered = sorted(predictions["actions"].items(), key=lambda pair: (-pair[1]["paid"], pair[0]))
    if not ordered:
        out += ["", "No paid action was taken."]
    for name, slot in ordered:
        out += ["", f"### {_prose(name)}", ""]
        out.append(f"{_plural(slot['paid'], 'paid action')}; {slot['changed']} changed the world.")
        if not slot["forms"]:
            out += ["", "- no graded outcome"]
            continue
        out.append("")
        forms = sorted(slot["forms"].items(), key=lambda pair: (-len(pair[1]["held"]), pair[0]))
        if len(forms) > items:
            out.append(f"- the {items} forms held most often of {len(forms)}; the JSON record has them all.")
        out += [_form_line(form, entry, items) for form, entry in forms[:items]]
    keys = record["declarations"]["keys"]
    if keys:
        out += ["", "The player also declared, with its actions (journaled on the event): "
                + ", ".join(f"{_prose(key)} {_plural(count, 'time')}" for key, count in keys.items()) + "."]
    return out


def _the_misses(record: Mapping[str, Any], items: int) -> list[str]:
    predictions = record["predictions"]
    out = ["", "## The misses", ""]
    out.append(
        "Each miss is the world correcting the model at one event: the outcome the player predicted, then what "
        "the code read instead. From the journal."
    )
    if not predictions["missed"]:
        out += ["", "No outcome missed."]
    else:
        out.append("")
        for item in predictions["missed"][:items]:
            tag = " (the kernel's prediction)" if item["counted_as"] == "machine" else ""
            out.append(
                f"- e{item['event']} {_code(item['action'], 80)}: {_code(item['outcome'])} missed{tag}; "
                f"the world: {_code(item['actual'])}."
            )
        if len(predictions["missed"]) > items:
            out.append(f"- and {len(predictions['missed']) - items} more, in the JSON record.")
    if predictions["ungraded"]:
        out += ["", "Outcomes the code could not grade, with the reason:", ""]
        for item in predictions["ungraded"][:items]:
            out.append(f"- e{item['event']} {_code(item['action'], 80)}: {_code(item['outcome'])}: {_code(item['actual'])}.")
        if len(predictions["ungraded"]) > items:
            out.append(f"- and {len(predictions['ungraded']) - items} more, in the JSON record.")
    return out


def _verified_lines(record: Mapping[str, Any], items: int) -> list[str]:
    notes = record["notes"]
    out = ["", "## The player's Verified lines", ""]
    if notes is None:
        out.append("The run holds no notes file.")
        return out
    if not notes["has_verified"]:
        out.append("The notes have no Verified section (a heading that says Verified).")
        return out
    if not notes["verified"]:
        out.append("The notes' Verified section is empty.")
        return out
    out.append(
        "The lines of the Verified section of .assay/NOTES.md, the player's own words (the chain does not "
        "cover the notes), each with how the events it cites graded in the journal. A cited miss can be "
        "the evidence a rule rests on; a line that cites nothing rests on nothing the record shows."
    )
    out.append("")
    for item in notes["verified"][:items]:
        cites = ", ".join(
            f"e{cite['event']} ({cite['status']})" if "," in cite["status"] else f"e{cite['event']} {cite['status']}"
            for cite in item["cites"]
        )
        out.append(f"- {_prose(item['line'])} ({item['says']}" + (f": {cites}" if cites else "") + ")")
    if len(notes["verified"]) > items:
        out.append(f"- and {len(notes['verified']) - items} more, in the JSON record.")
    tally = {key: sum(1 for item in notes["verified"] if item["verdict"] == key) for key in VERDICTS}
    parts = [f"{count} {VERDICTS[key][0 if count == 1 else 1]}" for key, count in tally.items() if count]
    out += ["", f"In all, of {_plural(len(notes['verified']), 'line')}: " + ", ".join(parts) + "."]
    return out


def _never_exercised(record: Mapping[str, Any], items: int) -> list[str]:
    cover = record["coverage"]
    end = record["ending"]
    notes = record["notes"]
    whole = cover["run"]
    out = ["", "## Never exercised", ""]
    out.append(
        "The coverage audit over the journal: what the record never tested. A conclusion that rests on one of "
        "these rests on an untested rule."
    )
    out += [
        "",
        f"- Advertised and never taken: {_names(whole['untried'])}.",
        f"- Taken {DEAD_MIN_TRIES} times or more and never changed the world: {_names(whole['dead'])}.",
        f"- Taken, but never with a specific outcome (only `change` or free text): {_names(whole['unspecific'])}.",
    ]
    if cover["states_never_graded"] is not None:
        out.append(
            "- Declared states never named by a graded outcome (from .assay/channels.json): "
            f"{_names(cover['states_never_graded'])}."
        )
    unit = cover["unit"]
    if end["outcome"] != "WIN" and (not end["progress"][1] or unit["unit"] < end["progress"][1]):
        out += [
            "",
            f"On the progress unit where the run stands (unit {unit['unit'] + 1}, {_plural(unit['paid'], 'paid action')}), "
            f"as the kernel's coverage audit reads it: untried: {_names(unit['untried'])}; never productive: "
            f"{_names(unit['dead'])}; stalled: {'yes' if unit['stalled'] else 'no'}"
            + (f" (the last {STALL_WINDOW} paid actions changed nothing)" if unit["stalled"] else "")
            + ".",
        ]
    declared = record["declarations"]
    if declared["conclusions"]:
        out += ["", "The conclusions of impossibility or absence the player journaled with an action, with the "
                "coverage audit it gave for each:", ""]
        for item in declared["conclusions"][:items]:
            said = "; ".join(f"{key}: {_prose(value, 200)}" for key, value in item["conclusion"].items())
            audit = _prose(item["coverage_audit"], 300) if item["coverage_audit"] else "none given"
            out.append(f"- e{item['event']} {_code(item['action'], 80)}: {said}. Coverage audit: {audit}.")
    if notes is not None and notes["open"]:
        out += ["", "The player's own open questions, from its notes:", ""]
        out += [f"- {_prose(line)}" for line in notes["open"][:items]]
    return out


def _owner_line(act: Mapping[str, Any]) -> str:
    kind = act.get("kind")
    when = _when(act.get("timestamp"))
    if kind == "approval_granted":
        return f"- {when}: the owner granted one use of {_prose(act.get('action'))}."
    if kind == "approval_used":
        return f"- {when}: the approval of {_prose(act.get('action'))} was used."
    if kind == "liveness_waived":
        return f"- {when}: the owner waived the rehearsal of {_prose(act.get('action'))}: {_prose(act.get('because'))}."
    if kind == "goal_ratified":
        return f"- {when}: the owner ratified goal proposal #{act.get('id')}: {_prose(act.get('text'))}."
    return f"- {when}: an owner operation was refused ({_prose(act.get('operation'))})."


def _owner_and_refusals(record: Mapping[str, Any], items: int) -> list[str]:
    activity = record["activity"]
    out = ["", "## The owner's acts and the refusals", ""]
    out.append("From the activity log (the chain does not cover it).")
    out.append("")
    if activity["owner"]:
        out += [_owner_line(act) for act in activity["owner"][:items]]
    else:
        out.append("- The owner did not act on this run.")
    refusals = activity["refusals"]
    if refusals:
        out.append(
            "- The commands the harness refused, by code: "
            + "; ".join(f"{code} {slot['count']} (first: {_code(slot['first'], 120)})" for code, slot in refusals.items())
            + "."
        )
    else:
        out.append("- The harness refused no command.")
    return out


def _sessions_and_cost(record: Mapping[str, Any], items: int) -> list[str]:
    sessions = record["sessions"]
    spend = record["activity"]["spend"]
    paid = record["ending"]["paid"]
    out = ["", "## Sessions and cost", ""]
    if sessions is None:
        out.append("No session runner's ledger sits beside the run: the player was not run by `/assay:run`.")
    else:
        out.append(f"From the session runner's ledger, {sessions['ledger']} (the chain does not cover it).")
        out += ["", "| Session | Host | Model | Minutes | Paid actions | Dollars | Tokens | Stop blocks | Ended |",
                "|---|---|---|---|---|---|---|---|---|"]
        for row in sessions["rows"]:
            host = " ".join(str(part) for part in (row["host"], row["host_version"]) if part)
            tokens = f"{row['tokens']:,}" if isinstance(row["tokens"], int) else ""
            ended = str(row["ended_because"] or "") + (" (usage limit, not counted)" if row["rate_limited"] else "")
            out.append(
                f"| {row['session']} | {_prose(host, 60)} | {_prose(row['model'] or 'default', 40)} | "
                f"{row['wall_seconds'] / 60:.1f} | {row['paid_from']} to {row['paid_to']} | {_money(row['dollars'])} | "
                f"{tokens} | {row['blocks']} | {ended} |"
            )
        totals = sessions["totals"]
        out += [
            "",
            f"In all: {_plural(totals['sessions'], 'session')}, {totals['wall_seconds'] / 60:.1f} min, "
            f"{_money(totals['dollars'])} reported by the host"
            + (f", {totals['tokens']:,} tokens" if totals["tokens"] is not None else "")
            + f"; the Stop gate blocked {_plural(totals['blocks'], 'stop')}.",
        ]
    if spend["reports"]:
        per = f", {_money(spend['usd'] / paid)} per paid action" if paid else ""
        out += ["", f"The spend reported to the run (`assay spend report`): {_money(spend['usd'])}, "
                f"{spend['tokens']:,} tokens, over {_plural(spend['reports'], 'report')}{per}."]
    return out


SECTIONS = (
    _head, _summary, _how_it_ended, _integrity, _what_held, _the_misses, _verified_lines, _never_exercised,
    _owner_and_refusals, _sessions_and_cost,
)


def render(record: Mapping[str, Any], items: int = DEFAULT_ITEMS) -> str:
    """The document, from the record alone, one section after another."""
    out: list[str] = []
    for section in SECTIONS:
        out += section(record, items)
    return "\n".join(out) + "\n"


# --- the command line ------------------------------------------------------------------


def _refuse_inside(run_dir: Path, target: Path) -> None:
    resolved = target.resolve()
    for name in (*STATE_DIRS, ".claude"):
        directory = (run_dir / name).resolve()
        try:
            resolved.relative_to(directory)
        except ValueError:
            continue
        raise EvalError(f"--out must not point under the run's {name}, got {target}")


def write(record: Mapping[str, Any], document: Path, items: int) -> Path:
    """The document and, beside it, the record; returns the record's path."""
    data = document.with_suffix(".json") if document.suffix == ".md" else document.with_name(document.name + ".json")
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(render(record, items))
    data.write_text(json.dumps(record, indent=2) + "\n")
    return data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.eval report",
        description="Write the report of one run: how it ended, the record's verdict and chain head, the "
        "predictions that held and missed, the player's Verified lines against the journal, what was never "
        "exercised, and the sessions and their cost.",
    )
    parser.add_argument("--run-dir", type=Path, required=True, metavar="DIR", help="the run directory")
    parser.add_argument("--out", type=Path, metavar="FILE", help="the document (default: <run dir>.report.md; the JSON record goes beside it)")
    parser.add_argument("--sessions", type=Path, metavar="DIR", help="the session runner's state (default: <run dir>.sessions)")
    parser.add_argument("--check", metavar="CMD", help="an external checker to run, `{run_dir}` replaced; exit status 0 is a pass")
    parser.add_argument("--items", type=int, default=DEFAULT_ITEMS, metavar="N", help=f"the longest list the document shows (default {DEFAULT_ITEMS})")
    parser.add_argument("--assay", metavar="CMD", help=f"the launcher that runs the kernel's audit, split like a shell line (default: {DEFAULT_LAUNCHER})")
    parser.add_argument("--no-audit", action="store_true", help="read the last .assay/audit.json instead of running the audit")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.items < 1:
            raise EvalError("--items must be at least 1")
        run_dir = args.run_dir.resolve()
        if not run_dir.is_dir():
            raise EvalError(f"no run directory at {args.run_dir}")
        if args.sessions is not None and not (args.sessions / "sessions.json").is_file():
            raise EvalError(f"no session ledger at {args.sessions}: expected sessions.json")
        document = args.out or run_dir.with_name(run_dir.name + DOCUMENT_SUFFIX)
        _refuse_inside(run_dir, document)
        launcher = None if args.no_audit else (shlex.split(args.assay) if args.assay else [str(DEFAULT_LAUNCHER)])
        record = build(run_dir, sessions_dir=args.sessions, check=args.check, launcher=launcher)
        try:
            data = write(record, document, args.items)
        except OSError as error:
            raise EvalError(f"cannot write the report at {document}: {error}") from None
    except EvalError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
    print(f"WROTE | {document.resolve()} | {data.resolve()}", flush=True)
    for line in summary_lines(record):
        print(line, flush=True)
    return 0
