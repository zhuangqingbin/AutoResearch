"""render CLI:把 L5 才渲染的确定性表提前到跑动中随时可调(Wave5 ①)。"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan import render


def _mk(tmp_path, date="2026-07-25"):
    d = tmp_path / date
    (d / "details").mkdir(parents=True)
    return d


def test_menu_health_view(tmp_path):
    d = _mk(tmp_path)
    (d / "L2_gbdt_top200.csv").write_text(
        "code,industry,pct_60d,main_pos,cmf_20,pe\n"
        "000001,银行,5.0,1,0.1,6.0\n000002,地产,-30.0,0,-0.1,8.0\n", encoding="utf-8")
    (d / "L1_scored_full.csv").write_text(
        "code,industry,pct_60d,main_pos,cmf_20,pe\n"
        "000001,银行,5.0,1,0.1,6.0\n000002,地产,-30.0,0,-0.1,8.0\n"
        "000003,银行,-25.0,0,-0.2,9.0\n", encoding="utf-8")
    out = render.render_view("2026-07-25", "menu_health", root=tmp_path)
    assert "L2 菜单体检" in out
    assert "落刀面" in out


def test_gate_hist_view_counts_cards(tmp_path):
    d = _mk(tmp_path)
    (d / "finalists.csv").write_text("code,name,lane\n000651,格力电器,composite\n", encoding="utf-8")
    (d / "details" / "000651.md").write_text(
        "# 决策卡 — 000651\n"
        "**Rubric建议**: 6 维净分 +1/6 ｜ OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✓ → **建议 Hold**\n"
        "**Rating**: Hold\n", encoding="utf-8")
    out = render.render_view("2026-07-25", "gate_hist", root=tmp_path)
    assert "OW三门失守分布" in out
    assert "业绩真兑现✗ 1" in out
    assert "评级分布" in out and "Hold 1" in out


def test_timing_view_reads_stage_timing(tmp_path):
    d = _mk(tmp_path)
    (d / "_stage_timing.json").write_text(
        json.dumps({"L0L1L2": {"wall_s": 505}, "L3精排": {"wall_s": 1077}}), encoding="utf-8")
    out = render.render_view("2026-07-25", "timing", root=tmp_path)
    assert "L3精排" in out
    assert "17m57s" in out          # 1077s = 17m57s


def test_missing_artifacts_say_so_not_silent(tmp_path):
    """B 级降级必留痕:产物缺失时显式说「缺」,不返回空串装作没事。"""
    _mk(tmp_path)
    out = render.render_view("2026-07-25", "menu_health", root=tmp_path)
    assert "缺" in out


def test_unknown_view_raises(tmp_path):
    _mk(tmp_path)
    with pytest.raises(ValueError):
        render.render_view("2026-07-25", "nope", root=tmp_path)
