import json

import pytest

from autoresearch.contracts.agent_output import OW_GATES, RUBRIC_DIMENSIONS


def decision_text(
    *,
    subject="NVDA",
    venue="XNAS",
    rating="Buy",
    gates=None,
    holding=False,
    dimensions=None,
    deviation="",
):
    data = {
        "schema_version": 2,
        "subject": subject,
        "venue": venue,
        "dimensions": dimensions or dict.fromkeys(RUBRIC_DIMENSIONS, "强"),
        "gates": gates or dict.fromkeys(OW_GATES, "PASS"),
        "early_stop": None,
        "rating_deviation_reason": deviation,
        "holding": holding,
    }
    return f"**Rating**: {rating}\nFINAL TRANSACTION PROPOSAL: {'BUY' if rating in {'Buy', 'Overweight'} else 'HOLD'}\n```research-decision-v2\n{json.dumps(data, ensure_ascii=False)}\n```\n"


def test_cross_market_card_preserves_identity_and_rating():
    from autoresearch.common.card_decision import card_from_decision_text, validate_card_decision

    card = card_from_decision_text(
        decision_text(), subject="NVDA", venue="XNAS", analysis_date="2026-09-30"
    )
    assert "code" not in card
    assert card["subject"] == "NVDA"
    assert validate_card_decision(card)[0] == "Buy"


@pytest.mark.parametrize("state", ["FAIL", "UNKNOWN"])
def test_cross_market_buy_cannot_bypass_gates(state):
    from autoresearch.common.card_decision import card_from_decision_text, validate_card_decision

    card = card_from_decision_text(
        decision_text(gates=dict.fromkeys(OW_GATES, state)),
        subject="NVDA",
        venue="XNAS",
        analysis_date="2026-09-30",
    )
    with pytest.raises(ValueError, match="three"):
        validate_card_decision(card)


def test_holding_unknown_quality_and_justified_deviation():
    from autoresearch.common.card_decision import card_from_decision_text, validate_card_decision

    dims = dict.fromkeys(RUBRIC_DIMENSIONS, "中")
    text = decision_text(rating="Overweight", dimensions=dims, deviation="独立新订单证据")
    card = card_from_decision_text(text, subject="NVDA", venue="XNAS", analysis_date="2026-09-30")
    assert validate_card_decision(card)[0] == "Hold"
    card["holding"] = True
    card["dimensions"]["偿付"] = "未核"
    with pytest.raises(ValueError, match="QUALITY_UNREVIEWED"):
        validate_card_decision(card)


def test_scan_v3_uses_same_card_contract_but_v2_keeps_anchor_parser():
    from autoresearch.scan.l4.card_io import card_from_text

    text = decision_text(subject="600519", venue="XSHG")
    card = card_from_text(
        text,
        code="600519",
        analysis_date="2026-09-13",
        holding=False,
        rules_version="skills-gap-v3",
    )
    assert card["schema_version"] == 2
    legacy = card_from_text(
        "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**",
        code="600519",
        analysis_date="2026-09-13",
        holding=False,
        rules_version="skills-gap-v2",
    )
    assert legacy["schema_version"] == 1
    with pytest.raises(ValueError, match="research-decision-v2"):
        card_from_text(
            "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**",
            code="600519",
            analysis_date="2026-09-13",
            holding=False,
            rules_version="skills-gap-v3",
        )


def test_decision_instruction_states_the_early_stop_shape_it_validates():
    """A real model only sees this text: the non-null early_stop shape must be spelled out."""
    from autoresearch.common.card_decision import (
        card_from_decision_text,
        decision_instruction,
        validate_card_decision,
    )
    from autoresearch.contracts.agent_output import EARLY_STOP_PHASES, STOP_REASONS

    text = decision_instruction(subject="603893.SS", venue="XSHG")
    assert '"phase"' in text and '"reason"' in text
    assert all(phase in text for phase in EARLY_STOP_PHASES)
    assert all(reason in text for reason in STOP_REASONS)
    # The shape exactly as instructed passes the parser + validator.
    data = {"schema_version": 2, "subject": "603893.SS", "venue": "XSHG",
            "dimensions": dict.fromkeys(RUBRIC_DIMENSIONS, "中"),
            "gates": dict.fromkeys(OW_GATES, "UNKNOWN"),
            "early_stop": {"phase": "P3", "reason": "资金流出"},
            "rating_deviation_reason": "", "holding": False}
    card_text = ("**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: HOLD\n```research-decision-v2\n"
                 + json.dumps(data, ensure_ascii=False) + "\n```\n")
    card = card_from_decision_text(card_text, subject="603893.SS", venue="XSHG",
                                   analysis_date="2026-09-30")
    assert validate_card_decision(card)[0] == "Hold"
