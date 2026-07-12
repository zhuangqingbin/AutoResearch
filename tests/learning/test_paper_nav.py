"""paper_nav 事件组合模拟:固定 10% 槽/持 10 日/次日开盘进出;三线渲染。合成,无网络。

spec: 2026-07-05 wave §WS-A1。规则零判断可复现;信号日非交易日(06-19 孤儿键)跳过。
"""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from autoresearch.learning.paper_nav import (
    market_nav_from_returns,
    render,
    shadow_signals,
    simulate,
    summary_line,
)

_DAYS = ["20260701", "20260702", "20260703"]


def test_simulate_default_hold_is_2():
    assert inspect.signature(simulate).parameters["hold"].default == 2


def test_simulate_hold2_exits_before_hold10():
    # 同一信号/价格下 hold=2 应比 hold=10 更早平仓:hold=2 卖飞后现金不再随价格波动,
    # hold=10 仍持仓吃到后续上涨——校验 exit_i=entry_i+hold 语义未被改坏。
    days = [f"202607{d:02d}" for d in range(1, 13)]
    code = "000001"
    prices = {
        (days[1], code): (10.0, 10.0),   # 次日开盘建仓
        (days[3], code): (12.0, 12.0),   # hold=2 平仓日(entry_i=1,exit_i=3)
        (days[5], code): (None, 15.0),   # hold=10 仍持仓,吃到收盘上涨
        (days[11], code): (20.0, 20.0),  # hold=10 平仓日(exit_i=11)
    }
    signals = [{"date": "2026-07-01", "code": code}]
    nav2, _ = simulate(signals, prices, days, hold=2)
    nav10, _ = simulate(signals, prices, days, hold=10)
    assert nav2[days[5]] == nav2[days[3]]      # hold=2 已平仓,day3 之后走平
    assert nav10[days[5]] > nav2[days[5]]      # hold=10 仍持仓,吃到 day5 上涨


def test_simulate_one_signal_math():
    # 信号 07-01 → 07-02 开盘 10 建仓(10% 槽) → hold=1 → 07-03 开盘 11 平仓 = 槽赚 10% = NAV +1%
    prices = {("20260702", "000001"): (10.0, 11.0), ("20260703", "000001"): (11.0, 12.0)}
    nav, skipped = simulate([{"date": "2026-07-01", "code": "000001"}], prices, _DAYS, hold=1)
    assert abs(nav.iloc[0] - 1.0) < 1e-9
    assert abs(nav.iloc[1] - 1.01) < 1e-9          # 收盘 11 估值:0.9 + 0.01*11
    assert abs(nav.iloc[2] - 1.01) < 1e-9          # 开盘 11 平仓落袋
    assert skipped == []


def test_simulate_orphan_and_missing_price():
    prices = {("20260702", "000001"): (10.0, 10.0)}
    nav, skipped = simulate([{"date": "2026-06-19", "code": "000001"},      # 非交易日 → 跳过
                             {"date": "2026-07-02", "code": "000009"}],     # 入场日无价 → 跳过
                            prices, _DAYS, hold=1)
    assert (nav == 1.0).all()
    assert len(skipped) == 2 and "孤儿" in skipped[0]


def test_market_nav_and_render():
    mkt = market_nav_from_returns([0.01, -0.02, 0.0], _DAYS)
    assert abs(mkt.iloc[1] - 1.01 * 0.98) < 1e-9
    flat = pd.Series([1.0] * 3, index=_DAYS)
    text = "\n".join(render(_DAYS, flat, flat, mkt, n_real=1, n_shadow=3, skipped=[]))
    assert "真实" in text and "影子" in text and "市场" in text and "20260703" in text


# ───────────────────────── W1·S3 sizer:sized 轨(presence-gated) ─────────────────────────


def test_simulate_uses_per_signal_weight_when_present():
    # 20% 槽(weight)× 10% 涨幅 = NAV +2%,对照默认 10% 槽的 +1%(见 test_simulate_one_signal_math)。
    prices = {("20260702", "000001"): (10.0, 11.0), ("20260703", "000001"): (11.0, 12.0)}
    nav, skipped = simulate([{"date": "2026-07-01", "code": "000001", "weight": 0.20}],
                            prices, _DAYS, hold=1)
    assert abs(nav.iloc[0] - 1.0) < 1e-9
    assert abs(nav.iloc[1] - 1.02) < 1e-9
    assert skipped == []


