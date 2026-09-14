from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_project_entry_docs_point_both_subscription_hosts_to_session_v1():
    for name in ("AGENTS.md", "CLAUDE.md", "README.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "autoresearch.session_agent" in text, name
        assert "session_v1" in text, name


def test_four_user_skills_have_the_same_session_agent_control_loop():
    for skill in ("scan-market", "stock-research", "macro-research", "sector-research"):
        text = (ROOT / ".claude/skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "begin → next → claim" in text, skill
        assert "submit → finish" in text, skill
        assert "--orchestration session_v1" in text, skill
        assert "HOST_CAPABILITY_REQUIRED" in text, skill
        assert "LEGACY_ENTRYPOINT_REQUIRED" in text, skill


def test_dossier_agent_and_stock_legacy_entry_are_explicitly_labeled():
    dossier = (ROOT / ".claude/agents/dossier-init.md").read_text(encoding="utf-8")
    assert "dossier-init / INIT" in dossier
    assert "--orchestration session_v1" in dossier
    stock = (ROOT / ".claude/skills/stock-research/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "--legacy-reason" in stock
