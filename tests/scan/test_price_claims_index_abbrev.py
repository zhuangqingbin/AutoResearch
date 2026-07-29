"""指数简称 + 元话语豁免的两个洞(Wave8 W8-14 实施时改判)。

**立案时以为**:2026-07-28 的 `price_claim_mismatch`(「称 −7.35% 实 3.57%」)是因为
湖里没有指数行情,审计器拿个股 OHLCV 硬对指数断言 → 修法是"指数入湖 + 对指数裁真伪"。

**动工一查:错了。** 601288 农业银行卡里的原句是

    【网查·〔转引〕非本票行情断言,未与本票 OHLCV 对账】T0(2026-07-28):
    「银行板块逆市走强对冲创指重挫(创指当日跌7.35%)」

两个洞让它被认领成"农业银行自己声称跌 7.35%":

1. **指数简称缺失**:`_INDEX_NAMES` 有「创业板指」,没有 **「创指」** —— `_near_index_name`
   的左邻窗认不出简称。同族缺口:沪指/深指/科创综指/创指。
2. **元话语豁免没覆盖到**:Wave7 B′-b 的豁免词表有 `未对账`,而卡里写的是
   **`未与本票 OHLCV 对账`**(中间插了字);`非本票行情自陈` 也没覆盖 `非本票行情断言`。
   卡片**明明白白在做对账并声明不采信**,却被判成自己捏造 —— 这正是 B′-b 修的那类
   「引用并拒绝被当成自己的断言」,只是词形差一点就漏网。

所以这条从来不该走"指数湖对账"这条路,它压根不该被认领。指数湖(W8-14 另一半)仍然有价值
—— `today_slice` 的指数涨跌行、以及将来**真**属本票语境的指数断言 —— 但不是这条的解药。

⚠️ 子串词表天然脆:词表匹配的是**词形**,而人写的是**意思**。所以豁免侧宁可放宽
(under-report),指控侧宁可收紧 —— 对一张正在做对账的卡输出「捏造股价」的代价,
是把 P0 探针的公信力磨掉(狼来了),之后真捏造也没人看。
"""

from __future__ import annotations

import pytest

from autoresearch.scan.price_claims import extract_price_claims

_REAL_CARD_LINE = (
    "| 催化 | 中(0) | • 【网查·〔转引〕非本票行情断言,未与本票 OHLCV 对账】"
    "T0(2026-07-28):「银行板块逆市走强对冲创指重挫(创指当日跌7.35%)」"
    "「建设银行盘中股价创历史新高(10.68元)」(东方财富)"
)


def _claims(text: str):
    return extract_price_claims(text, name="农业银行", code6="601288", year_hint=2026)


def test_real_0728_card_line_is_not_claimed():
    """**核心回归**:07-28 那条真实 warn 的原句,不得再被认领为本票断言。"""
    vals = [c["value"] for c in _claims(_REAL_CARD_LINE)]
    assert -7.35 not in vals, (
        f"「创指当日跌7.35%」被认领成农业银行自己的断言:{vals}"
    )


@pytest.mark.parametrize("abbrev", ["创指", "沪指", "深指", "科创综指"])
def test_index_abbreviations_are_recognised(abbrev):
    """指数**简称**紧邻百分数 → 属指数,不认领给本票。"""
    sent = f"2026-07-28 {abbrev}当日跌7.35%,本票逆势走强。"
    assert -7.35 not in [c["value"] for c in _claims(sent)], f"{abbrev} 未被识别为指数名"


@pytest.mark.parametrize("meta", [
    "未与本票 OHLCV 对账", "非本票行情断言", "未与本票对账", "他票数据",
])
def test_metadiscourse_variants_are_exempt(meta):
    """卡片自己声明「这不是本票的数」时,句内行情词不得被当成本票断言。"""
    sent = f"【网查·〔转引〕{meta}】2026-07-28:某板块当日跌7.35%。"
    assert -7.35 not in [c["value"] for c in _claims(sent)], f"元话语 `{meta}` 未豁免"


def test_impossible_daily_move_is_not_a_price_claim():
    """|值| 超过 A 股单日物理上限 → 不是日涨跌断言(是利润增速/区间累计/别家的数)。

    07-28 实况(688766 普冉卡):「〔网查〕**兆易创新** H1 **+1099%** 却自 6/29 高点
    回撤超 50%」—— 另一家公司的**中报利润增速**被认领成普冉 06-29 的**股价**断言。
    公司名/科目名的词表永远补不完,而「单日涨不到 1099%」是物理事实,不依赖措辞。
    """
    sent = "本票参照:兆易创新 H1 6/29 上涨1099%,与本票路径同型。"
    assert 1099.0 not in [c["value"] for c in _claims(sent)]


def test_limit_magnitude_moves_still_extracted():
    """边界:20cm 板的合法涨停(+19.98%)仍必须被认领 —— 闸不能误伤真行情。"""
    sent = "本票 2026-07-28 上涨19.98%,20cm 涨停。"
    assert 19.98 in [c["value"] for c in _claims(sent)]


def test_genuine_own_claim_still_extracted():
    """**不能因为放宽豁免就把真断言也放跑** —— 探针必须还留着牙齿。"""
    sent = "本票 2026-07-28 当日跌7.35%,创阶段新低。"
    assert -7.35 in [c["value"] for c in _claims(sent)], "本票自陈的价格断言必须仍被提取"


def test_own_claim_next_to_index_mention_still_extracted():
    """同句既提指数又给本票数:跳过指数那个、认领本票那个(左邻窗只保护紧邻指数名的数)。

    注:提取器要求句内有**日期锚**才认领(无日期 = 不知道在说哪天,不猜)——
    首版这条测试的句子没写日期,于是两个数都没被提取,我误读成"本票的数也被吞了"。
    """
    sent = "2026-07-28 创指跌7.35%,而本票今日下跌1.20% 相对抗跌。"
    vals = [c["value"] for c in _claims(sent)]
    assert -7.35 not in vals, "紧邻指数简称的数不得认领"
    assert -1.20 in vals, "本票自己的数必须仍被认领"
