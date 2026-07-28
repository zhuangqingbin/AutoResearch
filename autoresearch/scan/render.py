#!/usr/bin/env python3
"""scan 过程直播渲染器(确定性,零 LLM)——把 L5 才渲染的表提前到跑动中随时可调。

design: docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md §①

为什么单独一个模块:菜单体检 / 门直方图 / 耗时表 / 漏斗表全都是现成的确定性渲染器,
但此前只在 assemble(L5)里被调用一次 —— 用户在 L2 完成后想知道"今天菜单什么成色"、
在 L4 完成后想知道"门柱什么形状",都得等一小时后的 summary.md。本模块零新计算,
只是把同样的渲染器接到一个可随时调用的 CLI 上。

  uv run --no-sync python -m autoresearch.scan.render 2026-07-25 --view menu_health
  uv run --no-sync python -m autoresearch.scan.render 2026-07-25 --view gate_hist
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

VIEWS = ("menu_health", "gate_hist", "timing", "funnel")


def _scan_dir(date: str, root: Path | str | None = None) -> Path:
    return Path(root or "context/scan") / date


def _fmt_wall(v) -> str:
    if isinstance(v, dict):
        v = v.get("wall_s")
    if not v:
        return "—"
    v = int(v)
    return f"{v // 60}m{v % 60:02d}s" if v >= 60 else f"{v}s"


def _view_menu_health(det: Path) -> str:
    from autoresearch.scan.menu import menu_health
    out = menu_health(det)
    return out or "(菜单体检:staging 缺 L2_gbdt_top200.csv 或 L1_scored_full.csv —— 未跑或跑挂)"


def _view_gate_hist(det: Path) -> str:
    from autoresearch.scan.report_sections import gate_histogram
    from autoresearch.scan.health import final_ratings
    ratings = final_ratings(det)
    if not ratings:
        return "(门直方图:details/ 无决策卡 —— L4 未跑或全失败)"
    order = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    cnt = {k: sum(1 for r in ratings.values() if r == k) for k in order}
    dist = " · ".join(f"{k} {cnt[k]}" for k in order if cnt[k])
    lines = [f"**评级分布**({len(ratings)} 卡):{dist or '—'}"]
    from autoresearch.scan.market import _zero_buy_mechanism
    lines.append(f"**停因分桶**:{_zero_buy_mechanism(det, len(ratings))}")
    hist = gate_histogram(det, [{"code": c} for c in ratings])
    lines.append(hist or "(OW三门:无可解析卡 —— 多为早停卡,早停卡按定义不写三门段)")
    return "\n".join(lines)


def _view_timing(det: Path) -> str:
    from autoresearch.scan.stage_timing import ensure_stage_timing
    tmap = ensure_stage_timing(det)
    if not tmap:
        return "(耗时表:_stage_timing.json 缺且无可推导锚 —— 本次尚无产物落盘)"
    lines = ["| 阶段 | 墙钟 |", "|---|---:|"]
    lines += [f"| {k} | {_fmt_wall(v)} |" for k, v in tmap.items()]
    return "\n".join(lines)


def _view_funnel(det: Path) -> str:
    import pandas as pd

    from autoresearch.scan.report_sections import _funnel_rows

    def _n(name: str) -> int:
        p = det / name
        if not p.is_file():
            return 0
        try:
            return int(len(pd.read_csv(p)))
        except Exception:  # noqa: BLE001 — 半截文件不该炸掉直播
            return 0

    meta: dict = {}
    mp = det / "meta.json"
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    n_cards = len(list((det / "details").glob("*.md"))) if (det / "details").is_dir() else 0
    if not meta and not _n("L2_gbdt_top200.csv"):
        return "(漏斗表:meta.json 与 L2 staging 均缺 —— 前奏未跑)"
    return "\n".join(_funnel_rows(meta, _n("L2_gbdt_top200.csv"), _n("finalists.csv"), n_cards))


def render_view(date: str, view: str, root: Path | str | None = None) -> str:
    """按 view 名渲染一块 markdown;产物缺失 → 显式说明缺什么(不静默返回空串)。"""
    if view not in VIEWS:
        raise ValueError(f"未知 view「{view}」,可选:{'/'.join(VIEWS)}")
    det = _scan_dir(date, root)
    fn = {"menu_health": _view_menu_health, "gate_hist": _view_gate_hist,
          "timing": _view_timing, "funnel": _view_funnel}[view]
    return fn(det)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan 过程直播渲染器(零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--view", required=True, choices=VIEWS)
    ap.add_argument("--root", default=None, help="scan staging 根(默认 context/scan)")
    a = ap.parse_args(argv)
    print(render_view(a.date, a.view, root=a.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
