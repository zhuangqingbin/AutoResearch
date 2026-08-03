"""统一实验模板单测 —— 锁 §5-2 十项必填 与 §5-1「IMMATURE/UNKNOWN/FAIL 不批」。"""
from __future__ import annotations

import json

import pytest

from autoresearch.common import stats as st
from autoresearch.learning import experiment_template as et


def _t(**over) -> et.ExperimentTemplate:
    base = {
        "experiment_id": "exp_test", "h0": "challenger 与基线无差异", "h1": "challenger 更优",
        "data_cutoff": "2026-08-01", "paired_unit": "scan_day", "clustering": "date_cluster",
        "min_units": 20, "target_power": 0.8, "primary_metric": "excess_2_delta",
        "direction": et.HIGHER_IS_BETTER, "equivalence_margin": 0.02, "no_harm": False,
        "multiple_testing": "single_hypothesis", "n_hypotheses": 1,
        "stopping_rule": "固定 20 个成熟扫描日,不可提前停",
        "rollback": "registry rollback 到 stable pointer",
    }
    base.update(over)
    return et.ExperimentTemplate(**base)


def _iv(lo, hi, *, clusters=25, point=None) -> st.Interval:
    return st.Interval(point if point is not None else (lo + hi) / 2,
                       lo, hi, clusters * 3, clusters, "test")


# ── §5-2 十项必填 ────────────────────────────────────────────────────


def test_clean_template_validates():
    assert et.validate(_t()) == []


@pytest.mark.parametrize("field_name", ["h0", "h1", "data_cutoff", "primary_metric",
                                        "stopping_rule", "rollback"])
def test_empty_required_field_is_rejected(field_name):
    assert any(field_name in p for p in et.validate(_t(**{field_name: " "})))


def test_min_units_must_be_preregistered_positive():
    assert any("可选停止是作弊" in p for p in et.validate(_t(min_units=0)))


def test_zero_margin_cannot_prove_equivalence():
    assert any("margin=0" in p for p in et.validate(_t(equivalence_margin=0.0)))


def test_multi_hypothesis_must_declare_correction():
    bad = et.validate(_t(n_hypotheses=12, multiple_testing="single_hypothesis"))
    assert any("修正方式" in p for p in bad)


def test_multi_hypothesis_with_fdr_is_fine():
    assert et.validate(_t(n_hypotheses=12, multiple_testing="bh_fdr")) == []


def test_non_scanday_pairing_needs_clustering():
    bad = et.validate(_t(paired_unit="candidate_day", clustering="none"))
    assert any("窄到假" in p for p in bad)


def test_unknown_enum_values_rejected():
    assert et.validate(_t(direction="whatever"))
    assert et.validate(_t(paired_unit="vibes"))
    assert et.validate(_t(clustering="magic"))


def test_require_valid_raises():
    with pytest.raises(et.TemplateError):
        et.require_valid(_t(h0=""))


# ── §5-1:0 BUY 不是放松门的理由 ────────────────────────────────────


@pytest.mark.parametrize("motivation", ["最近连着 0 买,门是不是太严",
                                        "zero buy streak", "已经零买 11 天了"])
def test_zero_buy_motivation_is_blocked(motivation):
    assert any("0 BUY" in p for p in et.validate(_t(motivation=motivation)))
    with pytest.raises(et.TemplateError):
        et.assert_not_zero_buy_justification(motivation)


def test_legitimate_motivation_passes():
    assert et.assert_not_zero_buy_justification("该门单门错杀率 33.3%,n=6 需扩样本") == []


# ── 五态裁决:这是模块存在的理由 ──────────────────────────────────


def test_immature_outranks_everything():
    """样本不够时连「未知」都不该说。"""
    v = et.conclude(_t(min_units=20), _iv(0.10, 0.20, clusters=4))
    assert v["verdict"] == "IMMATURE" and v["recommendation"] == "DO_NOT_PROMOTE"


def test_wide_interval_is_unknown_never_equivalent():
    v = et.conclude(_t(), _iv(-0.30, 0.30))
    assert v["verdict"] == "UNKNOWN"
    assert v["recommendation"] == "DO_NOT_PROMOTE"
    assert "只有 EQUIVALENT" in v["readback_guard"]


def test_tight_interval_inside_band_is_equivalent_but_not_promoted():
    v = et.conclude(_t(), _iv(-0.005, 0.008))
    assert v["verdict"] == "EQUIVALENT"
    assert v["recommendation"] == "DO_NOT_PROMOTE"     # 没变好就没有换基线的理由


def test_interval_beyond_margin_passes():
    v = et.conclude(_t(), _iv(0.05, 0.12))
    assert v["verdict"] == "PASS" and v["recommendation"] == "PROMOTE"


def test_interval_beyond_negative_margin_fails():
    v = et.conclude(_t(), _iv(-0.14, -0.06))
    assert v["verdict"] == "FAIL" and v["recommendation"] == "DO_NOT_PROMOTE"


def test_lower_is_better_flips_the_sign():
    """false_kill_rate 这类指标:下降才是改善。两套符号逻辑最容易写反,这里钉死。"""
    lower = _t(direction=et.LOWER_IS_BETTER, primary_metric="false_kill_rate_delta")
    assert et.conclude(lower, _iv(-0.14, -0.06))["verdict"] == "PASS"
    assert et.conclude(lower, _iv(0.06, 0.14))["verdict"] == "FAIL"
    higher = _t()
    assert et.conclude(higher, _iv(-0.14, -0.06))["verdict"] == "FAIL"
    assert et.conclude(higher, _iv(0.06, 0.14))["verdict"] == "PASS"


