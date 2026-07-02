"""行业备忘录:upsert 覆盖/单行/块 cap/缺省降级 + L4 简报注入。合成,无网络。

spec: docs/specs/2026-07-02-scan-portfolio-memory-design.md §4
"""
from __future__ import annotations

from autoresearch.learning.sector_memo import (
    load_memos,
    render_memo_block,
    render_memo_line,
    upsert_memo,
)


def test_upsert_and_load(tmp_path):
    p = tmp_path / "memos.jsonl"
    upsert_memo("半导体", "fwd PE 常年 100+;CFO 门高频", "2026-07-02", path=p)
    upsert_memo("电力", "低估值防御;股息为锚", "2026-07-02", path=p)
    upsert_memo("半导体", "fwd PE 常年 100+;CFO 门高频;解禁潮 Q3", "2026-07-31", path=p)
    m = load_memos(p)
    assert len(m) == 2 and "解禁潮" in m["半导体"]["memo"]          # 覆盖式 upsert
    assert m["半导体"]["updated"] == "2026-07-31"


def test_render_line_and_block(tmp_path):
    p = tmp_path / "memos.jsonl"
    upsert_memo("半导体", "fwd PE 常年 100+", "2026-07-02", path=p)
    ln = render_memo_line("半导体", path=p)
    assert "行业备忘录" in ln and "历史事实非预判" in ln
    assert render_memo_line("白酒", path=p) == ""
    assert render_memo_line(None, path=p) == ""
    blk = render_memo_block(["半导体", "白酒", "半导体"], path=p)
    assert "非方向指令" in blk and blk.count("半导体") == 1          # 去重
    assert render_memo_block(["白酒"], path=p) == ""


def test_brief_injects_memo(tmp_path, monkeypatch):
    import pandas as pd

    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    d = tmp_path / "2026-07-02"
    d.mkdir()
    pd.DataFrame([{"code": "000001", "name": "甲", "industry": "半导体"}]).to_csv(
        d / "L1_recall_top1000.csv", index=False)
    import autoresearch.learning.sector_memo as sm
    monkeypatch.setattr(sm, "_DEFAULT", tmp_path / "memos.jsonl")
    upsert_memo("半导体", "fwd PE 常年 100+", "2026-07-02", path=tmp_path / "memos.jsonl")
    s = compose_funnel_brief("000001", d)
    assert "行业备忘录" in s and "fwd PE 常年 100+" in s
