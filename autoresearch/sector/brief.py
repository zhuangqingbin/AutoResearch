#!/usr/bin/env python3
"""sector-research · brief 契约(两段结构)与确定性抽取。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.3(Phase 3)。

brief 文件 = `context/scan/<date>/sector_briefs/<行业>.md`,由 lite subagent 按
`sector-playbook.md` 模板产出,**两段**(标题即机器契约,勿改字):
  `## 地形段(喂 L3/L4 · 描述性)` ← extract_terrain → 注 L3 表头 + L4 简报
  `## 研判段(仅 L5)`             ← extract_view → 只进 assemble 行业研判节;
                                    内含 `**行业方向**: 看多|中性|看空` 行(sector_ledger 记账)。
**三层同律**:地形段只许数字/事实/日历;方向性内容只在研判段(=只进 L5)。
"""
from __future__ import annotations

import re
from pathlib import Path

BRIEF_DIRNAME = "sector_briefs"
TERRAIN_HDR = "## 地形段"
VIEW_HDR = "## 研判段"
_DIR_RE = re.compile(r"\*\*行业方向\*\*\s*[::]\s*(看多|中性|看空)")


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


def extract_view(text: str) -> str:
    return _section(text, VIEW_HDR)


def parse_direction(text: str) -> str | None:
    """研判段的 `**行业方向**` keyed 行 → 看多|中性|看空;无 → None。"""
    m = _DIR_RE.search(text or "")
    return m.group(1) if m else None


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
