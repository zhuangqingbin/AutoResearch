"""门重标定单测 —— 锁 §4.1 的五条纪律。

1. 两个实验分开(证据增强 ≠ 门松紧),各有自己的 family/margin/判据;
2. n=6 的 33.3% 是 IMMATURE,不是「门无效」;
3. participation 不当单门分母;门总量价值只作背景守卫,不进判据;
4. 影子腿不写生产产物;
5. shrink 不得出现在裁门路径。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.learning import evidence_manifest as em, gate_recal as gr


def _day(root, date, fires, attr_rows):
    d = root / date
    (d / "retro").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fires).to_csv(d / "gate_fires.csv", index=False)
    pd.DataFrame(attr_rows).to_csv(d / "retro" / "attribution.csv", index=False)
    return d


def _fire(code, gate=gr.GATE):
    return {"check": f"OW三门·{gate}", "code": code, "severity": "high", "level": "binding"}


def _attr(code, fwd, *, buyable=True):
    # gap_c1_o2:当前 MAIN_RULER(T16 flip)——_day_facts() 按 attr[MAIN_RULER] 动态读源列,
    # 夹具必须喂这一列的真实名字,不是 gate_attribution 自己固定输出的 "fwd_2_oc" 那一列。
    return {"code": code, "gap_c1_o2": fwd, "buyable": buyable, "tradable": True}


def _n_days(root, n, *, false_kill_每日=1, other_每日=2):
    """造 n 天;每天 1 只错杀 + 2 只拦对,市场中位固定。"""
    for i in range(n):
        date = f"2026-06-{i + 1:02d}"
        fires, attrs = [], []
        for j in range(false_kill_每日):
            code = f"9{i:02d}{j:03d}"
            fires.append(_fire(code))
            attrs.append(_attr(code, 0.10))          # 远高于中位 → FALSE_KILL
        for j in range(other_每日):
            code = f"8{i:02d}{j:03d}"
            fires.append(_fire(code))
            attrs.append(_attr(code, -0.05))         # 低于中位 → CORRECT
        attrs += [_attr(f"7{i:02d}{k:03d}", 0.0) for k in range(5)]   # 市场票,定中位
        _day(root, date, fires, attrs)


# ── 纪律 1:两个实验分开 ──────────────────────────────────────────


def test_the_two_templates_are_distinct_experiments():
    recal, evidence = gr.recal_template(), gr.evidence_template()
    assert recal.experiment_id != evidence.experiment_id
    assert recal.primary_metric != evidence.primary_metric
    assert recal.direction == "lower_is_better"       # 错杀率:降才是好
    assert evidence.direction == "higher_is_better"   # claim 正确率:升才是好
    assert recal.paired_unit == "gate_fire"
    assert evidence.paired_unit == "candidate_day"


def test_both_templates_validate():
    from autoresearch.learning import experiment_template as et

    assert et.validate(gr.recal_template()) == []
    assert et.validate(gr.evidence_template()) == []


def test_recal_template_forbids_optional_stopping():
    assert "不可提前停" in gr.recal_template().stopping_rule
    assert "跑到通过为止" in gr.recal_template().stopping_rule


# ── 纪律 2:小样本 = IMMATURE,不是结论 ─────────────────────────


def test_n6_is_immature_not_a_gate_verdict(tmp_path):
    """设计稿的当前读数:n=6,CORRECT 2 / NEUTRAL 2 / FALSE_KILL 2。"""
    _n_days(tmp_path, 2, false_kill_每日=1, other_每日=2)
    result = gr.recal_verdict(tmp_path)
    assert result["n_binding_measured"] == 6
    assert result["verdict"]["verdict"] == "IMMATURE"
    assert result["verdict"]["recommendation"] == "DO_NOT_PROMOTE"


def test_rate_carries_an_interval_so_n6_and_n600_look_different(tmp_path):
    _n_days(tmp_path, 2)
    small = gr.recal_verdict(tmp_path)["false_kill_rate"]
    assert small["lo"] is not None and small["hi"] is not None
    assert small["hi"] - small["lo"] > 0.3          # n=6 的区间必然很宽


def test_no_measured_sample_reports_none_not_zero(tmp_path):
    (tmp_path / "2026-06-01").mkdir(parents=True)
    result = gr.recal_verdict(tmp_path)
    assert result["n_binding_measured"] == 0
    assert result["false_kill_rate"]["point"] is None    # 「没有观测」≠「错杀率 0」
    assert result["verdict"]["verdict"] == "IMMATURE"


def test_enough_mature_events_with_no_false_kills_is_equivalent(tmp_path):
    """样本够 + 零错杀 → EQUIVALENT(错杀率在可接受上限内),成熟门不是永远拦着。"""
    _n_days(tmp_path, 25, false_kill_每日=0, other_每日=2)
    result = gr.recal_verdict(tmp_path)
    assert result["n_binding_measured"] == 50
    assert result["FALSE_KILL"] == 0
    assert result["verdict"]["verdict"] == "EQUIVALENT"
    assert "无需重标定" in result["interpretation"]
    assert "不是「门有价值」的证明" in result["interpretation"]


def test_high_false_kill_rate_is_the_positive_finding(tmp_path):
    """错杀率显著高于上限 → 模板报 FAIL,而这正是本实验最强的**阳性**发现。

    翻译表存在的理由就在这里:有人会把 `FAIL` 读成「实验失败了」。
    """
    _n_days(tmp_path, 25, false_kill_每日=2, other_每日=0)
    result = gr.recal_verdict(tmp_path)
    assert result["verdict"]["verdict"] == "FAIL"
    assert "建议立项重标定" in result["interpretation"]


def test_pass_is_unreachable_by_construction():
    """比率恒 ≥0 → 区间不可能整体低于 −margin。明说出来,别让人以为算错了。"""
    assert "不可达" in gr.RECAL_INTERPRETATION["PASS"]
    assert set(gr.RECAL_INTERPRETATION) == set(
        __import__("autoresearch.learning.experiment_template",
                   fromlist=["VERDICTS"]).VERDICTS)


def test_counterfactual_labels_are_recorded(tmp_path):
    _n_days(tmp_path, 3)
    ledger = gr.recal_ledger(tmp_path)
    assert set(ledger["counterfactual"]) <= {
        "would_have_helped", "would_have_hurt", "no_material_difference", "unknown"}
    assert (ledger["counterfactual"] == "would_have_helped").sum() == 3


def test_only_the_target_gate_is_in_the_ledger(tmp_path):
    _day(tmp_path, "2026-06-01",
         [_fire("900001"), _fire("900002", gate="主力真在")],
         [_attr("900001", 0.1), _attr("900002", 0.1), _attr("700001", 0.0)])
    ledger = gr.recal_ledger(tmp_path)
    assert set(ledger["gate"]) == {gr.GATE}


# ── 纪律 3:背景守卫不进判据 ────────────────────────────────────


def test_background_guard_is_explicitly_out_of_the_verdict(tmp_path):
    _n_days(tmp_path, 3)
    guard = gr.background_guard(tmp_path)
    assert guard["in_verdict"] is False
    assert "不能替代单门归因" in guard["why"]
    payload = gr.build(tmp_path)
    assert "n_all_gates_measured" not in json.dumps(payload["recal"]["verdict"])


def test_participation_cannot_be_a_false_kill_denominator():
    """§4.1 的口径纪律:participation 是人口,不是单门因果分母。"""
    manifest = em.Manifest()
    manifest.declare_denominator(em.Denominator(
        "p_n", 49, "gate_participation", "participation 人口"))
    with pytest.raises(em.EvidenceError):
        manifest.add(em.Metric("x.false_kill_rate", "false_kill_rate", 0.33, 16,
                               "p_n", "gate_participation", "2026-08-03", [], {}))


def test_participation_count_semantic_is_confined_to_its_cohort():
    manifest = em.Manifest()
    manifest.declare_denominator(em.Denominator(
        "p_n", 49, "gate_participation", "participation 人口"))
    manifest.add(em.Metric("gate_participation.x.n", "gate_participation_count",
                           49, 49, "p_n", "gate_participation", "2026-08-03", [], {}))
    with pytest.raises(em.EvidenceError):
        manifest.add(em.Metric("bad", "gate_participation_count", 49, 49, None,
                               "experiment_eligible", "2026-08-03", [], {}))


# ── 纪律 4:影子腿不写生产产物 ──────────────────────────────────


def test_production_write_is_caught(tmp_path):
    d = _day(tmp_path, "2026-06-01", [_fire("900001")], [_attr("900001", 0.1)])
    before = gr.production_snapshot(d)
    gr.assert_no_production_writes(d, before)          # 没动 → 不抛

    import os
    import time

    time.sleep(0.01)
    os.utime(d / "gate_fires.csv", None)               # 模拟影子腿顺手写了一笔
    with pytest.raises(gr.GateRecalError, match="影子腿改动了生产产物"):
        gr.assert_no_production_writes(d, before)


def test_recal_verdict_declares_zero_production_side_effects(tmp_path):
    _n_days(tmp_path, 3)
    assert "现门不变" in gr.recal_verdict(tmp_path)["production_side_effects"]


# ── 纪律 5:shrink 不得裁门 ─────────────────────────────────────


@pytest.mark.parametrize("source", ["learning.shrink.rate", "gate_ledger.shrunk_tail",
                                    "SHRINKAGE_estimate"])
def test_shrink_source_is_rejected_on_the_gate_path(source):
    with pytest.raises(em.EvidenceError, match="收缩"):
        em.assert_not_shrink_derived(source)


def test_clean_source_passes_the_shrink_guard():
    em.assert_not_shrink_derived("gate_attribution.false_kill_rate")


def test_validate_flags_a_shrunk_false_kill_rate():
    payload = {
        "schema_version": em.SCHEMA_VERSION,
        "denominators": {},
        "metrics": {"g": {"metric_id": "g", "semantic": "false_kill_rate",
                          "cohort": "experiment_eligible",
                          "source_field": "gate_attribution.false_kill_rate",
                          "denominator_id": None}},
    }
    assert em.validate(payload) == []
    payload["metrics"]["g"]["source_field"] = "learning.shrink.false_kill_rate"
    assert any("收缩" in p for p in em.validate(payload))


# ── 口径漂移守卫 ────────────────────────────────────────────────


def test_gate_definition_drift_is_caught():
    payload = {"schema_version": em.SCHEMA_VERSION, "denominators": {}, "metrics": {},
               "gate_definition": {**em.GATE_DEFINITION,
                                   "false_kill_threshold": "excess_2 >= +0.05"}}
    assert any("口径漂移" in p for p in em.validate(payload))


def test_gate_definition_names_the_three_banned_sources():
    banned = " ".join(em.GATE_DEFINITION["not_derived_from"])
    assert "cross_calib" in banned and "participation" in banned and "shrink" in banned


def test_recal_payload_carries_the_definition(tmp_path):
    _n_days(tmp_path, 2)
    assert gr.recal_verdict(tmp_path)["gate_definition"] == em.GATE_DEFINITION


# ── 实验① 证据增强:两臂纪律 ───────────────────────────────────


def _arms(root, date, rows):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "_gate_evidence_arms.json").write_text(
        json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")


def test_missing_arms_is_immature_not_no_difference(tmp_path):
    result = gr.evidence_verdict(tmp_path)
    assert result["n_pairs"] == 0
    assert result["verdict"]["verdict"] == "IMMATURE"


def test_mismatched_gate_version_voids_the_pair(tmp_path):
    """两臂门版本不同 = 不是同一件事,整对作废并记数,不悄悄比。"""
    _arms(tmp_path, "2026-06-01", [
        {"code": "000001", "arm": gr.ARM_WITH, "gate_version": "v3",
         "claim_correct": 1.0, "gate_state": "PASS"},
        {"code": "000001", "arm": gr.ARM_WITHOUT, "gate_version": "v4",
         "claim_correct": 0.0, "gate_state": "FAIL"},
    ])
    result = gr.evidence_verdict(tmp_path)
    assert result["n_pairs"] == 0 and result["dropped_pairs"] == 1


def test_single_arm_row_is_dropped(tmp_path):
    _arms(tmp_path, "2026-06-01", [
        {"code": "000001", "arm": gr.ARM_WITH, "gate_version": "v3",
         "claim_correct": 1.0, "gate_state": "PASS"}])
    result = gr.evidence_verdict(tmp_path)
    assert result["dropped_pairs"] == 1 and result["n_pairs"] == 0


def test_valid_pairs_produce_a_delta_and_disagreement_rate(tmp_path):
    for i in range(1, 26):
        _arms(tmp_path, f"2026-06-{i:02d}", [
            {"code": f"00000{i % 7}", "arm": gr.ARM_WITH, "gate_version": "v3",
             "claim_correct": 1.0, "gate_state": "PASS"},
            {"code": f"00000{i % 7}", "arm": gr.ARM_WITHOUT, "gate_version": "v3",
             "claim_correct": 0.0, "gate_state": "FAIL"},
        ])
    result = gr.evidence_verdict(tmp_path)
    assert result["n_pairs"] == 25
    assert result["gate_disagreement_rate"] == 1.0
    assert result["claim_correct_delta"]["point"] == pytest.approx(1.0)


def test_corrupt_arm_file_is_skipped_not_fatal(tmp_path):
    d = tmp_path / "2026-06-01"
    d.mkdir(parents=True)
    (d / "_gate_evidence_arms.json").write_text("{ nope", encoding="utf-8")
    assert gr.evidence_verdict(tmp_path)["n_pairs"] == 0


# ── 汇总 / CLI ─────────────────────────────────────────────────


def test_build_keeps_the_two_experiments_separate(tmp_path):
    _n_days(tmp_path, 3)
    payload = gr.build(tmp_path)
    assert payload["recal"]["experiment"] == gr.RECAL_FAMILY
    assert payload["evidence"]["experiment"] == gr.EVIDENCE_FAMILY
    assert payload["current_ruling"] == "IMMATURE"


def test_cli_writes_both_artifacts(tmp_path):
    _n_days(tmp_path, 3)
    out_json, out_md = tmp_path / "o.json", tmp_path / "o.md"
    assert gr.main(["--scan-root", str(tmp_path), "--json-out", str(out_json),
                    "--md-out", str(out_md)]) == 0
    md = out_md.read_text(encoding="utf-8")
    assert "两个分开的实验" in md and "不进判据" in md
    assert json.loads(out_json.read_text(encoding="utf-8"))["gate"] == gr.GATE
