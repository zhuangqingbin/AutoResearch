"""OW 三门结构化账本(建账):卡文『OW三门…』失守(gate_status True=失守)→ gate_fires.csv binding 行,
(date,check,code) 幂等。design: 漏斗 P0+P1 波 Task 2 —— 门只有轶事没有账,补上 gate_ledger 的原料。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning import self_review

# 门名与 ✓/✗ 必须紧邻(gate_status 解析口径,同 test_shadow_buys.py::_CARD/test_cross_calib.py::CARD)。
CARD = """# 决策卡 — 600000 测试 @ 2026-07-09
**Rubric建议**(评分卡派生): 6 维净分 +1/6 ｜ OW三门 主力真在✓·业绩真兑现✗·估值不透支✓ → **建议 Hold**
**Rating**: Hold
"""


def test_dump_ow_gate_fires_appends_binding_rows(tmp_path):
    d = tmp_path / "2026-07-09"
    (d / "details").mkdir(parents=True)
    (d / "details" / "600000.md").write_text(CARD, encoding="utf-8")
    n = self_review.dump_ow_gate_fires(d)
    assert n == 1
    df = pd.read_csv(d / "gate_fires.csv", dtype=str)   # code 需按 str 读(纯数字码会被推断成 int)
    row = df.iloc[-1]
    assert row["check"] == "OW三门·业绩真兑现" and row["code"] == "600000" and row["level"] == "binding"
    assert self_review.dump_ow_gate_fires(d) == 0     # 幂等


def test_dump_ow_gate_fires_no_details_dir_is_zero_not_crash(tmp_path):
    """presence-gated:缺 details/ → 0 行,不炸。"""
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    assert self_review.dump_ow_gate_fires(d) == 0
    assert not (d / "gate_fires.csv").exists()


def test_dump_ow_gate_fires_skips_passed_gates(tmp_path):
    """全过(✓)的门不落账——只有失守(✗)才是 binding fire。"""
    d = tmp_path / "2026-07-09"
    (d / "details").mkdir(parents=True)
    card = "**Rubric建议**: OW三门 主力真在✓·业绩真兑现✓·估值不透支✓ → **建议 Overweight**\n"
    (d / "details" / "000001.md").write_text(card, encoding="utf-8")
    assert self_review.dump_ow_gate_fires(d) == 0


def test_dump_ow_gate_fires_dedupes_same_key_within_one_call(tmp_path):
    """同次调用内两张卡巧合解出同一 (date,check,code)(如 000001.md + 000001.bak.md 同 stem 前缀)
    不应各记一行——`seen` 须随 rows 累积同步更新,不能只在调用开头从旧文件建一次。"""
    d = tmp_path / "2026-07-09"
    (d / "details").mkdir(parents=True)
    card = "**Rubric建议**: OW三门 主力真在✗·业绩真兑现✓·估值不透支✓ → **建议 Hold**\n"
    (d / "details" / "000001.md").write_text(card, encoding="utf-8")
    (d / "details" / "000001.bak.md").write_text(card, encoding="utf-8")   # split(".")[0] 同 code
    n = self_review.dump_ow_gate_fires(d)
    assert n == 1
    df = pd.read_csv(d / "gate_fires.csv", dtype=str)
    assert len(df) == 1
