"""T7·纸面法庭+提案 nag:_proposals_nag 帮手 + summary_line 影子语义标注。

nag 的 build_summary 接线与 paper_nav 成绩单同姿势(仅真实现场 scan_dir==context/scan/<date> 注入);
行渲染/排序/标注(龄·配对·疑失效)委托 feedback_store.proposals_nag_lines(看板自清洁,机器只整理
不裁决)。测试用 set_root(tmp) 隔离(照 test_prompt_patch.py),不受开发机真 proposals.jsonl 污染。
"""
from __future__ import annotations

import json

import pytest

import autoresearch.learning.feedback_store as fs
from autoresearch.scan import assemble


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path):
    old = fs.KNOW
    fs.set_root(tmp_path / "knowledge")
    yield
    fs.set_root(old)


def _write_ledger(text: str) -> None:
    p = fs._f(fs._PROPOSALS)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_proposals_nag_lists_open_only():
    _write_ledger(
        json.dumps({"id": "pr_20260701_001", "ts": "2026-07-01T10:00:00", "status": "open",
                    "kind": "factor", "summary": "融资强度入组"}) + "\n"
        + json.dumps({"id": "pr_20260702_001", "ts": "2026-07-02T10:00:00", "status": "resolved",
                      "kind": "quota", "summary": "heat 降额"}) + "\n"
        + "not-json\n",
    )
    out = assemble._proposals_nag()
    assert out.startswith("## ⏳ 待裁决提案")
    assert "pr_20260701_001" in out and "融资强度入组" in out
    assert "pr_20260702_001" not in out            # resolved 不列
    assert "[factor·" in out                        # 看板标注(kind·龄)进了行
    assert "20 交易日" in out                        # 裁决节奏提醒行(原文保留)


def test_proposals_nag_missing_or_no_open_is_blank():
    assert assemble._proposals_nag() == ""          # 账本缺 → 原行为(空,parity)
    _write_ledger(json.dumps({"id": "pr_20260703_001", "ts": "2026-07-03T10:00:00",
                              "status": "resolved", "kind": "x", "summary": "s"}) + "\n")
    assert assemble._proposals_nag() == ""          # 无 open → 原行为(空)


def test_summary_line_annotates_shadow_semantics():
    import pandas as pd

    from autoresearch.learning import paper_nav
    days = ["2026-07-01", "2026-07-02"]
    nav = pd.Series([1.0, 1.01], index=days)
    line = paper_nav.summary_line(days, nav, nav, nav, 1, 2)
    assert "若门不拦最想买" in line                 # 影子=反事实买单的语义标注(纸面法庭一等公民)
