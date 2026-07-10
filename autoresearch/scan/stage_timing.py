#!/usr/bin/env python3
"""墙钟推导:从 staging 产物 mtime 链推各阶段耗时,写 `_stage_timing.json`(零 LLM)。

why mtime:workflow 沙箱禁 `Date.now()`(docs/specs/2026-07-07-scan-market-workflow-plan.md:693),
编排层写不了计时;但每阶段产物的落盘时刻天然=该阶段结束时刻。推导只补缺席 key
(编排/人工写过的一律尊重);锚缺/跨度为负(如当日全复用卡)→ 略过该 key,渲染回退 `—`。
键与 assemble 墙钟表消费口径一致。「总计」= t0 → 最晚产物(不含 assemble 自身,诚实下界)。
design: docs/specs/2026-07-10-ultrashort-t2-inst-progress-design.md §4.2
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path


def _mt(p: Path) -> float | None:
    return p.stat().st_mtime if p.is_file() else None


def _mx(paths) -> float | None:
    ts = [p.stat().st_mtime for p in paths if p.is_file()]
    return max(ts) if ts else None


def _maxopt(*xs) -> float | None:
    xs = [x for x in xs if x]
    return max(xs) if xs else None


def derive_stage_timing(det: Path) -> dict:
    """从 mtime 锚推导 `{key: {"wall_s": int}}`;锚缺/负跨度 → 略过该 key。"""
    det = Path(det)
    t0 = _mt(det / "_t0.json") or _mt(det / "market_pack.json")
    pack = _mt(det / "market_pack.json")
    view = _mt(det / "market_view.md")
    l2 = _mt(det / "L2_gbdt_top200.csv")
    table = _mt(det / "_l3_table.md")
    judged = _mt(det / "_l3_judged.json")
    briefs = _mx((det / "sector_briefs").glob("*.md")) if (det / "sector_briefs").is_dir() else None
    prompts = _mx(det.glob("_l4_prompt_*.md"))
    cards = _mx((det / "details").glob("*.md")) if (det / "details").is_dir() else None
    slim_root = det.parent.parent
    slims = _mx(slim_root.glob(f"*_{det.name}_slim.md")) if slim_root.exists() else None

    spans = {
        "L0L1L2": (t0, l2),
        "策略师": (pack, view),
        "行业brief": (_maxopt(l2, view), briefs),     # L3 相位始于 Prelude barrier 之后
        "L3精排": (table, judged),
        "L4slim": (prompts, slims),
        "L4研究": (prompts, cards),
        "总计": (t0, _maxopt(cards, judged, briefs, l2)),
    }
    out: dict = {}
    for k, (a, b) in spans.items():
        if a and b and b >= a:
            out[k] = {"wall_s": int(b - a)}
    return out


def ensure_stage_timing(det: Path) -> dict:
    """读 `_stage_timing.json`(已有 key 优先)+ mtime 推导补缺 → 合并写回。never raises。"""
    det = Path(det)
    fp = det / "_stage_timing.json"
    existing: dict = {}
    if fp.is_file():
        try:
            existing = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏文件当空,推导重建
            existing = {}
    try:
        derived = derive_stage_timing(det)
    except Exception:  # noqa: BLE001 — 计时可选,不挡 assemble
        derived = {}
    merged = {**derived, **existing}
    if merged and merged != existing:
        with contextlib.suppress(Exception):
            fp.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    return merged
