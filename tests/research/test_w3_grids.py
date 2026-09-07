"""F6:W3 三格普查 —— 合成湖上验定义、分桶、判据尺与诚实边界;真读数是另一回事。"""
import json

import pandas as pd
import pytest

from autoresearch.research import w3_grids as w3

DAYS = ["20260901", "20260902", "20260903", "20260904", "20260907"]


def _bar(code, o, h, lo, c, pct, amt=1000.0):
    return {"ts_code": f"{code}.SH" if code.startswith("6") else f"{code}.SZ",
            "open": o, "high": h, "low": lo, "close": c, "pct_chg": pct, "amount": amt}


@pytest.fixture()
def lake(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir(parents=True)
    # 600000:D0 首板 → D1 缩量回调(命中 G1);600001:D0 首板 → D1 放量(不命中)
    rows = {
        "20260901": [_bar("600000", 9.9, 10.0, 9.9, 10.0, 10.0, 5000.0),
                     _bar("600001", 9.9, 10.0, 9.9, 10.0, 10.0, 5000.0),
                     _bar("600002", 20.0, 20.2, 19.8, 20.0, 10.0, 3000.0),
                     _bar("600003", 30.0, 30.5, 29.5, 30.0, 1.0, 4000.0),
                     _bar("600004", 10.0, 10.0, 10.0, 10.0, 10.0, 2000.0)],
        "20260902": [_bar("600000", 9.9, 10.0, 9.5, 9.8, -2.0, 2000.0),   # 缩量(0.4)+回调 −2%
                     _bar("600001", 9.9, 10.0, 9.5, 9.8, -2.0, 9000.0),   # 回调但放量
                     _bar("600002", 20.2, 20.4, 20.0, 20.3, 1.5, 3200.0),
                     _bar("600003", 30.2, 30.6, 30.0, 30.4, 1.3, 4200.0),
                     _bar("600004", 11.0, 11.0, 10.6, 11.0, 10.0, 2100.0)],   # 仍封板 → 买不进
        "20260903": [_bar("600000", 10.2, 10.4, 10.0, 10.3, 5.1, 2500.0),
                     _bar("600001", 9.9, 10.1, 9.8, 10.0, 2.0, 9500.0),
                     _bar("600002", 20.5, 20.7, 20.3, 20.6, 1.5, 3300.0),
                     _bar("600003", 30.5, 30.9, 30.3, 30.7, 1.0, 4300.0),
                     _bar("600004", 11.5, 11.8, 11.3, 11.6, 5.5, 2200.0)],
        "20260904": [_bar("600000", 10.4, 10.6, 10.2, 10.5, 1.9, 2600.0),
                     _bar("600001", 10.1, 10.3, 10.0, 10.2, 2.0, 9600.0),
                     _bar("600002", 20.8, 21.0, 20.6, 20.9, 1.5, 3400.0),
                     _bar("600003", 30.8, 31.2, 30.6, 31.0, 1.0, 4400.0),
                     _bar("600004", 11.7, 11.9, 11.5, 11.8, 1.7, 2300.0)],
        "20260907": [_bar("600000", 10.6, 10.8, 10.4, 10.7, 1.9, 2700.0),
                     _bar("600001", 10.3, 10.5, 10.2, 10.4, 2.0, 9700.0),
                     _bar("600002", 21.0, 21.2, 20.8, 21.1, 1.0, 3500.0),
                     _bar("600003", 31.0, 31.4, 30.8, 31.2, 0.6, 4500.0),
                     _bar("600004", 11.9, 12.1, 11.7, 12.0, 1.7, 2400.0)],
    }
    for day, bars in rows.items():
        pd.DataFrame(bars).to_parquet(daily / f"{day}.parquet")

    lim = tmp_path / "limit_list_d"
    lim.mkdir()
    pd.DataFrame([
        # 600000/600001 首板(limit_times==1);600002 二板;600003 早封(不算晚封)
        {"ts_code": "600000.SH", "limit": "U", "limit_times": 1, "last_time": "145900"},
        {"ts_code": "600001.SH", "limit": "U", "limit_times": 1, "last_time": "093300"},
        {"ts_code": "600002.SH", "limit": "U", "limit_times": 2, "last_time": "143000"},
        {"ts_code": "600003.SH", "limit": "Z", "limit_times": 1, "last_time": "144500"},
        {"ts_code": "600004.SH", "limit": "U", "limit_times": 1, "last_time": "144000"},
    ]).to_parquet(lim / "20260901.parquet")

    # 标准量比(daily_basic.volume_ratio):600000 次日 0.4(缩量),600001 次日 1.8(放量)
    db = tmp_path / "daily_basic"
    db.mkdir()
    for day, vrs in (("20260901", {"600000": 3.0, "600001": 3.0, "600002": 2.0, "600003": 1.0, "600004": 3.0}),
                     ("20260902", {"600000": 0.4, "600001": 1.8, "600002": 1.1, "600003": 1.0, "600004": 0.5}),
                     ("20260903", {"600000": 0.9, "600001": 1.2, "600002": 1.0, "600003": 1.0, "600004": 0.9})):
        pd.DataFrame([{"ts_code": f"{c}.SH", "volume_ratio": v} for c, v in vrs.items()]) \
            .to_parquet(db / f"{day}.parquet")

    inst = tmp_path / "top_inst"
    inst.mkdir()
    pd.DataFrame([
        {"ts_code": "600003.SH", "exalter": "机构专用", "net_buy": 5.0e7},
        {"ts_code": "600003.SH", "exalter": "机构专用", "net_buy": -1.0e7},   # 合计仍为正
        {"ts_code": "600002.SH", "exalter": "机构专用", "net_buy": 1.0e7},
        {"ts_code": "600002.SH", "exalter": "机构专用", "net_buy": -3.0e7},   # 合计为负 → 不入
        {"ts_code": "600001.SH", "exalter": "某某营业部", "net_buy": 9.0e7},  # 非机构席位
    ]).to_parquet(inst / "20260901.parquet")
    return {"root": tmp_path, "daily": daily}


# ───────────────────────── 信号定义 ─────────────────────────

def test_first_board_is_limit_times_one_and_sealed(lake):
    assert w3.first_board_days("20260901", lake_root=lake["root"]) == {"600000", "600001", "600004"}


def test_late_seal_window_is_half_open_on_the_last_seal_time(lake):
    """末次封板 14:59 命中;09:33 早封与 14:30 的二板都不在窗内(二板另有 limit_times)。"""
    assert w3.late_seal_days("20260901", lake_root=lake["root"]) == {"600000", "600002", "600004"}


def test_institution_seats_are_summed_before_the_sign_test(lake):
    """两笔机构席位合计为正才入;合计为负的不入;非机构席位不看。"""
    assert w3.inst_seat_days("20260901", lake_root=lake["root"]) == {"600003"}


def test_missing_table_is_an_empty_set_and_shows_in_coverage(lake):
    """三个信号源缺表时**都**必须给空集 —— 缺表返回任何非空内容,都是把「没查」当成了读数。"""
    for fn in (w3.first_board_days, w3.late_seal_days, w3.inst_seat_days):
        assert fn("20260902", lake_root=lake["root"]) == set(), fn.__name__
    cov = w3.signal_coverage(DAYS, lake_root=lake["root"])
    assert cov["limit_list_d"] == {"days_with_table": 1, "days_requested": 5}
    assert cov["top_inst"]["days_with_table"] == 1


# ───────────────────────── 面板与格 ─────────────────────────

def test_panel_carries_three_rulers_and_the_tradability_flag(lake):
    panel = w3.build_panel(["20260901"], window=DAYS, lake_daily=lake["daily"], lake_root=lake["root"])
    assert set(w3.PANEL_COLS) <= set(panel.columns)
    row = panel.set_index("code").loc["600000"]
    # gap = open(20260903)/close(20260902) − 1 = 10.2/9.8 − 1
    assert row["gap_pp"] == pytest.approx((10.2 / 9.8 - 1) * 100)
    assert row["fwd5_pp"] is not None


def test_g1_requires_both_shrinking_volume_and_a_pullback(lake):
    panel = w3.build_panel(["20260901", "20260902"], window=DAYS, lake_daily=lake["daily"], lake_root=lake["root"])
    cells = w3.build_cells(panel, lake_root=lake["root"], signal_days=["20260901"])
    assert set(cells[w3.G1]["code"]) == {"600000"}, "放量那只必须落选"


def test_g1_without_the_next_day_in_the_panel_finds_nothing_and_that_is_a_gap(lake):
    """面板不带 D+1 时 G1 必然落空 —— 这不是「那天没信号」,是「没查」。
    `run()` 因此多建一天;这条用例把那个必要性钉住。"""
    panel = w3.build_panel(["20260901"], window=DAYS, lake_daily=lake["daily"], lake_root=lake["root"])
    assert w3.build_cells(panel, lake_root=lake["root"])[w3.G1].empty


def test_g1_diagnostics_explain_an_empty_cell(lake):
    """空样本必须可解释:逐级命中数 + 量比分位,让人分得出「没观测」与「阈值不可达」。"""
    panel = w3.build_panel(["20260901", "20260902"], window=DAYS, lake_daily=lake["daily"],
                           lake_root=lake["root"])
    diag = w3.g1_threshold_diagnostics(panel, signal_days=["20260901"], lake_root=lake["root"])
    assert diag["n_first_board"] == 3 and diag["n_pass_both"] == 1
    assert diag["n_pass_shrink"] == 2 and diag["n_pass_pullback"] == 2
    assert diag["registered_threshold"] == 0.6
    assert diag["volume_ratio_quantiles"]["0.5"] is not None


def test_extra_panel_day_does_not_leak_into_the_other_grids(lake):
    """多带的那天只供 G1 查表,不参与 G2/G3 的信号扫描。"""
    panel = w3.build_panel(["20260901", "20260902"], window=DAYS, lake_daily=lake["daily"], lake_root=lake["root"])
    cells = w3.build_cells(panel, lake_root=lake["root"], signal_days=["20260901"])
    for key in (w3.G2, f"{w3.G2}__unbuyable", w3.G3):
        assert set(cells[key]["date"]) <= {"20260901"}


def test_g2_splits_by_buyability_and_the_unbuyable_bucket_is_not_judged(lake):
    """600004 在 D+1 仍封板 → 买不进 → 必须落进不可买桶,且那个桶只报不判。"""
    panel = w3.build_panel(["20260901"], window=DAYS, lake_daily=lake["daily"], lake_root=lake["root"])
    cells = w3.build_cells(panel, lake_root=lake["root"])
    assert set(cells[w3.G2]["code"]) == {"600000", "600002"}
    assert set(cells[f"{w3.G2}__unbuyable"]["code"]) == {"600004"}
    stats = w3.judge_cells(cells, seed=1)
    assert "X_ORACLE" in stats[f"{w3.G2}__unbuyable"]["verdict"]
    assert stats[w3.G2]["verdict"] in ("正证据", "显著负", "未证", "样本不足")


def test_g3_is_judged_on_a_sensitivity_ruler_not_the_main_one(lake):
    panel = w3.build_panel(["20260901"], window=DAYS, lake_daily=lake["daily"], lake_root=lake["root"])
    stats = w3.judge_cells(w3.build_cells(panel, lake_root=lake["root"]), seed=1)
    assert stats[w3.G3]["label_col"] == "fwd5_pp"
    assert stats[w3.G3]["is_sensitivity_label"] is True
    assert "gap_pp" in stats[w3.G3]["observe_only"], "主尺必须并列输出,只观察"
    assert stats[w3.G1]["is_sensitivity_label"] is False


def test_small_sample_can_never_be_positive_evidence(lake):
    """样本门先判:5 天的合成湖不许刷出「正证据」。"""
    panel = w3.build_panel(["20260901"], window=DAYS, lake_daily=lake["daily"], lake_root=lake["root"])
    stats = w3.judge_cells(w3.build_cells(panel, lake_root=lake["root"]), seed=1)
    assert stats[w3.G1]["verdict"] == "样本不足"


def test_family_correction_is_by_and_labels_its_proxy_honestly():
    stats = {g: {"primary": {"ci_low_pp": 0.3, "ci_high_pp": 0.9}} for g in w3.GRID_ORDER}
    rows = w3.family_correction(stats)
    assert [r["method"] for r in rows] == ["BY"] * 3
    assert all("不是检验 p 值" in r["note"] for r in rows)
    assert all(r["interval_excludes_zero"] for r in rows)


# ───────────────────────── 端到端 ─────────────────────────

def test_run_writes_every_registered_output_and_refuses_overwrite(lake, tmp_path):
    spec = json.loads(Path_spec().read_text(encoding="utf-8"))
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    out = w3.run(spec_path=spec_path, since="20260901", until="20260901",
                 lake_daily=lake["daily"], lake_root=lake["root"], parent=tmp_path / "out")
    for name in ("spec.json", "cells.csv", "statistics.json", "signal_coverage.json",
                 "readout.md", "manifest.json"):
        assert (out / name).exists(), name
    readout = (out / "readout.md").read_text(encoding="utf-8")
    assert "诚实边界" in readout and "X_ORACLE" in readout and "敏感尺" in readout
    assert "乐观" in readout, "G1 的量比代理偏差方向必须写在读数里"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ci_lower_pp"] == 0.15 and manifest["ruler"] == "gap_c1_o2"
    with pytest.raises(FileExistsError):
        w3.run(spec_path=spec_path, since="20260901", until="20260901",
               lake_daily=lake["daily"], lake_root=lake["root"], parent=tmp_path / "out")


def Path_spec():
    from pathlib import Path
    return Path(__file__).resolve().parents[2] / "docs" / "research" / "2026-09-07-w3-three-grids-family.spec.json"


def test_empty_window_is_refused_not_an_empty_readout(lake, tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(Path_spec().read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="没有任何交易日"):
        w3.run(spec_path=spec_path, since="20990101", lake_daily=lake["daily"],
               lake_root=lake["root"], parent=tmp_path / "out")
