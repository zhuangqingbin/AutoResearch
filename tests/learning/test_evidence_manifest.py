"""证据清单(A0):语义绑定 / cohort-分母隔离 / 冲突降级 / 渲染无字面量。合成,无网络。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.common.ruler import MAIN_RULER
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


# ────────────────────────── Wave12-T8:定义串插值主尺 + 每指标 ruler 字段 ──────────────────────────


def test_semantics_definitions_no_longer_hardcode_fwd_2_oc():
    """A5(受控语义表自身失控):定义串写死 fwd_2_oc、取值多数早已随 MAIN_RULER 走
    (gate_ledger.tail_rate 已用 MAIN_RULER,COHORTS["t2_mature"] 描述的是"回填成熟"这件
    事本身)。唯一合法保留是 market_fwd2_mean —— 它绑定 zero_buy_ledger.mkt_fwd2,T6 明确
    把这一列钉死为"参考尺,不随主尺漂移"(fwd_2/fwd_5 降参考列保留,不改语义)——定义句故意
    不写字面量 fwd_2_oc(grep 也过不了),而是用 ruler 字段显式钉死它,不能靠插值 MAIN_RULER
    去描述一个永远不等于 MAIN_RULER 的取值源(那才是真正的"定义串与取值源不一致")。
    """
    for name, semantic in SEMANTICS.items():
        assert "fwd_2_oc" not in semantic.definition, (
            f"{name} 定义句仍裸写 fwd_2_oc 字面量")
    assert "fwd_2_oc" not in COHORTS["t2_mature"]
    assert MAIN_RULER in COHORTS["t2_mature"]
    # gate_ledger.tail_rate 真的按 MAIN_RULER 计(gate_ledger.py 已用 MAIN_RULER),定义句必须带它
    assert MAIN_RULER in SEMANTICS["left_tail_protection_rate"].definition
    # market_fwd2_mean 是 T6 明确保留的冻结参考列——ruler 字段钉死为 fwd_2_oc,不随主尺漂移,
    # 定义句也不该谎称它是 MAIN_RULER(此刻恰好是 gap_c1_o2,但概念上两者独立)
    assert SEMANTICS["market_fwd2_mean"].ruler == "fwd_2_oc"
    assert MAIN_RULER not in SEMANTICS["market_fwd2_mean"].definition


def test_semantic_ruler_defaults_to_main_ruler():
    """未显式覆盖的语义(绝大多数)ruler 默认追踪 MAIN_RULER——不用逐条改写。"""
    assert SEMANTICS["scan_day_count"].ruler == MAIN_RULER
    assert SEMANTICS["left_tail_protection_rate"].ruler == MAIN_RULER


def test_metric_ruler_field_is_auto_filled_from_semantic():
    """每指标含 ruler 字段(镜像 source_field 的自动补全 + 一致性守卫)。"""
    manifest = Manifest()
    manifest.add(_metric(metric_id="m", semantic="scan_day_count"))
    assert manifest.metrics["m"]["ruler"] == MAIN_RULER

    # 冻结参考指标(market_fwd2_mean):自动补全应为 fwd_2_oc,不是当前 MAIN_RULER
    frozen_metric = Metric(
        metric_id="mf", semantic="market_fwd2_mean", value=1, numerator=None,
        denominator_id=None, cohort="t2_mature", as_of=None,
        source_paths=[], source_hashes={},
    )
    manifest.add(frozen_metric)
    assert manifest.metrics["mf"]["ruler"] == "fwd_2_oc"


def test_metric_declaring_a_mismatched_ruler_is_rejected():
    """写侧守卫:显式声明的 ruler 与 semantic 绑定的不符 → 当场抛错(镜像 source_field 同款)。"""
    manifest = Manifest()
    with pytest.raises(EvidenceError, match="ruler"):
        manifest.add(_metric(semantic="scan_day_count", ruler="fwd_2_oc"))


def test_validate_catches_ruler_inconsistent_with_semantic():
    """注入「定义串(ruler)与取值源不一致」——A0 同款手法(仿既有 semantic 注入测试)。

    读侧守卫必须 presence-gated:T8 之前落盘的冻结快照(如 docs/research/
    2026-08-01-wave10-gate0-evidence.json)完全没有 ruler 键,不能因为"缺失"就被判违约,
    否则会追溯性地判旧审计记录不合格(该文件不改写、validate() 必须继续放行它)。
    """
    manifest = Manifest()
    manifest.add(_metric(metric_id="m", semantic="scan_day_count"))
    payload = manifest.to_dict()
    assert validate(payload) == []

    # 缺失 ruler 键(模拟 T8 之前的旧快照)—— 不得被判违约
    missing = json.loads(json.dumps(payload, ensure_ascii=False))
    del missing["metrics"]["m"]["ruler"]
    assert validate(missing) == []

    # ruler 键存在但被篡改(谎称这条 scan_day_count 是旧尺算的)—— 必须被逮住
    tampered = json.loads(json.dumps(payload, ensure_ascii=False))
    tampered["metrics"]["m"]["ruler"] = "fwd_2_oc"
    problems = validate(tampered)
    assert problems and any("ruler" in p for p in problems)


def test_frozen_gate0_snapshot_still_passes_validate_after_t8():
    """回归锁:T8 不得让 docs/research/2026-08-01-wave10-gate0-evidence.json 这份历史审计
    记录突然读不过 —— 与 test_wave10_gate0_freeze.py::test_frozen_snapshot_passes_its_own_validator
    是同一断言,这里在 T8 自己的测试文件内再钉一遍(防止未来有人只跑 test_evidence_manifest.py
    就以为够了)。"""
    path = Path("docs/research/2026-08-01-wave10-gate0-evidence.json")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert validate(frozen) == []
