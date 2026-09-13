import json

import pandas as pd
import pytest

from autoresearch.research import funnel_variants as fv


def _frames():
    full = pd.DataFrame({
        "code": ["000006", "000002", "000001", "000003", "000004", "000005", "000007", "000008"],
        "composite": [4.0, 9.0, 10.0, 8.0, 7.0, 6.0, 3.0, 2.0],
        "industry": ["A"] * 8,
    })
    current_l1 = full[full["code"].isin(["000001", "000002", "000003", "000004", "000008"])].copy()
    provenance = {
        "000001": ("momentum", 1), "000002": ("value", 1),
        "000003": ("growth", 1), "000004": ("momentum", 1),
        "000008": ("growth|value|momentum", 3),
    }
    current_l1["recall_channels"] = current_l1["code"].map(lambda c: provenance[c][0])
    current_l1["n_channels"] = current_l1["code"].map(lambda c: provenance[c][1])
    current_l1["best_rank"] = range(1, len(current_l1) + 1)
    current_l2 = current_l1[current_l1["code"].isin(["000001", "000002", "000008"])].copy()
    current_l2["selection_reason"] = ["merit", "backfill", "lane"]
    current_l2["selection_detail"] = ["", "", "成长"]
    current_pass1 = current_l2[current_l2["code"].isin(["000001", "000008"])].copy()
    current_pass1["selection_reason"] = ["lane", "conviction_guard"]
    current_pass1["selection_detail"] = ["momentum", "n_channels=3"]
    return full, current_l1, current_l2, current_pass1


def test_build_day_is_same_budget_and_preserves_current_facts():
    full, current_l1, current_l2, current_pass1 = _frames()
    result = fv.build_day(
        "2026-09-01", full=full, current_l1=current_l1,
        current_l2=current_l2, current_pass1=current_pass1,
        recall_n=5, l2_n=3, pass1_n=2, floors={}, sector_cap_frac=1.0,
        hybrid_core_fraction=0.8,
    )

    counts = result.groupby("variant")[["in_l1", "in_l2", "pass1_kept"]].sum().astype(int)
    assert (counts == [5, 3, 2]).all().all()
    current = result[result["variant"] == fv.CURRENT].set_index("code")
    assert current.index[current["in_l2"]].tolist() == ["000001", "000002", "000008"]
    assert current.loc["000008", "l2_selection_reason"] == "lane"
    assert current.loc["000008", "pass1_selection_reason"] == "conviction_guard"


def test_hybrid_has_fixed_core_and_only_twenty_percent_diversifier():
    full, current_l1, current_l2, current_pass1 = _frames()
    result = fv.build_day(
        "2026-09-01", full=full, current_l1=current_l1,
        current_l2=current_l2, current_pass1=current_pass1,
        recall_n=5, l2_n=3, pass1_n=2, floors={}, sector_cap_frac=1.0,
        hybrid_core_fraction=0.8,
    )
    hybrid = result[(result["variant"] == fv.HYBRID) & result["in_l1"]].set_index("code")
    assert {"000001", "000002", "000003", "000004"} <= set(hybrid.index)
    assert set(hybrid.index) - {"000001", "000002", "000003", "000004"} == {"000008"}
    assert (hybrid["l1_selection_reason"] == "diversifier").sum() == 1


def test_composite_only_has_no_fabricated_channel_lane_and_ties_break_by_code():
    full, current_l1, current_l2, current_pass1 = _frames()
    full.loc[full["code"].isin(["000001", "000002"]), "composite"] = 10.0
    result = fv.build_day(
        "2026-09-01", full=full, current_l1=current_l1,
        current_l2=current_l2, current_pass1=current_pass1,
        recall_n=2, l2_n=2, pass1_n=1, floors={"趋势": 2}, sector_cap_frac=1.0,
    )
    comp = result[(result["variant"] == fv.COMPOSITE_ONLY) & result["in_l1"]]
    assert comp["code"].tolist() == ["000001", "000002"]
    assert set(comp["l1_selection_reason"]) == {"composite"}
    assert not comp["l2_selection_reason"].eq("lane").any()
    kept = comp[comp["pass1_kept"]]
    assert kept["code"].tolist() == ["000001"]


