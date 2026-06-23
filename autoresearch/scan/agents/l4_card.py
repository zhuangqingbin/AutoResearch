#!/usr/bin/env python3
"""scan-market · L4 研究的确定性 helper(成本级联三层选择器 + 评级评分卡 rubric)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A/§D;Plan 4.1。

零 LLM。L4 决策卡(analyze-ticker-lite)+ Tier-2 平反 + Tier-3 多空辩论由 skill 编排 subagent
(见 screening-playbook.md);本模块只做**确定性的级联名单 + 评级派生**:Tier-1 分批、卡片评级
解析、Tier-2/Tier-3 候选名单、以及 LLM-as-judge 评分卡(净分定档 + OW 硬门压 Hold,防过度多报)。
selftest 已迁 pytest(tests/scan/test_agents.py)。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# ───────────────────────── L4:成本级联三层选择器(Tier-1 Sonnet / Tier-2 平反 / Tier-3 辩论) ─────────────────────────


def batch_finalists(df: pd.DataFrame, size: int = 3):
    """L4 Tier-1 分批:按 finalists 顺序每 size 只一个 subagent;yield (batch_idx, DataFrame)。

    宽筛阶段走 Sonnet,3 只/子代理摊薄重复子代理前导(~30 张卡 → ~10 个 subagent)。
    **这 ~10 个 subagent 由 skill 在一条消息里并发派发(并行启动),非顺序逐批**(见 screening-playbook.md)。
    """
    d = df.reset_index(drop=True)
    for i in range(0, len(d), size):
        yield i // size, d.iloc[i:i + size]


def parse_ratings_from_details(details_dir: Path | str) -> dict[str, str]:
    """读 details/*.md 决策卡,复用项目 `parse_rating` 提五档评级 → {code: rating}。

    code = 文件名 stem(6 位代码);读不到卡/无评级 → `parse_rating` 回退 'Hold'。
    """
    from autoresearch.agents.utils.rating import parse_rating  # 延迟导入,保持本模块轻量
    out: dict[str, str] = {}
    base = Path(details_dir)
    if not base.exists():
        return out
    for p in sorted(base.glob("*.md")):
        code = p.stem
        out[code.zfill(6) if code.isdigit() else code] = parse_rating(p.read_text(encoding="utf-8"))
    return out


def pick_buy_candidates(ratings: dict[str, str],
                        include: tuple[str, ...] = ("Buy", "Overweight")) -> list[str]:
    """L4 **Tier-3 多空辩论**名单:Tier-1(+Tier-2 平反)评级落在 include 的买点候选,直接进
    Tier-3 辩论(辩论既定级又证伪,吃掉旧 Tier-2 的单遍买点确认)。K2 默认 Buy/Overweight。"""
    keep = set(include)
    return [c for c, r in ratings.items() if r in keep]


def pick_buylist(ratings: dict[str, str], floor: str = "Overweight") -> list[str]:
    """评级 ≥ floor 的发布买单(floor=Overweight 时等价 pick_buy_candidates〔Buy/OW〕)。

    Tier-3 辩论输入用 `pick_buy_candidates`;本函数留作"最终买单"口径(Tier-3 折回后仍 ≥floor)。"""
    from autoresearch.agents.utils.rating import (
        RATINGS_5_TIER,  # Buy>Overweight>Hold>Underweight>Sell
    )
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    cap = order.get(floor, 1)
    return [c for c, r in ratings.items() if order.get(r, 99) <= cap]


def pick_downgrade_reviews(ratings: dict[str, str], finalists: pd.DataFrame,
                           conv_floor: float = 75, top_k: int = 5, max_rating: str = "Hold") -> list[str]:
    """L4 **Tier-2**(瘦,唯一职责=防假阴性平反,**条件触发**):Sonnet 把**高 conviction 的趋势 finalist**
    判到 ≤max_rating 的,才送 Opus 单遍复核平反——买点候选已直接进 Tier-3 辩论,Tier-2 只救误杀的边界假阴;
    名单空(无高 conviction 趋势被压)则 **Tier-2 完全不触发、零 Opus**。按 conviction 取 top_k。"""
    from autoresearch.agents.utils.rating import RATINGS_5_TIER
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    floor_idx = order.get(max_rating, 2)
    df = finalists.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    conv = pd.to_numeric(df.get("conviction"), errors="coerce").fillna(0)
    lane = df["lane"] if "lane" in df.columns else pd.Series("", index=df.index)
    picks: list[tuple[str, float]] = []
    for i, c in enumerate(df["code"]):
        rt = ratings.get(c, "Hold")
        if lane.iloc[i] == "trend" and conv.iloc[i] >= conv_floor and order.get(rt, 9) >= floor_idx:
            picks.append((c, float(conv.iloc[i])))
    picks.sort(key=lambda x: -x[1])
    return [c for c, _ in picks[:top_k]]


# ───────────────────────── L4 · P0:漏斗简报(定向,确定性组装) ─────────────────────────


def compose_funnel_brief(code: str, scan_dir: Path | str) -> str:
    """L4 **P0 定向**:从漏斗产物(L1_recall/L2/finalists)拼该票紧凑简报 markdown。

    **只定向 + 给评分卡先验,不作早停依据**(信息薄,据此判=误杀)。subagent 据此知道
    「该重点核哪条」,判定来自 P1–P5 读到的 slim 真数据。缺产物/列降级占位(`—`),不抛。
    """
    base = Path(scan_dir)
    code6 = str(code).split(".")[0].zfill(6)

    def _row(fname: str) -> dict:
        p = base / fname
        if not p.exists():
            return {}
        df = pd.read_csv(p, dtype={"code": str})
        if "code" not in df.columns:
            return {}
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        return sub.iloc[0].to_dict() if len(sub) else {}

    l1, l2, l3 = _row("L1_recall_top1000.csv"), _row("L2_gbdt_top200.csv"), _row("finalists.csv")

    def _g(d: dict, k: str, dflt: str = "—"):
        v = d.get(k, dflt)
        return dflt if v is None or (isinstance(v, float) and v != v) else v

    name = _g(l3, "name") if l3 else _g(l1, "name")
    lines = [
        f"## 漏斗简报 — {code6} {name}(L1/L2/L3 评价·定向用,**判定须读下方真数据**)",
        "",
        f"- **L1 召回**:命中 {_g(l1,'n_channels')} 路({_g(l1,'recall_channels')})｜"
        f"best_rank {_g(l1,'best_rank')}｜composite {_g(l1,'composite')}",
        f"- **L1 子分**:动量{_g(l1,'score_momentum')}·主力{_g(l1,'score_fund_main')}·"
        f"成长{_g(l1,'score_growth')}·价值{_g(l1,'score_value')}·量价{_g(l1,'score_volprice')}·"
        f"筹码{_g(l1,'score_chip')}·北向{_g(l1,'score_north')}·技术{_g(l1,'score_tech')}",
        f"- **基本面(先验)**:np_yoy {_g(l1,'np_yoy')}·rev_yoy {_g(l1,'rev_yoy')}·roe {_g(l1,'roe')}",
        f"- **估值(先验)**:pe {_g(l1,'pe')}·pb {_g(l1,'pb')}·股息 {_g(l1,'dv_ratio')}",
        f"- **资金/技术(先验)**:主力净占比 {_g(l1,'main_net_ratio')}·cmf20 {_g(l1,'cmf_20')}·"
        f"obv20 {_g(l1,'obv_mom_20')}·rsi6 {_g(l1,'rsi6')}·多头排列 {_g(l1,'ma_bull')}·pct60d {_g(l1,'pct_60d')}",
        f"- **筹码(先验)**:winner {_g(l1,'winner_rate')}·集中度 {_g(l1,'chip_concentration')}·"
        f"现价/成本 {_g(l1,'price_to_cost')}·北向占比 {_g(l1,'hk_ratio')}",
        f"- **L2**:gbdt_score {_g(l2,'gbdt_score')}(rank {_g(l2,'l2_rank')})",
        f"- **L3 入选**:conviction {_g(l3,'conviction')}·lane {_g(l3,'lane')}·情感 {_g(l3,'sentiment')}",
        f"  - 多头论点:{_g(l3,'thesis')}",
        f"  - 最大风险:{_g(l3,'risk')}",
        f"  - 催化:{_g(l3,'catalyst')}",
    ]
    return "\n".join(lines) + "\n"


# ───────────────────────── L4 · C:评级评分卡(LLM-as-judge rubric,确定性锚) ─────────────────────────

_RUBRIC_DIMS = ("基本面", "估值", "技术资金", "盈利质量", "偿付", "催化")
_DIM_SCORE = {"强": 1, "中": 0, "弱": -1}
_OW_GATES = ("主力真在", "业绩真兑现", "估值不透支")


def _norm_dim(k: str) -> str:
    """维度名归一:技术·资金→技术资金、偿付(爆雷)→偿付,去修饰/空白对齐锚键。"""
    s = str(k)
    for ch in "·()（）爆雷 　":
        s = s.replace(ch, "")
    return s


def rubric_rating(dims: dict, gates: dict) -> tuple[str, str]:
    """C·LLM-as-judge 评分卡:6 维(强+1/中0/弱−1)净分定档 + 3 道 OW 硬门 → 确定性建议评级 + 约束因。

    动机:Sonnet 凭 gestalt 过度多报(实测 6-18:10 OW vs Opus 3 OW),撑大 Tier-2 复核量。把评级
    **派生**自评分卡——净分映射档位,但**任一 OW 门未过则 ≥Overweight 一律压到 Hold**(对齐 Tier-1
    『三条全中才 OW』)。卡片据此自检:`**Rating**` 必须 = 建议,否则显式写 `**偏离**:<硬理由>`。

    dims: {维度: 强|中|弱}(缺/不识别按 中=0;键名容错 技术·资金 / 偿付(爆雷));
    gates: {主力真在|业绩真兑现|估值不透支: bool}(缺按 False 保守)。
    返回 (建议评级, 约束因)。
    """
    from autoresearch.agents.utils.rating import RATINGS_5_TIER  # Buy>OW>Hold>UW>Sell
    nd = {_norm_dim(k): v for k, v in (dims or {}).items()}
    net = sum(_DIM_SCORE.get(str(nd.get(d, "中")).strip(), 0) for d in _RUBRIC_DIMS)
    if net >= 4:
        base = "Buy"
    elif net >= 2:
        base = "Overweight"
    elif net >= -1:
        base = "Hold"
    elif net >= -3:
        base = "Underweight"
    else:
        base = "Sell"
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    failed = [g for g in _OW_GATES if not (gates or {}).get(g, False)]
    if order[base] < order["Hold"] and failed:        # 想给 ≥OW 但有门没过 → 压 Hold(防过度多报)
        return "Hold", f"净分{net:+d}→{base},OW门未过({'、'.join(failed)})→压Hold"
    suffix = "(OW门3/3)" if order[base] < order["Hold"] else ""
    return base, f"净分{net:+d}→{base}{suffix}"
