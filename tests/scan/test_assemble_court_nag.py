"""T7·纸面法庭+提案 nag:_proposals_nag 帮手 + summary_line 影子语义标注。

nag 的 build_summary 接线与 paper_nav 成绩单同姿势(仅真实现场 scan_dir==context/scan/<date> 注入),
tmp_path 测试天然不受开发机真 proposals.jsonl 污染——此处只测帮手本体。
"""
from __future__ import annotations

import json

from autoresearch.scan import assemble


def test_proposals_nag_lists_open_only(tmp_path, monkeypatch):
    p = tmp_path / "proposals.jsonl"
    p.write_text(
        json.dumps({"id": "pr_x", "status": "open", "kind": "factor", "summary": "融资强度入组"}) + "\n"
        + json.dumps({"id": "pr_y", "status": "resolved", "kind": "quota", "summary": "heat 降额"}) + "\n"
        + "not-json\n",
        encoding="utf-8")
    monkeypatch.setattr(assemble, "_PROPOSALS_PATH", p)
    out = assemble._proposals_nag()
    assert out.startswith("## ⏳ 待裁决提案")
    assert "pr_x" in out and "融资强度入组" in out
    assert "pr_y" not in out                       # resolved 不列
    assert "20 交易日" in out                       # 裁决节奏提醒行


def test_proposals_nag_missing_or_no_open_is_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(assemble, "_PROPOSALS_PATH", tmp_path / "none.jsonl")
    assert assemble._proposals_nag() == ""
    p = tmp_path / "proposals.jsonl"
    p.write_text(json.dumps({"id": "pr_z", "status": "resolved", "kind": "x", "summary": "s"}) + "\n",
                 encoding="utf-8")
    monkeypatch.setattr(assemble, "_PROPOSALS_PATH", p)
    assert assemble._proposals_nag() == ""


def test_summary_line_annotates_shadow_semantics():
    import pandas as pd

    from autoresearch.learning import paper_nav
    days = ["2026-07-01", "2026-07-02"]
    nav = pd.Series([1.0, 1.01], index=days)
    line = paper_nav.summary_line(days, nav, nav, nav, 1, 2)
    assert "若门不拦最想买" in line                 # 影子=反事实买单的语义标注(纸面法庭一等公民)
