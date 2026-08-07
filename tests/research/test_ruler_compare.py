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
    _bool_col,
    _retirement_notes,
    _selftest,
    _verdict,
    analyze,
    channel_day_stats,
    day_universe,
    gap_frame,
    gate_day_stats,
    l3_day_stats,
    lake_days,
    load_shadow_buys,
    main,
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


def test_verdict_empty_shadow_returns_none():
    codes = pd.Series(["000001"])
    s = pd.Series([True])
    assert _verdict(pd.Series([True]), pd.Series([-0.05]), s, codes, set(), False) is None


def test_verdict_degraded_forces_neutral_even_if_would_be_correct():
    codes = pd.Series(["000001"])
    elig = pd.Series([True])
    v = _verdict(pd.Series([False]), pd.Series([-0.05]), elig, codes, {"000001"}, True)
    assert v == "NEUTRAL"


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
    assert abs(gate["gate_value_oc"] - (-0.0125)) < 1e-6
    assert abs(gate["gate_value_gap"] - 0.0575) < 1e-6
    assert gate["gate_value_oc"] < 0 < gate["gate_value_gap"]           # 门的价值两尺符号相反

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
        "l3": {"n_days": 14, "agg_oc": {"edge": 0.0159}, "agg_gap": {"edge": -0.0001}},
    }
    joined = "\n".join(_retirement_notes(result))
    assert "L3 真选 edge" in joined and "坍缩" in joined


def test_retirement_notes_l3_edge_consistent_sign_no_warning():
    result = {
        "channels": {"compare": pd.DataFrame(columns=["channel", "sign_flip"])},
        "abstention": {"n_days": 0, "flipped_dates": [], "n_reproduced": 0, "n_checked_reproduction": 0},
        "l3": {"n_days": 14, "agg_oc": {"edge": 0.02}, "agg_gap": {"edge": 0.015}},
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
