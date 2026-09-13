import json

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.common.run_identity import resolve_git_sha
from autoresearch.research import funnel_variants as fv
from autoresearch.research.registration import (
    file_manifest,
    manifest_digest,
    registered_date_slice,
)


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


# ───────────────── 预注册(F1)端到端:方案冻结之后才谈得上读数 ─────────────────


def _spec(**updates):
    value = {
        "schema_version": 1, "experiment_id": "FV_T1", "engine": ws.ENGINE,
        "created_at": "2026-09-13T12:00:00+08:00", "code_sha": resolve_git_sha(),
        "prompt_hashes": {}, "input_manifest_hash": "b" * 64,
        "experiment_family": "funnel-shape",
        "hypotheses": [{
            "hypothesis_id": "funnel_composite_only",
            "mechanism": "通道成员差异未必是独立信号", "expected_direction": "positive",
            "available_at": "T 日盘后", "metric_definition": "日等权配对差",
            "population": "冻结日全帧", "label": "gap_c1_o2",
            "rejection_condition": "CI 下界 ≤ 0 即无切换证据",
        }],
        "population_rule": "冻结产物", "ruler": "gap_c1_o2",
        "selection_rule": {"hybrid_core_fraction": 0.8, "sector_cap_frac": 1.0, "floors": {}},
        "sensitivity_rulers": [], "return_unit": "fraction", "baseline": "current",
        "evidence_mode": "EOD_PROXY", "weighting": "day_equal", "cost_model_version": "none",
        "split": {"train": ["2026-06-01", "2026-08-01"],
                  "validation": ["2026-08-01", "2026-09-01"],
                  "test": ["2026-09-01", "2026-09-30"]},
        "purge_rule": "隔夜标签无重叠", "embargo_sessions": 1,
        "bootstrap": {"n_boot": 99, "seed": 7}, "multiplicity": "BY",
        "maturity_policy": "scan_days >= 20", "quality_constraints": "只读冻结产物",
        "stop_rule": "样本终点 2026-09-30",
    }
    value.update(updates)
    return value


def _frozen_day(tmp_path, date="2026-09-01"):
    """落一天的四份冻结产物 + 收益表,返回 (scan_dir, outcomes_path)。"""
    full, current_l1, current_l2, current_pass1 = _frames()
    scan = tmp_path / date
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
        "date": [date] * len(full), "code": full["code"],
        "gap_c1_o2": [.01] * len(full), "status_gap_c1_o2": ["MATURE"] * len(full),
        "buyable_c1": [True] * len(full), "unsellable_o2": [False] * len(full),
    }).to_csv(outcomes, index=False)
    return scan, outcomes


def _bound(tmp_path, scan, outcomes, **updates):
    """把 spec 的 input_manifest_hash 绑到真实输入上(不绑就等于没声明输入身份)。"""
    value = _spec(**updates)
    paths = [scan / name for name in fv._DAY_INPUTS.values()] + [outcomes]
    value["input_manifest_hash"] = manifest_digest(
        file_manifest(paths, registered_date_slice(value)))
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return spec_path


