"""个股档案(前科卡):聚合历次 scan、exclude 当日、防锚定 footer、降级。合成,无网络。

spec: docs/specs/2026-07-02-scan-dossier-design.md
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.dossier import render_dossier, stock_dossier

_CODE = "002049"


def _mk_day(root, date, with_verify=False, with_card=True):
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": _CODE, "name": "紫光国微", "sector": "半导体", "lane": "trend",
                   "conviction": 62, "risk": "CFO连续为负未验证利润"}]).to_csv(
        d / "finalists.csv", index=False)
    if with_card:
        (d / "details" / f"{_CODE}.md").write_text(
            "# 决策卡 — 002049 紫光国微\n\n**Rating**: Hold\n\n"
            "**一行多空**:多:并购催化 ｜ 空:CFO−3.5亿现金未兑现\n", encoding="utf-8")
    if with_verify:
        pd.DataFrame([{"code": _CODE, "verdict": "降级", "bull": "b", "bear": "现金流",
                       "trigger": "中报CFO转正", "consensus": "2/3"}]).to_csv(
            d / "verify.csv", index=False)


def test_dossier_aggregates_and_excludes_today(tmp_path):
    _mk_day(tmp_path, "2026-06-29")
    _mk_day(tmp_path, "2026-06-30", with_verify=True)
    _mk_day(tmp_path, "2026-07-01")                       # 当日,应被 exclude
    entries = stock_dossier(_CODE, scan_root=tmp_path, exclude="2026-07-01")
    assert [e["date"] for e in entries] == ["2026-06-29", "2026-06-30"]
    assert entries[0]["rating"] == "Hold" and "CFO" in entries[0]["l4_line"]
    assert entries[1]["verify"] and entries[1]["verify"]["verdict"] == "降级"


def test_render_has_known_kills_and_anchor_footer(tmp_path):
    _mk_day(tmp_path, "2026-06-30", with_verify=True)
    s = render_dossier(_CODE, scan_root=tmp_path, exclude="2026-07-01")
    assert "📁 个股档案" in s and "已知证伪点" in s and "变化项" in s
    assert "2026-07-01" not in s
    assert render_dossier("999999", scan_root=tmp_path) == ""     # 无历史 → ""


def test_missing_card_degrades(tmp_path):
    _mk_day(tmp_path, "2026-06-30", with_card=False)
    entries = stock_dossier(_CODE, scan_root=tmp_path)
    assert len(entries) == 1 and entries[0]["rating"] is None     # 缺卡不抛
