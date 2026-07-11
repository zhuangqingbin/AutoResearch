"""agent-defs lint —— `.claude/agents/` 叶子 agent 定义与源码/playbook 的最低一致性。

2026-07-05:scan 叶子 agent 化(l4-card / sector-brief;buy-skeptic 07-07 移除)——稳定人设烤进
agent system prompt(派发 prompt 缩到两行、前缀吃 cache、契约不再靠每次转述)。
本文件治两类漂移:① agent 文件缺/frontmatter 坏;② 关键**机器契约锚**(进入P4倾向 /
FINAL TRANSACTION PROPOSAL / OW三门名 / 两段标题)在 agent 定义与真值源间失同步。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".claude" / "agents"
SKILLS = ROOT / ".claude" / "skills"

_NAMES = ("l4-card", "sector-brief", "macro-brief", "l3-rank")


def _agent_text(name: str) -> str:
    p = AGENTS / f"{name}.md"
    assert p.exists(), f"缺 agent 定义:{p.relative_to(ROOT)}"
    return p.read_text(encoding="utf-8")


def test_agent_files_exist_with_frontmatter():
    """叶子 agent 定义在位:frontmatter 有 name/description/model: opus(全 Opus 设计)。"""
    for name in _NAMES:
        text = _agent_text(name)
        assert text.startswith("---"), f"{name}: 缺 frontmatter"
        head = text.split("---", 2)[1]
        assert f"name: {name}" in head, f"{name}: frontmatter name 不符"
        assert "description:" in head, f"{name}: 缺 description"
        assert "model: opus" in head, f"{name}: 应为 model: opus(scan 全 Opus 设计)"


def test_l4_card_contract_anchors_synced():
    """l4-card 与 lite-playbook 的机器契约锚一致(卡被 parse_rating/lint/stage_eval 直接读)。"""
    from autoresearch.scan.agents.l4_card import _OW_GATES  # 单一事实源
    agent = _agent_text("l4-card")
    playbook = (SKILLS / "stock-research" / "lite-playbook.md").read_text(encoding="utf-8")
    anchors = ["进入P4倾向", "FINAL TRANSACTION PROPOSAL", "**Rating**",
               "早停只向下", "Rubric建议", "一段话研判", "L3 论点裁决",
               "已核数字摘录", "多写不多读", "龙虎榜席位", "活体新闻",
               "早停卡短格式", "卡契约 v3·超短 1~2 日", "超短口径",
               "机构面网查", "先读数据后读论点", *(g for g in _OW_GATES)]
    for a in anchors:
        assert a in agent, f"l4-card 缺契约锚「{a}」"
        assert a in playbook, f"lite-playbook 缺契约锚「{a}」(真值源被改,先同步 agent 定义)"


def test_l3_rank_anchors_present():
    """l3-rank 契约锚:T+2 兑现机制维 + conviction 行为化重锚(≥70 限额)+ mechanism 输出字段。

    l3-rank 无 lite-playbook 式的独立真值源(screening-playbook 已退役),故只做单文件
    存在性检查,不做双侧同步(与 test_l4_card_contract_anchors_synced 的双文件模式不同)。
    """
    agent = _agent_text("l3-rank")
    for a in ("兑现机制", "≥70", "mechanism"):
        assert a in agent, f"l3-rank 缺契约锚「{a}」"


def test_sector_brief_anchors_synced():
    """sector-brief 两段标题/方向行与 brief.py 机器契约同源(extract_terrain/extract_view/记账)。"""
    from autoresearch.sector.brief import TERRAIN_HDR, VIEW_HDR  # 单一事实源
    agent = _agent_text("sector-brief")
    for a in (TERRAIN_HDR, VIEW_HDR, "**行业方向**", "不编", "实时网查"):
        assert a in agent, f"sector-brief 缺契约锚「{a}」"
    assert "WebSearch" in agent.split("---", 2)[1], "sector-brief frontmatter 缺 WebSearch tool"
    playbook = (SKILLS / "sector-research" / "sector-playbook.md").read_text(encoding="utf-8")
    assert "实时网查" in playbook, "sector-playbook lite 段缺实时网查 note(agent↔真值源漂移)"


def test_skill_docs_wire_agent_types():
    """scan SKILL/STAGES 派发口径指向 agent 类型(防"agent 建了没人用"的接线漂移)。"""
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    for name in _NAMES:
        assert name in skill or name in stages, f"scan 文档未接线 agent 类型「{name}」"


def test_macro_brief_anchors_synced():
    """macro-brief 六小节标题 + 防锚定铁律与 macro-playbook 末节(市场研判 lite)同源。"""
    agent = _agent_text("macro-brief")
    playbook = (SKILLS / "macro-research" / "macro-playbook.md").read_text(encoding="utf-8")
    anchors = ["一句话定调", "市场结构", "板块红黑榜", "操作基调",
               "描述性地形", "不锚定卡片"]
    for a in anchors:
        assert a in agent, f"macro-brief 缺契约锚「{a}」"
        assert a in playbook, f"macro-playbook 缺契约锚「{a}」(真值源被改,先同步 agent 定义)"
    assert "实时网查" in agent, "macro-brief 缺契约锚「实时网查」"
    assert "实时网查" in playbook, "macro-playbook lite 段缺实时网查 note(agent↔真值源漂移)"
    assert "WebSearch" in agent.split("---", 2)[1], "macro-brief frontmatter 缺 WebSearch tool"