@pytest.fixture(autouse=True)
def _accept_uncommitted_test_tree(monkeypatch):
    """测试树是脏的,`verify_code_provenance` 必然拒 —— 这里只替掉它,其余校验照跑。

    它自身的鉴别力由 `tests/research/test_registration.py` 负责,不在本文件重复。
    """
    monkeypatch.setattr(
        fv, "verify_code_provenance",
        lambda spec, roots: {"declared": spec["code_sha"], "observed": resolve_git_sha(),
                             "behavior_roots": sorted(roots)})


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_run_writes_auditable_exclusive_bundle(tmp_path, monkeypatch, engine):
    """bundle 必须记**当前**引擎,而不是开发这条特性那天恰好用的那个。

    原断言写死 `== "codex"`,于是它在 codex 会话里恒绿、在 claude 会话里恒红 —— 双引擎全量
    才逮得到(同款前科:2026-08-28 法证 capsule 只在 codex 下开发,合并后 claude 侧 11 条夹具红)。
    改成逐引擎参数化而不是 `== ws.ENGINE`:后者两边读同一个常量,生产侧写死字面量它也绿。
    """
    monkeypatch.setattr(ws, "ENGINE", engine)
    scan, outcomes = _frozen_day(tmp_path)
    spec_path = _bound(tmp_path, scan, outcomes, engine=engine)
    parent = tmp_path / "out"

    out = fv.run(spec_path=spec_path, scan_dirs=[scan], outcomes_path=outcomes, parent=parent)
    assert out == parent / "FV_T1"
    assert {p.name for p in out.iterdir()} == {
        "spec.json", "membership.csv", "daily_metrics.csv", "paired_summary.json", "manifest.json",
    }
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["engine"] == engine
    assert manifest["ruler"] == "gap_c1_o2"
    assert manifest["experiment_id"] == "FV_T1"
    assert manifest["selection_rule"]["hybrid_core_fraction"] == 0.8
    assert manifest["min_common_days"] == 20 and manifest["alpha"] == 0.10
    assert manifest["bootstrap"] == {"n_boot": 99, "seed": 7}
    assert len(manifest["inputs"]) == 5
    # 方案随读数一起冻在结果目录里 —— 事后改不动,也不用另去找那份 spec
    assert json.loads((out / "spec.json").read_text(encoding="utf-8"))["experiment_id"] == "FV_T1"
    with pytest.raises(FileExistsError):
        fv.run(spec_path=spec_path, scan_dirs=[scan], outcomes_path=outcomes, parent=parent)


def test_wrong_input_manifest_is_refused_before_anything_is_written(tmp_path):
    """声明的输入身份对不上真实文件 → 拒跑,且**一个字节都不落盘**。

    半个实验目录是「试过、没跑完」的记录;被拒的跑法如果也留下目录,两者就分不清了。
    """
    scan, outcomes = _frozen_day(tmp_path)
    spec_path = _bound(tmp_path, scan, outcomes)
    value = json.loads(spec_path.read_text(encoding="utf-8"))
    value["input_manifest_hash"] = "0" * 64
    spec_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="input manifest"):
        fv.run(spec_path=spec_path, scan_dirs=[scan], outcomes_path=outcomes,
               parent=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_days_outside_the_registered_test_window_are_refused(tmp_path):
    """区间外的日子混进来,等于事后把样本挪到好看的地方。"""
    scan, outcomes = _frozen_day(tmp_path, date="2026-08-15")     # 落在 validation 段
    spec_path = _bound(tmp_path, scan, outcomes)

    with pytest.raises(ValueError, match="registered test interval"):
        fv.run(spec_path=spec_path, scan_dirs=[scan], outcomes_path=outcomes,
               parent=tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(("rule", "match"), [
    ("flags", "selection_rule must register"),                       # 字符串 = 旋钮没冻
    ({"hybrid_core_fraction": 0.8, "sector_cap_frac": 1.0, "tweak": 1}, "unregistered selection knobs"),
    ({"hybrid_core_fraction": 1.5, "sector_cap_frac": 1.0}, "hybrid_core_fraction"),
    ({"hybrid_core_fraction": 0.8, "sector_cap_frac": 0}, "sector_cap_frac"),
    ({"hybrid_core_fraction": 0.8, "sector_cap_frac": 1.0, "floors": {"趋势": -1}}, "floors"),
])
def test_funnel_knobs_must_be_frozen_in_the_spec(rule, match):
    """80/20、行业帽、style floor 留在命令行上,预注册就只是句口号。"""
    with pytest.raises(ValueError, match=match):
        fv.registered_selection(_spec(selection_rule=rule))


def test_registered_engine_mismatch_is_refused(tmp_path, monkeypatch):
    """方案声明的引擎与运行时不符 → 拒跑,不静默按当前引擎改写。"""
    monkeypatch.setattr(ws, "ENGINE", "claude")
    scan, outcomes = _frozen_day(tmp_path)
    spec_path = _bound(tmp_path, scan, outcomes, engine="codex")

    with pytest.raises(ValueError, match="registered engine mismatch"):
        fv.run(spec_path=spec_path, scan_dirs=[scan], outcomes_path=outcomes,
               parent=tmp_path / "out")
    assert not (tmp_path / "out").exists()
