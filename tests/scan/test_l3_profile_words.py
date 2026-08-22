"""`row_profile` 的 pf 词与 L3 表列(2026-08-22 批 B)。

立案:L3 表此前**没有** `pct_1d`/`dist_high_60`,却被要求替 L4 避开「涨停追高」——
2026-08-21 两只入围票双双在 L4 早停「涨停追高」(design §1.2)。纯函数,零 IO。
"""
from __future__ import annotations

from autoresearch.scan.l3.prompt import _L3_COLS, row_profile


def _words(r):
    return row_profile(r).split("·")


def test_table_exposes_pct_1d_and_dist_high_60():
    assert "pct_1d" in _L3_COLS and "dist_high_60" in _L3_COLS


def test_chase_word_appears_at_threshold():
    assert "今日大涨" in _words({"pct_60d": 20.0, "pct_1d": 9.5})
    assert "今日大涨" in _words({"pct_60d": 20.0, "pct_1d": 10.0})


def test_chase_word_absent_below_threshold_and_when_missing():
    assert "今日大涨" not in _words({"pct_60d": 20.0, "pct_1d": 9.49})
    assert "今日大涨" not in _words({"pct_60d": 20.0})
    assert "今日大涨" not in _words({"pct_60d": 20.0, "pct_1d": float("nan")})


def test_top_word_needs_both_near_high_and_positive_60d():
    assert "贴顶" in _words({"pct_60d": 21.4, "dist_high_60": -0.8})
    assert "贴顶" in _words({"pct_60d": 5.0, "dist_high_60": -2.0})       # 边界含等号
    assert "贴顶" not in _words({"pct_60d": 21.4, "dist_high_60": -2.01})  # 不够贴
    assert "贴顶" not in _words({"pct_60d": -12.0, "dist_high_60": -0.5})  # 60 日为负 → 不算贴顶
    assert "贴顶" not in _words({"pct_60d": 21.4})                          # 缺列不冤枉


def test_real_20260821_rows():
    """真实两只:002716(当日 +10.0、贴顶)与 300857(缺当日大涨、非贴顶)。"""
    assert "今日大涨" in _words({"pct_60d": 8.0, "pct_1d": 10.0, "dist_high_60": 0.0})
    assert "贴顶" in _words({"pct_60d": 8.0, "pct_1d": 10.0, "dist_high_60": 0.0})
    w = _words({"pct_60d": -1.9, "pct_1d": 0.0, "dist_high_60": -30.4})
    assert "今日大涨" not in w and "贴顶" not in w


def test_existing_words_unchanged():
    """新词不得挤掉旧词(位置/放量/主力/PE/筹码/RSI 六组)。"""
    w = _words({"pct_60d": 50.0, "vol_ratio": 3.0, "main_net_ratio": 0.2, "cmf_20": 0.1,
                "obv_mom_20": 0.3, "pe": 15.0, "winner_rate": 95.0, "rsi6": 85.0})
    assert w == ["高位", "放量", "主力+", "PE低", "满盈利⚠", "超买"]
