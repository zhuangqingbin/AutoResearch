#!/usr/bin/env python3
"""ResearchCard → 卡面锚点(D3):JSON 是事实源,Markdown 是派生展示。派生报告**不反写** initial_rating。"""
from __future__ import annotations

from autoresearch.contracts.profiles import (
    CURRENT_CARD_RULES,
    LEGACY_CARD_RULES,
    validate_card_rules_version,
)
from autoresearch.contracts.research_card import validate_card
from autoresearch.scan.l4.rubric import rubric_rating, validate_card_decision

COMPARE_KEYS: tuple[str, ...] = (
    "rating", "proposal", "gate_states", "early_stop", "evidence_refs", "holding", "target", "rr",
)


def render_anchors(card: dict, *, rules_version: str = CURRENT_CARD_RULES, rating_bands: dict | None = None) -> str:
    """最小机读锚点:Rating / FINAL TRANSACTION PROPOSAL / Rubric / 置信度(+ 偏离 / 早停)。

    评级与 rubric 建议不一致又没写偏离理由 → 抛错:卡片自检规则(`rubric.py` docstring)在
    JSON 时代的等价物。三门 PASS/FAIL/UNKNOWN → rubric 需要的「通过」bool 在这里显式转,
    UNKNOWN 按未通过(保守)。
    """
    validate_card_rules_version(rules_version)
    if rules_version == LEGACY_CARD_RULES:
        validate_card(card)
        rating, reason = rubric_rating(card["dimensions"],
                                       {key: state == "PASS" for key, state in card["gates"].items()})
        if card["initial_rating"] != rating and not card["rating_deviation_reason"].strip():
            raise ValueError("rating deviation requires explicit justification")
    else:
        rating, reason = validate_card_decision(card, bands=rating_bands)
    lines = [
        f"**Rating**: {card['initial_rating']}",
        f"FINAL TRANSACTION PROPOSAL: {card['proposal']}",
        f"Rubric: {rating} — {reason}",
        f"置信度:{card['confidence'] or '未核'}",
    ]
    if card["rating_deviation_reason"]:
        lines.append(f"**偏离**: {card['rating_deviation_reason']}")
    if card["early_stop"]:
        early = card["early_stop"]
        lines.append(f"**早停**:停于 {early['phase']} ｜ 停因:{early['reason']}")
    if card["schema_version"] == 2:
        import json

        from autoresearch.common.card_decision import DECISION_FIELDS
        lines.extend(["```research-decision-v2", json.dumps({key: card[key] for key in sorted(DECISION_FIELDS)}, ensure_ascii=False), "```"])
        if card["scenario_estimate"] is not None:
            lines.extend(["```conditional-scenarios-v1", json.dumps(card["scenario_estimate"], ensure_ascii=False), "```"])
    return "\n".join(lines) + "\n"


def compare_facts(legacy: dict, candidate: dict) -> dict:
    """legacy 行 × 候选事实 → 只列**不一致**的键;旧解析器不支持的键记 unsupported_in_legacy,
    不编空值宣称完全一致。"""
    out = {}
    for key in COMPARE_KEYS:
        if key not in legacy:
            out[key] = {"legacy": "unsupported_in_legacy", "candidate": candidate.get(key)}
        elif legacy.get(key) != candidate.get(key):
            out[key] = {"legacy": legacy.get(key), "candidate": candidate.get(key)}
    return out
