#!/usr/bin/env python3
"""`overnight_census.panel` —— 湖 → 面板层的锁。零网络、零真实湖:全部走 `tmp_path` 合成湖。

锁的是设计稿 §4.6 里属于面板层的那几条 + 契约里点名的边界:

1. `gap_pp` 与直接调 `factor_lab.forward_returns` **逐值相等**(同一实现,不是同名两把尺);
   并证明它没有被错移到 D+1(错移必红)。
2. `|gap| > ruler.GAP_CLIP` 置 NaN 且计数进 `meta["n_clipped"]`。
3. `rel_gap_pp` 的基准人口 = 当日**可买**全集:不可买票被排除在**分母**外,但它自己仍有 `gap_pp`。
4. `exec_ok` 与 `scan/outcome.py` 常量同源,四个边界点(3.0 / 3.01 / 0.699 / 0.7)与生产
   同为 `<=` 与 `<`。
5. `total_mv_yi` 前向填充 ≤10 交易日:第 11 日必须是 NaN 而不是继续填;`mv_stale_days` 正确。
6. 缺日不伪造:湖里没有的日子不出现在面板里。
7. 缓存往返:build → 读缓存 → `attrs["meta"]` 能从 sidecar 装回。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autoresearch.common import ruler as _ruler, workspace as ws
from autoresearch.dataflows.symbol_utils import to_ts_code
from autoresearch.research import edge_census as ec, factor_lab as fl
from autoresearch.research.overnight_census import panel as P_
from autoresearch.research.overnight_census.core import CAP_FLOOR_YI
from autoresearch.scan import outcome as _outcome

DAYS = ["20260105", "20260106", "20260107", "20260108", "20260109",
        "20260112", "20260113", "20260114"]


# ───────────────────────── 合成湖 ─────────────────────────

def _write_lake(root: Path, bars: dict[str, dict[str, dict]], *,
                basic: dict[str, dict[str, tuple[float, float]]] | None = None,
                names: dict[str, str] | None = None) -> Path:
    """`bars = {日: {code6: {open/high/low/close/pct_chg/amount}}}` → tmp 湖(daily /
    daily_basic / stock_basic 三个目录),返回湖根。`basic` 的值 = (total_mv 万元, 换手率%)。"""
    daily = root / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    for day, per in bars.items():
        rows = [{"ts_code": to_ts_code(code), "trade_date": day, **bar}
                for code, bar in per.items()]
        pd.DataFrame(rows).to_parquet(daily / f"{day}.parquet", index=False)
    if basic:
        db = root / "daily_basic"
        db.mkdir(parents=True, exist_ok=True)
        for day, per in basic.items():
            rows = [{"ts_code": to_ts_code(code), "trade_date": day,
                     "total_mv": mv, "turnover_rate": tr} for code, (mv, tr) in per.items()]
            pd.DataFrame(rows).to_parquet(db / f"{day}.parquet", index=False)
    sb = root / "stock_basic"
    sb.mkdir(parents=True, exist_ok=True)
    codes = sorted({c for per in bars.values() for c in per})
    pd.DataFrame([{"ts_code": to_ts_code(c), "name": (names or {}).get(c, f"测试{c}"),
                   "list_date": "20100101", "market": "主板", "industry": "测试"}
                  for c in codes]).to_parquet(sb / "static.parquet", index=False)
    return root


def _bar(close: float, prev: float, *, i: int = 0) -> dict:
    o = round((prev + close) / 2, 4)
    return {"open": o, "high": round(max(o, close) * 1.01, 4),
            "low": round(min(o, close) * 0.99, 4), "close": close,
            "pct_chg": round((close / prev - 1) * 100, 4) if prev else 0.0,
            "amount": 1000.0 + i}


def _bars_from_closes(closes: dict[str, list[float]], days: list[str]) -> dict:
    bars: dict[str, dict[str, dict]] = {}
    for i, day in enumerate(days):
        bars[day] = {code: _bar(cs[i], cs[i - 1] if i else cs[i], i=i)
                     for code, cs in closes.items()}
    return bars


def _smooth(base: float, n: int, k: float) -> list[float]:
    return [round(base * (1 + 0.02 * math.sin(k + i)), 4) for i in range(n)]


@pytest.fixture(autouse=True)
def _isolated_reports(tmp_path, monkeypatch):
    """任何测试都不许把缓存写进真实 `reports_<engine>/`。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports")


