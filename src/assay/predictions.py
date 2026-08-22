"""A tiny prediction vocabulary the runtime grades automatically.

Every live action carries a prediction. Structured claims are graded against
the settled result; free text is graded as "some visible change". Coordinates
are x=column, y=row — the same convention as ACTION6.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from .core import AssayError, frame_at
from .textobs import changed_count

GRID_KINDS = {"cell", "move", "vanish", "region"}
GAMBLE_KINDS = {"win", "level_up"}
CHANNEL_KINDS = {"channel_eq", "channel_delta", "channel_cross"}
_MILESTONE = {"goal", "level"}  # channel claims here gamble; the rest world-model

CLAIMS_HELP = """\
PREDICTION CLAIMS | separate several with ";" | x=column y=row (like ACTION6)
  noop                 nothing visible changes
  change               something visible changes (free text means this too)
  cell X,Y=V           cell at column X, row Y becomes hex color V
  move X,Y DX,DY       the object covering X,Y shifts by (DX,DY) and vacates its old cells
  vanish X,Y           every cell of the object covering X,Y stops being its color
  region X0:X1,Y0:Y1   all changes fall inside this half-open box, and something changes
  level+1              this action completes the level
  win                  this action wins the game
  verify:PATH.py       run your verifier file: def verify(before, after) -> (ok, actual)
  ch NAME = V [± TOL]  a registered channel reads V (goal/level built in;
                       `assay channel declare` registers more; delta/crosses forms exist)
Free text that is not a claim is kept as commentary. Example:
  --predict "door opens; cell 12,5=0; region 10:14,3:8"
"""

GENERAL_CLAIMS_HELP = """\
PREDICTION CLAIMS | separate several with ";"
  noop                 no observed change
  change               the observation changes (free text means this too)
  level+1              this action completes the current level/stage
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
    (
        "cell",
        re.compile(r"^cell\s+(\d+)\s*,\s*(\d+)\s*=\s*([0-9a-fA-F])$", re.IGNORECASE),
    ),
    (
        "move",
        re.compile(
            r"^move\s+(\d+)\s*,\s*(\d+)\s+([+-]?\d+)\s*,\s*([+-]?\d+)$", re.IGNORECASE
        ),
    ),
    ("vanish", re.compile(r"^vanish\s+(\d+)\s*,\s*(\d+)$", re.IGNORECASE)),
    (
        "region",
        re.compile(r"^region\s+(\d+)\s*:\s*(\d+)\s*,\s*(\d+)\s*:\s*(\d+)$", re.IGNORECASE),
    ),
)

_KEYWORD = re.compile(
    r"^(noop|change|cell|move|vanish|region|level|win|verify|ch|agg)\b", re.IGNORECASE
)


def claim_bucket(kind: str, channel: str | None = None) -> str:
    """Claim taxonomy: goal/milestone claims gamble, the rest world-model."""
    if kind in CHANNEL_KINDS:
        return "gamble" if channel in _MILESTONE else "world_model"
    if kind == "aggregate":
        return "aggregate"
    return "gamble" if kind in GAMBLE_KINDS else "world_model"


def parse_claims(text: str, *, general: bool = False) -> list[dict[str, Any]]:
    """Parse a prediction string into claims; free text implies `change`.

    With general=True (registry runs without grid observations) the grid-only
    claim forms are refused before any spend.
    """
    help_text = GENERAL_CLAIMS_HELP if general else CLAIMS_HELP
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
        for kind, pattern in _PATTERNS:
            found = pattern.match(part)
            if not found:
                continue
            if general and kind in GRID_KINDS:
                raise AssayError(
                    f"claim {part!r} needs a grid observation; this run has none\n"
                    f"{help_text}"
                )
            claim: dict[str, Any] = {"kind": kind, "text": part}
            if window_s is not None:
                claim["window_s"] = window_s
            if kind == "verify":
                claim["path"] = found.group(1)
            elif kind == "channel_delta_sign":
                claim.update(
                    kind="channel_delta",
                    channel=found.group(1).lower(),
                    op="sign",
                    sign=found.group(2),
                )
            elif kind == "channel_delta":
                claim.update(
                    channel=found.group(1).lower(),
                    op=found.group(2),
                    value=_parse_value(found.group(3)),
                )
                if not isinstance(claim["value"], (int, float)) or isinstance(
                    claim["value"], bool
                ):
                    raise AssayError(f"delta claims need a numeric value: {part!r}")
            elif kind == "channel_cross":
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
            elif kind == "channel_eq":
                claim.update(
                    channel=found.group(1).lower(),
                    value=_parse_value(found.group(2)),
                )
                if found.group(3) is not None:
                    claim["tol"] = float(found.group(3))
            elif kind == "aggregate":
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
            elif kind == "cell":
                claim.update(
                    x=int(found.group(1)),
                    y=int(found.group(2)),
                    value=int(found.group(3), 16),
                )
            elif kind == "move":
                claim.update(
                    x=int(found.group(1)),
                    y=int(found.group(2)),
                    dx=int(found.group(3)),
                    dy=int(found.group(4)),
                )
            elif kind == "vanish":
                claim.update(x=int(found.group(1)), y=int(found.group(2)))
            elif kind == "region":
                claim.update(
                    x0=int(found.group(1)),
                    x1=int(found.group(2)),
                    y0=int(found.group(3)),
                    y1=int(found.group(4)),
                )
            claims.append(claim)
            matched = True
            break
        if not matched:
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


