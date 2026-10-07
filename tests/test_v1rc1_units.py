"""Unit tests for the v1-rc1 subsystems: registry v2, the grammar additions,
redaction, the hash chain, and hazard demand logic."""

from __future__ import annotations

import json

import pytest

from conftest import event_of, run_of

from assay.core import AssayError, RunPaths, atomic_json
from assay.integrity import (
    audit,
    compute_chain,
    redact,
    redact_mapping,
    ungated_events,
)
from assay.run import Run
from assay.predictions import claim_bucket, parse_claims
from assay.registry import (
    hand_cap,
    notes_cap,
    spend_reports,
    validate_registry,
    zero_prior,
)

BASE = {"actions": [{"name": "GO", "params": {}}]}


# ---------------------------------------------------------------- registry v2


def test_registry_v2_fields_roundtrip():
    spec = validate_registry(
        {
            **BASE,
            "actions": [
                {
                    "name": "FIRE",
                    "params": {},
                    "destructive": True,
                    "approval": True,
                    "liveness": "live",
                    "rehearsal_quota": 3,
                    "description": "hint text",
                }
            ],
            "budget": {"actions": 5, "usd": 2.5},
            "goal": {"text": "reach the vault"},
            "batching": {"hand_cap": None},
            "notes_cap": 500,
            "zero_prior": True,
            "module_modes": {"hazard": "block"},
            "secrets": ["MY_KEY"],
            "observers": [{"name": "cam", "rate_hz": 10}],
            "control": {"tiers": 2},
        }
    )
    action = spec["actions"][0]
    assert action["destructive"] and action["approval"]
    assert action["liveness"] == "live" and action["rehearsal_quota"] == 3
    assert spec["budget"] == {"actions": 5, "usd": 2.5}
    assert spec["goal"]["text"] == "reach the vault"
    assert hand_cap(spec) is None
    assert notes_cap(spec) == 500
    assert zero_prior(spec)


def test_registry_v2_defaults_and_refusals():
    spec = validate_registry(BASE)
    assert hand_cap(spec) == 3           # the batching law's kernel default
    assert hand_cap(None) is None        # no registry: uncapped
    assert notes_cap(spec) == 16_000
    assert not zero_prior(spec)
    for bad in [
        {**BASE, "batching": {"hand_cap": 0}},
        {**BASE, "budget": {"usd": -1}},
        {**BASE, "goal": {"text": "  "}},
        {**BASE, "module_modes": {"hazard": "loud"}},
        {**BASE, "actions": [{"name": "A", "params": {}, "rehearsal_quota": 2}]},
        {**BASE, "actions": [{"name": "A", "params": {}, "liveness": "maybe"}]},
    ]:
        with pytest.raises(AssayError):
            validate_registry(bad)


def test_spend_reports_idempotent_by_id():
    activity = [
        {"kind": "spend_report", "id": "t1", "usd": 1.0, "tokens": 10},
        {"kind": "spend_report", "id": "t1", "usd": 1.5, "tokens": 15},  # correction
        {"kind": "spend_report", "id": "t2", "usd": 2.0, "tokens": 20},
    ]
    assert spend_reports(activity) == (3.5, 35)


# ------------------------------------------------------------------- grammar


def test_channel_claims_parse():
    claims = parse_claims(
        "ch counter = 3; ch counter delta >= 1; ch temp crosses 5 from below; "
        "ch level delta sign +",
    )
    kinds = [claim.kind for claim in claims]
    assert kinds == ["channel_eq", "channel_delta", "channel_cross", "channel_delta"]
    assert claims[0].value == 3
    assert claims[1].op == ">=" and claims[1].value == 1
    assert claims[2].direction == "below"
    assert claims[3].op == "sign" and claims[3].sign == "+"


def test_channel_claim_tolerance_and_window():
    claims = parse_claims("ch price = 4.5 +- 0.2 @within 1.5s")
    assert claims[0].tol == 0.2 and claims[0].window_s == 1.5
    with pytest.raises(AssayError):
        parse_claims("ch price = up down")  # malformed, not a note


def test_channel_bucket_split():
    assert claim_bucket("channel_eq", "goal") == "gamble"
    assert claim_bucket("channel_delta", "level") == "gamble"
    assert claim_bucket("channel_eq", "counter") == "world_model"
    assert claim_bucket("aggregate", "counter") == "aggregate"


def test_aggregate_additive_rule():
    with pytest.raises(AssayError, match="additive"):
        parse_claims(
            "agg ch counter mean >= 1 over 3a horizon 5a on-fail advise",
            )
    claims = parse_claims(
        "noop; agg ch counter mean >= 1 over 3a horizon 5a on-fail revoke_batching",
    )
    aggregate = next(claim for claim in claims if claim.kind == "aggregate")
    assert aggregate.over == 3 and aggregate.horizon == 5
    assert aggregate.on_fail == "revoke_batching"
    with pytest.raises(AssayError):
        parse_claims("noop; agg ch c mean >= 1 over 3a horizon 500a on-fail advise")


# ------------------------------------------------------------------ redaction