@pytest.fixture
def lake(tmp_path) -> Path:
    closes = {"600001": _smooth(10.0, len(DAYS), 0.0),
              "600002": _smooth(20.0, len(DAYS), 1.3),
              "300001": _smooth(15.0, len(DAYS), 2.6),
              "688001": _smooth(30.0, len(DAYS), 3.9)}
    root = tmp_path / "lake"
    basic = {d: dict.fromkeys(closes, (400000.0 + 1000 * i, 1.5)) for i, d in enumerate(DAYS)}
    return _write_lake(root, _bars_from_closes(closes, DAYS), basic=basic)


# ───────────────────────── 1. 目标列与生产实现逐值相等 ─────────────────────────

def test_gap_pp_is_value_identical_to_factor_lab_forward_returns(lake):
    df = P_.build_panel(lake_root=lake, cache=False)
    days = ec.lake_trade_days(lake / "daily")
    piv = ec.load_lake_pivots(days, lake / "daily")
    assert set(df["date"]) == set(days)
    for day in days:
        fr = fl.forward_returns(piv, days, day, fwd=10)
        got = df[df["date"] == day].set_index("code")["gap_pp"].astype("float64")
        exp = (fr["gap_c1_o2"] * 100.0).reindex(got.index).astype("float64")
        np.testing.assert_allclose(got.to_numpy(), exp.to_numpy(), rtol=1e-5, equal_nan=True)
    # 同源的三列一起锁,防「只有 gap 走复用、旗自己再算一遍」
    for day in days:
        fr = fl.forward_returns(piv, days, day, fwd=10)
        sub = df[df["date"] == day].set_index("code")
        exp_sell = fr["unsellable_o2"].fillna(False).astype(bool).reindex(sub.index)
        assert sub["unsellable_o2"].tolist() == exp_sell.tolist()
        exp_known = fr["buyable_c1"].notna().reindex(sub.index)
        assert sub["buyable_known"].tolist() == exp_known.tolist()


def test_gap_pp_is_not_shifted_to_next_session(lake):
    """变异探针:把因子错移到 D+1 必须与面板不同(否则第 1 条断言是恒真的假绿灯)。"""
    df = P_.build_panel(lake_root=lake, cache=False)
    days = ec.lake_trade_days(lake / "daily")
    piv = ec.load_lake_pivots(days, lake / "daily")
    diffs = 0
    for i, day in enumerate(days[:-1]):
        shifted = fl.forward_returns(piv, days, days[i + 1], fwd=10)["gap_c1_o2"] * 100.0
        got = df[df["date"] == day].set_index("code")["gap_pp"].astype("float64")
        exp = shifted.reindex(got.index).astype("float64")
        both = got.notna() & exp.notna()
        if both.any() and not np.allclose(got[both], exp[both], rtol=1e-9):
            diffs += 1
    assert diffs > 0


# ───────────────────────── 2. GAP_CLIP ─────────────────────────

def test_gap_beyond_clip_is_nan_and_counted(tmp_path):
    # D = 20260105 的 gap 吃 close(20260106) 与 open(20260107):10 → 14 = +40% > 31%。
    per = {
        "20260105": {"600001": _bar(10.0, 10.0)},
        "20260106": {"600001": _bar(10.0, 10.0)},
        "20260107": {"600001": {"open": 14.0, "high": 14.2, "low": 13.8, "close": 14.0,
                                "pct_chg": 40.0, "amount": 1000.0}},
        "20260108": {"600001": _bar(14.0, 14.0)},
    }
    lake = _write_lake(tmp_path / "lake", per)
    df = P_.build_panel(lake_root=lake, cache=False)
    row = df[(df["date"] == "20260105") & (df["code"] == "600001")].iloc[0]
    assert pd.isna(row["gap_pp"])
    assert df.attrs["meta"]["n_clipped"] == 1
    assert df.attrs["meta"]["gap_clip"] == _ruler.GAP_CLIP


