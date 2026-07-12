#!/usr/bin/env python3
"""scan-market 进度探测(确定性读盘,零 LLM)。

**为什么存在**:六段漏斗跑在后台 workflow 里(~65 分钟,开了情报站更久),`/workflows` 有
实时进度树,但**主对话一片空白** —— 用户看不到跑到哪一步了(2026-07-12 用户反馈)。

本模块从**产物文件**反推当前阶段与各段计数,一行一条,供 `Monitor` 轮询播报到主对话。

铁律:**只读盘,不猜**。产物不在 = 该阶段未完成(不臆测"大概快好了");读不到 = 报读不到。

用法:
  uv run --no-sync python -m autoresearch.scan.progress <date>            # 打一行当前进度
  uv run --no-sync python -m autoresearch.scan.progress <date> --watch    # 轮询,变化才打印,完成即退出
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPORT_ROOT = Path("reports/scan")


def _n_rows(p: Path) -> int:
    """CSV 行数(不含表头);缺文件/读不动 → 0。"""
    if not p.exists():
        return 0
    try:
        import pandas as pd
        return int(len(pd.read_csv(p, dtype={"code": str})))
    except Exception:  # noqa: BLE001 — 半写入的 CSV 也算 0,不抛
        return 0


def _rating_of(card: Path) -> str | None:
    """从决策卡里抠 `**Rating**: X`;抠不到 → None(不猜)。"""
    try:
        for ln in card.read_text(encoding="utf-8").splitlines():
            if ln.startswith("**Rating**:"):
                return ln.split(":", 1)[1].replace("*", "").strip() or None
    except Exception:  # noqa: BLE001
        return None
    return None


def _published_report(date: str, report_root: Path) -> str | None:
    """已发布的报告目录(manifest.analysis_date == date ∧ summary.md 在)。

    **必须按 manifest 定位**:reports/scan/ 里全是历史跑的目录,按 mtime 取最新会把
    昨天的报告当成今天的 → 进度误报 Done。
    """
    if not report_root.exists():
        return None
    for d in sorted(report_root.iterdir(), reverse=True):
        mf, sm = d / "manifest.json", d / "summary.md"
        if not (mf.exists() and sm.exists()):
            continue
        try:
            if json.loads(mf.read_text(encoding="utf-8")).get("analysis_date") == date:
                return str(d)
        except Exception:  # noqa: BLE001
            continue
    return None


def snapshot(scan_dir: Path | str, report_root: Path | str = REPORT_ROOT) -> dict:
    """当前跑到哪一步 + 各段计数。纯读盘,可重复调用。"""
    sd = Path(scan_dir)
    date = sd.name
    rr = Path(report_root)

    done: list[str] = []
    for key, p in (("market_pack", sd / "market_pack.json"),
                   ("L1", sd / "L1_recall_top1000.csv"),
                   ("L2", sd / "L2_gbdt_top200.csv"),
                   ("market_view", sd / "market_view.md"),
                   ("l3_table", sd / "_l3_table.md"),
                   ("l3_judged", sd / "_l3_judged.json"),
                   ("finalists", sd / "finalists.csv")):
        if p.exists() and p.stat().st_size > 0:
            done.append(key)

    details = sd / "details"
    cards = sorted(details.glob("*.md")) if details.is_dir() else []
    ratings: dict[str, int] = {}
    for c in cards:
        r = _rating_of(c)
        if r:
            ratings[r] = ratings.get(r, 0) + 1

    snap = {
        "date": date,
        "done": done,
        "l1": _n_rows(sd / "L1_recall_top1000.csv"),
        "l2": _n_rows(sd / "L2_gbdt_top200.csv"),
        "briefs": len(list((sd / "sector_briefs").glob("*.md"))) if (sd / "sector_briefs").is_dir() else 0,
        "finalists": _n_rows(sd / "finalists.csv"),
        "dispatch": len(list(sd.glob("_l4_prompt_*.md"))),
        "intel": len(list(sd.glob("_l4_intel_*.md"))),
        "cards": len(cards),
        "ratings": ratings,
        "report": _published_report(date, rr),
    }

    if snap["report"]:
        snap["stage"] = "Done"
    elif "finalists" in done or snap["dispatch"]:
        snap["stage"] = "L4"
    elif "L2" in done:
        snap["stage"] = "L3"
    else:
        snap["stage"] = "Prelude"
    return snap


def render_line(snap: dict) -> str:
    """一行进度(Monitor 一行 = 主对话一条通知)。不含换行。"""
    st = snap["stage"]
    if st == "Done":
        buys = snap["ratings"].get("Overweight", 0) + snap["ratings"].get("Buy", 0)
        return (f"✅ 扫描完成 · {snap['cards']} 卡 · ≥OW {buys} 只 · 报告 {snap['report']}")
    if st == "Prelude":
        bits = [f"L1 {snap['l1']}" if snap["l1"] else "全市场取数中(~10m)"]
        if "market_view" in snap["done"]:
            bits.append("市场研判 ✓")
        return f"⏳ Prelude · {' · '.join(bits)}"
    if st == "L3":
        bits = [f"L2 菜单 {snap['l2']} 只"]
        if snap["briefs"]:
            bits.append(f"行业 brief {snap['briefs']}")
        bits.append("L3 精排 ✓" if "l3_judged" in snap["done"] else "L3 精排中(~14m)")
        return f"⏳ L3 · {' · '.join(bits)}"
    # L4
    bits = [f"finalists {snap['finalists']}"]
    if snap["intel"]:
        bits.append(f"🕵️ 情报 {snap['intel']}")
    bits.append(f"卡 {snap['cards']}/{snap['dispatch'] or snap['finalists']}")
    if snap["ratings"]:
        bits.append("·".join(f"{k} {v}" for k, v in sorted(snap["ratings"].items())))
    return f"⏳ L4 · {' · '.join(bits)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan 进度探测(确定性读盘)")
    ap.add_argument("date")
    ap.add_argument("--watch", action="store_true", help="轮询;仅在进度变化时打印;完成即退出")
    ap.add_argument("--interval", type=int, default=30, help="轮询间隔秒(默认 30)")
    ap.add_argument("--max-min", type=int, default=180, help="最长监视分钟(默认 180)")
    a = ap.parse_args(argv)

    sd = Path("context/scan") / a.date
    if not a.watch:
        print(render_line(snapshot(sd)), flush=True)
        return 0

    last = None
    deadline = time.time() + a.max_min * 60
    while time.time() < deadline:
        snap = snapshot(sd)
        line = render_line(snap)
        if line != last:                       # 只在变化时发事件,避免刷屏被 Monitor 掐掉
            print(line, flush=True)
            last = line
        if snap["stage"] == "Done":
            return 0
        time.sleep(a.interval)
    print(f"⚠️ 进度监视超时({a.max_min} 分钟未见 summary.md)—— workflow 可能已失败,查 /workflows", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
