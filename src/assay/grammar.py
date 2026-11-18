"""The prediction language.

A prediction is one or more outcomes separated by `;`. Each outcome is a
predicate over one transition, graded in code (`grade`). The forms are fixed:
what they cannot say, a verifier program says. Free text that is no outcome
is kept as commentary, and a prediction with no gradable outcome is coerced to
`change`: the gate blocks an empty prediction, not a vague one, and the
specificity meter prices the vagueness.

The journal keeps the spelling of its first release for a state: the keyword
`ch`, the field `channel` and the kinds `channel_eq`, `channel_delta` and
`channel_cross`.
"""

from __future__ import annotations

import re
from typing import Any

from . import AssayError

Outcome = dict[str, Any]

HELP = """\
PREDICTION | one or more outcomes, separated by ";"
  noop                 no observed change
  change               the observation changes (free text means this too)
  level+1              this action completes the current progress unit
  win                  this action reaches the goal state
  verify:PATH.py       run your verifier file: def verify(before, after) -> (ok, actual)
  ch NAME = V [± TOL]  a declared state reads V after this action
  ch NAME delta OP V   the state moves by an amount, where OP is =, >=, <=
  ch NAME delta sign +|-    the state moves up / down
  ch NAME crosses V [from below|from above]   the state crosses a threshold
Any outcome may end with `@within Ns`: it grades only if the result settles in time.
States: `goal`, `level` and `budget_remaining` are built in; declare your own with `assay state declare`.
Example: --predict "ch counter delta = 1; verify:checks/counter.py\""""

STATE_KINDS = frozenset({"channel_eq", "channel_delta", "channel_cross"})
_STATE = r"ch\s+([A-Za-z][A-Za-z0-9_]{0,31})"
_VALUE = r"(-?\d+(?:\.\d+)?|true|false|\"[^\"]*\"|'[^']*'|[A-Za-z_][A-Za-z0-9_]*)"
_WINDOW = re.compile(r"^(.*\S)\s+@within\s+(\d+(?:\.\d+)?)s$", re.IGNORECASE)
_KEYWORD = re.compile(r"^(noop|change|level|win|verify|ch)\b", re.IGNORECASE)
_FORMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("noop", re.compile(r"^noop$", re.IGNORECASE)),
    ("change", re.compile(r"^change$", re.IGNORECASE)),
    ("win", re.compile(r"^win$", re.IGNORECASE)),
    ("level_up", re.compile(r"^level\s*\+\s*1$", re.IGNORECASE)),
    ("verify", re.compile(r"^verify:(\S+)$", re.IGNORECASE)),
    ("delta_sign", re.compile(rf"^{_STATE}\s+delta\s+sign\s*([+-])$", re.IGNORECASE)),
    ("channel_delta", re.compile(rf"^{_STATE}\s+delta\s*(=|>=|<=)\s*{_VALUE}$", re.IGNORECASE)),
    ("channel_cross", re.compile(rf"^{_STATE}\s+crosses\s+{_VALUE}(?:\s+from\s+(above|below))?$", re.IGNORECASE)),
    ("channel_eq", re.compile(rf"^{_STATE}\s*=\s*{_VALUE}(?:\s*(?:±|\+-)\s*(\d+(?:\.\d+)?))?$", re.IGNORECASE)),
)


def _syntax(message: str) -> AssayError:
    return AssayError(f"{message}\n{HELP}", code="PREDICTION_SYNTAX", hint="`assay act --help` lists the outcome forms")


def _value(raw: str) -> Any:
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    if raw[0] in "'\"":
        return raw[1:-1]
    for read in (int, float):
        try:
            return read(raw)
        except ValueError:
            pass
    return raw


def _number(raw: str, part: str) -> int | float:
    value = _value(raw)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _syntax(f"a delta or a crossing needs a number: {part!r}")
    return value


def _outcome(form: str, part: str, found: re.Match[str]) -> Outcome:
    if form == "verify":
        return {"kind": form, "text": part, "path": found.group(1)}
    if form in ("noop", "change", "win", "level_up"):
        return {"kind": form, "text": part}
    outcome: Outcome = {"text": part, "channel": found.group(1).lower()}
    if form == "delta_sign":
        return {**outcome, "kind": "channel_delta", "op": "sign", "sign": found.group(2)}
    if form == "channel_delta":
        return {**outcome, "kind": form, "op": found.group(2), "value": _number(found.group(3), part)}
    if form == "channel_cross":
        outcome.update(kind=form, value=_number(found.group(2), part))
        if found.group(3):
            outcome["direction"] = found.group(3).lower()
        return outcome
    outcome.update(kind=form, value=_value(found.group(2)))
    if found.group(3) is not None:
        outcome["tol"] = float(found.group(3))
    return outcome


def parse(text: str | None) -> list[Outcome]:
    """A prediction as its outcomes. Refuses an empty prediction and a
    malformed outcome; free text becomes a note, and `change` is appended,
    marked coerced, when no gradable outcome remains."""
    if not text or not text.strip():
        raise AssayError(
            f"an empty prediction predicts nothing; say what you expect\n{HELP}",
            code="PREDICTION_REQUIRED",
            hint='add --predict "<outcomes>", for example --predict "change"',
        )
    outcomes: list[Outcome] = []
    for raw in text.split(";"):
        part = raw.strip()
        if not part:
            continue
        window: float | None = None
        windowed = _WINDOW.match(part)
        if windowed:
            part, window = windowed.group(1), float(windowed.group(2))
            if window <= 0:
                raise _syntax(f"@within needs a positive number of seconds: {raw.strip()!r}")
        for form, pattern in _FORMS:
            found = pattern.match(part)
            if found:
                outcome = _outcome(form, part, found)
                if window is not None:
                    outcome["window_s"] = window
                outcomes.append(outcome)
                break
        else:
            if _KEYWORD.match(part):
                raise _syntax(f"malformed outcome {part!r}")
            outcomes.append({"kind": "note", "text": part})
    if not outcomes:
        return parse(None)
    if all(outcome["kind"] == "note" for outcome in outcomes):
        outcomes.append({"kind": "change", "text": "change (implied by free text)", "coerced": True})
    return outcomes


def bucket(outcome: Outcome) -> str:
    """Which miss rate an outcome counts toward: `gamble` for a prediction of
    progress (`win`, `level+1`, an outcome on `goal` or `level`),
    `world_model` for a prediction of mechanics."""
    if outcome["kind"] in ("win", "level_up") or outcome.get("channel") in ("goal", "level"):
        return "gamble"
    return "world_model"
