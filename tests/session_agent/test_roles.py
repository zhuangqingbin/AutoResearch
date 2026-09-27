from __future__ import annotations

import pytest

from autoresearch.session_agent.roles import get_role, roles_hash


def test_roles_reference_existing_single_source_instructions():
    card = get_role("stock.card")
    # 2026-09-26 A2-6:lite-playbook 只剩指针,agent 文件是唯一真身
    assert card["instruction_refs"] == [".claude/agents/l4-card.md"]
    assert card["output_contract"] == "stock.lite.v1"
    assert get_role("scan.l3")["instruction_refs"] == [".claude/agents/l3-rank.md"]


def test_unknown_role_is_rejected_and_registry_hash_is_stable():
    with pytest.raises(KeyError):
        get_role("fake.model.client")
    assert roles_hash() == roles_hash()
    assert len(roles_hash()) == 64
