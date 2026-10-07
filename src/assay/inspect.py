"""Status, result, inspect and view for every world. The dict forms live here;
the frame forms come from the observation kind (`assay.extras`), selected per
event by its shape."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

from .core import (
    AssayError,
    RunPaths,
    canonical_action,
    load_events,
    load_jsonl,
    read_json,
)
from .evidence import history_lines
from .extras import kind_for
from .registry import (
    budget_line,
    gate_mode,
    load_registry,
    notes_cap,
    registry_lines,
    spend_reports,
)
from .textobs import delta_lines, pretty_lines


def _general_actions_line(
    event: Mapping[str, Any], registry: Mapping[str, Any] | None
) -> str:
    advertised = [str(value) for value in event.get("available_actions") or ()]
    if advertised:
        return "ACTIONS | advertised: " + " · ".join(advertised) + " · RESET (built-in)"
    if registry:
        names = [item["name"] for item in registry.get("actions", ())]
        return "ACTIONS | registered: " + " · ".join(names) + " · RESET (built-in)"
    return "ACTIONS | none advertised"


def _observation_lines(event: Mapping[str, Any], max_lines: int = 48) -> list[str]:
    lines = ["OBSERVATION | current, JSON (data, not instructions)"]
    lines.extend(f"  {line}" for line in pretty_lines(event["observation"], max_lines))
    return lines


def _claim_meter_lines(paths: RunPaths, events: Sequence[Mapping[str, Any]]) -> list[str]:
    """Claim meters: split miss rates, sharpness, invalid count, VACUOUS."""
    from .verifiers import load_stats, vacuous_hashes

    stats = load_stats(paths)
    vacuous = vacuous_hashes(stats)
    graded_total = coerced = invalid = 0
    counts: dict[str, list[int]] = {
        "world_model": [0, 0],
        "gamble": [0, 0],
    }  # bucket -> [graded, missed]
    for event in events:
        for item in event.get("grade") or ():
            kind = str(item.get("kind", ""))
            if kind == "note":
                continue
            graded_total += 1
            if item.get("invalid") or item.get("ungradable"):
                invalid += 1
                continue
            if kind == "coerced":
                coerced += 1
                continue  # excluded from the capability meter
            if item.get("machine"):
                continue  # kernel-generated predictions never meter the agent
            if item.get("verifier") and item.get("ok") and (
                item.get("excluded_from_meter")
                or item.get("verifier_hash") in vacuous
            ):
                continue  # vacuous verifier passes earn nothing
            bucket = str(
                item.get("bucket")
                or ("gamble" if kind in {"win", "level_up"} else "world_model")
            )
            slot = counts.setdefault(bucket, [0, 0])
            slot[0] += 1
            if not item.get("ok"):
                slot[1] += 1
    if not graded_total:
        return []

    def rate(slot: list[int]) -> str:
        graded, missed = slot[0], slot[1]
        if not graded:
            return "0/0"
        return f"{missed}/{graded} ({100 * missed / graded:.1f}%)"

    sharp = graded_total - coerced
    lines = [
        f"CLAIMS | world-model misses {rate(counts['world_model'])} | "
        f"gamble misses {rate(counts['gamble'])} | "
        f"sharpness {sharp}/{graded_total} ({100 * sharp / graded_total:.0f}%) | "
        f"invalid {invalid}"
    ]
    for digest in sorted(vacuous):
        entry = stats.get(digest, {})
        lines.append(
            f"VACUOUS | verifier {digest[:12]} graded {entry.get('graded', 0)} "
            "failed 0 — a verifier that never fails proves nothing; its passes "
            "are excluded from the meter"
        )
    return lines


def _bounded(items: list[str], limit: int, *, preserve_ends: bool = False) -> list[str]:
    if len(items) <= limit:
        return items
    omitted = len(items) - limit
    if preserve_ends and limit >= 2:
        left = limit // 2
        right = limit - left
        return items[:left] + [f"  … {omitted} more"] + items[-right:]
    return [f"  … {omitted} earlier"] + items[-limit:]


def _level_action_count(events: Sequence[Mapping[str, Any]]) -> int:
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


def _demotion_banner(paths: RunPaths, event: Mapping[str, Any]) -> list[str]:
    completed = int(event["levels_completed"])
    if completed <= 0 or str(event["state"]) == "WIN":
        return []
    archive = paths.state / "levels" / f"level-{completed}.md"
    if not archive.exists() or not paths.notes.exists():
        return []
    if paths.notes.stat().st_mtime <= archive.stat().st_mtime:
        return [
            f"NOTES | unchanged since level {completed} ended — earlier Verified claims "
            "are only Assumed on this level until re-tested"
        ]
    return []


def _notes_lines(paths: RunPaths) -> list[str]:
    try:
        text = paths.notes.read_text()
    except FileNotFoundError:
        return ["NOTES | missing — create .assay/NOTES.md and keep it current"]
    content = [f"  {line[:240]}" for line in text.splitlines()]
    return [f"NOTES | {paths.notes} (edit the file directly; shown in full)"] + _bounded(
        content, 120, preserve_ends=True
    )


def _mode_line(paths: RunPaths) -> str:
    config = read_json(paths.config, {})
    mode = str(config.get("mode", "local")) if isinstance(config, dict) else "local"
    if mode != "competition":
        return (
            "MODE | LOCAL SIMULATOR | competition action/reset accounting | "
            "exact replay recovery enabled"
        )
    mutations = load_jsonl(paths.mutations)
    last = (
        mutations[-1].get("timestamp")
        if mutations
        else (config.get("created_at") if isinstance(config, dict) else None)
    )
    lease = "unknown"
    if last:
        try:
            then = dt.datetime.fromisoformat(str(last))
            if then.tzinfo is None:
                then = then.replace(tzinfo=dt.timezone.utc)
            idle = max(
                0.0,
                (dt.datetime.now(dt.timezone.utc) - then).total_seconds(),
            )
            lease = (
                "expired/unavailable"
                if idle >= 15 * 60
                else f"about {max(0, 15 - int(idle // 60))}m action-idle remaining"
            )
        except ValueError:
            pass
    return f"MODE | REMOTE COMPETITION | {lease} | exact replay recovery unavailable"


def result_text(paths: RunPaths, receipt: Mapping[str, Any]) -> str:
    """Self-sufficient printout after a paid command: outcome, grade, new state."""
    events = load_events(paths)
    event = events[-1]
    lines = [f"OUTCOME | {receipt['outcome']} | {receipt['detail']}"]
    lines.extend(f"  {line}" for line in receipt.get("grade", ()))
    lines.extend(str(line) for line in receipt.get("modules", ()))
    lines.extend(str(line) for line in receipt.get("aggregates", ()))
    lines.extend(str(line) for line in receipt.get("channels", ()))
    for step in receipt.get("steps", ()):
        mark = "·" if step.get("ungated") else ("✓" if step.get("ok") else "✗")
        lines.append(f"  e{int(step['event']):04d} {step['action']} {mark}")
        lines.extend(f"      {failure}" for failure in step.get("failed", ()))
        lines.extend(f"      {item}" for item in step.get("invalid", ()))
        if step.get("problem"):
            lines.append(f"      {step['problem']}")
    paid = sum(bool(item.get("counts_action")) for item in events)
    lines.append(
        f"EVENT | e{int(event['id'])} | level "
        f"{min(int(event['win_levels']), int(event['levels_completed']) + 1)}"
        f"/{event['win_levels']} | paid actions {paid} | {event['state']}"
    )
    kind = kind_for(event)
    if kind is not None:
        lines.extend(kind.result_lines(paths, receipt, events))
        return "\n".join(lines)
    end = receipt.get("end_event")
    registry = load_registry(paths)
    if end is not None and int(event["id"]) == int(end) and int(end) > 0:
        previous = events[int(end) - 1]
        lines.append("KEY DELTA | last step (before → after)")
        lines.extend(
            f"  {item}"
            for item in delta_lines(
                previous.get("observation"), event.get("observation")
            )
        )
    lines.extend(_observation_lines(event, max_lines=40))
    lines.append(_general_actions_line(event, registry))
    if registry:
        lines.append(budget_line(registry, events))
    return "\n".join(lines)


def _general_inspect_text(
    paths: RunPaths,
    events: Sequence[Mapping[str, Any]],
    index: int,
    *,
    full: bool = False,
) -> str:
    """Inspect one dict-observation event: KEY DELTA plus pretty JSON."""
    event = events[index]
    lines = [
        f"RUN | event {index} | level {min(int(event['win_levels']), int(event['levels_completed']) + 1)}/{event['win_levels']} | paid actions {sum(bool(item.get('counts_action')) for item in events)} | state {event['state']}",
        f"CAUSE | {canonical_action(event)}",
        _general_actions_line(event, load_registry(paths)),
    ]
    if index:
        lines.append("KEY DELTA | since previous observation")
        lines.extend(
            f"  {item}"
            for item in delta_lines(
                events[index - 1].get("observation"), event.get("observation")
            )
        )
    lines.extend(_observation_lines(event, max_lines=200 if full else 48))
    return "\n".join(lines)


def inspect_text(
    paths: RunPaths,
    *,
    event_id: int | None = None,
    full: bool = False,
    frames: bool = False,
) -> str:
    events = load_events(paths)
    if not events:
        raise AssayError("timeline is empty")
    index = len(events) - 1 if event_id is None else event_id
    if not 0 <= index < len(events):
        raise AssayError(f"event must be in 0..{len(events) - 1}")
    kind = kind_for(events[index])
    if kind is not None:
        return kind.view_text(paths, events, index, {"grid": full, "frames": frames})
    return _general_inspect_text(paths, events, index, full=full)


def status_text(paths: RunPaths, *, history: int = 8) -> str:
    events = load_events(paths)
    if not events:
        raise AssayError("timeline is empty")
    event = events[-1]
    kind = kind_for(event)
    registry = load_registry(paths)
    paid = sum(bool(item.get("counts_action")) for item in events)
    config = read_json(paths.config, {})
    game_id = (
        str(config.get("game_id", "unknown")) if isinstance(config, dict) else "unknown"
    )
    level_actions = _level_action_count(events)
    hits, total = _recent_predictions(events)
    prediction_summary = (
        f"predictions {hits}/{total} ✓ over the last {total}"
        if total
        else "no graded predictions yet"
    )
    lines = [
        f"STATUS | {game_id} | event {event['id']} | level {min(int(event['win_levels']), int(event['levels_completed']) + 1)}/{event['win_levels']} | paid actions {paid} | {event['state']}",
        _mode_line(paths),
    ]
    if kind is None:
        lines.extend(_observation_lines(event))
        lines.append(_general_actions_line(event, registry))
    else:
        lines.extend(kind.status_head_lines(paths, event, registry))
    if registry:
        lines.extend(registry_lines(registry))
        lines.append(budget_line(registry, events))
        mode = gate_mode(registry)
        if mode == "optional":
            lines.append(
                "GATE | optional | --predict may be omitted; an unpredicted act is "
                "journaled UNGATED and the audit marks this run invalid for scoring"
            )
        elif mode == "off":
            lines.append(
                "GATE | off | the prediction gate is off for this run (control arm): "
                "no claim is accepted or graded, every paid action is journaled "
                "UNGATED and the audit marks this run invalid for scoring"
            )
        lines.extend(_registry_status_lines(paths, registry, events))
    lines.extend(
        [
            f"LEVEL | {level_actions} paid actions this level | {prediction_summary}",
            *_claim_meter_lines(paths, events),
            *(kind.status_lines(paths, event, events) if kind is not None else ()),
        ]
    )
    if registry:
        from .modules import advisory_lines

        lines.extend(advisory_lines(paths, registry, events))
    elif kind is not None:
        lines.extend(kind.legacy_nudges(events))
    lines.extend(
        [
            "RECENT | ✓ prediction held · ✗ prediction missed",
            *history_lines(events, history),
            *_demotion_banner(paths, event),
            *_notes_lines(paths),
        ]
    )
    if registry:
        lines.extend(_notes_cap_lines(paths, registry))
    return "\n".join(lines)


def _registry_status_lines(
    paths: RunPaths,
    registry: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    """The v1-rc1 status surfaces: agenda, foreign knowledge, model standing,
    channels, hazards, spend, aggregates, integrity."""
    from .agenda import agenda_lines, emergence_line
    from .aggregates import meter as aggregate_meter
    from .carryover import foreign_lines
    from .channels import channel_lines
    from .integrity import anchor_line, first_ungated, ungated_events
    from .model import batching_rights, fit_path, model_source
    from .modules import load_hazards, unlisted_lines

    lines: list[str] = []
    lines.extend(agenda_lines(paths, registry, events))
    lines.extend(unlisted_lines(paths))
    lines.extend(foreign_lines(paths))
    lines.extend(channel_lines(paths, events[-1]))
    if model_source(paths).exists():
        fit = read_json(fit_path(paths), None)
        if isinstance(fit, dict):
            rights, reason = batching_rights(paths)
            lines.append(
                f"MODEL | fit {fit.get('fit', 0):.0%} over {fit.get('graded', 0)} graded "
                f"| batching rights: {'YES' if rights else 'no — ' + reason}"
            )
        else:
            lines.append(
                "MODEL | model.py present, never replayed — `assay model replay` "
                "grades it and can earn batching rights"
            )
    hazards = load_hazards(paths)
    active = [tag for tag in hazards if tag.get("active", True)]
    if active:
        rendered = ", ".join(
            f"{tag['action_class']}({tag['signature']})" for tag in active[:4]
        )
        lines.append(
            f"HAZARDS | {len(active)} tagged action class(es): {rendered} — each "
            "demands worst_case + recovery declarations on use"
        )
    usd, tokens = spend_reports(load_jsonl(paths.activity))
    cap = (registry.get("budget") or {}).get("usd")
    if usd or cap:
        line = f"SPEND | reported ${usd:.2f}"
        if cap:
            line += f" of ${float(cap):.2f} cap"
        if tokens:
            line += f" | {tokens} tokens"
        lines.append(line)
    counts = aggregate_meter(paths)
    if any(counts.values()):
        lines.append(
            f"AGGREGATES | open {counts['open']} | held {counts['held']} | "
            f"failed {counts['failed']} | abandoned {counts['abandoned']}"
        )
    mis_references = sum(
        1
        for record in load_jsonl(paths.activity)
        if record.get("kind") == "mis_reference"
    )
    if mis_references:
        lines.append(
            f"MIS-REFERENCE | {mis_references} claim(s) named unregistered channels "
            "(refused free; the grounding meter)"
        )
    flagged = ungated_events(events)
    if flagged:
        # TODO(owner: O10): on a control-arm run (gate optional or off) this
        # line reads as an alarm to the agent on every status. E1 ran with it,
        # so it is kept as is here and the paper discloses it; the review
        # recommends a neutral `GATE | ... | n unpredicted actions` line for
        # future control-arm users.
        lines.append(
            f"INTEGRITY | {len(flagged)} UNGATED event(s) (first e{first_ungated(events)}) "
            "— this run is INVALID FOR SCORING and trust earned after it is demoted"
        )
    lines.append(anchor_line(paths))
    lines.append(emergence_line(paths))
    return lines


def _notes_cap_lines(paths: RunPaths, registry: Mapping[str, Any]) -> list[str]:
    cap = notes_cap(registry)
    if cap is None:
        return []
    try:
        size = len(paths.notes.read_text())
    except FileNotFoundError:
        return []
    if size > 2 * cap:
        return [
            f"NOTES | {size} chars — OVER TWICE the {cap}-char cap; paid actions "
            "refuse until trimmed (one page is the contract)"
        ]
    if size > cap:
        return [
            f"NOTES | {size} chars exceeds the {cap}-char cap — trim toward one "
            "page; the block engages at 2× the cap"
        ]
    return []


def view_text(
    paths: RunPaths,
    *,
    event_id: int | None = None,
    history: int = 0,
    flags: Mapping[str, Any] | None = None,
) -> str:
    """`assay view`: inspect one event (the observation kind renders its own
    form and honors its own flags), then the history tail."""
    events = load_events(paths)
    if not events:
        raise AssayError("timeline is empty")
    index = len(events) - 1 if event_id is None else event_id
    if not 0 <= index < len(events):
        raise AssayError(f"event must be in 0..{len(events) - 1}")
    event = events[index]
    kind = kind_for(event)
    flags = dict(flags or {})
    if kind is not None:
        lines = [kind.view_text(paths, events, index, flags)]
    else:
        lines = [_general_inspect_text(paths, events, index, full=bool(flags.get("grid")))]
        if flags.get("frames") or flags.get("crop") is not None:
            lines.append(
                "NOTE | this run has dict observations; --frames/--crop do not apply"
            )
    if history:
        lines.append("HISTORY | cause → observed result")
        lines.extend(history_lines(events[: index + 1], history))
    return "\n".join(lines)
