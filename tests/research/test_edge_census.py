"""家族 × 尺 edge 普查(2026-08-22):合成湖 + 合成 staging,零网络。

覆盖:湖→pivot→前向收益接线 / 家族抽取(presence-gated、📌 剔除)/ 日统计与聚合 /
rank-IC / §0 判读四态 / 缺产物不伪造 / 渲染脚注。预注册见 docs/research/2026-08-22-edge-census.md §0。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from autoresearch.research import edge_census as ec

DAYS = ["20260801", "20260804", "20260805", "20260806", "20260807", "20260808", "20260811",
        "20260812", "20260813", "20260814", "20260815", "20260818", "20260819", "20260820"]
CODES = [f"{600000 + i:06d}" for i in range(80)]


def _lake(tmp_path, planted: dict[str, float] | None = None, seed=0):
    """合成湖:每日每票 open/high/low/close;`planted` 让某些票在 D+2 开盘相对 D+1 收盘多涨 x。"""
    rng = np.random.default_rng(seed)
    d = tmp_path / "lake" / "daily"
    d.mkdir(parents=True)
    px = {c: 10.0 + rng.uniform(0, 5) for c in CODES}
    for i, day in enumerate(DAYS):
        rows = []
        for c in CODES:
            base = px[c] * (1 + rng.normal(0, 0.01))
            o = base
            if planted and c in planted and i >= 2:
                o = base * (1 + planted[c])          # 抬开盘 → gap 为正
            cl = base * (1 + rng.normal(0, 0.005))
            rows.append({"ts_code": f"{c}.SZ", "trade_date": day, "open": o, "high": max(o, cl) * 1.01,
                         "low": min(o, cl) * 0.99, "close": cl, "pre_close": px[c],
                         "change": cl - px[c], "pct_chg": (cl / px[c] - 1) * 100, "vol": 1e5, "amount": 1e6})
            px[c] = cl
        pd.DataFrame(rows).to_parquet(d / f"{day}.parquet", index=False)
    return d


def _scan_day(root, date: str, *, finalists=(), pinned=(), bench=(), ratings=None,
              channels=None, e6=None, decisions=None):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    fin = [{"code": c, "name": c, "lane": "healthy", "conviction": 70} for c in finalists]
    fin += [{"code": c, "name": c, "lane": "pinned", "conviction": 50, "pinned_note": "持仓"} for c in pinned]
    if fin:
        pd.DataFrame(fin).to_csv(d / "finalists.csv", index=False)
    jd = [{"code": c, "lane": "healthy", "conviction": 70, "finalist": True} for c in finalists]
    jd += [{"code": c, "lane": "trend", "conviction": 60, "finalist": False} for c in bench]
    jd += [{"code": c, "lane": "trend", "conviction": 50, "finalist": False} for c in pinned]
    if jd:
        pd.DataFrame(jd).to_csv(d / "L3_judged_full.csv", index=False)
    if channels:
        pd.DataFrame([{"channel": ch, "code": c, "channel_rank": i + 1, "channel_score": 1.0}
                      for ch, cs in channels.items() for i, c in enumerate(cs)]).to_csv(d / "L1_channels.csv", index=False)
    if ratings is not None:
        (d / "_final_ratings.json").write_text(json.dumps(ratings), encoding="utf-8")
        (d / "details").mkdir(exist_ok=True)
    if e6:
        (d / "_relative_buy_decision.json").write_text(json.dumps({"date": date, **e6}), encoding="utf-8")
    return d


# ── 价格接线 ────────────────────────────────────────────────────────────────

def test_lake_pivots_and_forward_frame_main_ruler_is_c1_to_o2(tmp_path):
    lake = _lake(tmp_path)
    P = ec.lake_trade_days(lake)
    assert P == DAYS
    piv = ec.load_lake_pivots(P, lake)
    assert set(piv) == {"open", "high", "low", "close", "pct_chg", "amount"}
    fr = ec.forward_frame(piv, P, "20260805")
    c = CODES[0]
    want = piv["open"].loc[c, "20260807"] / piv["close"].loc[c, "20260806"] - 1
    assert np.isclose(fr.loc[c, "gap_c1_o2"], want)
    assert "buyable_c1" in fr.columns and "fwd_5_oc" in fr.columns


def test_forward_frame_none_when_day_not_in_lake(tmp_path):
    lake = _lake(tmp_path)
    P = ec.lake_trade_days(lake)
    piv = ec.load_lake_pivots(P, lake)
    assert ec.forward_frame(piv, P, "20260803") is None      # 周日/湖里没有
    assert ec.forward_frame({}, P, "20260805") is None


def test_forward_frame_clips_impossible_gap(tmp_path):
    lake = _lake(tmp_path)
    P = ec.lake_trade_days(lake)
    piv = ec.load_lake_pivots(P, lake)
    piv["open"].loc[CODES[3], "20260807"] = piv["close"].loc[CODES[3], "20260806"] * 3   # +200%,板制度下不可能
    fr = ec.forward_frame(piv, P, "20260805")
    assert np.isnan(fr.loc[CODES[3], "gap_c1_o2"]) and fr.attrs["n_clipped"] == 1


# ── 家族抽取 ────────────────────────────────────────────────────────────────

def test_families_presence_gated_and_pinned_excluded(tmp_path):
    d = _scan_day(tmp_path / "scan", "2026-08-05", finalists=["600001", "600002"], pinned=["600009"],
                  bench=["600003"], ratings={"600001": "Hold", "600002": "Overweight", "600009": "Sell"},
                  channels={"momentum": ["600001", "600009"], "lowturn": ["600003"]},
                  e6={"buys": [{"code": "600002", "rank": 1}],
                      "candidates": [{"code": "600002", "eligible": True, "rank": 1},
                                     {"code": "600001", "eligible": True, "rank": 2},
                                     {"code": "600003", "eligible": False, "rank": None}]})
    fam, ord_map, pin = ec.families_for_day(d)
    assert pin == {"600009"}
    assert fam["📌·保送"] == {"600009"}
    assert fam["L3·finalist"] == {"600001", "600002"}          # 保送不在 finalist 家族
    assert fam["L3·bench"] == {"600003"}
    assert fam["L1·momentum"] == {"600001", "600009"}          # L1 是召回集,保送留着(它本就可被召回)
    assert fam["L4·评级·Overweight"] == {"600002"} and "L4·评级·Sell" not in fam   # Sell 只有保送票 → 剔后为空不出族
    assert fam["L4·≥OW"] == {"600002"} and fam["L4·<OW"] == {"600001"}
    assert ord_map == {"600001": 3, "600002": 4}
    assert fam["E6·rank1"] == {"600002"} and fam["E6·eligible非rank1"] == {"600001"} and fam["E6·硬否决"] == {"600003"}
    assert "L2·全体" not in fam and "L4·早停" not in fam       # 缺产物 → 不出现(≠ 空集)


def test_families_e6_stale_date_ignored(tmp_path):
    d = _scan_day(tmp_path / "scan", "2026-08-05", finalists=["600001"],
                  e6={"buys": [{"code": "600001", "rank": 1}], "candidates": []})
    (d / "_relative_buy_decision.json").write_text(json.dumps({"date": "2026-08-04", "buys": [{"code": "600001"}]}),
                                                   encoding="utf-8")
    fam, _, _ = ec.families_for_day(d)
    assert not any(k.startswith("E6") for k in fam)


def test_families_empty_dir_is_empty_not_crash(tmp_path):
    d = tmp_path / "scan" / "2026-08-05"
    d.mkdir(parents=True)
    fam, ord_map, pin = ec.families_for_day(d)
    assert fam == {} and ord_map == {} and pin == set()


# ── 统计 ────────────────────────────────────────────────────────────────────

def _fr(n=200, seed=0):
    rng = np.random.default_rng(seed)
    codes = [f"{600000 + i:06d}" for i in range(n)]
    return pd.DataFrame({"gap_c1_o2": rng.normal(0, 0.02, n), "fwd_5_oc": rng.normal(0, 0.04, n),
                         "fwd_10_oc": rng.normal(0, 0.06, n), "buyable_c1": True, "buyable": True}, index=codes)


def test_daily_stats_excess_vs_median_and_mean():
    fr = _fr()
    grp = set(fr.index[:20])
    fr.loc[list(grp), "gap_c1_o2"] += 0.05
    st = ec.daily_stats(fr, grp, "gap_c1_o2")
    assert st["n"] == 20 and st["excess_med"] > 0.03 and st["excess_mean"] > 0.03
    assert ec.daily_stats(fr, {"999999"}, "gap_c1_o2") is None      # 族内无可算票
    assert ec.daily_stats(_fr(n=30), grp, "gap_c1_o2") is None      # 截面 <50


def test_daily_stats_respects_entry_tradable_flag():
    fr = _fr()
    grp = set(fr.index[:10])
    fr.loc[list(grp), "buyable_c1"] = False                           # T+1 封板买不进 → 不计
    assert ec.daily_stats(fr, grp, "gap_c1_o2") is None


def test_aggregate_t_and_pp():
    days = [{"n": 5, "mean": 0.01, "hit": 0.6, "excess_med": 0.01, "excess_mean": 0.008}] * 10
    agg = ec.aggregate(days)
    assert agg["n_days"] == 10 and np.isclose(agg["excess_med_pp"], 1.0) and np.isclose(agg["excess_mean_pp"], 0.8)
    assert np.isnan(agg["t"])                                         # 零方差 → t 不可定义,不伪造
    agg2 = ec.aggregate([dict(d, excess_med=0.01 + 0.001 * i) for i, d in enumerate(days)])
    assert agg2["t"] > 10


def test_rank_ic_sign_and_min_cards():
    fr = _fr()
    codes = list(fr.index[:10])
    ord_map = {c: (i % 5) + 1 for i, c in enumerate(codes)}
    fr.loc[codes, "gap_c1_o2"] = [ord_map[c] * 0.01 for c in codes]  # 评级越高 gap 越高
    assert ec.rank_ic_day(fr, ord_map) > 0.99
    assert ec.rank_ic_day(fr, {c: 3 for c in codes}) is None         # 只有一档
    assert ec.rank_ic_day(fr, dict(list(ord_map.items())[:3])) is None  # <5 张卡


@pytest.mark.parametrize("n,ex,t,want", [
    (30, 0.5, 2.5, "正证据"), (30, 0.5, 1.9, "未证"), (30, -0.5, -2.5, "显著负"),
    (30, -0.5, -1.0, "未证"), (10, 0.9, 5.0, "样本不足"), (30, 0.5, float("nan"), "未证")])
def test_verdict_four_states(n, ex, t, want):
    assert ec.verdict({"n_days": n, "excess_med_pp": ex, "t": t}) == want


# ── 端到端(合成湖 + 合成 staging)────────────────────────────────────────────

def test_run_census_end_to_end_finds_planted_family(tmp_path):
    planted = {c: 0.03 for c in CODES[:5]}                           # 5 只票 D+2 开盘系统性 +3%
    lake = _lake(tmp_path, planted=planted)
    scan = tmp_path / "scan"
    for day in ["2026-08-05", "2026-08-06", "2026-08-07", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]:
        _scan_day(scan, day, finalists=CODES[:5], bench=CODES[5:9],
                  ratings={**{c: "Overweight" for c in CODES[:5]}, **{c: "Hold" for c in CODES[5:9]}})
    table, ic, meta = ec.run_census(scan_root=scan, lake_daily=lake)
    assert meta["computable_days"] >= 5
    row = table[(table["family"] == "L3·finalist") & (table["ruler"] == "gap_c1_o2")].iloc[0]
    assert row["excess_med_pp"] > 2.0 and row["t"] > 2.0
    assert row["verdict"] == "样本不足"                              # <20 日:再显著也不判正证据
    bench = table[(table["family"] == "L3·bench") & (table["ruler"] == "gap_c1_o2")].iloc[0]
    assert abs(bench["excess_med_pp"]) < 1.0
    assert ic["n_days"] >= 5 and ic["ic_mean"] > 0.5                  # OW 卡 = 种了 +3% 的票


def test_run_census_empty_scan_root(tmp_path):
    table, ic, meta = ec.run_census(scan_root=tmp_path / "nope", lake_daily=_lake(tmp_path))
    assert table.empty and ic == {} and meta["scan_days"] == 0


def test_render_has_h0_line_and_fixed_footnote():
    table = pd.DataFrame([{"family": "L3·finalist", "layer": "L3", "ruler": "gap_c1_o2", "n_days": 25,
                           "n_med_per_day": 8.0, "mean_pp": 0.1, "excess_med_pp": 0.3, "excess_mean_pp": 0.2,
                           "t": 2.4, "hit_mean": 0.55, "verdict": "正证据"}])
    md = ec.render(table, {"n_days": 25, "ic_mean": 0.1, "t": 1.2, "hit": 0.6}, {"scan_days": 30, "computable_days": 25, "main_ruler": "gap_c1_o2"})
    assert "H0(无家族有正证据):被推翻" in md and "只观察" in md and "不显著 ≠ 有 alpha" in md
    md2 = ec.render(table.assign(verdict="未证"), {}, {"scan_days": 30, "computable_days": 25, "main_ruler": "gap_c1_o2"})
    assert "未被推翻" in md2