def _flood(grid: np.ndarray, x: int, y: int) -> tuple[int, set[tuple[int, int]]]:
    color = int(grid[y, x])
    height, width = grid.shape
    stack = [(y, x)]
    cells = {(y, x)}
    while stack:
        row, col = stack.pop()
        for nr, nc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            if (
                0 <= nr < height
                and 0 <= nc < width
                and (nr, nc) not in cells
                and int(grid[nr, nc]) == color
            ):
                cells.add((nr, nc))
                stack.append((nr, nc))
    return color, cells


def grade_claims(
    claims: Sequence[Mapping[str, Any]],
    before: np.ndarray,
    event: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Grade each claim against the settled result; ok plus a short actual."""
    after = frame_at(event)
    same_shape = before.shape == after.shape
    changed = int(np.count_nonzero(before != after)) if same_shape else None
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
            ok = same_shape and changed == 0
            actual = (
                "no visible change"
                if ok
                else f"{'board size changed' if not same_shape else f'{changed} cells changed'}"
            )
        elif kind == "change":
            ok = (not same_shape) or bool(changed) or level_advanced
            actual = (
                "no visible change (0 cells)"
                if not ok
                else f"{changed if same_shape else 'board'} changed"
            )
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
        elif kind == "cell":
            x, y = int(claim["x"]), int(claim["y"])
            if not (same_shape and 0 <= y < after.shape[0] and 0 <= x < after.shape[1]):
                actual = "coordinates out of bounds"
            else:
                ok = int(after[y, x]) == int(claim["value"])
                actual = f"cell ({x},{y}) is {int(after[y, x]):x}"
        elif kind == "vanish":
            x, y = int(claim["x"]), int(claim["y"])
            if not (same_shape and 0 <= y < before.shape[0] and 0 <= x < before.shape[1]):
                actual = "coordinates out of bounds"
            else:
                color, cells = _flood(before, x, y)
                remaining = sum(
                    1 for row, col in cells if int(after[row, col]) == color
                )
                ok = remaining == 0
                actual = (
                    f"object of color {color:x} gone from its cells"
                    if ok
                    else f"{remaining}/{len(cells)} cells still color {color:x}"
                )
        elif kind == "move":
            x, y = int(claim["x"]), int(claim["y"])
            dx, dy = int(claim["dx"]), int(claim["dy"])
            if not (same_shape and 0 <= y < before.shape[0] and 0 <= x < before.shape[1]):
                actual = "coordinates out of bounds"
            else:
                color, cells = _flood(before, x, y)
                shifted = {(row + dy, col + dx) for row, col in cells}
                in_bounds = all(
                    0 <= row < after.shape[0] and 0 <= col < after.shape[1]
                    for row, col in shifted
                )
                if not in_bounds:
                    actual = "shifted object would leave the board"
                else:
                    arrived = all(
                        int(after[row, col]) == color for row, col in shifted
                    )
                    vacated = all(
                        int(after[row, col]) != color
                        for row, col in cells - shifted
                    )
                    ok = arrived and vacated
                    if ok:
                        actual = f"object moved by ({dx:+d},{dy:+d})"
                    elif not arrived:
                        actual = f"target cells are not color {color:x}"
                    else:
                        actual = f"old cells still color {color:x} (copied, not moved)"
        elif kind == "region":
            x0, x1 = int(claim["x0"]), int(claim["x1"])
            y0, y1 = int(claim["y0"]), int(claim["y1"])
            if not same_shape:
                actual = "board size changed"
            elif not changed:
                actual = "no visible change"
            else:
                outside = [
                    (int(col), int(row))
                    for row, col in np.argwhere(before != after)
                    if not (x0 <= col < x1 and y0 <= row < y1)
                ]
                ok = not outside
                actual = (
                    f"all {changed} changes inside the box"
                    if ok
                    else f"{len(outside)} changes outside, e.g. (x,y) {outside[0]}"
                )
        graded.append({**dict(claim), "ok": bool(ok), "actual": actual})
    return graded


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
    """Grade every claim of one paid action: grid or general, channels, verifiers.

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
    if "frames" in event:
        graded.extend(grade_claims(plain, frame_at(prior_event), event))
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
