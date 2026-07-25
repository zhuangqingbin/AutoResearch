"""早停记账:0买真机制是早停(07-21 实测 12 卡中 6 张早停、仅 2 张可解析三门),此前零计量。"""
from __future__ import annotations

import json

from autoresearch.scan import assemble

_EARLY = """# 决策卡 — 000651 格力电器 @ 2026-07-25  ·  〔早停·表面 DD〕
**Rubric建议**: 表面4维净分 -1/4 ｜ 早停因:资金派发无催化 → **建议 Hold**
**Rating**: Hold
**早停**: 停于 P3 ｜ 停因:资金流出
FINAL TRANSACTION PROPOSAL: **HOLD**
"""

_FULL = """# 决策卡 — 300857 协创数据 @ 2026-07-25
**Rubric建议**(评分卡派生): 6 维净分 +2/6 ｜ OW三门 主力真在 ✓·业绩真兑现 ✓·估值不透支 ✓ → **建议 Overweight**
**Rating**: Overweight
FINAL TRANSACTION PROPOSAL: **BUY**
"""


def test_parse_early_stop_reads_phase_and_reason():
    got = assemble.parse_early_stop(_EARLY)
    assert got == {"phase": "P3", "reason": "资金流出"}


def test_full_card_has_no_early_stop():
    assert assemble.parse_early_stop(_FULL) is None


def test_unknown_reason_falls_back_to_other():
    text = _EARLY.replace("停因:资金流出", "停因:老板长得不行")
    assert assemble.parse_early_stop(text) == {"phase": "P3", "reason": "其他"}


def test_write_early_stop_json(tmp_path):
    d = tmp_path / "2026-07-25"
    (d / "details").mkdir(parents=True)
    (d / "details" / "000651.md").write_text(_EARLY, encoding="utf-8")
    (d / "details" / "300857.md").write_text(_FULL, encoding="utf-8")
    got = assemble.write_early_stop(d)
    assert got == {"000651": {"phase": "P3", "reason": "资金流出"}}
    on_disk = json.loads((d / "_early_stop.json").read_text(encoding="utf-8"))
    assert on_disk == got
