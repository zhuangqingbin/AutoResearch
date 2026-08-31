"""外源扩面的 **I / B 分界**守卫(2026-08-29)。

design: docs/specs/2026-08-28-external-evidence-expansion-design.md §4 / §10

本波只做 **I 类**(数据基建 / 展示层 / 独立技能,零判断层 prompt diff)。**B 类**(改变
scan 判断层输入)受 08-26 A0「冻结到 09-中攒 20 个结果日」,一件都没做:

| 编号 | 内容 | 状态 |
|---|---|---|
| B-1 | `strategist_pack` allowlist 加 `global_tape` | 冻结 |
| B-2 | L4 slim「海外映射事实」2 行 | 冻结 |
| B-3 | `l4-intel` 第七面「海外链」 | 冻结 |
| B-4 | sector **lite** brief 地形段 +1 行 | 冻结 |
| B-5 | 工具级硬限频 | 冻结 |

**为什么要写成测试而不是写进文档**:本仓的家训是「指令级约束的失败率不为零,数据级为零」
(防锚定靠 prompt 叮嘱失守过两次,改成数据级投影之后归零)。冻结同理 —— 一句「记得别接」
挡不住下一个人顺手把新键接进 L4 简报;一条会变红的断言才挡得住。

解冻时:删本文件对应的用例 = 显式声明「我知道我在解冻哪一件」,而不是让守卫悄悄失效。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 判断层里**不该**出现的外源词。`global_tape` / `readthrough` 是本波新造的键名,
#: 它们出现在下面任何一个文件里,就意味着某条 B 类被接进了判断层。
_OVERSEAS_TOKENS = ("global_tape", "readthrough", "海外映射", "海外链")


def _read(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        pytest.skip(f"{rel} 不在(本仓可能已重构)")
    return p.read_text(encoding="utf-8")


def test_b1_strategist_projection_still_refuses_global_tape():
    """B-1 冻结:隔夜 tape 进 full market_pack(L5 展示),**进不了策略师投影**。"""
    from autoresearch.scan.strategist_pack import ALLOWED_KEYS

    assert "global_tape" not in ALLOWED_KEYS


def test_b2_l4_prompt_builder_carries_no_overseas_facts():
    """B-2 冻结:L4 决策卡的输入(prompt / slim 注入)不含任何海外映射事实。"""
    src = _read("autoresearch/scan/l4/prompts.py")
    hits = [t for t in _OVERSEAS_TOKENS if t in src]
    assert not hits, f"L4 prompt 里出现外源键 {hits} —— B-2 是冻结项,解冻要走裁决"


def test_b3_l4_intel_still_has_six_faces_not_seven():
    """B-3 冻结:`l4-intel` 仍是六面盲搜,没有第七面「海外链」。"""
    src = _read(".claude/agents/l4-intel.md")
    hits = [t for t in _OVERSEAS_TOKENS if t in src]
    assert not hits, f"l4-intel def 里出现外源词 {hits} —— 第七面是 B-3,冻结中"
    assert "六面全查" in src


def test_b4_sector_lite_brief_stays_untouched():
    """B-4 冻结:**lite** 行业 brief(喂 L3/L4)地形段没有海外映射行。

    full 深研的 `sector-intel` 与 pack 的 `readthrough` 键是 I 类,允许存在 —— 边界在
    「喂不喂判断层」,不在「碰没碰行业研究」。
    """
    src = _read(".claude/agents/sector-brief.md")
    hits = [t for t in _OVERSEAS_TOKENS if t in src]
    assert not hits, f"sector-brief(lite)里出现外源词 {hits} —— B-4 是冻结项"


def test_b5_web_budget_never_claims_hard_enforcement():
    """B-5 冻结:限频仍是**事后观测**,不得自称硬强制(双引擎更不得声称等价)。"""
    from autoresearch.trace import web_budget

    # 对**可发射值**断言,不对文本断言:docstring 里解释「为什么不做 HARD_SHARED」是好事,
    # 拿它当违规证据会把一份诚实的注释判成越界(第一版就这么误伤了)。
    assert web_budget.ENFORCEMENTS == (web_budget.OBSERVED_ONLY,
                                       web_budget.HARD_CLAUDE,
                                       web_budget.UNMEASURED)
    # 跨引擎硬 cap(`HARD_SHARED`)要求把预算放到共同 dispatcher 层(§6.5);
    # 靠解析 transcript 冒充强制是本稿明令禁止的 —— 所以它连**枚举值**都不该存在。
    assert not hasattr(web_budget, "HARD_SHARED")
    assert "HARD_SHARED" not in web_budget.ENFORCEMENTS


def test_overseas_calendar_feeds_display_only():
    """D-2 是 I 类的判据:日历只进展示三点,**不进任何 agent 输入构造**。"""
    from autoresearch.scan import overseas

    consumers = ("autoresearch/scan/l4/prompts.py",
                 "autoresearch/scan/l3/merge.py",
                 "autoresearch/scan/strategist_pack.py")
    for rel in consumers:
        p = ROOT / rel
        if p.exists():
            assert "overseas" not in p.read_text(encoding="utf-8"), \
                f"{rel} 引用了 overseas 日历 —— 它是展示层,不喂判断层"
    # 反面:展示侧确实接了(否则这条守卫在守一个不存在的东西)
    assert hasattr(overseas, "summary_lines") and hasattr(overseas, "brief_line")
