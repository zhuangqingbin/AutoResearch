"""run_health / churn / L4 阶段效能 / 买单计数 / index.md + assemble 接线。合成,无网络。

spec: docs/specs/2026-07-02-scan-observability-design.md
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from autoresearch.scan.health import (
    count_buys,
    finalist_churn,
    index_md,
    l4_phase_stats,
    ledger_freshness,
    run_health,
    write_run_health,
)
from autoresearch.scan.run_contract import RunContract, write_run_contract

CARD_OW = "# 决策卡\n**Rating**: Overweight\n进入P4倾向: Buy\n**一行多空**:多:强 ｜ 空:贵\n"
CARD_STOP = "# 早停卡\n**Rating**: Hold\n早停因: 主力流出 → 停\n"
CARD_REUSE = "♻️ 复用@2026-06-30 卡\n**Rating**: Hold\n"


def _mk_day(root, date, codes=("000001",), cards=None, l1_rows=None, meta=None):
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": c, "name": f"N{c}", "sector": "半导体"} for c in codes]).to_csv(
        d / "finalists.csv", index=False)
    for code, text in (cards or {}).items():
        (d / "details" / f"{code}.md").write_text(text, encoding="utf-8")
    if l1_rows is not None:
        pd.DataFrame(l1_rows).to_csv(d / "L1_recall_top1000.csv", index=False)
    if meta is not None:
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_run_health_core(tmp_path):
    l1 = [{"code": f"{i:06d}", "composite": 50.0, "pe": (None if i % 2 else 30.0),
           "main_net_ratio": 0.01} for i in range(10)]                    # pe 50% NaN → 降级
    d = _mk_day(tmp_path, "2026-07-02", cards={"000001": CARD_OW}, l1_rows=l1,
                meta={"regime": "risk_off", "l2_engine": "stratified(sn_composite)"})
    h = run_health(d)
    assert h["date"] == "2026-07-02" and h["regime"] == "risk_off"
    assert "pe" in h["degraded_fields"] and h["nan_rates"]["pe"] == 0.5
    assert h["artifacts"]["finalists.csv"] and "L2_gbdt_top200.csv" in h["missing"]
    assert "L2_gbdt_top200.csv" in h["core_missing"] and "market_view.md" not in h["core_missing"]
    assert h["counts"]["cards"] == 1 and h["counts"]["buys"] == 1
    p = write_run_health(d)
    assert json.loads(p.read_text(encoding="utf-8"))["counts"]["buys"] == 1


def _write_contract(day, *, config=None):
    contract = RunContract.build(
        analysis_date=day.name,
        user_config=config or {},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={"l3_finalist_max": 10},
        artifact_schema_versions={"market_pack": 1},
        git_sha="abc",
        now=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    write_run_contract(day / "run_contract.json", contract)
    return contract


def test_run_health_contract_absent_is_advisory(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    assert run_health(d)["run_contract"] == {
        "status": "ABSENT",
        "run_id": None,
        "contract_hash": None,
        "errors": [],
        "echo_config_match": None,
        "market_pack_config_match": None,
    }


def test_run_health_contract_ok_when_echoes_match(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    cfg = {"agents": {"l3_rank": {"effort": "high"}}}
    contract = _write_contract(d, config=cfg)
    (d / "user_config_echo.json").write_text(json.dumps(cfg), encoding="utf-8")
    (d / "market_pack.json").write_text(json.dumps({"user_config": cfg}), encoding="utf-8")
    result = run_health(d)["run_contract"]
    assert result["status"] == "OK"
    assert result["contract_hash"] == contract.contract_hash
    assert result["echo_config_match"] is True
    assert result["market_pack_config_match"] is True


def test_run_health_contract_invalid_on_config_drift(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    _write_contract(d, config={"redteam_prob": 0.1})
    (d / "user_config_echo.json").write_text(
        json.dumps({"redteam_prob": 0.2}),
        encoding="utf-8",
    )
    result = run_health(d)["run_contract"]
    assert result["status"] == "INVALID"
    assert result["echo_config_match"] is False
    assert "user_config_echo config_hash mismatch" in result["errors"]


def test_run_health_stage_results_absent_is_advisory(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    result = run_health(d)["stage_results"]
    assert result == {
        "status": "ABSENT",
        "counts": {},
        "failed": [],
        "degraded": [],
        "skipped": [],
        "invalid_files": [],
        "contract_hash_mismatches": [],
    }


def test_run_health_summarizes_valid_stage_results(tmp_path):
    from autoresearch.scan.stage_result import record_stage_result

    d = _mk_day(tmp_path, "2026-07-28")
    record_stage_result(
        d, stage="prelude", status="DEGRADED", artifacts=[], metrics={},
        warnings=["universe: boom"], error=None,
    )
    record_stage_result(
        d, stage="gate1", status="FAILED", artifacts=[], metrics={},
        warnings=[], error="L2 missing",
    )
    result = run_health(d)["stage_results"]
    assert result["status"] == "OK"
    assert result["counts"] == {"DEGRADED": 1, "FAILED": 1}
    assert result["failed"] == ["gate1"]
    assert result["degraded"] == ["prelude"]


def test_run_health_flags_stage_contract_mismatch_and_corruption(tmp_path):
    from autoresearch.scan.stage_result import (
        StageResult,
        record_stage_result,
        write_stage_result,
    )

    d = _mk_day(tmp_path, "2026-07-28")
    _write_contract(d)
    record_stage_result(
        d, stage="gate1", status="SUCCEEDED", artifacts=[], metrics={},
        warnings=[], error=None,
    )
    mismatch = StageResult.build(
        stage="gate2",
        analysis_date=d.name,
        status="SUCCEEDED",
        artifacts=[],
        metrics={},
        warnings=[],
        error=None,
        contract_hash="b" * 64,
    )
    write_stage_result(d, mismatch)
    (d / "stage_results" / "broken.json").write_text("{", encoding="utf-8")

    result = run_health(d)["stage_results"]
    assert result["status"] == "INVALID"
    assert result["invalid_files"] == ["broken.json"]
    assert result["contract_hash_mismatches"] == ["gate2"]


def test_decision_records_health_absent_is_advisory(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    result = run_health(d)["decision_records"]
    assert result == {
        "status": "ABSENT",
        "n_records": 0,
        "contract_hash_match": None,
        "final_ratings_match": None,
        "rating_mismatches": [],
        "early_stop_match": None,
        "early_stop_mismatches": [],
        "error": None,
    }


def test_decision_records_health_detects_legacy_rating_mismatch(tmp_path):
    from autoresearch.scan.decision_record import (
        DecisionRecord,
        write_decision_records,
    )

    d = _mk_day(tmp_path, "2026-07-28")
    record = DecisionRecord.build(
        analysis_date=d.name,
        contract_hash=None,
        code="000001",
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states={},
        early_stop=None,
        ensemble_ratings=[],
        final_rating="Hold",
        proposal="HOLD",
        reason="rubric:Hold",
        evidence_refs=[],
        first_rejection_stage="L4_RUBRIC",
    )
    write_decision_records(d, [record])
    (d / "_final_ratings.json").write_text(
        json.dumps({"000001": "Overweight"}),
        encoding="utf-8",
    )
    result = run_health(d)["decision_records"]
    assert result["status"] == "MISMATCH"
    assert result["final_ratings_match"] is False
    assert result["rating_mismatches"] == ["000001"]


def test_decision_records_health_rejects_tampered_book(tmp_path):
    from autoresearch.scan.decision_record import (
        DecisionRecord,
        write_decision_records,
    )

    d = _mk_day(tmp_path, "2026-07-28")
    record = DecisionRecord.build(
        analysis_date=d.name,
        contract_hash=None,
        code="000001",
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states={},
        early_stop=None,
        ensemble_ratings=[],
        final_rating="Hold",
        proposal="HOLD",
        reason="rubric:Hold",
        evidence_refs=[],
        first_rejection_stage="L4_RUBRIC",
    )
    path = write_decision_records(d, [record])
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["records"][0]["final_rating"] = "Buy"
    path.write_text(json.dumps(raw), encoding="utf-8")

    result = run_health(d)["decision_records"]
    assert result["status"] == "INVALID"
    assert "hash" in result["error"]
    assert run_health(d)["counts"]["buys"] is None


def test_post_run_health_absent_is_advisory_and_read_only(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    result = run_health(d)["post_run"]
    assert result == {
        "status": "ABSENT",
        "n_events": 0,
        "expected": 0,
        "succeeded": 0,
        "pending": 0,
        "pending_consumers": [],
        "failed_consumers": [],
        "error": None,
    }
    assert not (d / "outbox").exists()


def test_post_run_health_reports_consumer_backlog(tmp_path):
    from autoresearch.scan.outbox import OutboxEvent, emit_events
    from autoresearch.scan.post_run import initialize_consumer_state

    d = _mk_day(tmp_path, "2026-07-28")
    event = OutboxEvent.build(
        event_type="RUN_FINALIZED",
        analysis_date=d.name,
        run_id="run-1",
        contract_hash=None,
        aggregate_id="run-1",
        payload={"n_buys": 0, "n_decisions": 1},
        created_at="2026-07-28T10:00:00Z",
    )
    emit_events(d, [event])
    initialize_consumer_state(d)
    result = run_health(d)["post_run"]
    assert result["status"] == "BACKLOG"
    assert result["n_events"] == 1
    assert result["expected"] == 8
    assert result["pending"] == 8
    assert result["failed_consumers"] == []


def test_post_run_health_rejects_corrupt_events(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    (d / "outbox").mkdir()
    (d / "outbox" / "events.json").write_text("{", encoding="utf-8")
    result = run_health(d)["post_run"]
    assert result["status"] == "INVALID"
    assert "JSONDecodeError" in result["error"]


def test_retro_health_reports_facts_gates_consumers_and_shadow_counts(
    tmp_path,
):
    from autoresearch.learning.abstention_ledger import (
        write_abstention_verdict,
    )
    from autoresearch.scan.outbox import OutboxEvent, emit_events

    d = _mk_day(tmp_path, "2026-07-28")
    (d / "retro").mkdir()
    attr = pd.DataFrame(
        [
            {"code": "000001", "fwd_2_oc": 0.03},
            {"code": "000002", "fwd_2_oc": -0.03},
            {"code": "000003", "fwd_2_oc": 0.0},
        ]
    )
    attr.to_csv(d / "retro" / "attribution.csv", index=False)
    rejection = pd.DataFrame(
        [
            {
                "date": d.name,
                "code": "000001",
                "first_rejection_stage": "L4_GATE_MAIN",
                "final_action": "ABSTAIN",
                "gate_state_quality": "COMPLETE",
                "buyable": True,
                "mature": True,
                "excess_2": 0.03,
                "opportunity": True,
            },
            {
                "date": d.name,
                "code": "000002",
                "first_rejection_stage": "L4_MULTI_GATE",
                "final_action": "ABSTAIN",
                "gate_state_quality": "COMPLETE",
                "buyable": True,
                "mature": True,
                "excess_2": -0.03,
                "opportunity": False,
            },
            {
                "date": d.name,
                "code": "000003",
                "first_rejection_stage": "DATA_UNDECIDABLE",
                "final_action": "ABSTAIN",
                "gate_state_quality": "UNKNOWN",
                "buyable": True,
                "mature": True,
                "excess_2": 0.0,
                "opportunity": False,
            },
        ]
    )
    rejection.to_csv(d / "retro" / "rejection_attribution.csv", index=False)
    write_abstention_verdict(d, rejection)
    (d / "shadow").mkdir()
    pd.DataFrame({"code": ["000002", "000003"]}).to_csv(
        d / "shadow" / "l3_audit_candidates.csv",
        index=False,
    )
    event = OutboxEvent.build(
        event_type="RETRO_FINALIZED",
        analysis_date=d.name,
        run_id=None,
        contract_hash=None,
        aggregate_id=d.name,
        payload={
            "attribution_hash": "a" * 64,
            "rejection_attribution_hash": "b" * 64,
        },
        created_at="2026-07-28T10:00:00Z",
    )
    emit_events(d, [event])

    result = run_health(d)["retro"]

    assert result["rejection"]["status"] == "OK"
    assert result["rejection"]["n_rows"] == 3
    assert result["abstention"]["verdict"] == "FALSE"
    assert result["abstention"]["data_quality"] == "DEGRADED"
    assert result["gate_counts"] == {"unique": 1, "multi": 1, "unknown": 1}
    assert result["shadow_queues"]["l3_audit"] == 2
    assert result["retro_consumers"]["status"] == "BACKLOG"
    assert result["retro_consumers"]["pending"] > 0


def test_retro_health_absence_is_advisory(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    result = run_health(d)["retro"]
    assert result["status"] == "ABSENT"
    assert result["rejection"]["status"] == "ABSENT"


def test_finalist_churn(tmp_path):
    _mk_day(tmp_path, "2026-07-01", codes=("000001", "000002"))
    d = _mk_day(tmp_path, "2026-07-02", codes=("000002", "000003"))
    ch = finalist_churn(d)
    assert ch["prev_date"] == "2026-07-01" and ch["n_repeat"] == 1 and ch["repeat_rate"] == 0.5
    assert finalist_churn(tmp_path / "2026-07-01") is None      # 无更早日


def test_l4_phase_stats_and_p4_flip(tmp_path):
    d = _mk_day(tmp_path, "2026-07-02",
                cards={"000001": CARD_OW, "000002": CARD_STOP, "000003": CARD_REUSE})
    ph = l4_phase_stats(d)
    assert ph["n_cards"] == 3 and ph["n_earlystop"] == 1 and ph["n_reused"] == 1
    assert ph["n_full"] == 1 and ph["p4_seen"] == 1 and ph["p4_flips"] == 1   # 进P4倾向Buy→终OW


def test_count_buys_verify_downgrade(tmp_path):
    d = _mk_day(tmp_path, "2026-07-02", cards={"000001": CARD_OW})
    assert count_buys(d) == 1
    pd.DataFrame([{"code": "000001", "verdict": "降级", "bull": "", "bear": "b",
                   "trigger": "", "consensus": "2/3"}]).to_csv(d / "verify.csv", index=False)
    assert count_buys(d) == 0                                    # OW 降级→Hold,踢出买单


def test_index_md_links_and_prev_run(tmp_path):
    d = _mk_day(tmp_path / "ctx", "2026-07-02", cards={"000001": CARD_OW})
    rep = tmp_path / "reports"
    (rep / "20260701_0900").mkdir(parents=True)
    (rep / "20260701_0900" / "summary.md").write_text("x", encoding="utf-8")
    rd = rep / "20260702_1200"
    (rd / "details").mkdir(parents=True)
    (rd / "details" / "N甲.md").write_text("x", encoding="utf-8")
    (rd / "trace").mkdir()
    (rd / "trace" / "funnel.md").write_text("x", encoding="utf-8")
    s = index_md(d, rd)
    assert "summary.md" in s and "N甲" in s and "funnel.md" in s
    assert "20260701_0900" in s and "健康一行" in s


def test_index_md_shows_anns_unavailable_line(tmp_path):
    """anns_d 已退役:index.md 不再对此静默——每次跑动都有一行显式标注(此前仅 JSON 里可查,
    肉眼看报告完全无感,正是三个扫描日 news 列全零无人察觉的成因之一)。此形态 = 目录压根
    不存在(`anns_empty_rate` 走 `is None` 分支)——与下一条"真生产形态"并存两条,
    见 `test_index_md_shows_anns_unavailable_line_when_rate_is_one` 的说明。"""
    d = _mk_day(tmp_path / "ctx", "2026-07-02", cards={"000001": CARD_OW})
    rep = tmp_path / "reports" / "20260702_1200"
    (rep / "details").mkdir(parents=True)
    s = index_md(d, rep)
    assert "anns_d" in s and "退役" in s and "不可用" in s


def test_index_md_shows_anns_unavailable_line_when_rate_is_one(tmp_path):
    """Review Round 1 Important-2:上一条测试用的 `_mk_day` 不建 `L3_news/` 目录,走的是
    `anns_empty_rate is None` 分支——但**真生产退役日**是另一形态:`L3_news/` 里躺着一堆
    `[]` 空 json(`rate == 1.0`)。此前只测 is-None 分支时,变异 F2(把渲染判据从
    `anns_expected` 改成 `anns_empty_rate is None`,= 只在"连目录都没有"时报、真退役日反而
    静默,正是本 task 要治的病)能带绿全套件上线。本测试补造 `rate==1.0` 形态锁住它。"""
    d = _mk_day(tmp_path / "ctx4", "2026-07-02", cards={"000001": CARD_OW})
    (d / "L3_news").mkdir()
    (d / "L3_news" / "000001.json").write_text("[]", encoding="utf-8")
    rep = tmp_path / "reports4" / "20260702_1200"
    (rep / "details").mkdir(parents=True)
    s = index_md(d, rep)
    assert "anns_d" in s and "退役" in s and "不可用" in s


def test_index_md_hides_anns_line_when_unexpected_data_present(tmp_path):
    """anns_empty_rate<1.0(真出现部分数据,反常)→ 不误报"不可用"(anns_expected 语义不变)。"""
    d = _mk_day(tmp_path / "ctx2", "2026-07-02", cards={"000001": CARD_OW})
    (d / "L3_news").mkdir()
    (d / "L3_news" / "000001.json").write_text(json.dumps([{"title": "回购"}]), encoding="utf-8")
    rep = tmp_path / "reports2" / "20260702_1200"
    (rep / "details").mkdir(parents=True)
    s = index_md(d, rep)
    assert "anns_d 已退役" not in s


def test_retro_health_section(tmp_path):
    """retro 的运行健康节:降级字段/核心缺产物才出声;无恙/缺文件 → []。"""
    from autoresearch.learning.retro import _health_section
    assert _health_section(tmp_path) == []
    (tmp_path / "run_health.json").write_text(json.dumps(
        {"degraded_fields": ["pe"], "core_missing": ["L2_gbdt_top200.csv"]}), encoding="utf-8")
    s = "\n".join(_health_section(tmp_path))
    assert "运行健康" in s and "pe" in s and "数据病" in s and "L2_gbdt_top200.csv" in s
    (tmp_path / "run_health.json").write_text(json.dumps(
        {"degraded_fields": [], "core_missing": []}), encoding="utf-8")
    assert _health_section(tmp_path) == []


def test_ledger_freshness_pending_retro_days(tmp_path):
    """复盘欠账日数:有 attribution.csv(fwd 已实现)但无 done.json 的天数——纯本地文件判定,
    刻意不摸 retro.pending_days()/交易日历(避免网络依赖拖慢 run_health,见函数 docstring)。"""
    scan_root = tmp_path / "scan"
    for day, done in (("2026-07-07", False), ("2026-07-08", False), ("2026-07-09", True)):
        r = scan_root / day / "retro"
        r.mkdir(parents=True)
        (r / "attribution.csv").write_text("code\n", encoding="utf-8")
        if done:
            (r / "done.json").write_text("{}", encoding="utf-8")
    fr = ledger_freshness(scan_root / "2026-07-09", learning_root=tmp_path / "learning_nx")
    assert fr["pending_retro_days"] == 2
    assert fr["pending_retro_list"] == ["2026-07-07", "2026-07-08"]


def test_ledger_freshness_lag_days(tmp_path):
    """四账本 mtime 滞后 scan 日数:从未生成 → None;有 mtime → 数落后了几个 scan 日。"""
    import os
    from datetime import datetime

    scan_root = tmp_path / "scan"
    for day in ("2026-07-08", "2026-07-09", "2026-07-10"):
        (scan_root / day).mkdir(parents=True)
    learning_root = tmp_path / "learning"
    learning_root.mkdir()
    p = learning_root / "channel_ledger.md"
    p.write_text("x", encoding="utf-8")
    ts = datetime(2026, 7, 8).timestamp()
    os.utime(p, (ts, ts))
    fr = ledger_freshness(scan_root / "2026-07-10", learning_root=learning_root)
    assert fr["ledger_lag_days"]["channel_ledger"] == 2       # 07-09、07-10 两个 scan 日晚于账本 mtime
    assert fr["ledger_lag_days"]["gate_ledger"] is None       # 从未生成
    assert fr["ledger_lag_days"]["zero_buy_ledger"] is None
    assert fr["ledger_lag_days"]["changelog_ledger"] is None


def test_ledger_freshness_buy_count_consistent(tmp_path):
    """journal vs zero_buy 买单计数:同一 attribution.csv 喂两本账,D5 修复后应一致(✓)。"""
    d = tmp_path / "scan" / "2026-07-08"
    (d / "details").mkdir(parents=True)
    (d / "retro").mkdir()
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text("**Rating**: Overweight\n", encoding="utf-8")
    pd.DataFrame([{"code": "000001", "bought": True, "fwd_1_oo": 0.01}]).to_csv(
        d / "retro" / "attribution.csv", index=False)
    fr = ledger_freshness(d, learning_root=tmp_path / "learning_nx")
    assert fr["buy_count_consistent"] is True
    assert fr["buy_count_mismatches"] == []


def test_ledger_freshness_flags_buy_count_mismatch(tmp_path, monkeypatch):
    """回归检测器:两本账若被人为改分叉,一致性行必须能测出来(✗ + 具体日期)。"""
    import autoresearch.learning.journal as journal_mod
    d = tmp_path / "scan" / "2026-07-08"
    (d / "details").mkdir(parents=True)
    (d / "retro").mkdir()
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text("**Rating**: Overweight\n", encoding="utf-8")
    pd.DataFrame([{"code": "000001", "bought": True, "fwd_1_oo": 0.01}]).to_csv(
        d / "retro" / "attribution.csv", index=False)
    monkeypatch.setattr(journal_mod, "roll",
                        lambda root=None: pd.DataFrame([{"date": "2026-07-08", "buys": 0.0}]))
    fr = ledger_freshness(d, learning_root=tmp_path / "learning_nx")
    assert fr["buy_count_consistent"] is False
    assert "2026-07-08" in fr["buy_count_mismatches"]


def test_ledger_freshness_no_overlap_is_none(tmp_path):
    """两本账无重叠日(如 zero_buy 从没跑过)→ 一致性字段 None,不误判 True/False。"""
    d = _mk_day(tmp_path, "2026-07-09")
    fr = ledger_freshness(d, learning_root=tmp_path / "learning_nx")
    assert fr["buy_count_consistent"] is None


def test_run_health_includes_ledger_freshness(tmp_path):
    """run_health 顶层挂载账本新鲜度节(交付物:当天可读 run_health.json 里验)。"""
    d = _mk_day(tmp_path, "2026-07-02", cards={"000001": CARD_OW})
    h = run_health(d)
    assert "ledger_freshness" in h
    assert h["ledger_freshness"]["pending_retro_days"] == 0     # 无 retro/ 目录 → 0,不炸


def test_assemble_writes_health_and_index(tmp_path):
    """assemble.run 落 run_health.json(staging+trace)+ index.md(报告目录)。"""
    from autoresearch.scan.assemble import run as assemble_run
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    out = assemble_run("2026-07-02", scan_dir=d, out_root=tmp_path / "rep",
                       hhmm="1200", run_date="2026-07-02")
    base = out.parent
    assert (d / "run_health.json").exists()
    assert (base / "trace" / "run_health.json").exists()
    assert (base / "index.md").exists() and "扫描现场索引" in (base / "index.md").read_text(encoding="utf-8")


# ══ Wave7 P1:final_ratings 必须含 ensemble 折回那条腿 ════════════════════════
#
# assemble 里跑的是**两个** fold 循环(verify + ensemble),而 health.final_ratings 此前
# 只跑了 verify 那个 —— docstring 却写着「与 assemble 同口径」。
# 后果:sell_review 把 Sell 折回 Underweight 后本函数仍报 Sell(07-24/07-27 的 300857 实锤,
# 与同目录 _final_ratings.json 直接打架);ow_review 只向下折,一旦发生,buy_ledger /
# count_buys / paper_nav / shadow_buys 会把已折成 Hold 的卡继续算作买单 = 幽灵买单。
# 历史上尚未发生 ow 折回(全量重放 0 例)—— 既有账本没被污染是运气,不是设计。
# retro.py 早已绕过本函数直读 _final_ratings.json(「P0-2 坏账③修复」),那是消费侧补丁。


def _mk_fold_day(tmp_path, rating: str, ens: dict | None):
    import json as _json

    import pandas as _pd
    d = tmp_path / "2026-07-27"
    (d / "details").mkdir(parents=True)
    _pd.DataFrame([{"code": "300857", "name": "协创", "lane": "pinned"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "300857.md").write_text(
        f"〔卡契约 v3〕\n# 决策卡\n**Rating**: {rating}\n", encoding="utf-8")
    if ens is not None:
        (d / "_ensemble_300857.json").write_text(_json.dumps(ens), encoding="utf-8")
    return d


def test_final_ratings_prefers_decision_record(tmp_path):
    from autoresearch.scan.decision_record import (
        DecisionRecord,
        write_decision_records,
    )
    from autoresearch.scan.health import final_ratings

    d = _mk_fold_day(tmp_path, "Overweight", None)
    record = DecisionRecord.build(
        analysis_date=d.name,
        contract_hash=None,
        code="300857",
        source_rating="Overweight",
        rubric_rating="Overweight",
        gate_states={},
        early_stop=None,
        ensemble_ratings=[],
        final_rating="Hold",
        proposal="HOLD",
        reason="verify:降级",
        evidence_refs=[],
        first_rejection_stage="VERIFY",
    )
    write_decision_records(d, [record])
    (d / "_final_ratings.json").write_text(
        json.dumps({"300857": "Overweight"}),
        encoding="utf-8",
    )

    assert final_ratings(d) == {"300857": "Hold"}


def test_final_ratings_applies_sell_review_fold(tmp_path):
    """sell_review 只向温和折:卡判 Sell、三票中位 Underweight → 终评 Underweight。"""
    from autoresearch.scan.health import final_ratings
    d = _mk_fold_day(tmp_path, "Sell", {"code": "300857", "ratings": ["Sell", "Underweight", "Underweight"],
                                   "median": "Underweight", "spread": 1, "degraded": False,
                                   "trigger": "sell_review"})
    assert final_ratings(d)["300857"] == "Underweight"


def test_final_ratings_applies_ow_review_fold_down(tmp_path):
    """ow_review 只向下折:卡判 Overweight、中位 Hold → 终评 Hold(否则是幽灵买单)。"""
    from autoresearch.scan.health import final_ratings
    d = _mk_fold_day(tmp_path, "Overweight", {"code": "300857", "ratings": ["Overweight", "Hold", "Hold"],
                                         "median": "Hold", "spread": 1, "degraded": False,
                                         "trigger": "ow_review"})
    assert final_ratings(d)["300857"] == "Hold"


def test_final_ratings_respects_degraded_no_fold(tmp_path):
    """复核 run 不齐(degraded)→ 原样不折,交人裁 —— 与 assemble 语义一致。"""
    from autoresearch.scan.health import final_ratings
    d = _mk_fold_day(tmp_path, "Sell", {"code": "300857", "ratings": ["Sell", "Underweight"],
                                   "median": "Underweight", "spread": 1, "degraded": True,
                                   "trigger": "sell_review"})
    assert final_ratings(d)["300857"] == "Sell"


def test_final_ratings_without_ensemble_unchanged(tmp_path):
    """无复核文件 → 老路逐字不破(presence-gated)。"""
    from autoresearch.scan.health import final_ratings
    d = _mk_fold_day(tmp_path, "Hold", None)
    assert final_ratings(d)["300857"] == "Hold"
