"""家族登记必须过 F1 的方案契约 —— 登记不是散文,是机器可校验的冻结方案。

F6 Step 1 建表时只有 W3 三格;2026-09-13 漏斗形状家族(funnel-shape)按同一口径入册。
"""
import json
from pathlib import Path

from autoresearch.contracts import research_experiment as rx

SPEC = Path(__file__).resolve().parents[2] / "docs" / "research" / "2026-09-07-w3-three-grids-family.spec.json"
SPEC_V2 = SPEC.with_name("2026-09-07-w3-three-grids-family-v2.spec.json")
FUNNEL = SPEC.with_name("2026-09-13-funnel-shape-family.spec.json")
SWING = SPEC.with_name("2026-09-26-swing-ruler-family.spec.json")


def test_w3_family_spec_is_a_valid_frozen_experiment():
    spec = rx.validate_spec(json.loads(SPEC.read_text(encoding="utf-8")))
    assert spec["experiment_family"] == "w3-three-grids"
    assert [h["hypothesis_id"] for h in spec["hypotheses"]] == [
        "w3_first_board_pullback", "w3_late_seal_premium", "w3_institution_seat_5_20d"]


def test_seat_grid_is_labelled_on_sensitivity_rulers_not_the_main_ruler():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    seat = next(h for h in spec["hypotheses"] if h["hypothesis_id"] == "w3_institution_seat_5_20d")
    assert "fwd_5_oc" in seat["label"] and "fwd_10_oc" in seat["label"]
    assert set(spec["sensitivity_rulers"]) == {"fwd_5_oc", "fwd_10_oc"}
    assert spec["ruler"] == rx.MAIN_RULER


def test_every_grid_has_a_falsifiable_rejection_and_the_cost_floor():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    for h in spec["hypotheses"]:
        assert h["rejection_condition"].strip()
    assert "0.15" in spec["stop_rule"] and "不自动上线" in spec["stop_rule"]


def test_corrected_family_has_real_identity_and_supported_populations():
    spec = rx.validate_spec(json.loads(SPEC_V2.read_text(encoding="utf-8")))

    assert spec["experiment_id"] != json.loads(SPEC.read_text())["experiment_id"]
    assert spec["engine"] == "codex"
    assert len(spec["code_sha"]) == 40 and set(spec["code_sha"]) != {"0"}
    assert len(spec["input_manifest_hash"]) == 64
    assert set(spec["input_manifest_hash"]) != {"0"}
    assert spec["split"]["test"] == ["2022-03-01", "2026-09-03"]
    claims = " ".join(h["population"] for h in spec["hypotheses"])
    assert "剔 ST" not in claims and "北交所" not in claims and "新股" not in claims
    seat = next(h for h in spec["hypotheses"] if h["hypothesis_id"].startswith("w3_institution"))
    assert "D+1 开盘" in seat["population"] and "fwd_5_oc" in seat["population"]


# ───────────────── 漏斗形状家族(2026-09-13)─────────────────


def test_funnel_shape_family_spec_is_a_valid_frozen_experiment():
    spec = rx.validate_spec(json.loads(FUNNEL.read_text(encoding="utf-8")))
    assert spec["experiment_family"] == "funnel-shape"
    assert [h["hypothesis_id"] for h in spec["hypotheses"]] == [
        "funnel_composite_only", "funnel_composite_plus_diversifiers"]
    assert spec["ruler"] == rx.MAIN_RULER and spec["weighting"] == "day_equal"


def test_funnel_knobs_are_frozen_in_the_registration_not_left_to_the_command_line():
    """80/20、行业帽、style floor 必须在登记里写死 —— 留在 CLI 上就是"试到好看为止"。"""
    spec = json.loads(FUNNEL.read_text(encoding="utf-8"))
    rule = spec["selection_rule"]
    assert isinstance(rule, dict)
    assert rule["hybrid_core_fraction"] == 0.80 and rule["sector_cap_frac"] == 0.20
    assert rule["floors"] == {}          # composite_only 无 provenance → floor 自然为 0


def test_funnel_family_registers_a_falsifiable_bar_and_forward_out_of_sample_days():
    """晋级门槛与停止规则都要能被否掉,并且必须留出严格向前的样本外日。"""
    spec = json.loads(FUNNEL.read_text(encoding="utf-8"))
    for h in spec["hypotheses"]:
        assert "CI 下界" in h["rejection_condition"]
        # 「没有证据切换」不许被写成「两者等价」—— 这是本仓反复栽过的口径
        assert "不等于" in h["rejection_condition"] or "等价" in h["rejection_condition"]
    assert "2026-09-13" in spec["stop_rule"]          # 向前样本外的起算点 = 冻结日
    assert "10" in spec["stop_rule"] and "不自动上线" in spec["stop_rule"]
    assert spec["quality_constraints"].count("不") >= 3   # 不接 prelude/不写 run 目录/不改生产参数


# ───────────────── 10 日尺家族(2026-09-26 daily-engine §5 B2)─────────────────


def test_swing_ruler_family_spec_is_a_valid_frozen_experiment():
    spec = rx.validate_spec(json.loads(SWING.read_text(encoding="utf-8")))
    assert spec["experiment_id"] == "FAM_SWING_RULER_20260926"
    assert spec["experiment_family"] == "swing-ruler"
    assert [h["hypothesis_id"] for h in spec["hypotheses"]] == [
        "swing_h1_hold_plus_finalists", "swing_h2_lowturn_lane",
        "swing_h3_rejection_negative", "swing_h4_e6_r_tier_sign"]
    assert spec["engine"] == "claude" and len(spec["code_sha"]) == 40


