#!/usr/bin/env python3
"""行业备忘录 —— 记忆中层(全局 lessons ↔ 个股档案之间;确定性存取,内容由 retro 蒸馏)。

design: docs/specs/2026-07-02-scan-portfolio-memory-design.md §4

L3 在当前 regime 里每天重新发现"半导体太贵、CFO 门高频"——把这类**行业级历史事实**
月度蒸馏成 memo,行业出现时注回 L3/L4。防锚定铁律同档案:memo 是校准事实
(估值区间/门高频原因/坑位),**不是方向指令**;评级仍由本卡 rubric 三门定。

存储:context/knowledge/sector_memos.jsonl(每行 {sector, memo, updated});
蒸馏节奏:retro 月度步(≥20 scan 日)由 Claude 从当月卡片提炼,upsert_memo 落盘。
"""
from __future__ import annotations

import json
from pathlib import Path

_DEFAULT = Path("context/knowledge/sector_memos.jsonl")


def _p(path) -> Path:
    return Path(path) if path else _DEFAULT


def load_memos(path: Path | str | None = None) -> dict[str, dict]:
    p = _p(path)
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        if r.get("sector"):
            out[r["sector"]] = r          # 后写覆盖(upsert 语义)
    return out


def upsert_memo(sector: str, memo: str, day: str, path: Path | str | None = None) -> dict:
    """按 sector 覆盖式更新(重写整文件,保持每 sector 一行)。"""
    p = _p(path)
    memos = load_memos(p)
    rec = {"sector": sector, "memo": memo, "updated": day}
    memos[sector] = rec
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in memos.values()) + "\n",
                 encoding="utf-8")
    return rec


def render_memo_line(sector: str | None, path: Path | str | None = None) -> str:
    """单行业一行(L4 简报注入);无 memo → ""。"""
    if not sector:
        return ""
    m = load_memos(path).get(str(sector))
    if not m:
        return ""
    return f"- 🏭 **行业备忘录**({m.get('updated', '?')},历史事实非预判):{m['memo']}"


def render_memo_block(sectors, path: Path | str | None = None, cap: int = 3) -> str:
    """多行业块(L3 prompt 注入);全无 → ""。"""
    memos = load_memos(path)
    hits = [memos[s] for s in dict.fromkeys(sectors) if s in memos][:cap]
    if not hits:
        return ""
    lines = ["### 🏭 行业备忘录(月度蒸馏的历史事实;校准用,非方向指令——评级仍由个股 rubric 定)"]
    lines += [f"- **{m['sector']}**({m.get('updated', '?')}):{m['memo']}" for m in hits]
    return "\n".join(lines) + "\n"
