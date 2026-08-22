"""低位转强三段计数(2026-08-22 批 A)——纯函数 + prelude 汇总屏行。

`_lowturn_counts` 不取数、不跑 `run`:合成三个小帧即可。它存在的理由是 2026-08-21 首跑
「全帧 120 → L1 17 → L2 0」整天没人看见(design §3.4)。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.turnup import LOWTURN_DEFAULTS
from autoresearch.scan.universe import _lowturn_counts


def _row(code, *, pct_5d=2.0, name="甲"):
    """默认 = 旗亮行(与 test_lowturn_channel._row 同一组阈值)。"""
    return dict(code=code, name=name, dist_high_60=-30.0, pct_60d=0.0, pct_5d=pct_5d,
                vol_ratio_20=2.0, above_ma20=1.0, ma5_gt_ma10=1.0,
                main_inflow_yi=1.0, cmf_20=0.1, main_net_ratio=0.1)


def test_counts_three_stages():
    scored = pd.DataFrame([_row(f"{i:06d}") for i in range(5)]
                          + [_row(f"9{i:05d}", pct_5d=-1.0) for i in range(3)])
    recall = pd.DataFrame([_row(f"{i:06d}") for i in range(3)])
    l2 = pd.DataFrame([_row("000000")])
    assert _lowturn_counts(scored, recall, l2, LOWTURN_DEFAULTS) == {
        "lowturn_full": 5, "lowturn_l1": 3, "lowturn_l2": 1}


def test_empty_and_none_frames_are_zero_not_crash():
    got = _lowturn_counts(pd.DataFrame(), None, pd.DataFrame(), LOWTURN_DEFAULTS)
    assert got == {"lowturn_full": 0, "lowturn_l1": 0, "lowturn_l2": 0}


def test_missing_columns_degrade_to_zero_without_raising():
    bad = pd.DataFrame([{"code": "000001", "name": "甲"}])
    got = _lowturn_counts(bad, bad, bad, LOWTURN_DEFAULTS)
    assert got == {"lowturn_full": 0, "lowturn_l1": 0, "lowturn_l2": 0}


# ── prelude 汇总屏行(纯函数,真断言)─────────────────────────────────────

from autoresearch.scan.prelude import lowturn_line, universe_line   # noqa: E402

_BASE = {"universe": 4285, "recall_n": 1000, "l2_n": 202,
         "l2_engine": "stratified(sn_composite)"}


def test_universe_line_shows_three_stage_counts_and_warns_on_zero():
    line = universe_line({**_BASE, "lowturn_full": 120, "lowturn_l1": 17, "lowturn_l2": 0})
    assert line.startswith("L0 4285 → 召回 1000 → L2 202(stratified(sn_composite))")
    assert "lowturn 全帧 120 → L1 17 → L2 0" in line
    assert "⚠️L2 零到货" in line


def test_universe_line_no_warn_when_delivered():
    line = universe_line({**_BASE, "lowturn_full": 120, "lowturn_l1": 40, "lowturn_l2": 8})
    assert "lowturn 全帧 120 → L1 40 → L2 8" in line and "⚠️" not in line


def test_universe_line_parity_without_keys():
    """两把开关全关 → 三键缺 → 行与本波之前逐字相同。"""
    assert universe_line(_BASE) == "L0 4285 → 召回 1000 → L2 202(stratified(sn_composite))"
    assert lowturn_line(_BASE) == ""


def test_lowturn_line_no_warn_when_market_has_no_flag():
    assert "⚠️" not in lowturn_line({"lowturn_full": 0, "lowturn_l1": 0, "lowturn_l2": 0})


def test_lowturn_line_renders_none_as_question_mark():
    """观测腿算不出 → None → 印 `?`,不冒充 0(「没量到」≠「量到 0」)。"""
    out = lowturn_line({"lowturn_full": 120, "lowturn_l1": None, "lowturn_l2": 3})
    assert "L1 ?" in out
