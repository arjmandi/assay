"""A tiny prediction vocabulary the runtime grades automatically.

Every live action carries a prediction. Structured claims are graded against
the settled result; free text is graded as "some visible change". On frame
worlds, coordinates are x=column, y=row.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .core import AssayError
from .extras import ObservationKind, all_kinds, kind_for
from .textobs import changed_count

GAMBLE_KINDS = {"win", "level_up"}
CHANNEL_KINDS = {"channel_eq", "channel_delta", "channel_cross"}
_MILESTONE = {"goal", "level"}  # channel claims here gamble; the rest world-model

GENERAL_CLAIMS_HELP = """\
PREDICTION CLAIMS | separate several with ";"
  noop                 no observed change
  change               the observation changes (free text means this too)
  level+1              this action completes the current progress unit
  win                  this action reaches the goal state
  verify:PATH.py       run your verifier file: def verify(before, after) -> (ok, actual)
  ch NAME = V [± TOL]  a registered channel reads V after this action
  ch NAME delta OP V   the channel moves by an amount where OP is =, >=, <=
  ch NAME delta sign +|-    the channel moves up / down
  ch NAME crosses V [from below|from above]   the channel crosses a threshold
Any claim may end with `@within Ns` — it only grades if the result settles in time.
Channels: `goal` and `level` are built in; declare your own with `assay channel declare`.
Free text that is not a claim is kept as commentary. Example:
  --predict "ch counter delta = 1; verify:checks/counter.py"
"""



def claims_help() -> str:
    """The full help: the general table first (every world), then each
    importable kind's own section."""
    sections = [GENERAL_CLAIMS_HELP, *(kind.claims_help() for kind in all_kinds())]
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


def claim_bucket(kind: str, channel: str | None = None) -> str:
    """Claim taxonomy: goal/milestone claims gamble, the rest world-model."""
    if kind in CHANNEL_KINDS:
        return "gamble" if channel in _MILESTONE else "world_model"
    if kind == "aggregate":
        return "aggregate"
    return "gamble" if kind in GAMBLE_KINDS else "world_model"


def parse_claims(
    text: str, *, kind: ObservationKind | None = None
) -> list[dict[str, Any]]:
    """Parse a prediction string into claims; free text implies `change`.

    The core forms parse on every run. An observation kind's own forms (the
    frame world's `cell`, `move`, `vanish`, `region`) parse only when `kind`
    is given, which nothing in 1.1.0 does: such a claim is refused by name
    before any spend, the rule every published journal was recorded under
    (owner decision O1).
    """
    extra_patterns = list(kind.claim_patterns()) if kind is not None else []
    help_text = GENERAL_CLAIMS_HELP + (("\n" + kind.claims_help()) if kind is not None else "")
    if not text or not text.strip():
        raise AssayError(
            f"an empty prediction predicts nothing; say what you expect\n{help_text}"
        )
    claims: list[dict[str, Any]] = []
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
                raise AssayError(f"@within needs a positive number of seconds: {raw.strip()!r}")
        matched = False
        for name, pattern in _PATTERNS:
            found = pattern.match(part)
            if not found:
                continue
            claim: dict[str, Any] = {"kind": name, "text": part}
            if window_s is not None:
                claim["window_s"] = window_s
            if name == "verify":
                claim["path"] = found.group(1)
            elif name == "channel_delta_sign":
                claim.update(
                    kind="channel_delta",
                    channel=found.group(1).lower(),
                    op="sign",
                    sign=found.group(2),
                )
            elif name == "channel_delta":
                claim.update(
                    channel=found.group(1).lower(),
                    op=found.group(2),
                    value=_parse_value(found.group(3)),
                )
                if not isinstance(claim["value"], (int, float)) or isinstance(
                    claim["value"], bool
                ):
                    raise AssayError(f"delta claims need a numeric value: {part!r}")
            elif name == "channel_cross":
                claim.update(
                    channel=found.group(1).lower(),
                    value=_parse_value(found.group(2)),
                )
                if not isinstance(claim["value"], (int, float)) or isinstance(
                    claim["value"], bool
                ):
                    raise AssayError(f"crosses claims need a numeric value: {part!r}")
                if found.group(3):
                    claim["direction"] = found.group(3).lower()
            elif name == "channel_eq":
                claim.update(
                    channel=found.group(1).lower(),
                    value=_parse_value(found.group(2)),
                )
                if found.group(3) is not None:
                    claim["tol"] = float(found.group(3))
            elif name == "aggregate":
                claim.update(
                    channel=found.group(1).lower(),
                    stat=found.group(2).lower(),
                    op=found.group(3),
                    value=_parse_value(found.group(4)),
                    over=int(found.group(5)),
                    horizon=int(found.group(6)),
                    on_fail=found.group(7).lower(),
                )
                if not isinstance(claim["value"], (int, float)) or isinstance(
                    claim["value"], bool
                ):
                    raise AssayError(f"aggregate claims need a numeric value: {part!r}")
                if claim["over"] < 1 or claim["horizon"] < 1 or claim["horizon"] > 100:
                    raise AssayError(
                        "aggregate needs over >= 1a and 1a <= horizon <= 100a"
                    )
            claims.append(claim)
            matched = True
            break
        if not matched:
            for name, pattern in extra_patterns:
                found = pattern.match(part)
                if not found:
                    continue
                claim = {"kind": name, "text": part, **kind.claim_fields(name, found)}
                if window_s is not None:
                    claim["window_s"] = window_s
                claims.append(claim)
                matched = True
                break
        if not matched:
            for other in all_kinds():
                if other is kind:
                    continue
                if any(pattern.match(part) for _, pattern in other.claim_patterns()):
                    raise AssayError(
                        f"claim {part!r} is a {other.name}-world form and this run does "
                        f"not admit it (frame-world forms are not admitted in 1.1.0)\n"
                        f"{help_text}"
                    )
            if _KEYWORD.match(part):
                raise AssayError(f"malformed claim {part!r}\n{help_text}")
            claims.append({"kind": "note", "text": part})
    mechanical = [
        claim for claim in claims if claim["kind"] not in {"note", "aggregate"}
    ]
    if any(claim["kind"] == "aggregate" for claim in claims) and not mechanical:
        # Statistical claims are additive, never substitutive.
        raise AssayError(
            "aggregate claims are additive: this action still needs a mechanical "
            f"claim of its own\n{help_text}"
        )
    if not mechanical:
        # A prose prediction still commits to a visible effect. Coerced claims
        # are journaled as their own kind and excluded from the capability meter.
        claims.append(
            {"kind": "change", "text": "change (implied by free text)", "coerced": True}
        )
    return claims


