"""决策级噪声地板与等价检验(2026-10-03 B1 / 复盘稿 §6.3)。

降 effort 要过「决策级一致」门(用户 09-27 裁定):同一冻结任务包、同一配置重跑 k 次的自一致率
就是噪声地板;候选配置与基线的交叉一致率不得低于地板减边际。没有地板,任何「变了 / 没变」都无从说起。
"""
from __future__ import annotations

import pytest

from autoresearch.research import noise_floor as nf


def _card(rating, *, entry="条件(站上 10 元)", early=False):
    proposal = {"Buy": "BUY", "Overweight": "BUY", "Hold": "HOLD", "Underweight": "SELL",
                "Sell": "SELL"}[rating]
    stop = "**早停**: 停于 P3 ｜ 停因:估值透支\n" if early else ""
    return (f"# 决策卡\n{stop}**Rating**: {rating}\n**入场**: {entry}\n"
            f"FINAL TRANSACTION PROPOSAL: **{proposal}**\n")


def test_decision_bits_read_the_card_like_production_does():
    bits = nf.decision_bits(_card("Underweight", entry="禁止", early=True))
    assert bits == {"rating": "Underweight", "proposal": "SELL", "entry": "PROHIBITED",
                    "early_stop": True, "veto": True}
    assert nf.decision_bits("no anchors")["rating"] is None        # 读不出 = None,不猜


def test_self_agreement_is_the_noise_floor():
    reps = {"t1": [nf.decision_bits(_card("Hold")), nf.decision_bits(_card("Hold")),
                   nf.decision_bits(_card("Underweight", entry="禁止"))],
            "t2": [nf.decision_bits(_card("Hold")), nf.decision_bits(_card("Hold"))]}
    got = nf.self_agreement(reps)
    # t1 三对里一对一致(1/3),t2 一对一致(1/1);按任务等权 = (1/3 + 1) / 2
    assert got["rating_exact"] == pytest.approx((1 / 3 + 1) / 2)
    assert got["n_tasks"] == 2


def test_cross_agreement_pairs_every_baseline_rep_with_every_candidate_rep():
    base = {"t1": [nf.decision_bits(_card("Hold")), nf.decision_bits(_card("Hold"))]}
    cand = {"t1": [nf.decision_bits(_card("Hold")), nf.decision_bits(_card("Underweight"))]}
    assert nf.cross_agreement(base, cand)["rating_exact"] == pytest.approx(0.5)


def test_equivalence_needs_cross_within_margin_of_the_floor_and_enough_tasks():
    floor = {"rating_exact": 0.80, "veto_exact": 0.90, "entry_exact": 0.70, "n_tasks": 40}
    ok = {"rating_exact": 0.75, "veto_exact": 0.88, "entry_exact": 0.66, "n_tasks": 40}
    assert nf.equivalence(floor, ok, margin=0.10, min_tasks=20)["verdict"] == "EQUIVALENT"
    worse = {**ok, "veto_exact": 0.70}
    assert nf.equivalence(floor, worse, margin=0.10, min_tasks=20)["verdict"] == "NOT_EQUIVALENT"
    small = {**ok, "n_tasks": 8}
    assert nf.equivalence(floor, small, margin=0.10, min_tasks=20)["verdict"] == "INSUFFICIENT"


def test_load_reads_task_directories_of_replayed_cards(tmp_path):
    for task, ratings in {"600001": ["Hold", "Hold"], "600002": ["Sell"]}.items():
        (tmp_path / task).mkdir()
        for i, rating in enumerate(ratings):
            (tmp_path / task / f"r{i}.md").write_text(_card(rating), encoding="utf-8")
    got = nf.load(tmp_path)
    assert sorted(got) == ["600001", "600002"] and len(got["600001"]) == 2
