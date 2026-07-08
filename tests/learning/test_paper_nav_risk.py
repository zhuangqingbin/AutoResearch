"""X3·评测卫生:paper NAV 风险调整指标(MDD/Sortino)+ buy&hold(市场等权)基线对照。

StockBench 教训:多数 LLM agent 跑不赢 buy&hold → 成绩单必须日日直面风险调整口径,而非只看总收益。
纯数学(NAV 序列 → 指标),零新数据。

spec: docs/specs/2026-07-07-memory-astrategy-optimization-design.md §X3
"""
from __future__ import annotations

import math

import pandas as pd

from autoresearch.learning.paper_nav import risk_block, risk_metrics


def test_risk_metrics_mdd_and_total():
    nav = pd.Series([1.0, 1.2, 0.9, 1.0], index=["d1", "d2", "d3", "d4"])
    m = risk_metrics(nav)
    assert abs(m["total"] - 0.0) < 1e-9           # 1.0 → 1.0
    assert abs(m["mdd"] - (-0.25)) < 1e-9          # 峰 1.2 → 谷 0.9 = −25%


def test_risk_metrics_sortino_no_downside_is_inf():
    assert risk_metrics(pd.Series([1.0, 1.1, 1.2, 1.3]))["sortino"] == float("inf")


def test_risk_metrics_sortino_sign_matches_drift():
    assert risk_metrics(pd.Series([1.0, 1.01, 0.995, 1.02, 1.03]))["sortino"] > 0
    assert risk_metrics(pd.Series([1.0, 0.99, 1.005, 0.98, 0.97]))["sortino"] < 0


def test_risk_metrics_short_series_nan():
    m = risk_metrics(pd.Series([1.0]))
    assert math.isnan(m["total"]) and math.isnan(m["mdd"])


def test_risk_block_names_buyhold_and_three_lines():
    idx = ["d1", "d2", "d3"]
    real = pd.Series([1.0, 1.03, 1.05], index=idx)
    shadow = pd.Series([1.0, 0.98, 0.96], index=idx)
    mkt = pd.Series([1.0, 1.01, 1.02], index=idx)
    md = "\n".join(risk_block(real, shadow, mkt))
    assert "buy&hold" in md.lower() or "买入持有" in md
    assert "最大回撤" in md and "Sortino" in md
    assert "真实" in md and "影子" in md and "市场" in md
