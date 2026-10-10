"""A tiny prediction vocabulary the runtime grades automatically.

Every live action carries a prediction. Structured outcomes are graded against
the settled result; free text is graded as "some visible change". On frame
worlds, coordinates are x=column, y=row. The parser returns `Outcome` records
and the graders return `Grade` records (`records.py`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .core import AssayError
from .extras import ObservationKind, all_kinds, foreign_form, kind_for, refusal_text
from .records import STATE_KINDS, GAMBLE_KINDS, Outcome, Event, Grade, outcome_bucket
from .textobs import changed_count

if TYPE_CHECKING:
    from .run import Run

__all__ = [
    "STATE_KINDS",
    "GAMBLE_KINDS",
    "GENERAL_PREDICTION_HELP",
    "outcome_bucket",
    "prediction_help",
    "grade_general_outcomes",
    "grade_lines",
    "grade_pending",
    "parse_prediction",
]

GENERAL_PREDICTION_HELP = """\
PREDICTION | one or more outcomes, separated by ";"
  noop                 no observed change
  change               the observation changes (free text means this too)
  level+1              this action completes the current progress unit
  win                  this action reaches the goal state
  verify:PATH.py       run your verifier file: def verify(before, after) -> (ok, actual)
  ch NAME = V [± TOL]  a registered state reads V after this action
  ch NAME delta OP V   the state moves by an amount where OP is =, >=, <=
  ch NAME delta sign +|-    the state moves up / down
  ch NAME crosses V [from below|from above]   the state crosses a threshold
Any outcome may end with `@within Ns`; it only grades if the result settles in time.
Addressable states: `goal` and `level` are built in; declare your own with `assay state declare`.
Free text that is not an outcome is kept as commentary. Example:
  --predict "ch counter delta = 1; verify:checks/counter.py"
"""



def prediction_help() -> str:
    """The full help: the general table first (every world), then each
    importable kind's own section."""
    sections = [GENERAL_PREDICTION_HELP, *(kind.prediction_help() for kind in all_kinds())]
    return "\n".join(sections)


_WINDOW = re.compile(r"^(.*\S)\s+@within\s+(\d+(?:\.\d+)?)s$", re.IGNORECASE)
_VALUE = r"(-?\d+(?:\.\d+)?|true|false|\"[^\"]*\"|'[^']*'|[A-Za-z_][A-Za-z0-9_]*)"


def _parse_value(raw: str) -> Any:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if raw[:1] in {"'", '"'} and raw[-1:] == raw[:1] and len(raw) >= 2:
        return raw[1:-1]
    try:
        return int(raw, 10)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("noop", re.compile(r"^noop$", re.IGNORECASE)),
    ("change", re.compile(r"^change$", re.IGNORECASE)),
    ("win", re.compile(r"^win$", re.IGNORECASE)),
    ("level_up", re.compile(r"^level\s*\+\s*1$", re.IGNORECASE)),
    ("verify", re.compile(r"^verify:(\S+)$", re.IGNORECASE)),
    (
        "channel_delta_sign",
        re.compile(
            r"^ch\s+([A-Za-z][A-Za-z0-9_]{0,31})\s+delta\s+sign\s*([+-])$",
            re.IGNORECASE,
        ),
    ),
    (
        "channel_delta",
        re.compile(
            rf"^ch\s+([A-Za-z][A-Za-z0-9_]{{0,31}})\s+delta\s*(=|>=|<=)\s*{_VALUE}$",
            re.IGNORECASE,
        ),
    ),
    (
        "channel_cross",
        re.compile(
            rf"^ch\s+([A-Za-z][A-Za-z0-9_]{{0,31}})\s+crosses\s+{_VALUE}"
            r"(?:\s+from\s+(above|below))?$",
            re.IGNORECASE,
        ),
    ),
    (
        "channel_eq",
        re.compile(
            rf"^ch\s+([A-Za-z][A-Za-z0-9_]{{0,31}})\s*=\s*{_VALUE}"
            r"(?:\s*(?:±|\+-)\s*(\d+(?:\.\d+)?))?$",
            re.IGNORECASE,
        ),
    ),
    (
        "aggregate",
        re.compile(
            rf"^agg\s+ch\s+([A-Za-z][A-Za-z0-9_]{{0,31}})\s+(mean|min|max)"
            rf"\s*(=|>=|<=)\s*{_VALUE}\s+over\s+(\d+)a\s+horizon\s+(\d+)a"
            r"\s+on-fail\s+(advise|revoke_batching)$",
            re.IGNORECASE,
        ),
    ),
)

