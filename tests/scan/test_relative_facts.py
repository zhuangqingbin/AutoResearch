"""相对 BUY 事实读模型(brief ③ 与 summary 漏斗读数共用的唯一解析器;P0 低位转强波)。"""
from __future__ import annotations

from autoresearch.scan import brief, relative_facts as rf


def _doc(*, blocked=False):
    cands = [{"code": "002081", "name": "金螳螂", "sector": "装修装饰Ⅱ", "pinned": False,
              "eligible": True, "rank": 1, "relative_decision_score": 0.70,
              "faces": {}, "faces_missing": [], "research_rating": "Underweight",
              "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}},
             {"code": "600211", "name": "西藏药业", "sector": "生物制品", "pinned": False,
              "eligible": False, "rank": None, "relative_decision_score": None,
              "faces": {}, "faces_missing": [], "research_rating": "Underweight",
              "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}}]
    return {"mode": "active", "rule_version": "e6.v2.0", "ruler": "gap_c1_o2",
            "benchmark": {"market": {"column": "rel_gap_market", "n": 4276},
                          "sector": {"column": "rel_gap_sector", "n_sectors": 129}},
            "counts": {"candidates": 9, "eligible": 0 if blocked else 6},
            "candidates": cands,
            "buys": [] if blocked else [{"code": "002081", "basis": "relative", "rank": 1}],
            "blocked": blocked,
            "blocked_reasons": [{"reason": "hard_gate.no_redflag", "n": 9}] if blocked else []}


def test_missing_decision_is_explicitly_absent():
    assert rf.relative_facts(None) == {"present": False, "mode": None}


def test_buy_fields():
    out = rf.relative_facts(_doc())
    assert out["present"] and out["mode"] == "active" and not out["blocked"]
    assert out["code"] == "002081" and out["name"] == "金螳螂" and out["research_rating"] == "Underweight"
    assert out["rank"] == 1 and out["n_eligible"] == 6 and out["n_candidates"] == 9
    assert out["hard_reject"] == 1            # 不合格候选数
    assert out["abs_gap_status"] == "UNMEASURED" and out["ruler"] == "gap_c1_o2"


def test_blocked_fields():
    out = rf.relative_facts(_doc(blocked=True))
    assert out["blocked"] and out["blocked_reasons"] == ["hard_gate.no_redflag×9"]
    assert out["code"] is None


def test_brief_reexports_same_objects():
    """brief 不得再持有自己的一份常量/解析器 —— 同名属性必须是同一个对象(防两处各漂)。"""
    assert brief._relative_facts is rf.relative_facts
    assert brief.BANNED_RELATIVE_PHRASES is rf.BANNED_RELATIVE_PHRASES
    assert brief.WEAK_MARKET_PHRASE == rf.WEAK_MARKET_PHRASE == "弱市相对最优"
    assert brief.REL_MARKET_POPULATION is rf.REL_MARKET_POPULATION
    assert brief.DECISION_POOL_LABEL is rf.DECISION_POOL_LABEL
