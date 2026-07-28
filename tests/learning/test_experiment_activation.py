"""Wave 5 explicit human approval and activation boundary."""
from __future__ import annotations

import pytest

from autoresearch.learning import experiment_registry as registry
from autoresearch.learning import promotion


def _guards():
    return {
        "research": [
            {"metric": "research.edge", "op": "gt", "value": 0.0},
        ],
        "decision": [
            {"metric": "decision.false_buy_delta", "op": "lte", "value": 0.0},
        ],
        "token": [
            {"metric": "token.cost_delta", "op": "lte", "value": 0.0},
        ],
        "speed": [
            {"metric": "speed.p90_delta", "op": "lte", "value": 0.0},
        ],
        "architecture": [
            {"metric": "architecture.failures", "op": "eq", "value": 0},
        ],
    }


def _facts():
    return {
        "sample": {
            "forward_days": 20,
            "mature_events": 10,
            "unique_events": 0,
            "regimes": ["range", "risk_off"],
            "trial_count": 1,
            "definition_breakpoints": [],
        },
        "metrics": {
            "research": {"edge": 0.01},
            "decision": {"false_buy_delta": -0.01},
            "token": {"cost_delta": -0.10},
            "speed": {"p90_delta": -0.10},
            "architecture": {"failures": 0},
        },
    }


def _spec(exp_id="exp_a", family="family-a"):
    return {
        "id": exp_id,
        "title": exp_id,
        "trial_family": family,
        "definition": {"switch": exp_id},
        "start_date": "2026-07-28",
        "expires_date": "2026-10-31",
        "primary_metric": "research.edge",
        "promotion_guards": _guards(),
        "rollback_guards": _guards(),
        "challenger_pointer": {
            "kind": "git",
            "pointer": f"git:{exp_id}",
            "content_hash": ("b" if exp_id == "exp_a" else "c") * 64,
        },
        "minimums": {
            "forward_days": 20,
            "mature_events": 10,
            "unique_events": 0,
            "regimes": 2,
        },
        "rollback_window_runs": 3,
    }


def _recommended(tmp_path, exp_id="exp_a", family="family-a"):
    path = tmp_path / "registry.json"
    if not path.exists():
        registry.set_stable_baseline(
            path,
            name="wave4",
            pointer="git:6a06bc8",
            content_hash="a" * 64,
            approved_by="user",
            approved_at="2026-07-28T12:00:00+08:00",
        )
    registry.register_experiment(path, _spec(exp_id, family))
    promotion.evaluate_experiment(
        path,
        exp_id,
        _facts(),
        evaluated_at="2026-08-20T12:00:00+08:00",
    )
    return path


def test_promotion_recommendation_cannot_activate_without_approval(tmp_path):
    path = _recommended(tmp_path)

    with pytest.raises(registry.RegistryError, match="APPROVED"):
        registry.activate_experiment(
            path,
            "exp_a",
            activated_by="user",
            activated_at="2026-08-21T12:00:00+08:00",
        )

    record = registry.get_experiment(path, "exp_a")
    assert record["status"] == "RECOMMENDED"
    assert registry.load_registry(path)["active_by_family"] == {}


def test_approve_records_human_identity_but_does_not_activate(tmp_path):
    path = _recommended(tmp_path)
    got = registry.approve_experiment(
        path,
        "exp_a",
        approved_by="qingbin",
        approved_at="2026-08-20T13:00:00+08:00",
        approval_expires_at="2026-08-27T13:00:00+08:00",
        note="reviewed guard evidence",
    )

    assert got["status"] == "APPROVED"
    assert got["approval"]["approved_by"] == "qingbin"
    assert got["approval"]["evaluation_hash"] == got["latest_evaluation"]["evaluation_hash"]
    assert got["activated_at"] is None
    assert registry.load_registry(path)["active_by_family"] == {}


