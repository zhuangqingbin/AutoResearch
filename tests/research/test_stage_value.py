"""F4/F8:同日同人口、缺结果不填零、零 BUY 不是失败、UNKNOWN 旗不当 False、主尺敏感尺并列。"""
import json

import pandas as pd
import pytest

from autoresearch.research import stage_value as sv


def test_selection_delta_uses_the_same_days_candidate_population():
    frame = pd.DataFrame([
        {"date": "20260901", "code": "600000", "baseline": True, "refined": True, "value": .02},
        {"date": "20260901", "code": "600001", "baseline": True, "refined": False, "value": -.02},
    ])
    result = sv.paired_daily_selection(frame)
    assert result.iloc[0]["delta"] == pytest.approx(.02)
    assert result.iloc[0]["status"] == "COMPLETE"


def test_missing_rejected_outcome_does_not_become_zero():
    frame = pd.DataFrame([
        {"date": "20260901", "code": "600000", "baseline": True, "refined": True, "value": .02},
        {"date": "20260901", "code": "600001", "baseline": True, "refined": False, "value": None},
    ])
    result = sv.paired_daily_selection(frame)
    assert result.iloc[0]["status"] == "INCOMPLETE_OUTCOMES"
    assert pd.isna(result.iloc[0]["delta"])


def test_zero_buy_day_is_empty_selection_not_a_loss():
    frame = pd.DataFrame([
        {"date": "20260901", "code": "600000", "baseline": True, "refined": False, "value": .02},
    ])
    result = sv.paired_daily_selection(frame)
    assert result.iloc[0]["status"] == "EMPTY_SELECTION" and pd.isna(result.iloc[0]["delta"])


def test_days_do_not_bleed_into_each_other():
    frame = pd.DataFrame([
        {"date": "20260901", "code": "600000", "baseline": True, "refined": True, "value": .05},
        {"date": "20260902", "code": "600000", "baseline": True, "refined": False, "value": -.05},
        {"date": "20260902", "code": "600001", "baseline": True, "refined": True, "value": .01},
    ])
    result = sv.paired_daily_selection(frame).set_index("date")
    assert result.loc["20260901", "delta"] == pytest.approx(0.0)
    assert result.loc["20260902", "delta"] == pytest.approx(.01 - (-.05 + .01) / 2)


@pytest.mark.parametrize("bad", [
    pd.DataFrame([{"date": "20260901", "code": "600000", "baseline": True, "refined": True, "value": .02},
                  {"date": "20260901", "code": "600000", "baseline": True, "refined": True, "value": .02}]),
    pd.DataFrame([{"date": "20260901", "code": "600000", "baseline": None, "refined": True, "value": .02}]),
    pd.DataFrame([{"date": "20260901", "code": "600000", "baseline": 1, "refined": True, "value": .02}]),
    pd.DataFrame([{"date": "20260901", "code": "600000", "baseline": True, "refined": True, "value": float("inf")}]),
])
def test_bad_inputs_raise(bad):
    with pytest.raises(ValueError):
        sv.paired_daily_selection(bad)


# ───────────────────────── populations 适配 ─────────────────────────

def population(**overrides):
    base = pd.DataFrame({
        "analysis_date": ["2026-09-01"] * 4, "code": ["600000", "600001", "600002", "600003"],
        "in_l2": pd.array([True, True, True, False], dtype="boolean"),
        "is_finalist": pd.array([True, False, None, False], dtype="boolean"),
        "l4_rejected": pd.array([False, False, False, False], dtype="boolean"),
        "e6_candidate": pd.array([True, False, False, False], dtype="boolean"),
        "is_buy": pd.array([False, False, False, False], dtype="boolean"),
        "gap_c1_o2": [.02, -.01, .03, .00], "status_gap_c1_o2": ["MATURE"] * 4,
        "fwd_5_oc": [.05, .01, None, .0], "status_fwd_5_oc": ["MATURE", "MATURE", "PENDING", "MATURE"],
    })
    for k, v in overrides.items():
        base[k] = v
    return base


def test_unknown_flags_go_to_coverage_not_false():
    frame, cov = sv.pairs_from_population(population(), stage="menu_to_l3", ruler="gap_c1_o2")
    assert cov["unknown_flags"] == 1 and "600002" not in set(frame["code"])
    assert len(frame) == 3


def test_immature_ruler_makes_the_day_incomplete_not_zero():
    frame, _ = sv.pairs_from_population(population(is_finalist=pd.array([True, True, True, False], dtype="boolean")),
                                        stage="menu_to_l3", ruler="fwd_5_oc")
    daily = sv.paired_daily_selection(frame)
    assert daily.iloc[0]["status"] == "INCOMPLETE_OUTCOMES"


