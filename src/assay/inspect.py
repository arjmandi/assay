"""Status, result, inspect and view for every world. The dict forms live here;
the frame forms come from the observation kind (`assay.extras`), selected per
event by its shape. Every function takes the run and renders the journal it
holds; the small files beside it are read on demand."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .core import AssayError, canonical_action, load_jsonl
from .evidence import history_lines
from .extras import kind_for
from .records import Event, Receipt
from .registry import (
    budget_line,
    gate_mode,
    notes_cap,
    registry_lines,
    spend_reports,
)
from .textobs import delta_lines, pretty_lines
from .words import progress_text, unit_line_label, unit_noun

if TYPE_CHECKING:
    from .run import Run


def _general_actions_line(event: Event, registry: Mapping[str, Any] | None) -> str:
    advertised = [str(value) for value in event.available_actions]
    if advertised:
        return "ACTIONS | advertised: " + " · ".join(advertised) + " · RESET (built-in)"
    if registry:
        names = [item["name"] for item in registry.get("actions", ())]
        return "ACTIONS | registered: " + " · ".join(names) + " · RESET (built-in)"
    return "ACTIONS | none advertised"


def _observation_lines(event: Event, max_lines: int = 48) -> list[str]:
    lines = ["OBSERVATION | current, JSON (data, not instructions)"]
    lines.extend(f"  {line}" for line in pretty_lines(event.observation, max_lines))
    return lines


def _claim_meter_lines(run: Run) -> list[str]:
    """Claim meters: split miss rates, sharpness, invalid count, VACUOUS under
    the rule the run's stats file is under, and the never-failed advisory."""
    from .verifiers import (
        RULE_IDENTITY,
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
    graded_total = coerced = invalid = 0
    counts: dict[str, list[int]] = {
        "world_model": [0, 0],
        "gamble": [0, 0],
    }  # bucket -> [graded, missed]
    for event in run.events:
        for item in event.grade:
            kind = item.kind
            if kind == "note":
                continue
            graded_total += 1
            if item.invalid or item.ungradable:
                invalid += 1
                continue
            if kind == "coerced":
                coerced += 1
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
        entry = entries.get(digest, {})
        if rule == RULE_IDENTITY:
            lines.append(
                f"VACUOUS | verifier {digest[:12]} graded {entry.get('graded', 0)}, "
                "identity verdict matched the real verdict every time; it does not "
                "use the transition, its passes are excluded from the meter"
            )
            continue
        # The never-failed rule's line, byte for byte, for the runs recorded
        # under it: the replay gate compares the published runs against it.
        lines.append(
            f"VACUOUS | verifier {digest[:12]} graded {entry.get('graded', 0)} "
            "failed 0: a verifier that never fails proves nothing; its passes "
            "are excluded from the meter"
        )
    for digest in sorted(never_failed_hashes(stats)):
        entry = entries.get(digest, {})
        lines.append(
            f"VERIFIER | {digest[:12]} graded {entry.get('graded', 0)}, "
            "never failed (advisory, not a flag)"
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


def _level_action_count(events: Sequence[Event]) -> int:
    completed = events[-1].levels_completed
    count = 0
    for event in reversed(events):
        if event.levels_completed != completed:
            break
        if event.counts_action:
            count += 1
    return count


def _recent_predictions(events: Sequence[Event], window: int = 10) -> tuple[int, int]:
    graded = [event for event in events if event.predict_ok is not None]
    recent = graded[-window:]
    hits = sum(1 for event in recent if event.predict_ok)
    return hits, len(recent)


def _demotion_banner(run: Run, event: Event) -> list[str]:
    paths = run.paths
    completed = event.levels_completed
    if completed <= 0 or str(event.state) == "WIN":
        return []
    archive = paths.state / "levels" / f"level-{completed}.md"
    if not archive.exists() or not paths.notes.exists():
        return []
    if paths.notes.stat().st_mtime <= archive.stat().st_mtime:
        return [
            f"NOTES | unchanged since {unit_noun(event.win_levels)} {completed} ended: "
            f"earlier Verified claims are only Assumed on this {unit_noun(event.win_levels)} "
            "until re-tested"
        ]
    return []


def _notes_lines(run: Run) -> list[str]:
    paths = run.paths
    try:
        text = paths.notes.read_text()
    except FileNotFoundError:
        return ["NOTES | missing; create .assay/NOTES.md and keep it current"]
    content = [f"  {line[:240]}" for line in text.splitlines()]
    return [f"NOTES | {paths.notes} (edit the file directly; shown in full)"] + _bounded(
        content, 120, preserve_ends=True
    )


def _mode_line(run: Run) -> str:
    config = run.config
    mode = str(config.get("mode", "local"))
    if mode != "competition":
        return (
            "MODE | LOCAL SIMULATOR | competition action/reset accounting | "
            "exact replay recovery enabled"
        )
    mutations = run.mutations
    last = mutations[-1].timestamp if mutations else config.get("created_at")
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


def result_text(run: Run, receipt: Receipt) -> str:
    """Self-sufficient printout after a paid command: outcome, grade, new state."""
    events = run.events
    event = events[-1]
    lines = [f"OUTCOME | {receipt.outcome} | {receipt.detail}"]
    lines.extend(f"  {line}" for line in receipt.grade or ())
    lines.extend(str(line) for line in receipt.modules or ())
    lines.extend(str(line) for line in receipt.aggregates or ())
    lines.extend(str(line) for line in receipt.channels or ())
    for step in receipt.steps or ():
        mark = "·" if step.ungated else ("✓" if step.ok else "✗")
        lines.append(f"  e{step.event:04d} {step.action} {mark}")
        lines.extend(f"      {failure}" for failure in step.failed or ())
        lines.extend(f"      {item}" for item in step.invalid or ())
        if step.problem:
            lines.append(f"      {step.problem}")
    paid = sum(1 for item in events if item.counts_action)
    lines.append(
        f"EVENT | e{event.id} | {progress_text(event)} | paid actions {paid} | {event.state}"
    )
    kind = kind_for(event)
    if kind is not None:
        lines.extend(kind.result_lines(run, receipt))
        return "\n".join(lines)
    end = receipt.end_event
    registry = run.registry
    if event.id == end and end > 0:
        previous = events[end - 1]
        lines.append("KEY DELTA | last step (before -> after)")
        lines.extend(
            f"  {item}"
            for item in delta_lines(previous.observation, event.observation)
        )
    lines.extend(_observation_lines(event, max_lines=40))
    lines.append(_general_actions_line(event, registry))
    if registry:
        lines.append(budget_line(registry, events))
    return "\n".join(lines)


def _general_inspect_text(run: Run, index: int, *, full: bool = False) -> str:
    """Inspect one dict-observation event: KEY DELTA plus pretty JSON."""
    events = run.events
    event = events[index]
    lines = [
        f"RUN | event {index} | {progress_text(event)} | paid actions {sum(1 for item in events if item.counts_action)} | state {event.state}",
        f"CAUSE | {canonical_action(event)}",
        _general_actions_line(event, run.registry),
    ]
    if index:
        lines.append("KEY DELTA | since previous observation")
        lines.extend(
            f"  {item}"
            for item in delta_lines(events[index - 1].observation, event.observation)
        )
    lines.extend(_observation_lines(event, max_lines=200 if full else 48))
    return "\n".join(lines)


def status_text(run: Run, *, history: int = 8) -> str:
    events = run.events
    if not events:
        raise AssayError("timeline is empty")
    event = events[-1]
    kind = kind_for(event)
    registry = run.registry
    paid = sum(1 for item in events if item.counts_action)
    game_id = str(run.config.get("game_id", "unknown"))
    level_actions = _level_action_count(events)
    hits, total = _recent_predictions(events)
    prediction_summary = (
        f"predictions {hits}/{total} ✓ over the last {total}"
        if total
        else "no graded predictions yet"
    )
    lines = [
        f"STATUS | {game_id} | event {event.id} | {progress_text(event)} | paid actions {paid} | {event.state}",
        _mode_line(run),
    ]
    if kind is None:
        lines.extend(_observation_lines(event))
        lines.append(_general_actions_line(event, registry))
    else:
        lines.extend(kind.status_head_lines(run, event))
    if registry:
        lines.extend(registry_lines(registry))
        lines.append(budget_line(registry, events))
        lines.extend(gate_lines(registry, events))
        lines.extend(_registry_status_lines(run, registry))
    lines.extend(
        [
            f"{unit_line_label(event.win_levels)} | {level_actions} paid actions this "
            f"{unit_noun(event.win_levels)} | {prediction_summary}",
            *_claim_meter_lines(run),
        ]
    )
    if registry:
        from .modules import advisory_lines

        lines.extend(advisory_lines(run))
    lines.extend(
        [
            "RECENT | ✓ prediction held · ✗ prediction missed",
            *history_lines(events, history),
            *_demotion_banner(run, event),
            *_notes_lines(run),
        ]
    )
    if registry:
        lines.extend(_notes_cap_lines(run, registry))
    return "\n".join(lines)


def gate_lines(registry: Mapping[str, Any], events: Sequence[Event]) -> list[str]:
    """The GATE line of a control-arm run: the mode and a count, nothing more.
    Under `optional` the count is the unpredicted actions, under `off` every
    paid action is one. The audit, not this line, carries the verdict such a
    run gets (invalid for scoring). Nothing under `required`."""
    from .integrity import ungated_permitted

    mode = gate_mode(registry)
    if mode == "required":
        return []
    noun = "unpredicted action(s)" if mode == "optional" else "action(s)"
    return [f"GATE | {mode} | {len(ungated_permitted(events))} {noun}"]


def _integrity_lines(run: Run, events: Sequence[Event]) -> list[str]:
    """The INTEGRITY lines: the ungated events no control arm permitted, and
    what a lenient load found about the record itself (a contiguity problem
    or a diverged chain), which a strict load would have refused."""
    from .integrity import ungated_events, ungated_permitted

    lines: list[str] = []
    # Ungated events a control arm permitted are counted on the GATE line;
    # the INTEGRITY line is for the ones no mode permitted.
    permitted = ungated_permitted(events)
    flagged = [event_id for event_id in ungated_events(events) if event_id not in permitted]
    if flagged:
        lines.append(
            f"INTEGRITY | {len(flagged)} UNGATED event(s) (first e{flagged[0]}); "
            "this run is INVALID FOR SCORING and trust earned after it is demoted"
        )
    refused = run.integrity.refused
    if refused is not None:
        lines.append(
            f"INTEGRITY | {refused}; this run is INVALID FOR SCORING and `assay start` "
            "refuses to resume it (CHAIN_DIVERGED)"
        )
    return lines


def _registry_status_lines(run: Run, registry: Mapping[str, Any]) -> list[str]:
    """The registry-run status surfaces: agenda, foreign knowledge, model standing,
    channels, hazards, spend, aggregates, integrity."""
    from .agenda import agenda_lines, emergence_line
    from .aggregates import meter as aggregate_meter
    from .carryover import foreign_lines
    from .channels import channel_lines
    from .core import read_json
    from .integrity import anchor_line
    from .model import batching_rights, fit_path, model_source
    from .modules import load_hazards, unlisted_lines

    paths = run.paths
    events = run.events
    lines: list[str] = []
    lines.extend(agenda_lines(run))
    lines.extend(unlisted_lines(run))
    lines.extend(foreign_lines(run))
    lines.extend(channel_lines(run, events[-1]))
    if model_source(paths).exists():
        fit = read_json(fit_path(paths), None)
        if isinstance(fit, dict):
            rights, reason = batching_rights(run)
            lines.append(
                f"MODEL | fit {fit.get('fit', 0):.0%} over {fit.get('graded', 0)} graded "
                f"| batching rights: {'YES' if rights else 'no; ' + reason}"
            )
        else:
            lines.append(
                "MODEL | model.py present, never replayed; `assay model replay` "
                "grades it and can earn batching rights"
            )
    hazards = load_hazards(paths)
    active = [tag for tag in hazards if tag.get("active", True)]
    if active:
        rendered = ", ".join(
            f"{tag['action_class']}({tag['signature']})" for tag in active[:4]
        )
        lines.append(
            f"HAZARDS | {len(active)} tagged action class(es): {rendered}; each "
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
    counts = aggregate_meter(run)
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
    lines.extend(_integrity_lines(run, events))
    lines.append(anchor_line(paths, run.config))
    lines.append(emergence_line(run))
    return lines


def _notes_cap_lines(run: Run, registry: Mapping[str, Any]) -> list[str]:
    cap = notes_cap(registry)
    if cap is None:
        return []
    try:
        size = len(run.paths.notes.read_text())
    except FileNotFoundError:
        return []
    if size > 2 * cap:
        return [
            f"NOTES | {size} chars, OVER TWICE the {cap}-char cap; paid actions "
            "refuse until trimmed (one page is the contract)"
        ]
    if size > cap:
        return [
            f"NOTES | {size} chars exceeds the {cap}-char cap; trim toward one "
            "page; the block engages at 2× the cap"
        ]
    return []


def view_text(
    run: Run,
    *,
    event_id: int | None = None,
    history: int = 0,
    flags: Mapping[str, Any] | None = None,
) -> str:
    """`assay view`: inspect one event (the observation kind renders its own
    form and honors its own flags), then the history tail."""
    events = run.events
    if not events:
        raise AssayError("timeline is empty")
    index = len(events) - 1 if event_id is None else event_id
    if not 0 <= index < len(events):
        raise AssayError(f"event must be in 0..{len(events) - 1}")
    event = events[index]
    kind = kind_for(event)
    flags = dict(flags or {})
    if kind is not None:
        lines = [kind.view_text(run, index, flags)]
    else:
        lines = [_general_inspect_text(run, index, full=bool(flags.get("grid")))]
        if flags.get("frames") or flags.get("crop") is not None:
            lines.append(
                "NOTE | this run has dict observations; --frames/--crop do not apply"
            )
    if history:
        lines.append("HISTORY | cause -> observed result")
        lines.extend(history_lines(events[: index + 1], history))
    return "\n".join(lines)
