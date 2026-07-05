"""Phase 4 —— sector_ledger:方向记账(幂等)× 行业中位成熟 × 报告纪律(n<10 ⚠)。NO network。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning.sector_ledger import (
    backfill,
    mature_call,
    record_calls,
    render_report,
)

DATE = "2026-07-03"

BRIEF = """# 行业 brief — 半导体 @ 2026-07-03

## 地形段(喂 L3/L4 · 描述性)
- 景气读数:x

## 研判段(仅 L5)
**行业方向**: 看多 — 依据
"""


def _mk_briefs(tmp_path):
    d = tmp_path / DATE
    (d / "sector_briefs").mkdir(parents=True)
    (d / "sector_briefs" / "半导体.md").write_text(BRIEF, encoding="utf-8")
    (d / "sector_briefs" / "无方向.md").write_text("# x\n\n## 研判段(仅 L5)\n没写方向行\n",
                                                   encoding="utf-8")
    return d


def test_record_calls_idempotent(tmp_path):
    d = _mk_briefs(tmp_path)
    ledger = tmp_path / "sector_calls.jsonl"
    assert record_calls(d, DATE, path=ledger) == 1             # 无方向行的 brief 不记
    assert record_calls(d, DATE, path=ledger) == 0             # 幂等
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == {"date": DATE, "industry": "半导体", "direction": "看多",
                       "source": "brief", "realized_pct": None, "horizon": None}
    assert record_calls(tmp_path / "nope", DATE, path=ledger) == 0   # 无 briefs 目录 → 0


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
    assert out["realized_pct"] == 2.5 and out["horizon"] == "fwd_5"
    assert call["realized_pct"] is None                        # 不改原 dict


def test_render_report_discipline():
    calls = [{"date": DATE, "industry": "半导体", "direction": "看多", "realized_pct": 2.5},
             {"date": DATE, "industry": "煤炭", "direction": "看空", "realized_pct": -1.0},
             {"date": DATE, "industry": "白酒", "direction": "中性", "realized_pct": None}]
    rpt = render_report(calls)
    assert "⚠ 已成熟样本 2 < 10" in rpt                       # 薄样本只记账不下结论
    assert "| 看多 | 1 | +2.50 | 100% |" in rpt
    assert "| 看空 | 1 | -1.00 | 100% |" in rpt               # 看空且跌 = 命中
    assert "待成熟 1 条" in rpt
