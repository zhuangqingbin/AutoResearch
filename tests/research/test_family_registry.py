"""家族登记必须过 F1 的方案契约 —— 登记不是散文,是机器可校验的冻结方案。

F6 Step 1 建表时只有 W3 三格;2026-09-13 漏斗形状家族(funnel-shape)按同一口径入册。
"""
import json
from pathlib import Path

from autoresearch.contracts import research_experiment as rx

SPEC = Path(__file__).resolve().parents[2] / "docs" / "research" / "2026-09-07-w3-three-grids-family.spec.json"
SPEC_V2 = SPEC.with_name("2026-09-07-w3-three-grids-family-v2.spec.json")
FUNNEL = SPEC.with_name("2026-09-13-funnel-shape-family.spec.json")


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
