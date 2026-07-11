"""gate_status 格式容错回归:门名↔✓/✗ 间允许空白 + 卡片多处出现"OW三门"时取最后可解析段。

design: 「漏斗 P0+P1 波」Task 2 补充修复(2b)。背景(task-2-report.md Self-review 最后一条):
gate_status(assemble.py:222)作为 self_review.dump_ow_gate_fires / learning.shadow_buys._binding /
learning.cross_calib 三个消费方共用的解析器,有两处格式敏感导致 OW 三门失守被系统性漏记——
①`_GATESEG_RE.search` 只取全文**第一处**"OW三门"字样(卡片散文段先提到就锁死,文末真正的
结构化 Rubric 判定段被忽略);②门名与 ✓/✗ 之间若有空格就解析不出,而 `.claude/agents/l4-card.md`
满卡模板 Rubric 行写的正是「主力真在 ✓/✗」带空格格式。真实实例:
context/scan/2026-07-09/details/688213.md ——第 11 行先散文提一句"OW三门缺「主力真在」一门"
(无标记),第 39 行 Rubric 行才是结构化判定(且带空格),该卡的"主力真在"失守此前完全漏记。
"""
from __future__ import annotations

from autoresearch.scan.assemble import gate_status


def test_gate_status_tolerates_space_between_gate_name_and_mark():
    """门名与 ✓/✗ 之间允许空白(l4-card.md 满卡模板 Rubric 行的真实写法)。"""
    card = "OW三门 主力真在 ✗·业绩真兑现 ✓·估值不透支 ✓"
    assert gate_status(card) == {"主力真在": True, "业绩真兑现": False, "估值不透支": False}


def test_gate_status_uses_last_parseable_segment_when_ow_mentioned_multiple_times():
    """卡片正文先散文提一句"OW三门…"(无 ✓/✗ 标记),文末 Rubric 行才是结构化判定——
    应取全部匹配段中**最后一个**能解析出至少一个紧邻(允许空白)✓/✗ 的段,不能被前面
    无标记的散文段锁死(旧实现只取 `.search()` 命中的第一段,会在此丢失结构化判定)。"""
    card = (
        "## 一段话研判\n"
        "……OW三门缺「主力真在」一门,binding gate 封顶,故压至 Hold。\n"
        "## 收尾\n"
        "**Rubric建议**: OW三门 主力真在✗·业绩真兑现✓·估值不透支✓ → **建议 Hold**\n"
    )
    assert gate_status(card) == {"主力真在": True, "业绩真兑现": False, "估值不透支": False}


def test_gate_status_regression_688213_rubric_line_with_space():
    """真实卡回归:context/scan/2026-07-09/details/688213.md 第 11/39 行原文逐字摘录(sed -n
    '11p;39p' 核对过)。第 11 行是散文先提及"OW三门缺「主力真在」一门"(无标记);第 39 行 Rubric
    才是结构化判定,且门名与标记之间带空格(`主力真在 ✗`)——这正是 self-review 记录的
    "思特威复核卡漏记"实例(当时冒烟贡献 0 行,应为 1 行 binding fire)。
    """
    card = (
        "思特威是科创板 CIS(CMOS图像传感器)设计商,3+AI 战略(安防AIoT/智能手机/汽车电子),"
        "2025 营收90.3亿+51%、归母10亿+155%,行业周期从谷底(2022 ROE-2.6%)强修复至2025 ROE 21.3%。"
        "L3 选它是「拥挤半导体链中罕见估值不透支」——PE 40.7 对全链中位150、fwd 30.5x,"
        "叠 cmf+0.12/obv+0.31 号称真吸筹。实读确认:业绩真兑现(CFO/NI 1.87 现金背书、26Q1 np+23.66%)、"
        "估值确实相对便宜、盈利质量与偿付均健康(净债/权益0.28、利息覆盖14.5x、无雷)。"
        "但推翻了「主力真在」——主力绝对净额 -0.81亿(净出)、winner 89%高位获利盘、"
        "股东户数两季+59%(18334→29193 散户涌入=派发),占比+6.4%失真旗成立,"
        "三派发信号压过 cmf/obv 唯一正项。OW三门缺「主力真在」一门,binding gate 封顶,"
        "故从昨日 OW 压至 Hold,等8/22中报与筹码消化确认。\n"
        "\n"
        "**Rubric建议**(评分卡派生): 6 维净分 +4/6(强4·中2·弱0) ｜ "
        "OW三门 <主力真在 ✗·业绩真兑现 ✓·估值不透支 ✓> → 主力门未过,binding gate 封顶 → **建议 Hold**\n"
    )
    st = gate_status(card)
    assert st["主力真在"] is True


def test_gate_status_unchanged_when_no_segment_has_a_parseable_mark():
    """全部"OW三门"段都解析不出 ✓/✗ 标记(纯散文提及,没有结构化判定)时,返回语义与改动前
    完全一致——不得因为"找不到就返回 None"而误伤既有的"找到门名但没标记→False"契约,
    也不得因为"取最后一段"而改变单段场景下的既有行为。"""
    assert gate_status("OW三门缺「主力真在」一门,待补充结构化判定。") == {"主力真在": False}
    assert gate_status("# 卡\n无门柱段\n") is None
