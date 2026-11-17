"""The frontier of a world that scores code, RSI-top's stall rule over it,
and the measures a report quotes.

    python3 -m tools.eval frontier --run-dir DIR [--state-dir DIR] [--frontier FILE] [--json]

A world that grades programs by a cycle count writes one JSON line per
completed evaluation to `<run dir>/.assay/world_state/frontier.jsonl`: `n`
(from 1), `started_at`, `at` (the completion, UTC ISO 8601),
`duration_seconds`, `label`, `code_sha256`, `valid`, `cycles` (lower is
better, null when invalid), `failure_stage`, `error`, `frontier_before`,
`improved`, `best_cycles` (the frontier after) and `trials`. One line is one
attempt, as RSI-top counts them, whatever its outcome. The world writes the
file; this module only reads it, and a torn last line (the world is writing
it) is skipped until it is whole.

The stall rule is the live controller of RSI-top's wrapper
(`breadth_controller.py`), adapted to one sequential player:

- At the first session's launch the run age, the time anchor and the phase
  start: `run_started_at` and `flat_anchor_at` are that moment, the
  progress anchor `anchor_cycles` is the frontier then (the last record's
  `best_cycles`, else unset until a valid record sets it), the phase is
  `ordinary`.
- Each new record, in order: when it is valid and the anchor is unset or
  `anchor_cycles - cycles > relative_gain * abs(anchor_cycles)`, the anchor
  moves to its cycles (a meaningful improvement), and in the `ordinary`
  phase the time anchor moves to its `at`. The progress anchor moves in
  every phase; the time anchor only in `ordinary`.
- After new records (never without one, so a session is ended right after
  an evaluation and never inside one), the rule fires when all hold: the
  phase is `ordinary`; the run is `min_run_age_seconds` old by the wall
  clock; the active seconds since the time anchor reach `flat_seconds`; and
  `min_attempts` records completed at or after the time anchor. Firing
  turns the phase to `intervening` and records an intervention.
- The first session launched after that turns the phase back to `ordinary`
  and moves the time anchor to its launch. Nothing else cools the rule
  down, and an intervention never resets the progress anchor.

Active seconds are the length of the union of the player sessions'
intervals (launch to exit, or to now while one runs) inside `[time anchor,
now]`. A pause for a usage limit, the analyst's call and the gap between
sessions are not active. RSI-top counts the union of its attempts in flight;
with one player the session is the unit that is in flight.

The rule is a reducer, `apply(state, event, rule)`, over the events
`Launched`, `Exited`, `Paused`, `Unpaused`, `Recorded` and `Advised`, with
the phase changed only through `next_phase` (`fired`, `resumed`). It reads
no file and no clock, so the tests drive it with a fake one. The session
runner's supervisor (`supervisor.py`) feeds it and keeps the state in
`<state dir>/stall.json`.

The mode reads the frontier and the stall state and writes nothing:
evaluations, valid ones, the best cycles, `1363 / best` as the normalized
performance, evaluations per hour, the share of time stalled as the
RSI-top project page defines it, the paused time, and per intervention
whether the frontier moved in the next 1200 active seconds.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from . import EvalError
from .session import STATE_SUFFIX

FRONTIER_PATH = Path(".assay") / "world_state" / "frontier.jsonl"
STATE_NAME = "stall.json"
STATE_VERSION = 1
# The cycle count the comparison divides by for its normalized performance.
NORMALIZING_CYCLES = 1363
# The project page's measures: after the first hour, stretches of at least
# 20 minutes in which the best score improved by fewer than 2 cycles.
PAGE_AFTER_SECONDS = 3600.0
PAGE_SEGMENT_SECONDS = 1200.0
PAGE_MIN_GAIN_CYCLES = 2
OUTCOME_WINDOW_SECONDS = 1200.0

Phase = Literal["ordinary", "intervening"]
Interval = tuple[float, float | None]
PHASES: dict[tuple[str, str], Phase] = {
    ("ordinary", "fired"): "intervening",
    ("intervening", "resumed"): "ordinary",
}


def next_phase(phase: Phase, event: Literal["fired", "resumed"]) -> Phase:
    """The phase after `event`; any other pair is a bug in the caller."""
    try:
        return PHASES[(phase, event)]
    except KeyError:
        raise ValueError(f"no transition from {phase} on {event}") from None


# ---- time ---------------------------------------------------------------------------


def epoch(stamp: str) -> float:
    """An ISO 8601 time as seconds since the epoch; a naive one is UTC."""
    moment = dt.datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.timestamp()


def iso(seconds: float) -> str:
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat(timespec="milliseconds")


# ---- the records ------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Evaluation:
    n: int
    at: float
    valid: bool
    cycles: int | None
    best_cycles: int | None
    label: str = ""
    started_at: float | None = None
    duration_seconds: float | None = None
    code_sha256: str | None = None
    failure_stage: str | None = None
    error: str | None = None
    frontier_before: int | None = None
    improved: bool = False
    trials: Any = None

    def row(self) -> dict[str, Any]:
        """The short form a report or the analyst reads."""
        return {
            "n": self.n, "at": iso(self.at), "label": self.label, "valid": self.valid, "cycles": self.cycles,
            "failure_stage": self.failure_stage, "improved": self.improved,
        }


def _whole(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _real(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def evaluation(raw: Any) -> Evaluation:
    """One frontier line as a record; ValueError when it is not one."""
    if not isinstance(raw, Mapping):
        raise ValueError("not an object")
    n = _whole(raw.get("n"))
    if n is None or n < 1:
        raise ValueError("no n")
    if not isinstance(raw.get("at"), str):
        raise ValueError("no at")
    started = raw.get("started_at")
    return Evaluation(
        n=n,
        at=epoch(raw["at"]),
        valid=raw.get("valid") is True,
        cycles=_whole(raw.get("cycles")),
        best_cycles=_whole(raw.get("best_cycles")),
        label=str(raw.get("label") or ""),
        started_at=epoch(started) if isinstance(started, str) else None,
        duration_seconds=_real(raw.get("duration_seconds")),
        code_sha256=_text(raw.get("code_sha256")),
        failure_stage=_text(raw.get("failure_stage")),
        error=_text(raw.get("error")),
        frontier_before=_whole(raw.get("frontier_before")),
        improved=raw.get("improved") is True,
        trials=raw.get("trials"),
    )


def frontier_file(run_dir: Path) -> Path:
    return Path(run_dir) / FRONTIER_PATH


def read_frontier(path: Path) -> list[Evaluation]:
    """The evaluations in order. A missing file is no evaluation yet, and a
    line that does not parse (the last one, while the world writes it) is
    skipped."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    records: list[Evaluation] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(evaluation(json.loads(line)))
        except ValueError:
            continue
    return records


