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
    left = _build({"agents": {"l4_card": {"effort": "high"}}, "pinned": {"cap": 1}})
    right = _build({"pinned": {"cap": 1}, "agents": {"l4_card": {"effort": "high"}}})
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


# ── v2(2026-08-26 现场留存波):脏树 + prompt 指纹,且 v1 历史契约必须继续可读 ──

def test_v1_contract_still_loads_and_verifies(tmp_path):
    """全部历史 run 的契约都是 v1。读不了 = 把它们的 run_id/身份弄丢(publisher 的
    manifest、run_mode 的冻结快照都按它定位)。v2 只是加字段,v1 的 hash 按当年 payload 现算。"""
    v1 = {
        "schema_version": 1,
        "analysis_date": DATE,
        "run_id": "20260728T123456123456Z",
        "created_at": "2026-07-28T12:34:56.123456Z",
        "git_sha": "abc1234",
        "user_config": {},
        "config_hash": "",
        "agents": {},
        "pinned": {"kept": [], "expired": []},
        "data_policy": {},
        "stage_budgets": {},
        "artifact_schema_versions": {},
        "contract_hash": "",
    }
    from autoresearch.scan.run_contract import sha256_json
    v1["config_hash"] = sha256_json(v1["user_config"])
    payload = {k: v for k, v in v1.items() if k != "contract_hash"}
    v1["contract_hash"] = sha256_json(payload)          # 当年的算法:没有 v2 三键

    p = tmp_path / "run_contract.json"
    p.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    loaded = load_run_contract(p)
    assert loaded.schema_version == 1
    assert loaded.git_dirty is False and loaded.dirty_paths == ()
    assert loaded.prompt_hashes == {}


def test_v1_hash_payload_excludes_v2_fields():
    """变异探针:若 `_hash_payload` 不再按 schema_version 排除 v2 三键,上面那份 v1 契约
    立刻报 hash mismatch —— 全部历史 run 一起读不出来。"""
    from autoresearch.scan.run_contract import _V2_FIELDS
    c = _build()
    assert c.schema_version == 2
    assert all(f in c._hash_payload() for f in _V2_FIELDS)
    v1_like = RunContract(**{**c.to_dict(), "schema_version": 1})
    assert not any(f in v1_like._hash_payload() for f in _V2_FIELDS)


def test_prompt_hashes_and_dirty_enter_contract_hash():
    """prompt 换了内容 → 契约身份必须跟着变。否则「本地改过 agent def 跑的那趟」
    与「干净树跑的那趟」在账面上一模一样。"""
    base = RunContract.build(
        analysis_date=DATE, user_config={}, pinned={}, data_policy={},
        stage_budgets={}, artifact_schema_versions={}, git_sha="abc", now=NOW,
        git_dirty=False, dirty_paths=[], prompt_hashes={"a.md": "11"})
    changed = RunContract.build(
        analysis_date=DATE, user_config={}, pinned={}, data_policy={},
        stage_budgets={}, artifact_schema_versions={}, git_sha="abc", now=NOW,
        git_dirty=False, dirty_paths=[], prompt_hashes={"a.md": "22"})
    dirty = RunContract.build(
        analysis_date=DATE, user_config={}, pinned={}, data_policy={},
        stage_budgets={}, artifact_schema_versions={}, git_sha="abc", now=NOW,
        git_dirty=True, dirty_paths=["x.py"], prompt_hashes={"a.md": "11"})
    assert base.contract_hash != changed.contract_hash
    assert base.contract_hash != dirty.contract_hash
    assert base.git_sha == changed.git_sha == dirty.git_sha   # 同一个 HEAD 却三种身份


def test_resolve_git_dirty_reports_paths(tmp_path, monkeypatch):
    from autoresearch.scan import run_contract as rc

    class _Proc:
        stdout = " M a.py\n?? b/c.md\n\n"

    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: _Proc())
    dirty, paths = rc.resolve_git_dirty(tmp_path)
    assert dirty is True and paths == ["a.py", "b/c.md"]


def test_resolve_git_dirty_degrades_when_not_a_repo(tmp_path, monkeypatch):
    """git 不可用 → (False, []) 而不是抛。此时 `git_sha` 已经是 "unknown",两条合读不误判。"""
    from autoresearch.scan import run_contract as rc

    def _boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(rc.subprocess, "run", _boom)
    assert rc.resolve_git_dirty(tmp_path) == (False, [])


def test_dirty_paths_are_capped():
    from autoresearch.scan import run_contract as rc

    class _Proc:
        stdout = "".join(f" M f{i}.py\n" for i in range(rc.DIRTY_PATHS_CAP + 10))

    import unittest.mock as mock
    with mock.patch.object(rc.subprocess, "run", lambda *a, **k: _Proc()):
        dirty, paths = rc.resolve_git_dirty(".")
    assert dirty is True and len(paths) == rc.DIRTY_PATHS_CAP


def test_unsupported_schema_still_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({**_build().to_dict(), "schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported run contract schema_version"):
        load_run_contract(p)