def test_simulate_missing_weight_key_falls_back_to_slot():
    # 不带 "weight" 键的信号行为与改动前逐字一致(parity)。
    prices = {("20260702", "000001"): (10.0, 11.0), ("20260703", "000001"): (11.0, 12.0)}
    nav, _ = simulate([{"date": "2026-07-01", "code": "000001"}], prices, _DAYS, hold=1)
    assert abs(nav.iloc[1] - 1.01) < 1e-9


def test_render_without_sized_is_byte_identical_to_before():
    # parity 硬锁:sized 缺省(None)→ 表头/行格式与改动前逐字相同。
    mkt = market_nav_from_returns([0.0, 0.0, 0.0], _DAYS)
    flat = pd.Series([1.0, 1.0, 1.0], index=_DAYS)
    out = render(_DAYS, flat, flat, mkt, n_real=1, n_shadow=3, skipped=[])
    assert out[2] == "| 日期 | 真实线 | 影子线 | 市场等权 |"
    assert out[3] == "|---|---|---|---|"
    assert "sized" not in "\n".join(out)


def test_render_with_sized_adds_column_and_footnote():
    mkt = market_nav_from_returns([0.0, 0.0, 0.0], _DAYS)
    flat = pd.Series([1.0, 1.0, 1.0], index=_DAYS)
    sized = pd.Series([1.0, 1.02, 1.03], index=_DAYS)
    out = render(_DAYS, flat, flat, mkt, n_real=1, n_shadow=3, skipped=[], sized=sized)
    text = "\n".join(out)
    assert "| 日期 | 真实线 | 影子线 | 影子(sized) | 市场等权 |" in text
    assert "+3.00%" in text                     # sized 最新读数入 per-day 行与截至一句
    assert "sizer.py" in text                    # 公式出处脚注


def test_summary_line_without_sized_is_unchanged():
    days = ["2026-07-01", "2026-07-02"]
    nav = pd.Series([1.0, 1.01], index=days)
    line = summary_line(days, nav, nav, nav, 1, 2)
    assert "sized" not in line


def test_summary_line_with_sized_appends_reading():
    days = ["2026-07-01", "2026-07-02"]
    nav = pd.Series([1.0, 1.01], index=days)
    sized = pd.Series([1.0, 1.05], index=days)
    line = summary_line(days, nav, nav, nav, 1, 2, sized=sized)
    assert "影子(sized)" in line and "+5.00%" in line


def test_shadow_signals_carries_conviction_for_sizer(tmp_path):
    csv = tmp_path / "shadow_buys.csv"
    csv.write_text("date,code,name,conviction,binding,close\n"
                   "2026-07-01,000001,N1,88.0,,10.0\n", encoding="utf-8")
    sig = shadow_signals(csv)
    assert sig == [{"date": "2026-07-01", "code": "000001", "conviction": 88.0}]


def test_sized_nav_diverges_from_equal_weight_with_real_sizer(tmp_path):
    """端到端小样本:shadow_signals() 的 conviction → sizer.size_shadow_signals() 权重 →
    simulate() 双轨滚动——sized 轨与等权轨在同一信号下应产生不同(可解释)的 NAV 路径。
    """
    from autoresearch.learning.sizer import size_shadow_signals

    lake = tmp_path / "lake"
    lake.mkdir()
    days20 = [f"202606{d:02d}" for d in range(1, 21)]           # 20 个历史交易日(供 vol 计算)
    pcts = [2.0 if i % 2 == 0 else -2.0 for i in range(20)]
    for i, d in enumerate(days20):
        pd.DataFrame([{"ts_code": "000001.SZ", "pct_chg": pcts[i], "amount": 50_000.0}]
                     ).to_parquet(lake / f"{d}.parquet")

    ss = [{"date": days20[-1], "code": "000001", "conviction": 100}]
    ss_sized = size_shadow_signals(ss, lake=lake)
    assert ss_sized[0]["weight"] != pytest.approx(0.10)          # 走公式,非等权回退

    days = [days20[-1], "20260701", "20260702"]
    prices = {("20260701", "000001"): (10.0, 11.0), ("20260702", "000001"): (11.0, 12.0)}
    equal_nav, _ = simulate(ss, prices, days, hold=1)
    sized_nav, _ = simulate(ss_sized, prices, days, hold=1)
    assert equal_nav.iloc[1] != pytest.approx(sized_nav.iloc[1])  # 双轨滚动确有分歧
