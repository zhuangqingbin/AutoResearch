#!/usr/bin/env python3
"""相对 BUY 事实读模型 —— brief ③ 与 summary「📉 今日漏斗读数」共用的**唯一**解析器。

2026-08-21 低位转强波 P0:此前 `_relative_facts` 只住在 brief.py,summary 侧 `market.py`
仍按旧绝对门(卡面 ≥OW)数买单并印「0 买·空仓观望」,E6 active 后两处在同一份报告里
打架(08-20 实跑:brief ③ ✅ relative BUY 金螳螂 vs summary「0 买…空仓观望」)。修法不是
再写一个解析器——brief↔summary 一起错一起绿的前科(memory
`brief-reads-provisional-relative-buy`)——而是把读模型抽到这里,两边 import 同一个函数、
同一张禁词表。本模块只依赖 `common.ruler`,不反向依赖 brief/market(防环)。
"""
from __future__ import annotations

from autoresearch.common.ruler import MAIN_RULER, REL_MARKET, REL_SECTOR

#: 相对 BUY 区禁词 —— 出现任意一个即语义越权(它从不承诺绝对方向)。
BANNED_RELATIVE_PHRASES = ("预计上涨", "预计绝对上涨", "预期上涨", "看涨", "必涨", "稳赚")
#: 绝对 gap 为负时**固定**用这句话,不许换措辞。
WEAK_MARKET_PHRASE = "弱市相对最优"
#: `rel_gap_market` 这一列的真分母(评分时由 relative_ledger 另算,不在决策产物里)。
REL_MARKET_POPULATION = "全市场可交易"
#: 决策文档 `benchmark.market.n` 的人口标签(决策层四面分位 / 流动性门的分母)。
DECISION_POOL_LABEL = "L0 可交易"
#: v3.0 候选池的人话标签(brief ③ 用)。**两条规则不同名**,免得读者把两天的读数连成一条线。
POOL_LABELS = {"finalists": "L3 finalist 全体", "composite": "composite 证据席"}
#: v3.0 期望值的诚实措辞 —— 证据是 +0.14~0.17pp(t 1.96~2.78,42 个扫描日),与一次 A 股
#: 往返成本(印花税 0.05% + 佣金 + 滑点 ≈ 0.1~0.15%)**同量级**。写「预计上涨」是越权
#: (`BANNED_RELATIVE_PHRASES` 已挡),写「稳赚」更是;这句是能说的上限。
COMPOSITE_EXPECTATION = "期望≈往返成本量级,不承诺绝对收益为正"


def relative_facts(decision: dict | None) -> dict:
    """`_relative_buy_decision.json` → ③ 影子/正式 BUY 行需要的字段。缺文件 → `present=False`
    (**显式说「未生成」**,不静默省行——静默会被读成「今天没有相对 BUY」)。"""
    if not isinstance(decision, dict):
        return {"present": False, "mode": None}
    bench = decision.get("benchmark") or {}
    market, sector = bench.get("market") or {}, bench.get("sector") or {}
    buys = decision.get("buys") or []
    by_code = {row.get("code"): row for row in (decision.get("candidates") or [])
               if isinstance(row, dict)}
    top = by_code.get(buys[0]["code"]) if buys else None
    gap = (top or {}).get("expected_abs_gap") or {}
    hard_reject = sum(1 for row in (decision.get("candidates") or [])
                      if isinstance(row, dict) and not row.get("eligible"))
    reasons = [f"{r.get('reason')}×{r.get('n')}"
               for r in (decision.get("blocked_reasons") or []) if isinstance(r, dict)]
    return {
        "present": True,
        "mode": decision.get("mode"),
        "rule_version": decision.get("rule_version"),
        "blocked": bool(decision.get("blocked")),
        "blocked_reasons": reasons,
        "code": (buys[0]["code"] if buys else None),
        "basis": (buys[0].get("basis") if buys else None),
        "tier": (buys[0].get("tier") if buys else None),
        # v4.1(2026-09-25 §2.4):第五门评估留痕。None = 门未开 / 旧 schema(**不是**「当日无事件」);
        # dict = {source, gate_evaluated, n_rows, n_candidates_in_events, hits}。
        "rebalance": decision.get("index_events"),
        "name": (top or {}).get("name"),
        "rank": (top or {}).get("rank"),
        "score": (top or {}).get("relative_decision_score"),
        "research_rating": (top or {}).get("research_rating"),
        "n_eligible": (decision.get("counts") or {}).get("eligible"),
        "n_candidates": (decision.get("counts") or {}).get("candidates"),
        "market_column": market.get("column") or REL_MARKET,
        # B-1:**两个人口分成两个键**,不再共用一个含糊的 `market_n`。
        #   `decision_pool_n`  = `benchmark.market.n` = 决策层四面分位 / P10 流动性门的分母
        #                        (当日 **L0 过门票**);
        #   `eval_population`  = `rel_gap_market` 这一列的真分母 = **全市场可交易**
        #                        (`ruler.py` I-1 人口裁定,含漏在 L0 的票),评分时由
        #                        评分时另算,不在本产物里。
        # 两数常年不等(2026-08-04 实测 4193 vs 5426),`relative_ledger` 明文「不可互换」。
        "decision_pool_n": market.get("n"),
        "eval_population": market.get("eval_population") or REL_MARKET_POPULATION,
        "sector_column": sector.get("column") or REL_SECTOR,
        "n_sectors": market.get("n_sectors") or sector.get("n_sectors"),
        "abs_gap_status": gap.get("status", "UNMEASURED"),
        "abs_gap_value": gap.get("value"),
        "abs_gap_n": gap.get("n", 0),
        "hard_reject": hard_reject,
        "ruler": decision.get("ruler") or MAIN_RULER,
        # v3.0:BUY 从哪个池里选的 + 当日席位规模。读这份决策前先看这两个键。
        "pool": decision.get("pool") or "finalists",
        "pool_label": POOL_LABELS.get(decision.get("pool") or "finalists", "?"),
        "n_pool": (decision.get("counts") or {}).get("in_pool"),
    }
