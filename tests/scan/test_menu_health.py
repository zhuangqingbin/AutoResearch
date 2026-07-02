"""L2 菜单体检块:健康上涨/落刀/行业集中度/估值,缺列缺文件降级。合成 fixture。

spec: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.2
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.menu import menu_health


def _mk(scan_dir, l2_rows, l1_rows):
    scan_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(l2_rows).to_csv(scan_dir / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame(l1_rows).to_csv(scan_dir / "L1_scored_full.csv", index=False)


def _row(code, ind="半导体", pct=10.0, mnr=0.01, cmf=0.05, pe=30.0, reserved=0):
    return {"code": code, "industry": ind, "pct_60d": pct, "main_net_ratio": mnr,
            "cmf_20": cmf, "pe": pe, "l2_lane_reserved": reserved}


def test_menu_health_core(tmp_path):
    l2 = [_row("000001", "半导体", 10, 0.01, 0.05),        # 健康上涨
          _row("000002", "半导体", -30, -0.01, -0.05),     # 落刀
          _row("000003", "白酒Ⅱ", -25, 0.01, -0.02),       # 落刀
          _row("000004", "半导体", 60, 0.01, 0.05, pe=80), # 过热(pct>40 不算健康)
          _row("000005", "电力", 5, -0.02, 0.01, reserved=1)]
    l1 = l2 + [_row(f"00000{i}", "电力", 3, 0.01, 0.01) for i in range(6, 10)]  # 全市场再加 4 只健康
    _mk(tmp_path, l2, l1)
    s = menu_health(tmp_path)
    assert "🍱 L2 菜单体检" in s
    assert "健康上涨" in s and "1/5" in s and "5/9" in s     # L2 1只 vs 全市场 5只
    assert "落刀" in s and "40%" in s                        # L2 2/5
    assert "半导体" in s and "60%" in s                      # 行业 top 3/5
    assert "floor 救回" in s and "1" in s


def test_missing_files_empty(tmp_path):
    assert menu_health(tmp_path) == ""


def test_missing_cols_degrade(tmp_path):
    l2 = [{"code": "000001", "industry": "半导体", "pct_60d": -30.0}]
    _mk(tmp_path, l2, l2)
    s = menu_health(tmp_path)
    assert "🍱 L2 菜单体检" in s and "落刀" in s
    assert "健康上涨" not in s        # 缺 main/cmf 列 → 该行降级消失,不抛
