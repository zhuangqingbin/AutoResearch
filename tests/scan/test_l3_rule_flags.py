"""L3 规则旗的确定性预计算(2026-10-03 B4,影子:只记账,不进 L3 输入、不改任何选择)。

L3 契约里有一半是数字规则(② 资金三同向、B 下跌趋势 / 深跌落刀、H 当日大涨、⑤ 获利盘 > 90),
今天全靠模型逐只判断。先把它们按同一口径算成列,量模型的选择与这些旗的一致程度 —— Q5「先量噪声
地板与截面读数,再决定 L3 去向」的第一块砖。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.scan.l3 import rule_flags as rf


def _frame(**cols) -> pd.DataFrame:
    base = {"code": ["600001", "600002", "600003", "600004"],
            "main_net_ratio": [0.1, -0.2, 0.05, None], "cmf_20": [0.2, -0.1, -0.1, 0.1],
            "obv_mom_20": [0.3, -0.2, 0.2, 0.1], "above_ma20": [1, 0, 1, 1],
            "above_ma60": [1, 0, 1, 1], "pct_60d": [10, -25, 5, 0], "pct_1d": [1, -2, 9.8, 0],
            "winner_rate": [50, 5, 95, 40]}
    base.update(cols)
    return pd.DataFrame(base)


NO_LOWTURN = [False, False, False, False]


def test_flags_follow_the_numeric_part_of_the_l3_contract():
    got = rf.compute_flags(_frame(lowturn=NO_LOWTURN)).set_index("code")
    assert list(got.loc["600001", list(rf.FLAG_NAMES)]) == [True, False, False, False, False]
    assert got.loc["600002", "downtrend_b"] and got.loc["600002", "knife_b"]
    assert got.loc["600003", "chase_h"] and got.loc["600003", "fragile_winner"]
    assert not got.loc["600003", "funds_aligned"]                  # cmf 为负:不是三同向


def test_an_unknown_input_is_unknown_not_false():
    got = rf.compute_flags(_frame()).set_index("code")
    assert pd.isna(got.loc["600004", "funds_aligned"])             # 主力净占比缺:答不出


def test_the_lowturn_exception_clears_the_downtrend_flag_only():
    got = rf.compute_flags(_frame(lowturn=[False, True, False, False])).set_index("code")
    assert not got.loc["600002", "downtrend_b"]                    # 契约 B 的例外
    assert got.loc["600002", "knife_b"]                            # 深跌落刀不享受这条例外


def test_an_unknown_lowturn_exception_leaves_a_lit_downtrend_flag_unknown():
    """复审 M-2:L1 帧没有 lowturn 列;当成「没有例外」会把契约豁免的票也算成 B。"""
    got = rf.compute_flags(_frame()).set_index("code")
    assert pd.isna(got.loc["600002", "downtrend_b"])
    assert got.loc["600001", "downtrend_b"] is False or not got.loc["600001", "downtrend_b"]
    labelled = rf.compute_flags(_frame(lowturn=["", "转强", None, ""])).set_index("code")
    assert not labelled.loc["600002", "downtrend_b"]               # L3 表的「转强」标签 = 例外
    series = rf.compute_flags(_frame(), pd.Series([False, True, False, False])).set_index("code")
    assert not series.loc["600002", "downtrend_b"]


def test_readout_recomputes_the_exception_from_the_runs_own_config(tmp_path, monkeypatch):
    import json

    from autoresearch.common import turnup

    staging = tmp_path / "20260929-0929_2213" / "trace" / "staging"
    staging.mkdir(parents=True)
    _frame().to_csv(staging / "L1_scored_full.csv", index=False)
    (staging / "_l3_judged.json").write_text(json.dumps(
        [{"code": "600002", "finalist": True}]), encoding="utf-8")
    assert rf.readout(tmp_path)[0]["downtrend_b_flagged"] == 0     # 没有合同:例外未知,不算亮

    contract = staging.parent / "run_contract.json"
    contract.write_text(json.dumps({"user_config": {"l3": {"lowturn": {"enabled": False}}}}),
                        encoding="utf-8")
    assert rf.readout(tmp_path)[0]["downtrend_b_finalist"] == 1    # 当天例外关着:B 照算

    contract.write_text(json.dumps({"user_config": {"l3": {"lowturn": {"enabled": True}}}}),
                        encoding="utf-8")
    monkeypatch.setattr(turnup, "lowturn_mask",
                        lambda frame, cfg=None: pd.Series([False, True, False, False], index=frame.index))
    assert rf.readout(tmp_path)[0]["downtrend_b_flagged"] == 0     # 当天亮了「转强」:豁免


def test_agreement_counts_how_the_model_treated_flagged_names():
    flags = rf.compute_flags(_frame(lowturn=NO_LOWTURN))
    judged = [{"code": "600001", "finalist": True}, {"code": "600002", "finalist": "False"},
              {"code": "600003", "finalist": "True"}, {"code": "600004", "finalist": False}]
    got = rf.agreement(flags, judged)
    assert got["chase_h"] == {"flagged": 1, "flagged_finalist": 1}  # 模型选了当日大涨票 = 违反 H
    assert got["downtrend_b"] == {"flagged": 1, "flagged_finalist": 0}
    assert got["funds_aligned_share"] == {"finalist": 0.5, "bench": 0.0}


def test_retest_jaccard_compares_finalist_sets():
    a = [{"code": "600001", "finalist": True}, {"code": "600002", "finalist": True}]
    b = [{"code": "600001", "finalist": "True"}, {"code": "600003", "finalist": True}]
    assert rf.retest_jaccard(a, b) == pytest.approx(1 / 3)
    assert rf.retest_jaccard([], []) is None


def test_agreement_survives_a_duplicated_code_row():
    """tushare 盘后半截快照会给同一 code 多一行(实测 20260826_2120)。"""
    frame = _frame(lowturn=NO_LOWTURN)
    flags = rf.compute_flags(pd.concat([frame, frame.iloc[[2]]], ignore_index=True))
    got = rf.agreement(flags, [{"code": "600003", "finalist": True}])
    assert got["chase_h"] == {"flagged": 1, "flagged_finalist": 1}
