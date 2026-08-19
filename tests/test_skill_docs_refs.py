"""doc-lint —— .claude/skills 文档与源码的最低一致性(海拔重构 Phase 1,§7)。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md。
两条纪律,治"SKILL.md 旧文以源码为准"这类文档漂移:
① 旧 skill 名(analyze-ticker / analyze-ticker-lite)在活文档零命中(合并沿革注除外);
② skill 文档里引用的 `python -m autoresearch.<mod>` 模块全部可 import(防命令漂移)。
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 历史沿革注的合法提法(允许保留旧名的行必须含其一)
_LEGACY_OK = ("Merges former", "原 analyze-ticker", "已合并", "已被", "沿革")


def _docs() -> list[Path]:
    docs = sorted((ROOT / ".claude" / "skills").rglob("*.md"))
    docs += [ROOT / "CLAUDE.md", ROOT / "README.md"]
    return [p for p in docs if p.exists()]


def test_no_stale_skill_names():
    """旧 skill 名只许出现在标了沿革的行——其余一律应为 stock-research。"""
    stale = re.compile(r"analyze-ticker")
    hits: list[str] = []
    for p in _docs():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if stale.search(line) and not any(tag in line for tag in _LEGACY_OK):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert not hits, "旧 skill 名残留(应为 stock-research;历史注请带沿革标记):\n" + "\n".join(hits)


def test_no_dangling_local_doc_refs():
    """skill 文档反引号引用的 *-playbook.md / STAGES.md / SKILL.md 必须真实存在(治退役类悬空)。

    带沿革标记(退役/迁入/沿革/历史)的行豁免——那是有意保留的历史注。
    """
    pat = re.compile(r"`([\w./-]*(?:playbook|STAGES|SKILL)\.md)`")
    skills_root = ROOT / ".claude" / "skills"
    dangling: list[str] = []
    for p in sorted(skills_root.rglob("*.md")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if any(tag in line for tag in ("退役", "迁入", "沿革", "历史")):
                continue
            for ref in pat.findall(line):
                cand = [p.parent / ref, skills_root / ref]
                if "/" not in ref:
                    cand += list(skills_root.rglob(Path(ref).name))
                if not any(c.exists() for c in cand):
                    dangling.append(f"{p.relative_to(ROOT)}:{i}: `{ref}`")
    assert not dangling, "悬空的本地文档引用:\n" + "\n".join(dangling)


# ───────── 复盘不动刀禁令(2026-08-13 用户裁定 B 档;spec §3/§4.3/§6) ─────────
# 靶子:复盘/反馈流程曾有两条通往 .claude/** 的写路径——lesson「毕业」直写 playbook 正文
# (全仓唯一无人批门的直写指令)与 prompt_patch 施工处方。下线后靠这组正/负锚探针防复辟:
# 指令被删、被改回直写、或函数被悄悄加回来,任一发生这里就红。

_NO_SELFMODIFY_ANCHOR = (
    "复盘/反馈流程一律不得编辑 .claude/ 与 CLAUDE.md/AGENTS.md;"
    "skill/prompt/agent/workflow 文本只在用户显式发起的开发会话中修改。"
)

# 禁令约束的四个闭环文档(逐字带锚句);scan-market 只需引用,单列见下一个测试。
_LOOP_DOCS = (
    ".claude/skills/scan-retro/retro-playbook.md",
    ".claude/skills/scan-retro/SKILL.md",
    ".claude/skills/feedback/feedback-playbook.md",
    ".claude/skills/feedback/SKILL.md",
)


def test_no_selfmodify_anchor_present_in_loop_docs():
    """禁令锚句在四个闭环文档逐字存在——有人删掉禁令段,这里就红。"""
    missing = [d for d in _LOOP_DOCS
               if _NO_SELFMODIFY_ANCHOR not in (ROOT / d).read_text(encoding="utf-8")]
    assert not missing, ("复盘不动刀禁令锚句缺失(spec 2026-08-13 §4.3):\n"
                         + "\n".join(missing) + f"\n锚句原文:{_NO_SELFMODIFY_ANCHOR}")


def test_scan_market_prelude_references_the_ban():
    """scan-market 的「开跑前补跑复盘」段须引用禁令——补复盘会话同受约束,别从这个口子绕。"""
    txt = (ROOT / ".claude/skills/scan-market/SKILL.md").read_text(encoding="utf-8")
    assert "复盘不动刀" in txt, "scan-market SKILL.md 补复盘段缺禁令引用(spec 2026-08-13 §4.3)"


def test_graduation_section_is_nomination_not_direct_write():
    """毕业出口是提名制:有人把它改回「直接写进 playbook」,这里就红。"""
    txt = (ROOT / ".claude/skills/feedback/feedback-playbook.md").read_text(encoding="utf-8")
    assert "毕业提名" in txt and "add_graduation_nomination" in txt, \
        "feedback-playbook 毕业节应为提名制(spec 2026-08-13 §4.1)"


def test_no_prompt_patch_instructions_in_claude_tree():
    """prompt_patch 管线已退役(spec §4.2):`.claude/` 全树不得再教这招。

    沿革/退役/历史标记行豁免(照本文件 `_LEGACY_OK` 同款哲学)——历史注可以提它,
    活指令不行。
    """
    claude = ROOT / ".claude"
    hits: list[str] = []
    for p in sorted(claude.rglob("*")):
        if p.suffix not in (".md", ".js", ".jsonc") or not p.is_file():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "prompt_patch" in line and not any(t in line for t in ("沿革", "退役", "历史")):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert not hits, "prompt_patch 已退役,`.claude/` 不应再有活指令:\n" + "\n".join(hits)


def test_feedback_store_has_no_prompt_patch_api():
    """代码侧同步退役:函数还在就是诱饵——未来 session 会绕过文档直接调它。

    (代码探针放在 doc-lint 文件里是有意的:五条『复盘不动刀』锚句集中一处,
    读者一眼看全禁令边界,不必在两个文件间跳。)
    """
    import autoresearch.learning.feedback_store as fs
    for sym in ("add_prompt_patch", "_CONTRACT_ANCHORS", "_MAX_OPEN_PROMPT_PATCH",
                "_prompt_patch_payload", "_is_contract_file", "_CONTRACT_FILE_BASENAMES"):
        assert not hasattr(fs, sym), f"prompt_patch 管线残留符号 {sym}(spec 2026-08-13 §4.2 已退役)"


def test_graduation_section_has_no_direct_write_instruction():
    """毕业节不得复辟成直写:出现「固化写进」式指令即红。"""
    txt = (ROOT / ".claude/skills/feedback/feedback-playbook.md").read_text(encoding="utf-8")
    assert "固化写进" not in txt, "毕业节出现直写 playbook 的指令(spec 2026-08-13 §4.1 已改提名制)"


def test_skill_doc_modules_importable():
    """skill 文档教用户跑的 `python -m autoresearch.*` 模块必须真实存在(防命令漂移)。"""
    pat = re.compile(r"python -m (autoresearch(?:\.\w+)+)")
    mods: set[str] = set()
    for p in _docs():
        mods.update(pat.findall(p.read_text(encoding="utf-8")))
    assert mods, "未在 skill 文档中发现任何 autoresearch 模块引用(regex 失效?)"
    broken: list[str] = []
    for m in sorted(mods):
        try:
            importlib.import_module(m)
        except Exception as e:  # noqa: BLE001 — 任何 import 失败都算文档漂移
            broken.append(f"{m}: {type(e).__name__}: {e}")
    assert not broken, "skill 文档引用了不可 import 的模块:\n" + "\n".join(broken)