def test_no_interval_is_unknown():
    v = et.conclude(_t(), st.Interval(0.1, None, None, 90, 30, "single_day"))
    assert v["verdict"] == "UNKNOWN" and v["reason"] == "NO_INTERVAL"


def test_no_harm_one_sided_accepts_upside():
    """no-harm 只问「有没有变差」—— 上界很高不该判失败。"""
    t = _t(no_harm=True, equivalence_margin=0.02)
    v = et.conclude(t, _iv(-0.01, 0.50))
    assert v["verdict"] == "EQUIVALENT"


def test_conclude_rejects_invalid_template():
    with pytest.raises(et.TemplateError):
        et.conclude(_t(min_units=0), _iv(0.0, 0.1))


def test_observed_units_override():
    v = et.conclude(_t(min_units=20), _iv(0.05, 0.12, clusters=3), observed_units=30)
    assert v["verdict"] == "PASS"


def test_declared_dim_floor_without_observation_counts_as_zero():
    """声明了「要 30 个 unique」却一个都没报 → 不达标,不能当「不适用」放行。"""
    t = _t(min_units=20, maturity_minimums={"unique_n": 30, "regimes": 2})
    thin = et.conclude(t, _iv(0.05, 0.12, clusters=25))
    assert thin["verdict"] == "IMMATURE"
    assert any("unique_n" in m for m in thin["maturity"]["missing"])


def test_observed_dims_satisfy_declared_floors():
    t = _t(min_units=20, maturity_minimums={"unique_n": 30, "regimes": 2})
    v = et.conclude(t, _iv(0.05, 0.12, clusters=25),
                    observed_dims={"unique_n": 41, "regimes": 3})
    assert v["verdict"] == "PASS"


def test_observed_dims_below_floor_are_immature():
    t = _t(min_units=20, maturity_minimums={"unique_n": 30})
    v = et.conclude(t, _iv(0.05, 0.12, clusters=25), observed_dims={"unique_n": 12})
    assert v["verdict"] == "IMMATURE"


def test_minimums_block_reflects_declared_floors():
    t = _t(maturity_minimums={"unique_n": 30, "regimes": 3, "subgroup_n": 14})
    m = et.minimums_for(t)
    assert m["unique_events"] == 30 and m["regimes"] == 3 and m["mature_events"] == 14


# ── registry 咬合 ───────────────────────────────────────────────────


def test_definition_hash_changes_when_margin_moves():
    """中途把 margin 放宽到刚好通过 → definition_hash 变 → registry 拒绝沿用旧 id。"""
    from autoresearch.learning.experiment_registry import canonical_hash

    a = canonical_hash(et.to_registry_definition(_t(equivalence_margin=0.02)))
    b = canonical_hash(et.to_registry_definition(_t(equivalence_margin=0.20)))
    assert a != b


def test_definition_is_stable_for_same_template():
    from autoresearch.learning.experiment_registry import canonical_hash

    assert canonical_hash(et.to_registry_definition(_t())) == \
        canonical_hash(et.to_registry_definition(_t()))


def test_minimums_block_is_registry_shaped():
    from autoresearch.learning.experiment_registry import _validate_minimums

    assert _validate_minimums(et.minimums_for(_t()))


def test_template_flows_into_a_registerable_spec(tmp_path):
    """端到端:模板 → spec → register_experiment 真的能过 registry 校验。"""
    from autoresearch.learning import experiment_registry as reg

    path = tmp_path / "registry.json"
    reg.set_stable_baseline(path, name="base", pointer="p", approved_by="qa",
                            content_hash="a" * 64)
    t = _t(experiment_id="exp_flow")
    spec = {
        "id": t.experiment_id, "title": "模板端到端", "trial_family": "template_demo",
        "definition": et.to_registry_definition(t),
        "start_date": "2026-08-04", "expires_date": "2026-12-31",
        "primary_metric": t.primary_metric,
        "promotion_guards": {d: [{"metric": t.primary_metric, "op": "gt", "value": 0.02}]
                             for d in reg.GUARD_DOMAINS},
        "rollback_guards": {d: [{"metric": t.primary_metric, "op": "gt", "value": -0.02}]
                            for d in reg.GUARD_DOMAINS},
        "challenger_pointer": {"kind": "shadow", "pointer": "x", "content_hash": "b" * 64},
        "minimums": et.minimums_for(t), "rollback_window_runs": 5,
    }
    record = reg.register_experiment(path, spec)
    assert record["status"] == "PREREGISTERED"
    assert record["definition"]["equivalence_margin"] == 0.02


# ── 渲染 / CLI ──────────────────────────────────────────────────────


def test_render_states_the_readback_guard():
    md = et.render(et.conclude(_t(), _iv(-0.30, 0.30)))
    assert "UNKNOWN" in md and "只有 EQUIVALENT" in md


def test_render_handles_missing_interval():
    md = et.render(et.conclude(_t(), st.Interval(None, None, None, 0, 0, "empty")))
    assert "—" in md


def test_cli_check_reports_violations(tmp_path, capsys):
    p = tmp_path / "t.json"
    payload = _t(h0="").as_dict()
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert et.main(["--template", str(p)]) == 1


def test_cli_emits_definition_for_clean_template(tmp_path, capsys):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(_t().as_dict(), ensure_ascii=False), encoding="utf-8")
    assert et.main(["--template", str(p)]) == 0
    assert "equivalence_margin" in capsys.readouterr().out
