from __future__ import annotations

from autoresearch.session_agent.roles import get_role, role_stage


def test_full_logical_roles_reuse_the_existing_playbook():
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
            ".claude/skills/stock-research/engine-playbook.md"
        ]
        assert role_stage(role) in {"write", "intel"}
