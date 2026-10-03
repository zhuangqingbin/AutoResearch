"""文档字节预算 —— skill/agent 文档是 prompt,不是 changelog(2026-09-26 A2)。

预算是上限不是目标;超了先问「这段是现在怎么跑,还是历史」,历史搬 `docs/`。
设计稿:docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md §4 A2。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: 预算量的是**正文**(去掉开头的 YAML frontmatter)。2026-10-03 起 frontmatter 的 model / effort /
#: maxTurns / omitClaudeMd 是 `scan.agent_frontmatter` 按 scan_config 镜像的宿主元数据,不进 subagent 的
#: prompt;改量正文时各值同步扣掉当日的 frontmatter 字节(l4-card 226B、l3-rank 209B、SKILL 480B),
#: 正文额度一字节没放宽。
BUDGETS_BYTES = {
    # 两张卡模板 + 机读口径 + 评级规则 ≈ 13KB 是机器契约本体(实测 2026-09-26),砍不得;
    # 预算留给它 + 5KB 操作铁律。原 23.4KB 里被搬走的 4.3KB 全是事故编号与沿革。
    ".claude/agents/l4-card.md": 19_774,
    ".claude/agents/l3-rank.md": 7_891,     # +31B 2026-09-26:finalist 区间以派发上限为准(l4.max_cards 改大要真生效)
    # 终审修复轮(2026-09-26)补回 6 条被误删的操作指令(Codex 重启纪律 / Monitor 参数 /
    # capsule recover / factor_lab 链 / consensus 限频 / UNMEASURED 静默告警)+1KB → 17KB。
    ".claude/skills/scan-market/SKILL.md": 16_520,
    ".claude/skills/scan-market/STAGES.md": 26_000,
    ".claude/skills/stock-research/lite-playbook.md": 4_000,
}


def _body_bytes(path: Path) -> int:
    """文件字节 − 开头 YAML frontmatter(`---` … `---` 含两行分隔)字节。"""
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if not text.startswith("---"):
        return len(raw)
    end = text.find("\n---", 3)
    if end == -1:
        return len(raw)
    cut = text.find("\n", end + 4)
    return len(raw) - len(text[: (cut + 1) if cut != -1 else len(text)].encode("utf-8"))


@pytest.mark.parametrize("rel,budget", sorted(BUDGETS_BYTES.items()))
def test_doc_within_byte_budget(rel: str, budget: int):
    size = _body_bytes(ROOT / rel)
    assert size <= budget, f"{rel} 正文 = {size}B > 预算 {budget}B —— 先搬历史到 docs/,别调预算"


def test_no_session_v1_block_in_skills():
    for p in (ROOT / ".claude" / "skills").glob("*/SKILL.md"):
        assert "## session_v1 编排入口" not in p.read_text(encoding="utf-8"), p
