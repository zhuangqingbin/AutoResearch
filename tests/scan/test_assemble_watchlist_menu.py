"""assemble 嵌入观察单日检 + L2 菜单体检(presence-gated,缺 staging 不加节)。

spec: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.4
独立于 tests/scan/test_assemble.py(该文件另有未合改动),fixture 模式同 test_market_view_embed。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.assemble import build_summary


def _min_scan(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    return d


def test_watchlist_block_when_status_present(tmp_path):
    d = _min_scan(tmp_path)
    pd.DataFrame([{"code": "300476", "name": "胜宏科技", "status": "临近",
                   "detail": "ma_bull=no;close_above:314=yes", "narrative": "中报beat",
                   "born": "2026-06-30", "expiry": "2026-08-14"}]).to_csv(
        d / "watchlist_status.csv", index=False)
    md = build_summary(d, "2026-07-02", "1200", "20260702_1200")
    assert "👀 观察单日检" in md and "胜宏科技" in md


def test_no_watchlist_block_when_absent(tmp_path):
    md = build_summary(_min_scan(tmp_path), "2026-07-02", "1200", "20260702_1200")
    assert "观察单日检" not in md


def test_menu_block_when_l2_present(tmp_path):
    d = _min_scan(tmp_path)
    rows = [{"code": "000001", "industry": "半导体", "pct_60d": -30.0,
             "main_net_ratio": -0.01, "cmf_20": -0.02, "pe": 30.0}]
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame(rows * 3).to_csv(d / "L1_scored_full.csv", index=False)
    md = build_summary(d, "2026-07-02", "1200", "20260702_1200")
    assert "🍱 L2 菜单体检" in md


def test_no_menu_block_when_absent(tmp_path):
    md = build_summary(_min_scan(tmp_path), "2026-07-02", "1200", "20260702_1200")
    assert "菜单体检" not in md


def test_gate_fires_staging_written_by_assemble(tmp_path):
    """assemble 每次跑完幂等落 gate_fires.csv(即使 0 failures,写表头区分没拦/没跑)。"""
    d = _min_scan(tmp_path)
    build_summary(d, "2026-07-02", "1200", "20260702_1200")
    assert (d / "gate_fires.csv").exists()