_KEYWORD = re.compile(
    r"^(noop|change|level|win|verify|ch|agg)\b", re.IGNORECASE
)


PREDICTION_HINT = "`assay act --help` lists the outcome forms"
_COUNT_DIGITS = 9


def _numeric(value: Any, what: str, part: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AssayError(
            f"{what} outcomes need a numeric value: {part!r}", code="PREDICTION_SYNTAX", hint=PREDICTION_HINT
        )
    return value


def _count(raw: str, what: str, part: str) -> int:
    """An aggregate's `over` or `horizon` count. The grammar admits any run of
    digits; a count past nine of them is refused before `int` is asked to
    read it (Python refuses a conversion past its digit limit with a
    ValueError, which is no refusal)."""
    if len(raw) > _COUNT_DIGITS:
        raise AssayError(
            f"aggregate {what} count has {len(raw)} digits, more than {_COUNT_DIGITS}: {part[:80]!r}",
            code="PREDICTION_SYNTAX",
            hint=PREDICTION_HINT,
        )
    return int(raw)


def _core_outcome(name: str, part: str, found: re.Match[str], window_s: float | None) -> Outcome:
    """One core form from its match: the outcome's fields follow the form."""
    if name == "verify":
        return Outcome(kind=name, text=part, window_s=window_s, path=found.group(1))
    if name == "channel_delta_sign":
        return Outcome(
            kind="channel_delta",
            text=part,
            window_s=window_s,
            channel=found.group(1).lower(),
            op="sign",
            sign=found.group(2),
        )
    if name == "channel_delta":
        return Outcome(
            kind=name,
            text=part,
            window_s=window_s,
            channel=found.group(1).lower(),
            op=found.group(2),
            value=_numeric(_parse_value(found.group(3)), "delta", part),
        )
    if name == "channel_cross":
        return Outcome(
            kind=name,
            text=part,
            window_s=window_s,
            channel=found.group(1).lower(),
            value=_numeric(_parse_value(found.group(2)), "crosses", part),
            direction=found.group(3).lower() if found.group(3) else None,
        )
    if name == "channel_eq":
        return Outcome(
            kind=name,
            text=part,
            window_s=window_s,
            channel=found.group(1).lower(),
            value=_parse_value(found.group(2)),
            tol=float(found.group(3)) if found.group(3) is not None else None,
        )
    if name == "aggregate":
        outcome = Outcome(
            kind=name,
            text=part,
            window_s=window_s,
            channel=found.group(1).lower(),
            stat=found.group(2).lower(),
            op=found.group(3),
            value=_numeric(_parse_value(found.group(4)), "aggregate", part),
            over=_count(found.group(5), "over", part),
            horizon=_count(found.group(6), "horizon", part),
            on_fail=found.group(7).lower(),
        )
        assert outcome.over is not None and outcome.horizon is not None
        if outcome.over < 1 or outcome.horizon < 1 or outcome.horizon > 100:
            raise AssayError(
                "aggregate needs over >= 1a and 1a <= horizon <= 100a",
                code="PREDICTION_SYNTAX",
                hint=PREDICTION_HINT,
            )
        return outcome
    return Outcome(kind=name, text=part, window_s=window_s)


def parse_prediction(
    text: str, *, kind: ObservationKind | None = None
) -> list[Outcome]:
    """Parse a prediction string into outcomes; free text implies `change`.

    The core forms parse on every run. An observation kind's own forms (the
    frame world's `cell`, `move`, `vanish`, `region`) parse only when `kind`
    is given, which nothing in 1.2.0 does: such an outcome is refused by name
    before any spend, the rule every published journal was recorded under,
    from the kernel's own table of the forms (`extras.FRAME_FORMS`), so the
    refusal imports neither the extra nor pillow. Admitting them on frame
    worlds is a decision the owner has not made, so no caller passes `kind`.
    """
    extra_patterns = list(kind.outcome_patterns()) if kind is not None else []
    help_text = GENERAL_PREDICTION_HELP + (("\n" + kind.prediction_help()) if kind is not None else "")
    if not text or not text.strip():
        raise AssayError(
            "an empty prediction predicts nothing; say what you expect",
            code="PREDICTION_REQUIRED",
            hint='add --predict "<outcomes>" (for example --predict "change"); `assay act --help` lists the forms',
            detail=help_text,
        )
    outcomes: list[Outcome] = []
    for raw in text.split(";"):
        part = raw.strip()
        if not part:
            continue
        window_s: float | None = None
        windowed = _WINDOW.match(part)
        if windowed:
            part = windowed.group(1)
            window_s = float(windowed.group(2))
            if window_s <= 0:
                raise AssayError(
                    f"@within needs a positive number of seconds: {raw.strip()!r}",
                    code="PREDICTION_SYNTAX",
                    hint=PREDICTION_HINT,
                )
        matched = False
        for name, pattern in _PATTERNS:
            found = pattern.match(part)
            if not found:
                continue
            outcomes.append(_core_outcome(name, part, found, window_s))
            matched = True
            break
        if not matched and kind is not None:
            for name, pattern in extra_patterns:
                found = pattern.match(part)
                if not found:
                    continue
                outcomes.append(
                    Outcome(kind=name, text=part, window_s=window_s, extra=kind.outcome_fields(name, found))
                )
                matched = True
                break
        if not matched:
            foreign = foreign_form(part, kind)
            if foreign is not None:
                raise AssayError(
                    refusal_text(foreign, part), code="PREDICTION_SYNTAX", hint=PREDICTION_HINT, detail=help_text
                )
            if _KEYWORD.match(part):
                raise AssayError(
                    f"malformed outcome {part!r}", code="PREDICTION_SYNTAX", hint=PREDICTION_HINT, detail=help_text
                )
            outcomes.append(Outcome(kind="note", text=part))
    mechanical = [
        outcome for outcome in outcomes if outcome.kind not in {"note", "aggregate"}
    ]
    if any(outcome.kind == "aggregate" for outcome in outcomes) and not mechanical:
        # Statistical outcomes are additive, never substitutive.
        raise AssayError(
            "aggregate outcomes are additive: this action still needs a mechanical outcome of its own",
            code="PREDICTION_SYNTAX",
            hint=PREDICTION_HINT,
            detail=help_text,
        )
    if not mechanical:
        # A prose prediction still commits to a visible effect. Coerced outcomes
        # are journaled as their own kind and excluded from the capability meter.
        outcomes.append(
            Outcome(kind="change", text="change (implied by free text)", coerced=True)
        )
    return outcomes


def grade_general_outcomes(
    outcomes: Sequence[Outcome],
    prior_event: Event,
    event: Event,
) -> list[Grade]:
    """Grade the general outcome forms against dict-shaped observations."""
    before = prior_event.observation or {}
    after = event.observation or {}
    changed = changed_count(before, after)
    level_advanced = event.level_advanced
    graded: list[Grade] = []
    for outcome in outcomes:
        kind = outcome.kind
        if kind == "note":
            continue
        ok = False
        actual = ""
        if kind == "noop":
            ok = changed == 0 and not level_advanced
            if ok:
                actual = "no observed change"
            else:
                actual = (
                    f"{changed} keys changed" if changed else "level advanced"
                )
        elif kind == "change":
            ok = changed > 0 or level_advanced
            if changed:
                actual = f"{changed} keys changed"
            else:
                actual = "level advanced" if level_advanced else "no observed change (0 keys)"
        elif kind == "win":
            ok = str(event.state) == "WIN"
            actual = f"state {event.state}"
        elif kind == "level_up":
            ok = level_advanced
            actual = (
                f"level advanced to {int(event.levels_completed)} completed"
                if level_advanced
                else "level did not advance"
            )
        else:
            actual = "ungradable without a grid observation"
        graded.append(Grade.of(outcome, ok=bool(ok), actual=actual))
    return graded


RECOVERED_WINDOW_ACTUAL = "UNGRADABLE: recovered, step duration unknown"


def grade_pending(
    run: Run,
    outcomes: Sequence[Outcome],
    prior: Event,
    pending: Event,
    *,
    elapsed_s: float | None,
) -> list[Grade]:
    """Grade every outcome of one paid action against its pending event: the
    observation kind's grader or the general one, then states, then
    verifiers. The live path and recovery share it (docs/ARCHITECTURE.md
    section 6.5).

    Aggregate outcomes are NOT graded here; they open at the gate and resolve at
    their horizon. An outcome with a validity window grades only if the result
    settled inside it: `elapsed_s` is the daemon-measured step duration on the
    live path, and None on recovery, where the duration died with the process,
    so every windowed outcome is UNGRADABLE with `RECOVERED_WINDOW_ACTUAL`. A
    late settle is UNGRADABLE too: its own verdict, never a silent pass or
    miss.
    """
    from .states import grade_state_outcome
    from .verifiers import grade_verifier_outcome, observation_view

    graded: list[Grade] = []
    stale: list[Grade] = []
    timely: list[Outcome] = []
    for outcome in outcomes:
        if outcome.kind in {"note", "aggregate"}:
            continue
        window = outcome.window_s
        if window is None:
            timely.append(outcome)
        elif elapsed_s is None:
            stale.append(
                Grade.of(outcome, ok=False, ungradable=True, actual=RECOVERED_WINDOW_ACTUAL)
            )
        elif elapsed_s > float(window):
            stale.append(
                Grade.of(
                    outcome,
                    ok=False,
                    ungradable=True,
                    actual=(
                        f"UNGRADABLE: settled after {elapsed_s:.2f}s, "
                        f"outside the declared {float(window):g}s window"
                    ),
                )
            )
        else:
            timely.append(outcome)
    plain = [
        outcome
        for outcome in timely
        if outcome.kind != "verify" and outcome.kind not in STATE_KINDS
    ]
    kind = kind_for(pending)
    if kind is not None:
        graded.extend(kind.grade_outcomes(plain, prior, pending))
    else:
        graded.extend(grade_general_outcomes(plain, prior, pending))
    graded.extend(
        grade_state_outcome(run, outcome, prior, pending)
        for outcome in timely
        if outcome.kind in STATE_KINDS
    )
    verify_outcomes = [outcome for outcome in timely if outcome.kind == "verify"]
    if verify_outcomes:
        before_view = observation_view(prior)
        after_view = observation_view(pending)
        graded.extend(
            grade_verifier_outcome(run.paths, outcome, before_view, after_view)
            for outcome in verify_outcomes
        )
    graded.extend(stale)
    return graded


def grade_lines(graded: Sequence[Grade]) -> list[str]:
    lines: list[str] = []
    for item in graded:
        if item.invalid or item.ungradable:
            lines.append(f"! {item.text} | {item.actual}")
            continue
        lines.append(
            f"{'✓' if item.ok else '✗'} {item.text}"
            + ("" if item.ok else f" | {item.actual}")
        )
    return lines