def test_metrics_keep_raw_and_excess_separate_and_do_not_zero_missing_returns():
    membership = pd.DataFrame([
        {"date": "2026-09-01", "code": "000001", "variant": fv.CURRENT,
         "in_l1": True, "in_l2": True, "pass1_kept": True},
        {"date": "2026-09-01", "code": "000002", "variant": fv.CURRENT,
         "in_l1": True, "in_l2": False, "pass1_kept": False},
        {"date": "2026-09-01", "code": "000001", "variant": fv.COMPOSITE_ONLY,
         "in_l1": True, "in_l2": False, "pass1_kept": False},
        {"date": "2026-09-01", "code": "000002", "variant": fv.COMPOSITE_ONLY,
         "in_l1": True, "in_l2": True, "pass1_kept": True},
    ])
    outcomes = pd.DataFrame({
        "date": ["2026-09-01"] * 3,
        "code": ["000001", "000002", "000003"],
        "gap_c1_o2": [.02, None, -.02],
        "status_gap_c1_o2": ["MATURE", "PENDING", "MATURE"],
        "buyable_c1": pd.array([True, True, True], dtype="boolean"),
        "unsellable_o2": pd.array([False, False, True], dtype="boolean"),
    })

    metrics = fv.daily_metrics(membership, outcomes, stages=("pass1",))
    current = metrics[metrics["variant"] == fv.CURRENT].iloc[0]
    challenger = metrics[metrics["variant"] == fv.COMPOSITE_ONLY].iloc[0]
    assert current["status"] == "COMPLETE"
    assert current["raw_mean"] == pytest.approx(.02)
    assert current["market_median"] == pytest.approx(0.0)
    assert current["excess_vs_market_median"] == pytest.approx(.02)
    assert challenger["status"] == "INCOMPLETE_OUTCOMES"
    assert pd.isna(challenger["raw_mean"])


def test_paired_summary_uses_only_common_complete_days_and_marks_small_sample_immature():
    daily = pd.DataFrame([
        {"date": "2026-09-01", "variant": fv.CURRENT, "stage": "pass1",
         "status": "COMPLETE", "excess_vs_market_median": .01},
        {"date": "2026-09-01", "variant": fv.COMPOSITE_ONLY, "stage": "pass1",
         "status": "COMPLETE", "excess_vs_market_median": .03},
        {"date": "2026-09-02", "variant": fv.CURRENT, "stage": "pass1",
         "status": "COMPLETE", "excess_vs_market_median": -.01},
        {"date": "2026-09-02", "variant": fv.COMPOSITE_ONLY, "stage": "pass1",
         "status": "INCOMPLETE_OUTCOMES", "excess_vs_market_median": None},
    ])

    summary = fv.paired_summary(daily, baseline=fv.CURRENT, challenger=fv.COMPOSITE_ONLY,
                                stage="pass1", min_days=20, n_boot=99, seed=7)
    assert summary["n_common_complete_days"] == 1
    assert summary["point"] == pytest.approx(.02)
    assert summary["lo"] is None and summary["hi"] is None
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"


def test_run_writes_auditable_exclusive_bundle(tmp_path):
    full, current_l1, current_l2, current_pass1 = _frames()
    scan = tmp_path / "2026-09-01"
    scan.mkdir()
    for name, frame in {
        "L1_scored_full.csv": full,
        "L1_recall_top1000.csv": current_l1,
        "L2_gbdt_top200.csv": current_l2,
        "_l3_pass1_kept.csv": current_pass1,
    }.items():
        frame.to_csv(scan / name, index=False)
    outcomes = tmp_path / "outcomes.csv"
    pd.DataFrame({
        "date": ["2026-09-01"] * len(full), "code": full["code"],
        "gap_c1_o2": [.01] * len(full), "status_gap_c1_o2": ["MATURE"] * len(full),
        "buyable_c1": [True] * len(full), "unsellable_o2": [False] * len(full),
    }).to_csv(outcomes, index=False)
    out = tmp_path / "bundle"

    assert fv.run(scan_dirs=[scan], outcomes_path=outcomes, out_dir=out) == out
    assert {p.name for p in out.iterdir()} == {
        "membership.csv", "daily_metrics.csv", "paired_summary.json", "manifest.json",
    }
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["engine"] == "codex"
    assert manifest["ruler"] == "gap_c1_o2"
    assert len(manifest["inputs"]) == 5
    with pytest.raises(FileExistsError):
        fv.run(scan_dirs=[scan], outcomes_path=outcomes, out_dir=out)
