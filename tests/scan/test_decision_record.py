"""DecisionRecord domain facts, hashes, and atomic book persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.decision_record import (
    DecisionRecord,
    load_decision_records,
    write_decision_records,
)
from autoresearch.scan.run_contract import RunContract, write_run_contract

NOW = datetime(2026, 7, 28, 17, 0, tzinfo=timezone.utc)


def _record(code="000001", final="Hold", contract_hash=None):
    return DecisionRecord.build(
        analysis_date="2026-07-28",
        contract_hash=contract_hash,
        code=code,
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states={
            "主力真在": "PASS",
            "业绩真兑现": "FAIL",
            "估值不透支": "PASS",
        },
        early_stop=None,
        ensemble_ratings=[],
        final_rating=final,
        proposal="HOLD",
        reason="rubric:业绩真兑现",
        evidence_refs=[f"finalists.csv#{code}", f"details/{code}.md"],
        first_rejection_stage="L4_RUBRIC",
    )


def _contract(scan):
    contract = RunContract.build(
        analysis_date=scan.name,
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc",
        now=NOW,
    )
    write_run_contract(scan / "run_contract.json", contract)
    return contract


def test_record_round_trip_and_hash(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    contract = _contract(scan)
    record = _record(contract_hash=contract.contract_hash)
    path = write_decision_records(scan, [record])
    loaded = load_decision_records(path)
    assert path == scan / "decision_records.json"
    assert loaded["000001"].to_dict() == record.to_dict()
    assert len(record.record_hash) == 64


def test_book_is_sorted_and_semantically_stable(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    left = write_decision_records(scan, [_record("600000"), _record("000001")])
    before = left.read_bytes()
    right = write_decision_records(scan, [_record("000001"), _record("600000")])
    assert right.read_bytes() == before
    raw = json.loads(right.read_text(encoding="utf-8"))
    assert [row["code"] for row in raw["records"]] == ["000001", "600000"]


def test_load_rejects_tampered_record(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    path = write_decision_records(scan, [_record()])
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["records"][0]["final_rating"] = "Buy"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_decision_records(path)


def test_write_rejects_contract_mismatch(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    contract = _contract(scan)
    assert contract.contract_hash != "a" * 64
    with pytest.raises(ValueError, match="contract_hash"):
        write_decision_records(scan, [_record(contract_hash="a" * 64)])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "../1"),
        ("final_rating", "Strong Buy"),
        ("proposal", "WAIT"),
        ("gate_states", {"主力真在": "YES"}),
    ],
)
def test_record_rejects_invalid_domain_values(field, value):
    kwargs = {
        "analysis_date": "2026-07-28",
        "contract_hash": None,
        "code": "000001",
        "source_rating": "Hold",
        "rubric_rating": "Hold",
        "gate_states": {},
        "early_stop": None,
        "ensemble_ratings": [],
        "final_rating": "Hold",
        "proposal": "HOLD",
        "reason": "x",
        "evidence_refs": [],
        "first_rejection_stage": "L4_RUBRIC",
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        DecisionRecord.build(**kwargs)


# ── E3b 裁定 1(task-2.4):`proposal` 语义降格,但**仍旧读评级** ──────────────────
#
# active 期 `proposal` 是「研究评级派生的提案」,不是 BUY 决策(BUY 只在
# `_relative_buy_decision.json` 里)。**故意不改成读决策文件**:`decision_records.json`
# 正是 `relative_buy.build_decision` 的输入,反向依赖 = 循环。下面这条锁的就是这件事 ——
# 谁把 decision_finalize 改成读决策文件,它立刻变红(两个断言各堵一种改法)。


def test_proposal_stays_rating_derived_and_never_reads_the_decision_file(
    tmp_path, monkeypatch,
):
    from autoresearch.scan import relative_buy
    from autoresearch.scan.decision_finalize import _build_decision_records

    scan = tmp_path / "2026-08-19"
    (scan / "details").mkdir(parents=True)
    (scan / "details" / "600000.md").write_text(
        "# 决策卡\n**Rating**: Overweight\n", encoding="utf-8")
    # 当日决策文件说 BLOCKED(零 BUY)——若 decision_finalize 去读它,proposal 会变成 HOLD/—
    (scan / relative_buy.DECISION_FILENAME).write_text(json.dumps(
        {"date": "2026-08-19", "mode": "active", "buys": [], "blocked": True},
        ensure_ascii=False), encoding="utf-8")

    def _boom(*_a, **_k):                    # ② 连"读一下"都不许:循环依赖必须结构性不存在
        raise AssertionError("decision_finalize 不得读决策文件(E3b 裁定 1:反向依赖即循环)")

    monkeypatch.setattr(relative_buy, "load_decision", _boom)

    rows = [{"code": "600000", "rating": "Overweight", "_source_rating": "Overweight",
             "_post_verify_rating": "Overweight"}]
    records = _build_decision_records(scan, rows, {}, {})

    assert [r.proposal for r in records] == ["BUY"]        # ① 仍由评级派生
    assert [r.final_rating for r in records] == ["Overweight"]


def test_post_verify_rating_is_hashed_and_old_versions_keep_original_shape():
    from dataclasses import replace

    from autoresearch.scan.run_contract import sha256_json
    record=_record()
    arguments={key:value for key,value in record.to_dict().items() if key not in {'schema_version','record_hash'}}
    arguments['post_verify_rating']='Underweight'
    new=DecisionRecord.build(**arguments)
    assert new.schema_version==3 and new.post_verify_rating=='Underweight'
    assert new.record_hash!=record.record_hash
    assert DecisionRecord.from_dict(new.to_dict())==new
    for version in (1,2):
        legacy=replace(new,schema_version=version)
        raw=legacy.to_dict()
        assert 'post_verify_rating' not in raw
        raw['record_hash']=sha256_json({k:v for k,v in raw.items() if k!='record_hash'})
        loaded=DecisionRecord.from_dict(raw)
        assert loaded.post_verify_rating is None and loaded.to_dict()==raw
