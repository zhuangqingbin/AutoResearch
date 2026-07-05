"""0买日报告瘦身(2026-07-04):观察单上移、OW三门失守直方图、market_view 剥 H1/免责、
L3 精排节去重(论点只在表)。fixture 模式同 test_market_view_embed。合成,无网络。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.assemble import build_summary

_D = "2026-07-03"
_F = "20260703_1200"


def _scan(tmp_path):
    d = tmp_path / "s"
    (d / "details").mkdir(parents=True)
    (d / "meta.json").write_text("{}", encoding="utf-8")
    pd.DataFrame([
        {"code": "300476", "name": "甲", "sector": "元件",
         "thesis": "AI 光模块需求超预期", "risk": "估值高", "catalyst": "Q2 财报"},
        {"code": "000686", "name": "乙", "sector": "证券Ⅱ",
         "thesis": "券商β", "risk": "winner80", "catalyst": "无"},
    ]).to_csv(d / "finalists.csv", index=False)
    (d / "details" / "300476.md").write_text(
        "# 决策卡\n**Rubric建议**: 净分-1 ｜ OW三门 主力真在✗·业绩真兑现△·估值不透支✗ → 压Hold\n"
        "**Rating**: Hold\n", encoding="utf-8")
    (d / "details" / "000686.md").write_text(
        "# 决策卡\n**Rubric建议**: 净分0 ｜ OW三门 主力真在✗·业绩真兑现✓·估值不透支△ → 压Hold\n"
        "**Rating**: Hold\n", encoding="utf-8")
    return d


def test_gate_histogram_line(tmp_path):
    md = build_summary(_scan(tmp_path), _D, "1200", _F)
    assert "OW三门失守分布" in md
    assert "主力真在✗ 2" in md and "估值不透支✗ 1" in md


def test_watchlist_moved_above_funnel_and_sector(tmp_path):
    d = _scan(tmp_path)
    (d / "market_view.md").write_text("## 定调\n震荡\n", encoding="utf-8")
    pd.DataFrame([{"code": "300124", "name": "汇川技术", "status": "临近",
                   "detail": "close_above:71.42=yes", "narrative": "红队观察",
                   "born": "2026-07-02", "expiry": "2026-09-30"}]).to_csv(
        d / "watchlist_status.csv", index=False)
    (d / "sector_briefs").mkdir()
    (d / "sector_briefs" / "元件.md").write_text(
        "## 地形段\n中位60日+69%\n\n## 研判段\n**行业方向**: 中性 — 过热背离\n", encoding="utf-8")
    md = build_summary(d, _D, "1200", _F)
    i_watch, i_sector, i_funnel = (md.find("👀 观察单日检"), md.find("🏭 行业研判"), md.find("## 1. 漏斗"))
    assert -1 < i_watch < i_sector, "观察单应上移到行业研判之前"
    assert i_watch < i_funnel, "观察单应上移到漏斗节之前"


def test_market_view_h1_and_disclaimer_stripped(tmp_path):
    d = _scan(tmp_path)
    (d / "market_view.md").write_text(
        "# 市场研判 · 2026-07-03\n\n## 1. 定调\n震荡哑铃\n\n## 6. 免责\n仅供研究,非投资建议。\n",
        encoding="utf-8")
    md = build_summary(d, _D, "1200", _F)
    assert "震荡哑铃" in md
    assert "# 市场研判 ·" not in md, "嵌入时应剥掉 market_view 自带 H1(报告已有 H1)"
    assert "## 6. 免责" not in md, "嵌入时应剥掉自带免责节(报告已有诚实局限)"


def test_l3_section_dedup(tmp_path):
    """精排节只留 风险/催化(论点已在 buy-list 表 L3 列,不重复两遍)。"""
    md = build_summary(_scan(tmp_path), _D, "1200", _F)
    sec = md[md.find("精排(L3)入选"):md.find("## 3. 投资建议")]
    assert "风险:估值高" in sec and "催化:Q2 财报" in sec
    assert "AI 光模块需求超预期" not in sec, "论点不应在精排节重复(表里已有)"
    assert "论点见" in md[md.find("精排(L3)入选"):][:80]
