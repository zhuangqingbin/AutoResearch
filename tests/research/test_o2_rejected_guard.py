"""O2「52 周高距离族」否决守卫 —— design 2026-08-03 §3.2。

正式结论(`docs/research/2026-07-18-dist-high-252-ic.md`):**不过线**。
全样本 rank-IC −0.0023、两半反号、risk_off IC −0.082(t=−2.29)。
`t≈+2.62` 只表示它优于**更差的** `pct_60d`,不表示绝对有效 —— 这正是最容易复活它的误读
(记忆里那条「52周高侧:家族内胜 pct_60d(t+2.62)可作 momentum 换代候选」)。

守卫的职责很窄:**不许它在没走重开条件的情况下悄悄回到生产打分路径**。
它不阻止研究,只阻止「顺手加进 composite」。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common import scoring
from autoresearch.research import candidates as cd
from autoresearch.scan.recall.l2_stratify import DEFAULT_FLOORS

# 该家族在代码里可能出现的名字(新起一个别名规避守卫也算违规,故列全)
O2_TOKENS = ("dist_high_252", "dist_high", "high_252", "week52_high", "dist52")


def _o2() -> cd.Candidate:
    return next(c for c in cd.CANDIDATES if c.id == "O2_dist_high_252")


def test_o2_status_is_rejected():
    assert _o2().status == "REJECTED"


def test_o2_reopen_conditions_are_all_four():
    """四条缺一不可;少写一条就等于把门放宽。"""
    text = _o2().reopen_conditions
    for phrase in ("top-decile", "OOS", "risk_off", "成熟门"):
        assert phrase in text, phrase


def test_o2_is_not_a_near_term_candidate():
    """§3.2:删除「替换判据/新增 floor 桶」的近期候选资格。"""
    assert _o2().priority == "P2"


def test_o2_family_is_absent_from_composite_factor_groups():
    frame = pd.DataFrame({"code": ["000001"], "pct_60d": [1.0], "industry": ["电子"]})
    groups = set(scoring._factor_groups(frame))
    for token in O2_TOKENS:
        assert not any(token in g for g in groups), token


def test_o2_family_is_absent_from_prior_weights():
    weights = scoring._PRIOR_WEIGHTS["weights"]["__global__"]
    for token in O2_TOKENS:
        assert not any(token in k for k in weights), token


def test_o2_family_has_no_l2_floor_bucket():
    """§3.2 明令删除「新增 floor 桶」的候选资格。"""
    for token in O2_TOKENS:
        assert not any(token in bucket for bucket in DEFAULT_FLOORS), token


def test_o2_family_is_not_a_registered_recall_channel():
    from autoresearch.scan.recall import registered_channels

    for token in O2_TOKENS:
        assert not any(token in name for name in registered_channels()), token


def test_the_t_value_readback_trap_is_documented():
    """把「优于更差的 pct_60d」读成「有效」正是复活它的路径 —— 注释里必须写着。"""
    assert "pct_60d" in _o2().note and "不表示绝对有效" in _o2().note
