from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("skill", ["stock-research", "macro-research", "sector-research", "scan-market"])
def test_skills_bind_actionable_conclusions_to_overnight_frame(skill):
    text = (ROOT / ".claude/skills" / skill / "SKILL.md").read_text()
    assert "gap_c1_o2" in text
    assert "FULL/LITE 仅表示研究深度" in text


def test_intel_and_full_templates_have_no_competing_holding_clock():
    intel = (ROOT / ".claude/agents/l4-intel.md").read_text()
    full = (ROOT / ".claude/skills/stock-research/engine-playbook.md").read_text()
    skill = (ROOT / ".claude/skills/stock-research/SKILL.md").read_text()
    assert "D+1 开盘买 → D+2 收盘卖" not in intel
    assert "knowledge_cutoff" in intel
    assert "**Time Horizon**: 隔夜" in full
    assert "full 深研报告不受此限" not in skill


def test_card_uses_entry_denominator_and_records_exit_breach():
    text = (ROOT / ".claude/agents/l4-card.md").read_text()
    assert "对现价±%" not in text
    assert "assumed_entry_price" in text
    assert "holding_window_breached" in text
    assert "按目标带持有/减仓判断" not in text

