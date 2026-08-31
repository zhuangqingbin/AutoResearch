"""gate_status 格式容错回归:门名↔✓/✗ 间允许空白 + 卡片多处出现"OW三门"时取最后可解析段。

design: 「漏斗 P0+P1 波」Task 2 补充修复(2b)。背景(task-2-report.md Self-review 最后一条):
gate_status(assemble.py:222)作为门柱解析的
多个消费方共用的解析器,有两处格式敏感导致 OW 三门失守被系统性漏记——
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


def test_gate_status_no_segment_at_all_returns_none():
    """全文压根没有"OW三门"字样 → None(与改动前一致,这条从来没变过)。"""
    assert gate_status("# 卡\n无门柱段\n") is None


# D8.3 ①(2026-08-31 修口径):上面这条用例原名 `..._unchanged_when_no_segment_has_a_
# parseable_mark`,曾断言"全部『OW三门』段都解析不出 ✓/✗ 标记时 → {"主力真在": False}"
# ——即"找到门名但没标记 = 未失守(全过)"。审计（full-coverage-research-system-
# brainstorm §2.2 K2）指出这正是同一条「解析器读不懂就悄悄给通过」的病：`_mark_after`
# 对没写标记的门名返回 ""，`"" == "✗"` 恒 False，于是"一个字都没判"被读成"三门全过"。
# 本用例因此**改口径**（不是新增,是修正）：这个场景现在必须是 None（"这段解析不出判断"），
# 不能再默认全部通过。See `test_gate_status_unmarked_returns_none` below for the new
# behaviour's dedicated regression lock（同一事实,两个用例名字不同角度各锁一次）。

def test_gate_status_prose_mention_without_marks_returns_none():
    """纯散文提及门名、没有任何 ✓/✗ 标记时 → None,不再当"三门全过"读(D8.3 ①修口径,
    见上方旁注;此用例接手了原 `..._unchanged_...` 的这一半断言并翻转结论)。"""
    assert gate_status("OW三门缺「主力真在」一门,待补充结构化判定。") is None


# ── Wave10 A11 复核(2026-08-01):同一缺陷族的**第三次**复发 ────────────────
#
# 前两次是「空格」与「多段」;这次是 **markdown 强调号**。卡片里写
#   `OW三门 <主力真在 ✓·业绩真兑现 **✗**(营收/归母双降)·估值不透支 ✓> → **建议 Hold**`
# 而跳过逻辑只跳空白,于是 `**` 挡住了 ✗,整卡被判成「三门全过」。
#
# 全语料实测:241 张带门柱段的卡里 **42 张(17.4%)** 中招,方向**单向** —— 真失守被读成
# 通过。链路上每一环都吃了这个错:decision_records 记 PASS → 该票根本不进门账本 →
# 门柱直方图少数 → `shadow_buys.binding` 空 → 门归因 v3 全盘继承。
# 症状长得像「这道门最近没怎么拦人」,而事实是解析器看不懂加粗。

def test_gate_status_tolerates_markdown_emphasis_around_marks():
    """`**✗**` 是卡片里的主流写法,不是异常写法。"""
    card = "OW三门 <主力真在 ✓·业绩真兑现 **✗**(营收/归母双降)·估值不透支 ✓> → **建议 Hold**"
    assert gate_status(card) == {"主力真在": False, "业绩真兑现": True, "估值不透支": False}


def test_gate_status_tolerates_emphasis_without_space():
    """`主力真在**✗**`(无空格)与 `主力真在 **✗**`(有空格)必须同解。"""
    assert gate_status("OW三门 主力真在**✗**·业绩真兑现**✓**·估值不透支**✓**") == {
        "主力真在": True, "业绩真兑现": False, "估值不透支": False}


def test_gate_status_tolerates_single_asterisk_and_backtick():
    assert gate_status("OW三门 主力真在 *✗*·业绩真兑现 `✓`·估值不透支 ✓")["主力真在"] is True


def test_emphasis_segment_counts_as_parseable_for_last_segment_pick():
    """多段取最后可解析段:末段用加粗标记时,不能因"认不出标记"而退回首段。"""
    card = ("正文先提一句 OW三门缺一门(无标记)\n\n"
            "OW三门 <主力真在 **✗**·业绩真兑现 **✓**·估值不透支 **✓**> → 建议 Hold")
    assert gate_status(card) == {"主力真在": True, "业绩真兑现": False, "估值不透支": False}


def test_emphasis_without_a_real_mark_still_reads_as_not_failed():
    """`主力真在**(数据缺)**` 没有 ✓/✗ → 与改动前同语义,判 False,不臆测。"""
    assert gate_status("OW三门 主力真在**(数据缺)**·业绩真兑现 ✗·估值不透支 ✓") == {
        "主力真在": False, "业绩真兑现": True, "估值不透支": False}


# ── D8.3 ①:门记号容错(✔/✘/× 变体字形)+ 无记号段返回 None(不再"首段全 False=三门全
# 通过") ──────────────────────────────────────────────────────────────────────
#
# 病灶(2026-08-29 审计):`gate_status` 对"段内有门名、但一个 ✓/✗ 都没有"的旧回落是
# `_parse_gate_seg(matches[0].group(0))` —— 每个门名找不到标记 → `_mark_after` 返回
# "" → `"" == "✗"` 恒 False → 三门全判"未失守"(=全 PASS)。这是同一条「解析器读不懂
# 就悄悄给通过」的病(与加粗号那次同族),只是触发条件从"加粗看不懂"换成"压根没写"。
# 上面 `test_gate_status_unchanged_when_no_segment_has_a_parseable_mark` 正是锁死这条
# **旧错误语义**的用例(名字叫"unchanged"是因为那次修复不想动它)——本轮改动之后它必须
# 跟着改口径,不能继续断言"没记号=全过"。

def test_gate_marks_tolerant_glyphs():
    """`✔`(→✓)/`✘`/`×`(→✗)三种变体字形与 `✓`/`✗` 同解。"""
    seg = "OW三门 主力真在 ✘·业绩真兑现 ✔·估值不透支 ×"
    st = gate_status(seg)
    assert st == {"主力真在": True, "业绩真兑现": False, "估值不透支": True}  # True=✗失守


def test_gate_status_unmarked_returns_none():
    """段内三个门名全部提及、但一个 ✓/✗/✔/✘/× 都没有(纯散文提及)→ None,不再默认全过。"""
    assert gate_status("OW三门 主力真在·业绩真兑现·估值不透支(散文提及,无记号)") is None
