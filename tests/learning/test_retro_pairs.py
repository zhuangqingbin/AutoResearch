"""M1·同日配对蒸馏:build_retro_pairs 构造 ExpeL 式 fail/success 对(控制变量=同日)。

fail = 评级高(bought,或 0 买日取当日最高评级档)但 T+5 跌;
success = 同日被门拦/漏召回(bucket_5 missed_*)但 T+5 涨(winner_5)。
同 industry 最近邻优先,无则放宽到全局;输出带因子差(喂 Claude 蒸馏,走 M2 adjudicate 落库)。

spec: docs/specs/2026-07-07-memory-astrategy-optimization-design.md §M1
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.learning.retro import build_retro_pairs


def _row(code, name, industry, rating, fwd5, winner_5=False, bucket_5="other",
         composite=0.0, mom=0.0, main=0.0, wr=50.0, pct=0.0):
    return dict(code=code, name=name, industry=industry, rating=rating, fwd_5_oc=fwd5,
                winner_5=winner_5, bucket_5=bucket_5, composite=composite,
                score_momentum=mom, main_net_ratio=main, winner_rate=wr, pct_60d=pct)


def test_build_pairs_matches_same_industry():
    attr = pd.DataFrame([
        _row("000001", "买错", "半导体", "Overweight", -0.06),               # fail(bought+跌)
        _row("000002", "漏赢", "半导体", "Underweight", 0.15, winner_5=True, bucket_5="missed_l1"),  # success 同业
        _row("000003", "漏赢2", "医药", "Sell", 0.20, winner_5=True, bucket_5="missed_l0"),
    ])
    pairs = build_retro_pairs(attr)
    assert len(pairs) >= 1
    r = pairs.iloc[0]
    assert r["fail_code"] == "000001" and r["win_code"] == "000002"          # 同业最近邻优先
    assert r["matched_on"] == "industry"


def test_build_pairs_zero_buy_day_uses_top_rating_present():
    # 0 买日:无 Overweight/Buy → 取当日最高档(Hold)里下跌的当 fail 侧代理
    attr = pd.DataFrame([
        _row("000010", "持有跌", "电子", "Hold", -0.05),
        _row("000011", "低配", "电子", "Underweight", -0.02),                # 非最高档,不当 fail
        _row("000012", "漏赢", "电子", "Underweight", 0.12, winner_5=True, bucket_5="missed_l1"),
    ])
    pairs = build_retro_pairs(attr)
    assert len(pairs) == 1
    assert pairs.iloc[0]["fail_code"] == "000010"                            # Hold 档 fail 代理
    assert pairs.iloc[0]["win_code"] == "000012"


def test_build_pairs_empty_when_fwd5_immature():
    attr = pd.DataFrame([
        _row("000001", "a", "半导体", "Overweight", np.nan),
        _row("000002", "b", "半导体", "Underweight", np.nan, winner_5=False, bucket_5="missed_l1"),
    ])
    assert build_retro_pairs(attr).empty                                     # fwd_5 未成熟 → 优雅空


def test_build_pairs_factor_diff_is_fail_minus_win():
    attr = pd.DataFrame([
        _row("000001", "买错", "半导体", "Overweight", -0.06, composite=0.8, mom=0.5, wr=92.0),
        _row("000002", "漏赢", "半导体", "Underweight", 0.15, winner_5=True, bucket_5="missed_l1",
             composite=0.3, mom=-0.1, wr=20.0),
    ])
    r = build_retro_pairs(attr).iloc[0]
    assert abs(r["d_composite"] - 0.5) < 1e-9                                # 0.8 − 0.3
    assert abs(r["d_momentum"] - 0.6) < 1e-9                                 # 0.5 − (−0.1)
    assert abs(r["d_winner_rate"] - 72.0) < 1e-9                             # 92 − 20(fail 获利盘更满)


def test_build_pairs_fallback_global_when_no_same_industry():
    attr = pd.DataFrame([
        _row("000001", "买错", "半导体", "Overweight", -0.06),
        _row("000002", "漏赢", "医药", "Underweight", 0.15, winner_5=True, bucket_5="missed_l0"),
    ])
    pairs = build_retro_pairs(attr)
    assert len(pairs) == 1
    assert pairs.iloc[0]["matched_on"] == "global"                          # 跨业放宽仍配对


def test_build_pairs_excludes_unrated_universe_from_fail_side():
    # 未被 L4 评级的 universe 票(rating NaN)即便暴跌,也非"判断失败"——不该进 fail 侧(真数据 06-26 坑)
    attr = pd.DataFrame([
        _row("000001", "持有跌", "半导体", "Hold", -0.05),                    # 真评级 → fail
        _row("000099", "未评级暴跌", "半导体", np.nan, -0.40),                # universe 噪声 → 排除
        _row("000002", "漏赢", "半导体", "Underweight", 0.15, winner_5=True, bucket_5="missed_l1"),
    ])
    pairs = build_retro_pairs(attr)
    assert set(pairs["fail_code"]) == {"000001"}                            # 仅真评级进 fail 侧


def test_build_pairs_empty_when_no_success_side():
    attr = pd.DataFrame([
        _row("000001", "买错", "半导体", "Overweight", -0.06),
        _row("000009", "也跌", "医药", "Hold", -0.03),                        # 无 missed winner
    ])
    assert build_retro_pairs(attr).empty
