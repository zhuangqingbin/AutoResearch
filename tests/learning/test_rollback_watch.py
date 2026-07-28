"""Wave 5 post-activation rollback observation contracts."""
from __future__ import annotations

import pytest

from autoresearch.learning import experiment_registry as registry
from autoresearch.learning import promotion, rollback_watch


def _guards(*, rollback=False):
    edge_floor = -0.01 if rollback else 0.0
    tolerance = 0.02 if rollback else 0.0
    return {
        "research": [{"metric": "research.edge", "op": "gt" if not rollback else "gte", "value": edge_floor}],
        "decision": [{"metric": "decision.false_buy_delta", "op": "lte", "value": tolerance}],
        "token": [{"metric": "token.cost_delta", "op": "lte", "value": tolerance}],
        "speed": [{"metric": "speed.p90_delta", "op": "lte", "value": tolerance}],
        "architecture": [{"metric": "architecture.failures", "op": "eq", "value": 0}],
    }


def _metrics(**overrides):
    data = {
        "research": {"edge": 0.01},
        "decision": {"false_buy_delta": 0.0},
        "token": {"cost_delta": -0.05},
        "speed": {"p90_delta": -0.05},
        "architecture": {"failures": 0},
    }
    for domain, values in overrides.items():
        data.setdefault(domain, {}).update(values)
    return {"metrics": data}


def _active(tmp_path, *, activate=True):
    path = tmp_path / "registry.json"
    registry.set_stable_baseline(
        path,
        name="wave4",
        pointer="git:6a06bc8",
        content_hash="a" * 64,
        approved_by="user",
        approved_at="2026-07-28T12:00:00+08:00",
    )
    registry.register_experiment(
        path,
        {
            "id": "exp_a",
            "title": "challenger",
            "trial_family": "family-a",
            "definition": {"switch": "a"},
            "start_date": "2026-07-28",
            "expires_date": "2026-10-31",
            "primary_metric": "research.edge",
            "promotion_guards": _guards(),
            "rollback_guards": _guards(rollback=True),
            "challenger_pointer": {
                "kind": "git",
                "pointer": "git:challenger-a",
                "content_hash": "b" * 64,
            },
            "minimums": {
                "forward_days": 20,
                "mature_events": 10,
                "unique_events": 0,
                "regimes": 2,
            },
            "rollback_window_runs": 3,
        },
    )
    promotion.evaluate_experiment(
        path,
        "exp_a",
        {
            "sample": {
                "forward_days": 20,
                "mature_events": 10,
                "unique_events": 0,
                "regimes": ["range", "risk_off"],
                "trial_count": 1,
                "definition_breakpoints": [],
            },
            **_metrics(),
        },
        evaluated_at="2026-08-20T12:00:00+08:00",
    )
    registry.approve_experiment(
        path,
        "exp_a",
        approved_by="user",
        approved_at="2026-08-21T12:00:00+08:00",
        approval_expires_at="2026-08-28T12:00:00+08:00",
    )
    if activate:
        registry.activate_experiment(
            path,
            "exp_a",
            activated_by="user",
            activated_at="2026-08-22T12:00:00+08:00",
        )
    return path


def _observe(path, run_id, facts=None, day=23):
    return rollback_watch.observe(
        path,
        "exp_a",
        facts or _metrics(),
        run_id=run_id,
        observed_at=f"2026-08-{day:02d}T12:00:00+08:00",
    )


def test_observation_requires_active_experiment(tmp_path):
    path = _active(tmp_path, activate=False)

    with pytest.raises(registry.RegistryError, match="ACTIVE"):
        _observe(path, "run-1")


def test_missing_guard_metric_is_unknown_and_never_a_breach(tmp_path):
    path = _active(tmp_path)
    facts = _metrics()
    del facts["metrics"]["decision"]["false_buy_delta"]
    got = _observe(path, "run-1", facts)

    assert got["status"] == "UNKNOWN"
    assert got["recommendation"] == "CONTINUE_OBSERVATION"
    assert got["guards"]["decision"]["status"] == "UNKNOWN"
    assert registry.get_experiment(path, "exp_a")["status"] == "ACTIVE"


