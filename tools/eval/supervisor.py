"""The session runner's stall supervisor: the frontier's stall rule
(`frontier.py`) and the analyst (`analyst.py`) behind one object.

The runner tells it of each player session's launch and exit and of each
usage-limit pause, asks it on each poll whether the rule fired (so the
session ends), lets it consume the records and call the analyst before a
launch, and asks it for the block the next prompt carries. Its state is
`<state dir>/stall.json`, so a runner started again on the same state
directory continues the rule where the last one left it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .analyst import AnalystConfig, advice, bundle, call, context_files
from .diagnose import LAYERS
from .frontier import (
    STATE_NAME, Advised, Exited, Intervention, Launched, Paused, Recorded, StallRule, StallState, Unpaused, apply,
    begin, close_open, has_open, load_state, newly_fired, read_frontier, save_state, standing, standing_advice,
)

DIRECTIONS_HEADER = (
    "Directions from the analyst (a separate model read this run's record after the best score stopped "
    "improving; this is advice, not an observation of the world):"
)


class Supervisor:
    def __init__(
        self,
        *,
        run_dir: Path,
        state_dir: Path,
        frontier: Path,
        rule: StallRule,
        analyst: AnalystConfig | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.run_dir = run_dir
        self.state_dir = state_dir
        self.frontier = frontier
        self.rule = rule
        self.analyst = analyst
        self.clock = clock
        self.state: StallState | None = None
        self.analyst_version = ""

    @property
    def path(self) -> Path:
        return self.state_dir / STATE_NAME

    @property
    def analyst_dir(self) -> Path:
        return self.state_dir / "analyst"

    def check(self) -> None:
        """The refusals before anything runs: a stall state this version
        cannot read, the analyst's prompt and host."""
        load_state(self.path)
        if self.analyst is not None:
            self.analyst.template_text()
            self.analyst_version = self.analyst.version()

    def describe(self) -> str:
        rule = self.rule
        who = (
            f"analyst {self.analyst.host} {self.analyst_version}".rstrip() + f", model {self.analyst.model or 'the host default'}"
            if self.analyst else "no analyst"
        )
        return (
            f"TRIGGER | the stall rule: {rule.min_attempts} attempts and {rule.flat_seconds / 60:g} active min with no gain over "
            f"{rule.relative_gain * 100:g} percent, after {rule.min_run_age_seconds / 60:g} min | {who} | {self.frontier}"
        )

    def load(self) -> None:
        state = load_state(self.path)
        if state is not None and has_open(state):
            # A runner that was killed left its session (or pause) open. The
            # state file is written at every launch, exit and consumed
            # record, so its last write is the latest moment the runner is
            # known to have been alive; closing there under-counts active
            # time rather than inventing it.
            state = close_open(state, self.path.stat().st_mtime)
            save_state(self.path, state)
        self.state = state

    def _set(self, state: StallState) -> None:
        if state is not self.state:
            self.state = state
            save_state(self.path, state)

    # ---- told by the runner ------------------------------------------------------------
    def launched(self) -> None:
        at = self.clock()
        if self.state is None:
            self._set(begin(at, read_frontier(self.frontier)))
        else:
            self._set(apply(self.state, Launched(at), self.rule))

    def exited(self) -> None:
        if self.state is not None:
            self._set(apply(self.state, Exited(self.clock()), self.rule))

    def paused(self) -> None:
        if self.state is not None:
            self._set(apply(self.state, Paused(self.clock()), self.rule))

    def unpaused(self) -> None:
        if self.state is not None:
            self._set(apply(self.state, Unpaused(self.clock()), self.rule))

    # ---- asked by the runner -----------------------------------------------------------
    def poll(self, session: int) -> str | None:
        """Consume the new records; the STALL line when the rule fired."""
        if self.state is None:
            return None
        before = self.state
        self._set(apply(before, Recorded(self.clock(), tuple(read_frontier(self.frontier))), self.rule))
        fired = newly_fired(before, self.state)
        if fired is None:
            return None
        return (
            f"STALL | session {session} | evaluation {fired.record_n} | anchor {fired.anchor_cycles} cycles | "
            f"{fired.active_seconds / 60:.1f} active min | {fired.attempts} attempts since the anchor"
        )

    def awaiting(self) -> Intervention | None:
        """The intervention the analyst has not answered yet."""
        if self.analyst is None or self.state is None or not self.state.interventions:
            return None
        latest = self.state.interventions[-1]
        return latest if latest.advice is None else None

    def before_launch(
        self, session: int, *, seconds_left: float, env: Mapping[str, str], stop: Callable[[], bool],
    ) -> list[str]:
        """The rule once over the records that came while nothing polled,
        then the analyst for an intervention it has not answered. Returns
        the lines to print."""
        said = [line for line in [self.poll(session)] if line]
        pending = self.awaiting()
        if pending is None or self.analyst is None or self.state is None or seconds_left <= 0:
            return said
        records = read_frontier(self.frontier)
        data = bundle(self.run_dir, sessions_dir=self.state_dir, records=records, state=self.state, intervention=pending)
        if self.analyst.context:
            data["context"] = context_files(self.analyst.context)
        record = call(
            self.analyst, index=pending.index, fired_at=pending.fired_at, data=data, directory=self.analyst_dir,
            env=env, timeout_seconds=min(self.analyst.timeout_seconds, seconds_left),
            version=self.analyst_version, stop=stop,
        )
        if not record["interrupted"]:
            self._set(apply(self.state, Advised(pending.index, advice(record)), self.rule))
        cost = f"${record['dollars']:.2f}" if record["dollars"] is not None else "cost not reported"
        line = (
            f"ANALYST | call {pending.index} | {len(record['directions'])} direction(s) | {record['wall_seconds']:g} s | {cost}"
        )
        said.append(line + (f" | {record['reason']}" if record["reason"] else ""))
        return said

    def note(self) -> str | None:
        """The block the next prompt carries: the latest directions, or,
        without them, one line saying the frontier is flat."""
        if self.state is None or (latest := standing(self.state)) is None:
            return None
        advised = standing_advice(self.state)
        if advised is not None and advised.advice is not None:
            lines = [DIRECTIONS_HEADER]
            for item in advised.advice["directions"]:
                lines.append(f"{item['rank']}. Try: {item['try']}")
                lines.append(f"   Why: {item['why']}")
                lines.append(f"   Layer {item['layer']}: {LAYERS[item['layer']]}")
            return "\n".join(lines)
        return (
            f"A note from the session runner (it is not from the world): the best score has not improved by more "
            f"than {self.rule.relative_gain * 100:g} percent over the last {latest.attempts} evaluations and "
            f"{latest.active_seconds / 60:.0f} active minutes."
        )
