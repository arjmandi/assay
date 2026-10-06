"""coverage_audit --- ASSAY behavior module (external, ARC-AGI target).

Machinery for the coverage-audit protocol. The finding it implements: a
replay-validated world model can be confidently wrong exactly where no graded
transition ever went. Six times across the ARC-AGI-3 campaign the agent proved
a level impossible; every proof fit every recorded transition and was wrong on
a rule that had never been *exercised*. This module turns the manual protocol
into standing machinery:

  1. enumerate the rules an impossibility/absence conclusion load-bears on,
  2. audit the journal for what actually exercised each,
  3. buy graded probes for the gaps, cheapest first.

It does three concrete things over the journal, all model-agnostic:

  * a COVERAGE METER over the current level: which available actions were never
    tried, which were tried but never once changed the world (believed dead),
    and which grid regions the point action never probed.
  * an automatic HALT when a failing prediction is re-issued unmodified, and on
    a short loop of the same failing move.
  * a CONCLUSION GATE: when the agent tags an impossibility/absence conclusion,
    it demands a coverage-audit declaration and surfaces the remaining gaps.

Design notes for the integrator:
  * This is an EXTERNAL module. No core edits. Drop this file somewhere and add
    its path to the ARC registry's "modules" list. It is pinned into
    .assay/modules/ at run start and consulted before every action.
  * Pure standard library. It reads only `view.events` (the journal) and the
    pending action, both duck-typed, so it does not import anything from assay
    and is model-agnostic (works on any backend).
  * Ships MODE = "advise" per the owner law (teeth only after an A/B shows
    blocking pays). Set registry module_modes.coverage_audit = "block" to
    enforce the two demands below as hard, satisfiable declarations.
  * "Exercised" is approximated for ARC by two observable proxies: an action
    class is exercised in the productive regime if it has been seen to change
    the world, and a grid region is exercised if the point action has probed
    it. This does not capture arbitrary rule regimes (for example s5i5's
    off-board vs occupied rotation) unless the agent declares a channel that
    names the regime. See HANDOFF.md, "Limits and extension points".

The single integration convention: instruct the player that whenever it
concludes a level is impossible, that something is not present, or that the
board is already complete, it must tag that action with a declare naming the
conclusion, for example:

    assay act <ACTION> ... --predict "<claim>" --declare impossible="<reason>"

That declare is what the conclusion gate keys on.
"""

from __future__ import annotations

# --- tunables -------------------------------------------------------------
GRID = 64            # ARC observation is a 64x64 color grid
REGION = 8           # region bucket edge, so (64/8)^2 = 64 buckets over the grid
DEAD_MIN_TRIES = 6   # tried this many times, never productive => "believed dead"
STALL_WINDOW = 8     # this many consecutive non-productive paid actions => a stall
LOOP_MIN = 3         # this many identical failing moves in a row => a loop

# declare-keys (and conclusion-value words) that mark an impossibility/absence claim
SENTINELS = frozenset({
    "impossible", "impossibility", "unsolvable", "unwinnable",
    "absent", "not_present", "notpresent", "missing", "none_remain",
    "already_complete", "complete", "done_here", "saturated",
    "giveup", "give_up", "stuck", "dead_end", "deadend",
})


# --- journal helpers (all defensive against malformed events) --------------
def _i(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _norm_action(name):
    """Normalize 'ACTION6', 'action6', 6 and '6' to the bare token '6'."""
    s = str(name).strip().upper()
    return s[6:] if s.startswith("ACTION") else s


def _fmt_action(token):
    return "ACTION" + token if token.isdigit() else token


def _fmt_actions(tokens):
    return ", ".join(_fmt_action(t) for t in tokens) if tokens else "none"


def _params_key(params):
    """Canonical, type-robust key. Values are stringified so a pending param
    of "0" (CLI text) compares equal to a validated event datum of 0 (int)."""
    if not isinstance(params, dict):
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in params.items()))


def _event_sig(event):
    return (_norm_action(event.get("action")), _params_key(event.get("data")))


def _pending_sig(pending):
    return (_norm_action(pending.get("name")), _params_key(pending.get("params")))


def _fmt_move(sig):
    token, params = sig
    if params:
        inner = ",".join(f"{k}={v}" for k, v in params)
        return f"{_fmt_action(token)}({inner})"
    return _fmt_action(token)


