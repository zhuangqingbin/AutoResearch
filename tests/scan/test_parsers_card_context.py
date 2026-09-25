"""card_context 保守解析(Task 6 · spec 2026-09-12 scene-reconstruction-transcript-binding §7.1)。

E6(`relative_buy.py`)长期只读卡的机读提案(`FINAL TRANSACTION PROPOSAL`),不读卡面
仓位/触发位/执行线——于是写着"不新开仓"的卡也能被相对层选成当日 BUY(设计稿附录 A
E12:四笔亏损 BUY 现场,四张卡全写不建仓/早停不建仓/不追)。本文件测试新增的两个公共
入口,只做**保守解析**,不改 E6 的候选池/硬门/排序/评级规则(Task 7 才接线进决策文档):

  - `read_card_text(scan_dir, ticker)`:原私有 `_decision_text` 的公共名(别名仍保留)。
  - `parse_card_context(text, *, contract=None)`:卡文本 → 结构化 card_context。

四态 `entry_stance` 只认**正证据**(spec §7.1 段落 + task-6-brief 控制者裁决):
  0%/0.0%(完整仓位数值)或 不建仓/不新开仓/不新建仓 → PROHIBITED;
  待突破确认/满足条件才考虑/不追高 → CONDITIONAL;
  明确肯定且无否定/前置条件的新开仓建议 → ALLOWED;其余 → UNKNOWN
  (绝不能从"没有否定词"反推 ALLOWED;裸数字如"10%"不是推荐)。
`exec_lines` 的 `presence`(这行是否写了)与 `contract_match`(阈值是否等于传入的历史
`contract`)分开计算——不传 `contract` 恒 `UNKNOWN`,不拿今天的常量顶替历史版本。
"""
from __future__ import annotations

import pytest

from autoresearch.scan.l4.parsers import parse_card_context, read_card_text

# ── brief 给定的字面用例(逐字照抄,不得改写)──────────────────────────────────