def test_approval_requires_recommendation_and_nonempty_actor(tmp_path):
    path = tmp_path / "registry.json"
    registry.set_stable_baseline(
        path,
        name="wave4",
        pointer="git:6a06bc8",
        content_hash="a" * 64,
        approved_by="user",
    )
    registry.register_experiment(path, _spec())

    with pytest.raises(registry.RegistryError, match="RECOMMENDED"):
        registry.approve_experiment(path, "exp_a", approved_by="user")

    path = _recommended(tmp_path)
    with pytest.raises(registry.RegistryError, match="approved_by"):
        registry.approve_experiment(path, "exp_a", approved_by="")


def test_approval_timestamp_cannot_predate_evaluation(tmp_path):
    path = _recommended(tmp_path)

    with pytest.raises(registry.RegistryError, match="predate"):
        registry.approve_experiment(
            path,
            "exp_a",
            approved_by="user",
            approved_at="2026-08-19T12:00:00+08:00",
        )


def test_expired_approval_cannot_activate(tmp_path):
    path = _recommended(tmp_path)
    registry.approve_experiment(
        path,
        "exp_a",
        approved_by="user",
        approved_at="2026-08-20T13:00:00+08:00",
        approval_expires_at="2026-08-21T13:00:00+08:00",
    )

    with pytest.raises(registry.RegistryError, match="approval expired"):
        registry.activate_experiment(
            path,
            "exp_a",
            activated_by="user",
            activated_at="2026-08-22T12:00:00+08:00",
        )


def test_activation_keeps_stable_rollback_pointer_and_audit(tmp_path):
    path = _recommended(tmp_path)
    approved = registry.approve_experiment(
        path,
        "exp_a",
        approved_by="qingbin",
        approved_at="2026-08-20T13:00:00+08:00",
        approval_expires_at="2026-08-27T13:00:00+08:00",
    )
    stable_before = registry.load_registry(path)["stable_baseline"]
    active = registry.activate_experiment(
        path,
        "exp_a",
        activated_by="qingbin",
        activated_at="2026-08-21T12:00:00+08:00",
    )
    payload = registry.load_registry(path)

    assert approved["status"] == "APPROVED"
    assert active["status"] == "ACTIVE"
    assert active["rollback_pointer"] == stable_before
    assert payload["stable_baseline"] == stable_before
    assert payload["active_by_family"] == {"family-a": "exp_a"}
    assert [row["event"] for row in payload["audit"]][-2:] == [
        "EXPERIMENT_APPROVED",
        "EXPERIMENT_ACTIVATED",
    ]


def test_same_trial_family_allows_only_one_active_experiment(tmp_path):
    path = _recommended(tmp_path, "exp_a", "shared")
    registry.approve_experiment(
        path,
        "exp_a",
        approved_by="user",
        approved_at="2026-08-21T12:00:00+08:00",
    )
    registry.activate_experiment(
        path,
        "exp_a",
        activated_by="user",
        activated_at="2026-08-22T12:00:00+08:00",
    )
    _recommended(tmp_path, "exp_b", "shared")
    registry.approve_experiment(
        path,
        "exp_b",
        approved_by="user",
        approved_at="2026-08-21T12:00:00+08:00",
    )

    with pytest.raises(registry.RegistryError, match="already active"):
        registry.activate_experiment(
            path,
            "exp_b",
            activated_by="user",
            activated_at="2026-08-22T12:00:00+08:00",
        )


def test_stable_baseline_cannot_change_while_experiment_is_active(tmp_path):
    path = _recommended(tmp_path)
    registry.approve_experiment(
        path,
        "exp_a",
        approved_by="user",
        approved_at="2026-08-21T12:00:00+08:00",
    )
    registry.activate_experiment(
        path,
        "exp_a",
        activated_by="user",
        activated_at="2026-08-22T12:00:00+08:00",
    )

    with pytest.raises(registry.RegistryError, match="active experiment"):
        registry.set_stable_baseline(
            path,
            name="unsafe",
            pointer="git:unsafe",
            content_hash="d" * 64,
            approved_by="user",
        )
