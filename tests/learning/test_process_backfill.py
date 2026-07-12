"""P0-4 过程分历史回填 CLI 单测 —— 发现口径(manifest 优先/老布局兜底)+ 累计聚合,零 LLM/无网络。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-4 + §5 局限4;
plan:   docs/plans/2026-07-12-selflearning-p0-plan.md T2。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.learning import process_backfill as pb

_CARD = """# 决策卡 — {code} 票{code} @ {date}

## 决策仪表盘
| 评级 | 现价 | EV目标 | R:R | 置信度 |
|---|---|---|---|---|
| **Hold** | 10.00 | 10.50(+5%) | 1:1 | 中 |

进入P4倾向: Hold
**Rubric建议**: Hold(净分 0,OW三门 3/3)
**Rating**: Hold

FINAL TRANSACTION PROPOSAL: **HOLD**
"""


def _mk_scan_day(scan_root: Path, date: str, codes: list[str]) -> None:
    d = scan_root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"ticker": c, "code": c, "name": f"票{c}"} for c in codes]
                ).to_csv(d / "finalists.csv", index=False)
    for c in codes:
        (d / "details" / f"{c}.md").write_text(_CARD.format(code=c, date=date), encoding="utf-8")


def _mk_report_new_layout(report_root: Path, folder: str, analysis_date: str) -> None:
    d = report_root / folder
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"analysis_date": analysis_date}), encoding="utf-8")


def _mk_report_old_layout(report_root: Path, date_compact: str, hhmm: str = "0930") -> None:
    d = report_root / f"{date_compact}_{hhmm}"
    d.mkdir(parents=True)   # 无 manifest.json = 老布局


# ───────────────────────── 日期发现 ─────────────────────────


def test_discover_dates_prefers_manifest_analysis_date(tmp_path):
    report_root = tmp_path / "reports/scan"
    _mk_report_new_layout(report_root, "20260711_1808", "2026-07-09")   # 运行日≠数据日
    assert pb._discover_dates(report_root) == ["2026-07-09"]


def test_discover_dates_old_layout_uses_dirname_date(tmp_path):
    report_root = tmp_path / "reports/scan"
    _mk_report_old_layout(report_root, "20260620")
    assert pb._discover_dates(report_root) == ["2026-06-20"]


def test_discover_dates_dedupes_and_sorts(tmp_path):
    report_root = tmp_path / "reports/scan"
    _mk_report_new_layout(report_root, "20260706_2311", "2026-07-06")
    _mk_report_new_layout(report_root, "20260706_2354", "2026-07-06")   # 同数据日两次运行
    _mk_report_old_layout(report_root, "20260618")
    assert pb._discover_dates(report_root) == ["2026-06-18", "2026-07-06"]


def test_discover_dates_missing_report_root_returns_empty(tmp_path):
    assert pb._discover_dates(tmp_path / "nope") == []


# ───────────────────────── backfill 聚合 ─────────────────────────


def test_backfill_aggregates_across_dates_with_date_column(tmp_path):
    report_root = tmp_path / "reports/scan"
    scan_root = tmp_path / "context/scan"
    _mk_report_new_layout(report_root, "20260621_0930", "2026-06-20")
    _mk_report_new_layout(report_root, "20260622_0930", "2026-06-21")
    _mk_scan_day(scan_root, "2026-06-20", ["300476"])
    _mk_scan_day(scan_root, "2026-06-21", ["600519", "002384"])
    out = tmp_path / "out.csv"

    res = pb.backfill(report_root=report_root, scan_root=scan_root, out_path=out)
    assert res["dates_used"] == ["2026-06-20", "2026-06-21"]
    assert res["dates_skipped"] == []
    assert res["n_rows"] == 3
    assert out.exists()
    df = pd.read_csv(out, dtype={"code": str, "date": str})
    assert set(df["date"]) == {"2026-06-20", "2026-06-21"}
    assert set(df["code"]) == {"300476", "600519", "002384"}
    assert "process_score" in df.columns


def test_backfill_skips_dates_without_staging(tmp_path):
    """context/scan/<date> staging 已不在场(或无 finalists.csv)→ 计入 dates_skipped,不报错。"""
    report_root = tmp_path / "reports/scan"
    scan_root = tmp_path / "context/scan"
    _mk_report_new_layout(report_root, "20260621_0930", "2026-06-20")   # staging 缺失
    out = tmp_path / "out.csv"

    res = pb.backfill(report_root=report_root, scan_root=scan_root, out_path=out)
    assert res["dates_scanned"] == ["2026-06-20"]
    assert res["dates_used"] == []
    assert res["dates_skipped"] == ["2026-06-20"]
    assert res["n_rows"] == 0
    assert out.exists()   # 仍写空表(表头齐全),不是完全不落盘


def test_backfill_distribution_counts_process_score_histogram(tmp_path):
    report_root = tmp_path / "reports/scan"
    scan_root = tmp_path / "context/scan"
    _mk_report_new_layout(report_root, "20260621_0930", "2026-06-20")
    _mk_scan_day(scan_root, "2026-06-20", ["300476", "600519"])
    out = tmp_path / "out.csv"

    res = pb.backfill(report_root=report_root, scan_root=scan_root, out_path=out)
    assert res["n_rows"] == 2
    assert sum(res["distribution"].values()) == 2


def test_main_cli_runs_and_prints_summary(tmp_path, capsys):
    report_root = tmp_path / "reports/scan"
    scan_root = tmp_path / "context/scan"
    _mk_report_new_layout(report_root, "20260621_0930", "2026-06-20")
    _mk_scan_day(scan_root, "2026-06-20", ["300476"])
    out = tmp_path / "out.csv"

    rc = pb.main(["--report-root", str(report_root), "--scan-root", str(scan_root), "--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "process_backfill" in captured.out
    assert out.exists()
