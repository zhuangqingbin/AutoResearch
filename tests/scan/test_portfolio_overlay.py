"""组合视角 overlay:仓位档位/菜单病下沿/0买一致性/同板块 bet 告警/缺 regime 降级。合成。

spec: docs/specs/2026-07-02-scan-portfolio-memory-design.md §3
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.assemble import _portfolio_note, _position_overlay, build_summary


def _mk(tmp_path, regime="risk_off"):
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    return d


def test_position_overlay_zero_buy(tmp_path):
    d = _mk(tmp_path, "risk_off")
    s = _position_overlay(d, [])
    assert "0–2 成" in s and "空仓/底仓与系统读数一致" in s


def test_position_overlay_missing_regime(tmp_path):
    d = _mk(tmp_path, regime=None)
    assert _position_overlay(d, []) == ""                     # 缺 regime → 整行消失


def test_position_overlay_sick_menu_lower_band(tmp_path):
    d = _mk(tmp_path, "range")
    rows = [{"code": f"{i:06d}", "industry": "半导体", "pct_60d": -30.0,
             "main_net_ratio": -0.01, "cmf_20": -0.05, "pe": 30.0} for i in range(10)]
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    s = _position_overlay(d, [{"rating": "Overweight"}])
    assert "3–5 成" in s and "下沿" in s and "1 只买单" in s


def test_portfolio_note_same_sector_bet():
    rows = [{"rating": "Overweight", "sector": "半导体"},
            {"rating": "Buy", "sector": "半导体"},
            {"rating": "Hold", "sector": "电力"}]
    s = _portfolio_note(rows)
    assert "1 个 bet" in s and "2/2" in s


def test_summary_embeds_overlay(tmp_path):
    d = _mk(tmp_path, "risk_off")
    md = build_summary(d, "2026-07-02", "1200", "20260702_1200")
    assert "仓位建议" in md and "0–2 成" in md
