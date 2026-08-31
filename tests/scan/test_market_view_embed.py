"""策略师稿(`market_view.md`)进 summary 的**精确切片**契约。

design: `docs/specs/2026-08-28-summary-slimdown-design.md` §4.1 节 7 / §5 行 15 / §6.8。

旧行为是**整段嵌**:2.6KB 散文连同「1. 一句话定调」「4. 操作基调」一起塞进决策层,
于是同一个事实(定调)在仪表盘① 和市场地形节各展开一次、操作基调与 overlay 打架。
新契约两条:

1. **只嵌 2/3/5**(市场结构 / 板块红黑榜 / 关注)——1 定调归仪表盘①、4 操作基调归
   「## 行动」、6 免责归 appendix G。全文照常进 appendix C,**减层不减料**。
2. **解析失败不整段回退**:标题缺失 / 格式污染 → summary 只印一行链接,决不把全文倒回来。
   ——「回退」正是本波要治的病,防它的机制不能自己复刻一遍。

合成 fixture,零网络;产物全落 tmp_path。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.scan import report_sections as rs
from autoresearch.scan.report_appendix import render_appendix

_D, _HH, _F = "2026-06-30", "2314", "20260630_2314"

#: macro-brief 的六小节机器契约(agent def `.claude/agents/macro-brief.md:16-24` 逐字结构)。
_MV_SIX = """# 市场研判 — 2026-06-30

1. **一句话定调**:避险哑铃,AI 半导体极致拥挤 + 宽基超跌落刀。
2. **市场结构**:宽度 44.67% 站上 MA60;北向近 5 日净买 62 亿,两融余额 1.82 万亿。
3. **板块红黑榜**:强侧贵金属 +16.10%(n=12);弱侧半导体 −3.24%(n=88)。
4. **操作基调**:总仓位基准三到五成,不追高、不接落刀。
5. **关注**:中报披露收官周(8/31 截止);9 月首周解禁高峰。
6. 仅供研究,非投资建议。
"""

#: 标题缺失:2/3/5 三个 summary 小节一个都不在(只剩 1/4/6)。
_MV_MISSING = """# 市场研判 — 2026-06-30

1. **一句话定调**:避险哑铃,AI 半导体极致拥挤。
4. **操作基调**:总仓位基准三到五成,不追高、不接落刀。
6. 仅供研究,非投资建议。
"""

#: 格式污染:编号还在,`**` 粗体标题被写掉了 → 一个头都匹配不上。
_MV_POLLUTED = """# 市场研判 — 2026-06-30