def test_swing_family_is_labelled_on_the_swing_ruler_without_replacing_the_main_ruler():
    """契约只许主尺当 `ruler`;10 日尺只能以敏感尺身份出现 —— B0:不替换主尺,B4 再裁。"""
    spec = json.loads(SWING.read_text(encoding="utf-8"))
    assert spec["ruler"] == rx.MAIN_RULER
    assert spec["sensitivity_rulers"] == ["fwd_10_oc"]
    assert all(h["label"] == "fwd_10_oc" for h in spec["hypotheses"][:3])


def test_swing_family_freezes_its_decision_knobs_in_the_registration():
    """块长、样本门、评级集合、FDR 水平全写死在登记里 —— 留在 CLI 上就是「试到好看为止」。"""
    spec = json.loads(SWING.read_text(encoding="utf-8"))
    rule = spec["selection_rule"]
    assert rule["decision_block"] == 10 and rule["block_lengths"] == [1, 5, 10]
    assert rule["ge_hold_ratings"] == ["Buy", "Overweight", "Hold"]
    assert rule["veto_ratings"] == ["Underweight", "Sell"]
    assert rule["fdr_alpha"] == 0.05 and rule["ci_level"] == 0.95
    assert spec["maturity_policy"] == "scan_days >= 40"
    assert "BY" in spec["multiplicity"] and "arbitrary" in spec["multiplicity"]


def test_swing_family_directional_hypotheses_are_falsifiable_and_h4_stays_descriptive():
    spec = json.loads(SWING.read_text(encoding="utf-8"))
    h1, h2, h3, h4 = spec["hypotheses"]
    assert h1["expected_direction"] == h2["expected_direction"] == "positive"
    assert h3["expected_direction"] == "negative"
    for h in (h1, h2, h3):
        assert "CI" in h["rejection_condition"] and "40" in h["rejection_condition"]
        assert "不等于" in h["rejection_condition"]
    assert h4["expected_direction"] == "two_sided" and "描述性" in h4["rejection_condition"]
    assert "不追加第五个假设" in spec["stop_rule"] and "新 experiment_id" in spec["stop_rule"]
    assert "只展示不推" in spec["stop_rule"]


# ─────────── 10 日尺家族 v2(2026-09-26 批 5 复审 I1:判读检验重新登记)───────────

SWING_V2 = SPEC.with_name("2026-09-26-swing-ruler-family-v2.spec.json")


def test_swing_v2_is_a_new_frozen_experiment_that_supersedes_v1():
    """复审 I1:旧 id 的判读检验在 40 天/块长 10 上假阳 ~20%/格;旧 id 从未在生产上读过,
    所以**换 id 重新登记**(不改旧文件 —— 沿革留着),代码出处钉在校准检验落地的那个提交。"""
    v1 = json.loads(SWING.read_text(encoding="utf-8"))
    spec = rx.validate_spec(json.loads(SWING_V2.read_text(encoding="utf-8")))
    assert spec["experiment_id"] == "FAM_SWING_RULER_V2_20260926" != v1["experiment_id"]
    assert spec["experiment_family"] == v1["experiment_family"] == "swing-ruler"
    assert [h["hypothesis_id"] for h in spec["hypotheses"]] == [
        h["hypothesis_id"] for h in v1["hypotheses"]]
    for new, old in zip(spec["hypotheses"], v1["hypotheses"], strict=True):
        for key in ("mechanism", "expected_direction", "metric_definition", "population", "label"):
            assert new[key] == old[key], key             # 只换检验,不动假设与人口
    assert len(spec["code_sha"]) == 40 and spec["code_sha"] != v1["code_sha"]
    assert "FAM_SWING_RULER_20260926" in spec["stop_rule"] and "取代" in spec["stop_rule"]


def test_swing_v2_registers_the_calibrated_decision_test_with_the_module_constants():
    """登记的旋钮与判读模块的常量逐项相等;样本门就是尺寸校准测试用的判读样本量。"""
    from autoresearch.research import swing_ruler_decision as dec
    from autoresearch.research.registration import parse_maturity_policy
    from tests.research.test_swing_ruler_decision import DECISION_N

    spec = json.loads(SWING_V2.read_text(encoding="utf-8"))
    rule = spec["selection_rule"]
    assert rule["decision_test"] == dec.DECISION_TEST
    assert rule["hac_lag"] == dec.HAC_LAG == 9
    assert rule["null_reps"] == dec.NULL_REPS and rule["null_seed"] == dec.NULL_SEED
    assert "decision_block" not in rule and rule["block_lengths"] == [1, 5, 10]
    assert rule["fdr_alpha"] == 0.05 and rule["ci_level"] == 0.95
    assert parse_maturity_policy(spec["maturity_policy"]) == DECISION_N == 40
    for h in spec["hypotheses"][:3]:
        assert "CI" in h["rejection_condition"] and "不等于" in h["rejection_condition"]
        assert "MA(9)" in h["rejection_condition"] and "40" in h["rejection_condition"]
    assert "hac_bartlett_overlap_null" in spec["multiplicity"]
    # 为什么选它、尺寸与功效都写进登记文本(不是只在提交说明里)
    assert "10–12%" in spec["purge_rule"] and "4.8%" in spec["purge_rule"]
    assert "16%" in spec["purge_rule"]


def test_swing_v2_provenance_roots_leave_out_the_ledger_writer():
    """复审 M5:普查读的是账本**数据**;`scan/outcome.py` 不重算已写的 fwd_10_oc,钉它什么也
    保护不了,反而让任何无关的 outcome 修补逼出新 experiment_id。输入 sha256 进 manifest。"""
    spec = json.loads(SWING_V2.read_text(encoding="utf-8"))
    assert "swing_ruler_decision" in spec["quality_constraints"]
    assert "不含 scan/outcome.py" in spec["quality_constraints"]
