"""paper_nav 事件组合模拟:固定 10% 槽/持 10 日/次日开盘进出;三线渲染。合成,无网络。

spec: 2026-07-05 wave §WS-A1。规则零判断可复现;信号日非交易日(06-19 孤儿键)跳过。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.paper_nav import market_nav_from_returns, render, simulate

_DAYS = ["20260701", "20260702", "20260703"]


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
