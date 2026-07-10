"""assemble 嵌入「📌 保送」节(pinned 直通,presence-gated:无 pinned.json → 无节)。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.1。
plan: docs/plans/2026-07-11-pinned-config-plan.md Task 4。
fixture 模式同 tests/scan/test_assemble_watchlist_menu.py(独立于 test_assemble.py 的大 fixture)。
"""
from __future__ import annotations

import json

from autoresearch.scan.assemble import build_summary


def _min_scan(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    return d


def _scan_with_pinned_finalist(tmp_path):
    """finalists 里已有一只 lane=pinned 强留票(600000)+ 一张 Hold 决策卡。"""
    d = _min_scan(tmp_path)
    (d / "finalists.csv").write_text(
        "ticker,code,name,sector,lane,pinned_note\n"
        "600000,600000,手工票,银行,pinned,长期观察仓\n", encoding="utf-8")
    (d / "details").mkdir()
    (d / "details" / "600000.md").write_text(
        "# 决策卡\n**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n", encoding="utf-8")
    return d


def _section_body(md: str) -> str:
    sec = md.split("## 📌 保送", 1)[1]
    end = sec.find("\n## ")
    return sec[:end] if end != -1 else sec


def test_pinned_section_when_kept_present(tmp_path):
    d = _scan_with_pinned_finalist(tmp_path)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([
        {"code": "600000", "note": "长期观察仓", "expires": "2099-01-01"},
    ]), encoding="utf-8")
    md = build_summary(d, "2026-07-11", "1200", "20260711_1200", pinned_path=pin)
    assert "## 📌 保送" in md
    body = _section_body(md)
    assert "600000" in body and "手工票" in body and "长期观察仓" in body and "Hold" in body


def test_pinned_section_missing_card_shows_placeholder(tmp_path):
    """kept 票不在 rows(finalists)里 / 无卡 → 评级占位 ⚠️无卡,不崩。"""
    d = _min_scan(tmp_path)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([
        {"code": "600002", "note": "还没出卡", "expires": "2099-01-01"},
    ]), encoding="utf-8")
    md = build_summary(d, "2026-07-11", "1200", "20260711_1200", pinned_path=pin)
    body = _section_body(md)
    assert "600002" in body and "⚠️无卡" in body


def test_pinned_section_expired_entry_shown_as_footnote(tmp_path):
    d = _min_scan(tmp_path)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([
        {"code": "600001", "note": "老票该清了", "added": "2026-06-01", "expires": "2026-07-01"},
    ]), encoding="utf-8")
    md = build_summary(d, "2026-07-11", "1200", "20260711_1200", pinned_path=pin)
    assert "## 📌 保送" in md
    body = _section_body(md)
    assert "已过期" in body and "600001" in body and "老票该清了" in body


def test_no_pinned_section_when_no_pinned_json(tmp_path):
    md = build_summary(_min_scan(tmp_path), "2026-07-11", "1200", "20260711_1200",
                       pinned_path=tmp_path / "nope.json")
    assert "📌 保送" not in md


def test_no_pinned_section_even_with_pinned_lane_row_if_no_pinned_json(tmp_path):
    """gating 信号 = load_pinned() 结果,不是 finalists 里恰好存在的 lane=pinned 行
    (例如陈旧/手改数据)——presence-gate 单一事实源是 pinned.json 本身。"""
    d = _scan_with_pinned_finalist(tmp_path)
    md = build_summary(d, "2026-07-11", "1200", "20260711_1200",
                       pinned_path=tmp_path / "nope.json")
    assert "📌 保送" not in md


def test_no_pinned_section_default_path_is_parity(tmp_path):
    """不传 pinned_path(真实生产签名)→ 本仓无真实 pinned.json,行为与显式缺文件一致。"""
    md = build_summary(_min_scan(tmp_path), "2026-07-11", "1200", "20260711_1200")
    assert "📌 保送" not in md
    assert "## 1. 漏斗(数量)" in md          # 其余照旧