def test_any_guard_breach_recommends_but_does_not_apply_rollback(tmp_path):
    path = _active(tmp_path)
    stable = registry.load_registry(path)["stable_baseline"]
    got = _observe(
        path,
        "run-bad",
        _metrics(decision={"false_buy_delta": 0.03}),
    )
    record = registry.get_experiment(path, "exp_a")

    assert got["status"] == "FAIL"
    assert got["recommendation"] == "ROLLBACK"
    assert got["rollback_pointer"] == stable
    assert record["status"] == "ROLLBACK_RECOMMENDED"
    assert record.get("rollback") is None
    assert registry.load_registry(path)["stable_baseline"] == stable
    assert registry.load_registry(path)["active_by_family"] == {"family-a": "exp_a"}


def test_clean_window_becomes_stable_candidate_only_at_required_count(tmp_path):
    path = _active(tmp_path)
    one = _observe(path, "run-1", day=23)
    two = _observe(path, "run-2", day=24)
    three = _observe(path, "run-3", day=25)

    assert one["recommendation"] == two["recommendation"] == "CONTINUE_OBSERVATION"
    assert three["recommendation"] == "ACCEPT_BASELINE"
    assert three["window"] == {"required": 3, "observed": 3, "all_pass": True}
    assert registry.get_experiment(path, "exp_a")["status"] == "STABLE_CANDIDATE"


def test_same_run_replay_is_idempotent_but_changed_facts_fail(tmp_path):
    path = _active(tmp_path)
    first = _observe(path, "run-1")
    before = path.read_bytes()
    replay = _observe(path, "run-1")

    assert replay == first
    assert path.read_bytes() == before
    with pytest.raises(registry.RegistryError, match="different observation"):
        _observe(
            path,
            "run-1",
            _metrics(token={"cost_delta": 0.01}),
        )


def test_explicit_human_rollback_closes_active_slot_and_keeps_baseline(tmp_path):
    path = _active(tmp_path)
    stable = registry.load_registry(path)["stable_baseline"]
    _observe(path, "run-bad", _metrics(architecture={"failures": 1}))
    got = registry.rollback_experiment(
        path,
        "exp_a",
        rolled_back_by="qingbin",
        rolled_back_at="2026-08-23T13:00:00+08:00",
        note="architecture guard breached",
    )
    payload = registry.load_registry(path)

    assert got["status"] == "ROLLED_BACK"
    assert got["rollback"]["target"] == stable
    assert payload["active_by_family"] == {}
    assert payload["stable_baseline"] == stable


def test_explicit_accept_promotes_challenger_and_archives_prior_baseline(tmp_path):
    path = _active(tmp_path)
    prior = registry.load_registry(path)["stable_baseline"]
    for i, day in enumerate((23, 24, 25), 1):
        _observe(path, f"run-{i}", day=day)
    got = registry.accept_experiment_as_baseline(
        path,
        "exp_a",
        accepted_by="qingbin",
        accepted_at="2026-08-26T12:00:00+08:00",
        note="clean observation window",
    )
    payload = registry.load_registry(path)

    assert got["status"] == "ACCEPTED"
    assert payload["stable_baseline"]["pointer"] == "git:challenger-a"
    assert prior in payload["baseline_history"]
    assert payload["active_by_family"] == {}


def test_rollback_and_accept_are_illegal_without_their_recommendation(tmp_path):
    path = _active(tmp_path)

    with pytest.raises(registry.RegistryError, match="ROLLBACK_RECOMMENDED"):
        registry.rollback_experiment(path, "exp_a", rolled_back_by="user")
    with pytest.raises(registry.RegistryError, match="STABLE_CANDIDATE"):
        registry.accept_experiment_as_baseline(path, "exp_a", accepted_by="user")
