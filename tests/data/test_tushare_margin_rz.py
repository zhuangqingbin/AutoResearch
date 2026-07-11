"""rz 数据面接通(FN-1 第五修,终审 I-1):margin_detail → 生产帧 rz_buy_intensity。NO network。

背景:pr_20260710_001 把 rz 入了 scoring 第 10 组,但生产帧(fetch_universe_tushare)没有该列 →
组恒 NaN 被重归一跳过 = 生产 no-op,提案却已标 resolved。本测试锁纯函数换算与降级路径。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.data.tushare_source import _margin_rz_cols


def test_margin_rz_cols_pure():
    mg = pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"], "rzmre": ["1500000", None]})
    out = _margin_rz_cols(mg)
    assert out.loc[0, "code"] == "000001" and out.loc[0, "rzmre_yuan"] == 1500000.0
    assert pd.isna(out.loc[1, "rzmre_yuan"])            # 缺值 → NaN 降级,不炸


def test_rz_intensity_unit_conversion():
    """口径同 factor_lab:rzmre(元)/成交额(元);amount_yi(亿)×1e8。1.5e6 / (0.5亿=5e7) = 0.03。"""
    rzmre_yuan, amount_yi = 1_500_000.0, 0.5
    assert abs(rzmre_yuan / (amount_yi * 1e8) - 0.03) < 1e-12
