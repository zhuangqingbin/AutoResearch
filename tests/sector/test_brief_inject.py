"""Phase 3 —— brief 两段契约抽取 + 三注入点(L3 地形/L4 简报/L5 两节)。NO network。

锚:sector_terrain 默认关 = 逐字 parity;L4 无 brief 回退 memo 行;L5 节 presence-gated。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md
from autoresearch.scan.agents.l4_card import compose_funnel_brief
from autoresearch.scan.assemble import _same_chain_block, _sector_view_section
from autoresearch.sector.brief import (
    brief_path,
    extract_terrain,
    extract_view,
    parse_direction,
    render_terrain_block,
)

DATE = "2026-07-03"

BRIEF = """# 行业 brief — 半导体 @ 2026-07-03

## 地形段(喂 L3/L4 · 描述性)
- 景气读数:成分 4 只 · 中位60日 -27.5%
- 估值地形:中位PE 60.0(P25 55.0 / P75 80.0)

## 研判段(仅 L5)
**行业方向**: 看多 — 健康数回升
- 景气位置:磨底
"""


def _mk_brief(scan_dir, industry="半导体", text=BRIEF):
    p = brief_path(scan_dir, industry)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ───────────────────────── 契约抽取 ─────────────────────────


def test_extract_two_sections_and_direction():
    terr, view = extract_terrain(BRIEF), extract_view(BRIEF)
    assert "景气读数" in terr and "行业方向" not in terr        # 地形段不含方向(三层同律)
    assert "看多" in view and "磨底" in view
    assert parse_direction(view) == "看多"
    assert parse_direction("无方向文本") is None


def test_render_terrain_block(tmp_path):
    d = tmp_path / DATE
    d.mkdir(parents=True)
    assert render_terrain_block("半导体", d) == ""              # 无 brief → ''(回退 memo 行)
    _mk_brief(d)
    block = render_terrain_block("半导体", d)
    assert block.startswith("### 🏭 行业地形 — 半导体")
    assert "rubric 三门" in block and "估值地形" in block


# ───────────────────────── L3:全行业地形行(默认关=parity) ─────────────────────────


def _row(code, comp, pct):
    return {"code": code, "name": f"N{code}", "industry": "半导体",
            "composite": comp, "pct_60d": pct}


def test_l3_table_sector_terrain_default_off_parity(tmp_path):
    d = tmp_path / DATE
    d.mkdir(parents=True)
    pd.DataFrame([_row("000001", 50, 10), _row("000002", 51, 11)]
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    off = l3_table_md(DATE, root=tmp_path)
    on_no_l1 = l3_table_md(DATE, root=tmp_path, sector_terrain=True)
    assert on_no_l1 == off                                     # 缺 L1_scored_full → 优雅跳过
    assert "全行业地形" not in off
    pd.DataFrame([{**_row("000001", 50, 10), "close": 10, "pe": 30.0,
                   "main_net_ratio": 0.01, "cmf_20": 0.1, "mktcap_yi": 100}]
                 ).to_csv(d / "L1_scored_full.csv", index=False)
    on = l3_table_md(DATE, root=tmp_path, sector_terrain=True)
    assert on.startswith("## 全行业地形")
    assert on.endswith(off)                                    # 地形只前置,表体逐字不变
    assert l3_table_md(DATE, root=tmp_path) == off             # 默认仍 off = parity


# ───────────────────────── L4:简报注入(无 brief 回退) ─────────────────────────


def _mk_l4_staging(tmp_path):
    d = tmp_path / DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": "600001", "name": "甲", "industry": "半导体",
                   "composite": 55, "pct_60d": 20.0, "pe": 50.0, "main_net_ratio": 0.05,
                   "cmf_20": 0.3, "close": 10, "mktcap_yi": 900}]
                 ).to_csv(d / "L1_recall_top1000.csv", index=False)
    pd.DataFrame([{"code": "600001", "name": "甲", "sector": "半导体", "conviction": 80,
                   "lane": "trend", "sentiment": "利多", "thesis": "多头", "risk": "风险",
                   "catalyst": "催化"}]).to_csv(d / "finalists.csv", index=False)
    return d


def test_compose_funnel_brief_injects_sector_terrain(tmp_path):
    d = _mk_l4_staging(tmp_path)
    base = compose_funnel_brief("600001", d)
    assert "行业地形 — 半导体" not in base                     # 无 brief → 回退路径(不注入)
    _mk_brief(d)
    withb = compose_funnel_brief("600001", d)
    assert "🏭 行业地形 — 半导体" in withb
    assert "估值地形" in withb and "行业方向" not in withb     # 只注地形段,研判段不进卡


# ───────────────────────── L5:行业研判节 + 同链对比(presence-gated) ─────────────────────────


def test_sector_view_section(tmp_path):
    d = tmp_path / DATE
    d.mkdir(parents=True)
    assert _sector_view_section(d) == ""                       # 无 briefs → 不加节(parity)
    _mk_brief(d)
    s = _sector_view_section(d)
    assert s.startswith("## 🏭 行业研判")
    assert "半导体" in s and "方向:看多" in s and "磨底" in s
    assert "景气读数" not in s                                 # 地形段不重复进 L5


def test_same_chain_block():
    rows = [{"name": "甲", "sector": "半导体", "rating": "Hold", "target": "10", "conviction": 70},
            {"name": "乙", "sector": "半导体", "rating": "Overweight", "target": "12", "conviction": 80},
            {"name": "丙", "sector": "白酒", "rating": "Hold", "target": "99", "conviction": 60}]
    block = _same_chain_block(rows)
    assert "🔗 同链对比" in block and "半导体(2只)" in block
    assert block.index("乙") < block.index("甲")               # 评级高者列前(择最佳表达)
    assert "白酒" not in block                                 # <2 只不成链
    assert _same_chain_block(rows[2:]) == ""                   # 无 ≥2 链 → ''(parity)