# ───────────────────────── 3. rel_gap_pp 的分母人口 ─────────────────────────

def test_rel_gap_baseline_excludes_unbuyable_but_keeps_its_own_gap(tmp_path):
    """封板票(T+1 收盘涨停)被剔出**分母**,但它自己仍然有 gap_pp 与 rel_gap_pp。"""
    sealed = {"open": 10.5, "high": 11.0, "low": 10.4, "close": 11.0,   # 收=高 ∧ 涨 10%
              "pct_chg": 10.0, "amount": 1000.0}
    bars = {
        "20260105": {"600001": _bar(10.0, 10.0), "600002": _bar(20.0, 20.0),
                     "600003": _bar(30.0, 30.0)},
        "20260106": {"600001": sealed, "600002": _bar(20.4, 20.0), "600003": _bar(29.4, 30.0)},
        # D+2 开盘:三只各自不同 gap
        "20260107": {"600001": {"open": 11.55, "high": 11.6, "low": 11.5, "close": 11.5,
                                "pct_chg": 4.5, "amount": 1000.0},
                     "600002": {"open": 20.808, "high": 21.0, "low": 20.7, "close": 20.9,
                                "pct_chg": 2.4, "amount": 1000.0},
                     "600003": {"open": 29.106, "high": 29.4, "low": 29.0, "close": 29.2,
                                "pct_chg": -0.7, "amount": 1000.0}},
    }
    lake = _write_lake(tmp_path / "lake", bars)
    df = P_.build_panel(lake_root=lake, cache=False)
    day = df[df["date"] == "20260105"].set_index("code")
    assert bool(day.loc["600001", "buyable_c1"]) is False
    assert bool(day.loc["600002", "buyable_c1"]) is True
    gaps = day["gap_pp"].astype("float64")
    assert gaps.notna().all()                       # 不可买票**自己**仍有 gap
    base = float(gaps[["600002", "600003"]].mean())  # 分母只含可买两只
    for code in ("600001", "600002", "600003"):
        assert float(day.loc[code, "rel_gap_pp"]) == pytest.approx(gaps[code] - base, abs=1e-4)
    # 变异探针:若分母误含封板票,基准会变 → 断言必须能分辨
    wrong = float(gaps.mean())
    assert abs(wrong - base) > 1e-3


# ───────────────────────── 4. exec_ok 与生产常量/符号 ─────────────────────────

def test_exec_ok_constants_come_from_production():
    assert P_.EXEC_MAX_PCT_1D is _outcome.EXEC_MAX_PCT_1D
    assert P_.EXEC_MAX_POS_IN_RANGE is _outcome.EXEC_MAX_POS_IN_RANGE


def _tri(value) -> bool | None:
    """可空 boolean 单元格 → True / False / None(把 `np.True_` 折成 python bool 再断言)。"""
    return None if pd.isna(value) else bool(value)


def _exec_case(pct: float, pos: float) -> dict:
    """T+1 的一根 K:`pct_chg = pct`、`(close−low)/(high−low) = pos`(low 10 / high 20)。"""
    return {"open": 12.0, "high": 20.0, "low": 10.0, "close": 10.0 + pos * 10.0,
            "pct_chg": pct, "amount": 1000.0}


