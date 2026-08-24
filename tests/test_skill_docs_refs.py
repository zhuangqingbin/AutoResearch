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


# ───────── 自我修改禁令(2026-08-13 用户裁定 B 档 → 2026-08-21 结构性坐实) ─────────
# 原靶子:复盘/反馈流程曾有两条通往 `.claude/**` 的写路径(lesson「毕业」直写 playbook 正文、
# prompt_patch 施工处方)。2026-08-21 用户裁定「整个 learning 层退役」把两条路径的**宿主**
# 一并删除(`autoresearch/learning/` 整包 + `scan-retro`/`feedback` 两个 skill),原来那五条
# 锚句探针的被测文档已不存在,随之删除;下面这条负锚保留 —— 它管的是 `.claude/` 全树,
# 与哪个模块是宿主无关,正是防复辟该盯的位置。


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
