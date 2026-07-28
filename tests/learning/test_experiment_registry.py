"""Wave 5 experiment registry identity and lifecycle contracts."""
from __future__ import annotations

import json

import pytest

from autoresearch.learning import experiment_registry as registry


FIVE_GUARDS = {
    "research": [
        {"metric": "research.excess_t2_delta", "op": "gt", "value": 0.0},
        {"metric": "research.left_tail_delta", "op": "gte", "value": 0.0},
    ],
    "decision": [
        {"metric": "decision.false_buy_delta", "op": "lte", "value": 0.0},
        {"metric": "decision.false_abstention_delta", "op": "lte", "value": 0.0},
    ],
    "token": [
        {"metric": "token.cost_per_mature_delta_pct", "op": "lte", "value": 0.05},
    ],
    "speed": [
        {"metric": "speed.p90_delta_pct", "op": "lte", "value": 0.05},
    ],
    "architecture": [
        {"metric": "architecture.contract_failures", "op": "eq", "value": 0},
    ],
}

ROLLBACK_GUARDS = {
    "research": [
        {"metric": "research.excess_t2_delta", "op": "gte", "value": -0.01},
    ],
    "decision": [
        {"metric": "decision.false_buy_delta", "op": "lte", "value": 0.02},
    ],
    "token": [
        {"metric": "token.cost_per_mature_delta_pct", "op": "lte", "value": 0.10},
    ],
    "speed": [
        {"metric": "speed.p90_delta_pct", "op": "lte", "value": 0.10},
    ],
    "architecture": [
        {"metric": "architecture.contract_failures", "op": "eq", "value": 0},
    ],
}


def experiment_spec(**overrides):
    spec = {
        "id": "exp_value_quota_v1",
        "title": "Value lane quota challenger",
        "trial_family": "l1_quota",
        "definition": {
            "kind": "config_patch",
            "patch": {"funnel.l2_lane_floor.value": 0.10},
        },
        "start_date": "2026-07-28",
        "expires_date": "2026-10-31",
        "primary_metric": "research.excess_t2_delta",
        "promotion_guards": FIVE_GUARDS,
        "rollback_guards": ROLLBACK_GUARDS,
        "challenger_pointer": {
            "kind": "git",
            "pointer": "git:challenger-value-v1",
            "content_hash": "b" * 64,
        },
        "minimums": {
            "forward_days": 20,
            "mature_events": 10,
            "unique_events": 30,
            "regimes": 2,
        },
        "rollback_window_runs": 10,
    }
    spec.update(overrides)
    return spec


def set_baseline(path):
    return registry.set_stable_baseline(
        path,
        name="main-wave4",
        pointer="git:6a06bc8",
        content_hash="a" * 64,
        approved_by="user",
        approved_at="2026-07-28T12:00:00+08:00",
        note="Wave 4 accepted baseline",
    )


def test_registration_requires_stable_baseline(tmp_path):
    path = tmp_path / "registry.json"

    with pytest.raises(registry.RegistryError, match="stable baseline"):
        registry.register_experiment(
            path,
            experiment_spec(),
            registered_at="2026-07-28T12:01:00+08:00",
        )


def test_baseline_and_registration_are_atomic_hashed_and_replayable(tmp_path):
    path = tmp_path / "registry.json"
    set_baseline(path)
    first = registry.register_experiment(
        path,
        experiment_spec(),
        registered_at="2026-07-28T12:01:00+08:00",
    )
    before = path.read_bytes()
    replay = registry.register_experiment(
        path,
        experiment_spec(),
        registered_at="2099-01-01T00:00:00Z",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert first == replay
    assert path.read_bytes() == before
    assert first["status"] == "PREREGISTERED"
    assert len(first["definition_hash"]) == 64
    assert first["rollback_pointer"] == payload["stable_baseline"]
    assert payload["schema_version"] == 1
    assert len(payload["experiments"]) == 1
    assert [entry["event"] for entry in payload["audit"]] == [
        "STABLE_BASELINE_SET",
        "EXPERIMENT_PREREGISTERED",
    ]
    assert not path.with_name("registry.json.tmp").exists()


def test_duplicate_id_with_changed_definition_fails_loudly(tmp_path):
    path = tmp_path / "registry.json"
    set_baseline(path)
    registry.register_experiment(path, experiment_spec())

    with pytest.raises(registry.RegistryError, match="definition hash"):
        registry.register_experiment(
            path,
            experiment_spec(title="Changed definition"),
        )


@pytest.mark.parametrize(
    "mutator,match",
    [
        (
            lambda spec: spec.update(
                promotion_guards={
                    k: v for k, v in FIVE_GUARDS.items() if k != "architecture"
                }
            ),
            "five guard",
        ),
        (
            lambda spec: spec.update(expires_date="2026-01-01"),
            "expiry",
        ),
        (
            lambda spec: spec["challenger_pointer"].update(content_hash="short"),
            "content_hash",
        ),
    ],
)
def test_registration_rejects_invalid_contracts(tmp_path, mutator, match):
    path = tmp_path / "registry.json"
    set_baseline(path)
    spec = experiment_spec()
    mutator(spec)

    with pytest.raises(registry.RegistryError, match=match):
        registry.register_experiment(path, spec)


def test_corrupt_registry_is_not_silently_replaced(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(registry.RegistryError, match="invalid registry"):
        registry.load_registry(path)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_new_baseline_preserves_addressable_history(tmp_path):
    path = tmp_path / "registry.json"
    old = set_baseline(path)
    new = registry.set_stable_baseline(
        path,
        name="main-wave5",
        pointer="git:new-baseline",
        content_hash="c" * 64,
        approved_by="user",
        approved_at="2026-08-31T12:00:00+08:00",
        note="accepted after observation",
    )
    payload = registry.load_registry(path)

    assert old in payload["baseline_history"]
    assert new == payload["stable_baseline"]
