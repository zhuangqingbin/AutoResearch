"""ruler_compare 单测 —— 两尺对照(gap_c1_o2 vs fwd_2_oc)换尺认知底片。合成数据,零网络。

覆盖(与模块自带 `_selftest()` 互补,那里手算核对四节聚合数值;这里补 IO 层 + 边界情形):
  - gap_frame:湖 OHLC 现算 gap_c1_o2/buyable_c1/eligible_gap(腿别对照沿用
    tests/research/test_ruler_gap.py 同款数字,交叉验证两处实现口径一致)
  - day_universe:两尺并列 join + GAP_CLIP 裁剪
  - gate_day_stats / channel_day_stats / l3_day_stats / _verdict:边界情形(空表/缺列/无 shadow)
  - 端到端:analyze() 读合成 scan_root(attribution/L1_channels/L3_judged/rejection_attribution/
    abstention_verdict)+ 合成湖,对照手算的门的价值/channel 排名互换/L3 edge/弃权翻转
  - render():五节标题齐全 + 作废清单点名 value/momentum/0买日
  - main():--selftest 与 run 子命令端到端
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from autoresearch.common import ruler
from autoresearch.research.ruler_compare import (
    _agg_column,
    _bool_col,
    _gate_diff,
    _retirement_notes,
    _selftest,
    _verdict,
    analyze,
    channel_day_stats,
    day_universe,
    gap_frame,
    gate_day_stats,
    gate_value,
    l3_day_stats,
    l3_edge,
    lake_days,
    load_shadow_buys,
    main,
    pinned_codes_from_finalists,
    render,
    scan_dates_with_attribution,
    shadow_codes_by_date,
)

pytest.importorskip("pyarrow")   # 湖是 parquet;沙箱无 pyarrow 时跳过(与仓库其余 parquet 测试同策略)


# ───────────────────────── gap_frame(湖现算,腿别对照) ─────────────────────────


def _lake_day(lake: Path, day: str, rows: list[dict]) -> None:
    lake.mkdir(parents=True, exist_ok=True)
    cols = ["ts_code", "open", "high", "low", "close", "pct_chg"]
    pd.DataFrame(rows, columns=cols).to_parquet(lake / f"{day}.parquet")


def test_gap_frame_leg_exact(tmp_path):
    """数字沿用 test_ruler_gap.py::test_gap_legs_exact(10.5/10.0−1=0.05)—— 两处独立实现
    对同一组腿算出同一个数,交叉验证 D+1/D+2 定位没有被搞反。"""
    lake = tmp_path / "lake"
    _lake_day(lake, "20260101", [{"ts_code": "000001.SZ", "open": 9.0, "high": 9.9,
                                  "low": 8.9, "close": 9.5, "pct_chg": 1.0}])
    _lake_day(lake, "20260102", [{"ts_code": "000001.SZ", "open": 9.6, "high": 10.2,
                                  "low": 9.4, "close": 10.0, "pct_chg": 5.26}])
    _lake_day(lake, "20260103", [{"ts_code": "000001.SZ", "open": 10.5, "high": 11.0,
                                  "low": 10.3, "close": 10.8, "pct_chg": 8.0}])
    g = gap_frame("2026-01-01", lake=lake)
    row = g[g["code"] == "000001"].iloc[0]
    assert abs(row["gap_c1_o2"] - 0.05) < 1e-9
    assert bool(row["eligible_gap"]) is True


def test_gap_frame_buyable_c1_sealed(tmp_path):
    """D+1 收盘封涨停(20cm 创业板)→ buyable_c1=False,eligible_gap 随之 False。"""
    lake = tmp_path / "lake"
    _lake_day(lake, "20260101", [{"ts_code": "300999.SZ", "open": 10.0, "high": 10.0,
                                  "low": 10.0, "close": 10.0, "pct_chg": 0.0}])
    _lake_day(lake, "20260102", [{"ts_code": "300999.SZ", "open": 11.0, "high": 12.0,
                                  "low": 11.0, "close": 12.0, "pct_chg": 20.0}])  # 20cm 封板收盘
    _lake_day(lake, "20260103", [{"ts_code": "300999.SZ", "open": 12.5, "high": 12.6,
                                  "low": 12.4, "close": 12.5, "pct_chg": 4.2}])
    g = gap_frame("2026-01-01", lake=lake)
    row = g[g["code"] == "300999"].iloc[0]
    assert not bool(row["buyable_c1"])
    assert not bool(row["eligible_gap"])


def test_gap_frame_missing_d2_returns_empty(tmp_path):
    """D+2 分区不存在(越界)→ 空表,不抛异常。"""
    lake = tmp_path / "lake"
    _lake_day(lake, "20260101", [{"ts_code": "000001.SZ", "open": 9.0, "high": 9.9,
                                  "low": 8.9, "close": 9.5, "pct_chg": 1.0}])
    _lake_day(lake, "20260102", [{"ts_code": "000001.SZ", "open": 9.6, "high": 10.2,
                                  "low": 9.4, "close": 10.0, "pct_chg": 5.26}])
    g = gap_frame("2026-01-01", lake=lake)
    assert g.empty
    assert list(g.columns) == ["code", "gap_c1_o2", "buyable_c1", "eligible_gap"]


def test_gap_frame_scan_date_not_in_lake_returns_empty(tmp_path):
    lake = tmp_path / "lake"
    _lake_day(lake, "20260102", [{"ts_code": "000001.SZ", "open": 9.6, "high": 10.2,
                                  "low": 9.4, "close": 10.0, "pct_chg": 5.26}])
    g = gap_frame("2026-01-01", lake=lake)   # 2026-01-01 本身不在湖里
    assert g.empty


def test_lake_days_sorted_and_filters_non_parquet(tmp_path):
    lake = tmp_path / "lake"
    lake.mkdir()
    (lake / "20260103.parquet").touch()
    (lake / "20260101.parquet").touch()
    (lake / "readme.txt").touch()
    assert lake_days(lake) == ["20260101", "20260103"]


def test_lake_days_missing_dir_returns_empty(tmp_path):
    assert lake_days(tmp_path / "nonexistent") == []


# ───────────────────────── day_universe(GAP_CLIP 裁剪 + eligible join) ─────────────────────────


def test_day_universe_clips_extreme_gap_to_GAP_CLIP():
    attr = pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.05, "buyable": True, "bought": False}])
    gap = pd.DataFrame([{"code": "000001", "gap_c1_o2": 0.90, "buyable_c1": True, "eligible_gap": True}])
    u = day_universe(attr, gap)
    assert abs(u.iloc[0]["gap_c1_o2"] - ruler.GAP_CLIP) < 1e-9


def test_day_universe_negative_extreme_gap_clips_symmetric():
    attr = pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.05, "buyable": True, "bought": False}])
    gap = pd.DataFrame([{"code": "000001", "gap_c1_o2": -0.90, "buyable_c1": True, "eligible_gap": True}])
    u = day_universe(attr, gap)
    assert abs(u.iloc[0]["gap_c1_o2"] - (-ruler.GAP_CLIP)) < 1e-9


def test_day_universe_missing_code_in_gap_frame_is_not_eligible():
    """gap_frame 里没有这只票(该日 D+1/D+2 湖分区里查无)→ elig_gap=False,gap_c1_o2=NaN,
    不是静默当 0 或当"可交易"处理。"""
    attr = pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.05, "buyable": True, "bought": True}])
    gap = pd.DataFrame(columns=["code", "gap_c1_o2", "buyable_c1", "eligible_gap"])
    u = day_universe(attr, gap)
    row = u.iloc[0]
    assert row["elig_gap"] is False or row["elig_gap"] == False  # noqa: E712
    assert pd.isna(row["gap_c1_o2"])
    assert row["bought"] == True  # noqa: E712 — oc 侧不受 gap 侧缺数影响


def test_day_universe_buyable_defaults_true_when_column_missing():
    attr = pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.05, "bought": False}])  # 无 buyable 列
    gap = pd.DataFrame([{"code": "000001", "gap_c1_o2": 0.01, "buyable_c1": True, "eligible_gap": True}])
    u = day_universe(attr, gap)
    assert bool(u.iloc[0]["elig_oc"]) is True


# ───────────────────────── scan 目录发现 / shadow_buys ─────────────────────────


def test_scan_dates_with_attribution_filters_and_limits(tmp_path):
    root = tmp_path / "scan"
    for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
        (root / d / "retro").mkdir(parents=True)
        (root / d / "retro" / "attribution.csv").write_text("code\n000001\n")
    (root / "2026-01-04").mkdir(parents=True)   # 无 retro/attribution.csv → 不计入
    assert scan_dates_with_attribution(root) == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert scan_dates_with_attribution(root, limit=2) == ["2026-01-02", "2026-01-03"]


def test_scan_dates_with_attribution_missing_root(tmp_path):
    assert scan_dates_with_attribution(tmp_path / "nope") == []


def test_load_shadow_buys_zfills_code(tmp_path):
    p = tmp_path / "shadow_buys.csv"
    p.write_text("date,code\n2026-01-01,7\n")
    df = load_shadow_buys(p)
    assert df.iloc[0]["code"] == "000007"


def test_load_shadow_buys_missing_file_returns_empty(tmp_path):
    df = load_shadow_buys(tmp_path / "nope.csv")
    assert df.empty and list(df.columns) == ["date", "code"]


def test_shadow_codes_by_date_groups():
    df = pd.DataFrame([{"date": "2026-01-01", "code": "000001"},
                       {"date": "2026-01-01", "code": "000002"},
                       {"date": "2026-01-02", "code": "000003"}])
    out = shadow_codes_by_date(df)
    assert out == {"2026-01-01": {"000001", "000002"}, "2026-01-02": {"000003"}}


# ───────────────────────── ①②③ 纯函数边界情形 ─────────────────────────


def test_gate_day_stats_no_shadow_no_bought():
    u = pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.05, "gap_c1_o2": 0.02,
                       "elig_oc": True, "elig_gap": True, "bought": False}])
    s = gate_day_stats(u, set())
    assert s["n_real"] == 0 and s["n_shadow"] == 0
    assert s["real_oc"] is None and s["shadow_oc"] is None
    assert abs(s["market_oc"] - 0.05) < 1e-9


# ── _agg_column / _gate_diff(Important-3 review fix:①的生产聚合函数直接单测,
#    不再只靠 _selftest() 里手写一遍的重复实现;三值夹具专门区分 mean vs median) ──


def test_agg_column_mean_not_median_three_values():
    table = pd.DataFrame({"x": [0.02, 0.05, 0.20]})   # median=0.05,mean=0.09—— 两者不同才有鉴别力
    out = _agg_column(table, "x")
    assert out["n_days"] == 3
    assert abs(out["value"] - (0.02 + 0.05 + 0.20) / 3) < 1e-9
    assert abs(out["value"] - 0.05) > 1e-6   # 不是中位数


def test_agg_column_empty_table_returns_none_value():
    out = _agg_column(pd.DataFrame(), "x")
    assert out == {"value": None, "n_days": 0}


def test_agg_column_missing_column_returns_none_value():
    out = _agg_column(pd.DataFrame({"y": [0.1]}), "x")
    assert out == {"value": None, "n_days": 0}


def test_agg_column_drops_nan_independently():
    table = pd.DataFrame({"x": [0.1, None, 0.3]})
    out = _agg_column(table, "x")
    assert out["n_days"] == 2
    assert abs(out["value"] - 0.2) < 1e-9


def test_gate_diff_is_real_minus_shadow_not_reversed():
    real, shadow = {"value": 0.05, "n_days": 1}, {"value": 0.02, "n_days": 1}
    assert abs(_gate_diff(real, shadow) - 0.03) < 1e-9
    assert abs(_gate_diff(shadow, real) - (-0.03)) < 1e-9   # 参数顺序颠倒 → 结果反号


def test_gate_diff_none_when_either_side_missing():
    assert _gate_diff({"value": None, "n_days": 0}, {"value": 0.02, "n_days": 1}) is None
    assert _gate_diff({"value": 0.02, "n_days": 1}, {"value": None, "n_days": 0}) is None


def test_gate_value_paired_excludes_days_without_real_buys(tmp_path):
    """Important-2 review fix:非配对(real 只在有买单日有数)与配对(限制到同一批买单日)
    分母不同——用 3 天(2 天有买单、1 天没有)验证配对聚合真的只用了 2 天,而不是全 3 天。
    `real_oc` 天然只在买单日有数(非配对聚合本身就已经是"只用买单日"),所以真正能证明
    "配对 vs 非配对分母不同"的是 `shadow_oc`:它每天都有数,唯有配对聚合才会把第 3 天
    (无买单)剔除——这正是 review Important-2 指出的"非配对相减混入选择效应"的病灶所在。
    """
    scan_root = tmp_path / "scan"
    # 000002 每天都是 shadow(影子候选),000001 只在前两天被真实买入;第 3 天(无买单)
    # 000002 的 fwd_2_oc 给一个极端值(0.50),如果配对聚合没有正确剔除第 3 天,均值会被这个
    # 极端值显著拖动,断言就会失败——这就是本测试的鉴别力所在。
    days = {
        "2026-02-02": {"000001": (0.10, 0.05, True), "000002": (0.02, 0.01, False)},   # 有买单
        "2026-02-03": {"000001": (0.06, 0.03, True), "000002": (0.04, 0.02, False)},   # 有买单
        "2026-02-04": {"000001": (-0.20, 0.01, False), "000002": (0.50, 0.01, False)},  # 无买单
    }
    for d, rows in days.items():
        dd = scan_root / d / "retro"
        dd.mkdir(parents=True)
        pd.DataFrame([{"code": c, "fwd_2_oc": oc, "buyable": True, "bought": bought}
                     for c, (oc, _g, bought) in rows.items()]).to_csv(dd / "attribution.csv", index=False)
    gap_cache = {d: pd.DataFrame([{"code": c, "gap_c1_o2": g, "buyable_c1": True, "eligible_gap": True}
                                  for c, (_oc, g, _b) in rows.items()])
                for d, rows in days.items()}
    shadow_df = pd.DataFrame([{"date": d, "code": "000002"} for d in days])   # 每天都影子命中 000002
    result = gate_value(sorted(days), scan_root=scan_root, shadow_df=shadow_df, gap_cache=gap_cache)
    assert result["n_days"] == 3            # 全窗口三天都有 attribution.csv
    assert result["n_paired_days"] == 2     # 但只有两天有买单 → 配对聚合只用这两天

    # 配对:real_oc=(0.10+0.06)/2=0.08;shadow_oc 限同两天=(0.02+0.04)/2=0.03
    assert abs(result["agg_paired"]["real_oc"]["value"] - 0.08) < 1e-6
    assert abs(result["agg_paired"]["shadow_oc"]["value"] - 0.03) < 1e-6
    assert result["agg_paired"]["shadow_oc"]["n_days"] == 2

    # 非配对:shadow_oc 用全 3 天=(0.02+0.04+0.50)/3≈0.1867 —— 与配对版明显不同,证明
    # 两者分母确实不同,不是同一个数字披了两层皮。
    assert result["agg"]["shadow_oc"]["n_days"] == 3
    assert abs(result["agg"]["shadow_oc"]["value"] - (0.02 + 0.04 + 0.50) / 3) < 1e-6
    assert result["agg"]["shadow_oc"]["value"] > result["agg_paired"]["shadow_oc"]["value"] + 0.1

    # 门的价值(配对)= 0.08 − 0.03 = 0.05,不是用非配对 shadow(0.1867)去减
    assert abs(result["gate_value_paired_oc"] - 0.05) < 1e-6


def test_channel_day_stats_empty_when_no_channel_column():
    u = pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.05, "gap_c1_o2": 0.02,
                       "elig_oc": True, "elig_gap": True}])
    out = channel_day_stats(pd.DataFrame({"code": ["000001"]}), u, "fwd_2_oc", "elig_oc")
    assert out.empty


def test_l3_day_stats_none_when_finalist_column_missing():
    u = pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.05, "elig_oc": True}])
    assert l3_day_stats(pd.DataFrame({"code": ["000001"]}), u, "fwd_2_oc", "elig_oc") is None


def test_l3_day_stats_none_when_bench_side_empty():
    u = pd.DataFrame([{"code": "000001", "fwd_2_oc": 0.05, "elig_oc": True},
                      {"code": "000002", "fwd_2_oc": 0.03, "elig_oc": True}])
    judged = pd.DataFrame([{"code": "000001", "finalist": True}, {"code": "000002", "finalist": True}])
    assert l3_day_stats(judged, u, "fwd_2_oc", "elig_oc") is None   # bench 侧空


# ── pinned_codes_from_finalists / l3_day_stats(pinned_codes=...) ──
# (Important-1 review fix:📌保送票必须从 bench(分母)剔除,同 l3_marginal.FORCED_REASONS
# "两侧剔除"原则;真实数据交叉验证见 task-15-report.md v2 —— 剔除前 oc edge +1.59%,
# 剔除后 +0.93%,与 reviewer 独立复算完全一致)


def test_pinned_codes_from_finalists_reads_nonempty_pinned_note():
    finalists = pd.DataFrame([
        {"code": "000001", "pinned_note": "conviction_guard"},
        {"code": "2", "pinned_note": ""},          # 空字符串 → 不算保送(且验证 zfill)
        {"code": "3", "pinned_note": None},        # NaN → fillna("") 后不算保送
        {"code": "000004", "pinned_note": "  "},   # 纯空白 strip 后也不算保送
    ])
    assert pinned_codes_from_finalists(finalists) == {"000001"}


def test_pinned_codes_from_finalists_missing_column_or_empty():
    assert pinned_codes_from_finalists(pd.DataFrame({"code": ["000001"]})) == set()
    assert pinned_codes_from_finalists(pd.DataFrame()) == set()
    assert pinned_codes_from_finalists(None) == set()


def test_l3_day_stats_pinned_codes_excluded_from_both_buckets():
    """000003 是保送票,原本 finalist=False(会被算进 bench)——剔除后 bench 均值应只剩 000004,
    edge 应随之改变;若 000003 恰好 finalist=True,也不该被算进 finalist(两侧剔除)。"""
    u = pd.DataFrame([
        {"code": "000001", "fwd_2_oc": 0.10, "elig_oc": True},   # finalist
        {"code": "000002", "fwd_2_oc": 0.20, "elig_oc": True},   # finalist(另一票,凑够分子)
        {"code": "000003", "fwd_2_oc": -0.90, "elig_oc": True},  # bench,但是保送票——应被剔除
        {"code": "000004", "fwd_2_oc": 0.04, "elig_oc": True},   # 干净的 bench
    ])
    judged = pd.DataFrame([
        {"code": "000001", "finalist": True}, {"code": "000002", "finalist": True},
        {"code": "000003", "finalist": False}, {"code": "000004", "finalist": False},
    ])
    without = l3_day_stats(judged, u, "fwd_2_oc", "elig_oc")
    with_excl = l3_day_stats(judged, u, "fwd_2_oc", "elig_oc", pinned_codes={"000003"})
    assert without["n_bench"] == 2 and abs(without["bench_mean"] - (-0.43)) < 1e-9   # mean(-0.90,0.04)
    assert with_excl["n_bench"] == 1 and abs(with_excl["bench_mean"] - 0.04) < 1e-9   # 只剩 000004
    assert with_excl["n_finalist"] == 2   # finalist 侧本就没有 000003,不受影响
    # 剔除极端负值(-0.90)的保送票后 bench 均值从 -0.43 抬升到 0.04(变好了)——finalist 不变
    # (0.15)时,finalist−bench 这个差反而**收窄**(0.58→0.11),与真实数据的方向一致
    # (reviewer 独立复算:oc 头条从 +1.59% 剔除后降到 +0.93%)。
    assert abs(without["edge"] - 0.58) < 1e-9
    assert abs(with_excl["edge"] - 0.11) < 1e-9
    assert with_excl["edge"] < without["edge"]


def test_l3_edge_end_to_end_reads_pinned_from_finalists_csv(tmp_path):
    """finalists.csv 存在且带 pinned_note → l3_edge() 真的读了它、真的剔除;
    不给 finalists.csv(presence-gated)→ 退回不剔除的旧行为,不报错。"""
    scan_root = tmp_path / "scan"
    d = scan_root / "2026-03-02"
    (d / "retro").mkdir(parents=True)
    pd.DataFrame([
        {"code": "000001", "fwd_2_oc": 0.10, "buyable": True, "bought": False},
        {"code": "000002", "fwd_2_oc": 0.20, "buyable": True, "bought": False},
        {"code": "000003", "fwd_2_oc": -0.90, "buyable": True, "bought": False},
        {"code": "000004", "fwd_2_oc": 0.04, "buyable": True, "bought": False},
    ]).to_csv(d / "retro" / "attribution.csv", index=False)
    pd.DataFrame([
        {"code": "000001", "finalist": True}, {"code": "000002", "finalist": True},
        {"code": "000003", "finalist": False}, {"code": "000004", "finalist": False},
    ]).to_csv(d / "L3_judged_full.csv", index=False)
    # gap 侧给真实的四行(不能是空表——空表会让 elig_gap 全 False,gap 侧 l3_day_stats 返回
    # None,进而让 l3_edge() 把整天跳过,oc 侧数字也就永远读不到,和本测试想验证的东西无关)。
    gap = pd.DataFrame([{"code": c, "gap_c1_o2": 0.01, "buyable_c1": True, "eligible_gap": True}
                       for c in ("000001", "000002", "000003", "000004")])

    # 不给 finalists.csv → presence-gated 退回旧行为(bench 含 000003)
    no_finalists = l3_edge(["2026-03-02"], scan_root=scan_root, gap_cache={"2026-03-02": gap})
    assert abs(no_finalists["agg_oc"]["bench_mean"] - (-0.43)) < 1e-6
    assert no_finalists["agg_oc"]["n_pinned_excluded_total"] == 0

    # 给 finalists.csv 且 000003 是保送票 → bench 应剔除它
    pd.DataFrame([{"code": "000003", "pinned_note": "conviction_guard"}]).to_csv(
        d / "finalists.csv", index=False)
    with_finalists = l3_edge(["2026-03-02"], scan_root=scan_root, gap_cache={"2026-03-02": gap})
    assert abs(with_finalists["agg_oc"]["bench_mean"] - 0.04) < 1e-6
    assert with_finalists["agg_oc"]["n_pinned_excluded_total"] == 1


def test_verdict_empty_shadow_returns_none():
    codes = pd.Series(["000001"])
    s = pd.Series([True])
    assert _verdict(pd.Series([True]), pd.Series([-0.05]), s, codes, set(), False) is None


def test_verdict_degraded_forces_neutral_even_if_would_be_correct():
    codes = pd.Series(["000001"])
    elig = pd.Series([True])
    v = _verdict(pd.Series([False]), pd.Series([-0.05]), elig, codes, {"000001"}, True)
    assert v == "NEUTRAL"


# ── M4/M5 边界回归(Important-3 review 实测变异:旧夹具的机会命中恰好都在 shadow 内、
#    excess 都离 ±2pp 很远,两处漏检;这里专门构造"机会命中但不在 shadow"和"负但不够负"
#    两类此前测不出来的反例) ──


def test_verdict_opportunity_outside_shadow_does_not_trigger_false():
    """000001 有机会(excess≥2pp)但**不在** shadow 里;shadow 唯一成员 000002 没机会且
    excess≤-2pp → 应判 CORRECT,不应因为"某处存在机会"就误判 FALSE(v2 相对 v1 的核心
    设计点就是只认 shadow 集合内的机会,见 abstention_ledger.py 自身 docstring)。"""
    codes = pd.Series(["000001", "000002"])
    elig = pd.Series([True, True])
    opportunity = pd.Series([True, False])
    excess = pd.Series([0.05, -0.05])
    assert _verdict(opportunity, excess, elig, codes, {"000002"}, False) == "CORRECT"


def test_verdict_correct_threshold_is_minus_2pp_not_plus():
    """excess=-0.01 是负的,但没有 ≤-2pp —— 正确阈值下应是 NEUTRAL;若 `_CORRECT_THRESH`
    符号被错写成 +0.02,`-0.01 <= 0.02` 恒真会误判 CORRECT。"""
    codes = pd.Series(["000001"])
    elig = pd.Series([True])
    opportunity = pd.Series([False])
    v = _verdict(opportunity, pd.Series([-0.01]), elig, codes, {"000001"}, False)
    assert v == "NEUTRAL"


def test_verdict_correct_threshold_boundary_inclusive():
    """恰好等于 -2pp(闭区间边界,`<=`)应该算达标 → CORRECT。"""
    codes = pd.Series(["000001"])
    elig = pd.Series([True])
    opportunity = pd.Series([False])
    v = _verdict(opportunity, pd.Series([-0.02]), elig, codes, {"000001"}, False)
    assert v == "CORRECT"


def test_bool_col_default_when_column_missing():
    df = pd.DataFrame({"code": ["000001"]})
    s = _bool_col(df, "buyable", True)
    assert bool(s.iloc[0]) is True


# ───────────────────────── 端到端:analyze() 读合成 scan_root + 湖 ─────────────────────────
# 两日合成数据,数字与模块 `_selftest()` 的手算完全一致(交叉验证 IO 层没有在"读文件→拼表"
# 这一步把纯函数已经验证过的数学算错):
#   day1(2026-01-05,D+1=01-06,D+2=01-07):000001..000005
#   day2(2026-01-08,D+1=01-09,D+2=01-10):000001,000002,000003,000006,000007


_DAY1 = {
    "000001": {"fwd_2_oc": 0.05, "gap": 0.03, "bought": True},
    "000002": {"fwd_2_oc": 0.09, "gap": -0.02, "bought": False},
    "000003": {"fwd_2_oc": 0.01, "gap": 0.01, "bought": False},
    "000004": {"fwd_2_oc": -0.03, "gap": 0.06, "bought": False},
    "000005": {"fwd_2_oc": 0.02, "gap": -0.01, "bought": False},
}
_DAY2 = {
    "000001": {"fwd_2_oc": 0.02, "gap": 0.05, "bought": True},
    "000002": {"fwd_2_oc": 0.03, "gap": -0.02, "bought": False},
    "000003": {"fwd_2_oc": -0.01, "gap": 0.03, "bought": False},
    "000006": {"fwd_2_oc": 0.06, "gap": -0.04, "bought": False},
    "000007": {"fwd_2_oc": 0.00, "gap": 0.08, "bought": False},
}


def _write_lake_window(lake: Path, d0: str, d1: str, d2: str, day_spec: dict) -> None:
    """D+1 收盘价统一取 10.00(不封板:high=10.2,pct_chg=2.0);D+2 开盘价 = 10*(1+gap),
    这样 gap_frame 现算出的 gap_c1_o2 精确等于 day_spec 里给定的目标值。"""
    _lake_day(lake, d0, [{"ts_code": f"{c}.SZ", "open": 9.0, "high": 9.5, "low": 8.9,
                         "close": 9.2, "pct_chg": 1.0} for c in day_spec])
    _lake_day(lake, d1, [{"ts_code": f"{c}.SZ", "open": 9.8, "high": 10.2, "low": 9.7,
                         "close": 10.0, "pct_chg": 2.0} for c in day_spec])
    _lake_day(lake, d2, [{"ts_code": f"{c}.SZ", "open": round(10.0 * (1 + v["gap"]), 6),
                         "high": round(10.0 * (1 + v["gap"]) + 0.1, 6),
                         "low": round(10.0 * (1 + v["gap"]) - 0.1, 6),
                         "close": round(10.0 * (1 + v["gap"]), 6),
                         "pct_chg": v["gap"] * 100} for c, v in day_spec.items()])


def _write_scan_day(scan_root: Path, date: str, day_spec: dict,
                    channels: list[tuple[str, str]], finalists: set[str]) -> None:
    d = scan_root / date
    (d / "retro").mkdir(parents=True)
    attr = pd.DataFrame([{"code": c, "fwd_2_oc": v["fwd_2_oc"], "buyable": True,
                          "bought": v["bought"]} for c, v in day_spec.items()])
    attr.to_csv(d / "retro" / "attribution.csv", index=False)
    ch = pd.DataFrame([{"channel": ch_name, "code": code} for ch_name, code in channels])
    ch.to_csv(d / "L1_channels.csv", index=False)
    judged = pd.DataFrame([{"code": c, "finalist": c in finalists} for c in day_spec])
    judged.to_csv(d / "L3_judged_full.csv", index=False)


def _build_two_day_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    lake, scan_root = tmp_path / "lake", tmp_path / "scan"
    _write_lake_window(lake, "20260105", "20260106", "20260107", _DAY1)
    _write_lake_window(lake, "20260108", "20260109", "20260110", _DAY2)
    _write_scan_day(scan_root, "2026-01-05", _DAY1,
                    [("chan_a", "000001"), ("chan_a", "000002"),
                     ("chan_b", "000001"), ("chan_b", "000003")],
                    finalists={"000001", "000002"})
    _write_scan_day(scan_root, "2026-01-08", _DAY2,
                    [("chan_a", "000001"), ("chan_a", "000006"),
                     ("chan_b", "000001"), ("chan_b", "000007")],
                    finalists={"000001", "000006"})
    shadow = tmp_path / "shadow_buys.csv"
    pd.DataFrame([
        {"date": "2026-01-05", "code": "000002"}, {"date": "2026-01-05", "code": "000003"},
        {"date": "2026-01-08", "code": "000002"}, {"date": "2026-01-08", "code": "000006"},
    ]).to_csv(shadow, index=False)
    return lake, scan_root, shadow


def test_analyze_end_to_end_matches_hand_computed_selftest_numbers(tmp_path):
    lake, scan_root, shadow = _build_two_day_fixture(tmp_path)
    result = analyze(days=60, scan_root=scan_root, lake=lake, shadow_path=shadow)
    assert result["dates"] == ["2026-01-05", "2026-01-08"]

    gate = result["gate"]
    assert abs(gate["agg"]["real_oc"]["value"] - 0.035) < 1e-6
    assert abs(gate["agg"]["shadow_oc"]["value"] - 0.0475) < 1e-6
    # 两日都有真实买单(000001) → 配对聚合与非配对聚合数值相同(配对没剔掉任何一天),
    # 只是现在读的是"配对"字段(Important-2 review fix)。
    assert gate["n_paired_days"] == 2
    assert abs(gate["gate_value_paired_oc"] - (-0.0125)) < 1e-6
    assert abs(gate["gate_value_paired_gap"] - 0.0575) < 1e-6
    assert gate["gate_value_paired_oc"] < 0 < gate["gate_value_paired_gap"]   # 门的价值两尺符号相反

    compare = result["channels"]["compare"].set_index("channel")
    assert abs(compare.loc["chan_a", "unique_excess_oc"] - 0.055) < 1e-6
    assert abs(compare.loc["chan_a", "unique_excess_gap"] - (-0.05)) < 1e-6
    assert abs(compare.loc["chan_b", "unique_excess_oc"] - (-0.015)) < 1e-6
    assert abs(compare.loc["chan_b", "unique_excess_gap"] - 0.025) < 1e-6
    assert int(compare.loc["chan_a", "rank_oc"]) == 1 and int(compare.loc["chan_a", "rank_gap"]) == 2
    assert int(compare.loc["chan_b", "rank_oc"]) == 2 and int(compare.loc["chan_b", "rank_gap"]) == 1
    assert bool(compare.loc["chan_a", "sign_flip"]) is True

    l3 = result["l3"]
    assert abs(l3["agg_oc"]["edge"] - (0.07 + (0.04 - (0.03 - 0.01 + 0.00) / 3)) / 2) < 1e-6
    assert l3["agg_oc"]["edge"] > 0 > l3["agg_gap"]["edge"]


def test_render_has_five_sections_and_retirement_list(tmp_path):
    lake, scan_root, shadow = _build_two_day_fixture(tmp_path)
    result = analyze(days=60, scan_root=scan_root, lake=lake, shadow_path=shadow)
    body = render(result)
    for header in ("## ① 门的价值", "## ② 九路召回", "## ③ L3 真选 edge",
                  "## ④ 弃权日裁决翻转", "## ⑤ 作废/待重验清单"):
        assert header in body
    assert "value" in body and "momentum" in body and "0买日" in body
    assert "chan_a" in body and "chan_b" in body


def test_retirement_notes_flags_sign_flip_channel_named_value():
    """把①的合成 channel 改名成 "value",验证退役清单真的点名它并标"作废"。"""
    compare = pd.DataFrame([
        {"channel": "value", "n_days": 2, "unique_excess_oc": 0.055, "rank_oc": 1,
         "unique_excess_gap": -0.05, "rank_gap": 2, "rank_delta": 1, "sign_flip": True},
    ])
    result = {
        "channels": {"compare": compare},
        "abstention": {"n_days": 0, "flipped_dates": [], "n_reproduced": 0, "n_checked_reproduction": 0},
        "l3": {"n_days": 0, "agg_oc": {"edge": None}, "agg_gap": {"edge": None}},
    }
    notes = _retirement_notes(result)
    joined = "\n".join(notes)
    assert "value" in joined and ("作废" in joined or "待重验" in joined)
    assert "momentum 相位条件性" in joined


def test_retirement_notes_abstention_flip_lists_dates():
    result = {
        "channels": {"compare": pd.DataFrame(columns=["channel", "sign_flip"])},
        "abstention": {"n_days": 3, "flipped_dates": ["2026-01-05"],
                       "n_reproduced": 3, "n_checked_reproduction": 3},
        "l3": {"n_days": 0, "agg_oc": {"edge": None}, "agg_gap": {"edge": None}},
    }
    joined = "\n".join(_retirement_notes(result))
    assert "2026-01-05" in joined and "作废" in joined


def test_retirement_notes_l3_edge_collapses_to_near_zero():
    """finalist-vs-bench edge 从有意义的正值坍缩到 0.2pp 内 → 用"坍缩"措辞,不夸大成"符号翻转"。"""
    result = {
        "channels": {"compare": pd.DataFrame(columns=["channel", "sign_flip"])},
        "abstention": {"n_days": 0, "flipped_dates": [], "n_reproduced": 0, "n_checked_reproduction": 0},
        "l3": {"n_days": 14, "agg_oc": {"edge": 0.0159, "n_pinned_excluded_total": 49},
              "agg_gap": {"edge": -0.0001, "n_pinned_excluded_total": 49}},
    }
    joined = "\n".join(_retirement_notes(result))
    assert "L3 真选 edge" in joined and "坍缩" in joined
    assert "保送" in joined and "49" in joined   # Important-1 review fix:披露剔除的保送票数


def test_retirement_notes_l3_edge_consistent_sign_no_warning():
    result = {
        "channels": {"compare": pd.DataFrame(columns=["channel", "sign_flip"])},
        "abstention": {"n_days": 0, "flipped_dates": [], "n_reproduced": 0, "n_checked_reproduction": 0},
        "l3": {"n_days": 14, "agg_oc": {"edge": 0.02, "n_pinned_excluded_total": 0},
              "agg_gap": {"edge": 0.015, "n_pinned_excluded_total": 0}},
    }
    joined = "\n".join(_retirement_notes(result))
    assert "符号一致" in joined and "作废" not in joined


# ───────────────────────── ④ 端到端:rejection_attribution + abstention_verdict ─────────────────────────


def test_abstention_flip_end_to_end_false_to_neutral(tmp_path):
    """day1 universe(000001..5)重用:oc 侧存量裁决 FALSE(000002 在 shadow 且 opportunity),
    gap 侧重算 NEUTRAL(shadow 集合里 000003 的 excess_gap=0.00 不满足全 ≤-2pp)—— 手算见
    模块 docstring 的 `abstention_flip` 段。"""
    import json as _json

    lake, scan_root, shadow = _build_two_day_fixture(tmp_path)
    d = scan_root / "2026-01-05"
    market_oc = 0.02   # median(0.05,0.09,0.01,-0.03,0.02)
    rows = []
    for c, v in _DAY1.items():
        excess = round(v["fwd_2_oc"] - market_oc, 6)
        rows.append({"code": c, "final_action": "ABSTAIN", "buyable": True, "tradable": True,
                    "excess_2": excess, "opportunity": excess >= 0.02})
    pd.DataFrame(rows).to_csv(d / "retro" / "rejection_attribution.csv", index=False)
    (d / "retro" / "abstention_verdict.json").write_text(
        _json.dumps({"status_v2": "FALSE", "data_quality": "COMPLETE"}), encoding="utf-8")

    result = analyze(days=60, scan_root=scan_root, lake=lake, shadow_path=shadow)
    abst = result["abstention"]
    assert abst["n_days"] == 1
    row = abst["table"].iloc[0]
    assert row["status_oc"] == "FALSE"
    assert row["status_oc_reproduced"] == "FALSE"     # 自检:重实现精确复现存量裁决
    assert row["status_gap"] == "NEUTRAL"
    assert bool(row["flipped"]) is True
    assert abst["flipped_dates"] == ["2026-01-05"]


def test_abstention_flip_oc_side_honors_mature_not_phantom_tradable(tmp_path):
    """Minor review fix:oc 侧 eligible 应该是 `buyable & mature`,不是 `buyable & tradable`
    (`rejection_attribution.csv` 根本没有 `tradable` 列,该查找此前恒回退默认值 True,
    等于从未真正过滤)。构造:shadow={A,B};A 数据不成熟(`mature=False`)且 excess=+0.10
    (若被误当"已知"会破坏"shadow 集合全部 ≤-2pp"这个 CORRECT 判据);B 成熟且 excess=-0.05
    (达标)。修复前(误读 mature 为恒真)→ A 被错误纳入 scope → NEUTRAL。修复后(正确剔除
    未成熟的 A)→ scope 只剩 B → CORRECT。"""
    import json as _json

    lake, scan_root, shadow = _build_two_day_fixture(tmp_path)
    d = scan_root / "2026-01-05"
    rows = [
        {"code": "000002", "final_action": "ABSTAIN", "buyable": True, "mature": False,
         "excess_2": 0.10, "opportunity": False},
        {"code": "000003", "final_action": "ABSTAIN", "buyable": True, "mature": True,
         "excess_2": -0.05, "opportunity": False},
        {"code": "000001", "final_action": "ABSTAIN", "buyable": True, "mature": True,
         "excess_2": -0.03, "opportunity": False},
    ]
    pd.DataFrame(rows).to_csv(d / "retro" / "rejection_attribution.csv", index=False)
    (d / "retro" / "abstention_verdict.json").write_text(
        _json.dumps({"status_v2": "CORRECT", "data_quality": "COMPLETE"}), encoding="utf-8")
    # shadow_buys.csv 里 2026-01-05 已固定是 {000002, 000003}(_build_two_day_fixture)

    result = analyze(days=60, scan_root=scan_root, lake=lake, shadow_path=shadow)
    row = result["abstention"]["table"].iloc[0]
    assert row["status_oc_reproduced"] == "CORRECT", (
        "elig_oc 应正确剔除 mature=False 的 000002,只剩 000003(-0.05≤-2pp)进 scope —— "
        f"若又变回读 mature=False 视为已知/不存在的 tradable 列,000002(excess=+0.10)会"
        f"混进来破坏 all≤-2pp,得到 NEUTRAL 而不是 CORRECT(got {row['status_oc_reproduced']})")


def test_abstention_flip_skips_not_abstained_day(tmp_path):
    """当日有 BUY → 与 abstention_ledger.roll() 同样跳过,不进翻转统计。"""
    import json as _json

    lake, scan_root, shadow = _build_two_day_fixture(tmp_path)
    d = scan_root / "2026-01-05"
    rows = [{"code": c, "final_action": "BUY" if c == "000001" else "ABSTAIN",
            "buyable": True, "tradable": True, "excess_2": 0.0, "opportunity": False}
           for c in _DAY1]
    pd.DataFrame(rows).to_csv(d / "retro" / "rejection_attribution.csv", index=False)
    (d / "retro" / "abstention_verdict.json").write_text(
        _json.dumps({"status_v2": None, "data_quality": "COMPLETE"}), encoding="utf-8")
    result = analyze(days=60, scan_root=scan_root, lake=lake, shadow_path=shadow)
    assert result["abstention"]["n_days"] == 0


# ───────────────────────── main() CLI ─────────────────────────


def test_main_selftest_flag_returns_zero():
    assert main(["--selftest"]) == 0


def test_selftest_function_returns_zero():
    assert _selftest() == 0


def test_main_no_mode_prints_help_and_returns_1(capsys):
    rc = main([])
    assert rc == 1


def test_main_run_end_to_end_writes_report(tmp_path, monkeypatch):
    lake, scan_root, shadow = _build_two_day_fixture(tmp_path)
    outp = tmp_path / "out" / "report.md"
    rc = main(["run", "--scan-root", str(scan_root), "--lake", str(lake),
              "--shadow-path", str(shadow), "--out", str(outp)])
    assert rc == 0
    assert outp.exists()
    body = outp.read_text(encoding="utf-8")
    assert "两尺对照报告" in body
    assert "2026-01-05" in body and "2026-01-08" in body
