from __future__ import annotations

from autoresearch.session_agent.roles import get_role, role_stage


def test_full_logical_roles_load_only_their_own_contract_sections():
    for role in (
        "stock.quality",
        "stock.valuation",
        "stock.positioning",
        "stock.peer",
        "stock.solvency",
        "stock.reality_check",
        "stock.risk",
    ):
        spec = get_role(role)
        assert spec["instruction_refs"] == [
            ".claude/agents/stock-full.md"
        ]
        assert spec["context_policy"] == "INDEPENDENT"
        assert spec["config_role"] == "stock_full"
        assert spec["instruction_section"] == role
        assert role_stage(role) in {"write", "intel"}


def test_common_contract_announces_the_confidence_line_the_section_validator_checks():
    # validation._stock_section rejects any stock.section.v1 output without a literal
    # 「置信度:」/「置信度：」; the frozen common section is all a section agent is told, so it
    # must ask for that line. 2026-10-03 first Claude A-share FULL run: a 33KB market section
    # with a 置信度汇总 table was BLOCKED as CONTRACT_ERROR and the whole run was lost.
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".claude/agents/stock-full.md").read_text(encoding="utf-8")
    common = text.split("\n## common\n", 1)[1].split("\n## ", 1)[0]
    assert "置信度:" in common or "置信度：" in common


def test_pm_contract_lists_the_dimension_and_gate_vocabulary_the_card_validator_accepts():
    # The dispatch sample only shows 「未核」/「UNKNOWN」; 2026-10-03 the FULL PM wrote
    # 正面/中性/负面 and was rejected with "invalid dimension". The vocabulary must come from
    # the contract, mirroring l4-card's card spec.
    from pathlib import Path

    from autoresearch.contracts.agent_output import DIMENSION_LEVELS, GATE_STATES

    text = (Path(__file__).resolve().parents[2] / ".claude/agents/stock-full.md").read_text(encoding="utf-8")
    pm = text.split("\n## stock.pm\n", 1)[1].split("\n## ", 1)[0]
    assert "|".join(DIMENSION_LEVELS) in pm
    assert "|".join(GATE_STATES) in pm
