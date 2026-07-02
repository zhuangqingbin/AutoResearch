"""日历(解禁/预约披露):quarter 端点、flags、section、简报注入、assemble 嵌入。合成,无网络。

spec: docs/specs/2026-07-02-scan-calendar-shadow-design.md §1
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.calendar import _last_quarter_end, calendar_flags, calendar_section

_ROWS = [
    {"code": "000001", "kind": "unlock", "event_date": "20260715", "detail": "定增股份·3方", "ratio": 8.2},
    {"code": "000001", "kind": "disclosure", "event_date": "20260716", "detail": "预约披露(期 20260630)", "ratio": None},
    {"code": "000002", "kind": "unlock", "event_date": "20260710", "detail": "定增股份·1方", "ratio": 0.5},
    {"code": "000003", "kind": "unlock", "event_date": "20261230", "detail": "首发原股东", "ratio": 30.0},
]


def _mk(tmp_path, date="2026-07-02", finalists=("000001",)):
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(_ROWS).to_csv(d / "calendar.csv", index=False)
    pd.DataFrame([{"code": c, "name": f"N{c}", "sector": "半导体"} for c in finalists]).to_csv(
        d / "finalists.csv", index=False)
    return d


def test_last_quarter_end():
    assert _last_quarter_end("2026-07-02") == "20260630"
    assert _last_quarter_end("2026-02-01") == "20251231"
    assert _last_quarter_end("2026-04-01") == "20260331"


def test_calendar_flags(tmp_path):
    d = _mk(tmp_path)
    f1 = calendar_flags(d, "000001")
    assert any("解禁" in x and "8.2%" in x for x in f1)
    assert any("预约披露" in x and "20260716" in x for x in f1)
    assert calendar_flags(d, "000002") == []          # ratio 0.5 < 2.0 阈,不旗
    assert calendar_flags(d, "000003") == []          # 解禁在 30 日窗外
    assert calendar_flags(tmp_path / "nope", "000001") == []


def test_calendar_section(tmp_path):
    d = _mk(tmp_path)
    s = calendar_section(d)
    assert "📅" in s and "000001 20260716" in s        # finalists 披露
    assert "8%" in s or "8.2" in s or "(8%)" in s      # 大解禁 ≥5%
    assert "000003" not in s                           # 14 日窗外
    assert calendar_section(tmp_path / "nope") == ""


def test_brief_injects_calendar(tmp_path):
    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    d = _mk(tmp_path)
    s = compose_funnel_brief("000001", d)
    assert "解禁" in s and "预约披露" in s
    s2 = compose_funnel_brief("000002", d)
    assert "解禁" not in s2                            # 小解禁不旗


def test_assemble_embeds_calendar(tmp_path):
    from autoresearch.scan.assemble import build_summary
    d = _mk(tmp_path)
    (d / "meta.json").write_text("{}", encoding="utf-8")
    md = build_summary(d, "2026-07-02", "1200", "20260702_1200")
    assert "未来 14 天日历" in md
