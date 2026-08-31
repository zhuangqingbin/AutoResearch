#!/usr/bin/env python3
"""agent 产出语法 —— 「LLM 写什么、python 按什么读」的**唯一真身**(声明,零 IO)。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.2 K2 / §2.4 A2。

## 病灶(2026-08-29 审计实测)

agent 说一套、python 猜一套,中间没有共同的真值源:

- `**Rating**` 的解析器是泛正则 `rating.*?[:\\-]`,而且**兜底取全文第一个评级词**
  (`agents/utils/rating.py:27,41-45`)—— 一张卡里出现「不是 Buy」也可能被读成 Buy;
- `market_view.md` 的六节标题有**两个**解析器(`report_sections.py:876` 与
  `brief.py:199`),各写各的正则;
- `_l3_judged.json` 的 15 个键只写在 agent def 的散文里(`l3-rank.md:40-41`),
  python 只校 5 个;
- `[执行线]` 的阈值在 playbook 散文与 `outcome.EXEC_MAX_*` 各写一份,**没有测试对齐**;
- 评级序表在 JS 是 `RANK{sell:0…buy:4}`(`l4-stock.js:430`),在 python 是
  `RATINGS_5_TIER` Buy=0…Sell=4 —— **方向相反**,靠人记住;
- `tests/test_agent_defs.py` 的锚是**手写字面串**,没有真值源可对(08-26 A6 逮到)。

「指令级约束的失败率不为零,数据级为零」是本仓反复付过学费的结论。本模块把这些
契约从散文里搬出来,成为**两侧共同派生**的一份声明。

## 定位

只声明「字段叫什么、长什么样、必不必需」。**不做解析**——解析仍在
`scan/l4/parsers.py`、`sector/brief.py`、`agents/utils/rating.py` 各自的 owner 里,
本模块提供它们该用的 pattern,并由测试对拍两边结果一致(A2 的落点)。
"""
from __future__ import annotations

from dataclasses import dataclass

AGENT_OUTPUT_SCHEMA_VERSION = 1

#: 五档评级,**从最看多到最看空**。这是全仓唯一的顺序声明。
#: python 侧 `agents/utils/rating.RATINGS_5_TIER` 与它逐项相等(测试锁);
#: JS 侧的 `RANK` 是**反向**索引(sell=0…buy=4),由 `emit-js` 生成两个视图,
#: 不再靠人记住哪边是哪边。
RATING_ORDER: tuple[str, ...] = ("Buy", "Overweight", "Hold", "Underweight", "Sell")

#: 卡面 `FINAL TRANSACTION PROPOSAL` 的三个合法值。
PROPOSALS: tuple[str, ...] = ("BUY", "HOLD", "SELL")

#: 早停停因闭集(`l4/parsers._STOP_REASONS` 同源)。表外的词一律折成「其他」——
#: 折叠发生在解析侧,不在这里。
STOP_REASONS: tuple[str, ...] = (
    "数据不足", "涨停追高", "题材透支", "资金流出",
    "估值透支", "基本面恶化", "其他",
)

#: OW 三门的门名(`l4/parsers._GATES3` 同源)。
OW_GATES: tuple[str, ...] = ("主力真在", "业绩真兑现", "估值不透支")


@dataclass(frozen=True)
class Field:
    """agent 产出里的一个机器可读字段。

    `pattern` 是 python `re` 语法;`required=True` 表示这个字段缺席时该稿件
    **不合契约**(消费侧应当报错或记账,而不是默默走兜底)。
    """

    key: str
    pattern: str
    required: bool
    note: str = ""


@dataclass(frozen=True)
class OutputContract:
    """一个 agent 角色的产出契约。"""

    role: str
    artifact: str            # 对应 contracts.artifacts 的登记名
    fields: tuple[Field, ...]

    def field(self, key: str) -> Field:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(f"{self.role} 契约里没有字段 {key!r}")

    def required_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.required)


#: `l4-card` 决策卡。pattern 逐字搬自 `scan/l4/parsers.py:14-35`(对拍测试锁两边一致)。
L4_CARD = OutputContract(
    role="l4-card",
    artifact="l4_cards",
    fields=(
        Field("rating", r"(?im)^\s*\**\s*Rating\s*\**\s*[:：\-]\s*\**\s*(\w+)", True,
              "五档评级;严格档只认行首标签,不再兜底取全文第一个评级词"),
        Field("proposal", r"FINAL TRANSACTION PROPOSAL[:\s*]*\**\s*(BUY|HOLD|SELL)", True),
        Field("confidence", r"置信度[:：]\s*\**\s*([高中低]+)", False),
        Field("rubric", r"Rubric[^\n]*?(Buy|Overweight|Hold|Underweight|Sell)", False),
        Field("bull_bear", r"\*\*一行多空\*\*[:：]?\s*(.+)", False),
        Field("early_stop", r"\*\*早停\*\*[:：]\s*停于\s*(P[0-9])\s*[｜|]\s*停因[:：]\s*([^\s｜|]+)", False,
              "早停卡才有;停因须落在 STOP_REASONS 闭集内"),
        Field("ow_gates", r"OW三门[^\n→]*", False),
        Field("p4_intent", r"进入P4倾向[:：]\s*(\w+)", False),
        Field("exec_line_pct", r"\[执行线\]\s*pct_chg\s*<=\s*([\d.]+)", False,
              "T+1 尾盘入场条件;阈值真身 outcome.EXEC_MAX_PCT_CHG"),
        Field("exec_line_pos", r"\[执行线\]\s*pos_in_range\s*<\s*([\d.]+)", False,
              "阈值真身 outcome.EXEC_MAX_POS_IN_RANGE"),
    ),
)