def test_exec_ok_boundaries_match_outcome_symbols(tmp_path):
    """`<= EXEC_MAX_PCT_1D` 与 `< EXEC_MAX_POS_IN_RANGE`(与生产逐字同义)。"""
    cases = {"600001": (3.0, 0.5), "600002": (3.01, 0.5),
             "600003": (1.0, 0.699), "600004": (1.0, 0.7)}
    bars = {
        "20260105": {c: _bar(15.0, 15.0) for c in cases},
        "20260106": {c: _exec_case(*v) for c, v in cases.items()},
        "20260107": {c: _bar(15.5, 15.0) for c in cases},
    }
    lake = _write_lake(tmp_path / "lake", bars)
    day = P_.build_panel(lake_root=lake, cache=False)
    day = day[day["date"] == "20260105"].set_index("code")
    assert _tri(day.loc["600001", "exec_ok"]) is True         # 3.0 恰好过(<=)
    assert _tri(day.loc["600002", "exec_ok"]) is False        # 3.01 不过
    assert _tri(day.loc["600003", "exec_ok"]) is True         # 0.699 过(<)
    assert _tri(day.loc["600004", "exec_ok"]) is False        # 0.7 恰好不过
    assert float(day.loc["600004", "t1_pos_in_range"]) == pytest.approx(0.7, abs=1e-6)


def test_exec_ok_is_na_when_t1_unknown(tmp_path):
    """T+1 读数缺失 → `pd.NA`(未知),不折成 False。"""
    bars = {"20260105": {"600001": _bar(10.0, 10.0)}}
    lake = _write_lake(tmp_path / "lake", bars)
    df = P_.build_panel(lake_root=lake, cache=False)
    assert df["exec_ok"].isna().all()


# ───────────────────────── 5. daily_basic 前向填充 ≤10 交易日 ─────────────────────────

def test_total_mv_forward_fill_stops_after_ten_trading_days(tmp_path):
    days = [f"202601{d:02d}" for d in range(5, 18)]     # 13 个「交易日」
    closes = {"600001": [10.0 + 0.1 * i for i in range(len(days))]}
    bars = _bars_from_closes(closes, days)
    basic = {days[0]: {"600001": (500000.0, 2.5)}}       # 只有第 0 天有真值
    lake = _write_lake(tmp_path / "lake", bars, basic=basic)
    df = P_.build_panel(lake_root=lake, cache=False).set_index("date")
    for k in range(0, P_.MV_FILL_MAX_DAYS + 1):
        assert float(df.loc[days[k], "total_mv_yi"]) == pytest.approx(50.0, abs=1e-3)
        assert float(df.loc[days[k], "mv_stale_days"]) == pytest.approx(float(k))
        assert float(df.loc[days[k], "turnover_rate"]) == pytest.approx(2.5, abs=1e-4)
    for k in range(P_.MV_FILL_MAX_DAYS + 1, len(days)):  # 第 11 日起不再填
        assert pd.isna(df.loc[days[k], "total_mv_yi"])
        assert pd.isna(df.loc[days[k], "mv_stale_days"])
        assert pd.isna(df.loc[days[k], "turnover_rate"])
    meta = P_.build_panel(lake_root=lake, cache=False).attrs["meta"]
    assert meta["mv_coverage"] == pytest.approx(1 / len(days))
    assert meta["mv_filled"] == P_.MV_FILL_MAX_DAYS      # 填充是有损降级 → 必须记账
    assert meta["mv_fill_max_days"] == P_.MV_FILL_MAX_DAYS


def test_total_mv_unit_is_yi_not_wan(tmp_path):
    """`daily_basic.total_mv` 是**万元**;面板列是**亿元**(跨表比值前的单位锁)。"""
    days = ["20260105", "20260106"]
    bars = _bars_from_closes({"600001": [10.0, 10.1]}, days)
    lake = _write_lake(tmp_path / "lake", bars, basic={days[0]: {"600001": (123456.0, 1.0)}})
    df = P_.build_panel(lake_root=lake, cache=False)
    assert float(df.iloc[0]["total_mv_yi"]) == pytest.approx(12.3456, abs=1e-3)


# ───────────────────────── 6. 缺日不伪造 ─────────────────────────

