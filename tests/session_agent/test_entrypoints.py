from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_project_entry_docs_point_both_subscription_hosts_to_session_v1():
    for name in ("AGENTS.md", "CLAUDE.md", "README.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "autoresearch.session_agent" in text, name
        assert "session_v1" in text, name


def test_four_user_skills_have_the_same_session_agent_control_loop():
    """2026-09-26 A2-1:控制环只在 docs/session-agent/README.md 讲一遍;四个 SKILL.md 各留一行指针。

    此前四份 SKILL.md 逐字重复同一段(~500 token × 4,每场扫描都随 skill 装载);现在锚改为
    「指针在 + 唯一真身含全部关键字样」。变异验证:删掉 README 的控制环节 → 本测试红。
    """
    readme = (ROOT / "docs/session-agent/README.md").read_text(encoding="utf-8")
    for anchor in ("begin → next → claim", "submit → finish", "--orchestration session_v1",
                   "HOST_CAPABILITY_REQUIRED", "LEGACY_ENTRYPOINT_REQUIRED", "verify-report"):
        assert anchor in readme, f"README 缺控制环字样:{anchor}"
    for skill in ("scan-market", "stock-research", "macro-research", "sector-research"):
        text = (ROOT / ".claude/skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "session_v1" in text, skill
        assert "docs/session-agent/README.md" in text, skill
        assert "begin → next → claim" not in text, f"{skill}: 控制环正文又被抄回 SKILL.md"


def test_dossier_agent_and_stock_legacy_entry_are_explicitly_labeled():
    dossier = (ROOT / ".claude/agents/dossier-init.md").read_text(encoding="utf-8")
    assert "dossier-init / INIT" in dossier
    assert "--orchestration session_v1" in dossier
    stock = (ROOT / ".claude/skills/stock-research/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "--legacy-reason" in stock


def test_scan_runner_host_loop_is_documented_once_as_an_opt_in_pilot():
    """批 2–3 Task 4(控制器裁定):runner + mailbox 宿主循环只写在 README 一处;
    scan-market SKILL 默认仍是 legacy Workflow,只留一行 PILOT 指针,Task 6 真跑通过前不切换。"""
    readme = (ROOT / "docs/session-agent/README.md").read_text(encoding="utf-8")
    for anchor in ("session_agent run --executor mailbox", "mailbox wait", "mailbox complete",
                   "_dispatch/", "RUNNER_EXITED", "verify-report"):
        assert anchor in readme, f"README 缺 host 循环锚「{anchor}」"
    skill = (ROOT / ".claude/skills/scan-market/SKILL.md").read_text(encoding="utf-8")
    pointer = [line for line in skill.splitlines()
               if "session_agent run --executor mailbox" in line]
    assert len(pointer) == 1, "SKILL.md 应恰好一行 runner PILOT 指针"
    assert "PILOT" in pointer[0] and "docs/session-agent/README.md" in pointer[0]
    assert "mailbox complete" not in skill, "host 循环正文不得抄进 SKILL.md"
    assert any("Workflow({scriptPath" in line for line in skill.splitlines()), (
        "legacy Workflow 派发必须仍是 SKILL 默认流程(真跑验收前)")