#: `l3-rank` 的 `_l3_judged.json`。这些键此前只写在 def 散文里,python 只校 5 个。
L3_RANK = OutputContract(
    role="l3-rank",
    artifact="l3_judged_raw",
    fields=(
        Field("code", r"^\d{6}$", True),
        Field("thesis", r".+", True, "数字须能在 L2 行或 market_pack 找到(l3/validation 已锁)"),
        Field("mechanism", r".+", True, "两日内兑现机制 + 明日买家;写不出不选"),
        Field("conviction", r"^\d{1,3}$", True),
        Field("finalist", r"^(true|false)$", True),
        Field("catalyst", r".+", False),
        Field("risk", r".+", False),
        Field("sentiment", r".+", False),
        Field("lane", r".+", False),
    ),
)

#: `sector-brief`。标题即机器接口(`sector/brief.py:20` 的 `TERRAIN_HDR`)。
SECTOR_BRIEF = OutputContract(
    role="sector-brief",
    artifact="sector_briefs",
    fields=(
        Field("terrain_header", r"^## 地形段", True, "抽取锚点;改字即断契约"),
    ),
)

#: `macro-brief`(策略师)。六个编号小节;此前有两个各写各的解析器。
MACRO_BRIEF = OutputContract(
    role="macro-brief",
    artifact="market_view",
    fields=(
        Field("section_head", r"^[ \t]*([1-6])[.、]\s*\*\*", True, "六节头;report_sections 用"),
        Field("tone", r"^\s*1\.\s*\*\*一句话定调\*\*\s*[:：]\s*(.+)$", True, "brief ① 用"),
    ),
)

#: `l4-intel` 情报稿的声明行与时效窗。契约版本三种并存(v1 / v2_full / v2_macro),
#: **生产 lint 此前恒用 v1** —— 稿头声明的版本必须决定用哪套判据。
L4_INTEL = OutputContract(
    role="l4-intel",
    artifact="l4_intel",
    fields=(
        Field("declaration", r"网查\s*(\d+)\s*条", True, "自报数;真值以 capsule web_budget 为准"),
        Field("contract_version", r"〔intel[ _]?(v[0-9_a-z]*)", False),
    ),
)

CONTRACTS: dict[str, OutputContract] = {
    c.role: c for c in (L4_CARD, L3_RANK, SECTOR_BRIEF, MACRO_BRIEF, L4_INTEL)
}

#: 情报时效契约的三个版本名(`intel_guard` / `self_review` 的 recency lint 选型用)。
INTEL_CONTRACT_VERSIONS: tuple[str, ...] = ("v1", "v2_full", "v2_macro")

#: `analyze.harvest` slim 渲染必须齐的结构锚点(标题前缀)——**single source**
#: (design: 2026-08-31 D1.6 #5)。原分别定义在 `scan/l4/producers.py`(质检
#: `_slim_defect`/`_slim_bytes_defect` 用它判"结构缺块")与 `tests/analyze/
#: test_harvest.py`(回归锁 harvest 侧真渲染的标题);两边各写一份时,改一个标题
#: 字符串只会有一侧的测试报警,另一侧带着假设静默漂移。搬到这里后两侧同引一份,
#: 变异探针:改一个字符串 → 两侧测试都红。
SLIM_ANCHORS: tuple[str, ...] = (
    "## Verified market snapshot",
    "### Latest verified OHLCV row",
    "## Market context",
    "## Fundamentals overview",
)

#: `analyze.harvest.ashare_market_context_from_l1` 复用的 L1 召回因子行列名——
#: **single source**(design: 2026-08-31 D1.6 #6)。L1 生产者(`scan/universe.py`
#: 的 `keep` 白名单)必须把这些列都投影进 `L1_scored_full.csv` /
#: `L1_recall_top1000.csv`,否则 harvest 的 slim 复用会静默漏字段(消费侧此前
#: 无声回退 = 少了什么都看不出来;现在缺列会 `record_degradation("L1_scored_full",
#: ..., kind="legit_empty")` 留痕)。顺序即 `ashare_market_context_from_l1` 里
#: 读取它们的顺序,不是随意排列。
L1_REUSE_COLUMNS: tuple[str, ...] = (
    "composite",
    "score_momentum", "score_fund_main", "score_fund_retail", "score_chip",
    "score_north", "score_tech", "score_growth", "score_value",
    "main_inflow_yi", "main_net_ratio", "retail_net_yi",
    "ma_bull", "above_ma60", "rsi6", "rsi12",
    "winner_rate", "chip_concentration", "price_to_cost", "close",
    "hk_ratio",
)


def contract(role: str) -> OutputContract:
    """按角色取产出契约;未登记 → `KeyError`。"""
    return CONTRACTS[role]


def rank_of(rating: str) -> int:
    """评级 → 名次(0 = 最看多)。未知评级 → `ValueError`,不静默当 Hold。"""
    try:
        return RATING_ORDER.index(rating.capitalize())
    except ValueError as exc:  # noqa: TRY003
        raise ValueError(f"未知评级 {rating!r};合法值 {RATING_ORDER}") from exc


def js_rank_view() -> dict[str, int]:
    """JS 侧的反向视图(sell=0 … buy=4),供 `emit-js` 生成。

    两个方向都由本函数与 `RATING_ORDER` 派生 —— JS 的 `RANK` 字面量与 python 的
    `RATINGS_5_TIER` 方向相反这件事,此前只存在于人的记忆里。
    """
    return {r.lower(): len(RATING_ORDER) - 1 - i for i, r in enumerate(RATING_ORDER)}
