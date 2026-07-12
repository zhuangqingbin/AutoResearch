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
import time
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
    """从 mtime 锚推导 `{key: {"wall_s": int}}`;锚缺/负跨度 → 略过该 key。

    `预热` 例外:直接读 `_prewarm.json` **内容**(`started_at`/`ended_at` epoch 秒),
    非 mtime 链——该文件由独立 prewarm CLI 写,落盘时刻不等于预热跨度。
    `ensemble` = 卡 max-mtime → `ensemble/*.md` max-mtime(多轮蒸馏相位)。
    `assemble` = max(ensemble,卡,judged) → 本次推导时刻(诚实下界"截至本表";
    `ensure_stage_timing` 的「已有 key 优先」保证它只在首次 assemble 时定格,复渲染不漂移)。
    """
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
    ens = _mx((det / "ensemble").glob("*.md")) if (det / "ensemble").is_dir() else None

    spans = {
        "L0L1L2": (t0, l2),
        "策略师": (pack, view),
        "行业brief": (_maxopt(l2, view), briefs),     # L3 相位始于 Prelude barrier 之后
        "L3精排": (table, judged),
        "L4slim": (prompts, slims),
        "L4研究": (prompts, cards),
        "ensemble": (cards, ens),
        "assemble": (_maxopt(ens, cards, judged), time.time() if (ens or cards or judged) else None),
        "总计": (t0, _maxopt(ens, cards, judged, briefs, l2)),
    }
    out: dict = {}
    for k, (a, b) in spans.items():
        if a and b and b >= a:
            out[k] = {"wall_s": int(b - a)}
    pw = det / "_prewarm.json"                      # 预热:读内容(epoch),非 mtime 链
    if pw.is_file():
        try:
            j = json.loads(pw.read_text(encoding="utf-8"))
            w = int(float(j["ended_at"]) - float(j["started_at"]))
            if w >= 0:
                out["预热"] = {"wall_s": w}
        except Exception:  # noqa: BLE001 — 计时可选
            pass
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
