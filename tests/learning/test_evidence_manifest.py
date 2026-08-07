"""证据清单(A0):语义绑定 / cohort-分母隔离 / 冲突降级 / 渲染无字面量。合成,无网络。"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.learning.evidence_manifest import (
    COHORTS,
    SEMANTICS,
    Denominator,
    EvidenceError,
    Manifest,
    Metric,
    _parse_paper_nav,
    build,
    build_day,
    file_hash,
    render,
    validate,
)


def _metric(**overrides):
    base = {
        "metric_id": "m1", "semantic": "scan_day_count", "value": 1,
        "numerator": 1, "denominator_id": None, "cohort": "raw_run",
        "as_of": "2026-07-31", "source_paths": [], "source_hashes": {},
    }
    base.update(overrides)
    return Metric(**base)


# ────────────────── 验收 1:「39% = 错杀率」注入必须变红 ──────────────────

def test_left_tail_rate_cannot_be_labelled_false_kill_rate():
    """gate_ledger 的 39% 是左尾保护率;把它挂到 false_kill_rate 的语义上必须抛错。

    这是 Wave10 立案时点名的误读 —— 守卫必须钉在**语义与来源字段的绑定**上,
    而不是靠文档里写一句「注意区分」。
    """
    tail = SEMANTICS["left_tail_protection_rate"]
    false_kill = SEMANTICS["false_kill_rate"]
    assert tail.source_field != false_kill.source_field
    # 两个语义的 cohort 允许集不重叠于 experiment_eligible:左尾率只活在迁移 cohort
    assert "experiment_eligible" not in tail.cohorts
    assert "experiment_eligible" in false_kill.cohorts

    manifest = Manifest()
    manifest.declare_denominator(Denominator(
        "d_tail", 63, "legacy_migration", "被拦票 fwd_2 非空数"))
    # 合法:左尾率挂 legacy cohort
    manifest.add(_metric(metric_id="ok", semantic="left_tail_protection_rate",
                         value=0.39, denominator_id="d_tail",
                         cohort="legacy_migration"))
    # 非法:同一个 39% 改称错杀率并挂到实验 cohort
    with pytest.raises(EvidenceError, match="不允许 cohort"):
        manifest.add(_metric(metric_id="bad", semantic="left_tail_protection_rate",
                             value=0.39, denominator_id="d_tail",
                             cohort="experiment_eligible"))


def test_validate_catches_semantic_injection_in_frozen_payload():
    """冻结快照被人手改语义 → `validate` 必须报出来(读侧也有守卫,不只写侧)。"""
    manifest = Manifest()
    manifest.declare_denominator(Denominator(
        "d_tail", 63, "legacy_migration", "被拦票 fwd_2 非空数"))
    manifest.add(_metric(metric_id="gate.tail", semantic="left_tail_protection_rate",
                         value=0.39, denominator_id="d_tail",
                         cohort="legacy_migration"))
    payload = manifest.to_dict()
    assert validate(payload) == []

    # 注入手法一:换标签 + 换 cohort → 跨 cohort 引用分母被逮
    tampered = json.loads(json.dumps(payload, ensure_ascii=False))
    tampered["metrics"]["gate.tail"]["semantic"] = "false_kill_rate"
    tampered["metrics"]["gate.tail"]["cohort"] = "experiment_eligible"
    assert validate(tampered)

    # 注入手法二(更隐蔽):只换标签,cohort 保持 legacy_migration —— 它本来就在
    # false_kill_rate 的允许集里,分母规则管不到。必须由 semantic↔来源字段绑定逮住。
    sneaky = json.loads(json.dumps(payload, ensure_ascii=False))
    sneaky["metrics"]["gate.tail"]["semantic"] = "false_kill_rate"
    problems = validate(sneaky)
    assert problems and any("语义注入" in p for p in problems)


def test_no_two_semantics_share_a_source_field():
    """绑定表本身的守卫:谁想把左尾率和错杀率『统一』到同一个来源,这里先红。"""
    fields = [semantic.source_field for semantic in SEMANTICS.values()]
    assert len(fields) == len(set(fields)), "两个 semantic 指向同一来源字段"
    assert (SEMANTICS["left_tail_protection_rate"].source_field
            != SEMANTICS["false_kill_rate"].source_field)


def test_metric_declaring_a_mismatched_source_field_is_rejected():
    manifest = Manifest()
    with pytest.raises(EvidenceError, match="不符"):
        manifest.add(_metric(semantic="false_kill_rate",
                             cohort="experiment_eligible",
                             source_field="gate_ledger.tail_rate"))


def test_validate_rejects_cohort_outside_the_semantics_allowlist():
    """来源字段没动、只把 cohort 挪到不允许的组 —— 独立于来源绑定的第二道读侧守卫。"""
    manifest = Manifest()
    manifest.add(_metric(metric_id="tail", semantic="left_tail_protection_rate",
                         value=0.39, cohort="legacy_migration"))
    payload = manifest.to_dict()
    assert validate(payload) == []
    payload["metrics"]["tail"]["cohort"] = "t2_mature"   # 左尾率不许挂成熟日 cohort
    problems = validate(payload)
    assert problems and any("不允许 cohort" in p for p in problems)


def test_validate_rejects_unknown_semantic():
    manifest = Manifest()
    manifest.add(_metric(metric_id="x"))
    payload = manifest.to_dict()
    payload["metrics"]["x"]["semantic"] = "错杀率"
    assert any("未知 semantic" in p for p in validate(payload))


# ────────────── 验收 2:raw 30 与 mature 26 不得落同一个分母 ──────────────

def test_denominator_id_is_owned_by_exactly_one_cohort():
    manifest = Manifest()
    manifest.declare_denominator(Denominator(
        "scan_days", 30, "raw_run", "journal 行数"))
    with pytest.raises(EvidenceError, match="不能再登记成"):
        manifest.declare_denominator(Denominator(
            "scan_days", 26, "t2_mature", "成熟日数"))


def test_metric_cannot_borrow_another_cohorts_denominator():
    """把 t2_mature 的比例挂到 raw_run 的 30 天分母上 —— 正是首稿犯的错。"""
    manifest = Manifest()
    manifest.declare_denominator(Denominator(
        "journal_scan_days", 30, "raw_run", "journal 行数"))
    with pytest.raises(EvidenceError, match="跨 cohort 拼句"):
        manifest.add(_metric(
            metric_id="zero_buy.rate", semantic="zero_buy_day_count",
            value=20, numerator=20, denominator_id="journal_scan_days",
            cohort="t2_mature"))


def test_live_manifest_keeps_raw_and_mature_denominators_apart():
    """活体清单:30(raw)与 26(mature)必须是两个 denominator_id,分属两个 cohort。"""
    manifest = build()
    if "journal_scan_days" not in manifest.denominators:
        pytest.skip("无 journal 数据")
    raw = manifest.denominators["journal_scan_days"]
    mature = manifest.denominators["zero_buy_mature_days"]
    assert raw["cohort"] == "raw_run" and mature["cohort"] == "t2_mature"
    assert raw["value"] != mature["value"]
    for metric in manifest.metrics.values():
        if metric.get("denominator_id"):
            assert manifest.denominators[metric["denominator_id"]]["cohort"] == (
                metric["cohort"])


def test_unknown_cohort_rejected():
    manifest = Manifest()
    with pytest.raises(EvidenceError, match="unknown cohort"):
        manifest.declare_denominator(Denominator("d", 1, "made_up", "x"))
    with pytest.raises(EvidenceError, match="unknown cohort"):
        manifest.add(_metric(cohort="made_up"))


def test_metric_referencing_undeclared_denominator_rejected():
    manifest = Manifest()
    with pytest.raises(EvidenceError, match="未登记的分母"):
        manifest.add(_metric(denominator_id="ghost"))


# ────────────────────────── 冲突降级(不阻断) ──────────────────────────

def test_conflicting_metric_value_is_flagged_and_unquotable():
    manifest = Manifest()
    manifest.add(_metric(metric_id="buys", value=3))
    manifest.add(_metric(metric_id="buys", value=5))
    assert manifest.conflicts and manifest.conflicts[0]["kind"] == "metric_value"
    payload = manifest.to_dict()
    assert payload["metrics"]["buys"]["status"] == "CONFLICT"
    assert not manifest.quotable("buys")
    assert "⚠️" in "\n".join(render(payload))


def test_quotable_requires_ok_status_and_a_value():
    manifest = Manifest()
    manifest.add(_metric(metric_id="ok", value=1))
    manifest.add(_metric(metric_id="empty", value=None))
    assert manifest.quotable("ok")
    assert not manifest.quotable("empty")
    assert not manifest.quotable("missing")


def test_buy_count_disagreement_between_ledgers_is_a_conflict():
    from autoresearch.learning.evidence_manifest import _cross_check_buys

    manifest = Manifest()
    _cross_check_buys(
        manifest,
        pd.DataFrame([{"date": "2026-07-01", "buys": 2}]),
        pd.DataFrame([{"date": "2026-07-01", "n_bought": 0}]),
    )
    assert manifest.conflicts[0]["kind"] == "buy_count_disagreement"
    # 一致时不报
    clean = Manifest()
    _cross_check_buys(
        clean,
        pd.DataFrame([{"date": "2026-07-01", "buys": 2}]),
        pd.DataFrame([{"date": "2026-07-01", "n_bought": 2}]),
    )
    assert not clean.conflicts


# ────────────────────────── paper_nav 解析 ──────────────────────────

def test_paper_nav_takes_the_primary_hold_table_not_the_last(tmp_path):
    """报告里有两张成绩单 —— 抽错一张,四条线全错但看上去很合理。"""
    report = tmp_path / "paper_nav.md"
    report.write_text(
        "# 影子组合成绩单(paper NAV;10% 固定槽·持2交易日·次日开盘进出)\n"
        "- **截至 20260731**:真实 -0.24%(9 笔) vs 影子 -3.16%(81 笔)"
        " vs 影子(sized) -5.86% vs 市场 -9.85%;`真实 − 影子` = 门的价值。\n"
        "- **截至 20260731**:真实 -0.27%(9 笔) vs 影子 -13.77%(81 笔)"
        " vs 影子(sized) -17.15% vs 市场 -9.85%;`真实 − 影子` = 门的价值。\n",
        encoding="utf-8",
    )
    lanes = _parse_paper_nav(report)
    assert lanes["n_summaries"] == 2 and lanes["hold_days"] == 2
    assert lanes["real"] == (-0.0024, 9)
    assert lanes["shadow"] == (-0.0316, 81)
    assert lanes["shadow_sized"] == (-0.0586, 81)
    assert lanes["market_equal_weight"][0] == -0.0985


def test_paper_nav_missing_report_yields_nothing(tmp_path):
    assert _parse_paper_nav(tmp_path / "nope.md") == {}
    assert _parse_paper_nav(
        (tmp_path / "x.md", (tmp_path / "x.md").write_text("noise"))[0]) == {}


# ────────────────────────── 渲染 / 单日 / 指纹 ──────────────────────────

def test_render_is_driven_by_the_manifest_only():
    """渲染层不许有字面量数字 —— 改了 manifest 的值,Markdown 必须跟着变。"""
    manifest = Manifest()
    manifest.declare_denominator(Denominator("d", 7, "raw_run", "七天"))
    manifest.add(_metric(metric_id="a", value=7, numerator=7, denominator_id="d"))
    body = "\n".join(render(manifest))
    assert "`d`=7" in body and "| 7 |" in body

    manifest.denominators["d"]["value"] = 9
    assert "`d`=9" in "\n".join(render(manifest))


def test_render_lists_every_cohort_definition():
    body = "\n".join(render(Manifest()))
    for cohort in COHORTS:
        assert f"`{cohort}`" in body


def test_build_day_records_cohort_membership(tmp_path):
    day = tmp_path / "2026-07-31"
    (day / "retro").mkdir(parents=True)
    manifest = build_day(day)
    membership = manifest.metrics["day.cohort_membership"]
    assert membership["raw_run"] is True
    assert membership["valid_completed"] is False
    assert membership["t2_mature"] is False

    pd.DataFrame([{"code": "000001"}]).to_csv(day / "finalists.csv", index=False)
    pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.0}]).to_csv(
        day / "retro" / "attribution.csv", index=False)
    again = build_day(day).metrics["day.cohort_membership"]
    assert again["valid_completed"] and again["t2_mature"]


def test_file_hash_changes_with_content(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("one")
    first = file_hash(target)
    target.write_text("two")
    assert first != file_hash(target)
    assert file_hash(tmp_path / "missing") is None


def test_manifest_roundtrips_through_json():
    manifest = build()
    payload = json.loads(json.dumps(manifest.to_dict(), ensure_ascii=False))
    assert validate(payload) == []
    assert payload["schema_version"] == 1


# ────────────────────────── registry inventory(family vs pointer_kind)──────────────────────────

def test_registry_inventory_reports_family_not_pointer_kind(tmp_path):
    """`_add_registry` 曾经把 `family` 报成 `challenger_pointer.kind`(如 "shadow_gate")——

    两个不同 trial_family 的影子实验会显示成同一个 pointer_kind,§C2.0「同 family 不得
    并开」的冲突检查因此形同虚设。这条回归锁原住在 test_wave10_experiments.py(借
    wave10 的 exp1_spec 顺带验证),该模块 2026-08-06 D3 删除时以协议①迁移到此处并改用
    自建合成 registry(不再依赖已删模块,也不再依赖生产 registry.json 是否已注册)。
    """
    from autoresearch.learning import experiment_registry as reg

    path = tmp_path / "registry.json"
    reg.set_stable_baseline(
        path, name="base", pointer="git:x", content_hash="a" * 64,
        approved_by="test", approved_at="2026-08-01T00:00:00+08:00", note="",
    )
    metric_names = ["m_research", "m_decision", "m_token", "m_speed", "m_arch"]
    guards = {
        domain: [{"metric": metric, "op": "gt", "value": 0}]
        for domain, metric in zip(
            ("research", "decision", "token", "speed", "architecture"), metric_names,
            strict=True)
    }
    spec = {
        "id": "exp_regress_family_field",
        "title": "regression: family must not read as pointer_kind",
        "trial_family": "some_family",
        "definition": {"kind": "config_patch", "patch": {}},
        "start_date": "2026-08-01",
        "expires_date": "2026-11-30",
        "primary_metric": "m_research",
        "promotion_guards": guards,
        "rollback_guards": guards,
        "challenger_pointer": {"kind": "shadow_gate", "pointer": "x", "content_hash": "b" * 64},
        "minimums": {"forward_days": 1, "mature_events": 1, "unique_events": 0, "regimes": 1},
        "rollback_window_runs": 1,
    }
    reg.register_experiment(path, spec, registered_at="2026-08-01T00:00:00+08:00")

    inventory = build(scan_root=tmp_path / "no_scan_data", registry_path=path).registry_inventory
    record = inventory["experiments"][spec["id"]]
    assert record["family"] == "some_family"
    assert record["pointer_kind"] == "shadow_gate"
    assert record["family"] != record["pointer_kind"]
