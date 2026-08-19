"""Phase 4 —— sector_ledger:行业中位成熟 × 报告纪律(n<10 ⚠)。NO network。

`record_calls`(brief 研判段方向记账)已随 2026-08-19 D6(⚖A6,用户裁定)退役——brief
研判段整段砍除,行业方向记账现只剩 `record_top3`(见 tests/scan/test_sector_top3.py)。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.sector_ledger import (
    backfill,
    mature_call,
    render_report,
)

DATE = "2026-07-03"


def test_mature_call_and_backfill():
    f0 = pd.DataFrame([{"code": "600001", "industry": "半导体", "close": 10.0},
                       {"code": "600002", "industry": "半导体", "close": 20.0},
                       {"code": "600011", "industry": "白酒", "close": 100.0}])
    f1 = pd.DataFrame([{"code": "600001", "industry": "半导体", "close": 11.0},   # +10%
                       {"code": "600002", "industry": "半导体", "close": 19.0}])  # -5%
    assert mature_call(f0, f1, "半导体") == 2.5                # 中位((+10)+(−5))/2
    assert mature_call(f0, f1, "白酒") is None                 # 交集空 → None
    assert mature_call(None, f1, "半导体") is None             # 缺帧 → None
    call = {"date": DATE, "industry": "半导体", "direction": "看多", "realized_pct": None}
    out = backfill(call, f0, f1)
    assert out["realized_pct"] == 2.5 and out["horizon"] == "fwd_2"   # 默认 horizon 主尺切 fwd_2
    assert call["realized_pct"] is None                        # 不改原 dict


def test_render_report_discipline():
    calls = [{"date": DATE, "industry": "半导体", "direction": "看多", "realized_pct": 2.5},
             {"date": DATE, "industry": "煤炭", "direction": "看空", "realized_pct": -1.0},
             {"date": DATE, "industry": "白酒", "direction": "中性", "realized_pct": None}]
    rpt = render_report(calls)
    assert "⚠ 已成熟样本 2 < 10" in rpt                       # 薄样本只记账不下结论
    assert "| 方向·来源 | n | 中位已实现% | 命中率 |" in rpt   # P7:聚合键改「方向·来源」(缺 source→brief)
    assert "| 看多·brief | 1 | +2.50 | 100% |" in rpt
    assert "| 看空·brief | 1 | -1.00 | 100% |" in rpt          # 看空且跌 = 命中(纯方向剥离后仍判对)
    assert "待成熟 1 条" in rpt