# ---- the rule ----------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StallRule:
    """RSI-top's defaults, from its controller."""

    min_run_age_seconds: float = 3600.0
    flat_seconds: float = 1200.0
    min_attempts: int = 12
    relative_gain: float = 0.002


@dataclasses.dataclass(frozen=True)
class Intervention:
    index: int
    fired_at: float
    record_n: int
    anchor_cycles: int | None
    active_seconds: float
    attempts: int
    # What the analyst returned, as `a<k>.json` holds it in short: the
    # directions, the reason there are none, the record's file name.
    advice: Mapping[str, Any] | None = None


@dataclasses.dataclass(frozen=True)
class StallState:
    run_started_at: float
    flat_anchor_at: float
    anchor_cycles: int | None
    anchor_n: int
    phase: Phase
    consumed: int
    sessions: tuple[Interval, ...] = ()
    pauses: tuple[Interval, ...] = ()
    interventions: tuple[Intervention, ...] = ()


@dataclasses.dataclass(frozen=True)
class Launched:
    at: float


@dataclasses.dataclass(frozen=True)
class Exited:
    at: float


@dataclasses.dataclass(frozen=True)
class Paused:
    at: float


@dataclasses.dataclass(frozen=True)
class Unpaused:
    at: float


@dataclasses.dataclass(frozen=True)
class Recorded:
    at: float
    records: tuple[Evaluation, ...]


@dataclasses.dataclass(frozen=True)
class Advised:
    index: int
    advice: Mapping[str, Any]


