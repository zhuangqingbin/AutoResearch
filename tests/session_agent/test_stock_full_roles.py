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
