#!/usr/bin/env python3
"""P0-4·过程分历史回填 —— 遍历已发布 scan 报告,对仍在场的 staging 目录跑
`process_score.compute_process_scores`,汇总成一个累计 CSV(零 LLM)。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-4 + §5 局限4;
plan:   docs/plans/2026-07-12-selflearning-p0-plan.md T2。

**发现口径**:以 `reports/scan/*/`(已发布报告,权威的"这天真发过报告")为准 —— 新布局按
`manifest.json` 的 `analysis_date` 取数据日,老布局(无 manifest)退化为目录名
`YYYYMMDD_HHMM` 的日期段。**计算口径**:对每个发现的数据日,若 `context/scan/<date>/`
staging 目录仍在场(本仓截至目前全部历史日都还在),直接复用 `process_score` 模块跑同一套
checklist(与实时 assemble 完全同源,非重新发明)——若 staging 已被清理,该日跳过并计入
`dates_skipped`(诚实局限:不尝试从 `reports/scan/<run>/trace/` 反推残缺子集,避免半真半假
的读数混进累计表)。

**模板代际差容错**:各 checklist 项本身已presence-gated(见 `process_score.py` docstring)——
两遍法/ensemble/基率行等功能上线前的老日期,相应项自然读 False,不是本模块另开的特例。

用法:
  uv run --no-sync python -m autoresearch.learning.process_backfill
  uv run --no-sync python -m autoresearch.learning.process_backfill --out reports/learning/process_scores_history.csv
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from autoresearch.learning.process_score import _CHECKS, compute_process_scores

_DATE_DIR_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_\d{3,4}$")


def _discover_dates(report_root: Path) -> list[str]:
    """`reports/scan/*/` → 排序去重的数据日列表(manifest 优先,老布局按目录名兜底)。"""
    dates: set[str] = set()
    if not report_root.exists():
        return []
    for p in sorted(report_root.glob("*")):
        if not p.is_dir():
            continue
        mf = p / "manifest.json"
        if mf.exists():
            try:
                d = json.loads(mf.read_text(encoding="utf-8")).get("analysis_date")
            except Exception:  # noqa: BLE001 — 坏 manifest 走目录名兜底
                d = None
            if d:
                dates.add(d)
                continue
        m = _DATE_DIR_RE.match(p.name)
        if m:
            dates.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return sorted(dates)


def backfill(report_root: Path | str | None = None, scan_root: Path | str | None = None,
             out_path: Path | str | None = None) -> dict:
    """跑一次历史回填,写累计 CSV,返回 `{dates_scanned, dates_used, dates_skipped, n_rows,
    distribution, out_path}`(distribution = process_score 0-6 → 计数)。
    """
    report_root = Path(report_root) if report_root is not None else Path("reports/scan")
    scan_root = Path(scan_root) if scan_root is not None else Path("context/scan")
    out_path = Path(out_path) if out_path is not None else Path("reports/learning/process_scores_history.csv")

    dates = _discover_dates(report_root)
    frames: list[pd.DataFrame] = []
    used: list[str] = []
    skipped: list[str] = []
    for d in dates:
        sdir = scan_root / d
        if not (sdir / "finalists.csv").exists():
            skipped.append(d)
            continue
        df = compute_process_scores(sdir)
        if df.empty:
            skipped.append(d)
            continue
        df = df.copy()
        df.insert(0, "date", d)
        frames.append(df)
        used.append(d)

    cols = ["date", "code", *_CHECKS, "process_score"]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path, index=False)

    dist: dict[int, int] = {}
    if len(combined):
        dist = {int(k): int(v) for k, v in combined["process_score"].value_counts().sort_index().items()}

    return {"dates_scanned": dates, "dates_used": used, "dates_skipped": skipped,
            "n_rows": int(len(combined)), "distribution": dist, "out_path": str(out_path)}


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m autoresearch.learning.process_backfill",
                                 description="P0-4 过程分历史回填(遍历 reports/scan/*,汇总累计 CSV,零 LLM)")
    ap.add_argument("--report-root", default=None, help="已发布报告根目录(默认 reports/scan)")
    ap.add_argument("--scan-root", default=None, help="scan staging 根目录(默认 context/scan)")
    ap.add_argument("--out", default=None, help="累计 CSV 落点(默认 reports/learning/process_scores_history.csv)")
    args = ap.parse_args(argv)

    res = backfill(report_root=args.report_root, scan_root=args.scan_root, out_path=args.out)
    print(f"[process_backfill] 发现 {len(res['dates_scanned'])} 日"
          f"(用 {len(res['dates_used'])} 日,跳过 {len(res['dates_skipped'])} 日"
          f"{'·' + ','.join(res['dates_skipped']) if res['dates_skipped'] else ''})")
    print(f"[process_backfill] {res['n_rows']} 行 → {res['out_path']}")
    print(f"[process_backfill] process_score 分布(0-6):{res['distribution']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