@pytest.mark.parametrize(
    ("position", "stance"),
    [
        ("0%", "PROHIBITED"),
        ("0.0%", "PROHIBITED"),
        ("不新开仓", "PROHIBITED"),
        ("待突破确认", "CONDITIONAL"),
        ("不追高", "CONDITIONAL"),
        ("明确允许新开仓，仓位 10%", "ALLOWED"),
        ("10%", "UNKNOWN"),
        ("—", "UNKNOWN"),
    ],
)
def test_entry_stance_requires_positive_evidence(position, stance):
    text = "\n".join([
        "# 决策卡",
        "| 评级 | 现价 | 仓位 |",
        "|---|---|---|",
        f"| Hold | 10 | {position} |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == stance
    if stance in {"CONDITIONAL", "UNKNOWN"}:
        assert got["no_new_position"] is None


# ── 真实卡面形状(task-6-brief 第5条给定的字段样本)───────────────────────────

_EARLYSTOP_CARD = "\n".join([
    "# 决策卡 — 600000 示例票 @ 2026-09-12  ·  〔早停·表面 DD〕",
    "| 评级 | 现价 | 时间框架 | 触发位 | 置信度 |",
    "|---|---|---|---|---|",
    "| Hold | 12.34 | T+1 | 不新开仓;持有者 T+2 开盘 ≥180 减、跌破 166.39(布林下轨)清 | 中 |",
    "**早停**: 停于 P3 ｜ 停因:资金流出",
    "FINAL TRANSACTION PROPOSAL: **HOLD**",
])

_FULL_CARD = "\n".join([
    "# 决策卡 — 300857 示例票 @ 2026-09-12",
    "| 评级 | 现价 | EV目标(T+2 开盘预期带) | 上行 | 下行 | R:R | 时间框架 | 仓位 | 触发位 | 置信度 |",
    "|---|---|---|---|---|---|---|---|---|---|",
    "| Overweight | 18.20 | +3.2%~-1.1% | +8% | -2% | **0.83 : 1** | T+2 | "
    "0%(不新建仓) | 跌破 16.5 清 | 高 |",
    "FINAL TRANSACTION PROPOSAL: **BUY**",
])

_EXEC_CARD = "\n".join([
    "# 决策卡",
    "| 评级 | 现价 | 仓位 |",
    "|---|---|---|",
    "| Hold | 10 | 10% |",
    "- [执行线] pct_chg <= 3.0 → 当日涨超 3% 放弃本次尾盘入场",
    "- [执行线] pos_in_range < 0.7 → 收在当日区间上 30% 放弃",
    "FINAL TRANSACTION PROPOSAL: **HOLD**",
])


def test_card_kind_earlystop_detected_and_position_column_absent():
    got = parse_card_context(_EARLYSTOP_CARD)
    assert got["card_kind"] == "earlystop"
    assert got["position_raw"] is None            # 早停卡没有仓位列
    assert got["trigger_raw"] is not None
    assert got["entry_stance"] == "PROHIBITED"     # 证据来自触发位「不新开仓」
    assert got["no_new_position"] is True
    assert got["parse_status"] == "OK"
    # 早停卡不应有 EV/R:R(它们那张表压根没有这两列)——缺失为 null,不是错误。
    assert got["ev_target"] is None
    assert got["rr"] is None


def test_card_kind_full_reads_emphasis_and_position_note_without_recomputing_ev():
    got = parse_card_context(_FULL_CARD)
    assert got["card_kind"] == "full"
    assert got["proposal"] == "BUY"
    assert got["rr"] == "0.83 : 1"                 # `**` 强调号已被 _parse_dashboard 剥离
    assert got["ev_target"] == "+3.2%~-1.1%"       # 原文原样,不据区间中枢重算期望值
    assert got["position_raw"] == "0%(不新建仓)"
    assert got["trigger_raw"] == "跌破 16.5 清"
    assert got["entry_stance"] == "PROHIBITED"     # 0% 前缀 + 「不新建仓」双重命中
    assert got["no_new_position"] is True
    assert got["parse_status"] == "OK"


def test_entry_stance_tolerates_markdown_emphasis_in_position_cell():
    """08-01 17.4% 误判同根病灶(`**` 强调号)在 card_context 这条新链路上不能重演。"""
    text = "\n".join([
        "# 决策卡",
        "| 评级 | 现价 | 仓位 |",
        "|---|---|---|",
        "| **Hold** | 10 | **0%** |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["position_raw"] == "0%"
    assert got["entry_stance"] == "PROHIBITED"


def test_out_of_vocabulary_negation_is_unknown_not_prohibited_or_allowed():
    """PROHIBIT 词表是 spec 给定的封闭三词(不建仓/不新开仓/不新建仓)。"不可以新开仓"
    这类释义变体不在表内 → 落 UNKNOWN,不靠通用否定词外推 PROHIBITED;同时 ALLOW 正则的
    否定 lookbehind 也保证它不会被误配成 ALLOWED。两边都不外推,是同一个"只认正证据"
    纪律的两面。
    """
    text = "\n".join([
        "# 决策卡", "| 评级 | 现价 | 仓位 |", "|---|---|---|",
        "| Hold | 10 | 不可以新开仓 |", "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == "UNKNOWN"
    assert got["no_new_position"] is None


@pytest.mark.parametrize(
    ("position", "expected_stance", "expected_no_new_position"),
    [
        ("0%", "PROHIBITED", True),
        ("不新开仓", "PROHIBITED", True),
        ("明确允许新开仓，仓位 10%", "ALLOWED", False),
        ("待突破确认", "CONDITIONAL", None),
        ("10%", "UNKNOWN", None),
    ],
)
def test_no_new_position_is_compat_field_derived_from_stance(
    position, expected_stance, expected_no_new_position,
):
    text = "\n".join([
        "# 决策卡", "| 评级 | 现价 | 仓位 |", "|---|---|---|",
        f"| Hold | 10 | {position} |", "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == expected_stance
    assert got["no_new_position"] is expected_no_new_position


# ── 验收矩阵 E01:空表 / 数值损坏 / 相互矛盾 ─────────────────────────────────


def test_e01_empty_table_reports_error_without_raising():
    text = "# 决策卡\n\n(暂无仪表盘数据)\n"
    got = parse_card_context(text)
    assert got["card_kind"] == "unknown"
    assert got["entry_stance"] == "UNKNOWN"
    assert got["no_new_position"] is None
    assert got["proposal"] is None
    assert got["parse_status"] == "ERROR"
    assert got["parse_errors"]


def test_e01_header_only_table_without_data_row_is_unknown():
    text = "\n".join(["# 决策卡", "| 评级 | 现价 | 仓位 |", "|---|---|---|"])
    got = parse_card_context(text)
    assert got["card_kind"] == "unknown"
    assert got["parse_status"] == "ERROR"


def test_e01_corrupted_exec_threshold_does_not_raise_and_is_recorded():
    text = "\n".join([
        "# 决策卡",
        "| 评级 | 现价 | EV目标(T+2 开盘预期带) | R:R | 仓位 | 触发位 |",
        "|---|---|---|---|---|---|",
        "| Hold | 10 | 坏数据??? | N/A : 1 | 10% | — |",
        "- [执行线] pct_chg <= abc → 放弃",
        "- [执行线] pos_in_range < 0.7 → 收在当日区间上 30% 放弃",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)  # 不应抛异常
    assert got["ev_target"] == "坏数据???"          # 原文原样保留,不尝试数值化
    assert got["rr"] == "N/A : 1"
    assert got["entry_stance"] == "UNKNOWN"
    assert got["exec_lines"]["pct_chg"]["presence"] is True   # 行在,只是数字读不出来
    assert got["exec_lines"]["pct_chg"]["threshold"] is None
    assert got["exec_lines"]["pct_chg"]["op"] is None
    assert got["exec_lines"]["pos_in_range"]["presence"] is True
    assert got["exec_lines"]["pos_in_range"]["threshold"] == 0.7
    assert got["parse_status"] == "PARTIAL"
    assert any("pct_chg" in e for e in got["parse_errors"])


def test_e01_mutually_contradictory_card_stays_prohibited_and_records_conflict():
    text = "\n".join([
        "# 决策卡",
        "| 评级 | 现价 | 仓位 | 触发位 |",
        "|---|---|---|---|",
        "| Hold | 10 | 0% | 建议新开仓,风险可控 |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == "PROHIBITED"     # 否定/零仓位与允许同时出现 → 保守
    assert got["no_new_position"] is True
    assert got["parse_status"] == "PARTIAL"
    assert any("entry_stance" in e for e in got["parse_errors"])


def test_e01_table_without_earlystop_or_position_signal_is_unknown_not_full():
    """fix round 1 finding 1:`card_kind` 的兜底分支——表是真的(`评级` 表可解析),
    但既没有早停行、也没有仓位列(只有 评级/现价/时间框架/置信度)——此前**零覆盖**:
    diff 里每张卡不是带仓位就是带早停,把 `else: card_kind = "unknown"` 改成
    `"full"` 不会让任何用例变红。这条锁的正是 spec §7.1 的硬约束本身:
    "无卡或未知卡种不伪装满卡"。
    """
    text = "\n".join([
        "# 决策卡",
        "| 评级 | 现价 | 时间框架 | 置信度 |",
        "|---|---|---|---|",
        "| Hold | 10 | T+1 | 中 |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["card_kind"] == "unknown"          # 绝不能是 "full"
    assert got["entry_stance"] == "UNKNOWN"        # 既无仓位列也无触发位列 → 无证据
    assert got["no_new_position"] is None
    assert got["parse_status"] == "PARTIAL"        # 表可读,只是卡种判不出来——不是 ERROR
    assert any("card_kind" in e for e in got["parse_errors"])


# ── 验收矩阵 E02:执行线版本缺失 vs 漂移,presence 与 contract_match 独立移动 ──


def test_e02_contract_match_unknown_when_version_missing():
    got = parse_card_context(_EXEC_CARD)  # contract=None(默认;版本不可得)
    for metric in ("pct_chg", "pos_in_range"):
        assert got["exec_lines"][metric]["presence"] is True
        assert got["exec_lines"][metric]["contract_match"] == "UNKNOWN"
        assert got["exec_lines"][metric]["contract_version"] is None


def test_e02_contract_match_and_drift_with_presence_held_constant():
    matching = {"EXEC_LINE_MAX_PCT_1D": 3.0, "EXEC_LINE_MAX_POS_IN_RANGE": 0.7, "version": "v1"}
    got = parse_card_context(_EXEC_CARD, contract=matching)
    assert got["exec_lines"]["pct_chg"]["presence"] is True
    assert got["exec_lines"]["pct_chg"]["contract_match"] == "MATCH"
    assert got["exec_lines"]["pct_chg"]["contract_version"] == "v1"
    assert got["exec_lines"]["pos_in_range"]["contract_match"] == "MATCH"

    drifted = {"EXEC_LINE_MAX_PCT_1D": 2.0, "EXEC_LINE_MAX_POS_IN_RANGE": 0.7, "version": "v0"}
    got_drift = parse_card_context(_EXEC_CARD, contract=drifted)
    # 同一段卡面文本;presence 不因 contract 数字变化而变化,只有 contract_match 变。
    assert got_drift["exec_lines"]["pct_chg"]["presence"] is True
    assert got_drift["exec_lines"]["pct_chg"]["contract_match"] == "DRIFTED"
    assert got_drift["exec_lines"]["pos_in_range"]["contract_match"] == "MATCH"  # 只有 pct 漂移


def test_e02_exec_line_absent_stays_unknown_even_with_contract_supplied():
    text_no_exec = "\n".join([
        "# 决策卡", "| 评级 | 现价 | 仓位 |", "|---|---|---|", "| Hold | 10 | 10% |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text_no_exec, contract={
        "EXEC_LINE_MAX_PCT_1D": 3.0, "EXEC_LINE_MAX_POS_IN_RANGE": 0.7,
    })
    for metric in ("pct_chg", "pos_in_range"):
        assert got["exec_lines"][metric]["presence"] is False
        assert got["exec_lines"][metric]["contract_match"] == "UNKNOWN"


def test_e02_contract_missing_one_key_does_not_contaminate_the_other_metric():
    got = parse_card_context(_EXEC_CARD, contract={"EXEC_LINE_MAX_PCT_1D": 3.0})
    assert got["exec_lines"]["pct_chg"]["contract_match"] == "MATCH"
    assert got["exec_lines"]["pos_in_range"]["contract_match"] == "UNKNOWN"


# ── 任何异常都不能逃出本函数(brief 条款8:E6 不能因为一张坏卡丢掉整份决策文档)──


@pytest.mark.parametrize("bad_text", [None, "", "   \n\n  ", 12345, object()])
def test_parse_card_context_never_raises_on_degenerate_input(bad_text):
    got = parse_card_context(bad_text)  # type: ignore[arg-type]
    assert got["card_kind"] == "unknown"
    assert got["parse_status"] == "ERROR"
    assert got["entry_stance"] == "UNKNOWN"
    assert got["no_new_position"] is None
    assert got["parse_errors"]


# ── 外层 try/except 兜底必须真的被触发过(fix round 1 finding 2)──────────────


def test_backstop_catches_unexpected_exception_from_inner_helper(monkeypatch):
    """`parse_card_context` 的外层 `try/except` 是"E6 绝不能因为一张坏卡丢掉整份
    决策文档"这个保证唯一的执行机制——此前没有任何用例真正触发过它(五个退化输入
    全部在更早的 `isinstance`/空串 guard 里就被挡下,删掉整段 try/except 也不会有
    任何用例变红)。用 monkeypatch 让早 guard 通过*之后*才会被调用的内部函数
    (`_parse_dashboard`)抛出,证明外层兜底真的吞下了它、留了原因、没有向上传播。
    """
    from autoresearch.scan.l4 import parsers

    def _boom(_text: str) -> dict[str, str]:
        raise RuntimeError("boom: unexpected dashboard parser failure")

    monkeypatch.setattr(parsers, "_parse_dashboard", _boom)
    got = parsers.parse_card_context("# 决策卡\n| 评级 | 现价 |\n|---|---|\n| Hold | 10 |\n")
    assert got["card_kind"] == "unknown"
    assert got["parse_status"] == "ERROR"
    assert got["entry_stance"] == "UNKNOWN"
    assert got["no_new_position"] is None
    assert any("RuntimeError" in e and "boom" in e for e in got["parse_errors"])


# ── read_card_text:公共入口 + `_decision_text` 别名(既有调用方零改动)────────


def test_read_card_text_finds_card_by_exact_and_suffixed_ticker(tmp_path):
    details = tmp_path / "details"
    details.mkdir()
    (details / "000001.md").write_text(_FULL_CARD, encoding="utf-8")
    assert read_card_text(tmp_path, "000001") == _FULL_CARD
    assert read_card_text(tmp_path, "000001.SZ") == _FULL_CARD  # 带交易所后缀的 ticker


def test_read_card_text_zfill_glob_fallback_for_short_code(tmp_path):
    details = tmp_path / "details"
    details.mkdir()
    (details / "002156.md").write_text(_FULL_CARD, encoding="utf-8")
    assert read_card_text(tmp_path, "2156") is not None


def test_read_card_text_missing_card_returns_none(tmp_path):
    assert read_card_text(tmp_path, "999999") is None


def test_decision_text_alias_is_read_card_text():
    """既有调用方(decision_finalize/report_sections/l4.card_io/assemble 的 re-export)
    按 `_decision_text` 这个私有名导入;它必须与新公共名是同一个函数对象。"""
    from autoresearch.scan.l4 import parsers

    assert parsers._decision_text is parsers.read_card_text


# ── Task 17:机读入场行(2026-09-24 §2.5)优先于散文推断,entry_source 全路径填充 ─────


@pytest.mark.parametrize(("line", "stance"), [
    ("**入场**: 允许", "ALLOWED"),
    ("**入场**: 禁止", "PROHIBITED"),
    ("**入场**: 条件(收复 10EMA 才考虑)", "CONDITIONAL"),
    ("**入场**：允许", "ALLOWED"),
])
def test_entry_line_wins_over_prose(line, stance):
    """机读入场行(2026-09-24 §2.5)优先于仓位/触发位散文推断;仓位写 0% 也不能压过它。"""
    text = "\n".join([
        "# 决策卡 — 600018 上港集团 @ 2026-09-17",
        "| 评级 | 现价 | 仓位 | 触发位 |",
        "|---|---|---|---|",
        "| Hold | 5.43 | 0% | 不建仓 |",
        line,
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == stance and got["entry_source"] == "line"


def test_without_entry_line_prose_inference_is_kept_and_labelled():
    text = "\n".join([
        "# 决策卡", "| 评级 | 现价 | 仓位 |", "|---|---|---|", "| Hold | 10 | 不新开仓 |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == "PROHIBITED" and got["entry_source"] == "prose"


def test_l4_card_contract_has_entry_field():
    import re

    from autoresearch.contracts.agent_output import L4_CARD
    f = L4_CARD.field("entry")
    assert f.required is False
    assert re.search(f.pattern, "**入场**: 条件(x)").group(1) == "条件"


def test_entry_line_pattern_pinned_between_parser_and_contract():
    """fix round 2(2026-09-24):`self_review.has_machine_entry_line` 现在编译自
    `L4_CARD.field("entry").pattern`,但 `parsers._ENTRY_LINE_RE` 仍是它自己的字面量
    正则——两处今天相同,只是巧合,没有任何东西钉住它们**继续**相同。谁悄悄放宽
    parser 的正则(比如给"允许"加个同义词)而不动契约声明,lint 就会在无人察觉的
    情况下与 parser 问不同的问题——正是 fix round 1 刚修完的缺陷,一步之遥地在别的
    文件重演。这条测试把"今天恰好相同"钉成"字节相同,变了就红";不改 `parsers.py`
    本身(它的正则/派发逻辑归另一个任务),只导入它做字符串比对。
    """
    from autoresearch.contracts.agent_output import L4_CARD
    from autoresearch.scan.l4.parsers import _ENTRY_LINE_RE
    assert _ENTRY_LINE_RE.pattern == L4_CARD.field("entry").pattern


# ── fix round 1:entry_source 的 None/"prose" 组合此前没有专属断言火力点 ────────
#
# 复核跑了七个变异,六个被逮到;第七个——把 `_empty_card_context` 的 `entry_source`
# 默认值从 `None` 改成 `"prose"`——396 个用例一个都不红。原因是没有任何一条断言
# 是"entry_source is None"本身,也没有任何一条把 entry_stance=UNKNOWN 与
# entry_source="prose" 锁在同一个 assert 里。下面两条各自补一个火力点(手动执行过
# 那个变异并确认能让它们变红,回合报告里有记录)。


def test_unparsed_card_pins_entry_source_none_not_just_error_status():
    """Mutation-7 pin:走真实早退路径(仪表盘解不出来,不是 monkeypatch),同一个
    assert 里锁 `parse_status == "ERROR"` 与 `entry_source is None`——只测其中一个,
    `_empty_card_context` 把默认值从 `None` 换成任何别的字符串都不会被抓到。
    """
    text = "# 决策卡\n\n(暂无仪表盘数据)\n"
    got = parse_card_context(text)
    assert got["parse_status"] == "ERROR" and got["entry_source"] is None


def test_inconclusive_prose_pins_unknown_stance_together_with_prose_source():
    """`entry_source` 记的是"问过哪个机制",不是"那个机制给出的答案好不好"——散文被
    问过但没找到正证据(裸数字"10%",不是推荐)时 entry_stance 仍是 UNKNOWN,但
    entry_source 必须仍是 "prose",不能因为答案含糊就被"纠正"成 None(那等价于把
    "问过没答案"误记成"根本没问")。两值必须成对锁在同一个 assert 里。
    """
    text = "\n".join([
        "# 决策卡", "| 评级 | 现价 | 仓位 |", "|---|---|---|", "| Hold | 10 | 10% |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == "UNKNOWN" and got["entry_source"] == "prose"


# ── fix round 3(2026-09-25):CRITICAL——`.search` + 无锚正则命中卡面任意位置,
# 包括 agent 把 `.claude/agents/l4-card.md:64` 的规则原文(`**入场**: 允许 | 禁止 |
# 条件(<一句前置条件>)`,三选一用管道并列)抄进正文的情况。规则原文的『允许』被
# `.search` 当成第一个(也是唯一被看见的)结论,即便卡面后面另有一条真实
# `**入场**: 禁止`,真否决也会被抢先出现的规则原文盖过——一张写着"禁止"的卡
# 被读成允许新开仓。修复:行首锚定(`(?m)^[ \t]*`)+ 拒绝候选后紧跟管道符
# (`(?!\s*[|｜])`)+ `findall`(而非 `search`)取全部候选、分歧时 fail-closed。
# 下面四条直接对应 coordinator 复现表的四行;其余补充"合法行前有无关散文"
# "两条一致""两条分歧且含禁止"三个场景。
# ──────────────────────────────────────────────────────────────────────────

_RULE_TEXT_LINE = "**入场**: 允许 | 禁止 | 条件(<一句前置条件>)"           # 规则原文,不是真实入场行
_PLACEHOLDER_LINE = "**入场**: <允许|禁止|条件(<一句前置条件>)>"          # 未填的模板占位符


def _card_with_entry(*extra_lines: str) -> str:
    """最小满卡(带可解析仪表盘),把 `extra_lines` 插在仪表盘之后、提案行之前——
    entry-line 解析必须真的跑到(不能被 `_parse_dashboard` 早退挡在门外)。
    """
    return "\n".join([
        "# 决策卡 — 600018 上港集团 @ 2026-09-17",
        "| 评级 | 现价 | 仓位 | 触发位 |",
        "|---|---|---|---|",
        "| Hold | 5.43 | 10% | — |",
        *extra_lines,
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])


def test_repro_table_row1_unfilled_placeholder_stays_unknown_safe():
    """复现表第 1 行:未填占位符——此前的修复(round 1)已处理,回归收紧不能弄坏它。"""
    got = parse_card_context(_card_with_entry(_PLACEHOLDER_LINE))
    assert got["entry_stance"] == "UNKNOWN"
    assert got["entry_source"] == "prose"


def test_repro_table_row2_rule_text_alone_is_never_allowed():
    """复现表第 2 行,缺陷本体:规则原文单独出现在卡里,旧代码判 ALLOWED。
    `允许` 候选后紧跟管道符 → 被拒;`禁止`/`条件` 在该行都不紧跟 `**入场**:` 前缀
    (前面是管道符和空格)→ 从未构成候选。`findall` 为空,退回散文推断 → UNKNOWN。
    """
    got = parse_card_context(_card_with_entry(_RULE_TEXT_LINE))
    assert got["entry_stance"] != "ALLOWED"
    assert got["entry_stance"] == "UNKNOWN"
    assert got["entry_source"] == "prose"


def test_repro_table_row3_rule_text_then_real_prohibited_wins_not_allowed():
    """复现表第 3 行,缺陷本体:规则原文之后另有一条真实 `**入场**: 禁止`。旧代码
    `.search` 只取第一个匹配(规则原文的『允许』),真实否决被跳过 → 误判 ALLOWED。
    `findall` 只命中真实那一条(规则原文那行贡献 0 个候选,见上一条测试),一票
    否决生效。
    """
    got = parse_card_context(
        _card_with_entry(_RULE_TEXT_LINE, "中间是一段与入场无关的散文说明。", "**入场**: 禁止")
    )
    assert got["entry_stance"] == "PROHIBITED"
    assert got["entry_source"] == "line"
    assert not got["parse_errors"]  # 只有一条真实候选,不是分歧,不该留冲突痕迹


def test_repro_table_row4_real_prohibited_line_unchanged():
    """复现表第 4 行:干净的真实禁止行——修复前后都必须是 PROHIBITED,不能被收紧
    的正则误伤。"""
    got = parse_card_context(_card_with_entry("**入场**: 禁止"))
    assert got["entry_stance"] == "PROHIBITED"
    assert got["entry_source"] == "line"


def test_agent_def_rule_text_verbatim_embedded_in_prose_is_never_allowed():
    """`.claude/agents/l4-card.md:64` 的真实行原文(整句抄入卡面,规则原文嵌在
    "入场行(...)**:` 反引号包裹"的散文里,不独占一行)——行首锚点必须挡住它,不只
    是靠管道符。"""
    real_def_line = (
        "**入场行(2026-09-24 新增,机读契约,所有卡必写)**:"
        "`**入场**: 允许 | 禁止 | 条件(<一句前置条件>)`。它与五档评级**语义分离**"
    )
    got = parse_card_context(_card_with_entry(real_def_line))
    assert got["entry_stance"] != "ALLOWED"
    assert got["entry_stance"] == "UNKNOWN"
    assert got["entry_source"] == "prose"


def test_legitimate_entry_line_after_unrelated_prose_is_still_recognized():
    """真实入场行前面有大段无关散文(含"入场"两字但不构成机读行)——行首锚点只要求
    它独占一行,不要求它紧邻仪表盘或是卡里第一行提到入场的地方。"""
    got = parse_card_context(_card_with_entry(
        "**一行多空**: 多 <催化未落地> ｜ 空 <量能不足>",
        "**早停**: 停于 P3 ｜ 停因:资金流出",
        "一段复盘式散文,提到「入场」时机未到,但这句本身不构成机读入场行。",
        "**入场**: 禁止",
    ))
    assert got["entry_stance"] == "PROHIBITED"
    assert got["entry_source"] == "line"


def test_two_agreeing_entry_lines_use_that_stance_without_conflict():
    got = parse_card_context(_card_with_entry("**入场**: 禁止", "**入场**: 禁止"))
    assert got["entry_stance"] == "PROHIBITED"
    assert got["entry_source"] == "line"
    assert not got["parse_errors"]


def test_two_disagreeing_entry_lines_with_prohibit_present_stays_prohibited():
    """两条入场行互相矛盾(一条 允许、一条 禁止)——绝不能取 ALLOWED,必须是
    PROHIBITED 并在 `parse_errors` 留痕(spec:存疑就不该往"能买"的方向猜)。
    """
    got = parse_card_context(_card_with_entry("**入场**: 允许", "**入场**: 禁止"))
    assert got["entry_stance"] == "PROHIBITED"
    assert got["entry_source"] == "line"
    assert any("entry_stance" in e for e in got["parse_errors"])