def test_l3_to_l4_refined_excludes_rejected():
    frame, _ = sv.pairs_from_population(
        population(is_finalist=pd.array([True, True, False, False], dtype="boolean"),
                   l4_rejected=pd.array([False, True, False, False], dtype="boolean")),
        stage="l3_to_l4", ruler="gap_c1_o2")
    assert frame.set_index("code").loc["600001", "refined"] == False  # noqa: E712
    assert frame.set_index("code").loc["600000", "refined"] == True   # noqa: E712


def test_missing_flag_column_is_refused():
    with pytest.raises(ValueError):
        sv.pairs_from_population(population().drop(columns=["is_buy"]), stage="l4_to_e6", ruler="gap_c1_o2")


# ───────────────────────── CLI 端到端 ─────────────────────────

def spec():
    return {
        "schema_version": 1, "experiment_id": "SV_T1", "engine": "claude",
        "created_at": "2026-09-07T12:00:00+08:00", "code_sha": "a" * 40, "prompt_hashes": {},
        "input_manifest_hash": "b" * 64, "experiment_family": "stage-value",
        "hypotheses": [{"hypothesis_id": "h1", "mechanism": "L3 精排选出的票隔夜不优于菜单",
                        "expected_direction": "two_sided", "available_at": "2026-09-01T14:45:00+08:00",
                        "metric_definition": "refined − baseline 日等权", "population": "L2-200",
                        "label": "gap_c1_o2", "rejection_condition": "CI 不含 0"}],
        "population_rule": "populations parquet", "selection_rule": "flags", "ruler": "gap_c1_o2",
        "sensitivity_rulers": ["fwd_5_oc"], "return_unit": "fraction", "baseline": "in_l2 等权",
        "evidence_mode": "EOD_PROXY", "weighting": "day_equal", "cost_model_version": "none",
        "split": {"train": ["2022-03-01", "2025-01-01"], "validation": ["2025-01-01", "2026-01-01"],
                  "test": ["2026-01-01", "2026-09-01"]},
        "purge_rule": "label overlap", "embargo_sessions": 2, "bootstrap": {"n_boot": 200, "seed": 7},
        "multiplicity": "BY", "maturity_policy": "scan_days >= 20", "quality_constraints": "GATE 通过",
        "stop_rule": "样本终点 2026-12-31",
    }


def test_cli_writes_every_registered_output_and_keeps_rulers_apart(tmp_path):
    pop = tmp_path / "pop.csv"
    population(is_finalist=pd.array([True, True, False, False], dtype="boolean")).to_csv(pop, index=False)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec()), encoding="utf-8")
    out = sv.run(spec_path=spec_path, populations=[pop], parent=tmp_path / "out")
    for name in ("spec.json", "input_manifest.json", "daily_delta.csv", "coverage.json",
                 "statistics.json", "readout.md", "manifest.json"):
        assert (out / name).exists(), name
    stats = json.loads((out / "statistics.json").read_text(encoding="utf-8"))
    assert set(stats) == {f"{s}|{r}" for s in sv.STAGE_PAIRS for r in ("gap_c1_o2", "fwd_5_oc")}
    daily = pd.read_csv(out / "daily_delta.csv")
    assert set(daily["ruler"]) == {"gap_c1_o2", "fwd_5_oc"}
    readout = (out / "readout.md").read_text(encoding="utf-8")
    assert "敏感尺" in readout and "不给决策背书" in readout and "明确不支持的结论" in readout
    with pytest.raises(FileExistsError):
        sv.run(spec_path=spec_path, populations=[pop], parent=tmp_path / "out")


def test_small_samples_are_immature_not_conclusions(tmp_path):
    pop = tmp_path / "pop.csv"
    population(is_finalist=pd.array([True, True, False, False], dtype="boolean")).to_csv(pop, index=False)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec()), encoding="utf-8")
    out = sv.run(spec_path=spec_path, populations=[pop], parent=tmp_path / "out")
    stats = json.loads((out / "statistics.json").read_text(encoding="utf-8"))
    main = stats["menu_to_l3|gap_c1_o2"]
    assert main["n_complete"] == 1
    assert "IMMATURE" in json.dumps(main["maturity"])


def test_spec_with_unregistered_ruler_is_refused_before_any_output(tmp_path):
    pop = tmp_path / "pop.csv"
    population().to_csv(pop, index=False)
    bad = spec()
    bad["sensitivity_rulers"] = ["fwd_2_oc"]
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        sv.run(spec_path=spec_path, populations=[pop], parent=tmp_path / "out")
    assert not (tmp_path / "out").exists()