Event = Launched | Exited | Paused | Unpaused | Recorded | Advised


def begin(at: float, records: Sequence[Evaluation]) -> StallState:
    """The state at the first session's launch, with that session open."""
    last = records[-1] if records else None
    return StallState(
        run_started_at=at, flat_anchor_at=at, anchor_cycles=last.best_cycles if last else None,
        anchor_n=last.n if last else 0, phase="ordinary", consumed=last.n if last else 0, sessions=((at, None),),
    )


def _close(intervals: tuple[Interval, ...], at: float) -> tuple[Interval, ...]:
    if intervals and intervals[-1][1] is None:
        start = intervals[-1][0]
        return (*intervals[:-1], (start, max(start, at)))
    return intervals


def _open(intervals: tuple[Interval, ...], at: float) -> tuple[Interval, ...]:
    return (*_close(intervals, at), (at, None))


def union_seconds(intervals: Iterable[Interval], start: float, end: float) -> float:
    """The length of the union of the intervals (an open one ends at `end`)
    inside `[start, end]`."""
    clipped = sorted((max(a, start), min(end if b is None else b, end)) for a, b in intervals)
    total, reach = 0.0, start
    for a, b in clipped:
        a = max(a, reach)
        if b > a:
            total += b - a
            reach = b
    return total


def attempts_since(records: Iterable[Evaluation], at: float) -> int:
    return sum(1 for record in records if record.at >= at)


def meaningful(anchor: int | None, record: Evaluation, gain: float) -> bool:
    if not record.valid or record.cycles is None:
        return False
    return anchor is None or anchor - record.cycles > gain * abs(anchor)


def _consume(state: StallState, event: Recorded, rule: StallRule) -> StallState:
    fresh = [record for record in event.records if record.n > state.consumed]
    if not fresh:
        return state
    anchor, anchor_n, flat = state.anchor_cycles, state.anchor_n, state.flat_anchor_at
    for record in fresh:
        if meaningful(anchor, record, rule.relative_gain):
            anchor, anchor_n = record.cycles, record.n
            if state.phase == "ordinary":
                flat = record.at
    state = dataclasses.replace(
        state, anchor_cycles=anchor, anchor_n=anchor_n, flat_anchor_at=flat, consumed=fresh[-1].n,
    )
    attempts = attempts_since(event.records, flat)
    active = union_seconds(state.sessions, flat, event.at)
    if (
        state.phase == "ordinary"
        and event.at - state.run_started_at >= rule.min_run_age_seconds
        and active >= rule.flat_seconds
        and attempts >= rule.min_attempts
    ):
        fired = Intervention(
            index=len(state.interventions) + 1, fired_at=event.at, record_n=fresh[-1].n, anchor_cycles=anchor,
            active_seconds=round(active, 3), attempts=attempts,
        )
        state = dataclasses.replace(
            state, phase=next_phase(state.phase, "fired"), interventions=(*state.interventions, fired),
        )
    return state


def apply(state: StallState, event: Event, rule: StallRule) -> StallState:
    """The state after one event. Returns `state` itself when nothing changed."""
    match event:
        case Launched(at=at):
            if state.phase == "intervening":
                state = dataclasses.replace(state, phase=next_phase(state.phase, "resumed"), flat_anchor_at=at)
            return dataclasses.replace(state, sessions=_open(state.sessions, at))
        case Exited(at=at):
            return dataclasses.replace(state, sessions=_close(state.sessions, at))
        case Paused(at=at):
            return dataclasses.replace(state, pauses=_open(state.pauses, at))
        case Unpaused(at=at):
            return dataclasses.replace(state, pauses=_close(state.pauses, at))
        case Recorded():
            return _consume(state, event, rule)
        case Advised(index=index, advice=advice):
            return dataclasses.replace(state, interventions=tuple(
                dataclasses.replace(item, advice=dict(advice)) if item.index == index else item
                for item in state.interventions
            ))
    raise TypeError(f"not an event: {event!r}")


def newly_fired(before: StallState, after: StallState) -> Intervention | None:
    return after.interventions[-1] if len(after.interventions) > len(before.interventions) else None