def test_missing_lake_day_is_absent_not_interpolated(tmp_path):
    days = ["20260105", "20260106", "20260107", "20260108", "20260109"]
    closes = {"600001": [10.0, 10.2, 10.1, 10.4, 10.3]}
    bars = _bars_from_closes(closes, days)
    bars.pop("20260107")                                # 湖里就是没有这一天
    lake = _write_lake(tmp_path / "lake", bars)
    df = P_.build_panel(lake_root=lake, cache=False)
    assert sorted(df["date"].unique()) == ["20260105", "20260106", "20260108", "20260109"]
    df2 = P_.build_panel(since="20260106", until="20260108", lake_root=lake, cache=False)
    assert sorted(df2["date"].unique()) == ["20260106", "20260108"]
    assert df2.attrs["meta"]["n_days"] == 2


def test_rows_equal_lake_rows_no_ghost_codes(tmp_path):
    """窗口内并集的票不会在它没有日线的日子凭空出现(面板行数 = 湖行数)。"""
    days = ["20260105", "20260106", "20260107"]
    bars = {
        "20260105": {"600001": _bar(10.0, 10.0), "600002": _bar(20.0, 20.0)},
        "20260106": {"600001": _bar(10.1, 10.0)},        # 600002 当天停牌
        "20260107": {"600001": _bar(10.2, 10.1), "600002": _bar(20.5, 20.0)},
    }
    lake = _write_lake(tmp_path / "lake", bars)
    df = P_.build_panel(lake_root=lake, cache=False)
    assert len(df) == sum(len(v) for v in bars.values())
    assert df[df["date"] == "20260106"]["code"].tolist() == ["600001"]
    assert df.attrs["meta"]["n_rows"] == len(df)
    assert sorted(df["date"].unique()) == days


def test_all_nan_field_code_does_not_break_pivot_alignment(tmp_path):
    """`pivot_table` 会把某字段全 NaN 的 code 整行丢掉 → 各字段行轴不一致 →
    `factor_lab.forward_returns` 抛 `Can only compare identically-labeled Series objects`。

    真湖判例(2026-08-28,首个 250 日块):北交所新股 `920570` 在窗内只有 1 天日线且当天
    `pct_chg` 为 NaN。面板层负责把行轴补齐(只补 NaN、不改数值)并把 repair 记进 meta。
    """
    days = ["20260105", "20260106", "20260107"]
    bars = {d: {"600001": _bar(10.0 + 0.1 * i, 10.0 + 0.1 * (i - 1) if i else 10.0, i=i)}
            for i, d in enumerate(days)}
    bars[days[1]]["920570"] = {"open": 5.0, "high": 5.5, "low": 4.9, "close": 5.2,
                               "pct_chg": float("nan"), "amount": 500.0}
    lake = _write_lake(tmp_path / "lake", bars)
    piv = ec.load_lake_pivots(ec.lake_trade_days(lake / "daily"), lake / "daily")
    assert not piv["pct_chg"].index.equals(piv["close"].index)   # 前提成立才有得修
    df = P_.build_panel(lake_root=lake, cache=False)             # 不修就是 ValueError
    assert df.attrs["meta"]["n_pivot_realigned"] >= 1
    row = df[(df["date"] == days[1]) & (df["code"] == "920570")]
    assert len(row) == 1
    assert float(row.iloc[0]["close_d"]) == pytest.approx(5.2)
    assert row.iloc[0]["ts_code"] == "920570.BJ"


# ───────────────────────── 7. 缓存往返 ─────────────────────────

def test_cache_round_trip_restores_meta(lake, tmp_path):
    built = P_.build_panel(lake_root=lake, cache=True)
    assert P_.panel_path().exists() and P_.panel_path().parent.joinpath("panel_meta.json").exists()
    assert built.attrs["meta"]["from_cache"] is False
    again = P_.build_panel(lake_root=lake, cache=True)
    assert again.attrs["meta"]["from_cache"] is True
    assert again.attrs["meta"]["n_clipped"] == built.attrs["meta"]["n_clipped"]
    assert again.attrs["meta"]["is_st_source"] == P_.IS_ST_SOURCE
    assert again.shape == built.shape
    assert again.dtypes.astype(str).to_dict() == built.dtypes.astype(str).to_dict()
    pd.testing.assert_frame_equal(again.reset_index(drop=True), built.reset_index(drop=True))
    sidecar = json.loads(P_.panel_path().with_name("panel_meta.json").read_text(encoding="utf-8"))
    assert sidecar["built_first"] == DAYS[0] and sidecar["built_last"] == DAYS[-1]


