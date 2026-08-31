"""0买日报告瘦身:OW三门失守直方图、market_view 嵌入、L3 精排节去重。合成,无网络。

2026-08-28 summary 精简重构(design 2026-08-28-summary-slimdown-design.md §4.1/§5)后,本文件
锁的三件事各自换了页,断言**跟着搬**:

- **门柱直方图**:`gate_status` 解析卡片自由文本的那份下沉 appendix D(§4.1 节 6「单源」)——
  它口径不同(分母=可解析卡)且有漏读加粗 `**✗**` 前科,与仪表盘③同屏会打架。summary 节 6
  改读 `decision_records.gate_states` 结构化字段。**两份数都要在**,只是不同屏。
- **策略师嵌入**:`_load_market_view` 仍在读盘时剥掉自带 H1 与免责节(老行为不变);新增按
  六小节切片,summary 只嵌 2/3/5,§1 定调归仪表盘①、§4 操作基调归「行动」、§6 归 appendix G;
  解析失败**不整段回退**进 summary,全文照常进 appendix C。
- **L3 精排清单**:整节下沉 appendix C(§5 行 19),summary 的候选表只留同向「一句依据」。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.report_appendix import render_appendix
from autoresearch.scan.report_sections import (
    attach_review,
    prepare_report_model,
    render_summary,
)

_D = "2026-07-03"
_F = "20260703_1200"


def _render(d):
    """一次整形 → 两处纯渲染,返回 (summary, appendix)。顺序照 publisher 冻结时序。

    `attach_review` 必须排在 `render_appendix` 前:appendix A 节读的是它回填的全文 banner。
    """
    model = prepare_report_model(d, _D, "1200", _F)
    summary = render_summary(model)
    return summary, render_appendix(attach_review(model, summary))


def _scan(tmp_path):
    d = tmp_path / "s"
    (d / "details").mkdir(parents=True)
    (d / "meta.json").write_text("{}", encoding="utf-8")
    pd.DataFrame([
        {"code": "300476", "name": "甲", "sector": "元件",
         "thesis": "AI 光模块需求超预期", "risk": "估值高", "catalyst": "Q2 财报"},
        {"code": "000686", "name": "乙", "sector": "证券Ⅱ",
         "thesis": "券商β", "risk": "winner80", "catalyst": "无"},
    ]).to_csv(d / "finalists.csv", index=False)
    (d / "details" / "300476.md").write_text(
        "# 决策卡\n**Rubric建议**: 净分-1 ｜ OW三门 主力真在✗·业绩真兑现△·估值不透支✗ → 压Hold\n"
        "**Rating**: Hold\n", encoding="utf-8")
    (d / "details" / "000686.md").write_text(
        "# 决策卡\n**Rubric建议**: 净分0 ｜ OW三门 主力真在✗·业绩真兑现✓·估值不透支△ → 压Hold\n"
        "**Rating**: Hold\n", encoding="utf-8")
    return d


def test_gate_histogram_line(tmp_path):
    """自由文本口径的 OW三门直方图 → appendix D;两口径说明随行。

    旧断言(`OW三门失守分布` / `主力真在✗ 2` / `估值不透支✗ 1`)一字未改,只是改在 appendix
    上取证;新增两条:① 它**不得**留在 summary(同屏两个口径打架是本波要根治的病);
    ② summary 节 6 必须给出**结构化那侧**的同族计数,否则「搬走」就变成了「丢掉」。
    """
    md, ap = _render(_scan(tmp_path))
    assert "OW三门失守分布" in ap
    assert "主力真在✗ 2" in ap and "估值不透支✗ 1" in ap
    assert "以结构化那侧为准" in ap, "两口径说明(GATE_HIST_BASIS_NOTE)应随直方图一起搬"
    assert "OW三门失守分布" not in md, "自由文本口径直方图不得与仪表盘③同屏"
    # 决策层仍要回答「为什么没有 BUY」——单源 decision_records.gate_states
    assert "## 为什么没有 BUY" in md
    assert "满卡 2 张三门 ✗:主力真在 2 · 业绩真兑现 0 · 估值不透支 1" in md


# (test_watchlist_moved_above_funnel_and_sector 已删:观察单日检节整体退役,fb_20260714_002。)


def test_market_view_full_text_moves_to_appendix(tmp_path):
    """策略师稿的 H1 / 免责节仍在读盘时被剥掉;全文进 appendix C,不进决策层。

    本夹具用的是**旧散文格式**(`## 1. 定调`),不符合 macro-brief 的机器契约
    (`1. **一句话定调**:…`)→ 切片失败。失败态的契约是:summary 只印一行链接并指向
    附录,**不得整段回退**(整段回退曾把 2.6KB 散文连同「操作基调」塞进决策层)。
    """
    d = _scan(tmp_path)
    (d / "market_view.md").write_text(
        "# 市场研判 · 2026-07-03\n\n## 1. 定调\n震荡哑铃\n\n## 6. 免责\n仅供研究,非投资建议。\n",
        encoding="utf-8")
    md, ap = _render(d)
    assert "震荡哑铃" in ap, "策略师全文必须进 appendix C(搬家不减料)"
    assert "# 市场研判 ·" not in ap, "嵌入时应剥掉 market_view 自带 H1(报告已有 H1)"
    assert "# 市场研判 ·" not in md
    assert "6. 免责" not in ap, "嵌入时应剥掉自带免责节(附录 G 已有完整局限)"
    assert "6. 免责" not in md
    # 失败态:summary 只留一行指向附录,不整段回退
    assert "策略师分节解析失败" in md
    assert "震荡哑铃" not in md, "解析失败时不得把全文整段塞回决策层"


def test_market_view_slices_2_3_5_into_summary_only(tmp_path):
    """合规六小节稿:summary 只嵌 2 市场结构 / 3 板块红黑榜 / 5 关注。

    事实归属(§4.1.1)的 mutation 探针:§1 定调归 🧭 仪表盘①、§4 操作基调归「行动」节,
    两者若被塞回「市场地形」,本测试变红。全文六节照常在 appendix C。
    """
    d = _scan(tmp_path)
    (d / "market_view.md").write_text(
        "# 市场研判 · 2026-07-03\n\n"
        "1. **一句话定调**:震荡哑铃\n\n"
        "2. **市场结构**:宽度 44.67% 站上 MA60\n\n"
        "3. **板块红黑榜**:强侧贵金属 +16.10%\n\n"
        "4. **操作基调**:基准 3–5 成\n\n"
        "5. **关注**:中报披露收官周\n\n"
        "6. 仅供研究,非投资建议。\n", encoding="utf-8")
    md, ap = _render(d)
    assert "## 市场地形(首席策略师 · 描述性)" in md
    assert "策略师分节解析失败" not in md
    for kept in ("宽度 44.67% 站上 MA60", "强侧贵金属 +16.10%", "中报披露收官周"):
        assert kept in md, f"市场地形应嵌 2/3/5 小节原文,缺 {kept!r}"
    assert "震荡哑铃" not in md, "§1 定调的唯一展开点是 🧭 仪表盘①"
    assert "基准 3–5 成" not in md, "§4 操作基调的唯一展开点是「行动」节的 overlay"
    # 六节全文仍在附录 C(含被 summary 排除的 1/4)
    for full in ("震荡哑铃", "宽度 44.67% 站上 MA60", "强侧贵金属 +16.10%",
                 "基准 3–5 成", "中报披露收官周"):
        assert full in ap, f"appendix C 缺策略师原文片段 {full!r}"


def test_l3_section_dedup(tmp_path):
    """L3 逐票现场(论点/风险/催化)整节下沉 appendix C;决策层不重复展开。

    旧契约是「精排节只留 风险/催化,论点只在表里」——那是**同一页内**的去重。现在去重
    跨页:appendix C 是现场全文(论点/风险/催化俱全),summary 的候选表只有同向一句依据,
    L3 全文列已删。所以断言方向反过来钉:全文必须在 appendix、必须不在 summary。
    """
    md, ap = _render(_scan(tmp_path))
    start = ap.find("## C. 研究全文")
    assert start >= 0, "appendix 缺 C 节锚点 —— 后面的切片会恒空(假绿灯)"
    end = ap.find("\n## ", start + 1)
    sec = ap[start:end] if end != -1 else ap[start:]
    assert sec.strip(), "研究全文节切片为空 —— 锚点失效,断言会变成恒绿"
    assert "风险:估值高" in sec and "催化:Q2 财报" in sec
    assert "论点:AI 光模块需求超预期" in sec, "C 节是现场全文,论点也要在"
    assert "AI 光模块需求超预期" not in md, "L3 全文论点不得留在决策层(旧 `L3精排` 列已删)"
    # 决策层保留去附录的语义链接(旧「论点见…」交叉引用的去处)
    assert "论点全文见" in md and "appendix.md#appendix-c-research" in md