def _level_indices(events):
    """Indices of events on the current (latest) level, in order. Mirrors the
    kernel's own level-action counting: walk back while levels_completed holds."""
    if not events:
        return []
    completed = _i(events[-1].get("levels_completed"))
    out = []
    for idx in range(len(events) - 1, -1, -1):
        if _i(events[idx].get("levels_completed")) != completed:
            break
        out.append(idx)
    return list(reversed(out))


def _changed(events, idx):
    """Did paid event `idx` change the world? Frame comparison is the canonical,
    claim-independent signal; progress and grade text are fallbacks."""
    event = events[idx]
    if _i(event.get("levels_completed")) > _i(event.get("level_before")):
        return True
    if idx > 0:
        frame, prev = event.get("frames"), events[idx - 1].get("frames")
        if frame is not None and prev is not None:
            return frame != prev
    for grade in event.get("grade") or ():
        kind = grade.get("kind")
        if kind in ("level_up", "win", "change") and grade.get("ok"):
            return True
        actual = str(grade.get("actual", ""))
        if "cells changed" in actual or "changed=" in actual:
            if "changed=0" in actual or actual.strip().startswith("0 "):
                continue
            return True
    return False


def _coverage(events):
    """The coverage ledger for the current level."""
    indices = _level_indices(events)
    paid = [i for i in indices if events[i].get("counts_action")]
    plays = {}                      # token -> [tries, productive]
    regions = set()                 # (bx, by) probed by any point action
    point_used = False
    for i in paid:
        event = events[i]
        token = _norm_action(event.get("action"))
        rec = plays.setdefault(token, [0, 0])
        rec[0] += 1
        if _changed(events, i):
            rec[1] += 1
        data = event.get("data") or {}
        x, y = data.get("x"), data.get("y")
        if isinstance(x, int) and isinstance(y, int):
            point_used = True
            regions.add((x // REGION, y // REGION))
    avail = [_norm_action(a) for a in (events[-1].get("available_actions") or [])]
    untried = [a for a in avail if plays.get(a, (0, 0))[0] == 0]
    dead = [a for a in avail
            if plays.get(a, (0, 0))[0] >= DEAD_MIN_TRIES and plays[a][1] == 0]
    total_regions = (GRID // REGION) ** 2
    return {
        "level": _i(events[-1].get("levels_completed")),
        "paid_on_level": len(paid),
        "avail": avail,
        "untried": untried,
        "dead": dead,
        "regions_probed": len(regions),
        "regions_total": total_regions if point_used else 0,
        "regions_unprobed": (total_regions - len(regions)) if point_used else 0,
        "point_used": point_used,
        "_regions": regions,
    }


def _example_unprobed(cov, k=3):
    if not cov["point_used"]:
        return ""
    probed = cov["_regions"]
    n = GRID // REGION
    out = []
    for by in range(n):
        for bx in range(n):
            if (bx, by) not in probed:
                out.append(
                    f"x{bx * REGION}-{bx * REGION + REGION - 1},"
                    f"y{by * REGION}-{by * REGION + REGION - 1}"
                )
                if len(out) >= k:
                    return "; ".join(out)
    return "; ".join(out)


def _gap_phrase(cov):
    parts = []
    if cov["dead"]:
        parts.append(f"never-productive actions [{_fmt_actions(cov['dead'])}]")
    if cov["untried"]:
        parts.append(f"untried actions [{_fmt_actions(cov['untried'])}]")
    if cov["regions_unprobed"]:
        ex = _example_unprobed(cov)
        tail = f" (e.g. {ex})" if ex else ""
        parts.append(f"{cov['regions_unprobed']}/{cov['regions_total']} "
                     f"grid regions unprobed{tail}")
    if not parts:
        return ("coverage looks saturated: every available action has been "
                "productive and every region probed. If the conclusion still "
                "holds, name the unstated premise it rests on (the observation "
                "frame itself is the classic one).")
    return "unexercised: " + "; ".join(parts) + "."


def _stalled(events):
    indices = [i for i in _level_indices(events) if events[i].get("counts_action")]
    if len(indices) < STALL_WINDOW:
        return False
    if str(events[-1].get("state", "")) != "NOT_FINISHED":
        return False
    return not any(_changed(events, i) for i in indices[-STALL_WINDOW:])


def _declares(pending):
    return {str(k).lower(): v for k, v in (pending.get("declares") or {}).items()}


def _is_conclusion(pending):
    if not pending:
        return False
    if pending.get("kind") == "goal":
        return True
    declares = _declares(pending)
    if set(declares) & SENTINELS:
        return True
    for key in ("conclusion", "verdict", "claim"):
        value = str(declares.get(key, "")).lower()
        if any(word in value for word in SENTINELS):
            return True
    return False


# --- the module -----------------------------------------------------------
class _CoverageAudit:
    NAME = "coverage_audit"
    CONSTITUTION = (
        "Provably unsolvable is a property of your model, not of the world. "
        "Before you conclude that a level is impossible, that something is not "
        "present, or that the board is already complete, every rule that "
        "conclusion rests on must have been exercised by a graded transition in "
        "the regime where the conclusion needs it. Consistency with the record "
        "is not evidence, the record may simply never have visited the region "
        "your conclusion depends on. Enumerate the load-bearing rules, check "
        "which the journal actually exercised, and buy cheap probes for the gaps "
        "before the conclusion stands."
    )
    MODE = "advise"

    def trigger(self, view, pending):
        events = list(getattr(view, "events", None) or [])
        if len(events) < 2:
            return None
        cov = _coverage(events)
        paid = [e for e in events if e.get("counts_action")]

        # 1. halt on re-issue / loop of a failing move
        if pending and pending.get("kind") in ("act", "commit") and paid:
            sig = _pending_sig(pending)
            last = paid[-1]
            if last.get("predict_ok") is False and _event_sig(last) == sig:
                return (f"re-issuing {_fmt_move(sig)} unmodified, it just graded "
                        f"FALSE. Halt: revise the model or probe an unexercised "
                        f"rule. {_gap_phrase(cov)}")
            tail = paid[-LOOP_MIN:]
            if (len(tail) == LOOP_MIN
                    and all(_event_sig(e) == sig for e in tail)
                    and all(e.get("predict_ok") is False for e in tail)):
                return (f"{_fmt_move(sig)} has missed {LOOP_MIN} times in a row, "
                        f"you are looping. Stop repeating it. {_gap_phrase(cov)}")

        # 2. conclusion checkpoint
        if _is_conclusion(pending):
            return ("impossibility/absence conclusion detected, run the COVERAGE "
                    "AUDIT before it stands. Enumerate the rules the conclusion "
                    "load-bears on and cite the graded events that exercised each. "
                    + _gap_phrase(cov))

        # 3. status meter (no pending), or an implicit give-up via reset under a stall
        stalled = _stalled(events)
        if pending is None:
            if cov["paid_on_level"] < 3:
                return None
            note = " STALL: last "f"{STALL_WINDOW} actions changed nothing." if stalled else ""
            regions = (f"{cov['regions_probed']}/{cov['regions_total']} regions probed, "
                       if cov["point_used"] else "")
            return (f"coverage level {cov['level'] + 1}: {regions}"
                    f"untried [{_fmt_actions(cov['untried'])}], "
                    f"no-op-only [{_fmt_actions(cov['dead'])}].{note}")
        if pending.get("kind") == "reset" and stalled:
            return (f"resetting under a stall. {_gap_phrase(cov)} Probe these "
                    f"before treating the level as impossible.")
        return None

    def demand(self, view, pending):
        if not pending:
            return None
        declares = _declares(pending)

        # (a) conclusion gate: an impossibility/absence claim must carry an audit
        if _is_conclusion(pending) and not str(declares.get("coverage_audit", "")).strip():
            return {"coverage_audit":
                    "before an impossibility or absence claim, enumerate the "
                    "load-bearing rules and cite the graded event ids that "
                    "exercised each, and probe any unexercised rule first"}

        # (b) halt-on-reissue gate: re-issuing a just-failed move demands a revision
        if pending.get("kind") in ("act", "commit"):
            events = list(getattr(view, "events", None) or [])
            paid = [e for e in events if e.get("counts_action")]
            if (paid and paid[-1].get("predict_ok") is False
                    and _event_sig(paid[-1]) == _pending_sig(pending)
                    and not str(declares.get("revised", "")).strip()):
                return {"revised":
                        "the identical previous prediction graded FALSE, declare "
                        "what you changed or choose a different action or region"}
        return None

    def telemetry(self, view):
        events = list(getattr(view, "events", None) or [])
        if not events:
            return {}
        cov = _coverage(events)
        return {
            "level": cov["level"],
            "regions_probed": cov["regions_probed"],
            "regions_total": cov["regions_total"],
            "untried": len(cov["untried"]),
            "dead": len(cov["dead"]),
        }


MODULE = _CoverageAudit()