def grade_general_claims(
    claims: Sequence[Mapping[str, Any]],
    prior_event: Mapping[str, Any],
    event: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Grade the general claim forms against dict-shaped observations."""
    before = prior_event.get("observation") or {}
    after = event.get("observation") or {}
    changed = changed_count(before, after)
    level_before = event.get("level_before")
    level_advanced = (
        level_before is not None
        and int(event["levels_completed"]) > int(level_before)
    )
    graded: list[dict[str, Any]] = []
    for claim in claims:
        kind = claim["kind"]
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
            ok = str(event["state"]) == "WIN"
            actual = f"state {event['state']}"
        elif kind == "level_up":
            ok = level_advanced
            actual = (
                f"level advanced to {int(event['levels_completed'])} completed"
                if level_advanced
                else "level did not advance"
            )
        else:
            actual = "ungradable without a grid observation"
        graded.append({**dict(claim), "ok": bool(ok), "actual": actual})
    return graded


def _finalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the journaled claim kind and meter bucket."""
    output = dict(record)
    kind = str(output.get("kind", ""))
    output["bucket"] = claim_bucket(kind, output.get("channel"))
    if output.get("coerced"):
        output["kind"] = "coerced"
    return output


def grade_action_claims(
    paths: Any,
    claims: Sequence[dict[str, Any]],
    prior_event: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    elapsed_s: float | None = None,
) -> list[dict[str, Any]]:
    """Grade every claim of one paid action: the observation kind's grader or
    the general one, then channels, then verifiers.

    Aggregate claims are NOT graded here — they open at the gate and resolve at
    their horizon. A claim with a validity window grades only if the result
    settled inside it (`elapsed_s` is the daemon-measured step duration); a
    late settle is UNGRADABLE — its own outcome, never a silent pass or miss.
    """
    from .channels import grade_channel_claim
    from .verifiers import grade_verifier_claim, observation_view

    graded: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    timely: list[dict[str, Any]] = []
    for claim in claims:
        if claim["kind"] in {"note", "aggregate"}:
            continue
        window = claim.get("window_s")
        if window is not None and elapsed_s is not None and elapsed_s > float(window):
            stale.append(
                {
                    **dict(claim),
                    "ok": False,
                    "ungradable": True,
                    "actual": (
                        f"UNGRADABLE: settled after {elapsed_s:.2f}s, "
                        f"outside the declared {float(window):g}s window"
                    ),
                }
            )
        else:
            timely.append(claim)
    plain = [
        claim
        for claim in timely
        if claim["kind"] not in {"verify"} and claim["kind"] not in CHANNEL_KINDS
    ]
    kind = kind_for(event)
    if kind is not None:
        graded.extend(kind.grade_claims(plain, prior_event, event))
    else:
        graded.extend(grade_general_claims(plain, prior_event, event))
    graded.extend(
        grade_channel_claim(paths, claim, prior_event, event)
        for claim in timely
        if claim["kind"] in CHANNEL_KINDS
    )
    verify_claims = [claim for claim in timely if claim["kind"] == "verify"]
    if verify_claims:
        before_view = observation_view(prior_event)
        after_view = observation_view(event)
        graded.extend(
            grade_verifier_claim(paths, claim, before_view, after_view)
            for claim in verify_claims
        )
    graded.extend(stale)
    return [_finalize_record(record) for record in graded]


def grade_lines(graded: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in graded:
        if item.get("invalid") or item.get("ungradable"):
            lines.append(f"! {item['text']} — {item['actual']}")
            continue
        lines.append(
            f"{'✓' if item['ok'] else '✗'} {item['text']}"
            + ("" if item["ok"] else f" — {item['actual']}")
        )
    return lines