1. 一句话定调:避险哑铃,AI 半导体极致拥挤。
2. 市场结构:宽度 44.67% 站上 MA60;北向近 5 日净买 62 亿。
3. 板块红黑榜:强侧贵金属 +16.10%;弱侧半导体 −3.24%。
5. 关注:中报披露收官周(8/31 截止)。
"""

#: 只在某一小节里出现一次的指纹串 —— 断言用它而不是节标题,才分得清
#: 「这一节被嵌进来了」和「标题字样恰好也在别处出现」。
_FP = {
    "1": "避险哑铃",
    "2": "宽度 44.67% 站上 MA60",
    "3": "强侧贵金属 +16.10%",
    "4": "不追高、不接落刀",
    "5": "中报披露收官周",
    "6": "6. 仅供研究",
}


def _min_scan(tmp_path, market_view: str | None = None):
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    if market_view is not None:
        (d / "market_view.md").write_text(market_view, encoding="utf-8")
    return d


def _render(d):
    """一次 prepare → 两处纯渲染(§4.1.2)。搬去附录的断言在同一 fixture 上接住。"""
    model = rs.prepare_report_model(d, _D, _HH, _F)
    summary = rs.render_summary(model)
    model = rs.attach_review(model, summary)
    return summary, render_appendix(model)


# ── 1. 正常六节:summary 只拿 2/3/5,appendix 收全文 ────────────────────────────


def test_summary_embeds_only_sections_2_3_5(tmp_path):
    """**精确切片**:2/3/5 在决策层,1/4/6 一个字都不许进来。

    变异校验:把 `report_sections._MV_SUMMARY_SECTIONS` 改成 `("1","2","3","5")`(或退回
    整段嵌)本条立刻变红 —— 这正是「同一事实印两次」的复发形态。
    """
    summary, _ = _render(_min_scan(tmp_path, _MV_SIX))

    assert "## 市场地形(首席策略师 · 描述性)" in summary
    for key in ("2", "3", "5"):
        assert _FP[key] in summary, f"策略师第 {key} 小节没进 summary(减层不减料被违反)"
    for key in ("1", "4", "6"):
        assert _FP[key] not in summary, (
            f"策略师第 {key} 小节被印进 summary —— 它的唯一展开点不在这里"
            "(1 定调=仪表盘① / 4 操作基调=行动节 / 6 免责=appendix G)")


def test_appendix_keeps_the_full_market_view(tmp_path):
    """搬走 ≠ 丢:1/4 两节在 appendix C 全文在场(6 由 `_load_market_view` 前置剥离与否不管)。"""
    _, appendix = _render(_min_scan(tmp_path, _MV_SIX))

    assert "## C. 研究全文" in appendix
    for key in ("1", "2", "3", "4", "5"):
        assert _FP[key] in appendix, f"appendix C 少了策略师第 {key} 小节(现场层必须留全文)"


def test_slice_market_view_returns_all_six_sections():
    """单元:六节全解出 + `parse_ok=True`(切片器本体的鉴别力,不经渲染层)。"""
    slices, ok = rs.slice_market_view(_MV_SIX)

    assert ok is True
    assert set(slices) == {"1", "2", "3", "4", "5"}, "六小节切片键不对(第 6 节裸编号,不带 `**`)"
    assert _FP["2"] in slices["2"] and _FP["3"] in slices["3"] and _FP["5"] in slices["5"]
    assert _FP["3"] not in slices["2"], "小节之间串味 = 切片边界没收住"
    assert _FP["6"] not in slices["5"], "第 6 节尾注被第 5 节吞了 → 免责声明会跟着进决策层"


# ── 2. 解析失败:只留一行链接,**不整段回退** ─────────────────────────────────


@pytest.mark.parametrize(("label", "raw"), [("标题缺失", _MV_MISSING), ("格式污染", _MV_POLLUTED)])
def test_parse_failure_leaves_one_link_and_never_falls_back_to_the_whole_text(
        tmp_path, label, raw):
    """§6.8:解析失败 → summary 一行链接;**全文不得整段回退**,全文只在 appendix C。

    变异校验:把 `render_summary` 的失败分支改回 `out += [model.market_view_raw]`,
    本条立刻变红 —— 整段回退曾一次性把 2.6KB 散文 + 操作基调塞进决策层。
    """
    summary, appendix = _render(_min_scan(tmp_path, raw))

    assert "## 市场地形(首席策略师 · 描述性)" in summary
    assert "策略师分节解析失败" in summary, f"{label}:失败态没有自证文案(静默降级)"
    assert "appendix.md#appendix-c-research" in summary, f"{label}:失败态没给全文链接"
    for fp in _FP.values():
        assert fp not in summary, f"{label}:失败态把策略师原文({fp})整段回退进了决策层"
    assert any(fp in appendix for fp in _FP.values()), f"{label}:appendix C 也没留全文 = 真丢件"


@pytest.mark.parametrize(("label", "raw", "expect_slices"),
                         [("标题缺失", _MV_MISSING, True), ("格式污染", _MV_POLLUTED, False)])
def test_slice_market_view_reports_failure_without_raising(label, raw, expect_slices):
    """单元:两种坏输入都 `parse_ok=False`;格式污染连一个头都匹配不上(空 dict)。"""
    slices, ok = rs.slice_market_view(raw)

    assert ok is False, f"{label}:切片器把坏输入当成功"
    assert bool(slices) is expect_slices


# ── 3. 无策略师稿:确定性脉搏回退(老路不破)─────────────────────────────────


def test_fallback_pulse_when_view_absent_and_market_data_present(tmp_path):
    d = _min_scan(tmp_path)
    pd.DataFrame([{"code": "1", "above_ma60": 0.0, "pct_60d": -25.0}] * 5).to_csv(
        d / "L1_scored_full.csv", index=False)

    summary, _ = _render(d)

    assert "市场脉搏(确定性回退)" in summary
    assert "## 市场地形(确定性脉搏)" in summary


def test_no_market_section_when_no_data(tmp_path):
    """无策略师稿、无市场数据 → 整节不出现(presence-gated);漏斗现场照常落 appendix B。"""
    summary, appendix = _render(_min_scan(tmp_path))

    assert "## 市场地形" not in summary
    assert "市场脉搏" not in summary
    assert "漏斗数量" in appendix, "漏斗数量表下沉 appendix B 后必须还在(减层不减料)"
