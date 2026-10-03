#!/usr/bin/env python3
"""sector-research · brief 契约(单段结构)与确定性抽取。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.3(Phase 3)。
2026-08-19 D6(⚖A6,用户裁定):研判段整段砍除(旧版最终只在 summary 落一行方向+链接,
分析最重的半段落只值一行字)——brief 现在**只产出地形段**,行业方向叙事改由确定性
top3(`autoresearch/scan/market.py` 的 `sector_healthy_top3`/`render_sector_top3`)独扛。

brief 文件 = `context/scan/<date>/sector_briefs/<行业>.md`,由 lite subagent 按
`sector-playbook.md` 模板产出,**单段**(标题即机器契约,勿改字):
  `## 地形段(喂 L3/L4 · 描述性)` ← extract_terrain → 注 L3 表头 + L4 简报。
地形段只许数字/事实/日历,不带方向——个股评级只由本股 rubric 三门决定,行业方向不再由
本模块判断。
"""
from __future__ import annotations

from pathlib import Path

BRIEF_DIRNAME = "sector_briefs"
TERRAIN_HDR = "## 地形段"


def brief_path(scan_dir: Path | str, industry) -> Path:
    from autoresearch.sector.pack import _safe
    return Path(scan_dir) / BRIEF_DIRNAME / f"{_safe(industry)}.md"


def _section(text: str, start_hdr: str, stop_prefix: str = "## ") -> str:
    """取 start_hdr 小节正文(到下一个 `## ` 为止);无该节 → ''。"""
    out: list[str] = []
    on = False
    for ln in (text or "").splitlines():
        if ln.strip().startswith(start_hdr):
            on = True
            continue
        if on and ln.startswith(stop_prefix):
            break
        if on:
            out.append(ln)
    return "\n".join(out).strip()


def extract_terrain(text: str) -> str:
    return _section(text, TERRAIN_HDR)


def render_terrain_block(industry, scan_dir: Path | str) -> str:
    """L4 简报注入块 = 该行业 brief 的地形段。无 brief/空地形段 → ''(调用方回退 memo 行)。"""
    if not industry or str(industry) in ("nan", "—"):
        return ""
    p = brief_path(scan_dir, industry)
    if not p.exists():
        return ""
    terr = extract_terrain(p.read_text(encoding="utf-8"))
    if not terr:
        return ""
    return (f"### 🏭 行业地形 — {industry}(行业 brief · 描述性;个股评级仍由本卡 rubric 三门定)\n"
            f"{terr}")


def render_deterministic_terrain(pack: dict, **kwargs) -> str:
    """Public candidate renderer; existing extraction/injection contract is unchanged."""
    from autoresearch.sector.terrain import render_terrain
    return render_terrain(pack, **kwargs)