def close_open(state: StallState, at: float) -> StallState:
    """The intervals a killed runner left open, closed at `at`."""
    return dataclasses.replace(state, sessions=_close(state.sessions, at), pauses=_close(state.pauses, at))


def has_open(state: StallState) -> bool:
    return any(intervals and intervals[-1][1] is None for intervals in (state.sessions, state.pauses))


def standing(state: StallState) -> Intervention | None:
    """The latest intervention when no meaningful improvement came after
    it: the stall the next prompt should mention."""
    if not state.interventions:
        return None
    latest = state.interventions[-1]
    return latest if latest.record_n >= state.anchor_n else None


def standing_advice(state: StallState) -> Intervention | None:
    """The latest intervention since the last meaningful improvement whose
    analyst returned directions."""
    advised = [
        item for item in state.interventions
        if item.record_n >= state.anchor_n and item.advice and item.advice.get("directions")
    ]
    return advised[-1] if advised else None


# ---- the state file -----------------------------------------------------------------------


def _interval_json(interval: Interval) -> list[str | None]:
    return [iso(interval[0]), None if interval[1] is None else iso(interval[1])]


def _interval(raw: Any) -> Interval:
    start, end = raw
    return (epoch(start), None if end is None else epoch(end))


def state_json(state: StallState) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "run_started_at": iso(state.run_started_at),
        "anchor_cycles": state.anchor_cycles,
        "anchor_n": state.anchor_n,
        "flat_anchor_at": iso(state.flat_anchor_at),
        "phase": state.phase,
        "consumed": state.consumed,
        "sessions": [_interval_json(item) for item in state.sessions],
        "pauses": [_interval_json(item) for item in state.pauses],
        "interventions": [
            {
                "index": item.index, "fired_at": iso(item.fired_at), "record_n": item.record_n,
                "anchor_cycles": item.anchor_cycles, "active_seconds": item.active_seconds,
                "attempts": item.attempts, "advice": None if item.advice is None else dict(item.advice),
            }
            for item in state.interventions
        ],
    }


