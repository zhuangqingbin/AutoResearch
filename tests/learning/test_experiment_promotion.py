"""Wave 5 maturity and five-guard promotion contracts."""
from __future__ import annotations

import json

from autoresearch.learning import experiment_registry as registry
from autoresearch.learning import promotion


def _guards(primary_op="gt", primary_value=0.0):
    return {
        "research": [
            {
                "metric": "research.excess_t2_delta",
                "op": primary_op,
                "value": primary_value,
            },
            {
                "metric": "research.left_tail_delta",
                "op": "gte",
                "value": 0.0,
            },
        ],
        "decision": [
            {"metric": "decision.false_buy_delta", "op": "lte", "value": 0.0},
            {
                "metric": "decision.false_abstention_delta",
                "op": "lte",
                "value": 0.0,
            },
        ],
        "token": [
            {
                "metric": "token.cost_per_mature_delta_pct",
                "op": "lte",
                "value": 0.05,
            }
        ],
        "speed": [
            {"metric": "speed.p90_delta_pct", "op": "lte", "value": 0.05}
        ],
        "architecture": [
            {
                "metric": "architecture.contract_failures",
                "op": "eq",
                "value": 0,
            }
        ],
    }


def _registered(tmp_path, *, primary_op="gt", primary_value=0.0):
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
            "id": "exp_value_v1",
            "title": "value challenger",
            "trial_family": "l1_quota",
            "definition": {"patch": {"value": 0.1}},
            "start_date": "2026-07-28",
            "expires_date": "2026-10-31",
            "primary_metric": "research.excess_t2_delta",
            "promotion_guards": _guards(primary_op, primary_value),
            "rollback_guards": _guards("gte", -0.01),
            "challenger_pointer": {
                "kind": "git",
                "pointer": "git:value-v1",
                "content_hash": "b" * 64,
            },
            "minimums": {
                "forward_days": 20,
                "mature_events": 10,
                "unique_events": 30,
                "regimes": 2,
            },
            "rollback_window_runs": 5,
        },
        registered_at="2026-07-28T12:01:00+08:00",
    )
    return path


def _facts(**metrics):
    base = {
        "research": {"excess_t2_delta": 0.02, "left_tail_delta": 0.01},
        "decision": {
            "false_buy_delta": -0.01,
            "false_abstention_delta": -0.01,
        },
        "token": {"cost_per_mature_delta_pct": -0.10},
        "speed": {"p90_delta_pct": -0.10},
        "architecture": {"contract_failures": 0},
    }
    for domain, values in metrics.items():
        base.setdefault(domain, {}).update(values)
    return {
        "sample": {
            "forward_days": 20,
            "mature_events": 10,
            "unique_events": 30,
            "regimes": ["risk_off", "range"],
            "trial_count": 3,
            "definition_breakpoints": ["2026-08-05: data fix"],
        },
        "metrics": base,
    }


def test_insufficient_sample_is_immature_even_when_guards_pass(tmp_path):
    path = _registered(tmp_path)
    facts = _facts()
    facts["sample"]["forward_days"] = 19

    got = promotion.evaluate_experiment(
        path,
        "exp_value_v1",
        facts,
        evaluated_at="2026-08-20T12:00:00+08:00",
    )

    assert got["status"] == "IMMATURE"
    assert got["recommendation"] == "CONTINUE_SHADOW"
    assert got["maturity"]["shortfalls"] == {"forward_days": {"need": 20, "have": 19}}
    assert registry.get_experiment(path, "exp_value_v1")["status"] == "PREREGISTERED"


def test_missing_metric_is_unknown_not_fail(tmp_path):
    path = _registered(tmp_path)
    facts = _facts()
    del facts["metrics"]["decision"]["false_buy_delta"]

    got = promotion.evaluate_experiment(path, "exp_value_v1", facts)

    assert got["status"] == "UNKNOWN"
    assert got["recommendation"] == "CONTINUE_SHADOW"
    assert got["guards"]["decision"]["status"] == "UNKNOWN"
    assert not any(
        condition["status"] == "FAIL"
        for condition in got["guards"]["decision"]["conditions"]
    )


def test_one_guard_breach_fails_without_weighted_offset(tmp_path):
    path = _registered(tmp_path)
    got = promotion.evaluate_experiment(
        path,
        "exp_value_v1",
        _facts(decision={"false_buy_delta": 0.03}),
    )

    assert got["status"] == "FAIL"
    assert got["recommendation"] == "REJECT"
    assert got["guards"]["decision"]["status"] == "FAIL"
    assert got["guards"]["research"]["status"] == "PASS"


def test_all_guards_and_explicit_improvement_recommend_only(tmp_path):
    path = _registered(tmp_path)
    got = promotion.evaluate_experiment(
        path,
        "exp_value_v1",
        _facts(),
        evaluated_at="2026-08-31T12:00:00+08:00",
    )
    record = registry.get_experiment(path, "exp_value_v1")

    assert got["status"] == "PASS"
    assert got["recommendation"] == "PROMOTE"
    assert got["improvements"] == ["research.excess_t2_delta"]
    assert got["trial_count"] == 3
    assert got["definition_breakpoints"] == ["2026-08-05: data fix"]
    assert record["status"] == "RECOMMENDED"
    assert record["approval"] is None
    assert record["activated_at"] is None


def test_guard_pass_without_explicit_improvement_still_fails(tmp_path):
    path = _registered(tmp_path, primary_op="gte", primary_value=-0.01)
    got = promotion.evaluate_experiment(
        path,
        "exp_value_v1",
        _facts(research={"excess_t2_delta": 0.0}),
    )

    assert got["guards"]["research"]["status"] == "PASS"
    assert got["status"] == "FAIL"
    assert got["reason"] == "NO_EXPLICIT_IMPROVEMENT"


def test_evaluation_replay_is_byte_idempotent_and_json_can_be_published(tmp_path):
    path = _registered(tmp_path)
    out = tmp_path / "promotion.json"
    kwargs = {
        "evaluated_at": "2026-08-31T12:00:00+08:00",
        "json_out": out,
    }
    first = promotion.evaluate_experiment(path, "exp_value_v1", _facts(), **kwargs)
    before = path.read_bytes()
    second = promotion.evaluate_experiment(path, "exp_value_v1", _facts(), **kwargs)

    assert first == second
    assert path.read_bytes() == before
    assert json.loads(out.read_text(encoding="utf-8")) == first


def test_evaluation_after_expiry_marks_expired(tmp_path):
    path = _registered(tmp_path)
    got = promotion.evaluate_experiment(
        path,
        "exp_value_v1",
        _facts(),
        evaluated_at="2026-11-01T00:00:00+08:00",
    )

    assert got["status"] == "EXPIRED"
    assert got["recommendation"] == "DO_NOT_PROMOTE"
    assert registry.get_experiment(path, "exp_value_v1")["status"] == "EXPIRED"
