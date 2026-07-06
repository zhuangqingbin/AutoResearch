"""早停抽检对象挑选:确定性随机(seed=日期),只抽早停卡、排除复用卡。合成,无网络。

spec: 2026-07-05 wave §WS-A3(opt-in)——23 张早停弃单无人复核的单边质检补口。
"""
from __future__ import annotations

from autoresearch.scan.agents.l4_card import pick_earlystop_audit

_STOP = "# 决策卡\n**Rubric建议**: … ｜ 早停因:x → **建议 Hold**\n**Rating**: Hold\n"
_REUSE = "♻️ 复用 2026-07-01 卡\n" + _STOP
_FULL = "# 决策卡\n进入P4倾向: Hold\n**Rating**: Hold\n"


def _mk(root):
    d = root / "2026-07-03" / "details"
    d.mkdir(parents=True)
    for c, t in [("000001", _STOP), ("000002", _STOP), ("000003", _STOP),
                 ("000004", _REUSE), ("000005", _FULL)]:
        (d / f"{c}.md").write_text(t, encoding="utf-8")
    return root / "2026-07-03"


def test_pick_earlystop_deterministic_and_filters(tmp_path):
    sd = _mk(tmp_path)
    picks = pick_earlystop_audit(sd, k=2)
    assert len(picks) == 2
    assert set(picks) <= {"000001", "000002", "000003"}          # 复用/满卡不抽
    assert picks == pick_earlystop_audit(sd, k=2)                # 同日同 seed 确定性
    assert pick_earlystop_audit(tmp_path / "nope", k=2) == []