def state_from_json(raw: Any) -> StallState:
    """ValueError when `raw` is not a stall state this version writes."""
    try:
        if raw.get("version") != STATE_VERSION or raw["phase"] not in ("ordinary", "intervening"):
            raise ValueError("version or phase")
        return StallState(
            run_started_at=epoch(raw["run_started_at"]),
            flat_anchor_at=epoch(raw["flat_anchor_at"]),
            anchor_cycles=_whole(raw["anchor_cycles"]),
            anchor_n=int(raw["anchor_n"]),
            phase=raw["phase"],
            consumed=int(raw["consumed"]),
            sessions=tuple(_interval(item) for item in raw["sessions"]),
            pauses=tuple(_interval(item) for item in raw["pauses"]),
            interventions=tuple(
                Intervention(
                    index=int(item["index"]), fired_at=epoch(item["fired_at"]), record_n=int(item["record_n"]),
                    anchor_cycles=_whole(item["anchor_cycles"]), active_seconds=float(item["active_seconds"]),
                    attempts=int(item["attempts"]), advice=item.get("advice"),
                )
                for item in raw["interventions"]
            ),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(str(error)) from None


def load_state(path: Path) -> StallState | None:
    """The saved state, None before the first launch; EvalError when the
    file is there and unreadable."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None
    try:
        return state_from_json(json.loads(text))
    except ValueError as error:
        raise EvalError(f"{path} is not a stall state of version {STATE_VERSION} ({error}); move it aside") from None


def save_state(path: Path, state: StallState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state_json(state), indent=2) + "\n")
    os.replace(temporary, path)


# ---- the measures -------------------------------------------------------------------------


def best_curve(records: Sequence[Evaluation]) -> list[tuple[float, int]]:
    """The frontier as steps, (time, best cycles): the first record's
    `frontier_before` at its start, then each completion that lowered it."""
    points: list[tuple[float, int]] = []
    best: int | None = None
    if records and records[0].frontier_before is not None:
        best = records[0].frontier_before
        first = records[0]
        points.append((first.started_at if first.started_at is not None else first.at, best))
    for record in records:
        value = record.best_cycles
        if value is None and record.valid:
            value = record.cycles
        if value is not None and (best is None or value < best):
            best = value
            points.append((record.at, best))
    return points


def best_at(curve: Sequence[tuple[float, int]], at: float) -> int | None:
    value = None
    for moment, best in curve:
        if moment > at:
            break
        value = best
    return value


def stalled_fraction(
    records: Sequence[Evaluation],
    start: float,
    end: float,
    pauses: Iterable[Interval],
    *,
    after_seconds: float = PAGE_AFTER_SECONDS,
    segment_seconds: float = PAGE_SEGMENT_SECONDS,
    min_gain_cycles: int = PAGE_MIN_GAIN_CYCLES,
) -> dict[str, Any]:
    """The project page's measure: after the first hour of the run, the
    share of the time that lies in stretches of at least `segment_seconds`
    in which the best score improved by fewer than `min_gain_cycles`. The
    paused intervals come out of the clock before anything is measured, so
    they are in neither the numerator nor the denominator; their length is
    reported beside the share."""
    pauses = list(pauses)

    def unpaused(moment: float) -> float:
        moment = min(max(moment, start), end)
        return (moment - start) - union_seconds(pauses, start, moment)

    total = unpaused(end)
    window_start = unpaused(start + after_seconds)
    paused = union_seconds(pauses, start, end)
    window = total - window_start
    curve = best_curve(records)
    steps: list[tuple[float, int | None]] = [(0.0, best_at(curve, start))]
    steps += [(unpaused(moment), best) for moment, best in curve if start < moment <= end]
    stretches: list[Interval] = []
    for index, (begun, best) in enumerate(steps):
        ended = total
        for moment, later in steps[index + 1:]:
            if later is not None and (best is None or best - later >= min_gain_cycles):
                ended = moment
                break
        if ended - begun >= segment_seconds:
            stretches.append((begun, ended))
    stalled = union_seconds(stretches, window_start, total) if window > 0 else 0.0
    return {
        "fraction": round(stalled / window, 4) if window > 0 else None,
        "stalled_seconds": round(stalled, 1),
        "window_seconds": round(max(window, 0.0), 1),
        "paused_seconds": round(paused, 1),
    }


def after_active(intervals: Iterable[Interval], start: float, seconds: float, now: float) -> float | None:
    """The moment the sessions have been active `seconds` since `start`, or
    None when they have not been by `now`."""
    clipped = sorted((max(a, start), min(now if b is None else b, now)) for a, b in intervals)
    total, reach = 0.0, start
    for a, b in clipped:
        a = max(a, reach)
        if b <= a:
            continue
        if total + (b - a) >= seconds:
            return a + (seconds - total)
        total += b - a
        reach = b
    return None


def intervention_outcomes(
    state: StallState,
    records: Sequence[Evaluation],
    now: float,
    rule: StallRule,
    *,
    window_seconds: float = OUTCOME_WINDOW_SECONDS,
    min_gain_cycles: int = PAGE_MIN_GAIN_CYCLES,
) -> list[dict[str, Any]]:
    """Per intervention, whether the frontier moved within the next
    `window_seconds` of active time: by `min_gain_cycles` (the page's
    measure) and by the rule's relative gain. None while the window is open
    and the frontier has not moved yet."""
    curve = best_curve(records)
    out = []
    for item in state.interventions:
        closes = after_active(state.sessions, item.fired_at, window_seconds, now)
        before = best_at(curve, item.fired_at)
        after = best_at(curve, now if closes is None else closes)
        gain = before - after if before is not None and after is not None else 0
        closed = closes is not None
        directions = (item.advice or {}).get("directions") or []
        out.append({
            "index": item.index, "fired_at": iso(item.fired_at), "record_n": item.record_n,
            "anchor_cycles": item.anchor_cycles, "active_seconds": item.active_seconds, "attempts": item.attempts,
            "directions": len(directions), "best_at_fire": before, "best_after": after, "window_closed": closed,
            "moved_by_cycles": _verdict(gain >= min_gain_cycles, closed),
            "moved_by_rule": _verdict(before is not None and gain > rule.relative_gain * abs(before), closed),
        })
    return out


def _verdict(moved: bool, closed: bool) -> bool | None:
    return True if moved else (False if closed else None)


# ---- the mode -------------------------------------------------------------------------------


def summary(run_dir: Path, state_dir: Path, frontier: Path, now: float) -> dict[str, Any]:
    records = read_frontier(frontier)
    state = load_state(state_dir / STATE_NAME)
    valid = [record for record in records if record.valid and record.cycles is not None]
    curve = best_curve(records)
    best = curve[-1][1] if curve else None
    if state is not None:
        start = state.run_started_at
    elif records:
        start = records[0].started_at if records[0].started_at is not None else records[0].at
    else:
        start = now
    if state is not None and has_open(state):
        end = now
    else:
        ends = [record.at for record in records[-1:]]
        ends += [b for _, b in (state.sessions if state else ()) if b is not None]
        end = max(ends, default=start)
    hours = (end - start) / 3600.0
    pauses = state.pauses if state else ()
    return {
        "run": str(run_dir),
        "frontier": str(frontier),
        "state": str(state_dir / STATE_NAME) if state else None,
        "evaluations": len(records),
        "valid": len(valid),
        "best_cycles": best,
        "normalized": round(NORMALIZING_CYCLES / best, 4) if best else None,
        "started": iso(start),
        "ended": iso(end),
        "per_hour": round(len(records) / hours, 2) if hours > 0 else None,
        "stalled": stalled_fraction(records, start, end, pauses),
        "phase": state.phase if state else None,
        "anchor_cycles": state.anchor_cycles if state else None,
        "interventions": intervention_outcomes(state, records, now, StallRule()) if state else [],
    }


def _yes(value: bool | None) -> str:
    return "open" if value is None else ("yes" if value else "no")


def lines(found: Mapping[str, Any]) -> list[str]:
    best = found["best_cycles"]
    stalled = found["stalled"]
    share = "n/a" if stalled["fraction"] is None else f"{stalled['fraction'] * 100:.1f} percent"
    out = [
        f"FRONTIER | {Path(found['run']).name} | {found['evaluations']} evaluation(s), {found['valid']} valid | "
        + (f"best {best} cycles | normalized {found['normalized']}" if best else "no best yet")
        + f" | {found['per_hour'] if found['per_hour'] is not None else 'n/a'} per hour",
        f"STALLED | {share} of {stalled['window_seconds'] / 60:.1f} min after the first hour | "
        f"paused {stalled['paused_seconds'] / 60:.1f} min",
    ]
    for item in found["interventions"]:
        out.append(
            f"INTERVENTION | {item['index']} | {item['fired_at']} at evaluation {item['record_n']} | anchor "
            f"{item['anchor_cycles']} cycles | {item['directions']} direction(s) | moved by "
            f"{PAGE_MIN_GAIN_CYCLES} cycles: {_yes(item['moved_by_cycles'])} | by the rule: {_yes(item['moved_by_rule'])}"
        )
    return out


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.eval frontier",
        description="The frontier of a run whose world records evaluations, and the stall rule's interventions.",
    )
    parser.add_argument("--run-dir", type=Path, required=True, metavar="DIR")
    parser.add_argument("--state-dir", type=Path, metavar="DIR", help="the session runner's state (default: <run dir>.sessions)")
    parser.add_argument("--frontier", type=Path, metavar="FILE", help=f"the frontier (default: <run dir>/{FRONTIER_PATH})")
    parser.add_argument("--json", action="store_true", help="the summary as one JSON object")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    run_dir = args.run_dir.expanduser().resolve()
    try:
        if not run_dir.is_dir():
            raise EvalError(f"{run_dir} is not a directory")
        state_dir = args.state_dir.expanduser().resolve() if args.state_dir else run_dir.with_name(run_dir.name + STATE_SUFFIX)
        frontier = args.frontier.expanduser().resolve() if args.frontier else frontier_file(run_dir)
        found = summary(run_dir, state_dir, frontier, time.time())
    except EvalError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(found, indent=2) if args.json else "\n".join(lines(found)))
    return 0
