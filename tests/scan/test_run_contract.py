"""RunContract: canonical identity, integrity validation, and atomic persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.run_contract import (
    RunContract,
    load_run_contract,
    write_run_contract,
)

DATE = "2026-07-28"
NOW = datetime(2026, 7, 28, 12, 34, 56, 123456, tzinfo=timezone.utc)


def _build(user_config: dict | None = None) -> RunContract:
    return RunContract.build(
        analysis_date=DATE,
        user_config=user_config or {},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare", "cap_floor_yi": 30.0, "include_bj": True},
        stage_budgets={"l3_finalist_max": 10, "pinned_cap": 5, "pinned_ttl_days": 10},
        artifact_schema_versions={"market_pack": 1, "finalists": 1},
        git_sha="abc1234",
        now=NOW,
    )


def test_config_hash_is_canonical_but_contract_identity_is_explicit():
    left = _build({"agents": {"l4_card": {"effort": "high"}}, "redteam_prob": 0.1})
    right = _build({"redteam_prob": 0.1, "agents": {"l4_card": {"effort": "high"}}})
    assert left.config_hash == right.config_hash
    assert left.contract_hash == right.contract_hash
    assert left.run_id == "20260728T123456123456Z"


def test_contract_hash_covers_pinned_and_data_policy():
    base = _build()
    changed = RunContract.build(
        analysis_date=DATE,
        user_config={},
        pinned={"kept": [{"code": "000001"}], "expired": []},
        data_policy={"source": "tushare", "cap_floor_yi": 30.0, "include_bj": True},
        stage_budgets={"l3_finalist_max": 10, "pinned_cap": 5, "pinned_ttl_days": 10},
        artifact_schema_versions={"market_pack": 1, "finalists": 1},
        git_sha="abc1234",
        now=NOW,
    )
    assert changed.contract_hash != base.contract_hash


def test_write_and_load_round_trip(tmp_path):
    path = tmp_path / "run_contract.json"
    written = write_run_contract(path, _build())
    assert written == path
    loaded = load_run_contract(path)
    assert loaded.to_dict() == _build().to_dict()
    assert not (tmp_path / "run_contract.json.tmp").exists()


def test_load_rejects_tampered_contract(tmp_path):
    path = write_run_contract(tmp_path / "run_contract.json", _build())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["data_policy"]["source"] = "em"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="contract_hash"):
        load_run_contract(path)