def test_redact_secret_values(monkeypatch):
    monkeypatch.setenv("FAKE_SECRET_X", "hunter2secret")
    assert redact("key is hunter2secret ok", ["FAKE_SECRET_X"]) == (
        "key is [REDACTED:FAKE_SECRET_X] ok"
    )
    nested = redact_mapping(
        {"a": "hunter2secret", "b": {"c": ["hunter2secret", 3]}}, ["FAKE_SECRET_X"]
    )
    assert nested == {
        "a": "[REDACTED:FAKE_SECRET_X]",
        "b": {"c": ["[REDACTED:FAKE_SECRET_X]", 3]},
    }
    monkeypatch.setenv("SHORT", "ab")  # too short: never redacted
    assert redact("ab", ["SHORT"]) == "ab"


# ------------------------------------------------------------------ integrity


def _fake_paths(tmp_path) -> RunPaths:
    paths = RunPaths(tmp_path)
    paths.state.mkdir(parents=True, exist_ok=True)
    atomic_json(paths.config, {"game_id": "fake1", "mode": "local"})
    return paths


def _event(i: int, **overrides):
    record = {
        "id": i,
        "action": "GO",
        "data": None,
        "note": "",
        "state": "NOT_FINISHED",
        "levels_completed": 0,
        "level_before": 0 if i else None,
        "win_levels": 1,
        "available_actions": ["GO"],
        "counts_action": i > 0,
        "predict": "change",
        "predict_ok": True,
        "grade": [{"kind": "change", "ok": True, "bucket": "world_model"}],
    }
    record.update(overrides)
    return record


def test_chain_extend_and_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(tmp_path / "anchors"))
    paths = _fake_paths(tmp_path / "run")
    run = Run.load(paths, strict=True)
    for i in range(3):
        run.append(event_of(**_event(i, action="START" if i == 0 else "GO",
                                     predict=None if i == 0 else "change",
                                     predict_ok=None if i == 0 else True,
                                     grade=None if i == 0 else _event(i)["grade"])))
    report = audit(run)
    assert report["chain"] == "intact"
    assert report["contiguous"] and not report["ungated"]
    assert not report["invalid_for_scoring"]
    # Tampering: rewrite an early line -> recomputed head diverges from stored.
    lines = paths.events.read_text().splitlines()
    lines[1] = lines[1].replace('"GO"', '"XX"')
    paths.events.write_text("\n".join(lines) + "\n")
    report = audit(Run.load(paths, strict=False))
    assert report["chain"] == "DIVERGED"
    assert report["invalid_for_scoring"]


def test_ungated_definition(tmp_path):
    events = [
        _event(0, action="START", counts_action=False, predict=None,
               predict_ok=None, grade=None),
        _event(1),
        _event(2, predict=None, predict_ok=None, grade=None),      # UNGATED
        _event(3, action="RESET", predict=None, predict_ok=None, grade=None),
    ]
    assert ungated_events([event_of(**event) for event in events]) == [2]


def test_anchor_written_on_win(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(tmp_path / "anchors"))
    paths = _fake_paths(tmp_path / "run")
    run = Run.load(paths, strict=True)
    run.append(event_of(**_event(0, state="WIN")))
    from assay.integrity import anchor_file

    anchors = anchor_file(paths, run.config).read_text().splitlines()
    assert len(anchors) == 1
    entry = json.loads(anchors[0])
    assert entry["event_id"] == 0
    _, head = compute_chain(paths)
    assert entry["head"] == head


# -------------------------------------------------------------------- hazard


def test_hazard_tags_and_demands(tmp_path):
    from assay.modules import ModuleView, _Hazard

    paths = _fake_paths(tmp_path / "run")
    hazard = _Hazard()
    events = [
        event_of(**_event(0, action="START", counts_action=False)),
        event_of(**_event(1, action="BOMB", state="GAME_OVER")),
    ]
    view = ModuleView(run_of(paths, events, registry={"actions": []}))
    hazard.observe(view, events[1])
    tags = json.loads((paths.state / "hazards.json").read_text())
    assert tags[0]["action_class"] == "BOMB"
    assert tags[0]["signature"] == "entered_loss_state"
    pending = {"kind": "act", "name": "BOMB", "params": None, "claims": [], "declares": {}}
    demands = hazard.demand(view, pending)
    assert set(demands) == {"worst_case", "recovery"}
    pending["declares"] = {"worst_case": "lose level", "recovery": "reset"}
    assert hazard.demand(view, pending) is None
    safe = {"kind": "act", "name": "GO", "params": None, "claims": [], "declares": {}}
    assert hazard.demand(view, safe) is None


# --------------------------------------------------------------------- model


def test_batching_rights_reads_both_fit_record_forms(tmp_path):
    """A fit record written before the admission rule (#18) carries no
    `admitted_at_event` and reads exactly as it always did (the published
    run directories hold such records); a record with one names the counted
    transitions."""
    from assay.model import batching_rights, fit_path

    paths = _fake_paths(tmp_path / "run")
    run = run_of(paths)
    old = {"fit": 0.0, "graded": 0, "missed": 0, "recent_graded": 0, "promotion": False}
    atomic_json(fit_path(paths), old)
    assert batching_rights(run) == (
        False,
        "replay-fit not promoted: graded 0, missed 0, recent 0 "
        "(needs missed=0, graded>=20, recent>=5)",
    )
    new = {**old, "graded": 20, "admitted_at_event": 20, "counted": 0, "counted_recent": 0}
    atomic_json(fit_path(paths), new)
    assert batching_rights(run) == (
        False,
        "replay-fit not promoted: counted 0 since the admission at e20, missed 0, "
        "recent 0 (needs missed=0, counted>=20, recent>=5)",
    )