def test_cache_serves_subrange_and_rebuild_bypasses(lake):
    P_.build_panel(lake_root=lake, cache=True)
    sub = P_.build_panel(since=DAYS[2], until=DAYS[4], lake_root=lake, cache=True)
    assert sub.attrs["meta"]["from_cache"] is True
    assert sorted(sub["date"].unique()) == DAYS[2:5]
    fresh = P_.build_panel(since=DAYS[2], until=DAYS[4], lake_root=lake, cache=True, rebuild=True)
    assert fresh.attrs["meta"]["from_cache"] is False
    assert sorted(fresh["date"].unique()) == DAYS[2:5]


def test_cache_miss_when_range_not_covered(lake):
    P_.build_panel(since=DAYS[3], until=DAYS[4], lake_root=lake, cache=True)
    wider = P_.build_panel(lake_root=lake, cache=True)          # 请求区间超出缓存 → 重建
    assert wider.attrs["meta"]["from_cache"] is False
    assert sorted(wider["date"].unique()) == DAYS


def test_cache_false_never_writes(lake):
    P_.build_panel(lake_root=lake, cache=False)
    assert not P_.panel_path().exists()


# ───────────────────────── 人口门 / ST / 派生列 ─────────────────────────

def test_in_pop_gates_st_bj_capfloor_and_buyability(tmp_path):
    days = ["20260105", "20260106", "20260107"]
    codes = {"600001": 30.0, "600002": 29.99, "600003": 30.0, "830001": 100.0, "600004": 100.0}
    bars: dict[str, dict[str, dict]] = {}
    for i, day in enumerate(days):
        bars[day] = {c: _bar(10.0 + 0.1 * i, 10.0 + 0.1 * (i - 1) if i else 10.0, i=i)
                     for c in codes}
    # 600004 在 T+1 封涨停 → 买不进 → 出人口
    bars[days[1]]["600004"] = {"open": 10.6, "high": 11.11, "low": 10.5, "close": 11.11,
                               "pct_chg": 10.0, "amount": 1000.0}
    basic = {days[0]: {c: (mv * 1e4, 1.0) for c, mv in codes.items()}}
    lake = _write_lake(tmp_path / "lake", bars, basic=basic, names={"600003": "*ST测试"})
    df = P_.build_panel(lake_root=lake, cache=False)
    day0 = df[df["date"] == days[0]].set_index("code")
    assert bool(day0.loc["600001", "in_pop"]) is True     # 恰好 = CAP_FLOOR_YI(>=)
    assert bool(day0.loc["600002", "in_pop"]) is False    # 差 0.01 亿
    assert bool(day0.loc["600003", "in_pop"]) is False    # *ST
    assert bool(day0.loc["600003", "is_st"]) is True
    assert bool(day0.loc["830001", "in_pop"]) is False    # 北交所
    assert day0.loc["830001", "ts_code"] == "830001.BJ"
    assert bool(day0.loc["600004", "in_pop"]) is False    # T+1 封板买不进
    assert bool(day0.loc["600004", "buyable_c1"]) is False
    assert CAP_FLOOR_YI == 30.0                           # 常量单点(改坏 → 上面四条变红)
    assert df.attrs["meta"]["cap_floor_yi"] == CAP_FLOOR_YI
    assert df.attrs["meta"]["is_st_source"] == "static snapshot, not PIT"


def test_missing_market_cap_keeps_row_out_of_population(tmp_path):
    """没有市值就不知道够不够地板 → 出人口(不猜),但行仍在面板里。"""
    days = ["20260105", "20260106", "20260107"]
    bars = _bars_from_closes({"600001": [10.0, 10.1, 10.2]}, days)
    lake = _write_lake(tmp_path / "lake", bars)            # 没有 daily_basic 目录
    df = P_.build_panel(lake_root=lake, cache=False)
    assert len(df) == 3
    assert df["total_mv_yi"].isna().all()
    assert not df["in_pop"].any()
    assert df.attrs["meta"]["mv_coverage"] == 0.0
    assert df.attrs["meta"]["mv_filled"] == 0


def test_pct5d_vol20_limit5d_and_amount_unit(tmp_path):
    days = [f"202601{d:02d}" for d in range(5, 30)]
    closes = {"600001": [10.0 * (1.0 + 0.01 * i) for i in range(len(days))]}
    bars = _bars_from_closes(closes, days)
    bars[days[20]]["600001"]["pct_chg"] = 10.0             # 一个涨停落在 days[20]
    lake = _write_lake(tmp_path / "lake", bars)
    df = P_.build_panel(lake_root=lake, cache=False).set_index("date")
    # pct_5d:不足 5 日 → NaN;够了 → close(D)/close(D−5)−1
    assert pd.isna(df.loc[days[4], "pct_5d_pp"])
    exp = (closes["600001"][5] / closes["600001"][0] - 1) * 100
    assert float(df.loc[days[5], "pct_5d_pp"]) == pytest.approx(exp, rel=1e-4)
    # vol20:观测不足 VOL20_MIN_OBS → NaN
    assert pd.isna(df.loc[days[P_.VOL20_MIN_OBS - 2], "vol20"])
    assert not pd.isna(df.loc[days[P_.VOL20_MIN_OBS - 1], "vol20"])
    # limit_5d:含 D 的近 5 日出现过涨停
    assert not bool(df.loc[days[19], "limit_5d"])
    assert bool(df.loc[days[20], "limit_5d"])
    assert bool(df.loc[days[24], "limit_5d"])
    assert not bool(df.loc[days[19], "limit_5d"])
    # amount_d 原样透传湖的**千元**口径,不换算
    assert float(df.loc[days[0], "amount_d"]) == pytest.approx(bars[days[0]]["600001"]["amount"])


def test_board_limit_is_per_board(tmp_path):
    """创业板 20cm:9.9% 不算涨停,19.8% 才算(板幅走 `factor_lab._board_limit`)。"""
    days = ["20260105", "20260106"]
    bars = {days[0]: {"300001": {"open": 10.0, "high": 11.0, "low": 9.9, "close": 10.99,
                                 "pct_chg": 9.9, "amount": 1000.0},
                      "600001": {"open": 10.0, "high": 11.0, "low": 9.9, "close": 10.99,
                                 "pct_chg": 9.9, "amount": 1000.0}},
             days[1]: {c: _bar(11.0, 10.99) for c in ("300001", "600001")}}
    lake = _write_lake(tmp_path / "lake", bars)
    day = P_.build_panel(lake_root=lake, cache=False)
    day = day[day["date"] == days[0]].set_index("code")
    assert bool(day.loc["600001", "limit_5d"]) is True     # 主板 10cm → 9.9 ≥ 9.8
    assert bool(day.loc["300001", "limit_5d"]) is False    # 创业板 20cm → 9.9 < 19.6
    assert fl._board_limit("300001") == 20.0


def test_columns_dtypes_and_empty_range(lake):
    df = P_.build_panel(lake_root=lake, cache=False)
    assert list(df.columns) == list(P_.COLUMNS)
    for col in P_._FLOAT_COLS:
        assert str(df[col].dtype) == "float32"
    assert str(df["exec_ok"].dtype) == "boolean"
    empty = P_.build_panel(since="20990101", lake_root=lake, cache=False)
    assert empty.empty
    assert list(empty.columns) == list(P_.COLUMNS)
    assert empty.attrs["meta"]["n_days"] == 0
    assert empty.attrs["meta"]["lake_last"] == DAYS[-1]
